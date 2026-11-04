"""全新数据库初始化完整性回归。

历史缺陷：customer 表在 init_db() 中段才创建，但 M8/M12 的列迁移用函数开头的
`tables` 快照判断 `if "customer" in tables`，导致全新库跳过迁移、/api/customers
插入时报 "no column named province"。本测试用独立子进程建全新库验证列齐全。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FreshDbSchemaTest(unittest.TestCase):
    def test_fresh_db_customer_columns_complete(self):
        db = os.path.join(tempfile.gettempdir(), "miniwms_fresh_schema.db")
        if os.path.exists(db):
            os.remove(db)
        env = os.environ.copy()
        env["MINIWMS_DB"] = db
        code = (
            "import os, sys, sqlite3, json\n"
            f"sys.path.insert(0, {_ROOT!r})\n"
            "import main\n"
            "main.init_db()\n"
            "c = sqlite3.connect(os.environ['MINIWMS_DB'])\n"
            "print(json.dumps([r[1] for r in c.execute('PRAGMA table_info(customer)')]))\n"
        )
        try:
            out = subprocess.check_output(
                [sys.executable, "-c", code], env=env, cwd=_ROOT,
                stderr=subprocess.STDOUT, timeout=60,
            )
            cols = set(json.loads(out.decode("utf-8").strip().splitlines()[-1]))
            for col in ("email", "level", "region", "province", "city", "district",
                        "detail_address", "business_scope"):
                self.assertIn(col, cols, f"全新库 customer 缺列: {col}")
        finally:
            if os.path.exists(db):
                os.remove(db)

    def test_fresh_db_asn_line_and_asn_columns(self):
        db = os.path.join(tempfile.gettempdir(), "miniwms_fresh_schema2.db")
        if os.path.exists(db):
            os.remove(db)
        env = os.environ.copy()
        env["MINIWMS_DB"] = db
        code = (
            "import os, sys, sqlite3, json\n"
            f"sys.path.insert(0, {_ROOT!r})\n"
            "import main\n"
            "main.init_db()\n"
            "c = sqlite3.connect(os.environ['MINIWMS_DB'])\n"
            "print(json.dumps({\n"
            "  'asn': [r[1] for r in c.execute('PRAGMA table_info(asn)')],\n"
            "  'asn_line': [r[1] for r in c.execute('PRAGMA table_info(asn_line)')],\n"
            "}))\n"
        )
        try:
            out = subprocess.check_output(
                [sys.executable, "-c", code], env=env, cwd=_ROOT,
                stderr=subprocess.STDOUT, timeout=60,
            )
            cols = json.loads(out.decode("utf-8").strip().splitlines()[-1])
            for col in ("customer_id", "customer_contact", "customer_phone",
                        "customer_address", "source_warehouse", "supplier_id"):
                self.assertIn(col, cols["asn"], f"全新库 asn 缺列: {col}")
            self.assertIn("extra", cols["asn_line"], "全新库 asn_line 缺 extra 快照列")
        finally:
            if os.path.exists(db):
                os.remove(db)


if __name__ == "__main__":
    unittest.main()
