"""M40 工作量统计：事件流 / 按人 / 按节点 / 按时间段 / 过滤 / 货主隔离 / 只读。

共享物理库约定同 M37–M39：closing 包裸连接；setUp 只清本模块相关表与必要
的 owner / product / location / 父单据，不动别的里程碑数据；setUpClass 显式
init_db（M40 无新表，纯只读聚合现有留痕）。
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
import main  # noqa: E402
import scope as S  # noqa: E402
from flask import g  # noqa: E402

DB = os.environ["MINIWMS_DB"]


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


class M40WorkloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        with closing(_conn()) as c:
            c.execute("PRAGMA foreign_keys=OFF")
            for t in (
                "stock_movement", "care_log", "care_task", "care_plan",
                "dn_review_line", "dn_review_diff_log", "dn_review",
                "dn_order_log", "putaway_recommendation",
                "outbound_allocation_log", "inventory_change_log",
                "inventory_transfer_line", "inventory_transfer",
                "dn_pick", "dn_line", "dn", "asn_line", "asn",
                "product", "location", "owner",
            ):
                try:
                    c.execute(f"DELETE FROM {t}")
                except sqlite3.OperationalError:
                    pass
            c.commit()
        self.owner = self._post("/api/owners", {
            "code": "M40-OW", "name": "M40 货主"})["owner"]["id"]
        self.other = self._post("/api/owners", {
            "code": "M40-OW9", "name": "M40 其他货主"})["owner"]["id"]
        self.pid = self._post("/api/products", {
            "sku": "M40-P", "name": "M40 物料", "owner_id": self.owner})["product"]["id"]
        self.lid = self._post("/api/locations", {
            "code": "M40-L", "name": "M40 库位", "owner_id": self.owner})["location"]["id"]
        self.day1 = "2026-11-10 09:15:00"
        self.day2 = "2026-11-11 14:30:00"
        self._seed_events()

    # ---------- 辅助 ----------
    def _post(self, path, payload, status=201):
        r = self.client.post(path, json=payload)
        self.assertEqual(r.status_code, status, (path, r.get_json()))
        return r.get_json()

    def _get(self, path, status=200):
        r = self.client.get(path)
        self.assertEqual(r.status_code, status, (path, r.get_json()))
        return r.get_json()

    def _exec(self, sql, params=()):
        with closing(_conn()) as c:
            c.execute(sql, params)
            c.commit()

    def _seed_events(self):
        """八类数据源各造一条 + 一条其他货主事件，覆盖三个视角。"""
        with closing(_conn()) as c:
            c.execute("INSERT INTO asn (code, owner_id, doc_type, status) "
                      "VALUES ('M40-ASN',?, '采购','open')", (self.owner,))
            asn_id = c.execute("SELECT id FROM asn WHERE code='M40-ASN'").fetchone()[0]
            c.execute("INSERT INTO asn_line (asn_id, product_id, expected_qty) "
                      "VALUES (?,?,10)", (asn_id, self.pid))
            asn_line_id = c.execute(
                "SELECT id FROM asn_line WHERE asn_id=?", (asn_id,)).fetchone()[0]
            c.execute("INSERT INTO dn (code, owner_id, status) VALUES ('M40-DN',?,'open')",
                      (self.owner,))
            dn_id = c.execute("SELECT id FROM dn WHERE code='M40-DN'").fetchone()[0]
            c.execute("INSERT INTO dn_line (dn_id, product_id, ordered_qty) VALUES (?,?,8)",
                      (dn_id, self.pid))
            dn_line_id = c.execute(
                "SELECT id FROM dn_line WHERE dn_id=?", (dn_id,)).fetchone()[0]
            c.execute("INSERT INTO dn_pick (dn_id, line_id, location_id, qty) "
                      "VALUES (?,?,?,8)", (dn_id, dn_line_id, self.lid))
            pick_id = c.execute(
                "SELECT id FROM dn_pick WHERE dn_id=?", (dn_id,)).fetchone()[0]
            c.execute("INSERT INTO dn_review (code, dn_id, dn_code, owner_id, status) "
                      "VALUES ('M40-RV',?,'M40-DN',?,'done')", (dn_id, self.owner))
            rv_id = c.execute("SELECT id FROM dn_review WHERE code='M40-RV'").fetchone()[0]

            c.execute("INSERT INTO care_plan (code, owner_id, project, next_due_date) "
                      "VALUES ('M40-CP',?,?,'2026-11-10')",
                      (self.owner, C.CARE_PROJECT_TEMP))
            plan_id = c.execute("SELECT id FROM care_plan WHERE code='M40-CP'").fetchone()[0]
            c.execute("INSERT INTO care_task (code, plan_id, plan_code, owner_id, project, "
                      "due_date) VALUES ('M40-CT',?,'M40-CP',?,?,'2026-11-10')",
                      (plan_id, self.owner, C.CARE_PROJECT_TEMP))
            task_id = c.execute("SELECT id FROM care_task WHERE code='M40-CT'").fetchone()[0]

            c.execute("INSERT INTO inventory_transfer (code, transfer_type, status) "
                      "VALUES ('M40-IT','batch','approved')")
            it_id = c.execute(
                "SELECT id FROM inventory_transfer WHERE code='M40-IT'").fetchone()[0]
            c.execute("INSERT INTO inventory_transfer_line (transfer_id, owner_id, "
                      "product_id, location_id, qty) VALUES (?,?,?,?,3)",
                      (it_id, self.owner, self.pid, self.lid))
            it_line_id = c.execute(
                "SELECT id FROM inventory_transfer_line WHERE transfer_id=?",
                (it_id,)).fetchone()[0]

            # 1 库存流水（收货）
            c.execute("INSERT INTO stock_movement (owner_id, product_id, location_id, "
                      "qty_delta, ref_type, ref_id, note, operator, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      (self.owner, self.pid, self.lid, 10, C.MOVEMENT_ASN_RECEIVE,
                       asn_id, "收货", "alice", self.day1))
            # 2 库存流水（其他货主，发货）
            c.execute("INSERT INTO stock_movement (owner_id, product_id, location_id, "
                      "qty_delta, ref_type, ref_id, note, operator, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      (self.other, self.pid, self.lid, -4, C.MOVEMENT_DN_SHIP,
                       dn_id, "别家发货", "bob", self.day2))
            # 3 养护（异常处置）
            c.execute("INSERT INTO care_log (task_id, task_code, plan_id, plan_code, "
                      "owner_id, project, due_date, action, result, operator, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (task_id, "M40-CT", plan_id, "M40-CP", self.owner,
                       C.CARE_PROJECT_TEMP, "2026-11-10", C.CARE_LOG_ABNORMAL,
                       C.CARE_RESULT_ABNORMAL, "alice", self.day2))
            # 4 复核行
            c.execute("INSERT INTO dn_review_line (review_id, dn_id, pick_id, product_id, "
                      "location_id, expected_qty, actual_qty, result, operator, reviewed_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (rv_id, dn_id, pick_id, self.pid, self.lid, 8, 8, "ok",
                       "carol", self.day1))
            # 5 复核差异
            c.execute("INSERT INTO dn_review_diff_log (review_id, review_code, dn_id, "
                      "dn_code, owner_id, product_id, diff_qty, reason_code, operator, "
                      "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (rv_id, "M40-RV", dn_id, "M40-DN", self.owner, self.pid, -2,
                       C.REVIEW_DIFF_SHORT, "carol", self.day2))
            # 6 拆单合单
            c.execute("INSERT INTO dn_order_log (action, owner_id, source_dn_ids, "
                      "target_codes, operator, created_at) VALUES (?,?,?,?,?,?)",
                      (C.SPLIT_ACTION_SPLIT, self.owner, str(dn_id), "M40-DN-1",
                       "dave", self.day1))
            # 7 上架推荐决策
            c.execute("INSERT INTO putaway_recommendation (asn_id, asn_line_id, action, "
                      "operator, created_at) VALUES (?,?,?,?,?)",
                      (asn_id, asn_line_id, C.PUTAWAY_ACTION_ADOPT, "alice", self.day2))
            # 8 波次分配
            c.execute("INSERT INTO outbound_allocation_log (dn_id, qty, action, engine, "
                      "operator, created_at) VALUES (?,?,?,?,?,?)",
                      (dn_id, 5, "allocated", C.OUTBOUND_ENGINE_RULES, "carol", self.day1))
            # 9 转移变更文档
            c.execute("INSERT INTO inventory_change_log (transfer_id, line_id, field_name, "
                      "operator, system_at) VALUES (?,?,?,?,?)",
                      (it_id, it_line_id, "batch_no", "dave", self.day2))
            c.commit()

    # ---------- 字典 / 事件流 ----------
    def test_01_dictionary(self):
        d = self._get("/api/workload/dict")
        self.assertGreaterEqual(len(d["nodes"]), 15)
        self.assertEqual([p["code"] for p in d["periods"]], list(C.WORKLOAD_PERIODS))
        self.assertEqual(len(d["sources"]), 8)

    def test_02_events_all_sources(self):
        d = self._get("/api/workload/events")
        self.assertEqual(d["total"], 9)
        sources = {e["source"] for e in d["events"]}
        self.assertEqual(len(sources), 8)
        self.assertEqual(len({e["event_key"] for e in d["events"]}), 9)
        self.assertTrue(all(e["node_label"] for e in d["events"]))

    def test_03_events_paging(self):
        d = self._get("/api/workload/events?page=2&page_size=4")
        self.assertEqual(d["total"], 9)
        self.assertEqual(len(d["events"]), 4)
        self.assertEqual(d["page"], 2)

    def test_04_event_filters(self):
        self.assertEqual(self._get("/api/workload/events?node=review")["total"], 1)
        self.assertEqual(self._get("/api/workload/events?operator=alice")["total"], 3)
        self.assertEqual(self._get("/api/workload/events?source=care_log")["total"], 1)
        self.assertEqual(self._get(f"/api/workload/events?owner_id={self.other}")["total"], 1)
        self.assertEqual(self._get("/api/workload/events?date_from=2026-11-11")["total"], 5)

    def test_05_bad_params(self):
        for path in ("/api/workload/events?node=bogus",
                     "/api/workload/events?date_from=2026-13-01",
                     "/api/workload/events?page_size=999",
                     "/api/workload/by-period?period=year",
                     "/api/workload/events?owner_id=abc"):
            self.assertEqual(self._get(path, status=400)["code"], "WORKLOAD_BAD_PARAM")

    # ---------- 按人 / 按节点 ----------
    def test_06_by_operator(self):
        d = self._get("/api/workload/by-operator")
        counts = {o["operator"]: o["count"] for o in d["operators"]}
        self.assertEqual(counts, {"alice": 3, "carol": 3, "dave": 2, "bob": 1})
        alice = next(o for o in d["operators"] if o["operator"] == "alice")
        self.assertEqual(alice["active_days"], 2)
        self.assertIn(C.WL_NODE_CARE_HANDLE, alice["nodes"])

    def test_07_by_node(self):
        d = self._get("/api/workload/by-node")
        nodes = {n["node"]: n["count"] for n in d["nodes"]}
        self.assertEqual(nodes[C.WL_NODE_RECEIVE], 1)
        self.assertEqual(nodes[C.WL_NODE_CARE_HANDLE], 1)
        self.assertEqual(nodes[C.WL_NODE_REVIEW], 1)
        self.assertEqual(len(nodes), 9)
        self.assertTrue(all(n["label"] for n in d["nodes"]))

    def test_08_operator_filter_propagates(self):
        d = self._get("/api/workload/by-node?operator=carol")
        self.assertEqual(d["total"], 3)
        self.assertEqual({n["node"] for n in d["nodes"]},
                         {C.WL_NODE_REVIEW, C.WL_NODE_REVIEW_DIFF, C.WL_NODE_OUTBOUND})

    def test_09_operators_dropdown(self):
        d = self._get("/api/workload/operators")
        self.assertEqual(d["total"], 4)
        self.assertEqual([o["operator"] for o in d["operators"]],
                         ["alice", "carol", "dave", "bob"])

    # ---------- 时间段 ----------
    def test_10_by_period_day(self):
        d = self._get("/api/workload/by-period")
        series = {s["key"]: s for s in d["series"]}
        self.assertEqual(d["period"], C.WL_PERIOD_DAY)
        self.assertEqual(series["2026-11-10"]["count"], 4)
        self.assertEqual(series["2026-11-11"]["count"], 5)
        self.assertIn(C.WL_NODE_RECEIVE, series["2026-11-10"]["nodes"])

    def test_11_by_period_week_and_month(self):
        week = self._get("/api/workload/by-period?period=week")
        month = self._get("/api/workload/by-period?period=month")
        self.assertEqual(len(week["series"]), 1)
        self.assertEqual(week["series"][0]["count"], 9)
        self.assertEqual(month["series"][0]["key"], "2026-11")
        self.assertEqual(month["series"][0]["qty"], 29.0)  # 10+4+8+2+5 涉及数量

    def test_12_period_with_node_filter(self):
        d = self._get("/api/workload/by-period?period=day&node=care_handle")
        self.assertEqual(d["total"], 1)
        self.assertEqual(d["series"][0]["key"], "2026-11-11")

    # ---------- 概览 / 只读 / 隔离 ----------
    def test_13_overview(self):
        d = self._get("/api/workload/overview")
        self.assertEqual(d["cards"][0]["value"], 9)
        self.assertEqual(d["cards"][1]["value"], 4)
        self.assertEqual(d["cards"][2]["value"], 9)
        self.assertEqual(len(d["daily"]), 2)
        self.assertEqual(d["top_operators"][0]["operator"], "alice")

    def test_14_readonly(self):
        before = {}
        with closing(_conn()) as c:
            for t in ("stock", "stock_movement", "stock_batch", "stock_adjustment",
                      "dn", "care_log", "dn_review"):
                before[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for path in ("/api/workload/overview", "/api/workload/by-node",
                     "/api/workload/by-period", "/api/workload/events"):
            self._get(path)
        with closing(_conn()) as c:
            for t, n in before.items():
                self.assertEqual(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], n, t)

    def test_15_owner_scope_isolation(self):
        with main.app.test_request_context():
            g.scope = {"owner_all": False, "owner_ids": [self.owner]}
            rows = __import__("workload_service").filtered_events(main.get_db(), {})
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(r["owner_id"] == self.owner for r in rows))

    def test_16_scope_missing_denied(self):
        import workload_service as WL
        with main.app.test_request_context():
            g.scope = None
            with self.assertRaises(S.ScopeError):
                WL.overview(main.get_db(), {})

    def test_17_unknown_operator_grouping(self):
        self._exec("UPDATE stock_movement SET operator='' WHERE ref_type=?",
                   (C.MOVEMENT_ASN_RECEIVE,))
        d = self._get("/api/workload/by-operator")
        self.assertIn(C.WORKLOAD_UNKNOWN_OPERATOR,
                      [o["operator"] for o in d["operators"]])

    def test_18_move_qty_prefers_delta(self):
        self._exec("UPDATE stock_movement SET qty_delta=0, allocated_delta=6 "
                   "WHERE ref_type=?", (C.MOVEMENT_ASN_RECEIVE,))
        d = self._get("/api/workload/events?node=receive")
        self.assertEqual(d["events"][0]["qty"], 6.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
