"""Read-only adapter for the existing CardOrder Bug platform."""
import datetime as dt
import os
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path

BUG_DB = Path(os.environ.get("BUG_DB", "/home/cardxu/cardorder-platform/data/platform.db"))
BUG_URL = os.environ.get("BUG_URL", "http://192.168.31.145:8000").rstrip("/")
TZ = dt.timezone(dt.timedelta(hours=8))
SLA = {"待分析": 2, "待修复": 2, "待裁决": 3, "待测试": 3}
TERMINAL = {"已合并", "非Bug·重复"}


def now():
    return dt.datetime.now(TZ).isoformat(timespec="seconds")


def day_of(value):
    if not value:
        return ""
    try:
        value = dt.datetime.fromisoformat(value)
        if value.tzinfo is None:
            value = value.replace(tzinfo=TZ)
        return value.astimezone(TZ).date().isoformat()
    except (ValueError, TypeError):
        return ""


def connect():
    conn = sqlite3.connect(BUG_DB.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def users():
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute("SELECT id,userid,name,role FROM users ORDER BY id")]


def find_user(userid):
    return next((u for u in users() if u["userid"] == userid), None)


def source_data(day):
    """Reconstruct source state through the selected day's end; never invent history."""
    end = day + "T23:59:59.999999"
    with closing(connect()) as conn:
        conn.execute("BEGIN")
        user_rows = [dict(r) for r in conn.execute("SELECT id,userid,name,role FROM users")]
        bugs = [dict(r) for r in conn.execute(
            "SELECT id,title,reporter_id,assignee_id,status,priority,created_at,updated_at,"
            "claimed_by,claimed_at,depends_on,closed_at FROM bugs WHERE created_at<=?", (end,)
        )]
        histories = [dict(r) for r in conn.execute(
            "SELECT id,bug_id,from_status,to_status,actor_id,actor_kind,reason,created_at,agent "
            "FROM transitions WHERE created_at<=? ORDER BY created_at,id", (end,)
        )]
        qa = [dict(r) for r in conn.execute(
            "SELECT id,bug_id,tester_id,result,note,created_at FROM qa_results WHERE created_at<=? "
            "ORDER BY created_at,id", (end,)
        )]
        comments = [dict(r) for r in conn.execute(
            "SELECT bug_id,author_id,created_at,actor_kind FROM comments WHERE created_at<=?", (end,)
        )]
        reqs = [dict(r) for r in conn.execute(
            "SELECT id,title,status,proposer_id,created_at,updated_at FROM requirements WHERE created_at<=?", (end,)
        )]
    by_id = {u["id"]: u for u in user_rows}
    trans_by_bug, qa_by_bug, participants = defaultdict(list), defaultdict(list), defaultdict(set)
    activity = defaultdict(lambda: {"submitted": 0, "comments": 0, "qa": 0})
    for row in histories:
        trans_by_bug[row["bug_id"]].append(row)
        if row["actor_id"] in by_id:
            participants[row["bug_id"]].add(by_id[row["actor_id"]]["userid"])
    for row in qa:
        qa_by_bug[row["bug_id"]].append(row)
        if row["tester_id"] in by_id:
            uid = by_id[row["tester_id"]]["userid"]
            participants[row["bug_id"]].add(uid)
            if day_of(row["created_at"]) == day:
                activity[uid]["qa"] += 1
    for row in comments:
        if row["author_id"] in by_id:
            uid = by_id[row["author_id"]]["userid"]
            participants[row["bug_id"]].add(uid)
            if day_of(row["created_at"]) == day:
                activity[uid]["comments"] += 1
    items = []
    for b in bugs:
        tid = b["id"]
        history = trans_by_bug[tid]
        state = history[-1]["to_status"] if history else b["status"]
        # No state history and an update after the cutoff cannot support a historical claim.
        if not history and day_of(b["updated_at"]) > day:
            state = "历史状态未知"
        reporter = by_id.get(b["reporter_id"], {})
        tester = by_id.get(b["assignee_id"], {})
        members = set(participants[tid])
        for u in (reporter, tester):
            if u:
                members.add(u["userid"])
        if day_of(b["created_at"]) == day and reporter:
            activity[reporter["userid"]]["submitted"] += 1
        processed = state in {"待测试", "测试通过", "已合并"}
        verified = state in {"测试通过", "已合并"}
        merged = state == "已合并"
        latest = history[-1]["created_at"] if history else b["created_at"]
        completed_at = latest if merged else None
        baseline_state = next((r["to_status"] for r in reversed(history) if day_of(r["created_at"]) < day), "")
        baseline = sum([baseline_state in {"待测试", "测试通过", "已合并"},
                        baseline_state in {"测试通过", "已合并"}, baseline_state == "已合并"])
        children = []
        for name, achieved, states in [
            ("修复提交并进入待测试", processed, {"待测试"}),
            ("人工测试通过", verified, {"测试通过"}),
            ("合并主分支", merged, {"已合并"}),
        ]:
            evidence = next((r for r in reversed(history) if r["to_status"] in states), None) if achieved else None
            children.append({"id": str(len(children) + 1), "title": name,
                             "status": "verified" if achieved else "pending",
                             "time": evidence["created_at"] if evidence else None,
                             "evidence_url": f"{BUG_URL}/bugs/{tid}" if evidence else "",
                             "note": (evidence["reason"] or evidence["to_status"]) if evidence else "尚无对应流转记录"})
        group = "done" if merged else "archived" if state == "非Bug·重复" else "planned" if state == "挂起" else "current"
        item = {"id": f"bug-{tid}", "title": b["title"], "source": "Bug 平台", "source_type": "bug",
                "url": f"{BUG_URL}/bugs/{tid}", "owner": tester.get("userid") or reporter.get("userid") or "",
                "owner_name": tester.get("name") or reporter.get("name") or "未分配",
                "owner_label": "回归负责人" if tester else "提交人", "reporter": reporter.get("userid"),
                "members": sorted(members), "group": group, "state": state,
                "created_at": b["created_at"], "updated_at": latest, "completed_at": completed_at,
                "planned_for": None, "due_at": None, "subtasks": children,
                "processed": sum(c["status"] != "pending" for c in children), "total": 3,
                "baseline": baseline, "next_step": "在 Bug 平台继续处理 / 验证", "priority": b["priority"],
                "new_today": day_of(b["created_at"]) == day, "blocked": state == "挂起",
                "plan_note": "来源未提供计划日期，不推断为今日计划"}
        items.append(item)
    for r in reqs:
        u = by_id.get(r["proposer_id"], {})
        historical_unknown = day_of(r["updated_at"]) > day
        done = r["status"] == "已完成" and not historical_unknown
        state = "历史状态未知" if historical_unknown else r["status"]
        items.append({"id": f"req-{r['id']}", "title": r["title"], "source": "需求平台", "source_type": "requirement",
                      "url": f"{BUG_URL}/requirements/{r['id']}", "owner": u.get("userid", ""),
                      "owner_name": u.get("name", "未分配"), "owner_label": "需求提交人", "reporter": u.get("userid"),
                      "members": [u["userid"]] if u else [], "group": "done" if done else "archived" if state == "已拒绝" else "planned",
                      "state": state, "created_at": r["created_at"], "updated_at": r["updated_at"],
                      "completed_at": r["updated_at"] if done else None, "planned_for": None, "due_at": None,
                      "subtasks": [{"id": "1", "title": "需求完成", "status": "verified" if done else "pending",
                                    "time": r["updated_at"] if done else None, "evidence_url": f"{BUG_URL}/requirements/{r['id']}" if done else "", "note": "来源仅提供当前状态，无独立完成时间"}],
                      "processed": int(done), "total": 1, "baseline": int(done and day_of(r["updated_at"]) < day),
                      "next_step": "在原需求平台查看详情", "new_today": day_of(r["created_at"]) == day, "blocked": state == "暂缓",
                      "plan_note": "来源未提供计划日期，不推断为今日计划"})
    return {"users": user_rows, "items": items, "activity": dict(activity), "read_at": now()}


def bug_trace(item_id):
    """Full evidence remains in the existing platform; expose only working records."""
    if not item_id.startswith("bug-") or not item_id[4:].isdigit():
        return []
    bid = int(item_id[4:])
    with closing(connect()) as conn:
        result = []
        for table, text, kind in [("transitions", "reason", "状态流转"), ("comments", "body", "评论补充"), ("qa_results", "note", "测试记录")]:
            for r in conn.execute(f"SELECT id,{text},created_at FROM {table} WHERE bug_id=? ORDER BY created_at DESC LIMIT 30", (bid,)):
                result.append({"kind": kind, "text": r[text] or kind, "time": r["created_at"], "url": f"{BUG_URL}/bugs/{bid}"})
    return sorted(result, key=lambda r: r["time"], reverse=True)[:60]
