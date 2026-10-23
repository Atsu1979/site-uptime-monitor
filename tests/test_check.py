# -*- coding: utf-8 -*-
import datetime as dt
import ssl
import unittest
from unittest import mock

from monitor import check as chk


def F(status=200, body=b"", headers=None, error=""):
    return chk.Fetched(status=status, body=body, headers=headers or {}, elapsed=0.1, error=error)


PAGE = {"id": "p", "url": "https://example.com/", "must_contain": "Example", "min_bytes": 10}
HEALTH = {"id": "h", "url": "https://example.com/health", "json_true": "ok"}


class Evaluate(unittest.TestCase):
    def test_up(self):
        self.assertEqual(chk.evaluate(PAGE, F(body="<title>Example</title>".encode())).state, "up")

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
        good = F(body="Example ok".encode())
        v, waited = self.run_seq([bad, bad, good])
        self.assertEqual((v.state, v.attempts), ("up", 3))
        self.assertEqual(waited, [20, 60])

    def test_down_after_three_failures(self):
        bad = F(status=503, body=b"x")
        v, waited = self.run_seq([bad, bad, bad])
        self.assertEqual((v.state, v.attempts), ("down", 3))

    def test_first_success_does_not_wait(self):
        v, waited = self.run_seq([F(body="Example ok".encode())])
        self.assertEqual((v.state, v.attempts, waited), ("up", 1, []))

    def test_challenge_returns_immediately(self):
        ch = F(status=403, body=b"x", headers={"cf-mitigated": "challenge"})
        v, waited = self.run_seq([ch])
        self.assertEqual((v.state, v.attempts, waited), ("challenged", 1, []))


class EvaluateEdges(unittest.TestCase):
    def test_dns_failure_is_down(self):
        v = chk.evaluate(PAGE, F(status=0, error="gaierror: [Errno -2] Name or service not known"))
        self.assertEqual((v.state, v.status), ("down", 0))
        self.assertIn("gaierror", v.reason)

    def test_timeout_is_down(self):
        self.assertEqual(chk.evaluate(PAGE, F(status=0, error="TimeoutError: timed out")).state, "down")

    def test_5xx_are_down(self):
        for code in (500, 502, 503, 504):
            with self.subTest(code=code):
                self.assertEqual(chk.evaluate(PAGE, F(status=code, body=b"x" * 50)).state, "down")

    def test_cloudflare_challenge_on_429_and_503(self):
        for code in (429, 503):
            with self.subTest(code=code):
                v = chk.evaluate(PAGE, F(status=code, headers={"cf-mitigated": "Challenge"}))
                self.assertEqual(v.state, "challenged")

    def test_cloudflare_header_on_500_is_still_down(self):
        self.assertEqual(chk.evaluate(PAGE, F(status=500, headers={"cf-mitigated": "challenge"})).state, "down")

    def test_unredirected_3xx_is_down_unless_expected(self):
        self.assertEqual(chk.evaluate(PAGE, F(status=301)).state, "down")
        self.assertEqual(chk.evaluate({"url": "x", "expect_status": 301}, F(status=301)).state, "up")

    def test_timeout_then_dns_then_ok_recovers(self):
        seq = iter([F(status=0, error="TimeoutError: timed out"),
                    F(status=0, error="gaierror: temporary failure"),
                    F(body=b"Example ok")])
        v = chk.check(PAGE, "ua", fetcher=lambda *a: next(seq), sleep=lambda s: None)
        self.assertEqual((v.state, v.attempts), ("up", 3))

    def test_timeout_from_target_is_passed_to_fetcher(self):
        seen = []
        chk.check(dict(PAGE, timeout=3), "ua", fetcher=lambda url, timeout, ua: seen.append(timeout) or F(body=b"Example ok"))
        self.assertEqual(seen, [3.0])


def _expired_error():
    e = ssl.SSLCertVerificationError(1, "certificate verify failed: certificate has expired")
    e.verify_code = 10
    e.verify_message = "certificate has expired"
    return e


class Cert(unittest.TestCase):
    def test_verdict_thresholds(self):
        self.assertEqual(chk.cert_verdict(30, 14).state, "up")
        self.assertEqual(chk.cert_verdict(14, 14).state, "up")          # ちょうど N 日は警告しない（従来通り）
        self.assertEqual(chk.cert_verdict(13.9, 14).state, "expiring")
        self.assertEqual(chk.cert_verdict(0.5, 14).state, "expiring")
        self.assertEqual(chk.cert_verdict(-1, 14).state, "expired")

    def test_verdict_default_is_14_days(self):
        self.assertEqual(chk.CERT_WARN_DAYS, 14)
        self.assertEqual(chk.cert_verdict(10).state, "expiring")

    def test_up_reason_keeps_existing_format(self):
        self.assertEqual(chk.cert_verdict(86.2, 14).reason, "証明書の残り 86日")

    def test_expiring_reason_mentions_threshold(self):
        r = chk.cert_verdict(5, 30).reason
        self.assertIn("5日", r)
        self.assertIn("30日前", r)

    def test_check_uses_days_left(self):
        v = chk.cert_check("example.com", 14, days_left=lambda h, p, t: 3.0)
        self.assertEqual(v.state, "expiring")

    def test_expired_cert_is_detected_from_verification_error(self):
        def boom(h, p, t):
            raise _expired_error()
        self.assertEqual(chk.cert_check("example.com", 14, days_left=boom).state, "expired")

    def test_other_verification_error_is_skipped(self):
        def boom(h, p, t):
            e = ssl.SSLCertVerificationError(1, "hostname mismatch")
            e.verify_code = 62
            raise e
        self.assertIsNone(chk.cert_check("example.com", 14, days_left=boom))

    def test_connection_errors_are_skipped(self):
        for err in (ConnectionRefusedError(), TimeoutError(), OSError("dns"), ssl.SSLError()):
            with self.subTest(err=type(err).__name__):
                def boom(h, p, t, err=err):
                    raise err
                self.assertIsNone(chk.cert_check("example.com", 14, days_left=boom))

    def test_programming_error_is_not_swallowed(self):
        def boom(h, p, t):
            raise AttributeError("bug")
        with self.assertRaises(AttributeError):
            chk.cert_check("example.com", 14, days_left=boom)

    def test_cert_days_left_reads_not_after_without_network(self):
        not_after = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=10, hours=1)).strftime("%b %d %H:%M:%S %Y GMT")
        tls = mock.MagicMock()
        tls.__enter__.return_value = tls
        tls.getpeercert.return_value = {"notAfter": not_after}
        ctx = mock.MagicMock()
        ctx.wrap_socket.return_value = tls
        sock = mock.MagicMock()
        sock.__enter__.return_value = sock
        with mock.patch.object(chk.ssl, "create_default_context", return_value=ctx), \
                mock.patch.object(chk.socket, "create_connection", return_value=sock) as conn:
            days = chk.cert_days_left("example.com", 443, 5)
        conn.assert_called_once_with(("example.com", 443), timeout=5)
        ctx.wrap_socket.assert_called_once_with(sock, server_hostname="example.com")
        self.assertAlmostEqual(days, 10.04, delta=0.05)


if __name__ == "__main__":
    unittest.main()
