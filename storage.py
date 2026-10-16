"""Separate board persistence. No writes to the Bug database."""
import datetime as dt
import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from source import TZ, day_of, now

DATA_DIR = Path(os.environ.get("BOARD_DATA", str(Path(__file__).parent / "data")))


class ValidationError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def connect():
    conn = sqlite3.connect(DATA_DIR / "board.db", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with closing(connect()) as conn:
        conn.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, userid TEXT NOT NULL, expires TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tokens (digest TEXT PRIMARY KEY, userid TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS items (
          id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL,
          planned_for TEXT, due_at TEXT, next_step TEXT NOT NULL, blocked INTEGER NOT NULL DEFAULT 0,
          subtasks TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT);
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id), actor TEXT NOT NULL,
          source TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS idempotency (
          actor TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(actor,key));
        CREATE TABLE IF NOT EXISTS feedback (
          id INTEGER PRIMARY KEY, author TEXT NOT NULL, category TEXT NOT NULL, area TEXT NOT NULL,
          title TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT '待查看');
        CREATE TABLE IF NOT EXISTS personal_briefs (
          id INTEGER PRIMARY KEY, author TEXT NOT NULL, day TEXT NOT NULL,
          completed TEXT NOT NULL, unfinished TEXT NOT NULL, plans TEXT NOT NULL,
          revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
          UNIQUE(author,day));
        CREATE TABLE IF NOT EXISTS brief_history (
          id INTEGER PRIMARY KEY, brief_id INTEGER NOT NULL REFERENCES personal_briefs(id),
          actor TEXT NOT NULL, action TEXT NOT NULL, snapshot TEXT NOT NULL, created_at TEXT NOT NULL);
        """)
        conn.commit()


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def issue_session(userid):
    token = secrets.token_urlsafe(32)
    expires = (dt.datetime.now(TZ) + dt.timedelta(days=7)).isoformat()
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM sessions WHERE expires<?", (now(),))
        conn.execute("INSERT INTO sessions VALUES (?,?,?)", (digest(token), userid, expires))
    return token


def identity(token, agent=False):
    if not token:
        return None
    with closing(connect()) as conn:
        if agent:
            row = conn.execute("SELECT userid FROM tokens WHERE digest=?", (digest(token),)).fetchone()
        else:
            row = conn.execute("SELECT userid FROM sessions WHERE digest=? AND expires>?", (digest(token), now())).fetchone()
    return row["userid"] if row else None


def logout(token):
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM sessions WHERE digest=?", (digest(token),))


def text(data, key, limit=200, required=False):
    value = data.get(key, "")
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValidationError(f"{key} 内容无效或超过 {limit} 字")
    return value.strip()


def date_value(value):
    if not value:
        return None
    try:
        dt.date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValidationError("日期必须使用 YYYY-MM-DD")
    return value


def subtasks(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 100:
        raise ValidationError("每个工作项需要 1–100 个子项")
    result = []
    for v in values:
        if not isinstance(v, dict):
            raise ValidationError("子项格式无效")
        status = v.get("status", "pending")
        if status not in {"pending", "processed", "verified"}:
            raise ValidationError("子项状态无效")
        url = text(v, "evidence_url", 1500)
        if url and (urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).netloc):
            raise ValidationError("证据地址仅支持 http/https")
        note = text(v, "note", 4000)
        if status != "pending" and not (url or note):
            raise ValidationError("已处理或已验收子项必须填写证据或处理记录")
        result.append({"id": str(len(result) + 1), "title": text(v, "title", 200, True), "status": status,
                       "evidence_url": url, "note": note, "time": now() if status != "pending" else None})
    return result


def record_item(actor, payload, source_kind="human", item_id=None, key=""):
    fingerprint = digest(json.dumps({"item_id": item_id, "payload": payload}, sort_keys=True, ensure_ascii=False))
    with closing(connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if key:
            previous = conn.execute("SELECT * FROM idempotency WHERE actor=? AND key=?", (actor, key)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ValidationError("同一幂等键已用于其他内容", 409)
                return json.loads(previous["response"])
        moment = now()
        if not item_id:
            title = text(payload, "title", 200, True)
            children = subtasks(payload.get("subtasks"))
            status = payload.get("status", "planned")
            if status not in {"planned", "current", "done"}:
                raise ValidationError("工作状态无效")
            if status == "done" and not all(s["status"] == "verified" for s in children):
                raise ValidationError("完成工作前须验收全部子项")
            plan = date_value(payload.get("planned_for"))
            due = date_value(payload.get("due_at"))
            item_id = "work-" + uuid.uuid4().hex
            conn.execute("INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                item_id, actor, title, status, plan, due, text(payload, "next_step", 1000),
                int(bool(payload.get("blocked", False))), json.dumps(children, ensure_ascii=False),
                moment, moment, moment if status == "done" else None))
            kind = "created"
        else:
            row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            if not row:
                raise ValidationError("工作项不存在", 404)
            if row["owner"] != actor:
                raise ValidationError("只能更新本人的工作", 403)
            children = json.loads(row["subtasks"])
            if "subtask_id" in payload:
                child = next((c for c in children if c["id"] == str(payload["subtask_id"])), None)
                if child is None:
                    raise ValidationError("子项不存在", 404)
                updated = dict(child, **{k: payload[k] for k in ("status", "note", "evidence_url") if k in payload})
                validated = subtasks([updated])[0]
                child.update({k: validated[k] for k in ("status", "note", "evidence_url", "time")})
            status = payload.get("work_status", row["status"])
            if status not in {"planned", "current", "done"}:
                raise ValidationError("工作状态无效")
            if status == "done" and not all(s["status"] == "verified" for s in children):
                raise ValidationError("完成工作前须验收全部子项")
            if status != "done" and row["status"] == "done":
                kind = "reopened"
            else:
                kind = "updated"
            next_step = text(payload, "next_step", 1000) if "next_step" in payload else row["next_step"]
            blocked = int(bool(payload["blocked"])) if "blocked" in payload else row["blocked"]
            plan = date_value(payload["planned_for"]) if "planned_for" in payload else row["planned_for"]
            due = date_value(payload["due_at"]) if "due_at" in payload else row["due_at"]
            conn.execute("UPDATE items SET status=?,planned_for=?,due_at=?,next_step=?,blocked=?,subtasks=?,updated_at=?,completed_at=? WHERE id=?", (
                status, plan, due, next_step, blocked, json.dumps(children, ensure_ascii=False), moment,
                (row["completed_at"] or moment) if status == "done" else None, item_id))
        row = dict(conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone())
        # Full snapshots preserve plan changes and historical daily state.
        conn.execute("INSERT INTO events(item_id,actor,source,kind,payload,created_at) VALUES(?,?,?,?,?,?)", (
            item_id, actor, source_kind, kind, json.dumps(row, ensure_ascii=False), moment))
        response = {"id": item_id, "event": kind, "recorded_at": moment}
        if key:
            conn.execute("INSERT INTO idempotency VALUES (?,?,?,?)", (actor, key, fingerprint, json.dumps(response)))
        return response


def board_items(day, user_rows):
    users = {u["userid"]: u for u in user_rows}
    with closing(connect()) as conn:
        events = [dict(r) for r in conn.execute("SELECT * FROM events WHERE created_at<=? ORDER BY id", (day + "T23:59:59.999999+08:00",))]
    latest, before = {}, {}
    for e in events:
        latest[e["item_id"]] = json.loads(e["payload"])
        if day_of(e["created_at"]) < day:
            before[e["item_id"]] = json.loads(e["payload"])
    result = []
    for row in latest.values():
        children = json.loads(row["subtasks"])
        baseline = json.loads(before[row["id"]]["subtasks"]) if row["id"] in before else []
        user = users.get(row["owner"], {})
        result.append({**row, "source": "工作汇报", "source_type": "board", "owner_name": user.get("name", row["owner"]),
                       "owner_label": "负责人", "members": [row["owner"]], "group": row["status"],
                       "state": {"planned": "计划", "current": "处理中", "done": "已完成"}[row["status"]],
                       "subtasks": children, "total": len(children), "processed": sum(s["status"] != "pending" for s in children),
                       "baseline": sum(s["status"] != "pending" for s in baseline), "new_today": day_of(row["created_at"]) == day,
                       "url": "", "plan_note": "已录入计划日期" if row["planned_for"] else "未设置计划日期"})
    return result


def item_events(item_id, day):
    with closing(connect()) as conn:
        return [{"kind": r["kind"], "time": r["created_at"], "actor": r["actor"], "text": "工作状态 / 计划 / 子项更新已留痕", "url": ""}
                for r in conn.execute("SELECT * FROM events WHERE item_id=? AND created_at<=? ORDER BY id DESC LIMIT 60", (item_id, day + "T23:59:59.999999+08:00"))]


def list_briefs(day=None, author=None):
    conditions, args = ["deleted_at IS NULL"], []
    if day:
        conditions.append("day=?")
        args.append(day)
    if author:
        conditions.append("author=?")
        args.append(author)
    with closing(connect()) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM personal_briefs WHERE " + " AND ".join(conditions) + " ORDER BY day DESC,author LIMIT 500", args)]


def save_brief(actor, payload, delete=False):
    revision = payload.get("revision")
    if type(revision) is not int or revision < 0:
        raise ValidationError("请提供当前简报版本，刷新后重试")
    with closing(connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        moment = now()
        if delete:
            bid = payload.get("id")
            if type(bid) is not int:
                raise ValidationError("简报编号无效")
            old = conn.execute("SELECT * FROM personal_briefs WHERE id=?", (bid,)).fetchone()
            if not old or old["deleted_at"]:
                raise ValidationError("简报不存在或已删除", 404)
            if old["author"] != actor:
                raise ValidationError("只能删除本人的简报", 403)
            if old["revision"] != revision:
                raise ValidationError("简报已被更新，请刷新后再删除", 409)
            conn.execute("UPDATE personal_briefs SET deleted_at=?,updated_at=?,revision=revision+1 WHERE id=?", (moment, moment, bid))
            action = "deleted"
        else:
            day = date_value(payload.get("day"))
            if not day or day > moment[:10]:
                raise ValidationError("请选择今天或过去的日期")
            completed = text(payload, "completed", 10000)
            unfinished = text(payload, "unfinished", 10000)
            plans = text(payload, "plans", 10000)
            if not any((completed, unfinished, plans)):
                raise ValidationError("已完成、未完成、后续计划至少填写一项")
            old = conn.execute("SELECT * FROM personal_briefs WHERE author=? AND day=?", (actor, day)).fetchone()
            # Deleted entries may be recreated from an empty form; history remains intact.
            expected = old["revision"] if old and not old["deleted_at"] else 0
            if expected != revision:
                raise ValidationError("简报已被更新，请刷新后重试，避免覆盖他人更新", 409)
            if old:
                bid = old["id"]
                conn.execute("UPDATE personal_briefs SET completed=?,unfinished=?,plans=?,revision=revision+1,updated_at=?,deleted_at=NULL WHERE id=?", (completed, unfinished, plans, moment, bid))
                action = "created" if old["deleted_at"] else "updated"
            else:
                cur = conn.execute("INSERT INTO personal_briefs(author,day,completed,unfinished,plans,revision,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)", (actor, day, completed, unfinished, plans, moment, moment))
                bid, action = cur.lastrowid, "created"
        row = dict(conn.execute("SELECT * FROM personal_briefs WHERE id=?", (bid,)).fetchone())
        conn.execute("INSERT INTO brief_history(brief_id,actor,action,snapshot,created_at) VALUES(?,?,?,?,?)", (bid, actor, action, json.dumps(row, ensure_ascii=False), moment))
        return {"brief": row, "action": action}
