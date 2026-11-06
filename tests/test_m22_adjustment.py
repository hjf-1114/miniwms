"""M22 库存调整单 + 盘点增强专项测试。

覆盖：DDL/老库迁移、方案 CRUD 与试算、全盘/部分/按批号/动碰四类盘点、
真盲盘分级接口、差异人工生成调整单、审核过账/取消/冲销、三层不变式。
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


class M22AdjustmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            for table in ("stock_adjustment_line", "stock_adjustment",
                          "stocktake_plan", "stocktake_line", "stocktake",
                          "batch_location", "stock_batch", "stock_movement", "stock",
                          "dn_pick", "dn_line", "dn", "asn_line", "asn",
                          "batch_receipt", "batch", "product_location",
                          "customer", "product", "owner", "location", "zone"):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
        finally:
            conn.close()

    # ---------- 夹具 ----------
    def _owner(self, code="OW1"):
        return self.client.post(
            "/api/owners", json={"code": code, "name": f"货主{code}"}
        ).get_json()["owner"]["id"]

    def _product(self, owner_id, sku="P1", **extra):
        return self.client.post(
            "/api/products", json={"sku": sku, "name": f"物料{sku}",
                                  "owner_id": owner_id, **extra}
        ).get_json()["product"]["id"]

    def _loc(self, code, loc_type="storage", **extra):
        return self.client.post(
            "/api/locations", json={"code": code, "name": code,
                                    "loc_type": loc_type, **extra}
        ).get_json()["location"]["id"]

    def _inbound(self, owner_id, pid, loc, qty, tag="1"):
        recv = self._loc(f"RCV-{tag}", "receiving")
        body = self.client.post("/api/asns", json={
            "code": f"ASN-{tag}", "owner_id": owner_id,
            "lines": [{"product_id": pid, "expected_qty": qty}],
        }).get_json()
        aid, lid = body["asn"]["id"], body["lines"][0]["id"]
        self.client.post(f"/api/asns/{aid}/receive", json={
            "line_id": lid, "location_id": recv, "qty": qty})
        self.client.post(f"/api/asns/{aid}/putaway", json={
            "line_id": lid, "from_location_id": recv,
            "to_location_id": loc, "qty": qty})
        return aid

    def _stock_qty(self, pid, loc):
        rows = self.client.get(
            f"/api/stock?product_id={pid}&location_id={loc}"
        ).get_json()["stocks"]
        return rows[0]["qty"] if rows else 0.0

    def _plan(self, **kw):
        body = {"code": kw.pop("code", "PLAN-1"),
                "name": kw.pop("name", "测试方案"), **kw}
        r = self.client.post("/api/stocktake-plans", json=body)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["plan"]["id"]

    def _stocktake(self, owner_id, code="ST-001", **kw):
        r = self.client.post("/api/stocktakes",
                             json={"code": code, "owner_id": owner_id, **kw})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["stocktake"]["id"]

    def _freeze(self, sid):
        r = self.client.post(f"/api/stocktakes/{sid}/freeze", json={})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    def _count_all(self, sid, counted):
        lines = self.client.get(
            f"/api/stocktakes/{sid}").get_json()["lines"]
        payload = [{"line_id": l["id"], "counted_qty": counted} for l in lines]
        r = self.client.post(f"/api/stocktakes/{sid}/count", json={"lines": payload})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    def _generate(self, sid):
        r = self.client.post(f"/api/stocktakes/{sid}/generate-adjustment", json={})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    def _approve(self, adj_id):
        r = self.client.post(f"/api/stock-adjustments/{adj_id}/approve", json={})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    def _all_ok(self):
        i1 = self.client.get("/api/stock/reconcile").get_json()
        both = self.client.get("/api/stock/batch-reconcile").get_json()
        self.assertTrue(i1["consistent"], i1)
        self.assertTrue(both["consistent"], both)
        self.assertEqual(both["i2"]["mismatches"], [])
        self.assertEqual(both["i3"]["mismatches"], [])
        return both

    # ---------- A. DDL 与迁移（3） ----------
    def test_01_new_tables_exist(self):
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"stock_adjustment", "stock_adjustment_line",
                             "stocktake_plan"}.issubset(names))
            cols = {r[1] for r in conn.execute("PRAGMA table_info(stocktake)")}
            self.assertTrue({"plan_id", "scope_snapshot", "blind_mode",
                             "adjustment_id", "closed_at"}.issubset(cols))
        finally:
            conn.close()

    def test_02_stocktake_line_unique_includes_batch_no(self):
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='stocktake_line'"
            ).fetchone()[0]
            self.assertIn("batch_no", sql)
            self.assertIn("UNIQUE(stocktake_id, product_id, location_id, batch_no)", sql)
        finally:
            conn.close()

    def test_03_init_db_is_idempotent(self):
        main.init_db()
        main.init_db()
        self.assertEqual(self.client.get("/api/stocktake-plans").status_code, 200)

    # ---------- B. 方案 CRUD 与试算（6） ----------
    def test_04_plan_create_and_list(self):
        pid = self._plan(scope_type=C.SCOPE_PARTIAL, scope={"location_ids": [1]})
        data = self.client.get("/api/stocktake-plans").get_json()
        self.assertEqual(data["total"], 1)
        plan = data["plans"][0]
        self.assertEqual(plan["id"], pid)
        self.assertEqual(plan["scope"]["location_ids"], [1])
        self.assertEqual(plan["auto_adjust"], 0)
        self.assertTrue(plan["enabled"])

    def test_05_plan_duplicate_code_409(self):
        self._plan()
        r = self.client.post("/api/stocktake-plans",
                             json={"code": "PLAN-1", "name": "重复"})
        self.assertEqual(r.status_code, 409)

    def test_06_plan_invalid_enum_400(self):
        for key, val in (("scope_type", "BAD"), ("blind_mode", "BAD"),
                         ("dynamic_baseline", "BAD")):
            r = self.client.post("/api/stocktake-plans",
                                 json={"code": f"P-{key}", "name": "x", key: val})
            self.assertEqual(r.status_code, 400, key)

    def test_07_plan_invalid_movement_types_400(self):
        r = self.client.post("/api/stocktake-plans", json={
            "code": "PLAN-BAD", "name": "x", "movement_types": ["not_a_type"]})
        self.assertEqual(r.status_code, 400)

    def test_08_plan_update_keeps_missing_fields(self):
        pid = self._plan(enabled=1, auto_adjust=0)
        r = self.client.put(f"/api/stocktake-plans/{pid}", json={"name": "改名"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        plan = r.get_json()["plan"]
        self.assertEqual(plan["name"], "改名")
        self.assertEqual(plan["enabled"], 1)   # 漏传 enabled 不能被置 0
        self.assertEqual(plan["scope_type"], C.SCOPE_FULL)

    def test_09_plan_trial_counts_rows(self):
        oid, pid = self._owner(), None
        pid = self._product(oid)
        a, b = self._loc("A1"), self._loc("B1")
        self._inbound(oid, pid, a, 10, "t1")
        self._inbound(oid, pid, b, 5, "t2")
        full = self.client.post("/api/stocktake-plans/trial",
                                json={"owner_id": oid}).get_json()
        self.assertEqual(full["count"], 2)
        part = self.client.post("/api/stocktake-plans/trial", json={
            "owner_id": oid, "scope_type": C.SCOPE_PARTIAL,
            "scope": {"location_ids": [a]}}).get_json()
        self.assertEqual(part["count"], 1)
        trial = self.client.post("/api/stocktake-plans/trial",
                                 json={"owner_id": 999999})
        self.assertEqual(trial.status_code, 400)

    # ---------- C. 四类盘点（6） ----------
    def test_10_full_stocktake_keeps_old_behavior(self):
        oid = self._owner()
        pid = self._product(oid)
        a = self._loc("A1")
        self._inbound(oid, pid, a, 10)
        sid = self._stocktake(oid)
        fr = self._freeze(sid)
        self.assertEqual(len(fr["lines"]), 1)
        self.assertEqual(fr["lines"][0]["book_qty"], 10.0)
        self.assertEqual(fr["snapshot"]["line_count"], 1)
        st = self.client.get(f"/api/stocktakes/{sid}").get_json()["stocktake"]
        self.assertEqual(st["scope_type"], C.SCOPE_FULL)
        self.assertEqual(st["scope_snapshot"]["line_count"], 1)

    def test_11_partial_scope_by_location_and_product(self):
        oid = self._owner()
        p1, p2 = self._product(oid, "P1"), self._product(oid, "P2")
        a, b = self._loc("A1"), self._loc("B1")
        self._inbound(oid, p1, a, 10, "a")
        self._inbound(oid, p2, b, 5, "b")
        plan = self._plan(scope_type=C.SCOPE_PARTIAL,
                          scope={"location_ids": [a]})
        sid = self._stocktake(oid, plan_id=plan)
        fr = self._freeze(sid)
        self.assertEqual(len(fr["lines"]), 1)
        self.assertEqual(fr["lines"][0]["product_id"], p1)

    def test_12_batch_dimension_produces_one_line_per_batch(self):
        oid = self._owner()
        pid = self._product(oid)
        a = self._loc("A1")
        self._inbound(oid, pid, a, 10, "b1")
        self._inbound(oid, pid, a, 5, "b2")
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            batches = [r[0] for r in conn.execute(
                "SELECT DISTINCT batch_no FROM stock_batch WHERE location_id = ?", (a,))]
        finally:
            conn.close()
        self.assertEqual(len(batches), 2)
        plan = self._plan(code="PLAN-BATCH", scope_type=C.SCOPE_PARTIAL,
                          scope={"batch_no": [batches[0]]})
        sid = self._stocktake(oid, plan_id=plan)
        fr = self._freeze(sid)
        self.assertTrue(fr["snapshot"]["batch_mode"])
        self.assertEqual(len(fr["lines"]), 1)
        self.assertEqual(fr["lines"][0]["batch_no"], batches[0])

    def test_13_plan_snapshot_is_frozen_at_create(self):
        oid = self._owner()
        pid = self._product(oid)
        a, b = self._loc("A1"), self._loc("B1")
        self._inbound(oid, pid, a, 10, "s1")
        self._inbound(oid, pid, b, 5, "s2")
        plan = self._plan(scope_type=C.SCOPE_PARTIAL,
                          scope={"location_ids": [a]})
        sid = self._stocktake(oid, plan_id=plan)
        # 冻结前改方案，已创建单据不受影响
        self.client.put(f"/api/stocktake-plans/{plan}",
                        json={"scope": {"location_ids": [b]}})
        fr = self._freeze(sid)
        self.assertEqual(len(fr["lines"]), 1)
        self.assertEqual(fr["lines"][0]["location_id"], a)

    def test_14_dynamic_uses_movement_types(self):
        oid = self._owner()
        pid = self._product(oid)
        a = self._loc("A1")
        self._inbound(oid, pid, a, 10, "d1")
        plan = self._plan(code="PLAN-DYN", scope_type=C.SCOPE_DYNAMIC,
                          movement_types=[C.MOVEMENT_DN_SHIP])
        sid = self._stocktake(oid, plan_id=plan)
        fr = self._freeze(sid)
        # 只有收货/上架流水，没有发货 → 动碰集合为空
        self.assertEqual(len(fr["lines"]), 0)

    def test_15_dynamic_manual_window_excludes_everything(self):
        oid = self._owner()
        pid = self._product(oid)
        a = self._loc("A1")
        self._inbound(oid, pid, a, 10, "d2")
        plan = self._plan(code="PLAN-WIN", scope_type=C.SCOPE_DYNAMIC,
                          movement_from="2999-01-01 00:00:00")
        sid = self._stocktake(oid, plan_id=plan)
        fr = self._freeze(sid)
        self.assertEqual(len(fr["lines"]), 0)
        self.assertEqual(fr["snapshot"]["baseline_from"], "2999-01-01 00:00:00")

    # ---------- D. 真盲盘分级接口（3） ----------
    def test_16_count_sheet_hides_book_and_diff(self):
        oid = self._owner()
        pid = self._product(oid)
        a = self._loc("A1")
        self._inbound(oid, pid, a, 10)
        plan = self._plan(code="PLAN-BLIND", blind_mode=C.BLIND_HIDE_BOOK)
        sid = self._stocktake(oid, plan_id=plan)
        self._freeze(sid)
        sheet = self.client.get(f"/api/stocktakes/{sid}/count-sheet").get_json()
        self.assertTrue(sheet["hidden"])
        row = sheet["lines"][0]
        self.assertIn("line_id", row)
        self.assertIn("sku", row)
        for forbidden in ("book_qty", "counted_qty", "diff"):
            self.assertNotIn(forbidden, row)

    def test_17_count_returns_only_line_count(self):
        oid = self._owner()
        pid = self._product(oid)
        a = self._loc("A1")
        self._inbound(oid, pid, a, 10)
        sid = self._stocktake(oid)
        self._freeze(sid)
        r = self._count_all(sid, 7)
        self.assertEqual(r["counted_lines"], 1)
        self.assertNotIn("lines", r)
        self.assertNotIn("diff", r)

    def test_18_diff_preview_is_review_view(self):
        oid = self._owner()
        pid = self._product(oid)
        a = self._loc("A1")
        self._inbound(oid, pid, a, 10)
        sid = self._stocktake(oid)
        self._freeze(sid)
        self._count_all(sid, 8)
        pv = self.client.get(f"/api/stocktakes/{sid}/diff-preview").get_json()
        self.assertEqual(pv["diff_count"], 1)
        self.assertEqual(pv["lines"][0]["diff"], -2.0)
        self.assertEqual(pv["uncounted"], 0)

    # ---------- E. 调整单（7） ----------
    def test_19_manual_draft_does_not_touch_stock(self):
        oid = self._owner()
        pid = self._product(oid)
        a = self._loc("A1")
        self._inbound(oid, pid, a, 10)
        r = self.client.post("/api/stock-adjustments", json={
            "reason_code": "报损",
            "lines": [{"owner_id": oid, "product_id": pid,
                       "location_id": a, "delta_qty": -2}]})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual(body["status"], C.ADJUSTMENT_DRAFT)
        self.assertTrue(body["code"].startswith("ADJ-"))
        self.assertEqual(body["adjustment"]["lines"][0]["qty_before"], 10.0)
        self.assertEqual(self._stock_qty(pid, a), 10.0)

    def test_20_approve_posts_stock_and_is_traceable(self):
        oid = self._owner()
        pid = self._product(oid)
        a = self._loc("A1")
        self._inbound(oid, pid, a, 10)
        adj = self.client.post("/api/stock-adjustments", json={
            "lines": [{"owner_id": oid, "product_id": pid,
                       "location_id": a, "delta_qty": -2}]}).get_json()
        self._approve(adj["id"])
        self.assertEqual(self._stock_qty(pid, a), 8.0)
        mv = self.client.get(
            f"/api/movements?ref_type=adjustment&ref_id={adj['id']}").get_json()
        self.assertEqual(len(mv["movements"]), 1)
        self.assertEqual(mv["movements"][0]["qty_delta"], -2.0)
        self._all_ok()
        again = self.client.post(f"/api/stock-adjustments/{adj['id']}/approve", json={})
        self.assertEqual(again.status_code, 400)

    def test_21_create_rejects_zero_delta_and_bad_owner(self):
        oid = self._owner()
        pid = self._product(oid)
        a = self._loc("A1")
        self._inbound(oid, pid, a, 10)
        r = self.client.post("/api/stock-adjustments", json={
            "lines": [{"owner_id": oid, "product_id": pid,
                       "location_id": a, "delta_qty": 0}]})
        self.assertEqual(r.status_code, 400)
        r2 = self.client.post("/api/stock-adjustments", json={
            "lines": [{"owner_id": 999999, "product_id": pid,
                       "location_id": a, "delta_qty": 1}]})
        self.assertEqual(r2.status_code, 400)

    def test_22_cancel_then_regenerate_stocktake_adjustment(self):
        oid = self._owner()
        pid = self._product(oid)
        a = self._loc("A1")
        self._inbound(oid, pid, a, 10)
        sid = self._stocktake(oid)
        self._freeze(sid)
        self._count_all(sid, 7)
        gen = self._generate(sid)
        self.assertEqual(gen["status"], C.STOCKTAKE_ADJUSTED)
        dup = self.client.post(f"/api/stocktakes/{sid}/generate-adjustment", json={})
        self.assertEqual(dup.status_code, 409)
        self.client.post(f"/api/stock-adjustments/{gen['adjustment_id']}/cancel", json={})
        gen2 = self._generate(sid)
        self.assertNotEqual(gen2["adjustment_id"], gen["adjustment_id"])
        self._approve(gen2["adjustment_id"])
        self.assertEqual(self._stock_qty(pid, a), 7.0)
        st = self.client.get(f"/api/stocktakes/{sid}").get_json()["stocktake"]
        self.assertEqual(st["status"], C.STOCKTAKE_CLOSED)
        self._all_ok()

    def test_23_reverse_creates_opposite_draft(self):
        oid = self._owner()
        pid = self._product(oid)
        a = self._loc("A1")
        self._inbound(oid, pid, a, 10)
        adj = self.client.post("/api/stock-adjustments", json={
            "lines": [{"owner_id": oid, "product_id": pid,
                       "location_id": a, "delta_qty": 3}]}).get_json()
        self._approve(adj["id"])
        self.assertEqual(self._stock_qty(pid, a), 13.0)
        rev = self.client.post(f"/api/stock-adjustments/{adj['id']}/reverse", json={})
        self.assertEqual(rev.status_code, 201, rev.get_data(as_text=True))
        body = rev.get_json()
        self.assertEqual(body["status"], C.ADJUSTMENT_DRAFT)
        self.assertEqual(body["adjustment"]["lines"][0]["delta_qty"], -3.0)
        self.assertEqual(self._stock_qty(pid, a), 13.0)  # 冲销单也要审核才过账
        self._approve(body["id"])
        self.assertEqual(self._stock_qty(pid, a), 10.0)
        self._all_ok()

    def test_24_stocktake_no_diff_closes_without_adjustment(self):
        oid = self._owner()
        pid = self._product(oid)
        a = self._loc("A1")
        self._inbound(oid, pid, a, 10)
        sid = self._stocktake(oid)
        self._freeze(sid)
        self._count_all(sid, 10)
        gen = self._generate(sid)
        self.assertTrue(gen["no_diff"])
        self.assertEqual(gen["status"], C.STOCKTAKE_CLOSED)
        self.assertIsNone(gen["adjustment_id"])
        self.assertEqual(self.client.get("/api/stock-adjustments").get_json()["total"], 0)
        self._all_ok()

    def test_25_batch_level_diff_adjusts_only_that_batch(self):
        oid = self._owner()
        pid = self._product(oid)
        a = self._loc("A1")
        self._inbound(oid, pid, a, 10, "r1")
        self._inbound(oid, pid, a, 5, "r2")
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        conn.row_factory = sqlite3.Row
        try:
            batches = [dict(r) for r in conn.execute(
                "SELECT batch_no, qty FROM stock_batch WHERE location_id = ? ORDER BY id", (a,))]
        finally:
            conn.close()
        target = batches[0]["batch_no"]
        plan = self._plan(code="PLAN-BD", scope_type=C.SCOPE_PARTIAL,
                          scope={"batch_no": [target]})
        sid = self._stocktake(oid, plan_id=plan)
        self._freeze(sid)
        self._count_all(sid, batches[0]["qty"] - 3)
        gen = self._generate(sid)
        line = self.client.get(
            f"/api/stock-adjustments/{gen['adjustment_id']}").get_json()["adjustment"]["lines"][0]
        self.assertEqual(line["batch_no"], target)
        self._approve(gen["adjustment_id"])
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        conn.row_factory = sqlite3.Row
        try:
            after = {r["batch_no"]: r["qty"] for r in conn.execute(
                "SELECT batch_no, qty FROM stock_batch WHERE location_id = ?", (a,))}
        finally:
            conn.close()
        self.assertEqual(after[target], batches[0]["qty"] - 3)
        self.assertEqual(after[batches[1]["batch_no"]], batches[1]["qty"])
        self._all_ok()


if __name__ == "__main__":
    unittest.main()
