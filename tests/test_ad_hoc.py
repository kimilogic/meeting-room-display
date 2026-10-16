import http.client
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from app.ad_hoc import AdHoc, AdHocError
from app.config import Room, Settings
from app.graph import GraphError
from app.main import create_app
from app.schedule import Event, JST
from app.store import ScheduleStore
from test_http import get, running


class AdHocTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "state.json"
        self.now = datetime(2026, 9, 11, 10, 20, tzinfo=JST)
        self.client = Mock()
        self.client.fetch.return_value = ()
        self.store = ScheduleStore(Settings("graph", "試験室", "room@example.invalid"), self.client, clock=lambda: self.now)
        self.store.refresh()
        self.state = AdHoc(self.store, self.path)

    def booking(self):
        return Event("formal", "正式予約", self.now + timedelta(minutes=10),
                     self.now + timedelta(minutes=40), organizer="試験予約者")

    def test_start_display_elapsed_stop(self):
        started = self.state.operate("start")
        self.assertEqual(started["status_text"], "臨時使用中")
        self.assertTrue(started["ad_hoc_active"])
        self.assertEqual(started["ad_hoc_elapsed_minutes"], 0)
        self.now += timedelta(minutes=8)
        self.store.refresh()
        self.assertEqual(self.state.display()["ad_hoc_elapsed_minutes"], 8)
        stopped = self.state.operate("stop")
        self.assertEqual(stopped["status"], "free")
        self.assertFalse(stopped["ad_hoc_active"])

    def test_repeated_and_concurrent_start_stop(self):
        original = self.state.operate("start")["ad_hoc_started_at"]
        self.now += timedelta(seconds=30)
        with ThreadPoolExecutor(max_workers=8) as pool:
            starts = list(pool.map(lambda _: self.state.operate("start"), range(16)))
            self.assertTrue(all(data["ad_hoc_started_at"] == original for data in starts))
            stops = list(pool.map(lambda _: self.state.operate("stop"), range(16)))
            self.assertTrue(all(not data["ad_hoc_active"] for data in stops))

    def test_formal_reservation_preempts_and_next_preserved(self):
        event = self.booking()
        self.client.fetch.return_value = (event,)
        self.store.refresh()
        started = self.state.operate("start")
        self.assertEqual(started["next"]["title"], "正式予約")
        self.assertEqual(started["next"]["organizer"], "試験予約者")
        self.assertEqual(started["ad_hoc_next_reservation_ms"], int(event.start.timestamp() * 1000))
        self.now = event.start
        self.store.refresh()
        current = self.state.display()
        self.assertEqual(current["status"], "busy")
        self.assertFalse(current["ad_hoc_active"])
        self.assertFalse(json.loads(self.path.read_text())["active"])
        self.assertEqual(self.state.operate("stop")["status"], "busy")
        with self.assertRaises(AdHocError):
            self.state.operate("start")

    def test_graph_failure_and_stale_and_unfetched_reject_start(self):
        self.client.fetch.side_effect = GraphError("communication")
        self.store.refresh()
        self.assertEqual(self.state.display()["status"], "unknown")
        with self.assertRaises(AdHocError):
            self.state.operate("start")
        self.client.fetch.side_effect = None
        self.store.refresh()
        self.now += timedelta(minutes=5)
        with self.assertRaises(AdHocError):
            self.state.operate("start")
        self.store.snapshot = None
        with self.assertRaises(AdHocError):
            self.state.operate("start")

    def test_outage_preserves_record_but_unknown_has_priority(self):
        original = self.state.operate("start")["ad_hoc_started_at"]
        self.client.fetch.side_effect = GraphError("communication")
        self.store.refresh()
        data = self.state.display()
        self.assertEqual(data["status"], "unknown")
        self.assertTrue(data["ad_hoc_active"])
        self.assertEqual(data["ad_hoc_started_at"], original)
        self.assertEqual(self.state.operate("stop")["status"], "unknown")

    def test_reload_and_restart_restore_only_two_fields(self):
        original = self.state.operate("start")
        self.assertEqual(self.state.display()["ad_hoc_started_at"], original["ad_hoc_started_at"])
        persisted = json.loads(self.path.read_text())
        self.assertEqual(set(persisted), {"active", "started_at"})
        self.assertNotIn("room@example.invalid", self.path.read_text())
        self.now += timedelta(minutes=1)
        restored = AdHoc(self.store, self.path).display()
        self.assertEqual(restored["ad_hoc_started_at"], original["ad_hoc_started_at"])
        self.assertEqual(restored["ad_hoc_elapsed_minutes"], 1)

    def test_reservation_passed_during_restart_does_not_revive(self):
        event = self.booking()
        self.state.operate("start")
        self.now = event.end + timedelta(minutes=1)
        self.client.fetch.return_value = (event,)
        self.store.refresh()
        restored = AdHoc(self.store, self.path)
        self.assertFalse(restored.display()["ad_hoc_active"])
        self.assertEqual(restored.display()["status"], "free")

    def test_monitor_releases_without_display_request(self):
        event = self.booking()
        self.client.fetch.return_value = (event,)
        self.store.refresh()
        self.state.operate("start")
        self.now = event.start
        self.state.start_monitor()
        self.addCleanup(self.state.close)
        until = time.monotonic() + 3
        while self.state.started_at is not None and time.monotonic() < until:
            time.sleep(0.02)
        self.assertIsNone(self.state.started_at)

    def test_write_failure_and_corrupt_file_fail_safely(self):
        with patch.object(self.state, "_save", side_effect=OSError("internal path must not leak")):
            with self.assertRaises(AdHocError) as caught:
                self.state.operate("start")
            self.assertEqual(caught.exception.status, 503)
            self.assertNotIn("internal path", str(caught.exception))
            self.assertIsNone(self.state.started_at)
        self.path.write_text("corrupt", encoding="utf-8")
        broken = AdHoc(self.store, self.path)
        self.assertFalse(broken.display()["ad_hoc_can_start"])

    def test_formal_preempts_even_when_disk_write_fails(self):
        event = self.booking()
        self.client.fetch.return_value = (event,)
        self.store.refresh()
        self.state.operate("start")
        self.now = event.start
        self.store.refresh()
        with patch.object(self.state, "_save", side_effect=OSError()):
            data = self.state.display()
        self.assertEqual(data["status"], "busy")
        self.assertFalse(data["ad_hoc_active"])

    def test_dummy_is_not_graph_authorization(self):
        dummy = AdHoc(ScheduleStore(Settings(), clock=lambda: self.now))
        with self.assertRaises(AdHocError):
            dummy.operate("start")

    def test_no_time_limit_and_free_event_does_not_preempt(self):
        self.state.operate("start")
        self.now += timedelta(days=2)
        self.client.fetch.return_value = (Event("free", "空き時間", self.now - timedelta(minutes=1),
                                               self.now + timedelta(hours=1), blocks=False),)
        self.store.refresh()
        data = self.state.display()
        self.assertTrue(data["ad_hoc_active"])
        self.assertEqual(data["ad_hoc_elapsed_minutes"], 2880)

    def test_stop_write_failure_retains_existing_record(self):
        original = self.state.operate("start")["ad_hoc_started_at"]
        with patch.object(self.state, "_save", side_effect=OSError()):
            with self.assertRaises(AdHocError):
                self.state.operate("stop")
        self.assertEqual(self.state.display()["ad_hoc_started_at"], original)
        self.assertTrue(self.state.display()["ad_hoc_active"])


def post(port, path, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request("POST", path, headers=headers if headers is not None else {"X-Room-Action": "1"})
        result = connection.getresponse()
        return result.status, json.loads(result.read())
    finally:
        connection.close()


class AdHocHttpTests(unittest.TestCase):
    def test_actions_reload_and_other_room_rejection(self):
        rooms = (Room("idoutai", "移動体試験", "one@example.invalid"), Room("other", "別室", "two@example.invalid"))
        clients = {room.id: Mock() for room in rooms}
        for client in clients.values():
            client.fetch.return_value = ()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            def application():
                return create_app(Settings(data_source="graph", rooms=rooms), clients=clients, ad_hoc_path=path)
            app = application()
            with running(app) as port:
                app.state.store.refresh()
                self.assertIn(b'id="ad-hoc-start"', get(port, "/")[2])
                self.assertNotIn(b'id="ad-hoc-start"', get(port, "/room/other")[2])
                status, data = post(port, "/api/room/idoutai/ad-hoc/start")
                self.assertEqual(status, 200)
                original = data["ad_hoc_started_at"]
                self.assertEqual(json.loads(get(port, "/api/display")[2])["ad_hoc_started_at"], original)
                self.assertEqual(post(port, "/api/room/idoutai/ad-hoc/start")[1]["ad_hoc_started_at"], original)
                self.assertEqual(get(port, "/room/idoutai")[0], 200)
                for identity in ("other", "missing"):
                    for action in ("start", "stop"):
                        self.assertEqual(post(port, "/api/room/" + identity + "/ad-hoc/" + action)[0], 404)
                other = json.loads(get(port, "/api/room/other/status")[2])
                self.assertFalse(any(key.startswith("ad_hoc") for key in other))
                self.assertEqual(post(port, "/api/room/idoutai/ad-hoc/stop", {})[0], 403)
            restarted = application()
            with running(restarted) as port:
                restarted.state.store.refresh()
                self.assertEqual(json.loads(get(port, "/api/display")[2])["ad_hoc_started_at"], original)
                self.assertFalse(post(port, "/api/room/idoutai/ad-hoc/stop")[1]["ad_hoc_active"])
                self.assertFalse(post(port, "/api/room/idoutai/ad-hoc/stop")[1]["ad_hoc_active"])
                clients["idoutai"].fetch.side_effect = GraphError("communication")
                restarted.state.store.refresh()
                self.assertEqual(post(port, "/api/room/idoutai/ad-hoc/start")[0], 409)


if __name__ == "__main__":
    unittest.main()
