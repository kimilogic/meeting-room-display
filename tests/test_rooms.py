"""Multi-room routing and isolation; all Graph connections are mocked."""
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from app.config import ConfigError, Room, Settings, load_settings
from app.graph import GraphError
from app.main import create_app
from app.schedule import JST, normalize_event
from test_http import get, running

IDS = ("idoutai", "bc-first", "bc-second", "ds-asagaya", "ds-tachikawa-1", "ds-tachikawa-2")
ROOMS = tuple(Room(identity, "試験室 " + identity, identity + "@example.invalid") for identity in IDS)


class RoomConfigTests(unittest.TestCase):
    def read(self, content):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            path.write_text(content, encoding="utf-8")
            return load_settings(path)

    def text(self, rooms=ROOMS, source="graph"):
        return 'DATA_SOURCE = "' + source + '"\n' + "\n".join(
            '[[rooms]]\nid = "' + r.id + '"\nname = "' + r.name + '"\nupn = "' + r.upn + '"'
            for r in rooms)

    def test_six_room_settings(self):
        settings = self.read(self.text())
        self.assertEqual(settings.rooms, ROOMS)
        self.assertEqual(settings.data_source, "graph")

    def test_supplied_room_mapping(self):
        deployed = {room.id: room.upn for room in load_settings().room_definitions()}
        locals = ("idoutai_meeting_room", "BC_First_meeting_room", "BC_Second_meeting_room",
                  "dsasa_meeting_room", "dsta_meeting_room", "dsta_meeting_room2")
        for identity, local in zip(IDS, locals):
            self.assertEqual(deployed[identity], local + "@araidenki7000.onmicrosoft.com")

    def test_invalid_room_definitions(self):
        invalid = (self.text(ROOMS + (ROOMS[0],)), self.text(ROOMS[1:]),
                   self.text((ROOMS[0], Room("bad/id", "bad", "bad@example.invalid"))),
                   self.text((ROOMS[0], Room("other", "other", ROOMS[0].upn.upper()))),
                   self.text((Room("idoutai", "test", ""),)),
                   self.text() + '\nCLIENT_SECRET = "never-print-this"',
                   'DATA_SOURCE="graph"\nrooms=[]')
        for content in invalid:
            with self.subTest(content=content):
                with self.assertRaises(ConfigError) as caught:
                    self.read(content)
                self.assertNotIn("never-print-this", str(caught.exception))

    def test_old_configuration_maps_to_idoutai(self):
        settings = self.read('DATA_SOURCE="graph"\nROOM_NAME="旧室"\nROOM_UPN="old@example.invalid"')
        self.assertEqual(settings.room_definitions(), (Room("idoutai", "旧室", "old@example.invalid"),))

    def test_new_room_needs_only_config(self):
        settings = self.read(self.text(ROOMS + (Room("new-room", "追加室", "new@example.invalid"),)))
        self.assertEqual(len(settings.rooms), 7)


def wait_for_snapshots(application):
    until = time.monotonic() + 3
    while time.monotonic() < until:
        if all(store.snapshot is not None for store in application.state.stores.values()):
            return
        time.sleep(0.01)
    raise AssertionError("Mock room fetch did not finish")


class RoomHttpTests(unittest.TestCase):
    def test_dummy_six_urls_root_and_unknown(self):
        application = create_app(Settings(data_source="dummy", rooms=ROOMS))
        with running(application) as port:
            root = get(port, "/")
            self.assertEqual(root[0], 200)
            self.assertEqual(root[2], get(port, "/room/idoutai")[2])
            for room in ROOMS:
                self.assertEqual(get(port, "/room/" + room.id)[0], 200)
                data = json.loads(get(port, "/api/room/" + room.id + "/status")[2])
                self.assertEqual(data["room_name"], room.name)
                self.assertEqual(data["room_id"], room.id)
                self.assertTrue(data["is_demo"])
                self.assertTrue(all(event["organizer"] for event in data["events"]))
            self.assertEqual(json.loads(get(port, "/api/display")[2])["room_id"], "idoutai")
            for path in ("/room/missing", "/api/room/missing/status", "/room/BC-FIRST"):
                status, _, body = get(port, path)
                self.assertEqual(status, 404)
                self.assertNotIn("room_name", json.loads(body))

    def test_shared_auth_and_correct_graph_target(self):
        made = []
        def factory(upn, token):
            client = Mock()
            client.fetch.return_value = ()
            made.append((upn, token, client))
            return client
        token = Mock()
        with patch("app.main.load_credentials", return_value=object()) as credentials, \
             patch("app.main.MsalTokenProvider", return_value=token) as auth, \
             patch("app.main.GraphClient", side_effect=factory):
            application = create_app(Settings(data_source="graph", rooms=ROOMS))
            with running(application):
                wait_for_snapshots(application)
                credentials.assert_called_once()
                auth.assert_called_once()
                for room, (upn, actual_token, client) in zip(ROOMS, made):
                    self.assertEqual(room.upn, upn)
                    self.assertIs(actual_token, token)
                    self.assertIs(application.state.stores[room.id].client, client)
                    client.fetch.assert_called_once()
                self.assertEqual(len({id(store) for store in application.state.stores.values()}), 6)

    def test_room_failure_isolation_organizer_and_privacy(self):
        now = datetime.now(JST)
        clients = {}
        for room in ROOMS:
            event = normalize_event({"id": room.id, "subject": "secret-" + room.id,
                "start": {"dateTime": (now - timedelta(minutes=1)).isoformat()},
                "end": {"dateTime": (now + timedelta(hours=1)).isoformat()},
                "isCancelled": False, "isAllDay": False, "showAs": "busy", "sensitivity": "private",
                "organizer": {"emailAddress": {"name": "予約者 " + room.id}}})
            clients[room.id] = Mock()
            clients[room.id].fetch.return_value = (event,)
        application = create_app(Settings(data_source="graph", rooms=ROOMS), clients=clients)
        with running(application) as port:
            wait_for_snapshots(application)
            originals = {identity: store.snapshot for identity, store in application.state.stores.items()}
            clients["bc-first"].fetch.side_effect = GraphError("communication")
            application.state.stores["bc-first"].refresh()
            for room in ROOMS:
                status, _, body = get(port, "/api/room/" + room.id + "/status")
                self.assertEqual(status, 200)
                data = json.loads(body)
                self.assertEqual(data["warning"], room.id == "bc-first")
                self.assertEqual(data["current"]["title"], "非公開の予定")
                self.assertEqual(data["current"]["organizer"], "予約者 " + room.id)
                self.assertNotIn("secret-", body.decode())
                self.assertNotIn("@example.invalid", body.decode())
                self.assertIs(application.state.stores[room.id].snapshot, originals[room.id])
            clients["bc-second"].fetch.return_value = ()
            application.state.stores["bc-second"].refresh()
            self.assertEqual(application.state.stores["bc-second"].snapshot.events, ())
            self.assertIs(application.state.stores["bc-first"].snapshot, originals["bc-first"])

    def test_initial_failure_one_room_only(self):
        clients = {room.id: Mock() for room in ROOMS}
        for client in clients.values():
            client.fetch.return_value = ()
        clients["bc-first"].fetch.side_effect = GraphError("permission")
        application = create_app(Settings(data_source="graph", rooms=ROOMS), clients=clients)
        with running(application) as port:
            for room in ROOMS:
                application.state.stores[room.id].refresh()
                data = json.loads(get(port, "/api/room/" + room.id + "/status")[2])
                self.assertEqual(data["status"], "unknown" if room.id == "bc-first" else "free")


if __name__ == "__main__":
    unittest.main()
