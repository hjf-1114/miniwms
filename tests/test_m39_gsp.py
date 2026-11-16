"""M39 GSP 合规报表：批次追溯 / 效期分级 / 养护 / 验收 / 复核留痕 / 货主隔离 / 只读。

共享物理库约定同 M37/M38：裸连接 closing 包住；setUp 只清本模块相关表与必要
的 owner / product / location / batch / stock_batch / stock_movement，不动别的
里程碑数据；setUpClass 显式 init_db。M39 无新表（纯只读复用现有表）。
"""
import datetime
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
import gsp_service as GS  # noqa: E402
import main  # noqa: E402
import scope as S  # noqa: E402
from flask import g  # noqa: E402

DB = os.environ["MINIWMS_DB"]


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


class M39GspTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        with closing(_conn()) as c:
            c.execute("PRAGMA foreign_keys=OFF")
            for t in ("stock_movement", "stock_batch", "batch_receipt", "batch",
                      "care_log", "care_task", "care_plan", "dn_review_diff_log",
                      "product", "location", "owner"):
                try:
                    c.execute(f"DELETE FROM {t}")
                except sqlite3.OperationalError:
                    pass
            c.commit()
        self.owner = self._post("/api/owners", {
            "code": "M39-OW", "name": "M39 货主"})["owner"]["id"]
        self.other = self._post("/api/owners", {
            "code": "M39-OW9", "name": "M39 货主九"})["owner"]["id"]
        self.pid = self._post("/api/products", {
            "sku": "M39-P", "name": "M39 物料", "owner_id": self.owner})["product"]["id"]
        self.lid = self._post("/api/locations", {
            "code": "M39-L", "name": "M39 库位", "owner_id": self.owner})["location"]["id"]
        self.today = datetime.date.today()

    # ---------- 辅助 ----------
    def _post(self, path, payload, status=201):
        r = self.client.post(path, json=payload)
        self.assertEqual(r.status_code, status, (path, r.get_json()))
        return r.get_json()

    def _get(self, path, status=200):
        r = self.client.get(path)
        self.assertEqual(r.status_code, status, (path, r.get_json()))
        return r.get_json()

    def _sb(self, batch_no, expiry, *, owner=None, product=None, qty=5, key=None):
        with closing(_conn()) as c:
            c.execute(
                "INSERT INTO stock_batch (owner_id, product_id, location_id, batch_no, "
                "batch_key, expiry_date, qty, allocated_qty) VALUES (?,?,?,?,?,?,?,0)",
                (owner or self.owner, product or self.pid, self.lid, batch_no,
                 key or ("KEY-" + batch_no), expiry, qty))
            c.commit()

    def _batch(self, batch_key, *, owner=None, product=None):
        with closing(_conn()) as c:
            cur = c.execute(
                "INSERT INTO batch (code, batch_key, owner_id, product_id, key_info, info, "
                "receipt_count, created_at, updated_at) VALUES (?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
                ("B-" + batch_key, batch_key, owner or self.owner, product or self.pid,
                 "key", "info", 1))
            c.commit()
            return cur.lastrowid

    def _receipt(self, batch_no, batch_id, *, warning="", owner=None, doc_type="采购"):
        with closing(_conn()) as c:
            c.execute(
                "INSERT INTO batch_receipt (batch_no, batch_id, internal_batch_no, "
                "internal_source, owner_id, product_id, location_id, asn_id, asn_line_id, "
                "qty, doc_type, goods_property, info, created_at, external_batch_no, warning) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),?,?)",
                (batch_no, batch_id, "IN-" + batch_no, "asn", owner or self.owner,
                 self.pid, self.lid, 1, 1, 5, doc_type, "合格", "info", "EXT", warning))
            c.commit()

    def _diff(self, review_id, dn_code, diff_qty):
        with closing(_conn()) as c:
            c.execute(
                "INSERT INTO dn_review_diff_log (review_id, review_code, dn_id, dn_code, "
                "owner_id, sku, product_name, location_code, batch_no, batch_key, expiry_date, "
                "goods_property, stock_status, wave_no, expected_qty, actual_qty, diff_qty, "
                "reason_code, reason_label, note, action, operator, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                (review_id, "RV-" + dn_code, review_id, dn_code, self.owner, "M39-P", "物料",
                 "M39-L", "B", "K", "", "合格", "非限制", "", 10, 10 + diff_qty, diff_qty,
                 C.REVIEW_DIFF_SHORT, "少货", "", "diff_registered", "", ))
            c.commit()

    # ---------- 字典与概览 ----------
    def test_01_dict_all_sections(self):
        d = self._get("/api/gsp/dict")
        self.assertTrue(d["ok"])
        self.assertEqual(len(d["reports"]), len(C.GSP_REPORTS))
        self.assertEqual(len(d["expiry_levels"]), 4)
        self.assertEqual(len(d["trace_stages"]), 3)
        self.assertEqual(d["soon_days_default"], C.GSP_EXPIRY_SOON_DAYS_DEFAULT)

    def test_02_overview_cards_and_zeros(self):
        d = self._get("/api/gsp/overview")
        self.assertEqual(len(d["cards"]), 4)
        self.assertEqual({c["code"] for c in d["cards"]},
                         {C.GSP_REPORT_TRACE, C.GSP_REPORT_RECEIPT,
                          C.GSP_REPORT_CARE, C.GSP_REPORT_REVIEW})
        self.assertEqual(d["care_abnormal"], 0)
        self.assertEqual(d["receipt_warned"], 0)

    # ---------- 效期分级 ----------
    def test_03_expiry_four_levels_default_days(self):
        self._sb("EXP", (self.today - datetime.timedelta(days=5)).isoformat())
        self._sb("SOON", (self.today + datetime.timedelta(days=10)).isoformat())
        self._sb("NORM", (self.today + datetime.timedelta(days=100)).isoformat())
        self._sb("NONE", "")
        d = self._get("/api/gsp/expiry")
        stats = {s["level"]: s["count"] for s in d["stats"]}
        self.assertEqual(stats["expired"], 1)
        self.assertEqual(stats["soon"], 1)
        self.assertEqual(stats["normal"], 1)
        self.assertEqual(stats["none"], 1)
        by_no = {b["batch_no"]: b["level"] for b in d["batches"]}
        self.assertEqual(by_no["EXP"], C.GSP_EXPIRY_EXPIRED)
        self.assertEqual(by_no["SOON"], C.GSP_EXPIRY_SOON)
        self.assertEqual(by_no["NORM"], C.GSP_EXPIRY_NORMAL)
        self.assertEqual(by_no["NONE"], C.GSP_EXPIRY_NONE)

    def test_04_expiry_days_override(self):
        self._sb("SOON", (self.today + datetime.timedelta(days=10)).isoformat())
        self.assertEqual(
            {s["level"]: s["count"] for s in self._get("/api/gsp/expiry?days=5")["stats"]}["soon"], 0)
        self.assertEqual(
            {s["level"]: s["count"] for s in self._get("/api/gsp/expiry?days=15")["stats"]}["soon"], 1)

    def test_05_expiry_level_filter_and_days_left(self):
        self._sb("EXP", (self.today - datetime.timedelta(days=3)).isoformat())
        d = self._get("/api/gsp/expiry?level=expired")
        self.assertEqual(d["total"], 1)
        self.assertEqual(d["batches"][0]["days_left"], -3)

    def test_06_expiry_zero_qty_excluded(self):
        self._sb("ZERO", (self.today + datetime.timedelta(days=2)).isoformat(), qty=0)
        self.assertEqual(self._get("/api/gsp/expiry")["total"], 0)

    # ---------- 参数校验 ----------
    def test_07_bad_params(self):
        self.assertEqual(self._get("/api/gsp/expiry?days=999", status=400)["code"], "GSP_BAD_PARAM")
        self.assertEqual(self._get("/api/gsp/expiry?level=bogus", status=400)["code"], "GSP_BAD_PARAM")
        self.assertEqual(self._get("/api/gsp/trace", status=400)["code"], "GSP_BAD_PARAM")

    # ---------- 批次追溯链 ----------
    def test_08_trace_three_stages(self):
        key = "TRACE-KEY"
        bid = self._batch(key)
        self._receipt("T-BATCH", bid)
        with closing(_conn()) as c:
            c.execute(
                "INSERT INTO stock_movement (owner_id, product_id, location_id, qty_delta, "
                "ref_type, ref_id, batch_no, batch_key, note, internal_batch_no, "
                "goods_property, stock_status, expiry_date, allocated_delta, operator, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                (self.owner, self.pid, self.lid, 10, "asn", 1, "T-BATCH", key, "", "",
                 "合格", "非限制", "", 0, ""))
            c.commit()
        self._sb("T-BATCH", (self.today + datetime.timedelta(days=20)).isoformat(), key=key)
        d = self._get("/api/gsp/trace?batch_key=" + key)
        self.assertEqual(d["total"], 1)
        b = d["batches"][0]
        self.assertEqual(len(b["receipts"]), 1)
        self.assertEqual(len(b["stocks"]), 1)
        self.assertEqual(len(b["movements"]), 1)
        self.assertEqual(b["stage_counts"][C.GSP_TRACE_RECEIPT], 1)
        self.assertEqual(b["stage_counts"][C.GSP_TRACE_STOCK], 1)
        self.assertEqual(b["stage_counts"][C.GSP_TRACE_MOVEMENT], 1)

    def test_09_trace_by_batch_no(self):
        key = "BYNO-KEY"
        bid = self._batch(key)
        self._receipt("BYNO", bid)
        d = self._get("/api/gsp/trace?batch_no=BYNO")
        self.assertEqual(d["total"], 1)
        self.assertEqual(d["batches"][0]["receipts"][0]["batch_no"], "BYNO")

    # ---------- 四类留痕 ----------
    def test_10_care_log_report(self):
        with closing(_conn()) as c:
            for code, project, action, result, reason, label in (
                ("CT-1", C.CARE_PROJECT_TEMP, C.CARE_LOG_ABNORMAL,
                 C.CARE_RESULT_ABNORMAL, C.CARE_REASON_TEMP_HIGH, "温度超限"),
                ("CT-2", C.CARE_PROJECT_APPEARANCE, C.CARE_LOG_CHECKED,
                 C.CARE_RESULT_NORMAL, "", ""),
            ):
                c.execute(
                    "INSERT INTO care_log (task_id, task_code, plan_id, plan_code, owner_id, "
                    "project, due_date, action, result, reason_code, reason_label, note, "
                    "operator, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                    (1, code, 1, "CP-1", self.owner, project, "2026-01-01", action,
                     result, reason, label, "", ""))
            c.commit()
        d = self._get("/api/gsp/care")
        self.assertEqual(d["total"], 2)
        self.assertEqual(d["stats"]["abnormal"], 1)
        self.assertEqual(self._get("/api/gsp/care?project=TEMP_HUMIDITY")["total"], 1)
        self.assertEqual(self._get("/api/gsp/care?action=abnormal")["total"], 1)

    def test_11_receipt_log_report_warning_stat(self):
        b1 = self._batch("RB1")
        b2 = self._batch("RB2")
        self._receipt("R1", b1, warning="MISSING_EXTERNAL_BATCH_NO")
        self._receipt("R2", b2)
        d = self._get("/api/gsp/receipt")
        self.assertEqual(d["total"], 2)
        self.assertEqual(d["stats"]["warned"], 1)
        self.assertEqual(self._get("/api/gsp/receipt?batch_no=R1")["total"], 1)

    def test_12_review_diff_report(self):
        self._diff(101, "DN-RV1", -2)
        # over 场景用多货原因码，单独插一条
        with closing(_conn()) as c:
            c.execute(
                "INSERT INTO dn_review_diff_log (review_id, review_code, dn_id, dn_code, "
                "owner_id, sku, product_name, location_code, batch_no, batch_key, expiry_date, "
                "goods_property, stock_status, wave_no, expected_qty, actual_qty, diff_qty, "
                "reason_code, reason_label, note, action, operator, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                (102, "RV-DN-RV2", 102, "DN-RV2", self.owner, "M39-P", "物料", "M39-L",
                 "B", "K", "", "合格", "非限制", "", 10, 13, 3,
                 C.REVIEW_DIFF_OVER, "多货", "", "diff_registered", ""))
            c.commit()
        d = self._get("/api/gsp/review")
        self.assertEqual(d["total"], 2)
        self.assertEqual(d["stats"]["short"], 1)
        self.assertEqual(d["stats"]["over"], 1)
        self.assertEqual(self._get("/api/gsp/review?dn_code=DN-RV1")["total"], 1)

    # ---------- 只读无副作用 ----------
    def test_13_readonly_no_inventory_mutation(self):
        self._sb("RO", (self.today + datetime.timedelta(days=2)).isoformat())
        with closing(_conn()) as c:
            before_sb = c.execute("SELECT COUNT(*) FROM stock_batch").fetchone()[0]
            before_mv = c.execute("SELECT COUNT(*) FROM stock_movement").fetchone()[0]
            before_stock = c.execute("SELECT COUNT(*) FROM stock").fetchone()[0]
        for p in ("/api/gsp/overview", "/api/gsp/expiry", "/api/gsp/trace?product_id=%d" % self.pid,
                  "/api/gsp/care", "/api/gsp/receipt", "/api/gsp/review"):
            self._get(p)
        with closing(_conn()) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM stock_batch").fetchone()[0], before_sb)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM stock_movement").fetchone()[0], before_mv)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM stock").fetchone()[0], before_stock)

    # ---------- 货主隔离 ----------
    def test_14_owner_scope_isolation(self):
        self._sb("MINE", (self.today + datetime.timedelta(days=2)).isoformat())
        self._sb("OTHERS", (self.today + datetime.timedelta(days=2)).isoformat(), owner=self.other)
        with main.app.test_request_context():
            g.scope = {"owner_all": False, "owner_ids": [self.owner]}
            # 匿名 demo 下路由层不注入，直接验证服务层 SQL 的 scoped_where 隔离语义
            where, params = [], []
            S.scoped_where(where, params, table="sb")
            with closing(_conn()) as c:
                n = c.execute(
                    "SELECT COUNT(*) AS n FROM stock_batch sb WHERE sb.qty > 0 AND "
                    + " AND ".join(where), params).fetchone()["n"]
            self.assertEqual(n, 1)

    def test_15_expiry_level_pure_function(self):
        today = datetime.date(2026, 1, 1)
        self.assertEqual(GS._expiry_level("2025-12-31", today, 30)[0], C.GSP_EXPIRY_EXPIRED)
        self.assertEqual(GS._expiry_level("2026-01-20", today, 30)[0], C.GSP_EXPIRY_SOON)
        self.assertEqual(GS._expiry_level("2026-03-01", today, 30)[0], C.GSP_EXPIRY_NORMAL)
        self.assertEqual(GS._expiry_level("", today, 30)[0], C.GSP_EXPIRY_NONE)
        self.assertEqual(GS._expiry_level("bad-date", today, 30)[0], C.GSP_EXPIRY_NONE)


if __name__ == "__main__":
    unittest.main()
