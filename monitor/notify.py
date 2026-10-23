# -*- coding: utf-8 -*-
"""知らせる先: Telegram・汎用 Webhook・GitHub Issue。

鍵はリポジトリの Secrets から環境変数で渡す。コードにもログにも鍵は出さない。
知らせる先が設定されていなければ、黙って飛ばす（監視そのものは止めない）。
通知の失敗で監視の実行を落とさない: 例外はすべてここで止め、種類だけログに出す（本文は鍵や URL を含みうる）。
"""
import json
import os
import urllib.parse
import urllib.request

from .config import env_number

API = "https://api.github.com"
DEFAULT_TIMEOUT = 10


def _timeout():
    return env_number("NOTIFY_TIMEOUT", DEFAULT_TIMEOUT, lo=1, hi=60, cast=float)


def telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("  [telegram] 未設定のため送らない")
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
        with urllib.request.urlopen("https://api.telegram.org/bot" + token + "/sendMessage", data=data,
                                    timeout=_timeout()) as r:
            return 200 <= r.status < 300
    except Exception as e:
        print("  [telegram] 送れなかった: " + type(e).__name__)   # 例外の本文は鍵を含みうるので出さない
        return False


def webhook(text, event=None):
    """WEBHOOK_URL に JSON を POST する。本文は {"text": ..., "event": {...}}（Slack 互換の text を含む）。"""
    url = os.environ.get("WEBHOOK_URL", "").strip()
    if not url:
        return False
    try:
        if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
            print("  [webhook] WEBHOOK_URL は http(s) だけ使える")
            return False
        payload = {"text": text, "event": event or {}}
        req = urllib.request.Request(url, method="POST", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json; charset=utf-8",
                                              "User-Agent": "site-uptime-monitor"})
        with urllib.request.urlopen(req, timeout=_timeout()) as r:
            return 200 <= r.status < 300
    except Exception as e:
        print("  [webhook] 送れなかった: " + type(e).__name__)    # URL に鍵が入っていることがあるので出さない
        return False


def send(text, event=None):
    """設定されている通知先すべてに送る。どれか1つでも届けば True。決して例外を外へ出さない。"""
    ok = False
    for name, fn in (("telegram", lambda: telegram(text)), ("webhook", lambda: webhook(text, event))):
        try:
            ok = bool(fn()) or ok
        except Exception as e:      # 念のため。各通知先は自分で例外を止めているはず
            print("  [%s] 予期しない失敗: %s" % (name, type(e).__name__))
    return ok


def configured():
    """どの通知先が設定されているか（ログ用。値そのものは返さない）。"""
    names = []
    if os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() and os.environ.get("TELEGRAM_CHAT_ID", "").strip():
        names.append("telegram")
    if os.environ.get("WEBHOOK_URL", "").strip():
        names.append("webhook")
    return names


def _gh(method, path, payload=None):
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        return None
    try:
        req = urllib.request.Request(API + "/repos/" + repo + path, method=method,
                                     data=json.dumps(payload).encode() if payload is not None else None,
                                     headers={"Authorization": "Bearer " + token,
                                              "Accept": "application/vnd.github+json",
                                              "X-GitHub-Api-Version": "2022-11-28",
                                              "User-Agent": "site-uptime-monitor"})
        with urllib.request.urlopen(req, timeout=_timeout()) as r:
            return json.loads(r.read() or b"{}")
    except Exception as e:
        print("  [github] %s %s 失敗: %s" % (method, path, type(e).__name__))
        return None


def issue_open(title, body):
    r = _gh("POST", "/issues", {"title": title, "body": body})
    return r.get("number") if r else None


def issue_close(number, comment):
    if not number:
        return False
    _gh("POST", "/issues/%d/comments" % number, {"body": comment})
    return _gh("PATCH", "/issues/%d" % number, {"state": "closed", "state_reason": "completed"}) is not None
