"""货主 × 仓库业务关系（格子）测试。

锁死本次优化的核心语义：可见范围 = 账号授权 ∩ 当前仓已登记的业务关系，
而不是「可见仓集合 × 可见货主集合」的笛卡尔积。同时验证：
- 未登记任何格子时行为与增强前完全一致（clone 即用 / 老库零迁移）
- 列表、写路径、按 ID 访问三条路径都被格子收紧
- 格子按仓独立生效，清空即关闭
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
from auth import make_token  # noqa: E402

HEADER = main.WAREHOUSE_HEADER


class OwnerWarehouseGridTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def _db(self):
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        conn.row_factory = sqlite3.Row
        return conn

    def _clean(self):
        db = self._db()
        try:
            # 关系表引用 owner/warehouse，必须先清，否则删主数据会触发外键约束
            db.execute("DELETE FROM owner_warehouse")
            for t in ("user_warehouse_grant", "user_owner_grant"):
                db.execute(f"DELETE FROM {t}")
            for t, col in (("customer", "code"), ("carrier", "code"),
                           ("user", "username"), ("warehouse", "code"),
                           ("owner", "code")):
                db.execute(f"DELETE FROM {t} WHERE {col} LIKE 'GRID-%'")
            db.commit()
        finally:
            db.close()

    def setUp(self):
        self._clean()
        os.environ["MINIWMS_ALLOW_ANONYMOUS"] = "1"

    def tearDown(self):
        self._clean()
        os.environ["MINIWMS_ALLOW_ANONYMOUS"] = "1"

    # ── helpers ──
    def _admin_token(self):
        r = self.client.post("/api/auth/login",
                             json={"username": "admin", "password": "admin123"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()["token"]

    def _auth(self, tok):
        return {"Authorization": "Bearer " + tok}

    def _wh(self, wid):
        return {HEADER: str(wid)}

    def _owner(self, code):
        r = self.client.post("/api/owners", json={"code": code, "name": code})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["owner"]["id"]

    def _warehouse(self, code):
        r = self.client.post("/api/warehouses", json={"code": code, "name": code})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["warehouse"]["id"]

    def _user(self, username):
        db = self._db()
        try:
            rid = db.execute("SELECT id FROM role WHERE name='只读'").fetchone()[0]
        finally:
            db.close()
        r = self.client.post("/api/users", json={
            "username": username, "password": "pwd12345", "role_id": rid,
        }, headers=self._auth(self._admin_token()))
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["user"]["id"]

    def _grant(self, uid, owner_ids):
        r = self.client.put(f"/api/users/{uid}/grants", json={
            "warehouse_scope": "all", "owner_scope": "grant",
            "warehouse_ids": [], "owner_ids": owner_ids,
        }, headers=self._auth(self._admin_token()))
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def _set_grid(self, wid, owner_ids):
        r = self.client.put(f"/api/owner-warehouses/{wid}",
                            json={"owner_ids": owner_ids},
                            headers=self._auth(self._admin_token()))
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    def _customer(self, code, oid, headers=None):
        return self.client.post("/api/customers", json={
            "code": code, "name": code, "owner_id": oid,
        }, headers=headers or {})

    def _owner_ids(self, path, headers=None):
        r = self.client.get(path, headers=headers or {})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return {x["id"] for x in r.get_json()["owners"]}

    # ── DDL 与兼容 ──
    def test_grid_table_ddl(self):
        db = self._db()
        try:
            tables = {r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("owner_warehouse", tables)
            cols = {r[1] for r in db.execute("PRAGMA table_info(owner_warehouse)")}
            self.assertTrue({"owner_id", "warehouse_id", "status", "note", "created_at"}
                            .issubset(cols))
            idx = db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='idx_owner_warehouse_wh'").fetchone()
            self.assertIsNotNone(idx)
        finally:
            db.close()

    def test_no_grid_keeps_legacy_behavior(self):
        """零配置 = 功能未启用：ALL 账号仍看全部货主，受限账号仍按账号授权过滤。"""
        o1, o2 = self._owner("GRID-OW-A"), self._owner("GRID-OW-B")
        w1 = self._warehouse("GRID-WH-A")
        ctx = self.client.get("/api/scope/context", headers=self._wh(w1)).get_json()
        self.assertFalse(ctx["grid_enabled"])
        ids = self._owner_ids("/api/owners?page_size=100", self._wh(w1))
        self.assertIn(o1, ids)
        self.assertIn(o2, ids)
        uid = self._user("GRID-U-LEGACY")
        self._grant(uid, [o1])
        ids = self._owner_ids("/api/owners?page_size=100",
                              self._auth(make_token(uid)))
        self.assertIn(o1, ids)
        self.assertNotIn(o2, ids)

    # ── 核心：格子收紧 ALL 账号 ──
    def test_grid_narrows_even_all_owners_account(self):
        o1, o2 = self._owner("GRID-OW-A"), self._owner("GRID-OW-B")
        w1 = self._warehouse("GRID-WH-A")
        self._set_grid(w1, [o1])
        ids = self._owner_ids("/api/owners?page_size=100", self._wh(w1))
        self.assertIn(o1, ids)
        self.assertNotIn(o2, ids)
        ctx = self.client.get("/api/scope/context", headers=self._wh(w1)).get_json()
        self.assertTrue(ctx["grid_enabled"])
        self.assertEqual(ctx["grid_warehouse_id"], w1)
        self.assertEqual(ctx["grid_owner_ids"], [o1])

    def test_grid_filters_business_lists(self):
        o1, o2 = self._owner("GRID-OW-A"), self._owner("GRID-OW-B")
        w1 = self._warehouse("GRID-WH-A")
        self.assertEqual(self._customer("GRID-CUS-1", o1).status_code, 201)
        self.assertEqual(self._customer("GRID-CUS-2", o2).status_code, 201)
        self._set_grid(w1, [o1])
        in_grid = self.client.get("/api/customers?page_size=100",
                                  headers=self._wh(w1)).get_json()["customers"]
        codes = [x["code"] for x in in_grid]
        self.assertIn("GRID-CUS-1", codes)
        self.assertNotIn("GRID-CUS-2", codes)
        # 未指定仓 = 不启用格子（向下兼容边界，文档明确写出）
        no_wh = self.client.get("/api/customers?page_size=100").get_json()["customers"]
        codes = [x["code"] for x in no_wh]
        self.assertIn("GRID-CUS-1", codes)
        self.assertIn("GRID-CUS-2", codes)

    def test_grid_rejects_write_for_unregistered_owner(self):
        o1, o2 = self._owner("GRID-OW-A"), self._owner("GRID-OW-B")
        w1 = self._warehouse("GRID-WH-A")
        self._set_grid(w1, [o1])
        ok = self._customer("GRID-CUS-OK", o1, self._wh(w1))
        self.assertEqual(ok.status_code, 201, ok.get_data(as_text=True))
        bad = self._customer("GRID-CUS-BAD", o2, self._wh(w1))
        self.assertEqual(bad.status_code, 403)
        self.assertIn("无权", bad.get_json()["error"])

    def test_grid_rejects_detail_for_unregistered_owner(self):
        o1, o2 = self._owner("GRID-OW-A"), self._owner("GRID-OW-B")
        w1 = self._warehouse("GRID-WH-A")
        cid = self._customer("GRID-CUS-2", o2).get_json()["customer"]["id"]
        self._set_grid(w1, [o1])
        self.assertEqual(self.client.get(f"/api/customers/{cid}",
                                         headers=self._wh(w1)).status_code, 403)
        self.assertEqual(self.client.get(f"/api/customers/{cid}").status_code, 200)

    def test_grid_intersects_with_account_grants(self):
        """账号授权 [o1,o2] ∩ 仓格子 [o1] = 只看 o1。"""
        o1, o2 = self._owner("GRID-OW-A"), self._owner("GRID-OW-B")
        w1 = self._warehouse("GRID-WH-A")
        uid = self._user("GRID-U-INTERSECT")
        self._grant(uid, [o1, o2])
        self._set_grid(w1, [o1])
        h = {**self._auth(make_token(uid)), **self._wh(w1)}
        ids = self._owner_ids("/api/owners?page_size=100", h)
        self.assertIn(o1, ids)
        self.assertNotIn(o2, ids)

    def test_grid_is_per_warehouse(self):
        o1, o2 = self._owner("GRID-OW-A"), self._owner("GRID-OW-B")
        w1 = self._warehouse("GRID-WH-A")
        w2 = self._warehouse("GRID-WH-B")
        self._set_grid(w1, [o1])
        ids = self._owner_ids("/api/owners?page_size=100", self._wh(w2))
        self.assertIn(o1, ids)
        self.assertIn(o2, ids)
        ctx = self.client.get("/api/scope/context", headers=self._wh(w2)).get_json()
        self.assertFalse(ctx["grid_enabled"])

    # ── 配置端点 ──
    def test_save_grid_roundtrip_and_validation(self):
        o1, o2 = self._owner("GRID-OW-A"), self._owner("GRID-OW-B")
        w1 = self._warehouse("GRID-WH-A")
        body = self._set_grid(w1, [o1, o2])
        self.assertEqual({c["owner_id"] for c in body["cells"]}, {o1, o2})
        self.assertEqual(body["enabled_warehouse_ids"], [w1])
        h = self._auth(self._admin_token())
        bad_owner = self.client.put(f"/api/owner-warehouses/{w1}",
                                    json={"owner_ids": [999999]}, headers=h)
        self.assertEqual(bad_owner.status_code, 400)
        bad_type = self.client.put(f"/api/owner-warehouses/{w1}",
                                   json={"owner_ids": "oops"}, headers=h)
        self.assertEqual(bad_type.status_code, 400)
        bad_wh = self.client.put("/api/owner-warehouses/999999",
                                 json={"owner_ids": []}, headers=h)
        self.assertEqual(bad_wh.status_code, 404)

    def test_clear_grid_disables_filter(self):
        o1, o2 = self._owner("GRID-OW-A"), self._owner("GRID-OW-B")
        w1 = self._warehouse("GRID-WH-A")
        self._set_grid(w1, [o1])
        self.assertNotIn(o2, self._owner_ids("/api/owners?page_size=100", self._wh(w1)))
        body = self._set_grid(w1, [])
        self.assertEqual(body["cells"], [])
        self.assertEqual(body["enabled_warehouse_ids"], [])
        ids = self._owner_ids("/api/owners?page_size=100", self._wh(w1))
        self.assertIn(o1, ids)
        self.assertIn(o2, ids)
        ctx = self.client.get("/api/scope/context", headers=self._wh(w1)).get_json()
        self.assertFalse(ctx["grid_enabled"])


if __name__ == "__main__":
    unittest.main()
