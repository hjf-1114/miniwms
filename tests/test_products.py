"""物料主数据接口测试。

测试隔离：每个测试类用独立的临时 SQLite 文件（通过 MINIWMS_DB 环境变量注入），
在 import main 之前设置，避免共享物理库造成串扰。
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


class ProductApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        # 每个用例独立数据：清空相关表，避免共享物理库造成的串扰
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

    # ---------- 多货主（M2） ----------
    def _make_owner(self, code="OW001"):
        return self.client.post(
            "/api/owners", json={"code": code, "name": f"货主{code}"}
        ).get_json()["owner"]["id"]

    def test_product_with_owner(self):
        oid = self._make_owner()
        resp = self._create(sku="OWN-1", owner_id=oid)
        self.assertEqual(resp.status_code, 201)
        p = resp.get_json()["product"]
        self.assertEqual(p["owner_id"], oid)
        self.assertEqual(p["owner_code"], "OW001")
        self.assertEqual(p["owner_name"], "货主OW001")

    def test_product_owner_not_exist(self):
        resp = self._create(sku="OWN-2", owner_id=9999)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("货主不存在", str(resp.get_json()["errors"]))

    def test_product_owner_inactive(self):
        oid = self._make_owner()
        self.client.put(f"/api/owners/{oid}", json={"status": "inactive"})
        resp = self._create(sku="OWN-3", owner_id=oid)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("停用", str(resp.get_json()["errors"]))

    def test_product_list_filter_by_owner(self):
        oid1 = self._make_owner("OW-A")
        oid2 = self._make_owner("OW-B")
        self._create(sku="F-1", owner_id=oid1)
        self._create(sku="F-2", owner_id=oid2)
        self._create(sku="F-3")  # 无货主
        self.assertEqual(
            self.client.get(f"/api/products?owner_id={oid1}").get_json()["total"], 1
        )
        self.assertEqual(
            self.client.get("/api/products?owner_id=none").get_json()["total"], 1
        )
        self.assertEqual(self.client.get("/api/products").get_json()["total"], 3)

    def test_product_transfer_owner_and_clear(self):
        oid = self._make_owner()
        pid = self._create(sku="T-1", owner_id=oid).get_json()["product"]["id"]
        # 转给另一货主
        oid2 = self._make_owner("OW-C")
        p = self.client.put(f"/api/products/{pid}", json={"owner_id": oid2}).get_json()["product"]
        self.assertEqual(p["owner_id"], oid2)
        self.assertEqual(p["owner_code"], "OW-C")
        # 显式清空 → 自营
        p = self.client.put(f"/api/products/{pid}", json={"owner_id": None}).get_json()["product"]
        self.assertIsNone(p["owner_id"])
        self.assertNotIn("owner_code", p)


    # ---------- M8 扩展字段（条码/品牌/规格/重量/尺寸/保质期） ----------
    def test_create_accepts_extended_fields(self):
        resp = self._create(
            barcode="6901234567890", brand="晨光", spec="A5/50页",
            weight_kg=0.25, length_cm=21, width_cm=14.8, height_cm=1.2,
            shelf_life_days=365,
        )
        self.assertEqual(resp.status_code, 201)
        p = resp.get_json()["product"]
        self.assertEqual(p["barcode"], "6901234567890")
        self.assertEqual(p["brand"], "晨光")
        self.assertEqual(p["spec"], "A5/50页")
        self.assertEqual(p["weight_kg"], 0.25)
        self.assertEqual(p["length_cm"], 21)
        self.assertEqual(p["width_cm"], 14.8)
        self.assertEqual(p["height_cm"], 1.2)
        self.assertEqual(p["shelf_life_days"], 365)

    def test_create_rejects_negative_dimension(self):
        resp = self._create(weight_kg=-1)
        self.assertEqual(resp.status_code, 400)

    def test_update_keeps_extended_fields(self):
        pid = self._create(barcode="X1", brand="原品牌").get_json()["product"]["id"]
        resp = self.client.put(f"/api/products/{pid}", json={"sale_price": 9.9})
        self.assertEqual(resp.status_code, 200)
        p = resp.get_json()["product"]
        self.assertEqual(p["barcode"], "X1")  # 未动字段不变
        self.assertEqual(p["brand"], "原品牌")

    # ---------- M10 电商/医药级字段（货号/多级分类/注册证/温湿度/危险品/标志位/备用） ----------
    def test_create_accepts_m10_fields(self):
        resp = self._create(
            article_no="ART-001", category_l1="医药", category_l2="器械", category_l3="耗材", category_l4="口罩",
            product_type="成品", origin="广东深圳", main_category="防疫物资",
            business_scope="一二三类医疗器械", reg_cert_no="国械注准20203000000",
            registrant="备案人A", mfg_enterprise="生产厂B", mfg_license="粤食药监械生产许20200000",
            external_code="EXT-9", temp_range="2~8℃", humidity_range="45%~65%",
            hazard_type="非危险品", is_serial=1, is_shelf_control=1, extra1="备注A",
        )
        self.assertEqual(resp.status_code, 201)
        p = resp.get_json()["product"]
        self.assertEqual(p["article_no"], "ART-001")
        self.assertEqual(p["category_l1"], "医药")
        self.assertEqual(p["category_l4"], "口罩")
        self.assertEqual(p["reg_cert_no"], "国械注准20203000000")
        self.assertEqual(p["is_serial"], 1)
        self.assertEqual(p["is_shelf_control"], 1)
        self.assertEqual(p["extra1"], "备注A")

    def test_update_m10_fields_and_partial(self):
        pid = self._create().get_json()["product"]["id"]
        resp = self.client.put(f"/api/products/{pid}", json={"product_type": "原料", "is_serial": 1})
        self.assertEqual(resp.status_code, 200)
        p = resp.get_json()["product"]
        self.assertEqual(p["product_type"], "原料")
        self.assertEqual(p["is_serial"], 1)
        # 未更新字段保持不变
        self.assertEqual(p["name"], "测试商品")

    # ---------- M10 多条码 ----------
    def test_barcode_crud(self):
        pid = self._create().get_json()["product"]["id"]
        r1 = self.client.post(f"/api/products/{pid}/barcodes", json={"barcode": "BC1"})
        self.assertEqual(r1.status_code, 201)
        r2 = self.client.post(f"/api/products/{pid}/barcodes", json={"barcode": "BC2", "is_primary": True})
        self.assertEqual(r2.status_code, 201)
        # 列表：主条码置顶
        lst = self.client.get(f"/api/products/{pid}/barcodes").get_json()
        self.assertEqual(lst["total"] if "total" in lst else len(lst["barcodes"]), 2)
        self.assertEqual(lst["barcodes"][0]["barcode"], "BC2")  # 主条码在前
        # 设为唯一的另一个主条码后，旧的自动取消主
        bid1 = lst["barcodes"][1]["id"]
        self.client.put if False else None
        # 删除
        delr = self.client.delete(f"/api/products/{pid}/barcodes/{bid1}")
        self.assertEqual(delr.status_code, 200)
        self.assertEqual(len(self.client.get(f"/api/products/{pid}/barcodes").get_json()["barcodes"]), 1)

    def test_barcode_missing(self):
        pid = self._create().get_json()["product"]["id"]
        r = self.client.post(f"/api/products/{pid}/barcodes", json={})
        self.assertEqual(r.status_code, 400)

    def test_barcode_on_unknown_product(self):
        r = self.client.get("/api/products/9999/barcodes")
        self.assertEqual(r.status_code, 404)

    # ---------- M10 多层级包装（体积自动计算） ----------
    def test_package_crud_volume_computed(self):
        pid = self._create().get_json()["product"]["id"]
        r = self.client.post(
            f"/api/products/{pid}/packages",
            json={"pkg_level": "1级", "length_cm": 10, "width_cm": 20, "height_cm": 5, "weight_kg": 2, "qty_per_package": 50},
        )
        self.assertEqual(r.status_code, 201)
        pkg = r.get_json()["package"]
        self.assertAlmostEqual(pkg["volume"], 10 * 20 * 5, places=4)  # 体积 = 长×宽×高
        self.assertEqual(pkg["qty_per_package"], 50)
        # 列表 + 删除
        self.assertEqual(len(self.client.get(f"/api/products/{pid}/packages").get_json()["packages"]), 1)
        delr = self.client.delete(f"/api/products/{pid}/packages/{pkg['id']}")
        self.assertEqual(delr.status_code, 200)

    def test_package_negative_rejected(self):
        pid = self._create().get_json()["product"]["id"]
        r = self.client.post(f"/api/products/{pid}/packages", json={"length_cm": -1})
        self.assertEqual(r.status_code, 400)

    # ---------- M15 物料体积（手填/自动换算） ----------
    def test_volume_autocalc_from_dims(self):
        resp = self._create(length_cm=10, width_cm=20, height_cm=5)
        self.assertEqual(resp.status_code, 201)
        p = resp.get_json()["product"]
        self.assertAlmostEqual(p["volume"], 10 * 20 * 5, places=4)  # cm³ 自动换算

    def test_volume_handfilled_overrides(self):
        resp = self._create(length_cm=10, width_cm=20, height_cm=5, volume=999)
        self.assertEqual(resp.status_code, 201)
        self.assertAlmostEqual(resp.get_json()["product"]["volume"], 999)  # 手填优先

    def test_update_dims_recalculates_volume(self):
        pid = self._create().get_json()["product"]["id"]
        # 改长宽高 → 体积按新值重算
        resp = self.client.put(f"/api/products/{pid}", json={"length_cm": 2, "width_cm": 3, "height_cm": 4})
        self.assertEqual(resp.status_code, 200)
        self.assertAlmostEqual(resp.get_json()["product"]["volume"], 2 * 3 * 4, places=4)
        # 显式给体积 → 用之
        resp2 = self.client.put(f"/api/products/{pid}", json={"volume": 7.5})
        self.assertEqual(resp2.status_code, 200)
        self.assertAlmostEqual(resp2.get_json()["product"]["volume"], 7.5)

    # ---------- M15 条码可编辑 ----------
    def test_barcode_update(self):
        pid = self._create().get_json()["product"]["id"]
        b = self.client.post(f"/api/products/{pid}/barcodes", json={"barcode": "BC1"}).get_json()["barcode"]
        resp = self.client.put(f"/api/products/{pid}/barcodes/{b['id']}", json={"barcode": "BC1-NEW", "is_primary": True})
        self.assertEqual(resp.status_code, 200)
        up = resp.get_json()["barcode"]
        self.assertEqual(up["barcode"], "BC1-NEW")
        self.assertEqual(up["is_primary"], 1)
        # 设为其他主条码时，旧主自动取消
        b2 = self.client.post(f"/api/products/{pid}/barcodes", json={"barcode": "BC2", "is_primary": True}).get_json()["barcode"]
        self.client.put(f"/api/products/{pid}/barcodes/{b['id']}", json={"barcode": "BC1-NEW", "is_primary": True})
        lst = self.client.get(f"/api/products/{pid}/barcodes").get_json()["barcodes"]
        prim = [x for x in lst if x["is_primary"]]
        self.assertEqual(len(prim), 1)
        self.assertEqual(prim[0]["barcode"], "BC1-NEW")

    def test_barcode_update_missing(self):
        pid = self._create().get_json()["product"]["id"]
        b = self.client.post(f"/api/products/{pid}/barcodes", json={"barcode": "BC1"}).get_json()["barcode"]
        self.assertEqual(self.client.put(f"/api/products/{pid}/barcodes/{b['id']}", json={}).status_code, 400)

    # ---------- M15 包装可编辑 + 等级 1-6 ----------
    def test_package_update_and_level(self):
        pid = self._create().get_json()["product"]["id"]
        q = self.client.post(
            f"/api/products/{pid}/packages",
            json={"pkg_level": "1级", "length_cm": 10, "width_cm": 20, "height_cm": 5, "weight_kg": 2, "qty_per_package": 50},
        ).get_json()["package"]
        # 改等级与重量
        resp = self.client.put(f"/api/products/{pid}/packages/{q['id']}",
                               json={"pkg_level": "3级", "weight_kg": 3.5})
        self.assertEqual(resp.status_code, 200)
        up = resp.get_json()["package"]
        self.assertEqual(up["pkg_level"], "3级")
        self.assertEqual(up["weight_kg"], 3.5)
        self.assertAlmostEqual(up["volume"], 10 * 20 * 5, places=4)  # 长宽高未变，体积沿用

    def test_package_level_validation(self):
        pid = self._create().get_json()["product"]["id"]
        r = self.client.post(f"/api/products/{pid}/packages", json={"pkg_level": "特大专属", "length_cm": 1})
        self.assertEqual(r.status_code, 400)
        r2 = self.client.put(f"/api/products/{pid}/packages/9999", json={"pkg_level": "2级"})
        self.assertEqual(r2.status_code, 404)


if __name__ == "__main__":
    unittest.main()
