"""M20：简版上架推荐（绑定货位 / 推荐储区 / 聚拢 / 硬过滤 / 采纳）测试。"""
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


class PutawayRecommendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            for t in ("product_location", "stock_movement", "stock", "batch_receipt",
                      "batch", "asn_line", "asn", "product", "location", "owner"):
                conn.execute(f"DELETE FROM {t}")
            conn.execute("DELETE FROM zone WHERE code LIKE 'TEST-%'")
            conn.execute("DELETE FROM warehouse WHERE code LIKE 'TEST-WH%'")
            conn.commit()
        finally:
            conn.close()
        self.owner = self._post("/api/owners", {"code": "TEST-OW", "name": "测试货主"})["owner"]["id"]
        self.wh = self._post("/api/warehouses", {"code": "TEST-WH", "name": "测试仓"})["warehouse"]["id"]
        self.product = self._post("/api/products", {
            "sku": "TEST-SKU", "name": "测试物料", "owner_id": self.owner,
        })["product"]["id"]
        body = self._post("/api/asns", {
            "code": "TEST-ASN", "owner_id": self.owner, "source_warehouse": self.wh,
            "lines": [{"product_id": self.product, "expected_qty": 10, "batch_no": "LOT-A"}],
        })
        self.asn_id = body["asn"]["id"]
        self.line_id = body["lines"][0]["id"]
        self.loc_ids = {}

    def _post(self, path, payload):
        r = self.client.post(path, json=payload)
        self.assertEqual(r.status_code, 201, (path, r.get_json()))
        return r.get_json()

    def _loc(self, code, **kw):
        payload = {"code": code, "name": code, "loc_type": "storage"}
        payload.update(kw)
        out = self._post("/api/locations", payload)["location"]
        self.loc_ids[code] = out["id"]
        return out["id"]

    def _zone(self, code, **kw):
        payload = {"code": code, "name": code}
        payload.update(kw)
        return self._post("/api/zones", payload)["zone"]["id"]

    def _stock(self, loc_id, product_id, qty=5, owner_id=None):
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            conn.execute(
                "INSERT INTO stock (owner_id, product_id, location_id, qty, allocated_qty) "
                "VALUES (?, ?, ?, ?, 0)",
                (owner_id or self.owner, product_id, loc_id, qty),
            )
            conn.commit()
        finally:
            conn.close()

    def _recommend(self, qty=1, **extra):
        q = f"/api/putaway/recommend?asn_id={self.asn_id}&line_id={self.line_id}&qty={qty}"
        for k, v in extra.items():
            q += f"&{k}={v}"
        return self.client.get(q)

    def _rec_codes(self, body):
        return [r["code"] for r in body["recommendations"]]

    # ---------- 参数与边界 ----------
    def test_missing_params_rejected(self):
        self.assertEqual(self.client.get("/api/putaway/recommend").status_code, 400)

    def test_unknown_asn_rejected(self):
        r = self.client.get("/api/putaway/recommend?asn_id=999999&line_id=1")
        self.assertEqual(r.status_code, 400)

    def test_no_active_storage_location_returns_empty(self):
        r = self._recommend()
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["recommendations"], [])

    # ---------- 排序 ----------
    def test_bound_location_ranks_first(self):
        a, b = self._loc("TEST-A"), self._loc("TEST-B")
        self._post("/api/product-locations", {"product_id": self.product, "location_id": b})
        body = self._recommend().get_json()
        self.assertEqual(body["recommendations"][0]["location_id"], b)
        self.assertIn("bound_location", body["recommendations"][0]["reasons"])
        self.assertEqual(body["bound_location_count"], 1)
        self.assertIn(a, [x["location_id"] for x in body["recommendations"]])

    def test_default_zone_bonus(self):
        zid = self._zone("TEST-Z")
        self._loc("TEST-IN", zone_id=zid)
        self._loc("TEST-OUT")
        r = self.client.put(f"/api/products/{self.product}", json={"default_zone_id": zid})
        self.assertEqual(r.status_code, 200, r.get_json())
        body = self._recommend().get_json()
        first = body["recommendations"][0]
        self.assertEqual(first["code"], "TEST-IN")
        self.assertIn("default_zone", first["reasons"])
        self.assertEqual(first["breakdown"]["推荐储区"], 60)

    def test_same_product_gathering(self):
        a, b = self._loc("TEST-A"), self._loc("TEST-B")
        self._stock(a, self.product)
        body = self._recommend().get_json()
        by_code = {r["code"]: r for r in body["recommendations"]}
        self.assertIn("same_product", by_code["TEST-A"]["reasons"])
        self.assertNotIn("same_product", by_code["TEST-B"]["reasons"])

    def test_empty_location_bonus(self):
        a, b = self._loc("TEST-A"), self._loc("TEST-B")
        self._stock(a, self.product)
        body = self._recommend().get_json()
        by_code = {r["code"]: r for r in body["recommendations"]}
        self.assertIn("empty_location", by_code["TEST-B"]["reasons"])
        self.assertNotIn("empty_location", by_code["TEST-A"]["reasons"])

    # ---------- 硬过滤 ----------
    def test_temp_class_hard_filter(self):
        cold = self._zone("TEST-COLD", temp_class="冷藏")
        self._loc("TEST-常温", zone_id=self._zone("TEST-NORMAL", temp_class="常温"))
        self._loc("TEST-冷藏", zone_id=cold)
        r = self.client.put(f"/api/products/{self.product}", json={"temp_range": "2~8℃冷藏"})
        self.assertEqual(r.status_code, 200, r.get_json())
        body = self._recommend().get_json()
        self.assertEqual(self._rec_codes(body), ["TEST-冷藏"])
        self.assertIn("温区不匹配", str([x["reason"] for x in body["rejected"]]))

    def test_capacity_hard_filter(self):
        self._loc("TEST-FULL", capacity=5)
        self._loc("TEST-ROOM", capacity=100)
        self._stock(self.loc_ids["TEST-FULL"], self.product, qty=5)
        body = self._recommend(qty=1).get_json()
        self.assertEqual(self._rec_codes(body), ["TEST-ROOM"])
        self.assertIn("剩余容量不足", str([x["reason"] for x in body["rejected"]]))

    def test_mix_product_blocked_by_default(self):
        zid = self._zone("TEST-Z")
        loc = self._loc("TEST-MIX", zone_id=zid)
        other = self._post("/api/products", {"sku": "TEST-OTHER", "name": "别的料"})["product"]["id"]
        self._stock(loc, other)
        body = self._recommend().get_json()
        self.assertEqual(self._rec_codes(body), [])
        self.assertIn("禁止产品混放", str([x["reason"] for x in body["rejected"]]))

    def test_mix_allowed_when_zone_flag_on(self):
        zid = self._zone("TEST-Z", allow_mix_product=1)
        loc = self._loc("TEST-MIX", zone_id=zid)
        other = self._post("/api/products", {"sku": "TEST-OTHER", "name": "别的料"})["product"]["id"]
        self._stock(loc, other)
        body = self._recommend().get_json()
        self.assertEqual(self._rec_codes(body), ["TEST-MIX"])
        self.assertEqual(body["recommendations"][0]["effective_mix"]["allow_mix_product"], 1)

    def test_location_override_beats_zone(self):
        zid = self._zone("TEST-Z", allow_mix_product=1)
        loc = self._loc("TEST-MIX", zone_id=zid, allow_mix_product=0)
        other = self._post("/api/products", {"sku": "TEST-OTHER", "name": "别的料"})["product"]["id"]
        self._stock(loc, other)
        body = self._recommend().get_json()
        self.assertEqual(self._rec_codes(body), [])  # 库位显式禁止覆盖储区放开

    # ---------- 采纳推荐后可真正上架 ----------
    def test_adopt_recommendation_then_putaway(self):
        recv = self._loc("TEST-RECV", loc_type="receiving")
        storage = self._loc("TEST-STORE")
        r = self.client.post(f"/api/asns/{self.asn_id}/receive", json={
            "line_id": self.line_id, "location_id": recv, "qty": 5,
        })
        self.assertEqual(r.status_code, 200, r.get_json())
        body = self._recommend(qty=5).get_json()
        self.assertTrue(body["recommendations"])
        target = body["recommendations"][0]["location_id"]
        self.assertIn(target, (storage,))
        r = self.client.post(f"/api/asns/{self.asn_id}/putaway", json={
            "line_id": self.line_id, "from_location_id": recv,
            "to_location_id": target, "qty": 5,
        })
        self.assertEqual(r.status_code, 200, r.get_json())
