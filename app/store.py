"""Single worker, atomic in-memory snapshot and sanitized failure state."""
from dataclasses import dataclass
from datetime import datetime
from threading import Event, RLock, Thread

from app.graph import GraphError
from app.schedule import JST, build_display, window_for

REFRESH_SECONDS = 60
STALE_SECONDS = 300
ERROR_TEXT = {
    "authentication": "Graph認証に失敗しました。環境変数・Secretの有効期限を確認してください。",
    "permission": "Graphの読み取り権限を確認してください。",
    "dependencies": "Graph用ライブラリをインストールしてください。",
    "invalid_response": "Graphの応答を検証できませんでした。",
    "throttled": "Graphの取得制限により待機しています。",
    "http": "Graphから正常な応答を取得できませんでした。",
    "communication": "Graphに接続できませんでした。",
}


@dataclass(frozen=True)
class Snapshot:
    events: tuple
    start: datetime
    end: datetime
    fetched_at: datetime


class ScheduleStore:
    def __init__(self, settings, client=None, config_error="", clock=None):
        self.settings, self.client = settings, client
        self.config_error = config_error
        self.clock = clock or (lambda: datetime.now(JST))
        self.snapshot = None
        self.error = ""
        self.lock = RLock()
        self.stop = Event()
        self.thread = None

    def refresh(self):
        start, end = window_for(self.clock())
        try:
            events = self.client.fetch(start, end)
            snapshot = Snapshot(tuple(events), start, end, self.clock())
        except Exception as exc:
            code = exc.code if isinstance(exc, GraphError) else "communication"
            with self.lock:
                self.error = ERROR_TEXT.get(code, ERROR_TEXT["communication"])
            return max(REFRESH_SECONDS, exc.retry_after) if isinstance(exc, GraphError) else REFRESH_SECONDS
        with self.lock:
            self.snapshot = snapshot
            self.error = ""
        return REFRESH_SECONDS

    def start(self):
        if self.config_error or self.settings.data_source != "graph":
            return
        def worker():
            while not self.stop.is_set():
                if self.stop.wait(self.refresh()):
                    return
        self.thread = Thread(target=worker, name="graph-calendar-reader", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)

    def display(self):
        now = self.clock()
        args = {"room_name": self.settings.room_name, "is_demo": self.settings.data_source == "dummy"}
        if self.config_error:
            return build_display(now, (), **dict(args, is_demo=False), unknown_text="設定エラー", warning=self.config_error)
        if self.settings.data_source == "dummy":
            return build_display(now, **args)
        with self.lock:
            snapshot, error = self.snapshot, self.error
        if snapshot is None:
            return build_display(now, (), **args, unknown_text="未取得" if error else "取得中",
                                 warning=error or "Microsoft 365から予定を取得しています。")
        stale = (now - snapshot.fetched_at).total_seconds() >= STALE_SECONDS
        covered = snapshot.start <= now < snapshot.end
        warning = ("更新遅延 · 前回取得の予定を表示しています。" + (" " + error if error else "")) if error or stale or not covered else ""
        return build_display(now, snapshot.events, **args, fetched_at=snapshot.fetched_at,
                             warning=warning, unknown_text="状態未確認" if stale or not covered else None)
