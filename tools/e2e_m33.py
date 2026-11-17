"""M33 可复跑端到端回归（独立端口 + 临时库，绝不碰 5100 demo）。

用法（Playwright 只装在 C:/Python311/python.exe，venv 里没有）：
    C:/Python311/python.exe tools/e2e_m33.py
    C:/Python311/python.exe tools/e2e_m33.py --port 5139 --keep

覆盖：八端点 API → 序列号台账页首屏 → 弹窗登记 → 出库 → 状态变更 → 流水详情 →
按货品统计 tab → 筛选 → 状态机拒绝 → 导航入口 → 库存零副作用 → I1/I2/I3 → 无 JS 错误。
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
PORT = 5139
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
            http("GET", "/api/serials/dict")
            return True
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("服务启动超时")


def snapshot(db):
    with closing(sqlite3.connect(db)) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("stock", "stock_movement", "stock_adjustment", "stock_batch")}


def seed_data():
    ow = http("POST", "/api/owners", {"code": "E33-OW", "name": "E33 货主"})["owner"]["id"]
    other = http("POST", "/api/owners", {"code": "E33-OW9", "name": "E33 其他货主"})["owner"]["id"]
    prod = http("POST", "/api/products", {
        "sku": "E33-P1", "name": "E33 序列号货品", "is_serial": 1})["product"]["id"]
    plain = http("POST", "/api/products", {
        "sku": "E33-P2", "name": "E33 普通货品"})["product"]["id"]

    rows = [
        ("E33-S1", prod, "B-01", ow),
        ("E33-S2", prod, "B-01", ow),
        ("E33-S3", prod, "B-02", ow),
        ("E33-S4", plain, "", ow),
        ("E33-S5", prod, "B-02", None),      # 全系统台账
        ("E33-S6", prod, "B-03", other),    # 他货主台账
    ]
    for no, pid, batch, oid in rows:
        http("POST", "/api/serials", {"serial_no": no, "product_id": pid,
                                      "batch_no": batch, "owner_id": oid})
    # 一个已出库、一个冻结
    lst = http("GET", "/api/serials?keyword=E33-S3")["serials"]
    http("POST", "/api/serials/" + str(lst[0]["id"]) + "/outbound",
         {"operator": "seed", "note": "已发货"})
    lst2 = http("GET", "/api/serials?keyword=E33-S2")["serials"]
    http("POST", "/api/serials/" + str(lst2[0]["id"]) + "/status",
         {"status": "frozen", "operator": "seed"})
    return ow, other, prod, plain


def run(port, keep):
    global BASE, PORT
    PORT = port
    BASE = f"http://127.0.0.1:{PORT}"
    tmp = tempfile.mkdtemp(prefix="miniwms_e2e_m33_")
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
        ow, other, prod, plain = seed_data()
        before = snapshot(db)

        # ── 后端八端点 ─────────────────────────────────────────────
        d = http("GET", "/api/serials/dict")
        check("字典状态/动作/来源", all([
            len(d["statuses"]) == 4, len(d["actions"]) == 3,
            len(d["ref_types"]) == 3]), len(d["statuses"]))
        check("字典声明不写库存", d["affects_stock"] is False and d["dn_binding"] is False)

        lst = http("GET", "/api/serials?page_size=100")
        check("列表六条", lst["total"] == 6, lst["total"])
        check("共享台账可见", any(s["owner_id"] is None for s in lst["serials"]))
        check("状态标签齐", {s["status"] for s in lst["serials"]} ==
              {"in_stock", "out", "frozen"}, {s["status"] for s in lst["serials"]})

        one = http("GET", "/api/serials?keyword=E33-S1")["serials"][0]
        detail = http("GET", "/api/serials/" + str(one["id"]))["serial"]
        check("详情含建档流水", len(detail["transactions"]) == 1 and
              detail["transactions"][0]["action"] == "in",
              len(detail["transactions"]))
        check("详情带货品", detail["product_code"] == "E33-P1", detail["product_code"])

        upd = http("PUT", "/api/serials/" + str(one["id"]),
                   {"batch_no": "B-99"})["serial"]
        check("编辑非状态字段", upd["batch_no"] == "B-99" and upd["status"] == "in_stock")

        out = http("POST", "/api/serials/" + str(one["id"]) + "/outbound",
                   {"operator": "tester", "note": "发货", "ref_type": "dn",
                    "ref_id": 7})["serial"]
        check("出库端点", out["status"] == "out" and bool(out["out_date"]))
        check("出库流水带来源", out["transactions"][-1]["ref_type"] == "dn" and
              out["transactions"][-1]["ref_id"] == 7)

        check("重复出库 400",
              http("POST", "/api/serials/" + str(one["id"]) + "/outbound", {},
                   expect=(400,))["code"] == "SERIAL_BAD_TRANSITION")
        check("报废后再流转 400",
              http("POST", "/api/serials/" + str(one["id"]) + "/status",
                   {"status": "in_stock"}, expect=(400,))["code"] == "SERIAL_BAD_TRANSITION")
        check("重复序列号 400",
              http("POST", "/api/serials", {"serial_no": "E33-S1", "product_id": prod},
                   expect=(400,))["code"] == "SERIAL_DUPLICATE")
        check("缺货品 400",
              http("POST", "/api/serials", {"serial_no": "E33-NP"},
                   expect=(400,))["code"] == "SERIAL_BAD_PARAM")

        st = http("GET", "/api/serials/stats")
        check("统计条数", st["total"] == 6, st["total"])
        item = [i for i in st["items"] if i["product_id"] == prod][0]
        check("统计分类计数", item["total"] == 5 and item["in_stock"] == 2 and
              item["out"] == 2 and item["frozen"] == 1, item)

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
            expected_4xx = []
            page.on("dialog", lambda d: d.accept())
            page.on("pageerror", lambda e: js_errors.append(str(e)))
            page.on("response", lambda r: bad_responses.append((r.status, r.url))
                    if r.status >= 400 and "/api/" in r.url and "favicon" not in r.url
                    and not any(x in r.url for x in expected_4xx) else None)

            page.goto(BASE + "/serials.html", wait_until="networkidle")
            check("台账页标题", "序列号" in page.title())
            check("台账列表六行", page.locator("#tbody tr").count() == 6,
                  page.locator("#tbody tr").count())
            check("导航含序列号台账", page.locator("a[data-href='/serials.html']").count() == 1)
            body = page.locator("#tbody").inner_text()
            check("列表显示状态", "在库" in body and "已出库" in body and "冻结" in body)

            # 筛选
            page.locator("#kw").fill("E33-S1")
            page.locator("#btnQuery").click()
            page.wait_for_timeout(300)
            check("前端筛选", page.locator("#tbody tr").count() == 1,
                  page.locator("#tbody tr").count())
            page.locator("#kw").fill("")
            page.locator("#statusFilter").select_option("frozen")
            page.locator("#btnQuery").click()
            page.wait_for_timeout(300)
            check("状态筛选", page.locator("#tbody tr").count() == 1,
                  page.locator("#tbody tr").count())
            page.locator("#statusFilter").select_option("")
            page.locator("#btnQuery").click()
            page.wait_for_timeout(300)

            # 弹窗登记
            page.get_by_role("button", name="+ 登记序列号").click()
            page.wait_for_timeout(250)
            page.locator('.modal [name="serial_no"]').fill("E33-UI1")
            page.locator('.modal [name="product_id"]').select_option(str(prod))
            page.locator('.modal [name="batch_no"]').fill("B-UI")
            page.get_by_role("button", name="创建", exact=True).click()
            page.wait_for_timeout(500)
            check("弹窗登记序列号", "E33-UI1" in page.locator("#tbody").inner_text())
            check("登记后七行", page.locator("#tbody tr").count() == 7,
                  page.locator("#tbody tr").count())

            # 出库
            row = page.locator("#tbody tr", has_text="E33-UI1").first
            row.get_by_role("button", name="出库").click()
            page.wait_for_timeout(250)
            page.locator('.modal [name="operator"]').fill("e2e")
            page.get_by_role("button", name="确认出库", exact=True).click()
            page.wait_for_timeout(500)
            check("界面出库生效", "已出库" in
                  page.locator("#tbody tr", has_text="E33-UI1").first.inner_text(),
                  page.locator("#tbody tr", has_text="E33-UI1").first.inner_text()[:80])

            # 流水详情
            row2 = page.locator("#tbody tr", has_text="E33-UI1").first
            row2.get_by_role("button", name="流水").click()
            page.wait_for_timeout(400)
            modal_txt = page.locator(".overlay .modal").last.inner_text()
            check("流水弹窗内容", "序列号流水" in modal_txt and "出库" in modal_txt,
                  modal_txt[:80])
            page.locator(".overlay .modal button", has_text="关闭").last.click()
            page.wait_for_timeout(200)

            # 统计 tab
            page.locator(".tabbar .tab", has_text="按货品统计").click()
            page.wait_for_timeout(500)
            check("统计 tab 激活", page.locator("#panel-stats").is_visible())
            check("统计表有行", page.locator("#statsBody tr").count() >= 2,
                  page.locator("#statsBody tr").count())
            check("统计含缺口列", "待登记缺口" in page.locator("#panel-stats").inner_text())

            after = snapshot(db)
            check("台账操作不写库存", before == after, (before, after))
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
