"""供应商主数据接口测试（归属货主，多货主隔离）。"""
import os
import sys
import tempfile
import unittest

_TMP = os.path.join(tempfile.gettempdir(), "miniwms_unittest")
os.makedirs(_TMP, exist_ok=True)
os.environ.setdefault("MINIWMS_DB", os.path.join(_TMP, "test.db"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402  (须在环境变量设置之后)


class SupplierApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        import sqlite3
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            conn.execute("DELETE FROM stock_movement")
            conn.execute("DELETE FROM stock")
            conn.execute("DELETE FROM asn_line")
            conn.execute("DELETE FROM asn")
            conn.execute("DELETE FROM supplier")
            conn.execute("DELETE FROM product")
            conn.execute("DELETE FROM owner")
            conn.commit()
        finally:
            conn.close()

    def _mk_owner(self, code="OW1"):
        return self.client.post(
            "/api/owners", json={"code": code, "name": f"货主{code}"}
        ).get_json()["owner"]["id"]

    def _create(self, owner_id, **overrides):
        payload = {"code": "SUP001", "name": "代工厂", "owner_id": owner_id,
                   "contact": "陈", "phone": "138", "address": "深南大道"}
        payload.update(overrides)
        return self.client.post("/api/suppliers", json=payload)

    # ---------- 创建 ----------
    def test_create_ok(self):
        oid = self._mk_owner()
        resp = self._create(oid)
        self.assertEqual(resp.status_code, 201)
        s = resp.get_json()["supplier"]
        self.assertEqual(s["name"], "代工厂")
        self.assertEqual(s["owner_id"], oid)
        self.assertEqual(s["status"], "active")

    def test_create_missing_fields(self):
        oid = self._mk_owner()
        self.assertEqual(self._create(oid, code="").status_code, 400)
        self.assertEqual(self._create(oid, name="").status_code, 400)
        self.assertEqual(self._create(owner_id="").status_code, 400)

    def test_create_duplicate_code(self):
        oid = self._mk_owner()
        self._create(oid)
        self.assertEqual(self._create(oid, name="另一家").status_code, 409)

    def test_create_owner_not_found(self):
        self.assertEqual(self._create(99999).status_code, 400)

    # ---------- 查询 / 过滤 ----------
    def test_list_and_filters(self):
        o1 = self._mk_owner("OW-A")
        o2 = self._mk_owner("OW-B")
        for i in range(3):
            self._create(o1, code=f"S-{i}", name=f"供应商{i}")
        self._create(o2, code="S-X", name="别家供应商")
        body = self.client.get("/api/suppliers").get_json()
        self.assertEqual(body["total"], 4)
        # 按货主过滤
        body2 = self.client.get(f"/api/suppliers?owner_id={o1}").get_json()
        self.assertEqual(body2["total"], 3)
        # 关键词
        body3 = self.client.get("/api/suppliers?keyword=别家").get_json()
        self.assertEqual(body3["total"], 1)

    def test_get_not_found(self):
        self.assertEqual(self.client.get("/api/suppliers/9999").status_code, 404)

    # ---------- 更新 ----------
    def test_update_partial(self):
        oid = self._mk_owner()
        sid = self._create(oid).get_json()["supplier"]["id"]
        resp = self.client.put(f"/api/suppliers/{sid}", json={"contact": "李先生"})
        self.assertEqual(resp.status_code, 200)
        s = resp.get_json()["supplier"]
        self.assertEqual(s["contact"], "李先生")
        self.assertEqual(s["name"], "代工厂")  # 未动字段不变

    def test_update_code_conflict(self):
        oid = self._mk_owner()
        self._create(oid, code="A")
        sid = self._create(oid, code="B").get_json()["supplier"]["id"]
        self.assertEqual(
            self.client.put(f"/api/suppliers/{sid}", json={"code": "A"}).status_code, 409
        )

    def test_update_owner_not_found(self):
        oid = self._mk_owner()
        sid = self._create(oid).get_json()["supplier"]["id"]
        self.assertEqual(
            self.client.put(f"/api/suppliers/{sid}", json={"owner_id": 99999}).status_code, 400
        )

    # ---------- 删除 ----------
    def test_delete_ok(self):
        oid = self._mk_owner()
        sid = self._create(oid).get_json()["supplier"]["id"]
        self.assertEqual(self.client.delete(f"/api/suppliers/{sid}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/suppliers/{sid}").status_code, 404)

    def test_delete_referenced_by_asn_forbidden(self):
        oid = self._mk_owner()
        sid = self._create(oid).get_json()["supplier"]["id"]
        pid = self.client.post(
            "/api/products", json={"sku": "P1", "name": "物料1", "owner_id": oid}
        ).get_json()["product"]["id"]
        self.client.post("/api/asns", json={
            "code": "ASN-R", "owner_id": oid, "supplier_id": sid,
            "lines": [{"product_id": pid, "expected_qty": 1}]})
        self.assertEqual(self.client.delete(f"/api/suppliers/{sid}").status_code, 409)


if __name__ == "__main__":
    unittest.main()
