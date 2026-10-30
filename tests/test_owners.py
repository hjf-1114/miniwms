"""货主主数据接口测试。

测试隔离：每个测试类用独立的临时 SQLite 文件（通过 MINIWMS_DB 环境变量注入），
在 import main 之前设置；每个用例 setUp 清空相关表，避免共享物理库串扰。
"""
import os
import sys
import tempfile
import unittest

_TMP = os.path.join(tempfile.gettempdir(), "miniwms_unittest")
os.makedirs(_TMP, exist_ok=True)
# 注意：main.DB_PATH 在首次 import main 时冻结，所有测试模块必须共享同一物理库路径。
# setdefault 保证无论哪个测试模块先被导入，路径只定一次。
os.environ.setdefault("MINIWMS_DB", os.path.join(_TMP, "test.db"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402  (须在环境变量设置之后)


class OwnerApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        import sqlite3
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            # 先清子表再清主表，避免外键约束
            conn.execute("DELETE FROM product")
            conn.execute("DELETE FROM owner")
            conn.commit()
        finally:
            conn.close()

    def _create(self, **overrides):
        payload = {"code": "OW001", "name": "测试货主", "contact": "张三", "phone": "13800000000"}
        payload.update(overrides)
        return self.client.post("/api/owners", json=payload)

    # ---------- 创建 ----------
    def test_create_ok(self):
        resp = self._create()
        self.assertEqual(resp.status_code, 201)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["owner"]["code"], "OW001")
        self.assertEqual(body["owner"]["status"], "active")

    def test_create_missing_code(self):
        resp = self._create(code="")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("code 必填", str(resp.get_json()["errors"]))

    def test_create_duplicate_code(self):
        self._create()
        resp = self._create(name="另一个货主")
        self.assertEqual(resp.status_code, 409)

    def test_create_bad_status(self):
        resp = self._create(status="frozen")
        self.assertEqual(resp.status_code, 400)

    # ---------- 查询 ----------
    def test_list_pagination_and_search(self):
        for i in range(25):
            self._create(code=f"OW-{i:03d}", name=f"货主{i}")
        body = self.client.get("/api/owners").get_json()
        self.assertEqual(body["total"], 25)
        self.assertEqual(len(body["owners"]), 20)
        body2 = self.client.get("/api/owners?page=2").get_json()
        self.assertEqual(len(body2["owners"]), 5)
        body3 = self.client.get("/api/owners?keyword=货主7").get_json()
        self.assertEqual(body3["total"], 1)
        body4 = self.client.get("/api/owners?status=active").get_json()
        self.assertEqual(body4["total"], 25)

    def test_get_not_found(self):
        self.assertEqual(self.client.get("/api/owners/9999").status_code, 404)

    # ---------- 更新 ----------
    def test_update_partial(self):
        oid = self._create().get_json()["owner"]["id"]
        resp = self.client.put(f"/api/owners/{oid}", json={"name": "改名货主"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["owner"]["name"], "改名货主")
        self.assertEqual(resp.get_json()["owner"]["contact"], "张三")  # 未动字段不变

    def test_update_code_conflict(self):
        oid = self._create(code="A-1").get_json()["owner"]["id"]
        self._create(code="A-2")
        self.assertEqual(
            self.client.put(f"/api/owners/{oid}", json={"code": "A-2"}).status_code, 409
        )

    # ---------- 删除 ----------
    def test_delete_with_products_forbidden(self):
        oid = self._create().get_json()["owner"]["id"]
        self.client.post(
            "/api/products", json={"sku": "P1", "name": "物料", "owner_id": oid}
        )
        resp = self.client.delete(f"/api/owners/{oid}")
        self.assertEqual(resp.status_code, 409)
        self.assertIn("物料档案", resp.get_json()["error"])

    def test_delete_empty_ok(self):
        oid = self._create().get_json()["owner"]["id"]
        self.assertEqual(self.client.delete(f"/api/owners/{oid}").status_code, 200)


if __name__ == "__main__":
    unittest.main()
