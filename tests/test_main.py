# -*- coding: utf-8 -*-
"""main() を通しで動かす。判定（check/cert_check）と通知先は差し替えるので、外へは出ない。"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from monitor import check as chk
from monitor import main as M
from monitor import notify
from monitor import state as S

TARGETS = [
    {"id": "t1", "label": "Site A (top)", "name": "real-a", "url": "https://a.example/", "must_contain": "x"},
    {"id": "t2", "label": "Site B (top)", "name": "real-b", "url": "http://b.example/"},
]
ENV = {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "", "WEBHOOK_URL": "", "GITHUB_TOKEN": "",
       "GITHUB_REPOSITORY": "", "CERT_WARN_DAYS": "", "HISTORY_SIZE": "", "NOTIFY_TIMEOUT": ""}


class Run(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.targets = os.path.join(self.dir, "targets.json")
        with open(self.targets, "w", encoding="utf-8") as f:
            json.dump(TARGETS, f)
        self.status = os.path.join(self.dir, "status")
        self.status_md = os.path.join(self.dir, "STATUS.md")
        self.verdicts = {"t1": "up", "t2": "up"}
        self.cert = chk.Verdict("up", "証明書の残り 80日")
        self.sent = []
        self.cert_calls = []

    def fake_check(self, target, ua):
        state = self.verdicts[target["id"]]
        return chk.Verdict(state, "正常" if state == "up" else "HTTP 502（期待は 200）", 200 if state == "up" else 502)

    def fake_cert(self, host, warn_days):
        self.cert_calls.append((host, warn_days))
        return self.cert

    def run_main(self, *extra, **env):
        argv = ["--targets", self.targets, "--status-dir", self.status, "--status-md", self.status_md] + list(extra)
        with mock.patch.dict(os.environ, dict(ENV, **env)), \
                mock.patch.object(M.chk, "check", self.fake_check), \
                mock.patch.object(M.chk, "cert_check", self.fake_cert), \
                mock.patch.object(M.notify, "send", side_effect=lambda text, ev=None: self.sent.append((text, ev))), \
                mock.patch.object(M.notify, "issue_open", return_value=None), \
                mock.patch.object(M.notify, "issue_close", return_value=False), \
                mock.patch("builtins.print"):
            return M.main(argv)

    def events(self, kind=None):
        return [ev for _, ev in self.sent if ev and ev.get("type") not in ("weekly",) and (kind is None or ev["type"] == kind)]

    def state(self):
        return S.load(os.path.join(self.status, "state.json"))

    def test_down_then_recovered_notifies_twice_with_availability(self):
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.events(), [])                     # 最初の up は黙る
        self.verdicts["t1"] = "down"
        self.run_main()
        self.run_main()                                         # 落ちたまま → もう鳴らさない
        self.verdicts["t1"] = "up"
        self.run_main()
        kinds = [e["type"] for e in self.events()]
        self.assertEqual(kinds, ["down", "recovered"])
        down, rec = self.events()
        self.assertEqual((down["id"], down["old"], down["new"]), ("t1", "up", "down"))
        self.assertEqual(down["availability_pct"], 50.0)        # u d
        self.assertEqual((rec["old"], rec["new"], rec["availability_checks"]), ("down", "up", 4))
        self.assertEqual(rec["availability_pct"], 50.0)         # u d d u
        texts = [t for t, ev in self.sent if ev and ev["type"] in ("down", "recovered")]
        self.assertIn("落ちています", texts[0])
        self.assertIn("直近2回の稼働率: 50.0%", texts[0])
        self.assertIn("戻りました", texts[1])
        self.assertIn("https://a.example/", texts[1])          # 実URLは通知にだけ載る
        self.assertEqual(self.state()["history"]["t1"], "uddu")

    def test_real_names_and_urls_are_not_committed(self):
        self.verdicts["t1"] = "down"
        self.run_main()
        for path in (os.path.join(self.status, "state.json"), os.path.join(self.status, "incidents.md"), self.status_md):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn("a.example", text, path)
            self.assertNotIn("real-a", text, path)
        for _, ev in self.sent:
            self.assertNotIn("a.example", json.dumps(ev or {}))

    def test_old_state_file_without_history_is_upgraded(self):
        os.makedirs(self.status)
        old = {"incidents": [], "last_heartbeat": "",
               "targets": {"t1": {"issue": None, "name": "Site A (top)", "reason": "正常",
                                  "since": "2026-10-18T17:35:37Z", "state": "up", "url": ""}}}
        with open(os.path.join(self.status, "state.json"), "w", encoding="utf-8") as f:
            json.dump(old, f)
        self.assertEqual(self.run_main(), 0)
        st = self.state()
        self.assertEqual(st["history"], {"t1": "u", "t2": "u"})
        self.assertEqual(st["targets"]["t1"]["since"], "2026-10-18T17:35:37Z")

    def test_state_file_is_stable_once_history_is_full_and_all_up(self):
        for _ in range(3):
            self.run_main(HISTORY_SIZE="3")
        path = os.path.join(self.status, "state.json")
        with open(path, encoding="utf-8") as f:
            before = f.read()
        self.run_main(HISTORY_SIZE="3")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)

    def test_history_size_zero_disables_history(self):
        self.run_main(HISTORY_SIZE="0")
        self.assertEqual(self.state()["history"], {})

    def test_removed_target_history_is_pruned(self):
        self.run_main()
        st = self.state()
        st["history"]["gone"] = "uuu"
        S.write_if_changed(os.path.join(self.status, "state.json"), S.dump(st))
        self.run_main()
        self.assertNotIn("gone", self.state()["history"])

    def test_cert_expiring_is_notified_and_threshold_is_configurable(self):
        self.run_main(CERT_WARN_DAYS="30")
        self.assertEqual(self.cert_calls, [("a.example", 30)])  # https の対象だけ
        self.cert = chk.Verdict("expiring", "証明書の残り 9日（30日前から警告）")
        self.run_main(CERT_WARN_DAYS="30")
        ev = self.events("expiring")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["id"], "cert:c1")
        self.assertEqual(self.state()["targets"]["cert:c1"]["state"], "expiring")

    def test_bad_cert_warn_days_falls_back_to_default(self):
        self.run_main(CERT_WARN_DAYS="soon")
        self.assertEqual(self.cert_calls, [("a.example", 14)])

    def test_cert_unreadable_keeps_previous_state(self):
        self.run_main()
        self.cert = None
        self.run_main()
        self.assertEqual(self.state()["targets"]["cert:c1"]["state"], "up")

    def test_cert_expired_message(self):
        self.cert = chk.Verdict("expired", "証明書の期限が切れている")
        self.run_main()
        texts = [t for t, ev in self.sent if ev and ev["type"] == "expired"]
        self.assertEqual(len(texts), 1)
        self.assertTrue(texts[0].startswith("🔴"))

    def test_dry_run_writes_nothing_and_sends_nothing(self):
        self.verdicts["t1"] = "down"
        self.assertEqual(self.run_main("--dry-run"), 0)
        self.assertEqual(self.sent, [])
        self.assertFalse(os.path.exists(self.status))
        self.assertFalse(os.path.exists(self.status_md))

    def test_weekly_summary_is_sent_once_per_week(self):
        self.run_main()
        self.run_main()
        weekly = [t for t, ev in self.sent if ev and ev["type"] == "weekly"]
        self.assertEqual(len(weekly), 1)
        self.assertIn("直近1回 100.00%", weekly[0])


class NotificationFailures(unittest.TestCase):
    """通知先が壊れていても監視の実行は 0 で終わり、状態は書き出されること（send は差し替えない）。"""

    def test_broken_webhook_and_telegram_do_not_crash_the_run(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        targets = os.path.join(d, "targets.json")
        with open(targets, "w", encoding="utf-8") as f:
            json.dump([{"id": "t", "label": "T", "url": "http://t.example/"}], f)
        env = dict(ENV, WEBHOOK_URL="http://127.0.0.1:1/hook", TELEGRAM_BOT_TOKEN="x", TELEGRAM_CHAT_ID="y",
                   NOTIFY_TIMEOUT="1")
        down = chk.Verdict("down", "接続できない（gaierror: x）")
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(M.chk, "check", return_value=down), \
                mock.patch.object(notify.urllib.request, "urlopen", wraps=notify.urllib.request.urlopen) as op, \
                mock.patch.object(notify, "telegram", side_effect=RuntimeError("boom")), \
                mock.patch("builtins.print"):
            rc = M.main(["--targets", targets, "--status-dir", os.path.join(d, "status"),
                         "--status-md", os.path.join(d, "STATUS.md")])
        self.assertEqual(rc, 0)
        self.assertTrue(op.called)                               # Webhook は実際に送ろうとして失敗した
        st = S.load(os.path.join(d, "status", "state.json"))
        self.assertEqual(st["targets"]["t"]["state"], "down")

    def test_test_notify_without_any_channel_returns_1(self):
        with mock.patch.dict(os.environ, ENV), mock.patch("builtins.print"):
            self.assertEqual(M.main(["--test-notify"]), 1)

    def test_test_notify_uses_webhook_too(self):
        with mock.patch.dict(os.environ, ENV), mock.patch.object(notify, "webhook", return_value=True) as wh, \
                mock.patch("builtins.print"):
            self.assertEqual(M.main(["--test-notify"]), 0)
        self.assertEqual(wh.call_args[0][1], {"type": "test"})


class Message(unittest.TestCase):
    EV = {"id": "t", "name": "A", "url": "", "old": "up", "new": "down", "reason": "HTTP 502",
          "at": "2026-10-18T17:35:00Z", "started": "2026-10-18T17:00:00Z"}

    def test_without_history_has_no_availability_line(self):
        self.assertNotIn("稼働率", M.message(self.EV, "https://x/"))
        self.assertNotIn("稼働率", M.message(self.EV, "https://x/", (None, 0)))

    def test_payload_types(self):
        self.assertEqual(M.event_payload(self.EV)["type"], "down")
        rec = dict(self.EV, old="down", new="up")
        self.assertEqual(M.event_payload(rec, (99.3333, 144))["type"], "recovered")
        self.assertEqual(M.event_payload(rec, (99.3333, 144))["availability_pct"], 99.33)


if __name__ == "__main__":
    unittest.main()
