"""M38 可复跑端到端回归（独立端口 + 临时库，绝不碰 5100 demo）。

用法（Playwright 只装在 C:/Python311/python.exe，venv 里没有）：
    C:/Python311/python.exe tools/e2e_m38.py
    C:/Python311/python.exe tools/e2e_m38.py --port 5132 --keep

覆盖：页面首屏 → 候选单列表 → 拆单（数量守恒 / 源单扣减 / 库存零变动）→
越量与已选不可拆 → 合单（同客户汇总 / 源单作废 / 守恒）→ 跨货主与跨客户拒绝 →
留痕查询与血缘 → 导航入口 → I1/I2/I3 → 无 JS 错误与业务 4xx。
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
PORT = 5132
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
            http("GET", "/api/dn-order/dict")
            return True
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("服务启动超时")


def inventory_snapshot(db):
    with closing(sqlite3.connect(db)) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("stock", "stock_movement", "stock_adjustment", "stock_batch")}


def seed_docs():
    ow = http("POST", "/api/owners", {"code": "E38-OW", "name": "E38 货主"})["owner"]["id"]
    cu = http("POST", "/api/customers", {
        "code": "E38-CU", "name": "E38 客户", "owner_id": ow})["customer"]["id"]
    pids = [http("POST", "/api/products", {
        "sku": f"E38-P{i}", "name": f"物料{i}", "owner_id": ow})["product"]["id"]
        for i in (1, 2, 3)]

    def dn(code, lines):
        return http("POST", "/api/dns", {
            "code": code, "owner_id": ow, "customer_id": cu,
            "lines": [{"product_id": p, "ordered_qty": q} for p, q in lines]})

    d1 = dn("E38-DN-1", [(pids[0], 10), (pids[1], 5)])
    d2 = dn("E38-DN-2", [(pids[0], 3), (pids[2], 7)])
    d3 = dn("E38-DN-3", [(pids[1], 4)])
    return ow, cu, pids, d1, d2, d3


def run(port, keep):
    global BASE, PORT
    PORT = port
    BASE = f"http://127.0.0.1:{PORT}"
    tmp = tempfile.mkdtemp(prefix="miniwms_e2e_m38_")
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
        ow, cu, pids, d1, d2, d3 = seed_docs()
        before = inventory_snapshot(db)

        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True, channel="chrome",
                                            args=["--no-proxy-server"])
            except Exception:
                browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
            page = browser.new_page()
            js_errors, bad_responses = [], []
            page.on("pageerror", lambda e: js_errors.append(str(e)))
            page.on("response", lambda r: bad_responses.append((r.status, r.url))
                    if r.status >= 400 and "/api/" in r.url and "favicon" not in r.url else None)
            page.on("dialog", lambda d: d.accept())

            # ── 首屏 ────────────────────────────────────────────────
            page.goto(BASE + "/dn_order.html", wait_until="networkidle")
            check("拆合页标题", "拆单合单" in page.title())
            check("拆分模式下拉 2 项", page.locator("#splitMode option").count() == 2,
                  page.locator("#splitMode option").count())
            check("动作下拉 3 项（含全部）", page.locator("#logAction option").count() == 3,
                  page.locator("#logAction option").count())
            check("候选列表三行", page.locator("#candBody tr").count() == 3,
                  page.locator("#candBody tr").count())
            check("源单下拉三张", page.locator("#splitSource option").count() == 3,
                  page.locator("#splitSource option").count())
            check("拆单明细行已渲染", page.locator("#splitLines .split-line").count() >= 1,
                  page.locator("#splitLines .split-line").count())
            check("导航含拆单合单", page.locator("a[data-href='/dn_order.html']").count() == 1)

            # ── 拆单（UI）──────────────────────────────────────────
            page.locator("#splitSource").select_option(str(d1["dn"]["id"]))
            page.locator("#splitMode").select_option("by_qty")
            page.wait_for_timeout(200)
            check("选中源单后明细行 2 条", page.locator("#splitLines .split-line").count() == 2,
                  page.locator("#splitLines .split-line").count())
            page.locator(f"#qty_{pids[0]}").fill("4")
            page.locator("#splitNote").fill("E2E 拆出一部分")
            page.locator("button:has-text('确认拆单')").click()
            page.wait_for_timeout(600)
            split_logs = http("GET", "/api/dn-order/log?action=split")["logs"]
            check("拆单留痕已写", len(split_logs) == 1, len(split_logs))
            child_code = split_logs[0]["target_codes"]
            child = [x for x in http("GET", "/api/dns")["dns"] if x["code"] == child_code][0]
            check("子单数量正确", abs(child["total_qty"] - 4) < 1e-9, child["total_qty"])
            src1 = http("GET", f"/api/dns/{d1['dn']['id']}")["dn"]
            check("源单扣减到 11", abs(src1["total_qty"] - 11) < 1e-9, src1["total_qty"])
            check("源单仍 open", src1["status"] == "open", src1["status"])
            check("子单溯源标记", child["origin_action"] == "split" and child["origin_parent_id"] == d1["dn"]["id"])

            # ── 越量拒绝（API 边界）────────────────────────────────
            bad = http("POST", "/api/dn-order/split", {
                "source_id": d1["dn"]["id"],
                "parts": [{"lines": [{"product_id": pids[0], "qty": 999}]}]},
                expect=(400,))
            check("越量拆单被拒", bad.get("code") == "QTY_EXCEEDED", bad.get("error"))

            # ── 合单（UI，勾选两张）────────────────────────────────
            page.locator(f"input.chk[data-id='{d2['dn']['id']}']").check()
            page.locator(f"input.chk[data-id='{d3['dn']['id']}']").check()
            page.locator("#mergeNote").fill("E2E 两单合并")
            page.locator("button:has-text('确认合单')").click()
            page.wait_for_timeout(600)
            merge_logs = http("GET", "/api/dn-order/log?action=merge")["logs"]
            check("合单留痕已写", len(merge_logs) == 1, len(merge_logs))
            mtarget_code = merge_logs[0]["target_codes"]
            mtarget = [x for x in http("GET", "/api/dns")["dns"] if x["code"] == mtarget_code][0]
            # d2=10(3+7) + d3=4 → 14
            check("合单总量守恒 14", abs(mtarget["total_qty"] - 14) < 1e-9, mtarget["total_qty"])
            check("合单源单作废", http("GET", f"/api/dns/{d2['dn']['id']}")["dn"]["status"] == "cancelled"
                  and http("GET", f"/api/dns/{d3['dn']['id']}")["dn"]["status"] == "cancelled")
            check("合单子项按物料汇总", http("GET", f"/api/dns/{mtarget['id']}")["lines"][0]["ordered_qty"] in (3, 4, 7))

            # ── 跨货主 / 跨客户拒绝（API 边界）──────────────────────
            ow2 = http("POST", "/api/owners", {"code": "E38-OW2", "name": "E38 货主二"})["owner"]["id"]
            cu2 = http("POST", "/api/customers", {
                "code": "E38-CU2", "name": "E38 客户二", "owner_id": ow2})["customer"]["id"]
            pd2 = http("POST", "/api/products", {
                "sku": "E38-PX", "name": "跨货主物料", "owner_id": ow2})["product"]["id"]
            dx = http("POST", "/api/dns", {
                "code": "E38-DN-X", "owner_id": ow2, "customer_id": cu2,
                "lines": [{"product_id": pd2, "ordered_qty": 2}]})
            cross = http("POST", "/api/dn-order/merge", {
                "source_ids": [d1["dn"]["id"], dx["dn"]["id"]]}, expect=(400,))
            check("跨货主合单被拒", cross.get("code") == "OWNER_MISMATCH", cross.get("error"))
            cu3 = http("POST", "/api/customers", {
                "code": "E38-CU3", "name": "E38 客户三", "owner_id": ow})["customer"]["id"]
            dy = http("POST", "/api/dns", {
                "code": "E38-DN-Y", "owner_id": ow, "customer_id": cu3,
                "lines": [{"product_id": pids[0], "ordered_qty": 2}]})
            cross2 = http("POST", "/api/dn-order/merge", {
                "source_ids": [d1["dn"]["id"], dy["dn"]["id"]]}, expect=(400,))
            check("跨客户合单被拒", cross2.get("code") == "CUSTOMER_MISMATCH", cross2.get("error"))

            # ── 留痕与血缘（UI）────────────────────────────────────
            page.locator("#logAction").select_option("")
            page.locator("#logKeyword").fill("E38")
            page.locator("button:has-text('查询')").click()
            page.wait_for_timeout(400)
            check("留痕查询有数据", page.locator("#logBody tr").count() >= 2,
                  page.locator("#logBody tr").count())
            logs = http("GET", "/api/dn-order/log")
            check("留痕总数 2", logs["total"] == 2, logs["total"])
            check("源单血缘可查", len(http("GET", f"/api/dn-order/{d1['dn']['id']}/lineage")["logs"]) == 1)
            check("子单血缘可查", len(http("GET", f"/api/dn-order/{child['id']}/lineage")["logs"]) == 1)

            # ── 库存与一致性 ───────────────────────────────────────
            after = inventory_snapshot(db)
            check("拆合未改库存与流水", before == after, (before, after))
            check("I1 主账一致", http("GET", "/api/stock/reconcile")["consistent"])
            check("I2/I3 批次一致", http("GET", "/api/stock/batch-reconcile")["consistent"])
            check("无 JS 运行时错误", not js_errors, "; ".join(js_errors[:2]))
            check("无业务 4xx/5xx（预期错误除外）",
                  all(s in (400,) for s, _ in bad_responses), str(bad_responses[:3]))
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
