"""M20：储区主数据 / 仓库维度 / 混放开关 / 货品推荐储区 测试。"""
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


class ZoneApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            for t in ("product_location", "stock", "asn_line", "asn",
                      "product", "location", "owner"):
                conn.execute(f"DELETE FROM {t}")
            conn.execute("DELETE FROM zone WHERE code LIKE 'TEST-%'")
            conn.execute("DELETE FROM warehouse WHERE code LIKE 'TEST-WH-%'")
            conn.commit()
        finally:
            conn.close()

    # ---------- 夹具 ----------
    def _warehouse(self, code="TEST-WH-1"):
        r = self.client.post("/api/warehouses", json={"code": code, "name": f"仓{code}"})
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()["warehouse"]["id"]

    def _zone(self, code="TEST-Z1", **kw):
        payload = {"code": code, "name": f"储区{code}"}
        payload.update(kw)
        return self.client.post("/api/zones", json=payload)

    def _location(self, code="TEST-L1", **kw):
        payload = {"code": code, "name": code, "loc_type": "storage"}
        payload.update(kw)
        return self.client.post("/api/locations", json=payload)

    def _product(self, sku="TEST-P1", **kw):
        payload = {"sku": sku, "name": f"物料{sku}"}
        payload.update(kw)
        return self.client.post("/api/products", json=payload)

    # ---------- 储区 CRUD ----------
    def test_create_zone_with_mix_flags(self):
        r = self._zone(temp_class="冷藏", allow_mix_product=1, allow_mix_owner=1)
        self.assertEqual(r.status_code, 201, r.get_json())
        z = r.get_json()["zone"]
        self.assertEqual(z["temp_class"], "冷藏")
        self.assertEqual(z["allow_mix_product"], 1)
        self.assertEqual(z["allow_mix_batch"], 0)
        self.assertIn("允许产品混放", z["mix_labels"])
        self.assertIsNone(z["warehouse_id"])  # 留空＝全仓共享

    def test_zone_code_unique(self):
        self.assertEqual(self._zone().status_code, 201)
        self.assertEqual(self._zone().status_code, 409)

    def test_invalid_temp_class_rejected(self):
        r = self._zone(temp_class="火星")
        self.assertEqual(r.status_code, 400)
        self.assertIn("temp_class", str(r.get_json()["errors"]))

    def test_unknown_warehouse_rejected(self):
        self.assertEqual(self._zone(warehouse_id=999999).status_code, 400)

    def test_partial_update_keeps_other_fields(self):
        zid = self._zone(temp_class="阴凉", note="原备注").get_json()["zone"]["id"]
        r = self.client.put(f"/api/zones/{zid}", json={"status": "inactive"})
        self.assertEqual(r.status_code, 200, r.get_json())
        z = r.get_json()["zone"]
        self.assertEqual(z["status"], "inactive")
        self.assertEqual(z["temp_class"], "阴凉")
        self.assertEqual(z["note"], "原备注")

    def test_delete_blocked_by_locations(self):
        zid = self._zone().get_json()["zone"]["id"]
        self.assertEqual(self._location(zone_id=zid).status_code, 201)
        r = self.client.delete(f"/api/zones/{zid}")
        self.assertEqual(r.status_code, 409)
        self.assertIn("库位", r.get_json()["error"])

    def test_delete_blocked_by_product_default_zone(self):
        zid = self._zone().get_json()["zone"]["id"]
        self.assertEqual(self._product(default_zone_id=zid).status_code, 201)
        self.assertEqual(self.client.delete(f"/api/zones/{zid}").status_code, 409)

    def test_delete_empty_zone_ok(self):
        zid = self._zone().get_json()["zone"]["id"]
        self.assertEqual(self.client.delete(f"/api/zones/{zid}").status_code, 200)

    # ---------- 仓库 / 储区维度 ----------
    def test_location_carries_zone_and_warehouse(self):
        wid = self._warehouse()
        zid = self._zone(warehouse_id=wid).get_json()["zone"]["id"]
        r = self._location(zone_id=zid, warehouse_id=wid)
        self.assertEqual(r.status_code, 201, r.get_json())
        loc = r.get_json()["location"]
        self.assertEqual(loc["zone_id"], zid)
        self.assertEqual(loc["warehouse_id"], wid)
        self.assertTrue(loc["zone_name"])
        self.assertTrue(loc["warehouse_name"])

    def test_location_zone_warehouse_mismatch_rejected(self):
        w1, w2 = self._warehouse("TEST-WH-1"), self._warehouse("TEST-WH-2")
        zid = self._zone(warehouse_id=w1).get_json()["zone"]["id"]
        r = self._location(zone_id=zid, warehouse_id=w2)
        self.assertEqual(r.status_code, 400)
        self.assertIn("不一致", str(r.get_json()["errors"]))

    def test_shared_zone_allowed_for_any_warehouse(self):
        wid = self._warehouse()
        # 播种的全仓共享储区 warehouse_id 为 NULL
        shared = [z for z in self.client.get("/api/zones").get_json()["zones"]
                  if z["warehouse_id"] is None][0]
        r = self._location(zone_id=shared["id"], warehouse_id=wid)
        self.assertEqual(r.status_code, 201, r.get_json())

    def test_zone_change_warehouse_blocked_when_locations_conflict(self):
        w1, w2 = self._warehouse("TEST-WH-1"), self._warehouse("TEST-WH-2")
        zid = self._zone(warehouse_id=w1).get_json()["zone"]["id"]
        self.assertEqual(self._location(zone_id=zid, warehouse_id=w1).status_code, 201)
        r = self.client.put(f"/api/zones/{zid}", json={"warehouse_id": w2})
        self.assertEqual(r.status_code, 409)
        self.assertIn("其它仓库", str(r.get_json()["errors"]))

    def test_list_zones_by_warehouse_includes_shared(self):
        wid = self._warehouse()
        self._zone(warehouse_id=wid)
        other = self._warehouse("TEST-WH-2")
        self._zone("TEST-Z2", warehouse_id=other)
        body = self.client.get(f"/api/zones?warehouse_id={wid}&page_size=100").get_json()
        codes = [z["code"] for z in body["zones"]]
        self.assertIn("TEST-Z1", codes)
        self.assertNotIn("TEST-Z2", codes)
        self.assertIn("ZONE-SHARED", codes)  # 全仓共享储区一并可选

    # ---------- 混放继承 ----------
    def test_location_mix_inherits_zone_then_override(self):
        zid = self._zone(allow_mix_product=1).get_json()["zone"]["id"]
        loc = self._location(zone_id=zid).get_json()["location"]
        self.assertEqual(loc["effective_mix"]["allow_mix_product"], 1)
        self.assertEqual(loc["effective_mix"]["allow_mix_product_source"], "zone")
        r = self.client.put(f"/api/locations/{loc['id']}", json={"allow_mix_product": 0})
        self.assertEqual(r.status_code, 200, r.get_json())
        eff = r.get_json()["location"]["effective_mix"]
        self.assertEqual(eff["allow_mix_product"], 0)
        self.assertEqual(eff["allow_mix_product_source"], "location")

    # ---------- 货品推荐储区 ----------
    def test_product_default_zone_validation_and_output(self):
        zid = self._zone().get_json()["zone"]["id"]
        r = self._product(default_zone_id=zid)
        self.assertEqual(r.status_code, 201, r.get_json())
        p = r.get_json()["product"]
        self.assertEqual(p["default_zone_id"], zid)
        self.assertTrue(p["default_zone_name"])
        self.assertEqual(self._product("TEST-P2", default_zone_id=999999).status_code, 400)
