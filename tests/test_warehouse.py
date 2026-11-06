"""仓库主数据接口测试（全系统共享，不绑定货主）。"""
import os
import sys
import tempfile
import unittest

_TMP = os.path.join(tempfile.gettempdir(), "miniwms_unittest")
os.makedirs(_TMP, exist_ok=True)
os.environ.setdefault("MINIWMS_DB", os.path.join(_TMP, "test.db"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402  (须在环境变量设置之后)


class WarehouseApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        import sqlite3
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            conn.execute("DELETE FROM batch_location")
            conn.execute("DELETE FROM stock_batch")
            conn.execute("DELETE FROM stock_movement")
            conn.execute("DELETE FROM stock")
            conn.execute("DELETE FROM dn_pick")
            conn.execute("DELETE FROM dn_line")
            conn.execute("DELETE FROM dn")
            conn.execute("DELETE FROM asn_line")
            conn.execute("DELETE FROM asn")
            conn.execute("DELETE FROM supplier")
            conn.execute("DELETE FROM product")
            conn.execute("DELETE FROM owner")
            conn.execute("DELETE FROM warehouse")
            conn.commit()
        finally:
            conn.close()

    def _create(self, **overrides):
        payload = {"code": "WH001", "name": "深圳中心仓", "address": "深圳",
                   "contact": "仓管", "phone": "0755"}
        payload.update(overrides)
        return self.client.post("/api/warehouses", json=payload)

    # ---------- 创建 ----------
    def test_create_ok(self):
        resp = self._create()
        self.assertEqual(resp.status_code, 201)
        w = resp.get_json()["warehouse"]
        self.assertEqual(w["name"], "深圳中心仓")
        self.assertEqual(w["status"], "active")

    def test_create_missing_fields(self):
        self.assertEqual(self._create(code="").status_code, 400)
        self.assertEqual(self._create(name="").status_code, 400)

    def test_create_duplicate_code(self):
        self._create()
        self.assertEqual(self._create(name="另一仓").status_code, 409)

    # ---------- 查询 / 过滤 ----------
    def test_list_and_search(self):
        for i in range(3):
            self._create(code=f"W-{i}", name=f"仓库{i}")
        body = self.client.get("/api/warehouses").get_json()
        self.assertEqual(body["total"], 3)
        body2 = self.client.get("/api/warehouses?keyword=仓库1").get_json()
        self.assertEqual(body2["total"], 1)
        body3 = self.client.get("/api/warehouses?status=active").get_json()
        self.assertEqual(body3["total"], 3)

    def test_get_not_found(self):
        self.assertEqual(self.client.get("/api/warehouses/9999").status_code, 404)

    # ---------- 更新 ----------
    def test_update_partial(self):
        wid = self._create().get_json()["warehouse"]["id"]
        resp = self.client.put(f"/api/warehouses/{wid}", json={"contact": "新仓管"})
        self.assertEqual(resp.status_code, 200)
        w = resp.get_json()["warehouse"]
        self.assertEqual(w["contact"], "新仓管")
        self.assertEqual(w["name"], "深圳中心仓")

    def test_update_status_toggle(self):
        wid = self._create().get_json()["warehouse"]["id"]
        resp = self.client.put(f"/api/warehouses/{wid}", json={"status": "inactive"})
        self.assertEqual(resp.get_json()["warehouse"]["status"], "inactive")
        self.assertEqual(
            self.client.put(f"/api/warehouses/{wid}", json={"status": "坏状态"}).status_code, 400
        )

    def test_update_code_conflict(self):
        self._create(code="A")
        wid = self._create(code="B").get_json()["warehouse"]["id"]
        self.assertEqual(
            self.client.put(f"/api/warehouses/{wid}", json={"code": "A"}).status_code, 409
        )

    # ---------- 删除 ----------
    def test_delete_ok(self):
        wid = self._create().get_json()["warehouse"]["id"]
        self.assertEqual(self.client.delete(f"/api/warehouses/{wid}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/warehouses/{wid}").status_code, 404)

    def test_delete_referenced_by_asn_forbidden(self):
        wid = self._create().get_json()["warehouse"]["id"]
        oid = self.client.post(
            "/api/owners", json={"code": "OW1", "name": "货主1"}
        ).get_json()["owner"]["id"]
        pid = self.client.post(
            "/api/products", json={"sku": "P1", "name": "物料1", "owner_id": oid}
        ).get_json()["product"]["id"]
        self.client.post("/api/asns", json={
            "code": "ASN-R", "owner_id": oid, "source_warehouse": wid,
            "lines": [{"product_id": pid, "expected_qty": 1}]})
        self.assertEqual(self.client.delete(f"/api/warehouses/{wid}").status_code, 409)

    def test_delete_referenced_by_dn_forbidden(self):
        wid = self._create().get_json()["warehouse"]["id"]
        oid = self.client.post(
            "/api/owners", json={"code": "OW1", "name": "货主1"}
        ).get_json()["owner"]["id"]
        cid = self.client.post(
            "/api/customers", json={"code": "C1", "name": "客户1", "owner_id": oid}
        ).get_json()["customer"]["id"]
        pid = self.client.post(
            "/api/products", json={"sku": "P1", "name": "物料1", "owner_id": oid}
        ).get_json()["product"]["id"]
        self.client.post("/api/dns", json={
            "code": "DN-R", "owner_id": oid, "customer_id": cid,
            "source_warehouse": wid,
            "lines": [{"product_id": pid, "ordered_qty": 1}]})
        self.assertEqual(self.client.delete(f"/api/warehouses/{wid}").status_code, 409)


if __name__ == "__main__":
    unittest.main()
