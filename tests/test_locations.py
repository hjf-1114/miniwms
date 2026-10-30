"""库位主数据接口测试。

与其它测试模块共享同一临时库（main.DB_PATH 首次 import 冻结），
setUp 先子后主清表。
"""
import os
import sys
import tempfile
import unittest

_TMP = os.path.join(tempfile.gettempdir(), "miniwms_unittest")
os.makedirs(_TMP, exist_ok=True)
os.environ.setdefault("MINIWMS_DB", os.path.join(_TMP, "test.db"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402  (须在环境变量设置之后)


class LocationApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        import sqlite3
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            # 先子后主
            conn.execute("DELETE FROM stock")
            conn.execute("DELETE FROM product")
            conn.execute("DELETE FROM owner")
            conn.execute("DELETE FROM location")
            conn.commit()
        finally:
            conn.close()

    def _create(self, **overrides):
        payload = {"code": "A-01-01", "name": "存储位", "zone": "A区", "loc_type": "storage"}
        payload.update(overrides)
        return self.client.post("/api/locations", json=payload)

    # ---------- 创建 ----------
    def test_create_ok(self):
        resp = self._create()
        self.assertEqual(resp.status_code, 201)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        loc = body["location"]
        self.assertEqual(loc["code"], "A-01-01")
        self.assertEqual(loc["loc_type_label"], "存储区")
        self.assertEqual(loc["capacity"], 0)  # 0 = 不限容

    def test_create_missing_code(self):
        resp = self._create(code="")
        self.assertEqual(resp.status_code, 400)

    def test_create_duplicate_code(self):
        self._create()
        resp = self._create(name="重复编码库位")
        self.assertEqual(resp.status_code, 409)

    def test_create_bad_type_and_negative_capacity(self):
        self.assertEqual(self._create(loc_type="frozen").status_code, 400)
        self.assertEqual(self._create(capacity=-1).status_code, 400)

    # ---------- 查询 ----------
    def test_list_filters(self):
        self._create(code="A-01", zone="A区", loc_type="storage")
        self._create(code="B-01", zone="B区", loc_type="picking")
        self._create(code="C-01", zone="C区", loc_type="receiving", status="inactive")
        self.assertEqual(self.client.get("/api/locations").get_json()["total"], 3)
        self.assertEqual(
            self.client.get("/api/locations?zone=B区").get_json()["total"], 1
        )
        self.assertEqual(
            self.client.get("/api/locations?loc_type=receiving").get_json()["total"], 1
        )
        self.assertEqual(
            self.client.get("/api/locations?status=active").get_json()["total"], 2
        )
        self.assertEqual(
            self.client.get("/api/locations?keyword=B-01").get_json()["total"], 1
        )
        # 按编码排序
        body = self.client.get("/api/locations").get_json()
        self.assertEqual([x["code"] for x in body["locations"]], ["A-01", "B-01", "C-01"])

    def test_get_not_found(self):
        self.assertEqual(self.client.get("/api/locations/9999").status_code, 404)

    # ---------- 更新 ----------
    def test_update_partial(self):
        lid = self._create().get_json()["location"]["id"]
        resp = self.client.put(f"/api/locations/{lid}", json={"capacity": 100})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()["location"]
        self.assertEqual(body["capacity"], 100)
        self.assertEqual(body["zone"], "A区")  # 未动字段不变

    def test_update_code_conflict(self):
        lid = self._create(code="X-1").get_json()["location"]["id"]
        self._create(code="X-2")
        self.assertEqual(
            self.client.put(f"/api/locations/{lid}", json={"code": "X-2"}).status_code, 409
        )

    # ---------- 删除 ----------
    def test_delete_with_stock_forbidden(self):
        import sqlite3
        lid = self._create().get_json()["location"]["id"]
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            # 直接造一条库存（stock 表 M3 已建，M4 起由业务写入）
            conn.execute(
                "INSERT INTO stock (owner_id, product_id, location_id, qty) VALUES (1, 1, ?, 5)",
                (lid,),
            )
            conn.commit()
        finally:
            conn.close()
        resp = self.client.delete(f"/api/locations/{lid}")
        self.assertEqual(resp.status_code, 409)
        self.assertIn("库存记录", resp.get_json()["error"])

    def test_delete_empty_ok(self):
        lid = self._create().get_json()["location"]["id"]
        self.assertEqual(self.client.delete(f"/api/locations/{lid}").status_code, 200)


if __name__ == "__main__":
    unittest.main()
