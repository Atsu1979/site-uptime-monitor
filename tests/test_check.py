# -*- coding: utf-8 -*-
import unittest

from monitor import check as chk


def F(status=200, body=b"", headers=None, error=""):
    return chk.Fetched(status=status, body=body, headers=headers or {}, elapsed=0.1, error=error)


PAGE = {"id": "p", "url": "https://example.com/", "must_contain": "旅比較", "min_bytes": 10}
HEALTH = {"id": "h", "url": "https://example.com/health", "json_true": "ok"}


class Evaluate(unittest.TestCase):
    def test_up(self):
        self.assertEqual(chk.evaluate(PAGE, F(body="<title>旅比較</title>".encode())).state, "up")

    def test_connection_error_is_down(self):
        v = chk.evaluate(PAGE, F(status=0, error="URLError: timed out"))
        self.assertEqual(v.state, "down")
        self.assertIn("接続できない", v.reason)

    def test_http_500_is_down(self):
        v = chk.evaluate(PAGE, F(status=502, body=b"bad gateway"))
        self.assertEqual(v.state, "down")
        self.assertIn("502", v.reason)

    def test_short_body_is_down(self):
        v = chk.evaluate(PAGE, F(body="旅".encode()))
        self.assertEqual(v.state, "down")
        self.assertIn("短すぎる", v.reason)

    def test_missing_marker_is_down(self):
        v = chk.evaluate(PAGE, F(body=b"<html>maintenance</html>"))
        self.assertEqual(v.state, "down")
        self.assertIn("目印", v.reason)

    def test_cloudflare_challenge_is_not_down(self):
        v = chk.evaluate(PAGE, F(status=403, body=b"Just a moment", headers={"cf-mitigated": "challenge"}))
        self.assertEqual(v.state, "challenged")

    def test_plain_403_is_down(self):
        self.assertEqual(chk.evaluate(PAGE, F(status=403, body=b"forbidden")).state, "down")

    def test_health_true(self):
        self.assertEqual(chk.evaluate(HEALTH, F(body=b'{"ok": true, "chunks": 10}')).state, "up")

    def test_health_false(self):
        v = chk.evaluate(HEALTH, F(body=b'{"ok": false}'))
        self.assertEqual(v.state, "down")
        self.assertIn("true ではない", v.reason)

    def test_health_not_json(self):
        self.assertEqual(chk.evaluate(HEALTH, F(body=b"<html>")).state, "down")

    def test_health_truthy_string_is_not_enough(self):
        self.assertEqual(chk.evaluate(HEALTH, F(body=b'{"ok": "yes"}')).state, "down")


class Retry(unittest.TestCase):
    def run_seq(self, seq):
        it = iter(seq)
        waited = []
        v = chk.check(PAGE, "ua", fetcher=lambda url, timeout, ua: next(it), sleep=waited.append)
        return v, waited

    def test_recovers_on_third_try(self):
        bad = F(status=0, error="URLError: reset")
        good = F(body="旅比較 ok".encode())
        v, waited = self.run_seq([bad, bad, good])
        self.assertEqual((v.state, v.attempts), ("up", 3))
        self.assertEqual(waited, [20, 60])

    def test_down_after_three_failures(self):
        bad = F(status=503, body=b"x")
        v, waited = self.run_seq([bad, bad, bad])
        self.assertEqual((v.state, v.attempts), ("down", 3))

    def test_first_success_does_not_wait(self):
        v, waited = self.run_seq([F(body="旅比較 ok".encode())])
        self.assertEqual((v.state, v.attempts, waited), ("up", 1, []))

    def test_challenge_returns_immediately(self):
        ch = F(status=403, body=b"x", headers={"cf-mitigated": "challenge"})
        v, waited = self.run_seq([ch])
        self.assertEqual((v.state, v.attempts, waited), ("challenged", 1, []))


if __name__ == "__main__":
    unittest.main()
