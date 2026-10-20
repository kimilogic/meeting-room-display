"""Six-room ad-hoc integration using fake Graph and temporary persistence only."""
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from app.ad_hoc import room_state_path
from app.config import Settings
from app.graph import GraphError
from app.main import create_app
from app.schedule import Event, JST
from test_ad_hoc import post
from test_http import get, running
from test_rooms import ROOMS


class MultiRoomAdHocTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name) / "idoutai-ad-hoc.json"
        self.clients = {r.id: Mock() for r in ROOMS}
        for client in self.clients.values():
            client.fetch.return_value = ()
        self.now = datetime.now(JST) - timedelta(hours=1)

    def application(self):
        return create_app(Settings(data_source="graph", rooms=ROOMS),
                          clients=self.clients, ad_hoc_path=self.base)

    def ready(self, app):
        for store in app.state.stores.values():
            store.clock = lambda: self.now
            store.refresh()

    def status(self, port, room):
        return json.loads(get(port, "/api/room/" + room + "/status")[2])

    def action(self, port, room, action):
        return post(port, "/api/room/" + room + "/ad-hoc/" + action)

    def test_all_rooms_start_isolation_reload_restart_stop(self):
        originals = {}
        app = self.application()
        with running(app) as port:
            # Use real current time here so startup restoration accepts every timestamp.
            for store in app.state.stores.values():
                store.refresh()
            for room in ROOMS:
                self.assertIn(b'id="ad-hoc-start"', get(port, "/room/" + room.id)[2])
                code, data = self.action(port, room.id, "start")
                self.assertEqual(code, 200)
                self.assertEqual(data["room_id"], room.id)
                self.assertEqual(data["status"], "ad-hoc")
                originals[room.id] = data["ad_hoc_started_at"]
                self.assertEqual(self.action(port, room.id, "start")[1]["ad_hoc_started_at"],
                                 originals[room.id])
                for other in ROOMS:
                    self.assertEqual(self.status(port, other.id)["ad_hoc_active"],
                                     other.id in originals)
            self.assertEqual(get(port, "/")[2], get(port, "/room/idoutai")[2])
            self.assertEqual(json.loads(get(port, "/api/display")[2])["ad_hoc_started_at"],
                             originals["idoutai"])
        self.assertEqual(len(list(self.base.parent.glob("*.json"))), 6)
        with running(self.application()) as port:
            for room in ROOMS:
                self.assertEqual(self.status(port, room.id)["ad_hoc_started_at"], originals[room.id])
            for room in ROOMS:
                for _ in range(2):
                    code, data = self.action(port, room.id, "stop")
                    self.assertEqual(code, 200)
                    self.assertFalse(data["ad_hoc_active"])
                del originals[room.id]
                for other in ROOMS:
                    self.assertEqual(self.status(port, other.id)["ad_hoc_active"], other.id in originals)
                saved = json.loads(room_state_path(room.id, self.base).read_text())
                self.assertEqual(saved, {"active": False, "started_at": None})
            for room in ("missing", "BC-FIRST", "equipment"):
                for action in ("start", "stop"):
                    self.assertEqual(self.action(port, room, action)[0], 404)

    def test_reservation_monitor_and_graph_failure_are_room_local(self):
        app = self.application()
        with running(app) as port:
            self.ready(app)
            for room in ROOMS:
                self.assertEqual(self.action(port, room.id, "start")[0], 200)
            event = Event("reserved", "正式予約", self.now + timedelta(minutes=1),
                          self.now + timedelta(hours=1), organizer="試験予約者")
            self.clients["bc-first"].fetch.return_value = (event,)
            app.state.stores["bc-first"].refresh()
            self.assertEqual(self.status(port, "bc-first")["next"]["organizer"], "試験予約者")
            self.now = event.start
            # Wait for the background monitor without a display request.
            until = time.monotonic() + 3
            while app.state.ad_hocs["bc-first"].started_at is not None and time.monotonic() < until:
                time.sleep(0.02)
            self.assertIsNone(app.state.ad_hocs["bc-first"].started_at)
            for room in ROOMS:
                data = self.status(port, room.id)
                self.assertEqual(data["ad_hoc_active"], room.id != "bc-first")
                self.assertEqual(data["status"], "busy" if room.id == "bc-first" else "ad-hoc")
            self.assertEqual(self.action(port, "bc-first", "start")[0], 409)
            self.assertFalse(json.loads(room_state_path("bc-first", self.base).read_text())["active"])
            self.action(port, "bc-second", "stop")
            self.clients["bc-second"].fetch.side_effect = GraphError("communication")
            app.state.stores["bc-second"].refresh()
            self.assertFalse(self.status(port, "bc-second")["ad_hoc_can_start"])
            self.assertEqual(self.action(port, "bc-second", "start")[0], 409)
            self.assertTrue(self.status(port, "idoutai")["ad_hoc_active"])

    def test_corrupt_one_room_file_does_not_block_others(self):
        room_state_path("ds-asagaya", self.base).write_text("invalid", encoding="utf-8")
        app = self.application()
        with running(app) as port:
            self.ready(app)
            for room in ROOMS:
                self.assertEqual(self.action(port, room.id, "start")[0],
                                 409 if room.id == "ds-asagaya" else 200)
