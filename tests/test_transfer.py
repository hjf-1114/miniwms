"""移库单（Stock Transfer）接口测试：创建/校验/执行/取消 + 库存原子转移。"""
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


class TransferApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        import sqlite3
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            # 先子后主
            conn.execute("DELETE FROM transfer_line")
            conn.execute("DELETE FROM transfer")
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
        """走完整 ASN 链路入库：收货位收货 → 上架到 to_loc_id（storage/picking）。"""
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

    def _mk_transfer(self, owner_id, product_id, from_loc, to_loc, qty, code="TF-001"):
        resp = self.client.post("/api/transfers", json={
            "code": code, "owner_id": owner_id,
            "lines": [{"product_id": product_id, "from_location_id": from_loc,
                       "to_location_id": to_loc, "qty": qty}],
        })
        self.assertEqual(resp.status_code, 201, resp.get_json())
        return resp.get_json()["transfer"]["id"]

    def _stock_qty(self, owner_id, product_id, location_id):
        rows = self.client.get(
            f"/api/stock?owner_id={owner_id}&product_id={product_id}"
            f"&location_id={location_id}"
        ).get_json()["stocks"]
        return rows[0]["qty"] if rows else 0.0

    # ---------- 创建校验 ----------
    def test_create_ok(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage")
        fb = self._mk_location("B1", "storage")
        tid = self._mk_transfer(oid, pid, fa, fb, 5)
        tf = self.client.get(f"/api/transfers/{tid}").get_json()["transfer"]
        self.assertEqual(tf["status"], C.TRANSFER_OPEN)

    def test_code_required_and_too_long(self):
        oid = self._mk_owner()
        base = {"owner_id": oid, "lines": [{"product_id": 1, "from_location_id": 1,
                                            "to_location_id": 2, "qty": 1}]}
        self.assertEqual(self.client.post("/api/transfers", json={**base, "code": ""}).status_code, 400)
        self.assertEqual(self.client.post("/api/transfers", json={**base, "code": "X" * 33}).status_code, 400)

    def test_owner_required_and_invalid(self):
        base = {"code": "TF-1", "lines": [{"product_id": 1, "from_location_id": 1,
                                           "to_location_id": 2, "qty": 1}]}
        self.assertEqual(self.client.post("/api/transfers", json={**base, "owner_id": None}).status_code, 400)
        r = self.client.post("/api/transfers", json={**base, "owner_id": 99999})
        self.assertEqual(r.status_code, 400)

    def test_lines_required(self):
        oid = self._mk_owner()
        r = self.client.post("/api/transfers", json={"code": "TF-1", "owner_id": oid, "lines": []})
        self.assertEqual(r.status_code, 400)
        r2 = self.client.post("/api/transfers", json={"code": "TF-1", "owner_id": oid})
        self.assertEqual(r2.status_code, 400)

    def test_duplicate_code_409(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage")
        fb = self._mk_location("B1", "storage")
        self._mk_transfer(oid, pid, fa, fb, 1)
        r = self.client.post("/api/transfers", json={
            "code": "TF-001", "owner_id": oid,
            "lines": [{"product_id": pid, "from_location_id": fa, "to_location_id": fb, "qty": 1}]})
        self.assertEqual(r.status_code, 409)

    def test_product_owner_mismatch_400(self):
        o1 = self._mk_owner("O1")
        o2 = self._mk_owner("O2")
        pid = self._mk_product(o1)
        fa = self._mk_location("A1", "storage")
        fb = self._mk_location("B1", "storage")
        r = self.client.post("/api/transfers", json={
            "code": "TF-1", "owner_id": o2,
            "lines": [{"product_id": pid, "from_location_id": fa, "to_location_id": fb, "qty": 1}]})
        self.assertEqual(r.status_code, 400)

    def test_from_eq_to_400(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage")
        r = self.client.post("/api/transfers", json={
            "code": "TF-1", "owner_id": oid,
            "lines": [{"product_id": pid, "from_location_id": fa, "to_location_id": fa, "qty": 1}]})
        self.assertEqual(r.status_code, 400)

    def test_inactive_source_location_400(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage", status="inactive")
        fb = self._mk_location("B1", "storage")
        r = self.client.post("/api/transfers", json={
            "code": "TF-1", "owner_id": oid,
            "lines": [{"product_id": pid, "from_location_id": fa, "to_location_id": fb, "qty": 1}]})
        self.assertEqual(r.status_code, 400)

    # ---------- 执行移库 ----------
    def test_move_atomic_transfer(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage")
        fb = self._mk_location("B1", "storage")
        self._inbound(oid, pid, fa, 10)
        tid = self._mk_transfer(oid, pid, fa, fb, 4)
        r = self.client.post(f"/api/transfers/{tid}/move")
        self.assertTrue(r.get_json()["ok"], r.get_json())
        self.assertEqual(r.get_json()["status"], C.TRANSFER_DONE)
        self.assertEqual(self._stock_qty(oid, pid, fa), 6.0)
        self.assertEqual(self._stock_qty(oid, pid, fb), 4.0)

    def test_move_writes_two_movements(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage")
        fb = self._mk_location("B1", "storage")
        self._inbound(oid, pid, fa, 10)
        tid = self._mk_transfer(oid, pid, fa, fb, 4)
        self.client.post(f"/api/transfers/{tid}/move")
        mv = self.client.get(f"/api/movements?ref_id={tid}").get_json()["movements"]
        self.assertEqual(len(mv), 2)
        deltas = sorted(m["qty_delta"] for m in mv)
        self.assertEqual(deltas, [-4.0, 4.0])
        types = {m["ref_type"] for m in mv}
        self.assertEqual(types, {C.MOVEMENT_MOVE_OUT, C.MOVEMENT_MOVE_IN})

    def test_move_insufficient_available_400(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage")
        fb = self._mk_location("B1", "storage")
        self._inbound(oid, pid, fa, 10)
        tid = self._mk_transfer(oid, pid, fa, fb, 20)
        r = self.client.post(f"/api/transfers/{tid}/move")
        self.assertEqual(r.status_code, 400)
        # 未发生转移
        self.assertEqual(self._stock_qty(oid, pid, fa), 10.0)
        self.assertEqual(self._stock_qty(oid, pid, fb), 0.0)

    def test_move_already_done_409(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage")
        fb = self._mk_location("B1", "storage")
        self._inbound(oid, pid, fa, 10)
        tid = self._mk_transfer(oid, pid, fa, fb, 4)
        self.client.post(f"/api/transfers/{tid}/move")
        r = self.client.post(f"/api/transfers/{tid}/move")
        self.assertEqual(r.status_code, 409)

    def test_reconcile_consistent_after_move(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage")
        fb = self._mk_location("B1", "storage")
        self._inbound(oid, pid, fa, 10)
        tid = self._mk_transfer(oid, pid, fa, fb, 4)
        self.client.post(f"/api/transfers/{tid}/move")
        rec = self.client.get("/api/stock/reconcile").get_json()
        self.assertTrue(rec["consistent"], rec)
        self.assertGreater(rec["checked"], 0)

    # ---------- 取消 ----------
    def test_cancel_open_then_done_409(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage")
        fb = self._mk_location("B1", "storage")
        self._inbound(oid, pid, fa, 10)
        tid1 = self._mk_transfer(oid, pid, fa, fb, 4, code="TF-001")
        r = self.client.post(f"/api/transfers/{tid1}/cancel")
        self.assertEqual(r.get_json()["status"], C.TRANSFER_CANCELLED)
        # 执行已取消单应失败
        r2 = self.client.post(f"/api/transfers/{tid1}/move")
        self.assertEqual(r2.status_code, 409)
        # 取消已执行单应失败
        tid2 = self._mk_transfer(oid, pid, fa, fb, 2, code="TF-002")
        self.client.post(f"/api/transfers/{tid2}/move")
        r3 = self.client.post(f"/api/transfers/{tid2}/cancel")
        self.assertEqual(r3.status_code, 409)

    def test_list_and_get(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        fa = self._mk_location("A1", "storage")
        fb = self._mk_location("B1", "storage")
        tid = self._mk_transfer(oid, pid, fa, fb, 4)
        lst = self.client.get("/api/transfers").get_json()
        self.assertEqual(lst["total"], 1)
        det = self.client.get(f"/api/transfers/{tid}").get_json()
        self.assertEqual(det["transfer"]["code"], "TF-001")
        self.assertEqual(len(det["lines"]), 1)
        self.assertEqual(det["lines"][0]["from_location_code"], "A1")


if __name__ == "__main__":
    unittest.main()
