# -*- coding: utf-8 -*-
"""公開に出るもの（Actions のログ・status/ の記録）に、Secret の id・ホスト名・URL が出ないこと。"""
import contextlib, io, json, os, tempfile, unittest

from monitor import check as C
from monitor import main as M


class V:
    status, state, elapsed, attempts, reason = 503, "down", 0.1, 3, "HTTP 503"


class NoLeak(unittest.TestCase):
    def test_log_and_status_do_not_contain_secret_values(self):
        d = tempfile.mkdtemp()
        tf = os.path.join(d, "t.json")
        with open(tf, "w") as f:
            json.dump([{"id": "secretid-top", "label": "Site A (top)", "url": "https://secrethost.example/"}], f)
        orig = (C.check, C.cert_days_left)
        C.check, C.cert_days_left = (lambda t, ua: V()), (lambda h: 5)
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                M.main(["--dry-run", "--targets", tf, "--status-dir", d])
        finally:
            C.check, C.cert_days_left = orig
        out = buf.getvalue()
        for secret in ("secretid", "secrethost"):
            self.assertNotIn(secret, out)
        self.assertIn("Site A (top)", out)


    def test_connection_error_reason_hides_host(self):
        import ssl
        import urllib.error
        import urllib.request
        url = "https://secrethost.example/page"
        err = urllib.error.URLError(ssl.SSLCertVerificationError("hostname 'secrethost.example' doesn't match"))
        orig = urllib.request.urlopen

        def boom(*a, **k):
            raise err
        urllib.request.urlopen = boom
        try:
            f = C.fetch(url, 5, "ua")
        finally:
            urllib.request.urlopen = orig
        self.assertNotIn("secrethost", f.error)
        self.assertIn("<host>", f.error)


if __name__ == "__main__":
    unittest.main()
