# -*- coding: utf-8 -*-
"""知らせる先: Telegram と GitHub Issue。

鍵はリポジトリの Secrets から環境変数で渡す。コードにもログにも鍵は出さない。
知らせる先が設定されていなければ、黙って飛ばす（監視そのものは止めない）。
"""
import json
import os
import urllib.parse
import urllib.request

API = "https://api.github.com"


def telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("  [telegram] 未設定のため送らない")
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
    try:
        with urllib.request.urlopen("https://api.telegram.org/bot" + token + "/sendMessage", data=data, timeout=20) as r:
            return 200 <= r.status < 300
    except Exception as e:
        print("  [telegram] 送れなかった: " + type(e).__name__)   # 例外の本文は鍵を含みうるので出さない
        return False


def _gh(method, path, payload=None):
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        return None
    req = urllib.request.Request(API + "/repos/" + repo + path, method=method,
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Authorization": "Bearer " + token,
                                          "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28",
                                          "User-Agent": "site-uptime-monitor"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
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
