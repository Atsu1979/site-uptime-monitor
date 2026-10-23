# -*- coding: utf-8 -*-
"""監視を1回まわす。GitHub Actions から10分ごとに呼ばれる。

  python -m monitor.main              本番（通知し、status/ を書き換える）
  python -m monitor.main --dry-run    見るだけ（通知しない・書き換えない）

設定（環境変数）は README の表を参照。
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
from .config import env_number


def _avail_line(avail):
    pct, n = avail or (None, 0)
    return "" if pct is None else "\n直近%d回の稼働率: %.1f%%" % (n, pct)


def message(ev, url, avail=None):
    if ev["new"] == "up":
        dur = S.duration(ev["started"], ev["at"]) if ev["started"] else "—"
        return "🟢 %s が戻りました\n止まっていた時間: %s（%s〜%s JST）%s\n%s" % (
            ev["name"], dur, S.jst(ev["started"]), S.jst(ev["at"]), _avail_line(avail), url)
    if ev["new"] == "down":
        return "🔴 %s が落ちています\n理由: %s\n検知: %s JST%s\n%s" % (
            ev["name"], ev["reason"], S.jst(ev["at"]), _avail_line(avail), url)
    if ev["new"] == "challenged":
        return ("🟡 %s を確認できません\n%s\n監視側が止められているだけで、サイトが落ちているとは限りません\n%s"
                % (ev["name"], ev["reason"], url))
    if ev["new"] == "expired":
        return "🔴 %s\n%s\n%s" % (ev["name"], ev["reason"], url)
    return "🟡 %s\n%s" % (ev["name"], ev["reason"])


def event_payload(ev, avail=None):
    """Webhook に付ける機械向けの情報。実URLは入れない（本文の text には通知用に入っている）。"""
    pct, n = avail or (None, 0)
    kind = "recovered" if S.is_recovery(ev) else ev["new"]
    return {"type": kind, "id": ev["id"], "name": ev["name"], "old": ev["old"], "new": ev["new"],
            "reason": ev["reason"], "at": ev["at"], "started": ev["started"],
            "availability_pct": None if pct is None else round(pct, 2), "availability_checks": n}


def weekly_summary(st, targets, now):
    since = now - dt.timedelta(days=7)
    lines = ["📋 週次の報告（%s）: 監視は動いています" % S.week_key(now)]
    for t in targets:
        down = S.downtime_minutes(st, t["id"], since, now)
        pct = 100.0 * (1 - down / (7 * 24 * 60.0))
        line = "・%s 稼働率 %.2f%%（停止 %d分）" % (t.get("label", t["id"]), pct, round(down))
        recent, n = S.availability(st, t["id"])
        if recent is not None:
            line += "／直近%d回 %.2f%%" % (n, recent)
        lines.append(line)
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--targets", default="targets.json")
    ap.add_argument("--status-dir", default="status")
    ap.add_argument("--status-md", default="STATUS.md", help="現在の状態の表を書き出す先")
    ap.add_argument("--test-notify", action="store_true",
                    help="設定済みの通知先（Telegram・Webhook）にテストを1通送って終わる（鍵を登録した直後の確認用）")
    a = ap.parse_args(argv)

    if a.test_notify:
        ok = notify.send("✅ site-uptime-monitor からのテスト送信です。障害と復旧、週次の報告はここに届きます。",
                         {"type": "test"})
        print("テスト送信: " + ("成功" if ok else "失敗（TELEGRAM_BOT_TOKEN と TELEGRAM_CHAT_ID、または WEBHOOK_URL を確認）"))
        return 0 if ok else 1

    with open(a.targets, encoding="utf-8") as f:
        targets = json.load(f)
    # 実名/実URLは通知(Telegram=非公開)にだけ使う。コミット物には載せない。
    real = {t["id"]: (t.get("name", t.get("label", t["id"])), t["url"]) for t in targets}
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    ua = "site-uptime-monitor/%s (+https://github.com/%s)" % (__version__, repo) if repo \
        else "site-uptime-monitor/%s" % __version__

    state_path = os.path.join(a.status_dir, "state.json")
    st = S.load(state_path)
    now = S.now_utc()
    events = []
    hist_size = env_number("HISTORY_SIZE", S.HISTORY_SIZE, lo=0, hi=S.MAX_HISTORY_SIZE)
    warn_days = env_number("CERT_WARN_DAYS", chk.CERT_WARN_DAYS, lo=0, hi=365)

    for t in targets:
        v = chk.check(t, ua)
        S.record(st, t["id"], v.state, hist_size)
        avail = S.availability(st, t["id"])
        pct = "" if avail[0] is None else "  直近%d回 %.1f%%" % (avail[1], avail[0])
        print("%-4s %-10s %6.2fs 試行%d  %s  %s%s" % (v.status or "-", v.state, v.elapsed, v.attempts, t["id"],
                                                     v.reason, pct))
        ev = S.apply(st, t["id"], t.get("label", t["id"]), "", v.state, v.reason, now)
        if ev:
            events.append((ev, real[t["id"]][1], avail))
    S.prune_history(st, {t["id"] for t in targets})

    hosts = sorted({urllib.parse.urlparse(t["url"]).hostname for t in targets if t["url"].startswith("https://")})
    host_label = {h: ("cert:c%d" % i, "TLS証明書 %d" % i) for i, h in enumerate(hosts, 1)}
    for host in hosts:
        v = chk.cert_check(host, warn_days)
        if v is None:
            continue
        print("cert %-28s %s" % (host, v.reason))
        cid, clabel = host_label[host]
        ev = S.apply(st, cid, clabel, "", v.state, v.reason, now)
        if ev:
            events.append((ev, "https://" + host + "/", None))

    for ev, url, avail in events:
        text = message(ev, url, avail)
        print("\n[出来事] " + text.replace("\n", " / "))
        if a.dry_run:
            continue
        notify.send(text, event_payload(ev, avail))
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
            notify.send(summary, {"type": "weekly", "week": key})
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
    S.write_if_changed(a.status_md, S.render_status(st))
    return 0


if __name__ == "__main__":
    sys.exit(main())
