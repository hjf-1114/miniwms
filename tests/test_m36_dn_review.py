"""M36 出库复核：独立复核单 / 逐行复核 / 差异只留痕 / 不改库存。

验收口径来自用户 2026-11-10 4B：独立复核单 + 独立作业页 + 登记差异**只留痕**，
不生成调整单、不改库存；Q17 按 dn_pick 行逐条复核；Q18 复核独立于发货不拦截。

共享物理库：裸连接一律 closing 包住；setUp 先清子表再清主表，重建储区/规则种子，
上架过账不拦截（本类只测复核，不管 M25 limits）。
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing

_TMP = os.path.join(tempfile.gettempdir(), "miniwms_unittest")
os.makedirs(_TMP, exist_ok=True)
os.environ.setdefault("MINIWMS_DB", os.path.join(_TMP, "test.db"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import constants as C  # noqa: E402
import main  # noqa: E402
import putaway_rules as PR  # noqa: E402

DB = os.environ["MINIWMS_DB"]


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


class M36DnReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        with closing(_conn()) as c:
            c.execute("PRAGMA foreign_keys=OFF")
            for t in ("dn_review_diff_log", "dn_review_line", "dn_review",
                      "outbound_allocation_log", "customer_reservation_log",
                      "putaway_recommendation", "putaway_rule", "putaway_rule_set",
                      "product_location", "batch_location", "stock_movement",
                      "stock_batch", "stock", "batch_receipt", "batch",
                      "dn_pick", "dn_line", "dn", "asn_line", "asn",
                      "product", "location", "zone", "owner", "warehouse", "customer"):
                try:
                    c.execute(f"DELETE FROM {t}")
                except sqlite3.OperationalError:
                    pass
            c.commit()
            main._seed_zones(c)
            PR.seed_rules(c)
            c.execute("UPDATE putaway_rule SET enabled = 0 WHERE rule_type = 'putaway'")
            c.commit()
        self.owner = self._post("/api/owners", {
            "code": "M36-OW", "name": "M36 货主"})["owner"]["id"]
        self.wh = self._post("/api/warehouses", {
            "code": "M36-WH", "name": "M36 仓"})["warehouse"]["id"]
        self.product = self._post("/api/products", {
            "sku": "M36-SKU", "name": "M36 物料", "owner_id": self.owner})["product"]["id"]
        self.cust = self._post("/api/customers", {
            "code": "M36-C", "name": "M36 客户", "owner_id": self.owner})["customer"]["id"]
        self.recv = self._loc("M36-RECV", loc_type="receiving")
        self.loc = self._loc("M36-A")

    # ---------- 辅助 ----------
    def _post(self, path, payload, status=201):
        r = self.client.post(path, json=payload)
        self.assertEqual(r.status_code, status, (path, r.get_json()))
        return r.get_json()

    def _get(self, path):
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200, (path, r.get_json()))
        return r.get_json()

    def _bad(self, path, payload, status=400):
        r = self.client.post(path, json=payload)
        self.assertEqual(r.status_code, status, (path, r.get_json()))
        return r.get_json()

    def _loc(self, code, **kw):
        payload = {"code": code, "name": code, "loc_type": "storage"}
        payload.update(kw)
        return self._post("/api/locations", payload)["location"]["id"]

    def _stock(self, tag, qty, *, expiry="2026-12-01"):
        body = self._post("/api/asns", {
            "code": f"M36-ASN-{tag}", "owner_id": self.owner,
            "source_warehouse": self.wh, "doc_type": "采购",
            "lines": [{"product_id": self.product, "expected_qty": qty,
                       "batch_no": f"LOT-{tag}", "expiry_date": expiry,
                       "goods_property": "合格", "external_batch_no": "/"}],
        })
        asn_id, line_id = body["asn"]["id"], body["lines"][0]["id"]
        self._post(f"/api/asns/{asn_id}/receive", {
            "line_id": line_id, "location_id": self.recv, "qty": qty,
            "stock_status": ""}, status=200)
        self._post(f"/api/asns/{asn_id}/putaway", {
            "line_id": line_id, "from_location_id": self.recv,
            "to_location_id": self.loc, "qty": qty}, status=200)

    def _dn(self, qty, code="M36-DN"):
        body = self._post("/api/dns", {
            "code": code, "owner_id": self.owner, "customer_id": self.cust,
            "source_warehouse": self.wh,
            "lines": [{"product_id": self.product, "ordered_qty": qty}],
        })
        return body["dn"]["id"]

    def _dn_with_picks(self, qty=10, code="M36-DN"):
        """走 M35 自动分配，得到带完整批次维度的 dn_pick。"""
        self._stock("A", qty)
        dn_id = self._dn(qty, code)
        d = self._post("/api/outbound/allocate", {"dn_ids": [dn_id]}, status=201)
        self.assertTrue(d["allocations"])
        return dn_id

    def _review(self, dn_id):
        return self._post("/api/dn-reviews", {"dn_id": dn_id}, status=200)

    def _stock_snapshot(self):
        with closing(_conn()) as c:
            return [dict(r) for r in c.execute(
                "SELECT id, location_id, qty, allocated_qty FROM stock "
                "WHERE product_id = ? ORDER BY id", (self.product,))]

    # ---------- 用例 ----------
    def test_01_dictionary(self):
        d = self._get("/api/dn-reviews/dict")
        self.assertIn(C.REVIEW_DRAFT, d["statuses"])
        self.assertEqual(len(d["diff_reasons"]), 6)
        self.assertEqual(len(d["permissions"]), 6)
        self.assertEqual(d["engine"], C.REVIEW_ENGINE_MANUAL)

    def test_02_create_from_picks_snapshots_batch_dims(self):
        dn_id = self._dn_with_picks(10)
        d = self._review(dn_id)
        self.assertTrue(d["code"].startswith("RV-"))
        self.assertEqual(d["status"], C.REVIEW_DRAFT)
        self.assertEqual(d["total_lines"], 1)
        line = d["lines"][0]
        # Q17：批次维度快照到复核行
        self.assertTrue(line["batch_no"], "复核行应带批号")
        self.assertEqual(line["expiry_date"], "2026-12-01")
        self.assertEqual(line["goods_property"], "合格")
        self.assertEqual(line["expected_qty"], 10)
        self.assertTrue(line["wave_no"].startswith("WV-"))

    def test_03_no_picks_rejected(self):
        dn_id = self._dn(5)
        r = self._bad("/api/dn-reviews", {"dn_id": dn_id})
        self.assertEqual(r["code"], "NO_PICKS")

    def test_04_duplicate_active_review_rejected(self):
        dn_id = self._dn_with_picks(10)
        self._review(dn_id)
        r = self._bad("/api/dn-reviews", {"dn_id": dn_id})
        self.assertEqual(r["code"], "DUPLICATE_REVIEW")

    def test_05_pass_and_counts(self):
        dn_id = self._dn_with_picks(10)
        rv = self._review(dn_id)
        line_id = rv["lines"][0]["id"]
        d = self._post(f"/api/dn-reviews/{rv['id']}/check", {
            "line_id": line_id, "actual_qty": 10, "result": "pass"}, status=200)
        self.assertEqual(d["diff_qty"], 0)
        self.assertEqual(d["reviewed_lines"], 1)
        self.assertEqual(d["status"], C.REVIEW_REVIEWING)

    def test_06_pass_with_qty_mismatch_rejected(self):
        dn_id = self._dn_with_picks(10)
        rv = self._review(dn_id)
        r = self._bad(f"/api/dn-reviews/{rv['id']}/check", {
            "line_id": rv["lines"][0]["id"], "actual_qty": 9, "result": "pass"})
        self.assertEqual(r["code"], "PASS_QTY_MISMATCH")

    def test_07_diff_requires_reason(self):
        dn_id = self._dn_with_picks(10)
        rv = self._review(dn_id)
        r = self._bad(f"/api/dn-reviews/{rv['id']}/check", {
            "line_id": rv["lines"][0]["id"], "actual_qty": 8, "result": "diff"})
        self.assertEqual(r["code"], "REASON_REQUIRED")

    def test_08_diff_only_logs_no_stock_change(self):
        dn_id = self._dn_with_picks(10)
        rv = self._review(dn_id)
        before = self._stock_snapshot()
        d = self._post(f"/api/dn-reviews/{rv['id']}/check", {
            "line_id": rv["lines"][0]["id"], "actual_qty": 8, "result": "diff",
            "reason_code": C.REVIEW_DIFF_SHORT, "note": "现场少 2 件"}, status=200)
        self.assertEqual(d["diff_qty"], -2)
        self.assertEqual(d["diff_lines"], 1)
        after = self._stock_snapshot()
        self.assertEqual(before, after, "登记差异绝不改库存")
        with closing(_conn()) as c:
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM stock_movement WHERE ref_type='dn_review'"
            ).fetchone()[0], 0)
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM stock_adjustment").fetchone()[0], 0)
            log = dict(c.execute(
                "SELECT * FROM dn_review_diff_log ORDER BY id DESC LIMIT 1").fetchone())
        self.assertEqual(log["batch_no"], rv["lines"][0]["batch_no"])
        self.assertEqual(log["location_id"], rv["lines"][0]["location_id"])
        self.assertEqual(log["reason_label"], C.REVIEW_DIFF_LABELS[C.REVIEW_DIFF_SHORT])
        self.assertEqual(log["action"], C.REVIEW_LOG_ACTION_DIFF)

    def test_09_diff_repeat_not_double_count(self):
        dn_id = self._dn_with_picks(10)
        rv = self._review(dn_id)
        line_id = rv["lines"][0]["id"]
        self._post(f"/api/dn-reviews/{rv['id']}/check", {
            "line_id": line_id, "actual_qty": 8, "result": "diff",
            "reason_code": C.REVIEW_DIFF_SHORT}, status=200)
        self._post(f"/api/dn-reviews/{rv['id']}/check", {
            "line_id": line_id, "actual_qty": 7, "result": "diff",
            "reason_code": C.REVIEW_DIFF_SHORT}, status=200)
        with closing(_conn()) as c:
            rows = c.execute(
                "SELECT * FROM dn_review_diff_log WHERE review_id = ?", (rv["id"],)
            ).fetchall()
        self.assertEqual(len(rows), 1, "同一行重复复核只留最新一条，不重复计数")
        self.assertEqual(rows[0]["diff_qty"], -3)

    def test_10_diff_cleared_leaves_audit_trail(self):
        dn_id = self._dn_with_picks(10)
        rv = self._review(dn_id)
        line_id = rv["lines"][0]["id"]
        self._post(f"/api/dn-reviews/{rv['id']}/check", {
            "line_id": line_id, "actual_qty": 8, "result": "diff",
            "reason_code": C.REVIEW_DIFF_SHORT}, status=200)
        self._post(f"/api/dn-reviews/{rv['id']}/check", {
            "line_id": line_id, "actual_qty": 10, "result": "pass"}, status=200)
        with closing(_conn()) as c:
            actions = [r[0] for r in c.execute(
                "SELECT action FROM dn_review_diff_log WHERE review_id = ?",
                (rv["id"],))]
        self.assertIn(C.REVIEW_LOG_ACTION_CLEARED, actions, "改判为通过也要留痕")

    def test_11_complete_requires_all_reviewed(self):
        dn_id = self._dn_with_picks(10)
        rv = self._review(dn_id)
        r = self._bad(f"/api/dn-reviews/{rv['id']}/complete", {})
        self.assertEqual(r["code"], "UNREVIEWED_LINES")

    def test_12_full_lifecycle_and_terminal(self):
        dn_id = self._dn_with_picks(10)
        rv = self._review(dn_id)
        self._post(f"/api/dn-reviews/{rv['id']}/check", {
            "line_id": rv["lines"][0]["id"], "actual_qty": 10, "result": "pass"}, status=200)
        done = self._post(f"/api/dn-reviews/{rv['id']}/complete", {}, status=200)
        self.assertEqual(done["status"], C.REVIEW_COMPLETED)
        self.assertIsNotNone(done["reviewed_at"])
        r = self._bad(f"/api/dn-reviews/{rv['id']}/check", {
            "line_id": rv["lines"][0]["id"], "actual_qty": 9,
            "result": "diff", "reason_code": C.REVIEW_DIFF_SHORT})
        self.assertEqual(r["code"], "REVIEW_CLOSED")
        self.assertEqual(self._bad(
            f"/api/dn-reviews/{rv['id']}/complete", {})["code"], "REVIEW_CLOSED")

    def test_13_cancel_review(self):
        dn_id = self._dn_with_picks(10)
        rv = self._review(dn_id)
        d = self._post(f"/api/dn-reviews/{rv['id']}/cancel", {}, status=200)
        self.assertEqual(d["status"], C.REVIEW_CANCELLED)
        self.assertEqual(self._bad(
            f"/api/dn-reviews/{rv['id']}/cancel", {})["code"], "REVIEW_CLOSED")

    def test_14_review_completes_dn_stage_and_blocks_cancellation(self):
        dn_id = self._dn_with_picks(10)
        before = self._get(f"/api/dns/{dn_id}")["dn"]["status"]
        rv = self._review(dn_id)
        self._post(f"/api/dn-reviews/{rv['id']}/check", {
            "line_id": rv["lines"][0]["id"], "actual_qty": 8, "result": "diff",
            "reason_code": C.REVIEW_DIFF_SHORT}, status=200)
        self._post(f"/api/dn-reviews/{rv['id']}/complete", {}, status=200)
        after = self._get(f"/api/dns/{dn_id}")["dn"]["status"]
        self.assertEqual(before, C.DN_ALLOCATED)
        self.assertEqual(after, C.DN_REVIEWED)
        self.assertEqual(self.client.post(f"/api/dns/{dn_id}/cancel").status_code, 409)

    def test_15_logs_query_and_stats(self):
        dn_id = self._dn_with_picks(10)
        rv = self._review(dn_id)
        self._post(f"/api/dn-reviews/{rv['id']}/check", {
            "line_id": rv["lines"][0]["id"], "actual_qty": 8, "result": "diff",
            "reason_code": C.REVIEW_DIFF_SHORT}, status=200)
        d = self._get("/api/dn-reviews/logs")
        self.assertGreaterEqual(d["total"], 1)
        self.assertIn(C.REVIEW_LOG_ACTION_DIFF, d["stats"])
        self.assertEqual(d["reasons"].get(C.REVIEW_DIFF_SHORT), 1)
        by_review = self._get(f"/api/dn-reviews/logs?review_id={rv['id']}")
        self.assertEqual(by_review["total"], 1)

    def test_16_owner_isolation(self):
        """Q20：复核单与差异日志按货主隔离；受限账号看不到别的货主数据。"""
        dn_id = self._dn_with_picks(10)
        rv = self._review(dn_id)
        self._post(f"/api/dn-reviews/{rv['id']}/check", {
            "line_id": rv["lines"][0]["id"], "actual_qty": 8, "result": "diff",
            "reason_code": C.REVIEW_DIFF_SHORT}, status=200)
        # 造第二个货主，把自己授权集切到只含自己，验证第一个货主的复核单不可见
        other = self._post("/api/owners", {
            "code": "M36-OW2", "name": "M36 货主二"})["owner"]["id"]
        import scope as S
        from flask import g
        with main.app.test_request_context():
            g.scope = {"owner_all": False, "owner_ids": [other]}
            where, params = [], []
            S.scoped_where(where, params, table="r")
            with closing(_conn()) as c:
                sql = "SELECT COUNT(*) AS n FROM dn_review r WHERE " + " AND ".join(where)
                self.assertEqual(c.execute(sql, params).fetchone()["n"], 0)
                where2, params2 = [], []
                S.scoped_where(where2, params2, table="l")
                sql2 = "SELECT COUNT(*) AS n FROM dn_review_diff_log l WHERE " + " AND ".join(where2)
                self.assertEqual(c.execute(sql2, params2).fetchone()["n"], 0)

    def test_17_old_db_migration_idempotent(self):
        """老库反复 init_db 幂等，三张新表与索引可重复创建不报错。"""
        main.init_db()
        with closing(_conn()) as c:
            for t in ("dn_review", "dn_review_line", "dn_review_diff_log"):
                self.assertGreater(c.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                    (t,)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
