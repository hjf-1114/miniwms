"""ABC 分类：分级口径 / 只写一列 / 规则条件组打通。

背景：M25 的规则条件组里有 ``abc_class`` 维，但 ctx 里一直写死空串 ——
种子规则「A 类黄金拣货位」因此永远不可能命中。M33 之后补上数据源。

覆盖：
1. 分级口径（A / B / C 的累计占比边界、无出库不分类）；
2. 只写 ``product.abc_class`` 一列，不动库存；
3. 规则上下文的 ``abc_class`` 取到货品上的分类值（那条种子规则从此可命中）。
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

import abc_service as ABC  # noqa: E402
import constants as C  # noqa: E402
import main  # noqa: E402
import putaway_rules as PR  # noqa: E402

DB = os.environ["MINIWMS_DB"]
TODAY = datetime.date.today()


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


class AbcTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        with closing(_conn()) as c:
            c.execute("PRAGMA foreign_keys=OFF")
            c.execute("DELETE FROM stock_movement WHERE note LIKE 'ABC-%'")
            c.execute("DELETE FROM product WHERE sku LIKE 'ABC-%'")
            c.execute("DELETE FROM owner WHERE code = 'ABC-OW'")
            c.execute("DELETE FROM location WHERE code = 'ABC-L1'")
            c.execute("DELETE FROM asn_line WHERE id IN"
                      " (SELECT id FROM asn WHERE code LIKE 'ABC-%')")
            c.execute("DELETE FROM asn WHERE code LIKE 'ABC-%'")
            c.commit()
        self.owner = self.client.post("/api/owners", json={
            "code": "ABC-OW", "name": "ABC 货主"}).get_json()["owner"]["id"]
        self.loc = self.client.post("/api/locations", json={
            "code": "ABC-L1", "name": "ABC 库位"}).get_json()["location"]["id"]
        # 三个货品：出库金额 800 / 150 / 50（合计 1000）
        # 累计占比 80% → A；95% → B；100% → C
        self.p_a = self._product("ABC-P1", "高值货品", 10)
        self.p_b = self._product("ABC-P2", "中值货品", 15)
        self.p_c = self._product("ABC-P3", "低值货品", 50)
        self.p_none = self._product("ABC-P4", "无出库货品", 0)

    def _product(self, sku, name, unit_amount):
        r = self.client.post("/api/products", json={
            "sku": sku, "name": name, "purchase_price": unit_amount})
        return r.get_json()["product"]["id"]

    def _ship(self, product_id, qty):
        """造一条出库流水（负数量）。"""
        when = f"{TODAY.isoformat()} 10:00:00"
        with closing(_conn()) as c:
            c.execute(
                "INSERT INTO stock_movement (owner_id, product_id, location_id,"
                " qty_delta, ref_type, ref_id, note, created_at)"
                " VALUES (?, ?, ?, ?, 'dn_ship', 0, 'ABC-ship', ?)",
                (self.owner, product_id, self.loc, -qty, when))
            c.commit()

    # ---------- 用例 ----------
    def test_01_abc_column_exists(self):
        with closing(_conn()) as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(product)")}
        self.assertIn("abc_class", cols)

    def test_02_compute_grades_a_b_c(self):
        # 金额 800 / 150 / 50 → 累计 80% / 95% / 100%
        self._ship(self.p_a, 80)     # 80 × 10 = 800
        self._ship(self.p_b, 10)     # 10 × 15 = 150
        self._ship(self.p_c, 1)      # 1 × 50 = 50
        with closing(_conn()) as c:
            items = ABC.compute(c, 30)
        got = {i["product_id"]: i["abc_class"] for i in items}
        self.assertEqual(got[self.p_a], "A")
        self.assertEqual(got[self.p_b], "B")
        self.assertEqual(got[self.p_c], "C")
        # 无出库记录 → 不出现在结果里（不硬凑成 C）
        self.assertNotIn(self.p_none, got)

    def test_03_apply_writes_only_class_column(self):
        self._ship(self.p_a, 80)
        self._ship(self.p_b, 10)
        self._ship(self.p_c, 1)
        with closing(_conn()) as c:
            before = c.execute(
                "SELECT COUNT(*) FROM stock_movement").fetchone()[0]
            res = ABC.apply(c, 30)
            after = c.execute(
                "SELECT COUNT(*) FROM stock_movement").fetchone()[0]
            row = c.execute("SELECT abc_class FROM product WHERE id = ?",
                            (self.p_a,)).fetchone()
        self.assertEqual(before, after)          # 流水数不变
        self.assertEqual(row["abc_class"], "A")   # 分类已写回
        self.assertGreaterEqual(res["changed_count"], 3)

    def test_04_overview_distribution(self):
        self._ship(self.p_a, 80)
        with closing(_conn()) as c:
            ABC.apply(c, 30)
            ov = ABC.overview(c, 30)
        self.assertEqual(ov["thresholds"]["a"], C.ABC_A_CUM)
        self.assertGreaterEqual(ov["unclassified"], 1)   # 无出库的货品仍未分类

    def test_05_api_endpoints(self):
        self._ship(self.p_a, 80)
        d = self.client.get("/api/abc/dict")
        self.assertEqual(d.status_code, 200)
        self.assertEqual(d.get_json()["classes"], list(C.ABC_CLASSES))
        c1 = self.client.get("/api/abc/compute")
        self.assertEqual(c1.status_code, 200)
        c2 = self.client.post("/api/abc/recalculate", json={"days": 30})
        self.assertEqual(c2.status_code, 200, c2.get_data(as_text=True)[:200])
        o = self.client.get("/api/abc/overview")
        self.assertEqual(o.status_code, 200)
        bad = self.client.post("/api/abc/recalculate", json={"days": "abc"})
        self.assertEqual(bad.status_code, 400)

    def test_06_rule_context_carries_abc(self):
        """规则上下文取到货品的 abc_class —— 那条「A 类黄金拣货位」从此可命中。"""
        self._ship(self.p_a, 80)
        with closing(_conn()) as c:
            ABC.apply(c, 30)
        # 造 ASN + 明细行，验证 build_context 的 abc_class
        with closing(_conn()) as c:
            c.execute("INSERT INTO asn (code, owner_id, doc_type, status)"
                      " VALUES ('ABC-ASN', ?, '采购', 'open')", (self.owner,))
            asn_id = c.execute("SELECT id FROM asn WHERE code='ABC-ASN'").fetchone()[0]
            c.execute("INSERT INTO asn_line (asn_id, product_id, expected_qty)"
                      " VALUES (?, ?, 10)", (asn_id, self.p_a))
            line_id = c.execute(
                "SELECT id FROM asn_line WHERE asn_id = ?", (asn_id,)).fetchone()[0]
            c.commit()
        with main.app.app_context():
            ctx = PR.build_context(main.get_db(), asn_id, line_id, 10)
        self.assertEqual(ctx["ctx"]["abc_class"], "A")
        # 条件组按 abc_class 命中
        self.assertTrue(PR.matches({"abc_class": "A"}, ctx["ctx"]))
        self.assertFalse(PR.matches({"abc_class": "C"}, ctx["ctx"]))


if __name__ == "__main__":
    unittest.main()
