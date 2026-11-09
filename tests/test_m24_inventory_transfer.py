"""M24 库存转移单：同库位内批次维度变更（净零）测试。

用户 2026-11-09 四条 A 口径 + 设计 §7.2 / §7.3 / §7.4 / §14 阶段 D 验收标准：

- 只改批次信息、数量净零；主账 ``stock.qty`` 零 UPDATE，I1 / I2 / I3 保持
- 制单 → 分配库存（双层 allocated 真实占用）→ 审核生效终态；空单可关闭 / 删除
- 已分配单须先 unallocate 才能关闭 / 删除；approved 不可改不可删
- 字段级变更文档 + 冲销三份记录齐全
- 跨库位拒绝、特采布尔标记、数量字段一律 400、权限码只落常量
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
import constants as C  # noqa: E402
import inventory_transfer_service as SVC  # noqa: E402

_CLEAN_ORDER = (
    "inventory_change_log", "inventory_transfer_line", "inventory_transfer",
    "customer_reservation_log", "batch_location", "stock_batch", "stock_movement", "stock",
    "stock_adjustment_line", "stock_adjustment",
    "dn_pick", "dn_line", "dn", "stocktake_line", "stocktake", "stocktake_plan",
    "transfer_line", "transfer", "asn_line", "asn",
    "batch_receipt", "batch", "user_owner_grant", "user_warehouse_grant",
    "owner_warehouse", "customer", "supplier", "product", "owner",
    "location", "warehouse",
)


class M24InventoryTransferTest(unittest.TestCase):
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
            conn.commit()
        finally:
            conn.close()

    def setUp(self):
        self._clean()
        os.environ["MINIWMS_ALLOW_ANONYMOUS"] = "1"
        self.oid = self._owner("T24-OW")
        self.pid = self._product("T24-P1", self.oid)
        self.wh = self._warehouse("T24-WH")
        self.rcv = self._location("T24-RCV", "receiving")
        self.sto = self._location("T24-STO", "storage")
        self.sto2 = self._location("T24-STO2", "storage")
        self.cus1 = self._customer("T24-C1", self.oid)
        self.cus2 = self._customer("T24-C2", self.oid)

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

    def _stock(self, qty=8, code="T24-ASN", designated=None):
        """收货 + 上架到 sto，返回源批次明细行 id。"""
        line = {"product_id": self.pid, "expected_qty": qty}
        if designated:
            line["designated_customer_id"] = designated
        r = self.client.post("/api/asns", json={
            "code": code, "owner_id": self.oid, "source_warehouse": self.wh,
            "lines": [line],
        })
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        aid, lid = r.get_json()["asn"]["id"], r.get_json()["lines"][0]["id"]
        rv = self.client.post(f"/api/asns/{aid}/receive", json={
            "line_id": lid, "location_id": self.rcv, "qty": qty})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        pu = self.client.post(f"/api/asns/{aid}/putaway", json={
            "line_id": lid, "from_location_id": self.rcv,
            "to_location_id": self.sto, "qty": qty})
        self.assertEqual(pu.status_code, 200, pu.get_data(as_text=True))
        row = self._rows(
            "SELECT id FROM stock_batch WHERE owner_id=? AND product_id=? "
            "AND location_id=? ORDER BY id DESC LIMIT 1", self.oid, self.pid, self.sto)
        return row[0]["id"]

    def _it(self, ttype=C.IT_TYPE_CUSTOMER_BIND, reason="客户要求"):
        r = self.client.post("/api/inventory-transfers", json={
            "transfer_type": ttype, "reason_code": reason})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["id"]

    def _alloc(self, tid, sbid, qty=3, **to):
        line = {"stock_batch_id": sbid, "qty": qty}
        line.update(to)
        r = self.client.post(f"/api/inventory-transfers/{tid}/allocate",
                             json={"lines": [line]})
        return r

    def _act(self, tid, action):
        return self.client.post(f"/api/inventory-transfers/{tid}/{action}", json={})

    def _rows(self, sql, *args):
        conn = self._db()
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    def _stock_row(self):
        return self._rows("SELECT * FROM stock WHERE owner_id=? AND product_id=? "
                          "AND location_id=?", self.oid, self.pid, self.sto)[0]

    def _all_ok(self):
        self.assertTrue(self.client.get("/api/stock/reconcile").get_json()["consistent"],
                        "I1 不成立")
        both = self.client.get("/api/stock/batch-reconcile").get_json()
        self.assertTrue(both["consistent"], both["mismatches"])

    # ── A. 表结构与常量（2）──
    def test_01_three_tables_and_indexes(self):
        conn = self._db()
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            idx = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND (name LIKE 'idx_it%' OR name LIKE 'idx_itl%' OR name LIKE 'idx_icl%')")}
        finally:
            conn.close()
        self.assertTrue({"inventory_transfer", "inventory_transfer_line",
                         "inventory_change_log"} <= tables)
        self.assertEqual(len(idx), 5, idx)

    def test_02_constants_and_permission_codes(self):
        self.assertEqual(len(C.IT_TYPES), 7)
        self.assertEqual(len(C.IT_STATUSES), 5)
        self.assertEqual(len(C.IT_PERMISSIONS), 6)
        self.assertIn(C.MOVEMENT_IT_TRANSFER, (C.MOVEMENT_IT_TRANSFER,))
        # A4：权限码只落常量，路由不得挂 require_permission 装饰器
        path = os.path.join(os.path.dirname(main.__file__),
                            "routes_inventory_transfer.py")
        with open(path, encoding="utf-8") as fh:
            self.assertNotIn("@require_permission", fh.read())

    # ── B. 制单与空单分支（4）──
    def test_03_create_is_empty_draft_without_movement(self):
        tid = self._it()
        t = self.client.get(f"/api/inventory-transfers/{tid}").get_json()["transfer"]
        self.assertEqual(t["status"], C.IT_DRAFT)
        self.assertEqual(t["line_count"], 0)
        self.assertEqual(self._rows("SELECT COUNT(*) AS n FROM stock_movement")[0]["n"], 0)

    def test_04_empty_draft_can_close(self):
        tid = self._it()
        r = self._act(tid, "close")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["status"], C.IT_CLOSED)

    def test_05_empty_draft_can_delete(self):
        tid = self._it()
        r = self._act(tid, "delete")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["status"], C.IT_CANCELLED)

    def test_06_invalid_type_or_reason_rejected(self):
        for body in ({"transfer_type": "nope", "reason_code": ""},
                     {"transfer_type": C.IT_TYPE_CUSTOMER_BIND, "reason_code": "乱填"}):
            r = self.client.post("/api/inventory-transfers", json=body)
            self.assertEqual(r.status_code, 400, r.get_data(as_text=True))

    # ── C. 分配（5）──
    def test_07_allocate_locks_two_layers_and_qty_unchanged(self):
        sbid = self._stock()
        before = self._stock_row()
        tid = self._it()
        r = self._alloc(tid, sbid, 3, to_designated_customer_id=self.cus1)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        after = self._stock_row()
        self.assertEqual(after["qty"], before["qty"], "分配不得改主账 qty")
        self.assertEqual(after["allocated_qty"], 3.0)
        sb = self._rows("SELECT * FROM stock_batch WHERE id=?", sbid)[0]
        self.assertEqual(sb["allocated_qty"], 3.0)
        mv = self._rows("SELECT * FROM stock_movement WHERE ref_type=?",
                        C.MOVEMENT_IT_ALLOCATE)
        self.assertEqual(len(mv), 1)
        self.assertEqual(mv[0]["qty_delta"], 0.0)
        self.assertEqual(mv[0]["allocated_delta"], 3.0)

    def test_08_available_qty_really_decreased(self):
        sbid = self._stock()
        tid = self._it()
        r = self._alloc(tid, sbid, 5, to_designated_customer_id=self.cus1)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        row = self._stock_row()
        self.assertEqual(row["qty"] - row["allocated_qty"], 3.0)
        # 出库拣货的超卖拦截读主账 allocated_qty，超过可用量必须被拒
        dn = self.client.post("/api/dns", json={
            "code": "T24-DN", "owner_id": self.oid, "customer_id": self.cus2,
            "lines": [{"product_id": self.pid, "ordered_qty": 4}]})
        did, dlid = dn.get_json()["dn"]["id"], dn.get_json()["lines"][0]["id"]
        r = self.client.post(f"/api/dns/{did}/pick", json={
            "line_id": dlid, "location_id": self.sto, "qty": 4})
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))

    def test_09_identical_target_dimension_rejected(self):
        sbid = self._stock()
        # 目标批号显式填源批号（其余维度不变）＝完全没有变更，应被拒绝
        sb = self._rows("SELECT * FROM stock_batch WHERE id=?", sbid)[0]
        r2 = self._alloc(self._it(), sbid, 2, to_batch_no=sb["batch_no"])
        self.assertEqual(r2.status_code, 400, r2.get_data(as_text=True))
        self.assertIn("完全相同", r2.get_json()["error"])

    def test_10_insufficient_available_qty_rejected(self):
        tid = self._it()
        sbid = self._stock(qty=2)
        r = self._alloc(tid, sbid, 5, to_designated_customer_id=self.cus1)
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        self.assertEqual(self._stock_row()["allocated_qty"], 0.0, "失败整单回滚")

    def test_11_cross_location_and_cross_owner_customer_rejected(self):
        sbid = self._stock()
        r = self._alloc(self._it(), sbid, 2,
                        to_designated_customer_id=self.cus1,
                        to_location_id=self.sto2)
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        self.assertIn("M17", r.get_json()["error"])
        other = self._owner("T24-OW2")
        cus_other = self._customer("T24-CX", other)
        r2 = self._alloc(self._it(), sbid, 2,
                         to_designated_customer_id=cus_other)
        self.assertEqual(r2.status_code, 400, r2.get_data(as_text=True))

    # ── D. 取消分配与关闭 / 删除约束（4）──
    def test_12_unallocate_releases_symmetrically(self):
        sbid = self._stock()
        tid = self._it()
        self._alloc(tid, sbid, 3, to_designated_customer_id=self.cus1)
        r = self._act(tid, "unallocate")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["status"], C.IT_DRAFT)
        self.assertEqual(self._stock_row()["allocated_qty"], 0.0)
        self.assertEqual(self._rows("SELECT * FROM stock_batch WHERE id=?", sbid)[0]
                         ["allocated_qty"], 0.0)
        self.assertEqual(self._rows("SELECT COUNT(*) AS n FROM inventory_transfer_line")[0]["n"], 0)
        self._all_ok()

    def test_13_allocated_cannot_close_or_delete(self):
        sbid = self._stock()
        tid = self._it()
        self._alloc(tid, sbid, 2, to_designated_customer_id=self.cus1)
        for action in ("close", "delete"):
            r = self._act(tid, action)
            self.assertEqual(r.status_code, 400, (action, r.get_data(as_text=True)))
        self.assertEqual(self._stock_row()["allocated_qty"], 2.0)

    def test_14_after_unallocate_close_and_delete_allowed(self):
        sbid = self._stock()
        tid = self._it()
        self._alloc(tid, sbid, 2, to_designated_customer_id=self.cus1)
        self._act(tid, "unallocate")
        self.assertEqual(self._act(tid, "close").status_code, 200)
        self.assertEqual(self._act(tid, "delete").status_code, 200)

    def test_15_cannot_allocate_twice(self):
        sbid = self._stock()
        tid = self._it()
        self._alloc(tid, sbid, 2, to_designated_customer_id=self.cus1)
        r = self._alloc(tid, sbid, 2, to_designated_customer_id=self.cus2)
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))

    # ── E. 审核（5）──
    def test_16_approve_net_zero_and_main_ledger_unchanged(self):
        sbid = self._stock()
        before = self._stock_row()
        tid = self._it()
        self._alloc(tid, sbid, 3, to_designated_customer_id=self.cus1)
        r = self._act(tid, "approve")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["status"], C.IT_APPROVED)
        after = self._stock_row()
        self.assertEqual(after["qty"], before["qty"], "主账 qty 必须不变")
        self.assertEqual(after["allocated_qty"], 0.0, "审核须释放本单占用")
        mv = self._rows("SELECT * FROM stock_movement WHERE ref_type=?",
                        C.MOVEMENT_IT_TRANSFER)
        self.assertEqual(len(mv), 2)
        self.assertEqual(sum(x["qty_delta"] for x in mv), 0.0, "Σ qty_delta 必须为 0")
        self.assertEqual(sum(x["allocated_delta"] for x in mv), -3.0)
        rows = self._rows("SELECT qty, designated_customer_id FROM stock_batch "
                          "WHERE product_id=? AND location_id=?", self.pid, self.sto)
        self.assertEqual(sorted(x["qty"] for x in rows), [3.0, 5.0])

    def test_17_reconcile_i1_i2_i3_after_approve(self):
        sbid = self._stock()
        tid = self._it()
        self._alloc(tid, sbid, 4, to_designated_customer_id=self.cus1)
        self._act(tid, "approve")
        self._all_ok()

    def test_18_change_log_records_old_new(self):
        sbid = self._stock()
        tid = self._it()
        self._alloc(tid, sbid, 3, to_designated_customer_id=self.cus1)
        r = self._act(tid, "approve")
        self.assertEqual(r.get_json()["change_log_count"], 1)
        logs = self._rows("SELECT * FROM inventory_change_log")
        self.assertEqual(logs[0]["field_name"], "designated_customer_id")
        self.assertEqual(logs[0]["new_value"], str(self.cus1))
        self.assertEqual(logs[0]["object_type"], "stock_batch")

    def test_19_approved_is_terminal(self):
        sbid = self._stock()
        tid = self._it()
        self._alloc(tid, sbid, 2, to_designated_customer_id=self.cus1)
        self._act(tid, "approve")
        for action in ("approve", "close", "delete", "unallocate", "allocate"):
            r = self._act(tid, action)
            self.assertEqual(r.status_code, 400, (action, r.get_data(as_text=True)))

    def test_20_draft_cannot_approve(self):
        self._stock()
        tid = self._it()
        r = self._act(tid, "approve")
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))

    # ── F. 质量状态 / 特采（2）──
    def test_21_unqualified_to_qualified_requires_special_approval(self):
        sbid = self._stock()
        t1 = self._it(C.IT_TYPE_QUALITY_CHANGE, "质检判定")
        self._alloc(t1, sbid, 8, to_goods_property="不合格")
        self.assertEqual(self._act(t1, "approve").status_code, 200)
        bad = self._rows("SELECT id FROM stock_batch WHERE goods_property='不合格' "
                         "AND qty > 0")[0]["id"]
        t2 = self._it(C.IT_TYPE_QUALITY_CHANGE, "质检判定")
        r = self._alloc(t2, bad, 8, to_goods_property="合格")
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        self.assertIn("特采", r.get_json()["error"])

    def test_22_special_approval_reason_allows_transition(self):
        sbid = self._stock()
        t1 = self._it(C.IT_TYPE_QUALITY_CHANGE, "质检判定")
        self._alloc(t1, sbid, 8, to_goods_property="不合格")
        self._act(t1, "approve")
        bad = self._rows("SELECT id FROM stock_batch WHERE goods_property='不合格' "
                         "AND qty > 0")[0]["id"]
        t2 = self._it(C.IT_TYPE_QUALITY_CHANGE, "特采")
        self.assertEqual(self._alloc(t2, bad, 8, to_goods_property="合格").status_code, 200)
        self.assertEqual(self._act(t2, "approve").status_code, 200)
        self._all_ok()

    # ── G. 数量字段拦截（1）──
    def test_23_quantity_fields_rejected_with_rollback(self):
        sbid = self._stock()
        r = self.client.post("/api/inventory-transfers", json={
            "transfer_type": C.IT_TYPE_CUSTOMER_BIND, "delta_qty": 5})
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["code"], C.IT_ERROR_QTY_NOT_ZERO)
        tid = self._it()
        r2 = self.client.post(f"/api/inventory-transfers/{tid}/allocate", json={
            "lines": [{"stock_batch_id": sbid, "qty": 2,
                       "to_designated_customer_id": self.cus1, "to_qty": 9}]})
        self.assertEqual(r2.status_code, 400, r2.get_data(as_text=True))
        self.assertEqual(self._stock_row()["allocated_qty"], 0.0, "整单回滚不留占用")

    # ── H. 冲销（2）──
    def test_24_reverse_suggests_inverted_dims(self):
        sbid = self._stock()
        tid = self._it()
        self._alloc(tid, sbid, 3, to_designated_customer_id=self.cus1)
        self._act(tid, "approve")
        r = self._act(tid, "reverse")
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual(body["status"], C.IT_DRAFT)
        s = body["suggested_lines"][0]
        self.assertEqual(s["from_designated_customer_id"], self.cus1)
        self.assertIsNone(s["to_designated_customer_id"])
        self.assertEqual(self._act(tid, "reverse").status_code, 400, "不可重复冲销")

    def test_25_reverse_roundtrip_restores_balance(self):
        sbid = self._stock()
        tid = self._it()
        self._alloc(tid, sbid, 3, to_designated_customer_id=self.cus1)
        self._act(tid, "approve")
        rv = self._act(tid, "reverse").get_json()
        r2 = self.client.post(
            f"/api/inventory-transfers/{rv['id']}/allocate",
            json={"lines": rv["suggested_lines"]})
        self.assertEqual(r2.status_code, 200, r2.get_data(as_text=True))
        self.assertEqual(self._act(rv["id"], "approve").status_code, 200)
        rows = self._rows("SELECT qty, designated_customer_id FROM stock_batch "
                          "WHERE product_id=? AND location_id=?", self.pid, self.sto)
        common = [x for x in rows if x["designated_customer_id"] is None]
        self.assertEqual(common[0]["qty"], 8.0, "冲销后通用库存应复原")
        self.assertEqual(self._rows("SELECT COUNT(*) AS n FROM inventory_transfer")[0]["n"], 2)
        self.assertEqual(self._rows("SELECT COUNT(*) AS n FROM inventory_change_log")[0]["n"], 2)
        self._all_ok()


if __name__ == "__main__":
    unittest.main()
