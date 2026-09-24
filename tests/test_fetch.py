# -*- coding: utf-8 -*-
"""fetch() を本物の urllib で通す。手元に小さなHTTPサーバを立てるので、外へは出ない。

判定のテストだけでは、urllib の返す形（ヘッダの型など）の誤りを捕まえられなかった。
実際、ヘッダの扱いの誤りが『接続できない』と誤判定され、サイトは生きているのに障害扱いになった。
"""
import http.server
import threading
import unittest

from monitor import check as chk


class _H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/ok":
            body = "<title>旅比較</title>".encode("utf-8")
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


class Fetch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
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
        self.assertIn("旅比較", f.body.decode("utf-8"))

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
