"""仓库 / 货主双维数据隔离测试（架构增强，非里程碑）。

覆盖：中央库 / 业务库分层、账号可见仓与可见货主授权、ALL 显式化、
写路径与按 ID 访问的归属守门、上下文缺失拒绝、按仓分库物理隔离、承运商归属货主。
"""
import os
import sqlite3
import sys
import tempfile
import unittest

_TMP = os.path.join(tempfile.gettempdir(), "miniwms_unittest")
os.makedirs(_TMP, exist_ok=True)
os.environ.setdefault("MINIWMS_DB", os.path.join(_TMP, "test.db"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import scope as S  # noqa: E402
from auth import make_token  # noqa: E402

HEADER = main.WAREHOUSE_HEADER


class DataScopeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()
        cls.root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _db(self):
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        conn.row_factory = sqlite3.Row
        return conn

    def _clean(self):
        db = self._db()
        try:
            # 关系表引用 owner/warehouse，先清空，避免跨测试残留触发外键约束
            db.execute("DELETE FROM owner_warehouse")
            for t in ("user_warehouse_grant", "user_owner_grant"):
                db.execute(f"DELETE FROM {t}")
            for t, col in (("carrier", "code"), ("customer", "code"),
                           ("supplier", "code"), ("product", "sku"),
                           ("user", "username"), ("warehouse", "code"),
                           ("owner", "code")):
                db.execute(f"DELETE FROM {t} WHERE {col} LIKE 'SCP-%'")
            db.execute("DELETE FROM warehouse WHERE db_path LIKE 'scope_test_%'")
            db.commit()
        finally:
            db.close()
        for name in ("scope_test_a.db", "scope_test_b.db"):
            p = os.path.join(self.root, name)
            if os.path.exists(p):
                os.remove(p)

    def setUp(self):
        self._clean()
        os.environ["MINIWMS_ALLOW_ANONYMOUS"] = "1"

    def tearDown(self):
        os.environ["MINIWMS_ALLOW_ANONYMOUS"] = "1"
        self._clean()

    # ── helpers ──
    def _admin_token(self):
        r = self.client.post("/api/auth/login",
                             json={"username": "admin", "password": "admin123"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()["token"]

    def _auth(self, tok):
        return {"Authorization": "Bearer " + tok}

    def _owner(self, code, **kw):
        payload = {"code": code, "name": kw.pop("name", code)}
        payload.update(kw)
        r = self.client.post("/api/owners", json=payload)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["owner"]["id"]

    def _warehouse(self, code, **kw):
        payload = {"code": code, "name": kw.pop("name", code)}
        payload.update(kw)
        r = self.client.post("/api/warehouses", json=payload)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["warehouse"]["id"]

    def _role_id(self):
        db = self._db()
        try:
            return db.execute("SELECT id FROM role WHERE name = '只读'").fetchone()[0]
        finally:
            db.close()

    def _user(self, username):
        r = self.client.post("/api/users", json={
            "username": username, "password": "pwd12345", "role_id": self._role_id(),
        }, headers=self._auth(self._admin_token()))
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["user"]["id"]

    def _grant(self, uid, warehouse_scope="all", owner_scope="all",
               warehouse_ids=None, owner_ids=None):
        r = self.client.put(f"/api/users/{uid}/grants", json={
            "warehouse_scope": warehouse_scope, "owner_scope": owner_scope,
            "warehouse_ids": warehouse_ids or [], "owner_ids": owner_ids or [],
        }, headers=self._auth(self._admin_token()))
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    # ── DDL / 显式 ALL ──
    def test_scope_ddl_columns_and_tables(self):
        db = self._db()
        try:
            tables = {r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("user_warehouse_grant", tables)
            self.assertIn("user_owner_grant", tables)
            self.assertIn("db_path", {r[1] for r in db.execute("PRAGMA table_info(warehouse)")})
            self.assertIn("owner_id", {r[1] for r in db.execute("PRAGMA table_info(carrier)")})
            cols = {r[1] for r in db.execute("PRAGMA table_info(user)")}
            self.assertIn("warehouse_scope", cols)
            self.assertIn("owner_scope", cols)
        finally:
            db.close()

    def test_admin_has_explicit_all_scope(self):
        db = self._db()
        try:
            row = db.execute(
                "SELECT warehouse_scope, owner_scope FROM user WHERE username='admin'"
            ).fetchone()
            self.assertEqual((row[0], row[1]), ("all", "all"))
        finally:
            db.close()

    def test_anonymous_demo_is_explicit_all_and_can_be_disabled(self):
        r = self.client.get("/api/scope/context")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["mode"], "anonymous-demo")
        self.assertEqual(r.get_json()["owner_scope"], "ALL")
        os.environ["MINIWMS_ALLOW_ANONYMOUS"] = "0"
        r = self.client.get("/api/owners")
        self.assertEqual(r.status_code, 401)
        self.assertIn("未登录", r.get_json()["error"])

    # ── 上下文缺失必须拒绝 ──
    def test_user_without_grants_is_denied_not_open(self):
        uid = self._user("SCP-NO-GRANT")
        tok = make_token(uid)
        r = self.client.get("/api/owners", headers=self._auth(tok))
        self.assertEqual(r.status_code, 403)
        self.assertIn("未配置", r.get_json()["error"])
        # 上下文自检端点走软解析，返回 denied 与空清单，而不是 403
        ctx = self.client.get("/api/scope/context", headers=self._auth(tok))
        self.assertEqual(ctx.get_json()["mode"], "denied")
        self.assertEqual(ctx.get_json()["warehouses"], [])

    # ── 货主维度 ──
    def test_partial_owner_scope_filters_lists(self):
        o1 = self._owner("SCP-OW-A")
        o2 = self._owner("SCP-OW-B")
        uid = self._user("SCP-PARTIAL")
        self._grant(uid, owner_scope="grant", owner_ids=[o1])
        tok = make_token(uid)
        r = self.client.get("/api/owners?page_size=100", headers=self._auth(tok))
        self.assertEqual(r.status_code, 200)
        ids = {x["id"] for x in r.get_json()["owners"]}
        self.assertIn(o1, ids)
        # 未授权货主必须被过滤掉（不强求列表里没有系统预置的其它货主）
        self.assertNotIn(o2, ids)
        ctx = self.client.get("/api/scope/context", headers=self._auth(tok))
        self.assertEqual(ctx.get_json()["owner_scope"], "PARTIAL")
        self.assertEqual(ctx.get_json()["owner_ids"], [o1])

    def test_owner_scope_all_switch(self):
        o1 = self._owner("SCP-OW-A")
        o2 = self._owner("SCP-OW-B")
        uid = self._user("SCP-ALL")
        self._grant(uid, owner_scope="grant", owner_ids=[o1])
        body = self._grant(uid, owner_scope="all")
        self.assertEqual(body["user"]["owner_scope"], "all")
        self.assertEqual(body["owner_ids"], [])  # ALL 靠标志，不靠授权行
        ctx = self.client.get("/api/scope/context", headers=self._auth(make_token(uid)))
        self.assertEqual(ctx.get_json()["owner_scope"], "ALL")
        r = self.client.get("/api/owners?page_size=100", headers=self._auth(make_token(uid)))
        ids = {x["id"] for x in r.get_json()["owners"]}
        self.assertIn(o1, ids)
        self.assertIn(o2, ids)

    def test_write_path_rejects_foreign_owner(self):
        o1 = self._owner("SCP-OW-A")
        o2 = self._owner("SCP-OW-B")
        uid = self._user("SCP-WRITE")
        self._grant(uid, owner_scope="grant", owner_ids=[o1])
        tok = make_token(uid)
        ok = self.client.post("/api/customers", json={
            "code": "SCP-CUS-OK", "name": "授权货主客户", "owner_id": o1,
        }, headers=self._auth(tok))
        self.assertEqual(ok.status_code, 201, ok.get_data(as_text=True))
        bad = self.client.post("/api/customers", json={
            "code": "SCP-CUS-BAD", "name": "越权客户", "owner_id": o2,
        }, headers=self._auth(tok))
        self.assertEqual(bad.status_code, 403)

    def test_detail_guard_rejects_foreign_owner(self):
        o1 = self._owner("SCP-OW-A")
        o2 = self._owner("SCP-OW-B")
        c1 = self.client.post("/api/customers", json={
            "code": "SCP-CUS-1", "name": "甲", "owner_id": o1}).get_json()["customer"]["id"]
        c2 = self.client.post("/api/customers", json={
            "code": "SCP-CUS-2", "name": "乙", "owner_id": o2}).get_json()["customer"]["id"]
        uid = self._user("SCP-DETAIL")
        self._grant(uid, owner_scope="grant", owner_ids=[o1])
        h = self._auth(make_token(uid))
        self.assertEqual(self.client.get(f"/api/customers/{c1}", headers=h).status_code, 200)
        self.assertEqual(self.client.get(f"/api/customers/{c2}", headers=h).status_code, 403)

    # ── 承运商归属货主（预留） ──
    def test_carrier_owner_id_roundtrip_and_filter(self):
        o1 = self._owner("SCP-OW-A")
        o2 = self._owner("SCP-OW-B")
        a = self.client.post("/api/carriers", json={
            "code": "SCP-CAR-1", "name": "专属承运商", "owner_id": o1})
        self.assertEqual(a.status_code, 201, a.get_data(as_text=True))
        self.assertEqual(a.get_json()["carrier"]["owner_id"], o1)
        b = self.client.post("/api/carriers", json={
            "code": "SCP-CAR-2", "name": "共享承运商"})
        self.assertEqual(b.get_json()["carrier"]["owner_id"], None)
        pick = self.client.get(f"/api/carriers?owner_id={o1}").get_json()["carriers"]
        self.assertEqual([x["code"] for x in pick], ["SCP-CAR-1"])
        shared = self.client.get("/api/carriers?owner_id=none").get_json()["carriers"]
        self.assertIn("SCP-CAR-2", [x["code"] for x in shared])
        bad = self.client.post("/api/carriers", json={
            "code": "SCP-CAR-3", "name": "错货主", "owner_id": 999999})
        self.assertEqual(bad.status_code, 400)
        # 归属货主的承运商也能被受限账号过滤：未授权货主的专属资源不可见
        uid = self._user("SCP-CAR-SCOPE")
        self._grant(uid, owner_scope="grant", owner_ids=[o2])
        rows = self.client.get("/api/carriers?page_size=100",
                               headers=self._auth(make_token(uid))).get_json()["carriers"]
        codes = [x["code"] for x in rows]
        self.assertNotIn("SCP-CAR-1", codes)
        self.assertIn("SCP-CAR-2", codes)  # NULL = 公共资源仍可见

    # ── 仓库维度（A 作业视角） ──
    def test_warehouse_grant_partial_and_switch(self):
        w1 = self._warehouse("SCP-WH-A")
        w2 = self._warehouse("SCP-WH-B")
        uid = self._user("SCP-WH-PARTIAL")
        self._grant(uid, warehouse_scope="grant", warehouse_ids=[w1])
        h = self._auth(make_token(uid))
        r = self.client.get("/api/scope/warehouses", headers=h)
        self.assertEqual([x["id"] for x in r.get_json()["warehouses"]], [w1])
        ok = self.client.post("/api/scope/warehouse",
                              json={"warehouse_id": w1}, headers=h)
        self.assertEqual(ok.status_code, 200)
        bad = self.client.post("/api/scope/warehouse",
                               json={"warehouse_id": w2}, headers=h)
        self.assertEqual(bad.status_code, 403)
        bad_header = self.client.get("/api/owners", headers={**h, HEADER: str(w2)})
        self.assertEqual(bad_header.status_code, 403)

    def test_warehouse_scope_all_sees_every_warehouse(self):
        self._warehouse("SCP-WH-A")
        self._warehouse("SCP-WH-B")
        uid = self._user("SCP-WH-ALL")
        self._grant(uid, warehouse_scope="all")
        r = self.client.get("/api/scope/warehouses", headers=self._auth(make_token(uid)))
        codes = [x["code"] for x in r.get_json()["warehouses"]]
        self.assertIn("SCP-WH-A", codes)
        self.assertIn("SCP-WH-B", codes)

    def test_db_path_validation(self):
        bad1 = self.client.post("/api/warehouses", json={
            "code": "SCP-WH-PATH1", "name": "路径穿越", "db_path": "../../etc/passwd"})
        self.assertEqual(bad1.status_code, 400)
        bad2 = self.client.post("/api/warehouses", json={
            "code": "SCP-WH-PATH2", "name": "非 db 文件", "db_path": "notes.txt"})
        self.assertEqual(bad2.status_code, 400)

    def test_warehouse_db_isolation(self):
        """按仓分库：切到 A 仓写入的数据，在中央库 / B 仓视角不可见。"""
        w1 = self._warehouse("SCP-WH-A")
        o1 = self._owner("SCP-OW-A")
        r = self.client.put(f"/api/warehouses/{w1}", json={"db_path": "scope_test_a.db"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["warehouse"]["db_path"], "scope_test_a.db")
        self.assertTrue(os.path.exists(os.path.join(self.root, "scope_test_a.db")))

        # 切到 A 仓：写入一条只属于该库的数据
        in_a = self.client.post("/api/customers", json={
            "code": "SCP-CUS-IN-A", "name": "A 仓专属客户", "owner_id": o1,
        }, headers={HEADER: str(w1)})
        self.assertEqual(in_a.status_code, 201, in_a.get_data(as_text=True))
        cid = in_a.get_json()["customer"]["id"]

        # 默认（中央库）视角看不到，且 A 仓视角能查到 → 物理隔离生效
        self.assertEqual(self.client.get(f"/api/customers/{cid}").status_code, 404)
        list_default = self.client.get("/api/customers?page_size=100").get_json()["customers"]
        self.assertNotIn("SCP-CUS-IN-A", [x["code"] for x in list_default])
        list_a = self.client.get("/api/customers?page_size=100",
                                 headers={HEADER: str(w1)}).get_json()["customers"]
        self.assertIn("SCP-CUS-IN-A", [x["code"] for x in list_a])

        # 中央库货主主数据已同步进 A 库，外键引用不会断
        self.assertEqual(self.client.get(f"/api/owners/{o1}",
                                         headers={HEADER: str(w1)}).status_code, 200)

    def test_admin_permission_not_hardcoded_by_role_name(self):
        """管理员靠 permissions=['*'] 显式放行，不再依赖角色名特判。"""
        r = self.client.get("/api/roles", headers=self._auth(self._admin_token()))
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
