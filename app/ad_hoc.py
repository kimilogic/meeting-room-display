"""Local-only, idoutai ad-hoc occupancy. Never writes to Microsoft Graph."""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, RLock, Thread

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "idoutai-ad-hoc.json"


class AdHocError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class AdHoc:
    def __init__(self, store, path=None):
        self.store = store
        self.path = Path(path) if path is not None else None
        self.started_at = None
        self.storage_error = False
        self.lock = RLock()
        self.shutdown = Event()
        self.thread = None
        self._load()

    def _load(self):
        if self.path is None:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if set(data) != {"active", "started_at"} or type(data["active"]) is not bool:
                raise ValueError("invalid state")
            if data["active"]:
                started = datetime.fromisoformat(data["started_at"])
                if started.tzinfo is None or started > self.store.clock():
                    raise ValueError("invalid start time")
                self.started_at = started
            elif data["started_at"] is not None:
                raise ValueError("invalid inactive state")
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError):
            self.storage_error = True

    def _save(self, started):
        if self.path is None:
            return
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".ad-hoc-", suffix=".tmp", delete=False) as file:
                temporary = file.name
                json.dump({"active": started is not None,
                           "started_at": started.astimezone(timezone.utc).isoformat() if started else None}, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)

    def _reconcile(self, now):
        snapshot = self.store.snapshot
        # Also catch a reservation that began and ended between polls/restarts.
        if self.started_at and snapshot and any(
                e.blocks and e.start <= now and e.end > self.started_at for e in snapshot.events):
            self.started_at = None
            try:
                self._save(None)
                self.storage_error = False
            except OSError:
                # A disk error must never allow ad-hoc occupancy to outrank a booking.
                self.storage_error = True

    def _display(self):
        now = self.store.clock()
        self._reconcile(now)
        data = self.store.display()
        graph_healthy = (self.store.settings.data_source == "graph" and not data["warning"]
                         and data["status"] in ("free", "busy"))
        data.update({
            "ad_hoc_active": self.started_at is not None,
            "ad_hoc_started_at": self.started_at.astimezone(timezone.utc).isoformat() if self.started_at else None,
            "ad_hoc_started_epoch_ms": int(self.started_at.timestamp() * 1000) if self.started_at else None,
            "ad_hoc_elapsed_minutes": max(0, int((now - self.started_at).total_seconds() // 60)) if self.started_at else 0,
            "ad_hoc_can_start": graph_healthy and data["status"] == "free" and self.started_at is None and not self.storage_error,
            "ad_hoc_next_reservation_ms": None,
        })
        if self.store.settings.data_source == "graph" and not graph_healthy and data["status"] != "unknown":
            data.update(status="unknown", status_text="状態未確認", status_detail="最新の利用状況を確認できません")
        if self.started_at and graph_healthy and data["status"] == "free":
            data.update(status="ad-hoc", status_text="臨時使用中",
                        status_detail="使用開始 " + self.started_at.astimezone(now.tzinfo).strftime("%H:%M")
                        + " · 経過 " + str(data["ad_hoc_elapsed_minutes"]) + "分")
        if self.started_at and self.store.snapshot:
            future = [e.start for e in self.store.snapshot.events if e.blocks and e.start > now]
            if future:
                data["ad_hoc_next_reservation_ms"] = int(min(future).timestamp() * 1000)
        if self.storage_error:
            data["warning"] = True
            data["connection_text"] += " · 臨時使用の保存ファイルを確認してください（開始不可）"
        return data

    def display(self):
        with self.lock, self.store.lock:
            return self._display()

    def operate(self, action):
        with self.lock, self.store.lock:
            data = self._display()
            if action == "start":
                if self.started_at is not None:
                    return data
                if not data["ad_hoc_can_start"]:
                    raise AdHocError(409, "現在は臨時使用を開始できません。予定・更新状態を確認してください。")
                started = self.store.clock()
            elif action == "stop":
                if self.started_at is None and not self.storage_error:
                    return data
                started = None
            else:
                raise AdHocError(404, "操作が見つかりません")
            try:
                self._save(started)
            except OSError:
                self.storage_error = True
                raise AdHocError(503, "臨時使用状態を保存できません。自動で再送せず、表示を確認してください。") from None
            self.started_at = started
            self.storage_error = False
            return self._display()

    def start_monitor(self):
        def worker():
            while not self.shutdown.wait(1):
                with self.lock, self.store.lock:
                    self._reconcile(self.store.clock())
        self.thread = Thread(target=worker, name="idoutai-ad-hoc", daemon=True)
        self.thread.start()

    def close(self):
        self.shutdown.set()
        if self.thread:
            self.thread.join(timeout=2)
