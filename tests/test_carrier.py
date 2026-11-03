"""承运商主数据接口测试。

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
os.environ.setdefault("MINIWMS_DB", os.path.join(_TMP, "test.db"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402  (须在环境变量设置之后)


class CarrierApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        import sqlite3
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            conn.execute("DELETE FROM carrier")
            conn.commit()
        finally:
            conn.close()

    def _create(self, **overrides):
        payload = {"code": "CAR001", "name": "顺丰速运", "carrier_type": "快递", "contact": "王经理", "phone": "95338"}
        payload.update(overrides)
        return self.client.post("/api/carriers", json=payload)

    # ---------- 创建 ----------
    def test_create_ok(self):
        resp = self._create()
        self.assertEqual(resp.status_code, 201)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["carrier"]["code"], "CAR001")
        self.assertEqual(body["carrier"]["carrier_type"], "快递")
        self.assertEqual(body["carrier"]["status"], "active")

    def test_create_missing_code(self):
        resp = self._create(code="")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("code 必填", str(resp.get_json()["errors"]))

    def test_create_missing_name(self):
        resp = self._create(name="")
        self.assertEqual(resp.status_code, 400)

    def test_create_duplicate_code(self):
        self._create()
        resp = self._create(name="另一家")
        self.assertEqual(resp.status_code, 409)

    def test_create_bad_type(self):
        resp = self._create(carrier_type="火箭")
        self.assertEqual(resp.status_code, 400)

    # ---------- 查询 ----------
    def test_list_pagination_and_search(self):
        for i in range(25):
            self._create(code=f"CAR-{i:03d}", name=f"承运商{i}", carrier_type="快递" if i % 2 == 0 else "零担")
        body = self.client.get("/api/carriers").get_json()
        self.assertEqual(body["total"], 25)
        self.assertEqual(len(body["carriers"]), 20)
        body2 = self.client.get("/api/carriers?page=2").get_json()
        self.assertEqual(len(body2["carriers"]), 5)
        body3 = self.client.get("/api/carriers?keyword=承运商7").get_json()
        self.assertEqual(body3["total"], 1)
        body4 = self.client.get("/api/carriers?carrier_type=零担").get_json()
        self.assertEqual(body4["total"], 12)  # 奇数 13 个? 0..24 共 25, 偶数=13 快递, 奇数=12 零担
        body5 = self.client.get("/api/carriers?status=active").get_json()
        self.assertEqual(body5["total"], 25)

    def test_get_not_found(self):
        self.assertEqual(self.client.get("/api/carriers/9999").status_code, 404)

    # ---------- 更新 ----------
    def test_update_partial(self):
        cid = self._create().get_json()["carrier"]["id"]
        resp = self.client.put(f"/api/carriers/{cid}", json={"contact": "李经理"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["carrier"]["contact"], "李经理")
        self.assertEqual(resp.get_json()["carrier"]["name"], "顺丰速运")  # 未动字段不变

    def test_update_code_conflict(self):
        cid = self._create(code="A-1").get_json()["carrier"]["id"]
        self._create(code="A-2")
        self.assertEqual(
            self.client.put(f"/api/carriers/{cid}", json={"code": "A-2"}).status_code, 409
        )

    def test_update_bad_type(self):
        cid = self._create().get_json()["carrier"]["id"]
        self.assertEqual(
            self.client.put(f"/api/carriers/{cid}", json={"carrier_type": "飞艇"}).status_code, 400
        )

    # ---------- 删除 ----------
    def test_delete_ok(self):
        cid = self._create().get_json()["carrier"]["id"]
        self.assertEqual(self.client.delete(f"/api/carriers/{cid}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/carriers/{cid}").status_code, 404)

    def test_delete_not_found(self):
        self.assertEqual(self.client.delete("/api/carriers/9999").status_code, 404)


if __name__ == "__main__":
    unittest.main()
