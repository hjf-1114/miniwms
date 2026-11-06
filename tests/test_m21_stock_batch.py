"""M21 批次级库存明细层测试。

验收口径（docs/PUTAWAY_RECOMMENDATION.md §5 / 阶段 B）：
- ``stock`` 汇总层列与唯一键永不修改，I1 保持
- I2 ``stock_batch.qty == SUM(stock_movement.qty_delta)``（按批次维度）
- I3 ``stock.qty == SUM(stock_batch.qty)``（按三元组）
- 一律硬校验，**无灰度豁免**；五个库存动作都要同步明细层
- 老库 ``init_db()`` 一次性回填占位明细，回填后立即通过 I2/I3
"""
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
import inventory_service as IS  # noqa: E402
from auth import make_token  # noqa: E402

LEGACY = IS.LEGACY_BATCH_NO
_CLEAN_ORDER = (
    "batch_location", "stock_batch", "stock_movement", "stock",
    "dn_pick", "dn_line", "dn", "stocktake_line", "stocktake",
    "transfer_line", "transfer", "asn_line", "asn",
    "batch_receipt", "batch", "user_owner_grant", "user_warehouse_grant",
    "owner_warehouse", "customer", "supplier", "product", "owner",
    "location", "warehouse",
)


class M21StockBatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()
        cls.db_path = os.environ["MINIWMS_DB"]

    def _db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _clean(self):
        conn = self._db()
        try:
            for t in _CLEAN_ORDER:
                conn.execute(f"DELETE FROM {t}")
            # 只清本测试创建的账号，保留 _seed_auth 预置的 admin
            conn.execute("DELETE FROM user WHERE username LIKE 'M21-%'")
            conn.commit()
        finally:
            conn.close()

    def setUp(self):
        self._clean()
        os.environ["MINIWMS_ALLOW_ANONYMOUS"] = "1"
        self.oid = self._owner("M21-OW")
        self.pid = self._product("M21-P1", self.oid)
        self.wh = self._warehouse("M21-WH")
        self.rcv = self._location("M21-RCV", "receiving")
        self.sto = self._location("M21-STO", "storage")
        self.sto2 = self._location("M21-STO2", "storage")
        self.cus = self._customer("M21-CUS", self.oid)

    def tearDown(self):
        self._clean()
        os.environ["MINIWMS_ALLOW_ANONYMOUS"] = "1"

    # ── 夹具 ──
    def _owner(self, code):
        r = self.client.post("/api/owners", json={"code": code, "name": code})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["owner"]["id"]

    def _product(self, sku, oid):
        r = self.client.post("/api/products",
                             json={"sku": sku, "name": sku, "owner_id": oid})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["product"]["id"]

    def _location(self, code, loc_type):
        r = self.client.post("/api/locations", json={
            "code": code, "name": code, "loc_type": loc_type})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["location"]["id"]

    def _warehouse(self, code):
        r = self.client.post("/api/warehouses", json={"code": code, "name": code})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["warehouse"]["id"]

    def _customer(self, code, oid):
        r = self.client.post("/api/customers", json={
            "code": code, "name": code, "owner_id": oid})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["customer"]["id"]

    def _asn(self, qty=10, code="M21-ASN", **extra):
        r = self.client.post("/api/asns", json={
            "code": code, "owner_id": self.oid, "source_warehouse": self.wh,
            "lines": [{"product_id": self.pid, "expected_qty": qty, **extra}],
        })
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        b = r.get_json()
        return b["asn"]["id"], b["lines"][0]["id"]

    def _dn(self, qty=4, code="M21-DN"):
        r = self.client.post("/api/dns", json={
            "code": code, "owner_id": self.oid, "customer_id": self.cus,
            "lines": [{"product_id": self.pid, "ordered_qty": qty}],
        })
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        b = r.get_json()
        return b["dn"]["id"], b["lines"][0]["id"]

    def _receive(self, asn_id, line_id, qty=10, loc=None, **extra):
        r = self.client.post(f"/api/asns/{asn_id}/receive", json={
            "line_id": line_id, "location_id": loc or self.rcv, "qty": qty, **extra})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    def _putaway(self, asn_id, line_id, qty=10, to_loc=None):
        r = self.client.post(f"/api/asns/{asn_id}/putaway", json={
            "line_id": line_id, "from_location_id": self.rcv,
            "to_location_id": to_loc or self.sto, "qty": qty})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    def _rows(self, sql, *args):
        conn = self._db()
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    def _reconcile(self, path):
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    def _all_ok(self):
        self.assertTrue(self._reconcile("/api/stock/reconcile")["consistent"], "I1 不成立")
        data = self._reconcile("/api/stock/batch-reconcile")
        self.assertTrue(data["consistent"], data["mismatches"])
        return data

    # ── A. DDL 与结构（3） ──
    def test_01_ddl_tables_and_unique_index(self):
        conn = self._db()
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("stock_batch", tables)
            self.assertIn("batch_location", tables)
            idx = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'")}
            self.assertIn("uq_stock_batch", idx)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(stock_batch)")}
            self.assertTrue({"designated_customer_id", "allocated_qty",
                             "expiry_date", "goods_property", "stock_status"}
                            .issubset(cols))
        finally:
            conn.close()

    def test_02_movement_has_nine_dimension_columns(self):
        conn = self._db()
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(stock_movement)")}
            self.assertTrue({
                "batch_no", "batch_key", "internal_batch_no", "designated_customer_id",
                "goods_property", "stock_status", "expiry_date",
                "allocated_delta", "operator"}.issubset(cols))
        finally:
            conn.close()

    def test_03_stock_summary_layer_unchanged(self):
        conn = self._db()
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(stock)")}
            self.assertEqual({"id", "owner_id", "product_id", "location_id",
                              "qty", "allocated_qty", "updated_at"}, cols)
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='stock'"
            ).fetchone()[0]
            self.assertIn("UNIQUE(owner_id, product_id, location_id)", sql.replace("\n", " "))
        finally:
            conn.close()

    # ── B. 老库回填（3） ──
    def test_04_backfill_legacy_movement_creates_placeholder(self):
        aid, lid = self._asn()
        self._receive(aid, lid, 10)
        conn = self._db()
        try:
            conn.execute("DELETE FROM stock_batch")
            conn.execute("UPDATE stock_movement SET batch_no = ''")
            conn.commit()
        finally:
            conn.close()
        main.init_db()
        rows = self._rows("SELECT batch_no, qty FROM stock_batch")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["batch_no"], LEGACY)
        self.assertEqual(rows[0]["qty"], 10)
        self._all_ok()

    def test_05_backfill_is_idempotent(self):
        aid, lid = self._asn()
        self._receive(aid, lid, 10)
        main.init_db()
        main.init_db()
        rows = self._rows("SELECT batch_no, qty FROM stock_batch")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["qty"], 10)
        self._all_ok()

    def test_06_backfill_zero_qty_stock_row_has_detail(self):
        conn = self._db()
        try:
            conn.execute(
                "INSERT INTO stock (owner_id, product_id, location_id, qty, allocated_qty) "
                "VALUES (?, ?, ?, 0, 0)", (self.oid, self.pid, self.sto))
            conn.commit()
        finally:
            conn.close()
        conn = self._db()
        try:
            conn.execute("DELETE FROM stock_batch")
            conn.commit()
        finally:
            conn.close()
        main.init_db()
        rows = self._rows("SELECT qty FROM stock_batch WHERE location_id = ?", self.sto)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["qty"], 0)
        self._all_ok()

    # ── C. 五个库存动作（8） ──
    def test_07_receive_writes_real_batch_dimensions(self):
        aid, lid = self._asn()
        b = self._receive(aid, lid, 10)["batch"]
        rows = self._rows("SELECT * FROM stock_batch WHERE location_id = ?", self.rcv)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["batch_no"], b["batch_no"])
        self.assertEqual(r["batch_key"], b["batch_key"])
        self.assertEqual(r["internal_batch_no"], b["internal_batch_no"])
        self.assertEqual(r["qty"], 10)
        self.assertEqual(r["goods_property"], "合格")
        self.assertNotEqual(r["batch_no"], LEGACY)
        self._all_ok()

    def test_08_two_receipts_create_two_detail_rows(self):
        aid, lid = self._asn(qty=10)
        self._receive(aid, lid, 4)
        self._receive(aid, lid, 6)
        rows = self._rows("SELECT batch_no, qty FROM stock_batch ORDER BY id")
        self.assertEqual([r["qty"] for r in rows], [4, 6])
        self.assertNotEqual(rows[0]["batch_no"], rows[1]["batch_no"])
        self._all_ok()

    def test_09_putaway_moves_batch_dimension_and_logs_location(self):
        aid, lid = self._asn()
        b = self._receive(aid, lid, 10)["batch"]
        self._putaway(aid, lid, 10)
        self.assertEqual(self._rows("SELECT qty FROM stock_batch WHERE location_id = ?",
                                    self.rcv)[0]["qty"], 0)  # 零行保留
        dst = self._rows("SELECT batch_no, qty FROM stock_batch WHERE location_id = ?",
                         self.sto)
        self.assertEqual(dst[0]["batch_no"], b["batch_no"])
        self.assertEqual(dst[0]["qty"], 10)
        logs = self._rows("SELECT batch_no, qty FROM batch_location")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["batch_no"], b["batch_no"])
        self._all_ok()

    def test_10_pick_allocates_at_detail_layer(self):
        aid, lid = self._asn()
        self._receive(aid, lid, 10)
        self._putaway(aid, lid, 10)
        did, dlid = self._dn(4)
        r = self.client.post(f"/api/dns/{did}/pick", json={
            "line_id": dlid, "location_id": self.sto, "qty": 4})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        rows = self._rows("SELECT SUM(allocated_qty) AS a, SUM(qty) AS q "
                          "FROM stock_batch WHERE location_id = ?", self.sto)
        self.assertEqual(rows[0]["a"], 4)
        self.assertEqual(rows[0]["q"], 10)
        self._all_ok()

    def test_11_ship_releases_qty_and_allocation_together(self):
        aid, lid = self._asn()
        self._receive(aid, lid, 10)
        self._putaway(aid, lid, 10)
        did, dlid = self._dn(4)
        self.client.post(f"/api/dns/{did}/pick", json={
            "line_id": dlid, "location_id": self.sto, "qty": 4})
        r = self.client.post(f"/api/dns/{did}/ship", json={})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        rows = self._rows("SELECT SUM(qty) AS q, SUM(allocated_qty) AS a "
                          "FROM stock_batch WHERE location_id = ?", self.sto)
        self.assertEqual(rows[0]["q"], 6)
        self.assertEqual(rows[0]["a"], 0)
        self._all_ok()

    def test_12_transfer_keeps_batch_dimension(self):
        aid, lid = self._asn()
        b = self._receive(aid, lid, 10)["batch"]
        self._putaway(aid, lid, 10)
        r = self.client.post("/api/transfers", json={
            "code": "M21-TR", "owner_id": self.oid,
            "lines": [{"product_id": self.pid, "from_location_id": self.sto,
                       "to_location_id": self.sto2, "qty": 3}],
        })
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        tid = r.get_json()["transfer"]["id"]
        mv = self.client.post(f"/api/transfers/{tid}/move", json={})
        self.assertEqual(mv.status_code, 200, mv.get_data(as_text=True))
        for loc, qty in ((self.sto, 7), (self.sto2, 3)):
            rows = self._rows("SELECT batch_no, qty FROM stock_batch WHERE location_id = ?", loc)
            self.assertEqual(rows[0]["batch_no"], b["batch_no"])
            self.assertEqual(rows[0]["qty"], qty)
        self._all_ok()

    def test_13_stocktake_gain_uses_placeholder_detail(self):
        aid, lid = self._asn()
        self._receive(aid, lid, 10)
        self._putaway(aid, lid, 10)
        r = self.client.post("/api/stocktakes", json={
            "code": "M21-ST-GAIN", "owner_id": self.oid})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        sid = r.get_json()["stocktake"]["id"]
        frozen = self.client.post(f"/api/stocktakes/{sid}/freeze", json={}).get_json()
        line_id = frozen["lines"][0]["id"]
        self.client.post(f"/api/stocktakes/{sid}/count", json={
            "lines": [{"line_id": line_id, "counted_qty": 12}]})
        adj = self.client.post(f"/api/stocktakes/{sid}/adjust", json={})
        self.assertEqual(adj.status_code, 200, adj.get_data(as_text=True))
        rows = self._rows("SELECT batch_no, qty FROM stock_batch WHERE location_id = ?",
                          self.sto)
        self.assertEqual(sum(x["qty"] for x in rows), 12)
        self.assertIn(LEGACY, [x["batch_no"] for x in rows])
        self._all_ok()

    def test_14_stocktake_loss_spreads_by_fefo(self):
        aid, lid = self._asn()
        self._receive(aid, lid, 10)
        self._putaway(aid, lid, 10)
        r = self.client.post("/api/stocktakes", json={
            "code": "M21-ST-LOSS", "owner_id": self.oid})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        sid = r.get_json()["stocktake"]["id"]
        frozen = self.client.post(f"/api/stocktakes/{sid}/freeze", json={}).get_json()
        line_id = frozen["lines"][0]["id"]
        self.client.post(f"/api/stocktakes/{sid}/count", json={
            "lines": [{"line_id": line_id, "counted_qty": 7}]})
        adj = self.client.post(f"/api/stocktakes/{sid}/adjust", json={})
        self.assertEqual(adj.status_code, 200, adj.get_data(as_text=True))
        rows = self._rows("SELECT SUM(qty) AS q FROM stock_batch WHERE location_id = ?",
                          self.sto)
        self.assertEqual(rows[0]["q"], 7)
        self._all_ok()

    # ── D. 不变式（4） ──
    def test_15_full_flow_satisfies_i1_i2_i3(self):
        aid, lid = self._asn()
        self._receive(aid, lid, 10)
        self._putaway(aid, lid, 10)
        did, dlid = self._dn(4)
        self.client.post(f"/api/dns/{did}/pick", json={
            "line_id": dlid, "location_id": self.sto, "qty": 4})
        self.client.post(f"/api/dns/{did}/ship", json={})
        data = self._all_ok()
        self.assertGreater(data["i2"]["checked"], 0)
        self.assertGreater(data["i3"]["checked"], 0)
        self.assertEqual(data["tolerance"], 1e-9)

    def test_16_tampering_detail_is_detected_as_i3(self):
        aid, lid = self._asn()
        self._receive(aid, lid, 10)
        conn = self._db()
        try:
            conn.execute("UPDATE stock_batch SET qty = qty + 1")
            conn.commit()
        finally:
            conn.close()
        data = self._reconcile("/api/stock/batch-reconcile")
        self.assertFalse(data["consistent"])
        self.assertTrue(data["i3"]["mismatches"])
        self.assertEqual(data["i3"]["mismatches"][0]["invariant"], "I3")

    def test_17_tampering_movement_is_detected_as_i2(self):
        aid, lid = self._asn()
        self._receive(aid, lid, 10)
        conn = self._db()
        try:
            conn.execute("UPDATE stock_movement SET qty_delta = qty_delta + 1")
            conn.commit()
        finally:
            conn.close()
        data = self._reconcile("/api/stock/batch-reconcile")
        self.assertFalse(data["consistent"])
        self.assertTrue(data["i2"]["mismatches"])
        self.assertEqual(data["i2"]["mismatches"][0]["invariant"], "I2")

    def test_18_zero_qty_detail_row_is_kept(self):
        aid, lid = self._asn()
        self._receive(aid, lid, 10)
        self._putaway(aid, lid, 10)
        rows = self._rows("SELECT id, qty FROM stock_batch WHERE location_id = ?", self.rcv)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["qty"], 0)
        self._all_ok()

    # ── E. API 与服务层（4） ──
    def test_19_stock_batch_api_filters(self):
        aid, lid = self._asn()
        b = self._receive(aid, lid, 10)["batch"]
        data = self.client.get("/api/stock/batch?batch_no=" + b["batch_no"]).get_json()
        self.assertEqual(len(data["stock_batches"]), 1)
        self.assertEqual(data["stock_batches"][0]["batch_no"], b["batch_no"])
        self.assertEqual(data["stock_batches"][0]["available_qty"], 10)
        none = self.client.get("/api/stock/batch?batch_no=NO-SUCH").get_json()
        self.assertEqual(none["total"], 0)
        has = self.client.get("/api/stock/batch?has_stock=1").get_json()
        self.assertEqual(has["total"], 1)

    def test_20_stock_batch_api_respects_owner_scope(self):
        aid, lid = self._asn()
        self._receive(aid, lid, 10)
        other = self._owner("M21-OW-B")
        conn = self._db()
        try:
            rid = conn.execute("SELECT id FROM role WHERE name='只读'").fetchone()[0]
        finally:
            conn.close()
        admin_tok = self.client.post("/api/auth/login", json={
            "username": "admin", "password": "admin123"}).get_json()["token"]
        r = self.client.post("/api/users", json={
            "username": "M21-U", "password": "pwd12345", "role_id": rid,
        }, headers={"Authorization": "Bearer " + admin_tok})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        uid = r.get_json()["user"]["id"]
        tok = make_token(uid)
        self.client.put(f"/api/users/{uid}/grants", json={
            "warehouse_scope": "all", "owner_scope": "grant",
            "warehouse_ids": [], "owner_ids": [other],
        }, headers={"Authorization": "Bearer " + admin_tok})
        data = self.client.get("/api/stock/batch?page_size=100",
                               headers={"Authorization": "Bearer " + tok}).get_json()
        self.assertEqual(data["total"], 0)

    def test_21_batch_sum_mismatch_raises_stock_error(self):
        conn = self._db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaises(IS.StockError):
                IS.adjust_stock(conn, self.oid, self.pid, self.sto, 10, "TEST", 1,
                                batches=[{"batch_no": "B1", "qty": 5}])
            conn.rollback()
        finally:
            conn.close()

    def test_22_spread_batches_follows_fefo(self):
        conn = self._db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for no, exp, qty in (("B-LATE", "2027-12-31", 4), ("B-EARLY", "2026-01-01", 6)):
                IS.upsert_stock_batch(conn, self.oid, self.pid, self.sto,
                                      {"batch_no": no, "expiry_date": exp}, qty)
            items = IS.spread_batches(conn, self.oid, self.pid, self.sto, 8)
            self.assertEqual([i["batch_no"] for i in items], ["B-EARLY", "B-LATE"])
            self.assertEqual([i["qty"] for i in items], [6, 2])
            conn.rollback()
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
