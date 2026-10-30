"""商品档案接口测试。

测试隔离：每个测试类用独立的临时 SQLite 文件（通过 STOCKPILOT_DB 环境变量注入），
在 import main 之前设置，避免共享物理库造成串扰。
"""
import os
import sys
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="stockpilot_test_")
os.environ["STOCKPILOT_DB"] = os.path.join(_TMP, "test.db")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402  (须在环境变量设置之后)


class ProductApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        # 每个用例独立数据：清空 product 表，避免共享物理库造成的串扰
        import sqlite3
        conn = sqlite3.connect(os.environ["STOCKPILOT_DB"])
        try:
            conn.execute("DELETE FROM product")
            conn.commit()
        finally:
            conn.close()

    def _create(self, **overrides):
        payload = {
            "sku": "SKU-001",
            "name": "测试商品",
            "category": "文具",
            "unit": "件",
            "purchase_price": 3.5,
            "sale_price": 5.0,
            "min_stock": 10,
            "note": "",
        }
        payload.update(overrides)
        return self.client.post("/api/products", json=payload)

    # ---------- 创建 ----------
    def test_create_ok(self):
        resp = self._create()
        self.assertEqual(resp.status_code, 201)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["product"]["sku"], "SKU-001")
        self.assertEqual(body["product"]["stock_qty"], 0)  # 建档默认零库存

    def test_create_missing_sku(self):
        resp = self._create(sku="")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("sku 必填", str(resp.get_json()["errors"]))

    def test_create_negative_price(self):
        resp = self._create(sale_price=-1)
        self.assertEqual(resp.status_code, 400)

    def test_create_duplicate_sku(self):
        self._create()
        resp = self._create(name="另一个商品")
        self.assertEqual(resp.status_code, 409)

    def test_create_non_numeric_price(self):
        resp = self._create(purchase_price="abc")
        self.assertEqual(resp.status_code, 400)

    # ---------- 查询 ----------
    def test_list_pagination_and_search(self):
        for i in range(25):
            self._create(sku=f"P-{i:03d}", name=f"商品{i}", category="文具" if i < 20 else "食品")
        # 默认分页 20 条
        body = self.client.get("/api/products").get_json()
        self.assertEqual(body["total"], 25)
        self.assertEqual(len(body["products"]), 20)
        # 翻页
        body2 = self.client.get("/api/products?page=2").get_json()
        self.assertEqual(len(body2["products"]), 5)
        # 关键词搜索（子串命中：商品7 只匹配自身）
        body3 = self.client.get("/api/products?keyword=商品7").get_json()
        self.assertEqual(body3["total"], 1)
        # SKU 搜索
        body3b = self.client.get("/api/products?keyword=P-00").get_json()
        self.assertEqual(body3b["total"], 10)  # P-000 ~ P-009
        # category 过滤
        body4 = self.client.get("/api/products?category=食品").get_json()
        self.assertEqual(body4["total"], 5)
        # status 过滤
        body5 = self.client.get("/api/products?status=active").get_json()
        self.assertEqual(body5["total"], 25)

    def test_get_not_found(self):
        resp = self.client.get("/api/products/9999")
        self.assertEqual(resp.status_code, 404)

    # ---------- 更新 ----------
    def test_update_partial(self):
        pid = self._create().get_json()["product"]["id"]
        resp = self.client.put(f"/api/products/{pid}", json={"sale_price": 6.0})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["product"]["sale_price"], 6.0)
        # 未更新字段保持不变
        self.assertEqual(resp.get_json()["product"]["name"], "测试商品")

    def test_update_empty_payload(self):
        pid = self._create().get_json()["product"]["id"]
        resp = self.client.put(f"/api/products/{pid}", json={})
        self.assertEqual(resp.status_code, 400)

    def test_update_sku_conflict(self):
        pid1 = self._create(sku="A-1").get_json()["product"]["id"]
        self._create(sku="A-2")
        resp = self.client.put(f"/api/products/{pid1}", json={"sku": "A-2"})
        self.assertEqual(resp.status_code, 409)

    # ---------- 删除 ----------
    def test_delete_with_stock_forbidden(self):
        pid = self._create().get_json()["product"]["id"]
        self.client.put(f"/api/products/{pid}", json={"stock_qty": 5})
        resp = self.client.delete(f"/api/products/{pid}")
        self.assertEqual(resp.status_code, 409)

    def test_delete_zero_stock_ok(self):
        pid = self._create().get_json()["product"]["id"]
        resp = self.client.delete(f"/api/products/{pid}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.get(f"/api/products/{pid}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
