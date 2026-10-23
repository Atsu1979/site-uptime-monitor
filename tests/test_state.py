# -*- coding: utf-8 -*-
import datetime as dt
import json
import os
import tempfile
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


class Recovery(unittest.TestCase):
    def test_down_to_up_is_recovery(self):
        st = S.empty()
        S.apply(st, "a", "A", "u", "up", "", T(0))
        self.assertFalse(S.is_recovery(S.apply(st, "a", "A", "u", "down", "x", T(10))))
        self.assertTrue(S.is_recovery(S.apply(st, "a", "A", "u", "up", "", T(20))))

    def test_challenged_to_up_is_recovery(self):
        self.assertTrue(S.is_recovery({"old": "challenged", "new": "up"}))

    def test_first_observation_and_no_event_are_not_recovery(self):
        self.assertFalse(S.is_recovery(None))
        self.assertFalse(S.is_recovery({"old": None, "new": "up"}))
        self.assertFalse(S.is_recovery({"old": "up", "new": "up"}))


class History(unittest.TestCase):
    def test_record_is_bounded(self):
        st = S.empty()
        for _ in range(10):
            S.record(st, "a", "up", size=4)
        S.record(st, "a", "down", size=4)
        self.assertEqual(st["history"]["a"], "uuud")

    def test_size_is_capped(self):
        st = S.empty()
        for _ in range(3):
            S.record(st, "a", "up", size=10 ** 9)
        self.assertEqual(st["history"]["a"], "uuu")
        st["history"]["a"] = "u" * (S.MAX_HISTORY_SIZE + 5)
        S.record(st, "a", "up", size=10 ** 9)
        self.assertEqual(len(st["history"]["a"]), S.MAX_HISTORY_SIZE)

    def test_size_zero_disables_and_clears(self):
        st = S.empty()
        S.record(st, "a", "up", size=5)
        S.record(st, "a", "up", size=0)
        self.assertNotIn("a", st["history"])

    def test_shrinking_size_truncates_oldest(self):
        st = S.empty()
        for state in ("down", "down", "up", "up"):
            S.record(st, "a", state, size=10)
        S.record(st, "a", "up", size=3)
        self.assertEqual(st["history"]["a"], "uuu")

    def test_all_up_window_does_not_change_once_full(self):
        # 全部 up なら state.json が毎回変わらない（＝ワークフローが毎回コミットしない）こと
        st = S.empty()
        for _ in range(5):
            S.record(st, "a", "up", size=5)
        before = S.dump(st)
        S.record(st, "a", "up", size=5)
        self.assertEqual(S.dump(st), before)

    def test_unknown_state_counts_as_down(self):
        st = S.empty()
        S.record(st, "a", "weird", size=5)
        self.assertEqual(st["history"]["a"], "d")

    def test_prune_removes_unmonitored_targets(self):
        st = S.empty()
        S.record(st, "a", "up")
        S.record(st, "gone", "up")
        S.prune_history(st, {"a"})
        self.assertEqual(set(st["history"]), {"a"})


class Availability(unittest.TestCase):
    def st(self, hist):
        st = S.empty()
        st["history"]["a"] = hist
        return st

    def test_percent_over_window(self):
        self.assertEqual(S.availability(self.st("uuud"), "a"), (75.0, 4))

    def test_last_n(self):
        self.assertEqual(S.availability(self.st("dduu"), "a", last=2), (100.0, 2))

    def test_challenged_is_excluded(self):
        self.assertEqual(S.availability(self.st("ucud"), "a"), (200.0 / 3, 3))

    def test_no_data(self):
        self.assertEqual(S.availability(S.empty(), "a"), (None, 0))
        self.assertEqual(S.availability(self.st("ccc"), "a"), (None, 0))

    def test_all_down(self):
        self.assertEqual(S.availability(self.st("ddd"), "a"), (0.0, 3))


class LoadCompat(unittest.TestCase):
    def write(self, data):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "state.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return p

    def test_missing_file_is_empty(self):
        self.assertEqual(S.load(os.path.join(tempfile.mkdtemp(), "nope.json")), S.empty())

    def test_old_file_without_history_loads(self):
        old = {"incidents": [], "last_heartbeat": "2026-W43",
               "targets": {"a": {"issue": None, "name": "A", "reason": "正常", "since": "2026-10-18T17:35:37Z",
                                 "state": "up", "url": ""}}}
        st = S.load(self.write(old))
        self.assertEqual(st["history"], {})
        self.assertEqual(st["targets"]["a"]["state"], "up")
        S.record(st, "a", "up")
        self.assertEqual(S.availability(st, "a"), (100.0, 1))

    def test_committed_state_json_loads(self):
        here = os.path.dirname(os.path.abspath(__file__))
        st = S.load(os.path.join(here, os.pardir, "status", "state.json"))
        self.assertIn("history", st)
        self.assertTrue(st["targets"])

    def test_target_missing_optional_keys_and_wrong_types(self):
        st = S.load(self.write({"targets": {"a": {"name": "A", "state": "down", "since": "2026-10-18T17:35:37Z"}},
                                "incidents": None, "history": []}))
        self.assertEqual((st["incidents"], st["history"], st["last_heartbeat"]), ([], {}, ""))
        self.assertIsNone(st["targets"]["a"]["issue"])
        S.render_status(st)      # 欠けた鍵があっても表が作れること
        self.assertIsNotNone(S.apply(st, "a", "A", "", "up", "", T(0)))


if __name__ == "__main__":
    unittest.main()
