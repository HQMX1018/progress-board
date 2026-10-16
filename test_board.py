"""Isolated integration checks; never run writes against the live Bug database."""
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

import app
import source
import storage


class BoardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="board-test-", dir=Path(__file__).parent)
        storage.DATA_DIR = Path(cls.temp.name) / "board"
        source.BUG_DB = Path(cls.temp.name) / "bugs.db"
        cls.day = source.now()[:10]
        created = cls.day + "T09:00:00"
        with closing(sqlite3.connect(source.BUG_DB)) as conn, conn:
            conn.executescript("""
            CREATE TABLE users (id,userid,name,role);
            INSERT INTO users VALUES (1,'alice','Alice','admin'),(2,'bob','Bob','agent');
            CREATE TABLE bugs (id,title,reporter_id,assignee_id,status,priority,created_at,updated_at,claimed_by,claimed_at,depends_on,closed_at);
            CREATE TABLE transitions (id,bug_id,from_status,to_status,actor_id,actor_kind,reason,created_at,agent);
            CREATE TABLE qa_results (id,bug_id,tester_id,result,note,created_at);
            CREATE TABLE comments (id,bug_id,author_id,created_at,actor_kind,body);
            CREATE TABLE requirements (id,title,status,proposer_id,created_at,updated_at);
            """)
            conn.execute("INSERT INTO bugs VALUES (1,'Real bug',1,1,'已合并','high',?,?,NULL,NULL,NULL,?)", (created, created, created))
            conn.execute("INSERT INTO bugs VALUES (2,'Duplicate',1,1,'非Bug·重复','low',?,?,NULL,NULL,NULL,?)", (created, created, created))
            for idx, status in enumerate(["待修复", "待测试", "测试通过", "已合并"], 1):
                conn.execute("INSERT INTO transitions VALUES (?,1,NULL,?,2,'agent','Evidence',?,'agent-instance')", (idx, status, created))
            conn.execute("INSERT INTO transitions VALUES (5,2,NULL,'非Bug·重复',1,'human','Duplicate',?,NULL)", (created,))
        cls.source_hash = hashlib.sha256(source.BUG_DB.read_bytes()).hexdigest()
        storage.init()
        app.CACHE.clear()
        cls.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        assert hashlib.sha256(source.BUG_DB.read_bytes()).hexdigest() == cls.source_hash
        cls.temp.cleanup()

    def request(self, path, payload=None, cookie=None, bearer=None, key=None, origin=None):
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        if key:
            headers["Idempotency-Key"] = key
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(self.base + path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        return response.status, json.loads(response.read()), response.headers

    def login(self, uid="alice"):
        status, _, headers = self.request("/api/login", {"userid": uid})
        self.assertEqual(status, 200)
        return headers["Set-Cookie"].split(";", 1)[0]

    def test_auth_and_cross_origin(self):
        self.assertEqual(self.request("/api/dashboard")[0], 401)
        self.assertEqual(self.request("/api/login", {"userid": "unknown"})[0], 401)
        self.assertEqual(self.request("/api/login", {"userid": "alice"}, origin="http://evil.example")[0], 403)
        cookie = self.login()
        self.assertEqual(self.request("/api/me", cookie=cookie)[1]["user"]["userid"], "alice")
        self.request("/api/logout", {}, cookie=cookie)
        self.assertEqual(self.request("/api/me", cookie=cookie)[0], 401)

    def test_source_completion_and_plan_semantics(self):
        data = source.source_data(self.day)
        bugs = {i["id"]: i for i in data["items"]}
        self.assertEqual(bugs["bug-1"]["processed"], 3)
        self.assertEqual(bugs["bug-2"]["group"], "archived")
        self.assertIsNone(bugs["bug-1"]["planned_for"])
        report = app.period_data(self.day)
        self.assertIn("bug-1", report["done_ids"])
        self.assertNotIn("bug-2", report["done_ids"])
        self.assertNotIn("bug-1", report["plan_ids"])

    def test_agent_idempotency_and_owner(self):
        cookie = self.login()
        status, token_data, _ = self.request("/api/tokens", {"name": "tests"}, cookie=cookie)
        self.assertEqual(status, 201)
        token = token_data["token"]
        payload = {"title": "Agent work", "status": "current", "planned_for": self.day, "subtasks": [{"title": "Verify"}]}
        self.assertEqual(self.request("/api/work-items", payload, bearer=token)[0], 400)
        status, first, _ = self.request("/api/work-items", payload, bearer=token, key="create-1")
        self.assertEqual(status, 201)
        self.assertEqual(self.request("/api/work-items", payload, bearer=token, key="create-1")[1], first)
        self.assertEqual(self.request("/api/work-items", dict(payload, title="Changed"), bearer=token, key="create-1")[0], 409)
        path = "/api/work-items/" + first["id"] + "/events"
        self.assertEqual(self.request(path, {"work_status": "done"}, bearer=token, key="bad-done")[0], 400)
        self.assertEqual(self.request(path, {"subtask_id": "1", "status": "processed"}, bearer=token, key="no-proof")[0], 400)
        self.assertEqual(self.request(path, {"subtask_id": "1", "status": "verified", "note": "Verified with tests", "work_status": "done"}, bearer=token, key="verify-1")[0], 200)
        self.assertEqual(self.request(path, {"work_status": "current"}, cookie=self.login("bob"))[0], 403)
        item = self.request("/api/items/" + first["id"] + "?day=" + self.day, cookie=cookie)[1]["item"]
        self.assertEqual(item["group"], "done")
        self.assertEqual(item["processed"], 1)
        self.assertEqual(self.request(path, {"work_status": "current"}, bearer=token, key="reopen")[0], 200)
        report = app.period_data(self.day)
        self.assertNotIn(first["id"], report["done_ids"])
        self.assertIn(first["id"], report["plan_ids"])

    def test_evidence_url_and_feedback_persistence(self):
        cookie = self.login()
        bad = {"title": "Invalid evidence", "subtasks": [{"title": "Step", "status": "verified", "evidence_url": "javascript:alert(1)"}]}
        self.assertEqual(self.request("/api/work-items", bad, cookie=cookie)[0], 400)
        status, result, _ = self.request("/api/feedback", {"title": "Improve board", "content": "Real feedback", "category": "suggestion", "area": "overview"}, cookie=cookie)
        self.assertEqual(status, 201)
        rows = self.request("/api/feedback", cookie=cookie)[1]["feedback"]
        self.assertTrue(any(r["id"] == result["id"] and r["author"] == "alice" for r in rows))


if __name__ == "__main__":
    unittest.main()
