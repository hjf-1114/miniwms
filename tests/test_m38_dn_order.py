"""M38 出库单拆单 / 合单：单据层数量守恒 / 只留痕不动库存 / 未拣货约束 / 货主隔离。

共享物理库约定同 M36/M37：裸连接 closing 包住；setUp 只清本模块相关表，
不动别的里程碑数据；setUpClass 显式 init_db。
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
import scope as S  # noqa: E402
from flask import g  # noqa: E402

DB = os.environ["MINIWMS_DB"]


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


class M38DnOrderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        with closing(_conn()) as c:
            c.execute("PRAGMA foreign_keys=OFF")
            for t in ("dn_order_log", "dn_pick", "dn_line", "dn", "customer", "product", "owner"):
                try:
                    c.execute(f"DELETE FROM {t}")
                except sqlite3.OperationalError:
                    pass
            c.commit()
        self.owner = self._post("/api/owners", {
            "code": "M38-OW", "name": "M38 货主"})["owner"]["id"]
        self.customer = self._post("/api/customers", {
            "code": "M38-CU", "name": "M38 客户", "owner_id": self.owner})["customer"]["id"]
        self.pids = [self._post("/api/products", {
            "sku": f"M38-P{i}", "name": f"物料{i}", "owner_id": self.owner})["product"]["id"]
            for i in (1, 2, 3)]

    # ---------- 辅助 ----------
    def _post(self, path, payload, status=None):
        # 建资源 201；拆单/合单动作 200
        if status is None:
            status = 200 if path.startswith("/api/dn-order/") else 201
        r = self.client.post(path, json=payload)
        self.assertEqual(r.status_code, status, (path, r.get_json()))
        return r.get_json()

    def _bad(self, path, payload, status=400):
        r = self.client.post(path, json=payload)
        self.assertEqual(r.status_code, status, (path, r.get_json()))
        return r.get_json()

    def _get(self, path):
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200, (path, r.get_json()))
        return r.get_json()

    def _dn(self, code, lines, owner=None, customer=None):
        return self._post("/api/dns", {
            "code": code, "owner_id": owner or self.owner,
            "customer_id": customer or self.customer,
            "lines": [{"product_id": p, "ordered_qty": q} for p, q in lines]})

    def _stock_snapshot(self):
        with closing(_conn()) as c:
            return tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                         for t in ("stock", "stock_movement", "inventory_change_log"))

    def test_01_dictionary(self):
        d = self._get("/api/dn-order/dict")
        self.assertEqual([x["value"] for x in d["actions"]], ["split", "merge"])
        self.assertEqual([x["value"] for x in d["modes"]], ["by_qty", "by_line"])
        self.assertEqual(d["allowed_status"], ["open"])
        self.assertEqual(d["qty_eps"], 1e-9)

    def test_02_split_by_qty_conservation(self):
        dn = self._dn("M38-SPLIT-1", [(self.pids[0], 10), (self.pids[1], 5)])
        snap = self._stock_snapshot()
        r = self._post("/api/dn-order/split", {
            "source_id": dn["dn"]["id"],
            "parts": [{"lines": [{"product_id": self.pids[0], "qty": 4}]},
                      {"lines": [{"product_id": self.pids[1], "qty": 5}]}],
            "note": "拆两单"})
        self.assertEqual(len(r["targets"]), 2)
        self.assertAlmostEqual(sum(t["total_qty"] for t in r["targets"]), 9)
        # 源单剩 6（10-4；pid1 全拆走行被删）
        self.assertAlmostEqual(r["source"]["total_qty"], 6)
        self.assertEqual(r["source"]["status"], C.DN_OPEN)
        with closing(_conn()) as c:
            lines = c.execute("SELECT * FROM dn_line WHERE dn_id=?", (dn["dn"]["id"],)).fetchall()
        self.assertEqual(len(lines), 1)  # pid1 行删除
        self.assertAlmostEqual(lines[0]["ordered_qty"], 6)
        self.assertEqual(self._stock_snapshot(), snap)  # 库存零变动

    def test_03_split_partial_preserves_source(self):
        dn = self._dn("M38-SPLIT-2", [(self.pids[0], 8)])
        r = self._post("/api/dn-order/split", {
            "source_id": dn["dn"]["id"],
            "parts": [{"lines": [{"product_id": self.pids[0], "qty": 3}]}]})
        self.assertAlmostEqual(r["source"]["total_qty"], 5)
        self.assertAlmostEqual(r["targets"][0]["total_qty"], 3)
        self.assertTrue(r["targets"][0]["code"].startswith("DN-"))

    def test_04_split_all_closes_source(self):
        dn = self._dn("M38-SPLIT-3", [(self.pids[0], 6)])
        r = self._post("/api/dn-order/split", {
            "source_id": dn["dn"]["id"],
            "parts": [{"lines": [{"product_id": self.pids[0], "qty": 6}]}]})
        detail = self._get(f"/api/dns/{dn['dn']['id']}")
        self.assertEqual(detail["dn"]["status"], C.DN_CANCELLED)
        self.assertEqual(detail["lines"], [])
        self.assertEqual(r["source"]["status"], C.DN_CANCELLED)

    def test_05_split_qty_exceeded_rejected(self):
        dn = self._dn("M38-SPLIT-4", [(self.pids[0], 5)])
        err = self._bad("/api/dn-order/split", {
            "source_id": dn["dn"]["id"],
            "parts": [{"lines": [{"product_id": self.pids[0], "qty": 6}]}]})
        self.assertEqual(err["code"], "QTY_EXCEEDED")
        with closing(_conn()) as c:
            n = c.execute("SELECT COUNT(*) FROM dn_order_log").fetchone()[0]
        self.assertEqual(n, 0)

    def test_06_split_unknown_product_rejected(self):
        dn = self._dn("M38-SPLIT-5", [(self.pids[0], 5)])
        err = self._bad("/api/dn-order/split", {
            "source_id": dn["dn"]["id"],
            "parts": [{"lines": [{"product_id": 999999, "qty": 1}]}]})
        self.assertEqual(err["code"], "PRODUCT_NOT_IN_SOURCE")

    def test_07_split_dup_product_in_part_rejected(self):
        dn = self._dn("M38-SPLIT-6", [(self.pids[0], 5)])
        err = self._bad("/api/dn-order/split", {
            "source_id": dn["dn"]["id"],
            "parts": [{"lines": [{"product_id": self.pids[0], "qty": 1},
                                 {"product_id": self.pids[0], "qty": 2}]}]})
        self.assertEqual(err["code"], "DUP_PRODUCT")

    def test_08_split_empty_parts_rejected(self):
        dn = self._dn("M38-SPLIT-7", [(self.pids[0], 5)])
        self._bad("/api/dn-order/split", {"source_id": dn["dn"]["id"], "parts": []})

    def test_09_split_missing_source_404(self):
        self._bad("/api/dn-order/split", {"source_id": 999999, "parts": [
            {"lines": [{"product_id": self.pids[0], "qty": 1}]}]}, status=404)

    def test_10_merge_conservation(self):
        d1 = self._dn("M38-MERGE-1", [(self.pids[0], 10), (self.pids[1], 5)])
        d2 = self._dn("M38-MERGE-2", [(self.pids[0], 3), (self.pids[2], 7)])
        snap = self._stock_snapshot()
        r = self._post("/api/dn-order/merge", {
            "source_ids": [d1["dn"]["id"], d2["dn"]["id"]], "note": "合并同客户"})
        self.assertAlmostEqual(r["total_qty"], 25)
        self.assertEqual(len(r["target_lines"]), 3)  # pid0 聚合为 13
        agg = {x["product_id"]: x["ordered_qty"] for x in r["target_lines"]}
        self.assertAlmostEqual(agg[self.pids[0]], 13)
        self.assertAlmostEqual(agg[self.pids[1]], 5)
        self.assertAlmostEqual(agg[self.pids[2]], 7)
        for sid in (d1["dn"]["id"], d2["dn"]["id"]):
            self.assertEqual(self._get(f"/api/dns/{sid}")["dn"]["status"], C.DN_CANCELLED)
        self.assertEqual(self._stock_snapshot(), snap)

    def test_11_merge_requires_two_distinct(self):
        dn = self._dn("M38-MERGE-3", [(self.pids[0], 5)])
        for ids in ([dn["dn"]["id"]], [dn["dn"]["id"], dn["dn"]["id"]]):
            self._bad("/api/dn-order/merge", {"source_ids": ids})

    def test_12_merge_owner_mismatch_rejected(self):
        d1 = self._dn("M38-MERGE-4", [(self.pids[0], 5)])
        ow2 = self._post("/api/owners", {"code": "M38-OW2", "name": "M38 货主二"})["owner"]["id"]
        cu2 = self._post("/api/customers", {
            "code": "M38-CU2", "name": "M38 客户二", "owner_id": ow2})["customer"]["id"]
        pd = self._post("/api/products", {
            "sku": "M38-PX", "name": "跨货主物料", "owner_id": ow2})["product"]["id"]
        d2 = self._dn("M38-MERGE-5", [(pd, 5)], owner=ow2, customer=cu2)
        err = self._bad("/api/dn-order/merge", {
            "source_ids": [d1["dn"]["id"], d2["dn"]["id"]]})
        self.assertEqual(err["code"], "OWNER_MISMATCH")

    def test_13_merge_customer_mismatch_rejected(self):
        d1 = self._dn("M38-MERGE-6", [(self.pids[0], 5)])
        cu2 = self._post("/api/customers", {
            "code": "M38-CU3", "name": "M38 客户三", "owner_id": self.owner})["customer"]["id"]
        d2 = self._dn("M38-MERGE-7", [(self.pids[0], 5)], customer=cu2)
        err = self._bad("/api/dn-order/merge", {
            "source_ids": [d1["dn"]["id"], d2["dn"]["id"]]})
        self.assertEqual(err["code"], "CUSTOMER_MISMATCH")

    def test_14_picked_dn_cannot_split(self):
        dn = self._dn("M38-PICK-1", [(self.pids[0], 5)])
        # 直接写 dn_pick 模拟已分配（不经库存）
        with closing(_conn()) as c:
            line_id = c.execute("SELECT id FROM dn_line WHERE dn_id=?", (dn["dn"]["id"],)).fetchone()[0]
            c.execute("INSERT INTO dn_pick (dn_id, line_id, location_id, qty) VALUES (?,?,?,?)",
                      (dn["dn"]["id"], line_id, 1, 1))
            c.commit()
        err = self._bad("/api/dn-order/split", {
            "source_id": dn["dn"]["id"],
            "parts": [{"lines": [{"product_id": self.pids[0], "qty": 1}]}]})
        self.assertEqual(err["code"], "ALREADY_ALLOCATED")

    def test_15_shipped_dn_cannot_split(self):
        dn = self._dn("M38-SHIP-1", [(self.pids[0], 5)])
        with closing(_conn()) as c:
            c.execute("UPDATE dn SET status='shipped' WHERE id=?", (dn["dn"]["id"],))
            c.commit()
        err = self._bad("/api/dn-order/split", {
            "source_id": dn["dn"]["id"],
            "parts": [{"lines": [{"product_id": self.pids[0], "qty": 1}]}]})
        self.assertEqual(err["code"], "STATUS_NOT_ALLOWED")

    def test_16_log_and_lineage(self):
        dn = self._dn("M38-LOG-1", [(self.pids[0], 6)])
        r = self._post("/api/dn-order/split", {
            "source_id": dn["dn"]["id"],
            "parts": [{"lines": [{"product_id": self.pids[0], "qty": 2}]}], "note": "溯源"})
        child_id = r["targets"][0]["id"]
        logs = self._get("/api/dn-order/log")["logs"]
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["action"], "split")
        self.assertEqual(logs[0]["note"], "溯源")
        self.assertEqual(len(logs[0]["detail_rows"]), 1)
        self.assertEqual(len(self._get(f"/api/dn-order/{dn['dn']['id']}/lineage")["logs"]), 1)
        self.assertEqual(len(self._get(f"/api/dn-order/{child_id}/lineage")["logs"]), 1)
        self.assertEqual(self._get("/api/dn-order/log?action=merge")["total"], 0)
        self.assertEqual(self._get("/api/dn-order/log?keyword=M38-LOG-1")["total"], 1)

    def test_17_candidates_exclude_picked(self):
        dn1 = self._dn("M38-CAND-1", [(self.pids[0], 5)])
        dn2 = self._dn("M38-CAND-2", [(self.pids[0], 5)])
        with closing(_conn()) as c:
            line_id = c.execute("SELECT id FROM dn_line WHERE dn_id=?", (dn2["dn"]["id"],)).fetchone()[0]
            c.execute("UPDATE dn_line SET picked_qty=1 WHERE id=?", (line_id,))
            c.commit()
        cands = self._get("/api/dn-order/candidates")["candidates"]
        by_id = {x["id"]: x for x in cands}
        self.assertTrue(by_id[dn1["dn"]["id"]]["splittable"])
        self.assertFalse(by_id[dn2["dn"]["id"]]["splittable"])

    def test_18_owner_scope_isolation(self):
        dn = self._dn("M38-ISO-1", [(self.pids[0], 6)])
        self._post("/api/dn-order/split", {
            "source_id": dn["dn"]["id"],
            "parts": [{"lines": [{"product_id": self.pids[0], "qty": 2}]}]})
        other = self._post("/api/owners", {
            "code": "M38-OW9", "name": "M38 货主九"})["owner"]["id"]
        with main.app.test_request_context():
            g.scope = {"owner_all": False, "owner_ids": [other]}
            where, params = [], []
            S.scoped_where(where, params, table="l")
            with closing(_conn()) as c:
                n = c.execute("SELECT COUNT(*) AS n FROM dn_order_log l WHERE " + " AND ".join(where),
                              params).fetchone()["n"]
            self.assertEqual(n, 0)
            with self.assertRaises(S.ScopeError):
                S.assert_owner_allowed(g.scope, self.owner)

    def test_19_migration_idempotent(self):
        main.init_db()
        main.init_db()
        with closing(_conn()) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(dn)").fetchall()}
            self.assertIn("origin_action", cols)
            self.assertIn("origin_parent_id", cols)
            tables = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            self.assertIn("dn_order_log", tables)


if __name__ == "__main__":
    unittest.main()
