"""批次子系统（M19）测试：字段配置 / 批次档案 / 验收入库批次流水 / 内部批次号规则。"""
import os
import sys
import tempfile
import unittest

_TMP = os.path.join(tempfile.gettempdir(), "miniwms_unittest")
os.makedirs(_TMP, exist_ok=True)
os.environ.setdefault("MINIWMS_DB", os.path.join(_TMP, "test.db"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402  (须在环境变量设置之后)

import constants as C  # noqa: E402
from batch_fields import DEFAULT_KEY_FIELDS, seed_batch_config  # noqa: E402

FULL_INFO = {
    "batch_no": "LOT-2026-A", "production_date": "2026-01-10",
    "expiry_date": "2028-01-09", "sterilization_batch_no": "ST-7788",
    "udi": "06901234567892", "origin": "广东深圳",
    "production_enterprise": "深圳医疗器械有限公司",
    "reg_cert_no": "国械注准2026xxxx", "registrant": "某某医疗",
    "mfg_license": "粤食药监械生产许2026xxxx",
    "product_name": "一次性使用输液器", "spec": "0.7mm", "article_no": "ART-1",
    "unit": "支", "brand": "XX", "patient_name": "张三",
    "goods_property": "合格",
}


class BatchApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        import sqlite3
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            for t in ("batch_receipt", "batch", "batch_config",
                      "stock_movement", "stock", "asn_line", "asn",
                      "customer", "supplier", "warehouse",
                      "product", "owner", "location"):
                conn.execute(f"DELETE FROM {t}")
            conn.commit()
        finally:
            conn.close()
        # 每个用例恢复默认批次配置，避免互相污染
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            seed_batch_config(conn)
            conn.commit()
        finally:
            conn.close()
        self.wh_id = self._mk_warehouse("WH-B")
        self.oid = self._mk_owner("OW-B1")
        self.pid = self._mk_product(self.oid, "PB1")
        self.loc = self._mk_location("RCV-B", "receiving")

    # ---------- 夹具 ----------
    def _mk_owner(self, code):
        return self.client.post(
            "/api/owners", json={"code": code, "name": f"货主{code}"}
        ).get_json()["owner"]["id"]

    def _mk_product(self, owner_id, sku="P1", **extra):
        return self.client.post(
            "/api/products", json={"sku": sku, "name": f"物料{sku}",
                                   "owner_id": owner_id, **extra}
        ).get_json()["product"]["id"]

    def _mk_warehouse(self, code):
        return self.client.post(
            "/api/warehouses", json={"code": code, "name": f"仓{code}"}
        ).get_json()["warehouse"]["id"]

    def _mk_location(self, code, loc_type):
        return self.client.post(
            "/api/locations", json={"code": code, "name": code, "loc_type": loc_type}
        ).get_json()["location"]["id"]

    def _mk_asn(self, info=None, doc_type="采购", code="ASN-B1", expected=10):
        line = {"product_id": self.pid, "expected_qty": expected}
        if info:
            line.update(info)
        resp = self.client.post("/api/asns", json={
            "code": code, "owner_id": self.oid, "doc_type": doc_type,
            "source_warehouse": self.wh_id, "lines": [line],
        })
        self.assertEqual(resp.status_code, 201, resp.get_json())
        return resp.get_json()

    def _receive(self, asn_id, line_id, qty=4, **extra):
        resp = self.client.post(f"/api/asns/{asn_id}/receive", json={
            "line_id": line_id, "location_id": self.loc, "qty": qty, **extra,
        })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        return resp.get_json()

    def _receipts(self, **qs):
        q = "&".join(f"{k}={v}" for k, v in qs.items())
        return self.client.get("/api/batch/receipts" + ("?" + q if q else "")).get_json()

    # ---------- ① 批次配置 ----------
    def test_fields_and_config_seeded(self):
        fields = self.client.get("/api/batch/fields").get_json()["fields"]
        self.assertEqual(len(fields), 20)
        cfg = self.client.get("/api/batch/config").get_json()
        self.assertEqual(len(cfg["config"]), 20)
        self.assertEqual(cfg["key_fields"], list(DEFAULT_KEY_FIELDS))
        goods = next(r for r in cfg["config"] if r["field_name"] == "goods_property")
        self.assertEqual(goods["default_value"], "合格")
        self.assertEqual(goods["is_key"], 0)

    def test_update_config_key_and_default(self):
        r = self.client.put("/api/batch/config/udi", json={
            "is_key": 0, "default_value": "默认UDI", "label": "UDI 码", "note": "由数据传入",
        })
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["config"]["is_key"], 0)
        self.assertNotIn("udi", r.get_json()["key_fields"])
        cfg = self.client.get("/api/batch/config").get_json()
        row = next(x for x in cfg["config"] if x["field_name"] == "udi")
        self.assertEqual((row["label"], row["default_value"], row["note"]),
                         ("UDI 码", "默认UDI", "由数据传入"))

    def test_config_rejects_invalid_and_unknown(self):
        self.assertEqual(
            self.client.put("/api/batch/config/udi", json={"is_key": "x"}).status_code, 400)
        self.assertEqual(
            self.client.put("/api/batch/config/not_a_field", json={"label": "x"}).status_code, 404)
        self.assertEqual(
            self.client.put("/api/batch/config/udi", json={"label": "x" * 100}).status_code, 400)
        self.assertEqual(
            self.client.put("/api/batch/config/udi", json={}).status_code, 400)

    def test_cannot_remove_last_key_field(self):
        keys = list(DEFAULT_KEY_FIELDS)
        for name in keys[:-1]:
            self.assertEqual(
                self.client.put(f"/api/batch/config/{name}", json={"is_key": 0}).status_code, 200)
        r = self.client.put(f"/api/batch/config/{keys[-1]}", json={"is_key": 0})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.get("/api/batch/config").get_json()["key_fields"],
                         [keys[-1]])

    # ---------- ② 验收入库写入批次信息 ----------
    def test_receive_writes_batch_profile_and_receipt(self):
        body = self._mk_asn(FULL_INFO)
        r = self._receive(body["asn"]["id"], body["lines"][0]["id"], qty=4)
        b = r["batch"]
        self.assertTrue(b["batch_no"].startswith("BAT-"))
        self.assertTrue(b["batch_code"].startswith("BP-"))
        self.assertEqual(b["internal_batch_no"], b["batch_no"])
        self.assertEqual(b["internal_source"], C.INTERNAL_BATCH_FIRST_IN)
        self.assertEqual(b["info"]["udi"], FULL_INFO["udi"])
        self.assertEqual(b["info"]["sterilization_batch_no"], "ST-7788")
        self.assertEqual(b["info"]["patient_name"], "张三")
        self.assertEqual(b["info"]["production_enterprise"], "深圳医疗器械有限公司")
        self.assertEqual(b["info"]["goods_property"], "合格")

        receipts = self._receipts()["receipts"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["qty"], 4)
        self.assertEqual(receipts[0]["batch_code"], b["batch_code"])
        self.assertEqual(receipts[0]["asn_code"], body["asn"]["code"])
        profiles = self.client.get("/api/batch/profiles").get_json()["profiles"]
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["receipt_count"], 1)
        self.assertEqual(profiles[0]["key_info"]["batch_no"], FULL_INFO["batch_no"])

    def test_each_receipt_unique_batch_no_same_key_info(self):
        body = self._mk_asn(FULL_INFO)
        a = self._receive(body["asn"]["id"], body["lines"][0]["id"], 4)
        b = self._receive(body["asn"]["id"], body["lines"][0]["id"], 4)
        self.assertNotEqual(a["batch"]["batch_no"], b["batch"]["batch_no"])
        self.assertEqual(a["batch"]["batch_id"], b["batch"]["batch_id"],
                         "关键批次信息一致应归入同一批次档案")
        self.assertEqual(self._receipts()["total"], 2)
        self.assertEqual(
            self.client.get("/api/batch/profiles").get_json()["profiles"][0]["receipt_count"], 2)

    def test_different_key_info_creates_new_profile(self):
        a = self._mk_asn(FULL_INFO, code="ASN-B1")
        other = dict(FULL_INFO, batch_no="LOT-2026-B")
        b = self._mk_asn(other, code="ASN-B2")
        ra = self._receive(a["asn"]["id"], a["lines"][0]["id"], 4)
        rb = self._receive(b["asn"]["id"], b["lines"][0]["id"], 4)
        self.assertNotEqual(ra["batch"]["batch_id"], rb["batch"]["batch_id"])
        self.assertEqual(self.client.get("/api/batch/profiles").get_json()["total"], 2)

    def test_key_fields_config_controls_consistency(self):
        a = self._mk_asn(FULL_INFO, code="ASN-B1")
        self._receive(a["asn"]["id"], a["lines"][0]["id"], 4)
        b = self._mk_asn(dict(FULL_INFO, udi="ANOTHER-UDI"), code="ASN-B2")
        self._receive(b["asn"]["id"], b["lines"][0]["id"], 4)
        self.assertEqual(self.client.get("/api/batch/profiles").get_json()["total"], 2)

        # 把 UDI 从关键批次信息里摘掉后，仅 UDI 不同的两批应视为关键批次信息一致
        self.client.put("/api/batch/config/udi", json={"is_key": 0})
        c = self._mk_asn(dict(FULL_INFO, udi="UDI-C"), code="ASN-B3")
        d = self._mk_asn(dict(FULL_INFO, udi="UDI-D"), code="ASN-B4")
        rc = self._receive(c["asn"]["id"], c["lines"][0]["id"], 4)
        rd = self._receive(d["asn"]["id"], d["lines"][0]["id"], 4)
        self.assertEqual(rc["batch"]["batch_id"], rd["batch"]["batch_id"])
        self.assertEqual(self.client.get("/api/batch/profiles").get_json()["total"], 3)

    def test_config_default_fills_empty_fields(self):
        self.client.put("/api/batch/config/origin", json={"default_value": "配置产地"})
        info = {k: v for k, v in FULL_INFO.items()
                if k not in ("origin", "goods_property")}
        body = self._mk_asn(info)
        b = self._receive(body["asn"]["id"], body["lines"][0]["id"], 4)["batch"]
        self.assertEqual(b["info"]["origin"], "配置产地")
        self.assertEqual(b["info"]["goods_property"], "合格")

    # ---------- ③ 内部批次号规则 ----------
    def test_internal_batch_no_from_line_data(self):
        body = self._mk_asn(dict(FULL_INFO, internal_batch_no="IB-OUT-9"))
        b = self._receive(body["asn"]["id"], body["lines"][0]["id"], 4)["batch"]
        self.assertEqual(b["internal_batch_no"], "IB-OUT-9")
        self.assertEqual(b["internal_source"], C.INTERNAL_BATCH_EXTERNAL)

    def test_receive_payload_internal_batch_no_wins(self):
        body = self._mk_asn(dict(FULL_INFO, internal_batch_no="IB-FROM-LINE"))
        b = self._receive(body["asn"]["id"], body["lines"][0]["id"], 4,
                          internal_batch_no="IB-FROM-API")["batch"]
        self.assertEqual(b["internal_batch_no"], "IB-FROM-API")
        self.assertEqual(b["internal_source"], C.INTERNAL_BATCH_EXTERNAL)

    def test_return_receipt_reuses_internal_batch_no(self):
        first = self._mk_asn(dict(FULL_INFO, internal_batch_no="IB-OUT-1"), code="ASN-B1")
        self._receive(first["asn"]["id"], first["lines"][0]["id"], 4)
        ret = self._mk_asn(FULL_INFO, doc_type="销退", code="ASN-B2")
        b = self._receive(ret["asn"]["id"], ret["lines"][0]["id"], 2)["batch"]
        self.assertEqual(b["internal_batch_no"], "IB-OUT-1",
                         "退货应沿用出库的内部批次号")
        self.assertEqual(b["internal_source"], C.INTERNAL_BATCH_RETURN)
        self.assertEqual(self.client.get("/api/batch/profiles").get_json()["total"], 1)

    # ---------- ④ 手动预配置批次档案 ----------
    def test_manual_profile_reused_on_receive(self):
        r = self.client.post("/api/batch/profiles", json={
            "owner_id": self.oid, "product_id": self.pid, "info": FULL_INFO,
        })
        self.assertEqual(r.status_code, 201, r.get_json())
        profile_id = r.get_json()["profile"]["id"]
        body = self._mk_asn(FULL_INFO)
        b = self._receive(body["asn"]["id"], body["lines"][0]["id"], 4)["batch"]
        self.assertEqual(b["batch_id"], profile_id)
        self.assertEqual(self.client.get("/api/batch/profiles").get_json()["total"], 1)

    def test_manual_profile_duplicate_and_owner_isolation(self):
        payload = {"owner_id": self.oid, "product_id": self.pid, "info": FULL_INFO}
        self.assertEqual(self.client.post("/api/batch/profiles", json=payload).status_code, 201)
        self.assertEqual(self.client.post("/api/batch/profiles", json=payload).status_code, 409)
        oid2 = self._mk_owner("OW-B2")
        pid2 = self._mk_product(oid2, "PB2")
        r = self.client.post("/api/batch/profiles", json={
            "owner_id": self.oid, "product_id": pid2, "info": FULL_INFO,
        })
        self.assertEqual(r.status_code, 400)

    def test_invalid_goods_property_rejected(self):
        resp = self.client.post("/api/asns", json={
            "code": "ASN-BAD", "owner_id": self.oid, "source_warehouse": self.wh_id,
            "lines": [{"product_id": self.pid, "expected_qty": 1,
                       "goods_property": "未知"}],
        })
        self.assertEqual(resp.status_code, 400)

    # ---------- ⑤ 查询与联动 ----------
    def test_receipt_and_profile_filters(self):
        a = self._mk_asn(FULL_INFO, code="ASN-B1")
        self._receive(a["asn"]["id"], a["lines"][0]["id"], 4)
        b = self._mk_asn(dict(FULL_INFO, batch_no="LOT-B", goods_property="不合格"),
                         code="ASN-B2")
        self._receive(b["asn"]["id"], b["lines"][0]["id"], 2)
        self.assertEqual(self._receipts(goods_property="不合格")["total"], 1)
        self.assertEqual(self._receipts(asn_id=a["asn"]["id"])["total"], 1)
        self.assertEqual(self._receipts(asn_line_id=b["lines"][0]["id"])["total"], 1)
        self.assertEqual(self._receipts(keyword="BAT-")["total"], 2)
        self.assertEqual(self.client.get(
            f"/api/batch/profiles?product_id={self.pid}").get_json()["total"], 2)

    def test_profile_detail_and_asn_detail_include_batches(self):
        body = self._mk_asn(FULL_INFO)
        b = self._receive(body["asn"]["id"], body["lines"][0]["id"], 4)["batch"]
        pd = self.client.get(f"/api/batch/profiles/{b['batch_id']}").get_json()
        self.assertEqual(pd["profile"]["key_info"]["udi"], FULL_INFO["udi"])
        self.assertEqual(len(pd["receipts"]), 1)
        self.assertEqual(pd["receipts"][0]["batch_no"], b["batch_no"])
        rd = self.client.get(f"/api/batch/receipts/{b['receipt_id']}").get_json()
        self.assertEqual(rd["receipt"]["internal_batch_no"], b["batch_no"])
        self.assertEqual(self.client.get("/api/batch/receipts/99999").status_code, 404)

        detail = self.client.get(f"/api/asns/{body['asn']['id']}").get_json()
        self.assertEqual(len(detail["lines"][0]["batch_receipts"]), 1)
        self.assertEqual(detail["lines"][0]["batch_receipts"][0]["batch_no"], b["batch_no"])

    def test_reconcile_still_consistent_after_batch_receipt(self):
        body = self._mk_asn(FULL_INFO)
        self._receive(body["asn"]["id"], body["lines"][0]["id"], 4)
        rec = self.client.get("/api/stock/reconcile").get_json()
        self.assertTrue(rec["consistent"], rec)


if __name__ == "__main__":
    unittest.main()
