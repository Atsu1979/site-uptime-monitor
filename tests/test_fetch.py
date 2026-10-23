# -*- coding: utf-8 -*-
"""fetch() を本物の urllib で通す。手元に小さなHTTPサーバを立てるので、外へは出ない。

判定のテストだけでは、urllib の返す形（ヘッダの型など）の誤りを捕まえられなかった。
実際、ヘッダの扱いの誤りが『接続できない』と誤判定され、サイトは生きているのに障害扱いになった。
"""
import http.server
import socket
import ssl
import threading
import time
import unittest
import urllib.error
from unittest import mock

from monitor import check as chk


class _H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/slow":           # 応答の頭が来ない → urlopen の timeout
            time.sleep(1.5)
            return self._send(200, b"late")
        if self.path == "/drip":           # 少しずつ返し続ける → 1回の受信は間に合うが全体が終わらない
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            try:
                for _ in range(15):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.2)
            except OSError:
                pass
            return None
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if self.path == "/loop":
            self.send_response(302)
            self.send_header("Location", "/loop")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if self.path.startswith("/status/"):
            return self._send(int(self.path.rsplit("/", 1)[1]), b"server error")
        if self.path == "/ok":
            body = "<title>Example</title>".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("X-Test", "Yes")
        elif self.path == "/challenge":
            body = b"Just a moment..."
            self.send_response(403)
            self.send_header("cf-mitigated", "challenge")
        else:
            body = b"boom"
            self.send_response(502)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return None

    def _send(self, code, body):
        try:
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass                            # 相手（テスト側）が先に諦めて切った


class Fetch(unittest.TestCase):
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

    def test_200_with_lowercased_headers(self):
        f = chk.fetch(self.base + "/ok", 5, "test-ua")
        self.assertEqual((f.status, f.error), (200, ""))
        self.assertEqual(f.headers.get("x-test"), "Yes")
        self.assertIn("Example", f.body.decode("utf-8"))

    def test_http_error_keeps_status_and_headers(self):
        f = chk.fetch(self.base + "/challenge", 5, "test-ua")
        self.assertEqual(f.status, 403)
        self.assertEqual(f.headers.get("cf-mitigated"), "challenge")
        self.assertEqual(chk.evaluate({"url": "x"}, f).state, "challenged")

    def test_502_is_down(self):
        f = chk.fetch(self.base + "/x", 5, "test-ua")
        self.assertEqual(chk.evaluate({"url": "x"}, f).state, "down")

    def test_connection_refused_is_reported_as_error(self):
        f = chk.fetch("http://127.0.0.1:1/", 3, "test-ua")
        self.assertEqual(f.status, 0)
        self.assertTrue(f.error)

    def test_redirect_is_followed_to_final_page(self):
        f = chk.fetch(self.base + "/redirect", 5, "test-ua")
        self.assertEqual((f.status, f.error), (200, ""))
        self.assertEqual(chk.evaluate({"url": "x", "must_contain": "Example"}, f).state, "up")

    def test_redirect_loop_is_down(self):
        f = chk.fetch(self.base + "/loop", 5, "test-ua")
        self.assertEqual(f.status, 302)
        v = chk.evaluate({"url": "x"}, f)
        self.assertEqual(v.state, "down")
        self.assertIn("302", v.reason)

    def test_5xx_are_down(self):
        for code in (500, 502, 503, 504):
            with self.subTest(code=code):
                f = chk.fetch(self.base + "/status/%d" % code, 5, "test-ua")
                self.assertEqual(f.status, code)
                v = chk.evaluate({"url": "x"}, f)
                self.assertEqual(v.state, "down")
                self.assertIn(str(code), v.reason)

    def test_503_without_cloudflare_header_is_down_not_challenged(self):
        f = chk.fetch(self.base + "/status/503", 5, "test-ua")
        self.assertEqual(chk.evaluate({"url": "x"}, f).state, "down")

    def test_read_timeout_is_reported_as_error(self):
        t0 = time.monotonic()
        f = chk.fetch(self.base + "/slow", 0.4, "test-ua")
        self.assertEqual(f.status, 0)
        self.assertIn("timed out", f.error.lower())
        self.assertLess(time.monotonic() - t0, 1.4)
        self.assertEqual(chk.evaluate({"url": "x"}, f).state, "down")

    def test_slow_drip_is_cut_by_overall_deadline(self):
        # 1回の受信は 0.4 秒以内に来るので urlopen の timeout は効かない。全体の上限（timeout×2）で切る。
        t0 = time.monotonic()
        f = chk.fetch(self.base + "/drip", 0.4, "test-ua")
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual(f.status, 0)
        self.assertIn("TimeoutError: 全体で", f.error)

    def test_dns_failure_is_reported_as_error(self):
        # 本物の名前解決はしない。urlopen が DNS の失敗で返す形（URLError(gaierror)）をそのまま作る。
        err = urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, "Name or service not known"))
        with mock.patch.object(chk.urllib.request, "urlopen", side_effect=err):
            f = chk.fetch("https://no-such-host.invalid/", 3, "test-ua")
        self.assertEqual(f.status, 0)
        self.assertTrue(f.error.startswith("gaierror"))
        v = chk.evaluate({"url": "x"}, f)
        self.assertEqual(v.state, "down")
        self.assertIn("接続できない", v.reason)

    def test_tls_verification_failure_is_reported_as_error(self):
        err = urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed: certificate has expired"))
        with mock.patch.object(chk.urllib.request, "urlopen", side_effect=err):
            f = chk.fetch("https://expired.example/", 3, "test-ua")
        self.assertEqual(f.status, 0)
        self.assertIn("SSLCertVerificationError", f.error)

    def test_fetch_from_worker_thread_does_not_use_signals(self):
        # SIGALRM はメインスレッド専用。別スレッドから呼んでも ValueError で落ちないこと
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("f", chk.fetch(self.base + "/ok", 5, "test-ua")))
        th.start()
        th.join(10)
        self.assertEqual(out["f"].status, 200)

    def test_programming_error_is_not_swallowed(self):
        # 通信の途中でプログラムの誤りが起きたら、『接続できない』にせず、そのまま落ちること
        real = chk.urllib.request.urlopen

        def broken(*a, **k):
            raise AttributeError("bug")

        chk.urllib.request.urlopen = broken
        try:
            with self.assertRaises(AttributeError):
                chk.fetch(self.base + "/ok", 3, "test-ua")
        finally:
            chk.urllib.request.urlopen = real


if __name__ == "__main__":
    unittest.main()
