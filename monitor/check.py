# -*- coding: utf-8 -*-
"""1つのURLを見て「生きているか」「中身が壊れていないか」を判定する。

判定は3段階:
  up          正常
  down        落ちている／壊れている（接続できない、HTTPが想定外、本文が短い、目印が無い、health が false）
  challenged  Cloudflare の確認画面に止められた。監視側が見られないだけで、サイトが落ちているとは限らない

一瞬の揺れで騒がないよう、3回（すぐ・20秒後・さらに60秒後）試して全部駄目なときだけ down にする。
"""
import contextlib
import http.client
import json
import signal
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_TIMEOUT = 15
RETRY_WAITS = (0, 20, 60)
MAX_BODY = 2_000_000


@dataclass
class Fetched:
    status: int = 0
    body: bytes = b""
    headers: dict = field(default_factory=dict)
    elapsed: float = 0.0
    error: str = ""


@dataclass
class Verdict:
    state: str
    reason: str
    status: int = 0
    elapsed: float = 0.0
    attempts: int = 1


@contextlib.contextmanager
def _deadline(seconds):
    """urlopen の timeout は『1回の受信の待ち時間』で、全体の上限ではない。
    相手が少しずつ返し続けると永遠に終わらないことがあるので、全体にも上限を掛ける。"""
    if not hasattr(signal, "SIGALRM"):
        yield
        return

    def _boom(signum, frame):
        raise TimeoutError("全体で%d秒を超えた" % seconds)

    old = signal.signal(signal.SIGALRM, _boom)
    signal.alarm(int(seconds))
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _lower(headers):
    return {str(k).lower(): str(v) for k, v in (headers or {}).items()}


def fetch(url, timeout, user_agent):
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "*/*"})
    t0 = time.monotonic()
    try:
        with _deadline(timeout * 2):
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read(MAX_BODY)
                return Fetched(r.status, body, _lower(r.headers), time.monotonic() - t0)
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read(200_000)
        except Exception:
            pass
        hdrs = _lower(e.headers) if e.headers else {}
        return Fetched(e.code, body, hdrs, time.monotonic() - t0)
    except (OSError, http.client.HTTPException) as e:
        # 接続できない・DNS・TLS・時間切れ・途中で切れた。ここに入るのは通信の失敗だけ。
        # プログラムの誤り（AttributeError など）は拾わない。拾うと『サイトが落ちた』と誤報する。
        # 落ちれば GitHub Actions の実行が失敗になり、監視そのものが壊れたことが持ち主に届く。
        reason = getattr(e, "reason", e)
        return Fetched(0, b"", {}, time.monotonic() - t0, type(reason).__name__ + ": " + str(reason)[:160])


def evaluate(target, f):
    """取ってきた結果を、その対象の条件で判定する。"""
    if f.error:
        return Verdict("down", "接続できない（%s）" % f.error, 0, f.elapsed)
    if f.status in (403, 429, 503) and f.headers.get("cf-mitigated", "").lower() == "challenge":
        return Verdict("challenged", "Cloudflareの確認画面で止められた（HTTP %d）" % f.status, f.status, f.elapsed)
    expect = int(target.get("expect_status", 200))
    if f.status != expect:
        return Verdict("down", "HTTP %d（期待は %d）" % (f.status, expect), f.status, f.elapsed)
    min_bytes = int(target.get("min_bytes", 0) or 0)
    if min_bytes and len(f.body) < min_bytes:
        return Verdict("down", "本文が短すぎる（%dバイト、下限%d）" % (len(f.body), min_bytes), f.status, f.elapsed)
    text = f.body.decode("utf-8", "replace")
    mark = target.get("must_contain")
    if mark and mark not in text:
        return Verdict("down", "目印の文字列「%s」が無い" % mark, f.status, f.elapsed)
    key = target.get("json_true")
    if key:
        try:
            data = json.loads(text)
        except ValueError:
            return Verdict("down", "JSONとして読めない", f.status, f.elapsed)
        if not (isinstance(data, dict) and data.get(key) is True):
            return Verdict("down", "%s が true ではない" % key, f.status, f.elapsed)
    return Verdict("up", "正常", f.status, f.elapsed)


def check(target, user_agent, fetcher=fetch, sleep=time.sleep, waits=RETRY_WAITS):
    """最大3回試す。1回でも up なら up。challenged は何度やっても同じなので、すぐ返す。"""
    timeout = float(target.get("timeout", DEFAULT_TIMEOUT))
    verdict = None
    for i, wait in enumerate(waits, 1):
        if wait:
            sleep(wait)
        verdict = evaluate(target, fetcher(target["url"], timeout, user_agent))
        verdict.attempts = i
        if verdict.state in ("up", "challenged"):
            return verdict
    return verdict


def cert_days_left(host, port=443, timeout=10):
    """TLS証明書の残り日数。"""
    ctx = ssl.create_default_context()
    with _deadline(timeout * 2):
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                not_after = tls.getpeercert()["notAfter"]
    return (ssl.cert_time_to_seconds(not_after) - time.time()) / 86400.0
