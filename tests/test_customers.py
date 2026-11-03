"""客户主数据接口测试。"""
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

_CLIENT = None


def client():
    global _CLIENT
    if _CLIENT is None:
        main.init_db()
        _CLIENT = main.app.test_client()
    return _CLIENT


class CustomerApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            # 先子后主：dn 引用 customer，customer 引用 owner
            conn.execute("DELETE FROM dn_pick")
            conn.execute("DELETE FROM dn_line")
            conn.execute("DELETE FROM dn")
            conn.execute("DELETE FROM customer")
            conn.execute("DELETE FROM product")
            conn.execute("DELETE FROM owner")
            conn.commit()
        finally:
            conn.close()

    def _mk_owner(self, code="OW1"):
        return self.client.post(
            "/api/owners", json={"code": code, "name": f"货主{code}"}
        ).get_json()["owner"]["id"]

    def _mk_customer(self, owner_id, code="C1", name="客户1", **overrides):
        payload = {"code": code, "name": name, "owner_id": owner_id}
        payload.update(overrides)
        return self.client.post("/api/customers", json=payload).get_json()

    # ---------- 创建 ----------
    def test_create_ok_and_defaults(self):
        oid = self._mk_owner()
        body = self._mk_customer(oid)
        self.assertEqual(body["customer"]["status"], "active")
        self.assertEqual(body["customer"]["owner_id"], oid)

    def test_create_duplicate_code(self):
        oid = self._mk_owner()
        self._mk_customer(oid, code="C-X")
        resp = self.client.post("/api/customers", json={
            "code": "C-X", "name": "重复", "owner_id": oid})
        self.assertEqual(resp.status_code, 409)

    def test_create_owner_not_found(self):
        resp = self.client.post("/api/customers", json={
            "code": "C-Y", "name": "客户", "owner_id": 9999})
        self.assertEqual(resp.status_code, 400)

    def test_create_missing_fields(self):
        self._mk_owner()
        resp = self.client.post("/api/customers", json={"code": "C-Z"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("name", resp.get_json()["errors"][0])

    # ---------- 列表 / 过滤 ----------
    def test_list_and_filters(self):
        o1 = self._mk_owner("OW-A")
        o2 = self._mk_owner("OW-B")
        self._mk_customer(o1, "C-A", "甲客户")
        self._mk_customer(o2, "C-B", "乙客户")
        data = self.client.get("/api/customers").get_json()
        self.assertEqual(data["total"], 2)
        # 按货主过滤（多货主隔离）
        data = self.client.get(f"/api/customers?owner_id={o1}").get_json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["customers"][0]["owner_code"], "OW-A")
        # 关键词搜索
        data = self.client.get("/api/customers?keyword=乙").get_json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["customers"][0]["name"], "乙客户")

    # ---------- 更新 / 删除保护 ----------
    def test_update_and_delete(self):
        oid = self._mk_owner()
        cid = self._mk_customer(oid)["customer"]["id"]
        resp = self.client.put(f"/api/customers/{cid}", json={"name": "新名"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["customer"]["name"], "新名")

        # 无引用的客户可删除
        resp = self.client.delete(f"/api/customers/{cid}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            self.client.get(f"/api/customers/{cid}").status_code, 404)

    def test_delete_blocked_when_referenced_by_dn(self):
        """客户被出库单引用时禁止删除，保留溯源链路。"""
        oid = self._mk_owner()
        cid = self._mk_customer(oid)["customer"]["id"]
        pid = self.client.post("/api/products", json={
            "sku": "P1", "name": "物料", "owner_id": oid}).get_json()["product"]["id"]
        self.client.post("/api/dns", json={
            "code": "DN-CUST", "owner_id": oid, "customer_id": cid,
            "lines": [{"product_id": pid, "ordered_qty": 3}]})
        resp = self.client.delete(f"/api/customers/{cid}")
        self.assertEqual(resp.status_code, 409)
        self.assertIn("出库单", resp.get_json()["error"])


    # ---------- M8 扩展字段（email/level/region） ----------
    def test_create_accepts_extended_fields(self):
        oid = self._mk_owner()
        body = self._mk_customer(
            oid, code="C-EXT", name="扩展客户",
            email="buyer@acme.com", level="金牌", region="华南",
        )
        self.assertEqual(body["customer"]["email"], "buyer@acme.com")
        self.assertEqual(body["customer"]["level"], "金牌")
        self.assertEqual(body["customer"]["region"], "华南")

    def test_create_default_level_is_normal(self):
        oid = self._mk_owner()
        c = self._mk_customer(oid, code="C-DEF", name="默认等级")["customer"]
        self.assertEqual(c["level"], "普通")

    def test_update_keeps_extended_fields(self):
        oid = self._mk_owner()
        cid = self._mk_customer(
            oid, code="C-KEEP", name="保留字段", email="keep@x.com", level="VIP"
        )["customer"]["id"]
        resp = self.client.put(f"/api/customers/{cid}", json={"name": "改名"})
        self.assertEqual(resp.status_code, 200)
        c = resp.get_json()["customer"]
        self.assertEqual(c["name"], "改名")
        self.assertEqual(c["email"], "keep@x.com")  # 未动字段不变
        self.assertEqual(c["level"], "VIP")

    # ---------- M12 省市区 + 详细地址 + 经营范围 ----------
    def test_create_accepts_m12_fields(self):
        oid = self._mk_owner()
        c = self._mk_customer(
            oid, code="C-M12", name="区域客户", province="广东省", city="深圳市",
            district="南山区", detail_address="科技园 1 栋", business_scope="医疗器械销售",
        )["customer"]
        self.assertEqual(c["province"], "广东省")
        self.assertEqual(c["city"], "深圳市")
        self.assertEqual(c["district"], "南山区")
        self.assertEqual(c["detail_address"], "科技园 1 栋")
        self.assertEqual(c["business_scope"], "医疗器械销售")

    def test_update_m12_fields(self):
        oid = self._mk_owner()
        cid = self._mk_customer(oid, code="C-M12B", name="客户B")["customer"]["id"]
        resp = self.client.put(
            f"/api/customers/{cid}",
            json={"province": "浙江省", "city": "杭州市", "detail_address": "西湖区"},
        )
        self.assertEqual(resp.status_code, 200)
        c = resp.get_json()["customer"]
        self.assertEqual(c["province"], "浙江省")
        self.assertEqual(c["city"], "杭州市")
        self.assertEqual(c["detail_address"], "西湖区")
        self.assertEqual(c["name"], "客户B")  # 未动字段不变


if __name__ == "__main__":
    unittest.main()
