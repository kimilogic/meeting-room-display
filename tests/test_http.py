"""Real local HTTP using an ephemeral test port; never touches the running 8020 service."""
import http.client
import json
import socket
import threading
import time
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

import uvicorn

from app.config import ConfigError, Settings
from app.graph import GraphError
from app.main import create_app


@contextmanager
def running(application):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(application, log_level="critical", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        until = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < until:
            time.sleep(0.01)
        if not server.started:
            raise RuntimeError("Local test server did not start")
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


def get(port, path):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


class HttpTests(unittest.TestCase):
    def test_dummy_and_static_files(self):
        with running(create_app(Settings())) as port:
            for path in ("/", "/static/style.css", "/static/app.js"):
                self.assertEqual(get(port, path)[0], 200)
            status, headers, body = get(port, "/api/display")
            self.assertEqual(status, 200)
            self.assertIn("no-store", headers["cache-control"])
            data = json.loads(body)
            self.assertTrue(data["is_demo"])
            self.assertEqual(len(data["events"]), 4)
            self.assertTrue(all("organizer" in event for event in data["events"]))
            self.assertNotIn("CLIENT_SECRET", body.decode())

    def test_missing_credentials_returns_display_error_not_500(self):
        with patch("app.main.load_credentials", side_effect=ConfigError("環境変数が未設定です: CLIENT_SECRET")):
            with running(create_app(Settings("graph", "試験室", "room@example.invalid"))) as port:
                status, _, body = get(port, "/api/display")
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["status_text"], "設定エラー")

    def test_graph_failure_does_not_fail_display_api(self):
        client = Mock()
        client.fetch.side_effect = GraphError("permission")
        application = create_app(Settings("graph", "試験室", "room@example.invalid"), client)
        with running(application) as port:
            status, _, body = get(port, "/api/display")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["status"], "unknown")
            self.assertTrue(json.loads(body)["warning"])

    def test_graph_empty_success_and_later_failure(self):
        client = Mock()
        client.fetch.return_value = ()
        application = create_app(Settings("graph", "試験室", "room@example.invalid"), client)
        with running(application) as port:
            data = json.loads(get(port, "/api/display")[2])
            self.assertEqual(data["status"], "free")
            self.assertFalse(data["is_demo"])
            self.assertNotEqual(data["updated_text"], "—")
            client.fetch.side_effect = GraphError("communication")
            application.state.store.refresh()
            status, _, body = get(port, "/api/display")
            self.assertEqual(status, 200)
            failed = json.loads(body)
            self.assertEqual(failed["updated_text"], data["updated_text"])
            self.assertTrue(failed["warning"])


if __name__ == "__main__":
    unittest.main()
