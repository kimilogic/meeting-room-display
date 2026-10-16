import unittest
from datetime import datetime

from app.schedule import JST, build_display


class ScheduleTests(unittest.TestCase):
    def at(self, hour, minute=0, day=9):
        return build_display(datetime(2026, 9, day, hour, minute, tzinfo=JST))

    def test_before_first(self):
        data = self.at(8)
        self.assertEqual(data["status"], "free")
        self.assertIsNone(data["current"])
        self.assertEqual(data["next"]["title"], "朝会")

    def test_start_inclusive_end_exclusive(self):
        self.assertEqual(self.at(9)["current"]["title"], "朝会")
        self.assertEqual(self.at(9, 59)["status"], "busy")
        self.assertEqual(self.at(10)["status"], "free")
        self.assertEqual(self.at(10)["next"]["start_text"], "11:00")
        self.assertEqual(self.at(15, 29)["status"], "busy")
        self.assertEqual(self.at(15, 30)["status"], "free")

    def test_after_last(self):
        data = self.at(17)
        self.assertIsNone(data["next"])
        self.assertEqual(data["status"], "free")
        self.assertTrue(all(event["state"] == "past" for event in data["events"]))

    def test_new_day(self):
        data = self.at(0, day=10)
        self.assertEqual(len(data["events"]), 4)
        self.assertEqual(data["next"]["title"], "朝会")
        self.assertIn("09月10日", data["date_text"])
        self.assertTrue(all(event["state"] == "upcoming" for event in data["events"]))


if __name__ == "__main__":
    unittest.main()
