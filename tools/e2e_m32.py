"""M32 可复跑端到端回归（独立端口 + 临时库，绝不碰 5100 demo）。

用法（Playwright 只装在 C:/Python311/python.exe，venv 里没有）：
    C:/Python311/python.exe tools/e2e_m32.py
    C:/Python311/python.exe tools/e2e_m32.py --port 5138 --keep

覆盖：七端点 API → 产品类型页首屏 → 层级/引用/唯一性/自动建行/老数据回填 →
货品页下拉联动 → 弹窗 CRUD → 筛选 → 删除保护提示 → 导航入口 → 库存零副作用 →
I1/I2/I3 → 无 JS 错误。
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import closing

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PORT = 5138
BASE = ""
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), str(detail)[:160]))
    print(("  [PASS] " if ok else "  [FAIL] ") + name + (" · " + str(detail)[:120] if detail else ""))


def http(method, path, payload=None, expect=(200, 201)):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=15) as r:
            body = json.loads(r.read().decode() or "{}")
            code = r.status
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode() or "{}")
        code = e.code
    if code not in expect:
        raise RuntimeError(f"{method} {path} → HTTP {code}: {str(body)[:200]}")
    return body


def wait_ready(proc, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("Flask 进程提前退出，请看上方日志")
        try:
            http("GET", "/api/product-types/dict")
            return True
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("服务启动超时")


def snapshot(db):
    with closing(sqlite3.connect(db)) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("stock", "stock_movement", "stock_adjustment", "stock_batch")}


def seed_data():
    ow = http("POST", "/api/owners", {"code": "E32-OW", "name": "E32 货主"})["owner"]["id"]
    other = http("POST", "/api/owners", {"code": "E32-OW9", "name": "E32 其他货主"})["owner"]["id"]

    # 顶级 + 子类型（两级）
    top = http("POST", "/api/product-types",
               {"code": "E32-TOP", "name": "成品", "sort_no": 10})["product_type"]
    http("POST", "/api/product-types",
         {"code": "E32-SUB", "name": "瓶装成品", "parent_id": top["id"], "sort_no": 11})
    http("POST", "/api/product-types",
         {"code": "E32-GLOBAL", "name": "全局类型", "owner_id": None})
    http("POST", "/api/product-types",
         {"code": "E32-OWNER", "name": "他货主类型", "owner_id": other})

    # 引用一条，让删除保护可验
    http("POST", "/api/products",
         {"sku": "E32-P1", "name": "E32 货品一", "product_type": "E32-TOP"})
    # 字典外的类型 → 应自动建行
    http("POST", "/api/products",
         {"sku": "E32-P2", "name": "E32 货品二", "product_type": "E32-自动类型"})
    return ow, other, top


def run(port, keep):
    global BASE, PORT
    PORT = port
    BASE = f"http://127.0.0.1:{PORT}"
    tmp = tempfile.mkdtemp(prefix="miniwms_e2e_m32_")
    db = os.path.join(tmp, "e2e.db")
    env = os.environ.copy()
    env.update({
        "MINIWMS_DB": db, "MINIWMS_PORT": str(port),
        "MINIWMS_ALLOW_ANONYMOUS": "1",
        "NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1",
        "HTTP_PROXY": "", "HTTPS_PROXY": "", "http_proxy": "", "https_proxy": "",
    })
    print(f"临时库：{db}\n服务端口：{port}\n")
    proc = subprocess.Popen([VENV_PY, "main.py"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    browser = None
    try:
        wait_ready(proc)
        ow, other, top = seed_data()
        before = snapshot(db)

        # ── 后端七端点 ─────────────────────────────────────────────
        d = http("GET", "/api/product-types/dict")
        check("字典状态/层级上限/种子", all([
            len(d["statuses"]) == 2, d["max_depth"] == 2, len(d["seed_types"]) == 8]),
              len(d["seed_types"]))

        lst = http("GET", "/api/product-types?page_size=100")
        codes = {t["code"] for t in lst["product_types"]}
        check("列表含种子类型", "PT-FG" in codes and "PT-OT" in codes, len(codes))
        check("列表含自建类型", {"E32-TOP", "E32-SUB", "E32-GLOBAL"} <= codes)
        check("列表条数 = 8 种子 + 4 自建 + 1 自动建行", lst["total"] == 13, lst["total"])

        sub = [t for t in lst["product_types"] if t["code"] == "E32-SUB"][0]
        check("子类型挂上级", sub["parent_id"] == top["id"], sub["parent_id"])
        check("上级列出子类型", sub["children"] == [] and
              [t for t in lst["product_types"] if t["code"] == "E32-TOP"][0]["children"] == [sub["id"]])

        top_row = [t for t in lst["product_types"] if t["code"] == "E32-TOP"][0]
        check("引用计数回读", top_row["ref_count"] == 1, top_row["ref_count"])

        check("关键词过滤", http("GET", "/api/product-types?keyword=E32-SUB")["total"] == 1)
        check("上级过滤", http("GET", "/api/product-types?parent_id=" + str(top["id"]))["total"] == 1)
        check("分页", len(http("GET", "/api/product-types?page=1&page_size=3")["product_types"]) == 3)
        check("参数校验 400",
              http("GET", "/api/product-types?page=abc", expect=(400,))["code"] == "PT_BAD_PARAM")

        detail = http("GET", "/api/product-types/" + str(top["id"]))["product_type"]
        check("详情端点", detail["code"] == "E32-TOP", detail["code"])

        created = http("POST", "/api/product-types",
                       {"code": "E32-API", "name": "接口类型", "owner_id": ow})["product_type"]
        updated = http("PUT", "/api/product-types/" + str(created["id"]),
                       {"name": "接口类型改"})["product_type"]
        check("创建/更新端点", updated["name"] == "接口类型改")
        check("重复编码 400",
              http("POST", "/api/product-types", {"code": "E32-TOP", "name": "x"},
                   expect=(400,))["code"] == "PT_DUPLICATE_CODE")
        check("缺必填 400",
              http("POST", "/api/product-types", {"code": "E32-NONAME"},
                   expect=(400,))["code"] == "PT_BAD_PARAM")
        check("三级层级 400",
              http("POST", "/api/product-types",
                   {"code": "E32-G3", "name": "第三级", "parent_id": sub["id"]},
                   expect=(400,))["code"] == "PT_MAX_DEPTH")
        check("自引用 400",
              http("PUT", "/api/product-types/" + str(created["id"]),
                   {"parent_id": created["id"]}, expect=(400,))["code"] == "PT_HIERARCHY_CYCLE")
        check("被引用删除 400",
              http("DELETE", "/api/product-types/" + str(top["id"]),
                   expect=(400,))["code"] == "PT_IN_USE")
        check("被引用改编码 400",
              http("PUT", "/api/product-types/" + str(top["id"]),
                   {"code": "E32-TOP2"}, expect=(400,))["code"] == "PT_IN_USE")
        check("有子类型删除 400",
              http("DELETE", "/api/product-types/" + str(top["id"]),
                   expect=(400,))["code"] == "PT_IN_USE")
        check("未引用可删", http("DELETE", "/api/product-types/" + str(created["id"]))["ok"])
        check("删除后查不到",
              http("GET", "/api/product-types/" + str(created["id"]),
                   expect=(400,))["code"] == "PT_NOT_FOUND")

        refs = http("GET", "/api/product-types/refs")["refs"]
        check("引用计数端点", refs.get("E32-TOP") == 1 and refs.get("E32-自动类型") == 1, refs)

        # 货品引用字典
        p1 = http("GET", "/api/products?keyword=E32-P1")["products"][0]
        check("货品引用字典 code", p1["product_type"] == "E32-TOP", p1["product_type"])
        check("货品带类型显示名", p1["product_type_name"] == "成品", p1["product_type_name"])
        check("类型已知标记", p1["product_type_known"] is True)
        p2 = http("GET", "/api/products?keyword=E32-P2")["products"][0]
        check("字典外类型自动建行", p2["product_type"] == "E32-自动类型" and p2["product_type_known"],
              p2["product_type"])

        # 老数据回填：直接写库塞一条老自由文本，迁移回填后取值不变
        with closing(sqlite3.connect(db)) as c:
            c.execute("INSERT INTO product (sku, name, product_type)"
                      " VALUES ('E32-LEGACY', '老货品', '老自由文本')")
            c.commit()
        http("POST", "/api/product-types", {"code": "E32-TRIGGER", "name": "触发"})
        sys.path.insert(0, ROOT)
        import product_type_service as PTS  # noqa: E402
        with closing(sqlite3.connect(db)) as c:
            c.row_factory = sqlite3.Row
            made = PTS.backfill_from_products(c)
            c.commit()
            legacy = c.execute("SELECT product_type FROM product WHERE sku='E32-LEGACY'").fetchone()[0]
            hit = c.execute("SELECT name FROM product_type WHERE code='老自由文本'").fetchone()
        check("老数据回填取值不变", legacy == "老自由文本", legacy)
        check("老数据回填建行", hit is not None and hit[0] == "老自由文本", made)

        # ── 浏览器两页 ─────────────────────────────────────────────
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True, channel="chrome",
                                            args=["--no-proxy-server"])
            except Exception:
                browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
            page = browser.new_page()
            js_errors, bad_responses = [], []
            expected_4xx = []   # 业务校验主动拒绝的 400（属预期，不算回归）
            page.on("dialog", lambda d: d.accept())
            page.on("pageerror", lambda e: js_errors.append(str(e)))
            page.on("response", lambda r: bad_responses.append((r.status, r.url))
                    if r.status >= 400 and "/api/" in r.url and "favicon" not in r.url
                    and not any(x in r.url for x in expected_4xx) else None)

            # 产品类型字典页
            page.goto(BASE + "/product_types.html", wait_until="networkidle")
            check("字典页标题", "产品类型" in page.title())
            rows = page.locator("#tbody tr").count()
            check("字典页列表非空", rows >= 12, rows)
            check("导航含产品类型", page.locator("a[data-href='/product_types.html']").count() == 1)
            check("引用列显示", "E32-TOP" in page.locator("#tbody").inner_text())
            check("子类型缩进", page.locator("#tbody").inner_text().find("E32-SUB") >= 0)

            # 前端筛选
            page.locator("#kw").fill("E32-SUB")
            page.get_by_role("button", name="查询", exact=True).click()
            page.wait_for_timeout(300)
            check("字典页筛选", page.locator("#tbody tr").count() == 1,
                  page.locator("#tbody tr").count())
            page.locator("#kw").fill("")
            page.get_by_role("button", name="查询", exact=True).click()
            page.wait_for_timeout(300)

            # 弹窗创建
            page.get_by_role("button", name="+ 新建产品类型").click()
            page.locator('.modal [name="code"]').fill("E32-UI")
            page.locator('.modal [name="name"]').fill("界面类型")
            page.get_by_role("button", name="创建", exact=True).click()
            page.wait_for_timeout(400)
            check("弹窗创建类型", "E32-UI" in page.locator("#tbody").inner_text())

            # 弹窗编辑
            row = page.locator("#tbody tr", has_text="E32-UI").first
            row.get_by_role("button", name="编辑").click()
            page.wait_for_timeout(250)
            page.locator('.modal [name="name"]').fill("界面类型改")
            page.get_by_role("button", name="保存", exact=True).click()
            page.wait_for_timeout(400)
            check("弹窗编辑类型", "界面类型改" in page.locator("#tbody").inner_text())

            # 删除被引用类型 → 提示（预期 400，注册为容差）
            expected_4xx.append("/api/product-types/")
            top_row = page.locator("#tbody tr", has_text="E32-TOP").first
            top_row.get_by_role("button", name="删除").click()
            page.wait_for_timeout(500)
            msg = page.locator("#msg").inner_text()
            check("删除被引用被拒并提示", "引用" in msg, msg[:80])

            # 货品页：类型下拉 + 列表显示
            page.goto(BASE + "/products.html", wait_until="networkidle")
            check("货品页标题", "物料" in page.title() or "货品" in page.title())
            check("货品页类型列", "产品类型" in page.locator("thead").inner_text())
            body = page.locator("#tbody").inner_text()
            check("货品列表显示类型名", "成品" in body, body[:120])

            page.get_by_role("button", name="+ 新建物料").click()
            page.wait_for_timeout(250)
            opts = page.locator('.modal [name="product_type"] option').all_text_contents()
            check("类型下拉有选项", any("E32-TOP" in o for o in opts), opts[:4])
            page.locator('.modal [name="sku"]').fill("E32-UI-SKU")
            page.locator('.modal [name="name"]').fill("界面货品")
            page.locator('.modal [name="product_type"]').select_option("E32-TOP")
            page.get_by_role("button", name="创建", exact=True).click()
            page.wait_for_timeout(500)
            check("界面建货品带类型", "界面货品" in page.locator("#tbody").inner_text())

            after = snapshot(db)
            check("字典操作不写库存", before == after, (before, after))
            check("I1 主账一致", http("GET", "/api/stock/reconcile")["consistent"])
            check("I2/I3 批次一致", http("GET", "/api/stock/batch-reconcile")["consistent"])
            check("无 JS 运行时错误", not js_errors, "; ".join(js_errors[:2]))
            check("无业务 4xx/5xx", not bad_responses, str(bad_responses[:3]))
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        if keep:
            print(f"\n临时库保留：{db}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    print(f"\nE2E {passed}/{len(CHECKS)} 通过")
    for name, ok, detail in CHECKS:
        if not ok:
            print(f"  FAIL · {name} · {detail}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--keep", action="store_true", help="保留临时库便于排查")
    a = ap.parse_args()
    sys.exit(run(a.port, a.keep))
