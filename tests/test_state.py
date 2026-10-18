# -*- coding: utf-8 -*-
import datetime as dt
import unittest

from monitor import state as S


def T(minutes):
    return dt.datetime(2026, 9, 24, 0, 0, tzinfo=S.UTC) + dt.timedelta(minutes=minutes)


class Transitions(unittest.TestCase):
    def setUp(self):
        self.st = S.empty()

    def apply(self, new, minutes, reason="r"):
        return S.apply(self.st, "a", "A", "https://a/", new, reason, T(minutes))

    def test_first_up_is_silent(self):
        self.assertIsNone(self.apply("up", 0))
        self.assertEqual(self.st["targets"]["a"]["state"], "up")

    def test_first_down_is_reported(self):
        ev = self.apply("down", 0)
        self.assertEqual((ev["old"], ev["new"]), (None, "down"))
        self.assertEqual(len(self.st["incidents"]), 1)

    def test_same_state_is_silent_and_does_not_touch_since(self):
        self.apply("up", 0)
        self.assertIsNone(self.apply("up", 10))
        self.assertEqual(self.st["targets"]["a"]["since"], S.iso(T(0)))

    def test_down_then_up_closes_incident_with_duration(self):
        self.apply("up", 0)
        down = self.apply("down", 10, "HTTP 502")
        self.assertEqual((down["old"], down["new"]), ("up", "down"))
        self.assertIsNone(self.apply("down", 20))
        up = self.apply("up", 95)
        self.assertEqual((up["old"], up["new"], up["started"]), ("down", "up", S.iso(T(10))))
        inc = self.st["incidents"][-1]
        self.assertEqual((inc["start"], inc["end"]), (S.iso(T(10)), S.iso(T(95))))
        self.assertEqual(S.duration(inc["start"], inc["end"]), "1時間25分")

    def test_down_to_challenged_rolls_incident(self):
        self.apply("up", 0)
        self.apply("down", 10)
        ev = self.apply("challenged", 30)
        self.assertEqual((ev["old"], ev["new"]), ("down", "challenged"))
        self.assertIsNotNone(self.st["incidents"][0]["end"])
        self.assertIsNone(self.st["incidents"][1]["end"])


class Downtime(unittest.TestCase):
    def test_counts_only_down_inside_window(self):
        st = S.empty()
        S.apply(st, "a", "A", "u", "up", "", T(0))
        S.apply(st, "a", "A", "u", "down", "x", T(100))
        S.apply(st, "a", "A", "u", "up", "", T(130))
        S.apply(st, "a", "A", "u", "challenged", "cf", T(200))
        S.apply(st, "a", "A", "u", "up", "", T(260))
        self.assertAlmostEqual(S.downtime_minutes(st, "a", T(0), T(1000)), 30.0)
        self.assertAlmostEqual(S.downtime_minutes(st, "a", T(120), T(1000)), 10.0)

    def test_open_incident_counts_until_now(self):
        st = S.empty()
        S.apply(st, "a", "A", "u", "down", "x", T(0))
        self.assertAlmostEqual(S.downtime_minutes(st, "a", T(0), T(45)), 45.0)


class Render(unittest.TestCase):
    def test_incidents_table_empty(self):
        self.assertIn("まだ障害は無い", S.render_incidents(S.empty()))

    def test_status_table_shows_reason_only_when_not_up(self):
        st = S.empty()
        S.apply(st, "a", "A", "https://a/", "up", "正常", T(0))
        S.apply(st, "b", "B", "https://b/", "down", "HTTP 502", T(0))
        rows = {line.split("]")[0].lstrip("| ["): line for line in S.render_status(st).splitlines() if line.startswith("| [")}
        self.assertTrue(rows["A"].endswith("|  |"))        # 正常なら理由の欄は空
        self.assertTrue(rows["B"].endswith("| HTTP 502 |"))

    def test_week_key_uses_jst(self):
        self.assertEqual(S.week_key(dt.datetime(2026, 9, 27, 16, 0, tzinfo=S.UTC)), "2026-W40")


if __name__ == "__main__":
    unittest.main()
