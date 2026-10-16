import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from app.config import Credentials, Settings
from app.graph import GraphClient, GraphError, MsalTokenProvider, SELECT, retry_delay
from app.schedule import JST, build_display, normalize_event, organizer_for_display, window_for
from app.store import ScheduleStore


def raw_event(identity="one", start="2026-09-09T00:00:00.0000000", end="2026-09-09T01:00:00.0000000", **changes):
    event = {"id": identity, "subject": "試験予定", "start": {"dateTime": start, "timeZone": "UTC"},
             "end": {"dateTime": end, "timeZone": "UTC"}, "isCancelled": False,
             "isAllDay": False, "showAs": "busy", "sensitivity": "normal"}
    event.update(changes)
    return event


def response(body=None, status=200, headers=None):
    result = Mock(status_code=status, headers=headers or {})
    result.json.return_value = body
    return result


NOW = datetime(2026, 9, 9, 9, 30, tzinfo=JST)


class GraphTests(unittest.TestCase):
    def test_organizer_through_graph_and_display(self):
        organizer = {"emailAddress": {"name": "試験予約者", "address": "person@example.invalid"}}
        client, session = self.client([response({"value": [raw_event(organizer=organizer)]})])
        events = client.fetch(*window_for(NOW))
        data = build_display(NOW, events, is_demo=False, fetched_at=NOW)
        self.assertEqual(data["current"]["organizer"], "試験予約者")
        self.assertEqual(data["events"][0]["organizer"], "試験予約者")
        before = build_display(NOW - timedelta(hours=1), events, is_demo=False, fetched_at=NOW)
        self.assertEqual(before["next"]["organizer"], "試験予約者")
        self.assertIn("organizer", session.get.call_args.kwargs["params"]["$select"].split(","))

    def client(self, responses):
        session = Mock()
        session.get.side_effect = responses
        # Reserved example domain and synthetic token are test fixtures only.
        return GraphClient("room@example.invalid", Mock(return_value="synthetic-test-token"), session), session

    def test_pages_and_request_minimization(self):
        next_link = "https://graph.microsoft.com/v1.0/users/room/calendarView?$skiptoken=opaque"
        first = response({"value": [raw_event()], "@odata.nextLink": next_link})
        second = response({"value": [raw_event("two", "2026-09-10T02:00:00Z", "2026-09-10T03:00:00Z")]})
        client, session = self.client([first, second])
        events = client.fetch(*window_for(NOW))
        self.assertEqual(len(events), 2)
        call = session.get.call_args_list[0]
        self.assertTrue(call.args[0].endswith("/users/room%40example.invalid/calendarView"))
        self.assertEqual(call.kwargs["params"]["startDateTime"], "2026-09-09T00:00:00+09:00")
        self.assertEqual(call.kwargs["params"]["endDateTime"], "2026-09-11T00:00:00+09:00")
        self.assertEqual(call.kwargs["params"]["$select"], SELECT)
        self.assertFalse(call.kwargs["allow_redirects"])
        self.assertEqual(session.get.call_args_list[1].args[0], next_link)
        self.assertIsNone(session.get.call_args_list[1].kwargs["params"])
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_empty_response(self):
        client, _ = self.client([response({"value": []})])
        self.assertEqual(client.fetch(*window_for(NOW)), ())

    def test_invalid_and_external_next_links(self):
        for link in ("http://graph.microsoft.com/v1.0/x", "https://example.invalid/v1.0/x", "", None):
            with self.subTest(link=link):
                client, session = self.client([response({"value": [], "@odata.nextLink": link})])
                with self.assertRaises(GraphError):
                    client.fetch(*window_for(NOW))
                self.assertEqual(session.get.call_count, 1)

    def test_loop_rejected(self):
        url = "https://graph.microsoft.com/v1.0/users/room%40example.invalid/calendarView"
        client, _ = self.client([response({"value": [], "@odata.nextLink": url})])
        with self.assertRaises(GraphError):
            client.fetch(*window_for(NOW))

    def test_401_refreshes_once(self):
        client, _ = self.client([response(status=401), response({"value": []})])
        client.fetch(*window_for(NOW))
        self.assertEqual(client.token_provider.call_count, 2)
        client.token_provider.assert_called_with(force_refresh=True)

    def test_429_retry_after(self):
        client, _ = self.client([response(status=429, headers={"Retry-After": "120"})])
        with self.assertRaises(GraphError) as result:
            client.fetch(*window_for(NOW))
        self.assertEqual(result.exception.retry_after, 120)
        self.assertEqual(retry_delay("invalid"), 60)

    def test_bad_response_does_not_become_empty_success(self):
        bad = response()
        bad.json.side_effect = ValueError("sensitive raw body")
        for item in (response({}), response({"value": [{}]}), bad):
            with self.subTest(item=item):
                client, _ = self.client([item])
                with self.assertRaises(GraphError) as result:
                    client.fetch(*window_for(NOW))
                self.assertNotIn("sensitive", str(result.exception))

    def test_private_subject_and_unneeded_fields_not_retained(self):
        client, _ = self.client([response({"value": [raw_event(subject="DO-NOT-DISPLAY", sensitivity="private", body="secret-body", attendees=["secret-person"])]})])
        events = client.fetch(*window_for(NOW))
        data = build_display(NOW, events, is_demo=False, fetched_at=NOW)
        serialized = json.dumps(data, ensure_ascii=False)
        self.assertIn("非公開の予定", serialized)
        for value in ("DO-NOT-DISPLAY", "secret-body", "secret-person", "room@example.invalid", "synthetic-test-token"):
            self.assertNotIn(value, serialized + repr(events))

    def test_token_provider_uses_msal_and_memory_cache(self):
        fake_msal = Mock()
        application = fake_msal.ConfidentialClientApplication.return_value
        application.acquire_token_for_client.return_value = {"access_token": "synthetic-token"}
        credentials = Credentials("test-tenant", "test-client", "test-secret")
        with patch.dict("sys.modules", {"msal": fake_msal}):
            provider = MsalTokenProvider(credentials)
            self.assertEqual(provider(), "synthetic-token")
            provider(force_refresh=True)
            fake_msal.ConfidentialClientApplication.assert_called_once()
            self.assertEqual(fake_msal.ConfidentialClientApplication.call_args.kwargs["client_credential"], "test-secret")
            application.acquire_token_for_client.assert_called_with(scopes=["https://graph.microsoft.com/.default"])
            application.remove_tokens_for_client.assert_called_once()
            application.acquire_token_for_client.return_value = {"error_description": "do-not-expose"}
            with self.assertRaises(GraphError) as result:
                provider()
            self.assertNotIn("do-not-expose", str(result.exception))


class CalendarTests(unittest.TestCase):
    def test_organizer_fallbacks(self):
        for organizer, expected in (
            ({"emailAddress": {"name": " 試験予約者 ", "address": "person@example.invalid"}}, "試験予約者"),
            ({"emailAddress": {"address": "person@example.invalid"}}, "person@example.invalid"),
            ({"emailAddress": {"name": "  ", "address": "person@example.invalid"}}, "person@example.invalid"),
            (None, "－"), ({}, "－"), ({"emailAddress": None}, "－"),
            ({"emailAddress": {"name": None, "address": ""}}, "－"),
        ):
            with self.subTest(organizer=organizer):
                self.assertEqual(normalize_event(raw_event(organizer=organizer)).organizer, expected)
        self.assertEqual(normalize_event(raw_event()).organizer, "－")

    def test_private_subject_masks_but_organizer_displays(self):
        raw = raw_event(subject="PRIVATE-SUBJECT", sensitivity="private",
                        organizer={"emailAddress": {"name": "試験予約者"}})
        data = build_display(NOW, [normalize_event(raw)], is_demo=False, fetched_at=NOW)
        self.assertEqual(data["current"]["title"], "非公開の予定")
        self.assertEqual(data["current"]["organizer"], "試験予約者")
        self.assertNotIn("PRIVATE-SUBJECT", json.dumps(data))
        self.assertEqual(organizer_for_display(raw, hide_private=True), "－")

    def test_dummy_and_overlap_organizers(self):
        dummy = build_display(NOW)
        self.assertEqual(dummy["current"]["organizer"], "テスト予約者1")
        self.assertEqual(dummy["next"]["organizer"], "テスト予約者2")
        self.assertTrue(all(e["organizer"].startswith("テスト予約者") for e in dummy["events"]))
        events = [normalize_event(raw_event(str(i), organizer={"emailAddress": {"name": "予約者" + str(i)}})) for i in (1, 2)]
        self.assertEqual(build_display(NOW, events)["current"]["organizer"], "予約者1 / 予約者2")

    def test_cancelled(self):
        self.assertIsNone(normalize_event(raw_event(isCancelled=True)))

    def test_free_shown_but_not_busy_or_next(self):
        event = normalize_event(raw_event(showAs="free"))
        data = build_display(NOW, [event])
        self.assertEqual(data["status"], "free")
        self.assertIsNone(data["current"])
        self.assertIsNone(data["next"])
        self.assertIn("空き時間扱い", data["events"][0]["title"])
        self.assertIsNone(build_display(NOW - timedelta(hours=1), [event])["next"])

    def test_empty_and_boundaries(self):
        event = normalize_event(raw_event())
        self.assertEqual(build_display(NOW, [])["status"], "free")
        self.assertEqual(build_display(event.start, [event])["status"], "busy")
        self.assertEqual(build_display(event.end, [event])["status"], "free")
        self.assertIsNotNone(build_display(event.start - timedelta(seconds=1), [event])["next"])

    def test_overlap_never_selects_one_end_time(self):
        events = [normalize_event(raw_event()), normalize_event(raw_event("two", end="2026-09-09T02:00:00Z", subject="別予定"))]
        data = build_display(NOW, events)
        self.assertEqual(data["status"], "busy")
        self.assertIn("予約重複（2件）", data["status_detail"])
        self.assertIn("試験予定", data["current"]["title"])
        self.assertIn("別予定", data["current"]["title"])
        self.assertNotIn("まで", data["status_detail"])

    def test_tomorrow_next_not_in_today_list(self):
        event = normalize_event(raw_event(start="2026-09-10T00:00:00Z", end="2026-09-10T01:00:00Z"))
        data = build_display(NOW, [event], is_demo=False)
        self.assertEqual(data["events"], [])
        self.assertEqual(data["next"]["start_text"], "09/10 09:00")

    def test_cross_midnight_all_day_and_timezone(self):
        event = normalize_event(raw_event(start="2026-09-08T15:00:00Z", end="2026-09-09T15:00:00Z", isAllDay=True))
        self.assertEqual(build_display(NOW, [event])["status"], "busy")
        self.assertIn("終日", build_display(NOW, [event])["current"]["time_text"])
        tokyo = raw_event(start="2026-09-09T09:00:00", end="2026-09-09T10:00:00")
        tokyo["start"]["timeZone"] = tokyo["end"]["timeZone"] = "Tokyo Standard Time"
        self.assertEqual(build_display(NOW, [normalize_event(tokyo)])["status"], "busy")


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.now = NOW
        self.settings = Settings("graph", "試験室", "room@example.invalid")

    def store(self, client):
        return ScheduleStore(self.settings, client, clock=lambda: self.now)

    def test_second_page_failure_preserves_entire_snapshot(self):
        session = Mock()
        session.get.side_effect = [response({"value": [raw_event()]}),
            response({"value": [raw_event("new")], "@odata.nextLink": "https://graph.microsoft.com/v1.0/next"}),
            response(status=503), response({"value": []})]
        store = self.store(GraphClient(self.settings.room_upn, Mock(return_value="test-token"), session))
        store.refresh()
        original = store.snapshot
        self.now += timedelta(seconds=60)
        store.refresh()
        self.assertIs(store.snapshot, original)
        data = store.display()
        self.assertEqual(data["updated_text"], "09/09 09:30:00")
        self.assertTrue(data["warning"])
        self.assertEqual(len(data["events"]), 1)
        self.now += timedelta(seconds=60)
        store.refresh()
        self.assertFalse(store.display()["warning"])
        self.assertEqual(store.snapshot.events, ())
        self.assertEqual(store.display()["updated_text"], "09/09 09:32:00")

    def test_never_fetched_and_config_error_not_free(self):
        client = Mock()
        client.fetch.side_effect = RuntimeError("sensitive details")
        store = self.store(client)
        self.assertEqual(store.display()["status"], "unknown")
        store.refresh()
        self.assertEqual(store.display()["status_text"], "未取得")
        self.assertNotIn("sensitive", json.dumps(store.display()))
        invalid = ScheduleStore(self.settings, config_error="環境変数が未設定です")
        self.assertEqual(invalid.display()["status_text"], "設定エラー")

    def test_stale_and_expired_coverage_not_free(self):
        client = Mock()
        client.fetch.return_value = ()
        store = self.store(client)
        store.refresh()
        self.assertEqual(store.display()["status"], "free")
        self.now += timedelta(seconds=300)
        self.assertEqual(store.display()["status_text"], "状態未確認")
        self.now += timedelta(days=2)
        self.assertEqual(store.display()["status"], "unknown")

    def test_api_reads_do_not_advance_fetch_timestamp(self):
        client = Mock()
        client.fetch.return_value = (normalize_event(raw_event()),)
        store = self.store(client)
        store.refresh()
        self.now += timedelta(seconds=15)
        self.assertEqual(store.display()["updated_text"], "09/09 09:30:00")
        self.assertEqual(client.fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
