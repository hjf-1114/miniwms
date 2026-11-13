"""M26 规则配置编辑器与推荐交互：配置 API / 采纳驳回 / 排序 / 统计 / 字典。

对照 ``docs/PUTAWAY_RECOMMENDATION.md`` §14 阶段 F 验收标准：
多规则增删改排序、试算面板可用、改位必填原因码、推荐→采纳→上架链路留痕。

注意共享物理库的坑：所有裸连接一律用 ``contextlib.closing`` 包住，
且 ``setUp`` 先清 M21/M22/M25 子表再重建种子（``seed_rules`` 只在策略包不存在时插行）。
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


class M26RuleEditorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        with closing(_conn()) as c:
            c.execute("PRAGMA foreign_keys=OFF")
            for t in ("putaway_recommendation", "putaway_rule", "putaway_rule_set",
                      "product_location", "batch_location", "stock_movement",
                      "stock_batch", "stock", "batch_receipt", "batch",
                      "asn_line", "asn", "product", "location", "zone", "owner",
                      "warehouse", "customer"):
                try:
                    c.execute(f"DELETE FROM {t}")
                except sqlite3.OperationalError:
                    pass
            # 字典种子保留，只清本测试自己造的前缀，避免污染其它测试
            c.execute("DELETE FROM external_doc_type_dict WHERE code LIKE 'M26-%'")
            c.commit()
            main._seed_zones(c)
            PR.seed_rules(c)
            c.commit()

        self.owner = self._post("/api/owners", {"code": "M26-OW", "name": "M26 货主"})["owner"]["id"]
        self.wh = self._post("/api/warehouses", {"code": "M26-WH", "name": "M26 仓"})["warehouse"]["id"]
        self.product = self._post("/api/products", {
            "sku": "M26-SKU", "name": "M26 物料", "owner_id": self.owner,
        })["product"]["id"]
        body = self._post("/api/asns", {
            "code": "M26-ASN", "owner_id": self.owner, "source_warehouse": self.wh,
            "lines": [{"product_id": self.product, "expected_qty": 10, "batch_no": "LOT-A"}],
        })
        self.asn_id = body["asn"]["id"]
        self.line_id = body["lines"][0]["id"]
        self.loc = self._loc("M26-A")

    def _post(self, path, payload):
        r = self.client.post(path, json=payload)
        self.assertEqual(r.status_code, 201, (path, r.get_json()))
        return r.get_json()

    def _loc(self, code, **kw):
        payload = {"code": code, "name": code, "loc_type": "storage"}
        payload.update(kw)
        return self._post("/api/locations", payload)["location"]["id"]

    def _preview(self):
        """走真实推荐接口产生一条 preview 日志，返回日志 id。"""
        r = self.client.get(
            f"/api/putaway/recommend?asn_id={self.asn_id}&line_id={self.line_id}"
            "&qty=1&top_n=5")
        self.assertEqual(r.status_code, 200, r.get_json())
        rid = r.get_json().get("recommendation_id")
        self.assertTrue(rid, "规则引擎分支应写入推荐日志并返回 recommendation_id")
        return rid

    def _action(self, rid, payload, expect=200):
        r = self.client.post(f"/api/putaway-recommendations/{rid}/action", json=payload)
        self.assertEqual(r.status_code, expect, (expect, r.get_json()))
        return r.get_json()

    # ── 1. 外部单据类别字典 ────────────────────────────────────────────
    def test_doc_type_seed_and_crud(self):
        d = self.client.get("/api/external-doc-types")
        self.assertEqual(d.status_code, 200)
        body = d.get_json()
        self.assertGreaterEqual(len(body["doc_types"]), 6)
        self.assertTrue(body["override_reasons"])

        created = self._post("/api/external-doc-types", {
            "code": "m26-test", "name": "M26 测试类别", "note": "临时",
        })
        tid = created["id"]
        rows = self.client.get("/api/external-doc-types").get_json()["doc_types"]
        row = next(x for x in rows if x["id"] == tid)
        self.assertEqual(row["code"], "M26-TEST")     # 统一大写
        self.assertEqual(row["enabled"], 1)

        # 浏览器 select 会提交字符串 "0"，后端必须兼容（不是 bool("0") == True）
        r = self.client.put(f"/api/external-doc-types/{tid}", json={
            "code": "M26-TEST", "name": "M26 停用类别", "enabled": "0"})
        self.assertEqual(r.status_code, 200, r.get_json())
        row = next(x for x in self.client.get("/api/external-doc-types").get_json()["doc_types"]
                   if x["id"] == tid)
        self.assertEqual(row["enabled"], 0)

        r = self.client.delete(f"/api/external-doc-types/{tid}")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertFalse([x for x in self.client.get("/api/external-doc-types").get_json()["doc_types"]
                          if x["id"] == tid])

    def test_doc_type_duplicate_code_rejected(self):
        self._post("/api/external-doc-types", {"code": "M26-DUP", "name": "甲"})
        r = self.client.post("/api/external-doc-types",
                             json={"code": "M26-DUP", "name": "乙"})
        self.assertEqual(r.status_code, 400)

    # ── 2. 采纳 / 驳回动作 ─────────────────────────────────────────────
    def test_adopt_requires_location_and_reject_requires_reason(self):
        rid = self._preview()
        self._action(rid, {"action": "adopt"}, expect=400)
        self._action(rid, {"action": "reject"}, expect=400)
        self._action(rid, {"action": "unknown", "chosen_location_id": self.loc}, expect=400)

    def test_adopt_then_reject_updates_same_row(self):
        rid = self._preview()
        d = self._action(rid, {"action": "adopt", "chosen_location_id": self.loc,
                               "operator": "jeff"})
        self.assertEqual(d["recommendation"]["action"], "adopt")
        self.assertEqual(d["recommendation"]["chosen_location_id"], self.loc)
        self.assertEqual(d["recommendation"]["operator"], "jeff")

        d = self._action(rid, {"action": "reject", "override_reason": "现场货位已满"})
        self.assertEqual(d["recommendation"]["action"], "reject")
        self.assertEqual(d["recommendation"]["override_reason"], "现场货位已满")
        # 重复动作不新增日志行
        total = self.client.get("/api/putaway-recommendations").get_json()["total"]
        self.assertEqual(total, 1)

    def test_action_not_found_returns_404(self):
        r = self.client.post("/api/putaway-recommendations/999999/action",
                             json={"action": "adopt", "chosen_location_id": self.loc})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()["code"], "NOT_FOUND")

    def test_rates_and_filters(self):
        r1 = self._preview()
        self._action(r1, {"action": "adopt", "chosen_location_id": self.loc})
        r2 = self._preview()
        self._action(r2, {"action": "reject", "override_reason": "客户指定货位"})

        body = self.client.get("/api/putaway-recommendations").get_json()
        rates = body["rates"]
        self.assertEqual(rates["total"], 2)
        self.assertEqual(rates["adopt"], 1)
        self.assertEqual(rates["reject"], 1)
        self.assertEqual(rates["adopt_rate"], 0.5)
        self.assertEqual(rates["hit_rate"], 1.0)   # 两条都由规则引擎命中

        filtered = self.client.get("/api/putaway-recommendations?action=adopt").get_json()
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["recommendations"][0]["id"], r1)
        self.assertEqual(filtered["rates"]["adopt_rate"], 1.0)

    def test_recommendation_filters_by_asn_and_has_hit(self):
        rid = self._preview()
        d = self.client.get(
            f"/api/putaway-recommendations?asn_id={self.asn_id}"
            f"&asn_line_id={self.line_id}&has_hit=1").get_json()
        self.assertEqual(d["total"], 1)
        self.assertEqual(d["recommendations"][0]["id"], rid)
        d = self.client.get(
            f"/api/putaway-recommendations?asn_id={self.asn_id + 999}").get_json()
        self.assertEqual(d["total"], 0)

    # ── 3. 拖拽排序 / 策略包删除保护 ────────────────────────────────────
    def test_reorder_swaps_priority_without_unique_conflict(self):
        rows = self.client.get("/api/putaway-rules").get_json()["rules"]
        a, b = rows[0], rows[1]
        r = self.client.post("/api/putaway-rules/reorder", json={
            "items": [{"id": a["id"], "priority": b["priority"]},
                      {"id": b["id"], "priority": a["priority"]}]})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["updated"], 2)
        after = {x["id"]: x["priority"]
                 for x in self.client.get("/api/putaway-rules").get_json()["rules"]}
        self.assertEqual(after[a["id"]], b["priority"])
        self.assertEqual(after[b["id"]], a["priority"])

    def test_reorder_rejects_unknown_id(self):
        r = self.client.post("/api/putaway-rules/reorder",
                             json={"items": [{"id": 999999, "priority": 1}]})
        self.assertEqual(r.status_code, 400)

    def test_delete_default_set_protected_empty_set_deletable(self):
        sets = self.client.get("/api/putaway-rule-sets").get_json()["rule_sets"]
        default_id = next(x["id"] for x in sets if x["code"] == C.RULE_SET_DEFAULT_CODE)
        r = self.client.delete(f"/api/putaway-rule-sets/{default_id}")
        self.assertEqual(r.status_code, 400)

        new_id = self._post("/api/putaway-rule-sets", {
            "code": "M26-EMPTY", "name": "M26 空策略包"})["id"]
        r = self.client.delete(f"/api/putaway-rule-sets/{new_id}")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertFalse([x for x in self.client.get("/api/putaway-rule-sets").get_json()["rule_sets"]
                          if x["id"] == new_id])

    def test_delete_set_with_rules_rejected(self):
        new_id = self._post("/api/putaway-rule-sets", {
            "code": "M26-BUSY", "name": "M26 有规则策略包"})["id"]
        self._post("/api/putaway-rules", {
            "rule_set_id": new_id, "name": "M26 测试规则", "priority": 10,
            "condition": {}, "steps": [{"type": "all_active"}],
            "limits": {}, "score": {},
        })
        r = self.client.delete(f"/api/putaway-rule-sets/{new_id}")
        self.assertEqual(r.status_code, 400)

    # ── 4. 既有能力回归 ────────────────────────────────────────────────
    def test_trial_still_available(self):
        r = self.client.post("/api/putaway-rules/trial", json={
            "condition": {"is_cold_chain": 1}, "ctx": {"is_cold_chain": 1}})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(r.get_json()["matched"])
        r = self.client.post("/api/putaway-rules/trial", json={
            "condition": {"is_cold_chain": 1}, "ctx": {"is_cold_chain": 0}})
        self.assertFalse(r.get_json()["matched"])

    def test_engine_and_flags_dict_regression(self):
        d = self.client.get("/api/putaway-rule-types").get_json()
        self.assertEqual(len(d["condition_dims"]), 13)
        self.assertEqual(len(d["step_types"]), 9)
        self.assertEqual(len(d["limit_keys"]), 8)
        self.assertEqual(len(d["rule_types"]), 2)


if __name__ == "__main__":
    unittest.main()
