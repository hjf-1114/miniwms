"""盘点单（Stocktake）接口测试：创建/冻结快照/盲盘/差异调整/解冻结案 + 账实核对。"""
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


class StocktakeApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        import sqlite3
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            conn.execute("DELETE FROM stocktake_line")
            conn.execute("DELETE FROM stocktake")
            conn.execute("DELETE FROM stock_movement")
            conn.execute("DELETE FROM stock")
            conn.execute("DELETE FROM asn_line")
            conn.execute("DELETE FROM asn")
            conn.execute("DELETE FROM dn_pick")
            conn.execute("DELETE FROM dn_line")
            conn.execute("DELETE FROM dn")
            conn.execute("DELETE FROM customer")
            conn.execute("DELETE FROM product")
            conn.execute("DELETE FROM owner")
            conn.execute("DELETE FROM location")
            conn.commit()
        finally:
            conn.close()

    # ---------- 夹具 ----------
    def _mk_owner(self, code="OW1"):
        return self.client.post(
            "/api/owners", json={"code": code, "name": f"货主{code}"}
        ).get_json()["owner"]["id"]

    def _mk_product(self, owner_id, sku="P1"):
        return self.client.post(
            "/api/products", json={"sku": sku, "name": f"物料{sku}", "owner_id": owner_id}
        ).get_json()["product"]["id"]

    def _mk_location(self, code, loc_type, status="active"):
        return self.client.post(
            "/api/locations",
            json={"code": code, "name": code, "loc_type": loc_type, "status": status},
        ).get_json()["location"]["id"]

    def _inbound(self, owner_id, product_id, to_loc_id, qty):
        recv_loc = self._mk_location(f"RCV-{to_loc_id}-{qty}", "receiving")
        body = self.client.post("/api/asns", json={
            "code": f"ASN-{to_loc_id}-{qty}", "owner_id": owner_id,
            "lines": [{"product_id": product_id, "expected_qty": qty}],
        }).get_json()
        asn_id, line_id = body["asn"]["id"], body["lines"][0]["id"]
        self.client.post(f"/api/asns/{asn_id}/receive", json={
            "line_id": line_id, "location_id": recv_loc, "qty": qty})
        self.client.post(f"/api/asns/{asn_id}/putaway", json={
            "line_id": line_id, "from_location_id": recv_loc,
            "to_location_id": to_loc_id, "qty": qty})

    def _mk_stocktake(self, owner_id, code="ST-001"):
        resp = self.client.post("/api/stocktakes", json={"code": code, "owner_id": owner_id})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        return resp.get_json()["stocktake"]["id"]

    def _stock_qty(self, owner_id, product_id, location_id):
        rows = self.client.get(
            f"/api/stock?owner_id={owner_id}&product_id={product_id}"
            f"&location_id={location_id}"
        ).get_json()["stocks"]
        return rows[0]["qty"] if rows else 0.0

    # ---------- 创建校验 ----------
    def test_create_ok(self):
        oid = self._mk_owner()
        tid = self._mk_stocktake(oid)
        st = self.client.get(f"/api/stocktakes/{tid}").get_json()["stocktake"]
        self.assertEqual(st["status"], C.STOCKTAKE_OPEN)

    def test_code_required_and_too_long(self):
        oid = self._mk_owner()
        base = {"owner_id": oid}
        self.assertEqual(self.client.post("/api/stocktakes", json={**base, "code": ""}).status_code, 400)
        self.assertEqual(self.client.post("/api/stocktakes", json={**base, "code": "X" * 33}).status_code, 400)

    def test_owner_required_and_invalid(self):
        base = {"code": "ST-1"}
        self.assertEqual(self.client.post("/api/stocktakes", json={**base, "owner_id": None}).status_code, 400)
        r = self.client.post("/api/stocktakes", json={**base, "owner_id": 99999})
        self.assertEqual(r.status_code, 400)

    def test_duplicate_code_409(self):
        oid = self._mk_owner()
        self._mk_stocktake(oid)
        r = self.client.post("/api/stocktakes", json={"code": "ST-001", "owner_id": oid})
        self.assertEqual(r.status_code, 409)

    # ---------- 冻结快照 ----------
    def test_freeze_snapshots_book(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage")
        self._inbound(oid, pid, fa, 10)
        tid = self._mk_stocktake(oid)
        r = self.client.post(f"/api/stocktakes/{tid}/freeze").get_json()
        self.assertEqual(r["status"], C.STOCKTAKE_FROZEN)
        self.assertEqual(len(r["lines"]), 1)
        self.assertEqual(r["lines"][0]["book_qty"], 10.0)
        self.assertIsNone(r["lines"][0]["counted_qty"])

    def test_freeze_non_open_409(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage")
        self._inbound(oid, pid, fa, 10)
        tid = self._mk_stocktake(oid)
        self.client.post(f"/api/stocktakes/{tid}/freeze")
        r = self.client.post(f"/api/stocktakes/{tid}/freeze")
        self.assertEqual(r.status_code, 409)

    # ---------- 盲盘 ----------
    def test_count_sets_counted(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage")
        self._inbound(oid, pid, fa, 10)
        tid = self._mk_stocktake(oid)
        fr = self.client.post(f"/api/stocktakes/{tid}/freeze").get_json()
        line_id = fr["lines"][0]["id"]
        r = self.client.post(f"/api/stocktakes/{tid}/count",
                             json={"lines": [{"line_id": line_id, "counted_qty": 7}]}).get_json()
        self.assertEqual(r["status"], C.STOCKTAKE_FROZEN)
        self.assertEqual(r["lines"][0]["counted_qty"], 7.0)
        self.assertEqual(r["lines"][0]["diff"], -3.0)

    def test_count_negative_400(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage")
        self._inbound(oid, pid, fa, 10)
        tid = self._mk_stocktake(oid)
        fr = self.client.post(f"/api/stocktakes/{tid}/freeze").get_json()
        line_id = fr["lines"][0]["id"]
        r = self.client.post(f"/api/stocktakes/{tid}/count",
                             json={"lines": [{"line_id": line_id, "counted_qty": -1}]})
        self.assertEqual(r.status_code, 400)

    def test_count_non_frozen_409(self):
        oid = self._mk_owner()
        tid = self._mk_stocktake(oid)
        r = self.client.post(f"/api/stocktakes/{tid}/count",
                             json={"lines": [{"line_id": 1, "counted_qty": 5}]})
        self.assertEqual(r.status_code, 409)

    # ---------- 差异调整 ----------
    def test_adjust_applies_diff_and_keeps_reconcile(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage")
        self._inbound(oid, pid, fa, 10)
        tid = self._mk_stocktake(oid)
        fr = self.client.post(f"/api/stocktakes/{tid}/freeze").get_json()
        line_id = fr["lines"][0]["id"]
        self.client.post(f"/api/stocktakes/{tid}/count",
                         json={"lines": [{"line_id": line_id, "counted_qty": 7}]})
        r = self.client.post(f"/api/stocktakes/{tid}/adjust").get_json()
        self.assertEqual(r["status"], C.STOCKTAKE_ADJUSTED)
        # 账存被修正为实盘 7
        self.assertEqual(self._stock_qty(oid, pid, fa), 7.0)
        # 产生一条 adjustment 流水
        mv = self.client.get(f"/api/movements?ref_type=adjustment&ref_id={tid}").get_json()["movements"]
        self.assertEqual(len(mv), 1)
        self.assertEqual(mv[0]["qty_delta"], -3.0)
        # 余额仍可由流水核对
        rec = self.client.get("/api/stock/reconcile").get_json()
        self.assertTrue(rec["consistent"], rec)

    def test_adjust_with_uncounted_400(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage")
        self._inbound(oid, pid, fa, 10)
        tid = self._mk_stocktake(oid)
        self.client.post(f"/api/stocktakes/{tid}/freeze")
        # 不复盘直接调整
        r = self.client.post(f"/api/stocktakes/{tid}/adjust")
        self.assertEqual(r.status_code, 400)

    def test_adjust_non_frozen_409(self):
        oid = self._mk_owner()
        tid = self._mk_stocktake(oid)
        r = self.client.post(f"/api/stocktakes/{tid}/adjust")
        self.assertEqual(r.status_code, 409)

    def test_adjust_no_diff_still_adjusted(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage")
        self._inbound(oid, pid, fa, 10)
        tid = self._mk_stocktake(oid)
        fr = self.client.post(f"/api/stocktakes/{tid}/freeze").get_json()
        line_id = fr["lines"][0]["id"]
        self.client.post(f"/api/stocktakes/{tid}/count",
                         json={"lines": [{"line_id": line_id, "counted_qty": 10}]})
        r = self.client.post(f"/api/stocktakes/{tid}/adjust").get_json()
        self.assertEqual(r["status"], C.STOCKTAKE_ADJUSTED)
        self.assertEqual(self._stock_qty(oid, pid, fa), 10.0)

    # ---------- 解冻结案 ----------
    def test_close_flow_and_guards(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage")
        self._inbound(oid, pid, fa, 10)
        tid = self._mk_stocktake(oid)
        fr = self.client.post(f"/api/stocktakes/{tid}/freeze").get_json()
        line_id = fr["lines"][0]["id"]
        self.client.post(f"/api/stocktakes/{tid}/count",
                         json={"lines": [{"line_id": line_id, "counted_qty": 7}]})
        self.client.post(f"/api/stocktakes/{tid}/adjust")
        # 未调整单不能结案
        tid2 = self._mk_stocktake(oid, code="ST-002")
        self.client.post(f"/api/stocktakes/{tid2}/freeze")
        self.assertEqual(self.client.post(f"/api/stocktakes/{tid2}/close").status_code, 409)
        # 已调整单结案
        r = self.client.post(f"/api/stocktakes/{tid}/close").get_json()
        self.assertEqual(r["status"], C.STOCKTAKE_CLOSED)

    def test_list_and_get(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage")
        self._inbound(oid, pid, fa, 10)
        tid = self._mk_stocktake(oid)
        self.client.post(f"/api/stocktakes/{tid}/freeze")
        lst = self.client.get("/api/stocktakes").get_json()
        self.assertEqual(lst["total"], 1)
        det = self.client.get(f"/api/stocktakes/{tid}").get_json()
        self.assertEqual(det["stocktake"]["code"], "ST-001")
        self.assertEqual(len(det["lines"]), 1)


if __name__ == "__main__":
    unittest.main()
