# -*- coding: utf-8 -*-
"""通知のテスト。外へは出ない（Webhook は手元のHTTPサーバ、Telegram は urlopen の差し替え）。

大事なのは2つ: 未設定なら何もしない／どんな失敗でも例外を外へ出さず、監視の実行を止めない。
"""
import http.server
import io
import json
import os
import threading
import time
import unittest
import urllib.error
from unittest import mock

from monitor import config
from monitor import notify

CLEAN = {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "", "WEBHOOK_URL": "", "NOTIFY_TIMEOUT": "",
         "GITHUB_TOKEN": "", "GITHUB_REPOSITORY": ""}


class _H(http.server.BaseHTTPRequestHandler):
    received = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        _H.received.append((self.path, self.headers.get("Content-Type"), body))
        if self.path == "/slow":
            time.sleep(2)
        code = 500 if self.path == "/fail" else 200
        try:
            self.send_response(code)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
        except OSError:
            pass


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Webhook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        cls.srv.daemon_threads = True
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        _H.received.clear()

    def env(self, **kw):
        return mock.patch.dict(os.environ, dict(CLEAN, **kw))

    def test_unset_is_noop(self):
        with self.env(), mock.patch.object(notify.urllib.request, "urlopen") as op:
            self.assertFalse(notify.webhook("hi"))
            op.assert_not_called()

    def test_posts_json_with_text_and_event(self):
        with self.env(WEBHOOK_URL=self.base + "/hook"):
            self.assertTrue(notify.webhook("🔴 落ちた", {"type": "down", "id": "t1"}))
        path, ctype, body = _H.received[0]
        self.assertEqual(path, "/hook")
        self.assertTrue(ctype.startswith("application/json"))
        self.assertEqual(json.loads(body.decode("utf-8")), {"text": "🔴 落ちた", "event": {"type": "down", "id": "t1"}})

    def test_server_error_returns_false(self):
        with self.env(WEBHOOK_URL=self.base + "/fail"):
            self.assertFalse(notify.webhook("x"))

    def test_unreachable_returns_false(self):
        with self.env(WEBHOOK_URL="http://127.0.0.1:1/hook"):
            self.assertFalse(notify.webhook("x"))

    def test_short_timeout_is_respected(self):
        t0 = time.monotonic()
        with self.env(WEBHOOK_URL=self.base + "/slow", NOTIFY_TIMEOUT="1"):
            self.assertFalse(notify.webhook("x"))
        self.assertLess(time.monotonic() - t0, 1.9)

    def test_non_http_scheme_is_refused(self):
        with self.env(WEBHOOK_URL="file:///etc/passwd"), mock.patch.object(notify.urllib.request, "urlopen") as op:
            self.assertFalse(notify.webhook("x"))
            op.assert_not_called()

    def test_error_log_does_not_leak_url(self):
        secret = "http://127.0.0.1:1/hook?token=SECRET123"
        with self.env(WEBHOOK_URL=secret), mock.patch("builtins.print") as pr:
            notify.webhook("x")
        logged = " ".join(str(c) for c in pr.call_args_list)
        self.assertNotIn("SECRET123", logged)


class Telegram(unittest.TestCase):
    def env(self, **kw):
        return mock.patch.dict(os.environ, dict(CLEAN, **kw))

    def test_unset_is_noop(self):
        with self.env(), mock.patch.object(notify.urllib.request, "urlopen") as op:
            self.assertFalse(notify.telegram("hi"))
            op.assert_not_called()

    def test_only_one_of_token_or_chat_is_noop(self):
        with self.env(TELEGRAM_BOT_TOKEN="t"), mock.patch.object(notify.urllib.request, "urlopen") as op:
            self.assertFalse(notify.telegram("hi"))
            op.assert_not_called()

    def test_sends_with_short_timeout(self):
        with self.env(TELEGRAM_BOT_TOKEN="tok", TELEGRAM_CHAT_ID="42"), \
                mock.patch.object(notify.urllib.request, "urlopen", return_value=_Resp()) as op:
            self.assertTrue(notify.telegram("hello"))
        args, kw = op.call_args
        self.assertEqual(args[0], "https://api.telegram.org/bottok/sendMessage")
        self.assertIn(b"chat_id=42", kw["data"])
        self.assertEqual(kw["timeout"], notify.DEFAULT_TIMEOUT)

    def test_failures_never_raise_and_never_leak_token(self):
        errors = [urllib.error.URLError("down"), TimeoutError(), ValueError("bad"),
                  urllib.error.HTTPError("https://api.telegram.org/botSECRET/sendMessage", 401, "no", {}, None)]
        for err in errors:
            with self.subTest(err=type(err).__name__):
                with self.env(TELEGRAM_BOT_TOKEN="SECRET", TELEGRAM_CHAT_ID="1"), \
                        mock.patch.object(notify.urllib.request, "urlopen", side_effect=err), \
                        mock.patch("builtins.print") as pr:
                    self.assertFalse(notify.telegram("x"))
                self.assertNotIn("SECRET", " ".join(str(c) for c in pr.call_args_list))


class Send(unittest.TestCase):
    def test_nothing_configured_is_false(self):
        with mock.patch.dict(os.environ, CLEAN):
            self.assertFalse(notify.send("x"))

    def test_one_channel_failing_does_not_stop_the_other(self):
        with mock.patch.object(notify, "telegram", side_effect=RuntimeError("bug")), \
                mock.patch.object(notify, "webhook", return_value=True) as wh:
            self.assertTrue(notify.send("x", {"type": "down"}))
        wh.assert_called_once_with("x", {"type": "down"})

    def test_all_failing_returns_false_without_raising(self):
        with mock.patch.object(notify, "telegram", side_effect=RuntimeError("bug")), \
                mock.patch.object(notify, "webhook", side_effect=OSError("x")):
            self.assertFalse(notify.send("x"))

    def test_github_issue_without_token_is_noop(self):
        with mock.patch.dict(os.environ, CLEAN), mock.patch.object(notify.urllib.request, "urlopen") as op:
            self.assertIsNone(notify.issue_open("t", "b"))
            self.assertFalse(notify.issue_close(None, "c"))
            op.assert_not_called()


class EnvNumber(unittest.TestCase):
    def get(self, value, **kw):
        with mock.patch.dict(os.environ, {"X_TEST_NUM": value}), mock.patch("builtins.print"):
            return config.env_number("X_TEST_NUM", 14, **kw)

    def test_unset_and_empty_use_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("X_TEST_NUM", None)
            self.assertEqual(config.env_number("X_TEST_NUM", 14), 14)
        self.assertEqual(self.get(""), 14)
        self.assertEqual(self.get("   "), 14)

    def test_parses_and_clamps(self):
        self.assertEqual(self.get("30"), 30)
        self.assertEqual(self.get("-5", lo=0), 0)
        self.assertEqual(self.get("99999", hi=365), 365)
        self.assertEqual(self.get("2.5", cast=float), 2.5)

    def test_garbage_uses_default(self):
        self.assertEqual(self.get("abc"), 14)
        self.assertEqual(self.get("1.5"), 14)        # int として読めない
        self.assertEqual(self.get("nan", cast=float), 14)


if __name__ == "__main__":
    unittest.main()
