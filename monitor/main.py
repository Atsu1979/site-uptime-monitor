# -*- coding: utf-8 -*-
"""監視を1回まわす。GitHub Actions から10分ごとに呼ばれる。

  python -m monitor.main              本番（通知し、status/ を書き換える）
  python -m monitor.main --dry-run    見るだけ（通知しない・書き換えない）
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.parse

from . import __version__
from . import check as chk
from . import notify
from . import state as S


def message(ev, url):
    if ev["new"] == "up":
        dur = S.duration(ev["started"], ev["at"]) if ev["started"] else "—"
        return "🟢 %s が戻りました\n止まっていた時間: %s（%s〜%s JST）\n%s" % (
            ev["name"], dur, S.jst(ev["started"]), S.jst(ev["at"]), url)
    if ev["new"] == "down":
        return "🔴 %s が落ちています\n理由: %s\n検知: %s JST\n%s" % (ev["name"], ev["reason"], S.jst(ev["at"]), url)
    if ev["new"] == "challenged":
        return ("🟡 %s を確認できません\n%s\n監視側が止められているだけで、サイトが落ちているとは限りません\n%s"
                % (ev["name"], ev["reason"], url))
    return "🟡 %s\n%s" % (ev["name"], ev["reason"])


def weekly_summary(st, targets, now):
    since = now - dt.timedelta(days=7)
    lines = ["📋 週次の報告（%s）: 監視は動いています" % S.week_key(now)]
    for t in targets:
        down = S.downtime_minutes(st, t["id"], since, now)
        pct = 100.0 * (1 - down / (7 * 24 * 60.0))
        lines.append("・%s 稼働率 %.2f%%（停止 %d分）" % (t["name"], pct, round(down)))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--targets", default="targets.json")
    ap.add_argument("--status-dir", default="status")
    ap.add_argument("--test-notify", action="store_true",
                    help="Telegram にテストを1通送って終わる（鍵を登録した直後の確認用）")
    a = ap.parse_args(argv)

    if a.test_notify:
        ok = notify.telegram("✅ site-uptime-monitor からのテスト送信です。障害と復旧、週次の報告はここに届きます。")
        print("テスト送信: " + ("成功" if ok else "失敗（TELEGRAM_BOT_TOKEN と TELEGRAM_CHAT_ID を確認）"))
        return 0 if ok else 1

    with open(a.targets, encoding="utf-8") as f:
        targets = json.load(f)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    ua = "site-uptime-monitor/%s (+https://github.com/%s)" % (__version__, repo) if repo \
        else "site-uptime-monitor/%s" % __version__

    state_path = os.path.join(a.status_dir, "state.json")
    st = S.load(state_path)
    now = S.now_utc()
    events = []

    for t in targets:
        v = chk.check(t, ua)
        print("%-4s %-10s %6.2fs 試行%d  %s  %s" % (v.status or "-", v.state, v.elapsed, v.attempts, t["id"], v.reason))
        ev = S.apply(st, t["id"], t["name"], t["url"], v.state, v.reason, now)
        if ev:
            events.append((ev, t["url"]))

    hosts = sorted({urllib.parse.urlparse(t["url"]).hostname for t in targets if t["url"].startswith("https://")})
    warn_days = int(os.environ.get("CERT_WARN_DAYS", "14"))
    for host in hosts:
        try:
            days = chk.cert_days_left(host)
        except Exception as e:
            print("cert %-28s 取れなかった: %s" % (host, type(e).__name__))
            continue
        new = "up" if days >= warn_days else "expiring"
        reason = "証明書の残り %.0f日" % days
        print("cert %-28s 残り%.0f日" % (host, days))
        ev = S.apply(st, "cert:" + host, "証明書 " + host, "https://" + host + "/", new, reason, now)
        if ev:
            events.append((ev, "https://" + host + "/"))

    for ev, url in events:
        text = message(ev, url)
        print("\n[出来事] " + text.replace("\n", " / "))
        if a.dry_run:
            continue
        notify.telegram(text)
        cur = st["targets"][ev["id"]]
        if ev["old"] not in (None, "up") and cur.get("issue"):
            notify.issue_close(cur["issue"], text)
            cur["issue"] = None
        if ev["new"] != "up":
            cur["issue"] = notify.issue_open(text.split("\n")[0], text)

    key = S.week_key(now)
    if st.get("last_heartbeat") != key:
        summary = weekly_summary(st, targets, now)
        print("\n" + summary)
        if not a.dry_run:
            notify.telegram(summary)
            weekly = os.path.join(a.status_dir, "weekly.md")
            head = "# 週次の報告\n\n60日間リポジトリに動きが無いと GitHub が定時実行を止めるため、週1回ここに書き足す。\n\n"
            body = head
            if os.path.exists(weekly):
                with open(weekly, encoding="utf-8") as f:
                    body = f.read()
            S.write_if_changed(weekly, body + "\n```\n" + summary + "\n```\n")
        st["last_heartbeat"] = key

    if a.dry_run:
        print("\n（--dry-run: 通知も書き込みもしていない）")
        return 0
    S.write_if_changed(state_path, S.dump(st))
    S.write_if_changed(os.path.join(a.status_dir, "incidents.md"), S.render_incidents(st))
    S.write_if_changed("STATUS.md", S.render_status(st))
    return 0


if __name__ == "__main__":
    sys.exit(main())
