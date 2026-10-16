"""Independent LAN board on 9090, using only the Python standard library."""
import argparse
import datetime as dt
import json
import logging
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import source
import storage
from storage import ValidationError

ROOT = Path(__file__).parent
LOG = logging.getLogger("progress-board")
CACHE = {}
CACHE_LOCK = threading.Lock()


def selected_day(query):
    value = query.get("day", [source.now()[:10]])[0]
    storage.date_value(value)
    if value > source.now()[:10]:
        raise ValidationError("不能查看未来日期的实际工作")
    return value


def load_data(day):
    with CACHE_LOCK:
        cached = CACHE.get(day)
        if cached and time.monotonic() - cached[0] < 10:
            data = cached[1]
        else:
            data = source.source_data(day)
            CACHE.clear()
            CACHE[day] = (time.monotonic(), data)
    return data


def period_data(day, weekly=False):
    data = load_data(day)
    items = data["items"] + storage.board_items(day, data["users"])
    start = (dt.date.fromisoformat(day) - dt.timedelta(days=dt.date.fromisoformat(day).weekday())).isoformat() if weekly else day
    in_range = lambda value: bool(source.day_of(value) and start <= source.day_of(value) <= day)
    done = [i for i in items if i["group"] == "done" and in_range(i["completed_at"])]
    new = [i for i in items if in_range(i["created_at"])]
    plans = [i for i in items if i.get("planned_for") and start <= i["planned_for"] <= day]
    pending = [i for i in items if i["group"] not in {"done", "archived"}]
    exceptions = []
    cutoff = dt.datetime.fromisoformat(day + "T23:59:59").replace(tzinfo=source.TZ)
    for i in pending:
        kind = None
        note = ""
        if i.get("due_at") and i["due_at"] < day:
            kind, note = "已超期", f"计划完成日期 {i['due_at']}，工作尚未完成"
        elif i.get("blocked"):
            kind, note = "已受阻 / 暂缓", i.get("next_step") or "请在来源平台补充解除条件"
        elif i["source_type"] == "bug" and i["state"] in source.SLA:
            timestamp = dt.datetime.fromisoformat(i["updated_at"])
            timestamp = timestamp.replace(tzinfo=source.TZ) if timestamp.tzinfo is None else timestamp
            days = (cutoff - timestamp).days
            if days > source.SLA[i["state"]]:
                kind, note = "状态停留较久", f"{i['state']}已停留 {days} 天（沿用 Bug 平台 {source.SLA[i['state']]} 天阈值）"
        if kind:
            exceptions.append({"item_id": i["id"], "title": i["title"], "owner": i["owner_name"], "kind": kind,
                               "note": note, "next_step": i["next_step"], "url": i["url"]})
    return {"day": day, "start": start, "weekly": weekly, "read_at": data["read_at"], "users": data["users"],
            "items": items, "activity": data["activity"], "exceptions": exceptions,
            "stats": {"planned": len(plans), "new": len(new), "done": len(done), "pending": len(pending)},
            "plan_ids": [i["id"] for i in plans], "new_ids": [i["id"] for i in new], "done_ids": [i["id"] for i in done],
            "notes": ["计划工作仅统计明确填写计划日期的工作；Bug 未提供计划日期，不自动算作计划。",
                      "计划、新增、完成可以重叠，不直接相加。完成工作按工作项去重，不含非 Bug / 重复归档。",
                      "成员参与同一 Bug 不代表都独立完成该 Bug；提交、评论与测试按实际操作人归集。",
                      "Bug 进度采用修复提交、人工测试通过、合并三阶段；回退后按当前状态重算。",
                      "需求平台没有独立完成时间，以最后更新时间近似显示，并在详情说明。"]}


class Handler(BaseHTTPRequestHandler):
    server_version = "CardOrderBoard/1.0"

    def log_message(self, fmt, *args):
        # Do not log credentials or request bodies.
        LOG.info("%s %s", self.command, self.path.split("?")[0])

    def response(self, payload, status=200, headers=None):
        data = json.dumps(payload, ensure_ascii=False).encode() if not isinstance(payload, bytes) else payload
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValidationError("请求长度无效")
        if not 0 < length <= 262144:
            raise ValidationError("请求体为空或超过 256KB", 413)
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            raise ValidationError("需要有效的 JSON 请求体")
        if not isinstance(data, dict):
            raise ValidationError("请求体必须是对象")
        return data

    def user(self):
        bearer = self.headers.get("Authorization", "")
        agent = bearer.startswith("Bearer ")
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            pass
        token = bearer[7:] if agent else cookie.get("board_session").value if cookie.get("board_session") else ""
        uid = storage.identity(token, agent)
        user = source.find_user(uid) if uid else None
        if not user:
            raise ValidationError("请使用 Bug 平台登录标识登录", 401)
        return user, "agent" if agent else "human", token

    def do_GET(self):
        try:
            self.get_route()
        except ValidationError as exc:
            self.response({"error": str(exc)}, exc.status)
        except (sqlite3.Error, OSError):
            LOG.exception("Data source unavailable")
            self.response({"error": "数据源暂不可用，请稍后刷新；未将失败视为零进度。"}, 503)
        except Exception:
            LOG.exception("Unhandled GET")
            self.response({"error": "服务暂时异常"}, 500)

    def get_route(self):
        parsed = urlsplit(self.path)
        path, query = unquote(parsed.path), parse_qs(parsed.query)
        if path in {"/", "/app.js", "/style.css"}:
            filename = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[path]
            data = (ROOT / "static" / filename).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", {"/": "text/html", "/app.js": "text/javascript", "/style.css": "text/css"}[path] + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/healthz":
            self.response({"status": "ok", "service": "progress-board", "time": source.now()})
            return
        user, kind, _ = self.user()
        if path == "/api/me":
            self.response({"user": user, "today": source.now()[:10], "bug_url": source.BUG_URL})
        elif path == "/api/dashboard":
            self.response(period_data(selected_day(query), query.get("period", ["day"])[0] == "week"))
        elif path.startswith("/api/items/"):
            day = selected_day(query)
            iid = path.rsplit("/", 1)[-1]
            data = period_data(day)
            item = next((i for i in data["items"] if i["id"] == iid), None)
            if not item:
                raise ValidationError("工作项不存在", 404)
            events = source.bug_trace(iid) if item["source_type"] == "bug" else storage.item_events(iid, day)
            self.response({"item": item, "events": [e for e in events if source.day_of(e["time"]) <= day]})
        elif path == "/api/feedback":
            with closing(storage.connect()) as conn:
                self.response({"feedback": [dict(r) for r in conn.execute("SELECT * FROM feedback ORDER BY id DESC LIMIT 100")]})
        elif path == "/api/tokens":
            with closing(storage.connect()) as conn:
                self.response({"tokens": [dict(r) for r in conn.execute("SELECT digest,name,created_at FROM tokens WHERE userid=?", (user["userid"],))]})
        else:
            raise ValidationError("接口不存在", 404)

    def do_POST(self):
        try:
            # Session writes must originate from the same host; scripts use bearer tokens.
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                raise ValidationError("不接受跨站写入", 403)
            self.post_route()
        except ValidationError as exc:
            self.response({"error": str(exc)}, exc.status)
        except (sqlite3.Error, OSError):
            LOG.exception("Write failed")
            self.response({"error": "保存失败，请稍后重试"}, 503)
        except Exception:
            LOG.exception("Unhandled POST")
            self.response({"error": "请求处理异常"}, 500)

    def post_route(self):
        path = urlsplit(self.path).path
        data = self.body()
        if path == "/api/login":
            uid = storage.text(data, "userid", 100, True)
            user = source.find_user(uid)
            if not user:
                raise ValidationError("登录标识不存在，请使用 Bug 平台已有标识", 401)
            token = storage.issue_session(uid)
            self.response({"user": user}, headers={"Set-Cookie": f"board_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800"})
            return
        user, kind, token = self.user()
        uid = user["userid"]
        if path == "/api/logout":
            if kind == "human":
                storage.logout(token)
            self.response({"ok": True}, headers={"Set-Cookie": "board_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
        elif path == "/api/work-items" or (path.startswith("/api/work-items/") and path.endswith("/events")):
            key = self.headers.get("Idempotency-Key", "")
            if kind == "agent" and not key:
                raise ValidationError("Agent 写入必须携带 Idempotency-Key")
            if len(key) > 200:
                raise ValidationError("幂等键过长")
            iid = path.split("/")[3] if path.endswith("/events") else None
            self.response(storage.record_item(uid, data, kind, iid, key), 200 if iid else 201)
        elif path == "/api/feedback":
            title = storage.text(data, "title", 200, True)
            content = storage.text(data, "content", 4000, True)
            category = storage.text(data, "category", 50, True)
            area = storage.text(data, "area", 50, True)
            with closing(storage.connect()) as conn, conn:
                cur = conn.execute("INSERT INTO feedback(author,category,area,title,content,created_at) VALUES(?,?,?,?,?,?)", (uid, category, area, title, content, source.now()))
                feedback_id = cur.lastrowid
            self.response({"id": feedback_id, "status": "待查看"}, 201)
        elif path == "/api/tokens" and kind == "human":
            name = storage.text(data, "name", 100, True)
            secret = "board_" + secrets.token_urlsafe(32)
            with closing(storage.connect()) as conn, conn:
                conn.execute("INSERT INTO tokens VALUES(?,?,?,?)", (storage.digest(secret), uid, name, source.now()))
            self.response({"token": secret, "name": name, "notice": "仅显示一次，请妥善保存；身份固定为当前登录人。"}, 201)
        elif path == "/api/tokens/revoke" and kind == "human":
            with closing(storage.connect()) as conn, conn:
                conn.execute("DELETE FROM tokens WHERE digest=? AND userid=?", (storage.text(data, "digest", 100, True), uid))
            self.response({"ok": True})
        else:
            raise ValidationError("接口不存在或不允许此身份操作", 404)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9090)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    storage.init()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
