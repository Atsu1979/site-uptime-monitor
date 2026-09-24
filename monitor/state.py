# -*- coding: utf-8 -*-
"""状態の記録と、変化（障害の発生・復旧）の検出。

通知は『状態が変わったときだけ』。落ちている間じゅう10分ごとに鳴らさない。
状態は status/state.json に置き、変化があったときだけリポジトリへ書き戻す。
"""
import datetime as dt
import json
import os

JST = dt.timezone(dt.timedelta(hours=9))
UTC = dt.timezone.utc
FMT = "%Y-%m-%dT%H:%M:%SZ"
MAX_INCIDENTS = 500


def now_utc():
    return dt.datetime.now(UTC).replace(microsecond=0)


def iso(t):
    return t.astimezone(UTC).strftime(FMT)


def parse(s):
    return dt.datetime.strptime(s, FMT).replace(tzinfo=UTC)


def jst(s):
    return parse(s).astimezone(JST).strftime("%Y-%m-%d %H:%M")


def week_key(t):
    return t.astimezone(JST).strftime("%G-W%V")


def duration(start, end):
    minutes = int(round((parse(end) - parse(start)).total_seconds() / 60.0))
    if minutes < 60:
        return "%d分" % minutes
    h, m = divmod(minutes, 60)
    return "%d時間%d分" % (h, m) if m else "%d時間" % h


def empty():
    return {"targets": {}, "incidents": [], "last_heartbeat": ""}


def load(path):
    if not os.path.exists(path):
        return empty()
    with open(path, encoding="utf-8") as f:
        st = json.load(f)
    base = empty()
    base.update(st)
    return base


def dump(st):
    return json.dumps(st, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_if_changed(path, text):
    old = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = f.read()
    if old == text:
        return False
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True


def apply(st, tid, name, url, new_state, reason, now):
    """1つの対象の判定を記録する。状態が変わったときだけ出来事（event）を返す。"""
    at = iso(now)
    cur = st["targets"].get(tid)
    if cur is None:
        st["targets"][tid] = {"name": name, "url": url, "state": new_state, "since": at,
                              "reason": reason, "issue": None}
        if new_state == "up":
            return None
        _open_incident(st, tid, name, new_state, reason, at)
        return {"id": tid, "name": name, "url": url, "old": None, "new": new_state,
                "reason": reason, "at": at, "started": None}
    cur["name"], cur["url"] = name, url
    if cur["state"] == new_state:
        return None
    old, started = cur["state"], cur["since"]
    cur.update(state=new_state, since=at, reason=reason)
    if old != "up":
        _close_incident(st, tid, at)
    if new_state != "up":
        _open_incident(st, tid, name, new_state, reason, at)
    return {"id": tid, "name": name, "url": url, "old": old, "new": new_state,
            "reason": reason, "at": at, "started": started}


def _open_incident(st, tid, name, kind, reason, at):
    st["incidents"].append({"id": tid, "name": name, "kind": kind, "start": at, "end": None, "reason": reason})
    del st["incidents"][:-MAX_INCIDENTS]


def _close_incident(st, tid, at):
    for inc in reversed(st["incidents"]):
        if inc["id"] == tid and inc["end"] is None:
            inc["end"] = at
            return inc
    return None


def downtime_minutes(st, tid, since, until):
    """[since, until] の間に down だった分数（障害の記録から計算する）。"""
    total = 0.0
    for inc in st["incidents"]:
        if inc["id"] != tid or inc["kind"] != "down":
            continue
        s = max(parse(inc["start"]), since)
        e = min(parse(inc["end"]) if inc["end"] else until, until)
        if e > s:
            total += (e - s).total_seconds() / 60.0
    return total


LABEL = {"up": "🟢 正常", "down": "🔴 障害", "challenged": "🟡 監視側が確認できない", "expiring": "🟡 証明書の期限が近い"}


def render_status(st):
    rows = ["# 現在の状態", "",
            "状態が変わったときにだけ更新される。監視そのものが動いているかは README のバッジで見る。", "",
            "| 対象 | 状態 | いつから（JST） | 理由 |", "|---|---|---|---|"]
    for tid in sorted(st["targets"]):
        t = st["targets"][tid]
        rows.append("| [%s](%s) | %s | %s | %s |" % (t["name"], t["url"], LABEL.get(t["state"], t["state"]),
                                                  jst(t["since"]), "" if t["state"] == "up" else t["reason"]))
    return "\n".join(rows) + "\n"


def render_incidents(st):
    rows = ["# 障害の記録", "", "新しい順。終わっていないものは「継続中」。", "",
            "| 始まり（JST） | 終わり（JST） | 対象 | 種類 | 止まっていた時間 | 理由 |", "|---|---|---|---|---|---|"]
    for inc in reversed(st["incidents"]):
        end = jst(inc["end"]) if inc["end"] else "継続中"
        dur = duration(inc["start"], inc["end"]) if inc["end"] else "—"
        rows.append("| %s | %s | %s | %s | %s | %s |" % (jst(inc["start"]), end, inc["name"],
                                                       LABEL.get(inc["kind"], inc["kind"]), dur, inc["reason"]))
    if len(rows) == 6:
        rows.append("| — | — | — | — | — | まだ障害は無い |")
    return "\n".join(rows) + "\n"
