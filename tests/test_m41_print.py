"""M41 打印模板 / 单据取值 / HTML 渲染 / Chrome PDF / 临时编辑不回写。

共享库约定同 M37–M40：closing 包裸连接；setUp 只清本模块相关表与必要主数据；
默认模板被删光后需显式调用 print_service.seed_templates() 重建基线，避免影响别的测试。
"""
import json
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
import print_service as PS  # noqa: E402
import scope as S  # noqa: E402
from flask import g  # noqa: E402

DB = os.environ["MINIWMS_DB"]


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


class M41PrintTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        with closing(_conn()) as c:
            c.execute("PRAGMA foreign_keys=OFF")
            for t in ("print_template", "dn_review_line", "dn_review_diff_log",
                      "dn_review", "dn_pick", "dn_line", "dn", "asn_line", "asn",
                      "customer", "product", "location", "owner"):
                try:
                    c.execute(f"DELETE FROM {t}")
                except sqlite3.OperationalError:
                    pass
            c.commit()
            PS.seed_templates(c)
            c.commit()
        self.owner = self._post("/api/owners", {
            "code": "M41-OW", "name": "M41 货主"})["owner"]["id"]
        self.other = self._post("/api/owners", {
            "code": "M41-OW9", "name": "M41 其他货主"})["owner"]["id"]
        self.pid = self._post("/api/products", {
            "sku": "M41-P", "name": "M41 物料", "owner_id": self.owner})["product"]["id"]
        self.lid = self._post("/api/locations", {
            "code": "M41-L", "name": "M41 库位", "owner_id": self.owner})["location"]["id"]
        self.customer_id = self._post("/api/customers", {
            "code": "M41-C", "name": "M41 客户", "owner_id": self.owner})["customer"]["id"]
        self.asn_id = self._post("/api/asns", {
            "code": "M41-ASN", "owner_id": self.owner,
            "lines": [{"product_id": self.pid, "expected_qty": 10}]})["asn"]["id"]
        self.dn_id = self._post("/api/dns", {
            "code": "M41-DN", "owner_id": self.owner, "customer_id": self.customer_id,
            "lines": [{"product_id": self.pid, "ordered_qty": 8}]})["dn"]["id"]
        with closing(_conn()) as c:
            c.execute("INSERT INTO dn_pick (dn_id, line_id, location_id, qty, batch_no) "
                      "VALUES (?, (SELECT id FROM dn_line WHERE dn_id=? LIMIT 1), ?, 8, 'LOT-DN')",
                      (self.dn_id, self.dn_id, self.lid))
            c.execute("INSERT INTO dn_review (code, dn_id, dn_code, owner_id, status, operator) "
                      "VALUES ('M41-RV',?,'M41-DN',?,'done','carol')",
                      (self.dn_id, self.owner))
            self.rv_id = c.execute(
                "SELECT id FROM dn_review WHERE code='M41-RV'").fetchone()[0]
            c.execute("INSERT INTO dn_review_line (review_id, dn_id, pick_id, product_id, "
                      "sku, product_name, location_id, location_code, batch_no, "
                      "expected_qty, actual_qty, result, operator) "
                      "VALUES (?,?,?,?,'M41-P','M41 物料',?,'M41-L','LOT-M41',8,8,'ok','carol')",
                      (self.rv_id, self.dn_id,
                       c.execute("SELECT id FROM dn_pick WHERE dn_id=?",
                                 (self.dn_id,)).fetchone()[0],
                       self.pid, self.lid))
            c.commit()

    # ---------- 辅助 ----------
    def _post(self, path, payload, status=201):
        r = self.client.post(path, json=payload)
        self.assertEqual(r.status_code, status, (path, r.get_json()))
        return r.get_json()

    def _get(self, path, status=200):
        r = self.client.get(path)
        self.assertEqual(r.status_code, status, (path, r.get_json()))
        return r.get_json()

    # ---------- 模板配置 ----------
    def test_01_dictionary(self):
        d = self._get("/api/print/dict")
        self.assertEqual(len(d["nodes"]), 6)
        self.assertEqual(len(d["sources"]), 3)
        self.assertEqual(len(d["blocks"]), 5)
        self.assertIn("qty", d["editable_fields"])
        self.assertTrue(d["no_install"] and d["temp_edit_only"])

    def test_02_default_templates(self):
        d = self._get("/api/print/templates")
        self.assertEqual(d["total"], 6)
        self.assertEqual(sum(1 for t in d["templates"] if t["is_active"]), 6)
        self.assertEqual(self._get("/api/print/templates?node=ship")["total"], 1)

    def test_03_create_and_activate(self):
        layout = {"blocks": [], "content_template": "{{code}} / {{lines_table}}"}
        made = self._post("/api/print/templates", {
            "node": C.PRINT_NODE_RECEIVE, "code": "M41-CUSTOM", "name": "自定义收货单",
            "is_active": 1, "layout_json": layout,
        })["template"]
        self.assertTrue(made["is_active"])
        rows = self._get("/api/print/templates?node=receive")["templates"]
        self.assertEqual(sum(1 for t in rows if t["is_active"]), 1)
        self.assertEqual(made["layout"]["content_template"], "{{code}} / {{lines_table}}")
        self._post(f"/api/print/templates/{made['id']}/activate", {}, status=200)
        self.assertEqual(self._get("/api/print/templates?node=receive")["total"], 2)

    def test_04_update_and_validation(self):
        t = self._get("/api/print/templates?node=pick")["templates"][0]
        upd = self.client.put(f"/api/print/templates/{t['id']}",
                              json={"name": "改过的拣货单", "version": "2.0"}).get_json()
        self.assertEqual(upd["template"]["name"], "改过的拣货单")
        self.assertEqual(upd["template"]["version"], "2.0")
        r = self._post("/api/print/templates", {
            "node": "bogus", "code": "X", "name": "x"}, status=400)
        self.assertEqual(r["code"], "PRINT_BAD_PARAM")
        r = self._post("/api/print/templates", {
            "node": C.PRINT_NODE_PICK, "code": t["code"], "name": "dup"}, status=400)
        self.assertEqual(r["code"], "PRINT_DUPLICATE_CODE")

    def test_05_delete_not_resurrect(self):
        t = self._get("/api/print/templates?node=label")["templates"][0]
        self.assertEqual(self.client.delete(
            f"/api/print/templates/{t['id']}").get_json()["deleted"], t["id"])
        with closing(_conn()) as c:
            PS.seed_templates(c)
        self.assertEqual(self._get("/api/print/templates?node=label")["total"], 0)
        # 删光后仍可用内置默认排版渲染
        d = self._get(f"/api/print/preview?node=label&source=asn&doc_id={self.asn_id}")
        self.assertIn("M41-ASN", d["html"])

    # ---------- 单据取值 ----------
    def test_06_asn_document(self):
        d = self._get(f"/api/print/preview?node=receive&source=asn&doc_id={self.asn_id}")
        doc = d["document"]
        self.assertEqual(doc["code"], "M41-ASN")
        self.assertEqual(len(doc["lines"]), 1)
        self.assertEqual(doc["fields"]["owner_name"], "M41 货主")
        self.assertIn("M41-ASN", d["html"])
        self.assertIn("M41 物料", d["html"])

    def test_07_dn_document(self):
        d = self._get(f"/api/print/preview?node=pick&source=dn&doc_id={self.dn_id}")
        doc = d["document"]
        self.assertEqual(doc["code"], "M41-DN")
        self.assertEqual(doc["lines"][0]["qty"], 8.0)
        self.assertEqual(doc["lines"][0]["location_code"], "M41-L")

    def test_08_review_document(self):
        d = self._get(f"/api/print/preview?node=check&source=dn_review&doc_id={self.rv_id}")
        doc = d["document"]
        self.assertEqual(doc["code"], "M41-RV")
        self.assertEqual(doc["fields"]["operator"], "carol")
        self.assertEqual(doc["lines"][0]["batch_no"], "LOT-M41")

    def test_09_doc_missing_and_bad_params(self):
        self.assertEqual(self._get("/api/print/preview?node=receive&source=asn&doc_id=999999",
                                   status=400)["code"], "PRINT_DOC_NOT_FOUND")
        self.assertEqual(self._get("/api/print/preview?node=receive&source=asn&doc_id=M41-ASN")
                         ["document"]["code"], "M41-ASN")
        for q in ("node=bogus&source=asn&doc_id=1",
                  "node=receive&source=bogus&doc_id=1",
                  "node=receive&source=asn&doc_id="):
            self.assertEqual(self._get("/api/print/preview?" + q, status=400)["code"],
                             "PRINT_BAD_PARAM")

    def test_10_owner_scope_isolation(self):
        with main.app.test_request_context():
            g.scope = {"owner_all": False, "owner_ids": [self.other]}
            with self.assertRaises(PS.PrintError):
                PS.load_document(main.get_db(), C.PRINT_NODE_RECEIVE,
                                 C.PRINT_SOURCE_ASN, self.asn_id)

    # ---------- 渲染 / 临时编辑 ----------
    def test_11_l1_template_overrides_blocks(self):
        t = self._post("/api/print/templates", {
            "node": C.PRINT_NODE_SHIP, "code": "M41-L1", "name": "L1 文本模板",
            "is_active": 1,
            "layout_json": {"blocks": [{"type": C.PRINT_BLOCK_TOTAL, "fields": []}],
                            "content_template": "单号 {{code}}｜{{lines_table}}"},
        })["template"]
        d = self._get(f"/api/print/preview?node=ship&source=dn&doc_id={self.dn_id}"
                      f"&template_id={t['id']}")
        self.assertIn("单号 M41-DN", d["html"])
        self.assertIn("LOT-DN", d["html"])  # 明细占位符被展开

    def test_12_temp_edits_not_written(self):
        with closing(_conn()) as c:
            before = c.execute("SELECT note FROM asn WHERE id=?",
                               (self.asn_id,)).fetchone()[0]
        overrides = {"fields": {"note": "临时备注", "receiver": "张三"},
                     "lines": [{"qty": 99, "line_note": "临时行备注"}]}
        d = self._get(f"/api/print/preview?node=receive&source=asn&doc_id={self.asn_id}"
                      "&overrides=" + __import__("urllib.parse", fromlist=["quote"]).quote(
                          json.dumps(overrides, ensure_ascii=False)))
        self.assertEqual(d["document"]["fields"]["note"], "临时备注")
        self.assertEqual(d["document"]["lines"][0]["qty"], 99)
        self.assertIn("张三", d["html"])
        with closing(_conn()) as c:
            after = c.execute("SELECT note FROM asn WHERE id=?",
                              (self.asn_id,)).fetchone()[0]
        self.assertEqual(before, after)

    def test_13_merge_whitelist(self):
        doc = self._get(f"/api/print/preview?node=receive&source=asn&doc_id={self.asn_id}")["document"]
        merged = PS.merge_overrides(doc, {"fields": {"code": "HACK", "note": "ok"},
                                          "lines": [{"qty": 7, "product_name": "HACK"}]})
        self.assertEqual(merged["fields"]["code"], "M41-ASN")
        self.assertEqual(merged["fields"]["note"], "ok")
        self.assertEqual(merged["lines"][0]["qty"], 7)
        self.assertEqual(merged["lines"][0]["product_name"], "M41 物料")

    # ---------- PDF ----------
    def test_14_pdf_bytes(self):
        r = self.client.get(f"/api/print/pdf?node=receive&source=asn&doc_id={self.asn_id}")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data.startswith(b"%PDF"))
        self.assertIn("application/pdf", r.content_type)
        self.assertIn("M41", r.headers.get("Content-Disposition", ""))

    def test_15_pdf_with_overrides(self):
        from urllib.parse import quote
        ov = quote(json.dumps({"fields": {"receiver": "李四"}}, ensure_ascii=False))
        r = self.client.get(f"/api/print/pdf?node=ship&source=dn&doc_id={self.dn_id}"
                            f"&overrides={ov}")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data.startswith(b"%PDF"))

    def test_16_no_business_writes(self):
        with closing(_conn()) as c:
            before = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("asn", "asn_line", "dn", "dn_line", "dn_pick",
                                "stock", "stock_movement", "dn_review")}
        self._get(f"/api/print/preview?node=receive&source=asn&doc_id={self.asn_id}")
        self.client.get(f"/api/print/pdf?node=pick&source=dn&doc_id={self.dn_id}")
        with closing(_conn()) as c:
            after = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                     for t in before}
        self.assertEqual(before, after)

    def test_17_chrome_detected(self):
        d = self._get(f"/api/print/preview?node=receive&source=asn&doc_id={self.asn_id}")
        self.assertTrue(d["chrome"])

    def test_18_template_id_param(self):
        t = self._post("/api/print/templates", {
            "node": C.PRINT_NODE_RECEIVE, "code": "M41-PICK-ID", "name": "指定模板",
            "layout_json": {"blocks": [], "content_template": "指定模板 {{code}}"},
        })["template"]
        d = self._get(f"/api/print/preview?node=receive&source=asn&doc_id={self.asn_id}"
                      f"&template_id={t['id']}")
        self.assertIn("指定模板 M41-ASN", d["html"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
