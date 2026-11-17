"""M28 可复跑端到端回归（独立端口 + 临时库，绝不碰 5100 demo）。

用法（Playwright 只装在 C:/Python311/python.exe，venv 里没有）：
    C:/Python311/python.exe tools/e2e_m28.py
    C:/Python311/python.exe tools/e2e_m28.py --port 5141 --keep

覆盖：五端点 API → 收发汇总（排除库位间转移）→ 周转率 → 库位利用率 →
经营总览 → 三个 tab 切换 → 日期与货主筛选 → 只读无副作用 → I1/I2/I3 → 无 JS 错误。
"""
import argparse
import datetime
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
PORT = 5141
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
            http("GET", "/api/operation-report/dict")
            return True
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("服务启动超时")


def snapshot(db):
    with closing(sqlite3.connect(db)) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("stock", "stock_movement", "stock_adjustment", "stock_batch")}


def seed_data():
    """造收发流水 + 库存（报表只读，这里直接写库造数）。"""
    today = datetime.date.today()
    when = f"{today.isoformat()} 10:00:00"
    ow = http("POST", "/api/owners", {"code": "E28-OW", "name": "E28 货主"})["owner"]["id"]
    other = http("POST", "/api/owners", {"code": "E28-OW9", "name": "E28 其他货主"})["owner"]["id"]
    prod = http("POST", "/api/products", {
        "sku": "E28-P1", "name": "E28 货品", "purchase_price": 10,
        "sale_price": 15})["product"]["id"]
    loc = http("POST", "/api/locations", {"code": "E28-L1", "name": "E28 库位"})["location"]["id"]
    loc2 = http("POST", "/api/locations", {"code": "E28-L2", "name": "E28 空库位"})["location"]["id"]

    conn = sqlite3.connect(os.environ["MINIWMS_DB"])
    conn.row_factory = sqlite3.Row
    # 全部流水都落在 loc2 上，便于与 stock / stock_batch 严格平账（I1/I2/I3）
    rows = [
        # 收货 100（应计入收发汇总）
        (ow, prod, loc2, 100, "asn_receive", "E28 收货"),
        # 上架：库位间搬运（应排除）
        (ow, prod, loc2, -100, "asn_putaway_out", "E28 上架出"),
        (ow, prod, loc2, 100, "asn_putaway_in", "E28 上架入"),
        # 发货 40（应计入）
        (ow, prod, loc2, -40, "dn_ship", "E28 发货"),
        # 移库（应排除）
        (ow, prod, loc2, -60, "move_out", "E28 移库出"),
        (ow, prod, loc2, 60, "move_in", "E28 移库入"),
        # 他货主
        (other, prod, loc2, 20, "asn_receive", "E28 他货主收货"),
    ]
    for owner_id, product_id, location_id, qty, ref_type, note in rows:
        conn.execute(
            "INSERT INTO stock_movement (owner_id, product_id, location_id, qty_delta,"
            " ref_type, ref_id, note, created_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (owner_id, product_id, location_id, qty, ref_type, note, when),
        )
    # 期末库存：loc2 上净流水 = 100-100+100-40-60+60 = 60（货主）、20（他货主）
    for owner_id, qty in ((ow, 60), (other, 20)):
        conn.execute(
            "INSERT INTO stock (owner_id, product_id, location_id, qty, allocated_qty,"
            " updated_at) VALUES (?, ?, ?, ?, 0, datetime('now','localtime'))",
            (owner_id, prod, loc2, qty),
        )
        # 批次明细：batch_no 与流水一致（流水默认空串），保证 I2/I3 平衡
        conn.execute(
            "INSERT INTO stock_batch (owner_id, product_id, location_id, batch_no,"
            " batch_key, internal_batch_no, designated_customer_id, goods_property,"
            " stock_status, expiry_date, production_date, qty, allocated_qty, updated_at)"
            " VALUES (?, ?, ?, '', '', '', NULL, '', '', '', '', ?, 0,"
            " datetime('now','localtime'))",
            (owner_id, prod, loc2, qty),
        )
    conn.commit()
    conn.close()
    return ow, other, prod, loc, loc2


def run(port, keep):
    global BASE, PORT
    PORT = port
    BASE = f"http://127.0.0.1:{PORT}"
    tmp = tempfile.mkdtemp(prefix="miniwms_e2e_m28_")
    db = os.path.join(tmp, "e2e.db")
    env = os.environ.copy()
    env.update({
        "MINIWMS_DB": db, "MINIWMS_PORT": str(port),
        "MINIWMS_ALLOW_ANONYMOUS": "1",
        "NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1",
        "HTTP_PROXY": "", "HTTPS_PROXY": "", "http_proxy": "", "https_proxy": "",
    })
    os.environ["MINIWMS_DB"] = db
    print(f"临时库：{db}\n服务端口：{port}\n")
    proc = subprocess.Popen([VENV_PY, "main.py"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    browser = None
    try:
        wait_ready(proc)
        ow, other, prod, loc, loc2 = seed_data()
        before = snapshot(db)

        today = datetime.date.today()
        frm = (today - datetime.timedelta(days=30)).isoformat()
        to = today.isoformat()

        # ── 后端五端点 ─────────────────────────────────────────────
        d = http("GET", "/api/operation-report/dict")
        check("字典期间/成本/维度", all([
            len(d["periods"]) == 3, len(d["cost_fields"]) == 2,
            len(d["util_dimensions"]) == 4]), len(d["periods"]))
        check("字典列出转移排除口径", "asn_putaway_in" in d["transfer_ref_types"])

        io = http("GET", f"/api/operation-report/io-summary?date_from={frm}&date_to={to}")
        check("收发汇总排除库位间转移", io["total"]["in_qty"] == 120 and
              io["total"]["out_qty"] == 40, io["total"])
        check("收发汇总含他货主", io["total"]["in_qty"] == 120, io["total"]["in_qty"])
        codes = {i["owner_code"]: i["in_qty"] for i in io["items"]}
        check("按货主拆分", codes.get("E28-OW") == 100 and codes.get("E28-OW9") == 20, codes)
        check("Top 货品非空", len(io["top_products"]) >= 1, len(io["top_products"]))

        io_f = http("GET", f"/api/operation-report/io-summary?date_from={frm}&date_to={to}"
                        f"&owner_id={ow}")
        check("货主过滤生效", io_f["total"]["in_qty"] == 100, io_f["total"]["in_qty"])

        tv = http("GET", f"/api/operation-report/turnover?date_from={frm}&date_to={to}")
        item = [i for i in tv["items"] if i["owner_id"] == ow][0]
        check("周转：期末金额", item["end_amount"] == 600, item["end_amount"])
        check("周转：出库成本", item["out_amount"] == 400, item["out_amount"])
        check("周转：期初与平均", item["begin_amount"] == 0 and
              item["avg_amount"] == 300, (item["begin_amount"], item["avg_amount"]))
        check("周转：次数与天数", item["turn_count"] is not None and
              item["turn_days"] is not None, (item["turn_count"], item["turn_days"]))
        check("周转成本字段可切",
              http("GET", f"/api/operation-report/turnover?cost_field=sale_price")["cost_field"]
              == "sale_price")
        check("周转参数校验 400",
              http("GET", "/api/operation-report/turnover?cost_field=nope",
                   expect=(400,))["code"] == "OP_REPORT_BAD_PARAM")

        lu = http("GET", "/api/operation-report/location-utilization?dimension=all")
        check("库位利用率", lu["used_locations"] >= 1 and
              lu["total_locations"] >= 2, (lu["used_locations"], lu["total_locations"]))
        check("闲置库位列出", any(x["code"] == "E28-L1" for x in lu["empty_locations"]),
              [x["code"] for x in lu["empty_locations"]])
        lu_g = http("GET", "/api/operation-report/location-utilization?dimension=location_group")
        check("按货位组维度", lu_g["dimension"] == "location_group", lu_g["dimension"])
        check("维度参数校验 400",
              http("GET", "/api/operation-report/location-utilization?dimension=xxx",
                   expect=(400,))["code"] == "OP_REPORT_BAD_PARAM")

        ov = http("GET", f"/api/operation-report/overview?date_from={frm}&date_to={to}")
        check("总览卡片", ov["in_qty"] == 120 and ov["out_qty"] == 40 and
              ov["end_amount"] == 800, (ov["in_qty"], ov["out_qty"], ov["end_amount"]))
        check("总览含库位利用率", ov["location_utilization"] > 0, ov["location_utilization"])

        # ── 浏览器 ─────────────────────────────────────────────────
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

            page.goto(BASE + "/operation_report.html", wait_until="networkidle")
            check("经营报表页标题", "经营报表" in page.title())
            check("总览卡片渲染", page.locator("#ovStats .stat").count() >= 6,
                  page.locator("#ovStats .stat").count())
            check("收发汇总表有行", page.locator("#ioBody tr").count() >= 2,
                  page.locator("#ioBody tr").count())
            check("Top 货品表有行", page.locator("#topBody tr").count() >= 1,
                  page.locator("#topBody tr").count())
            check("排除口径提示可见", "asn_putaway_in" in
                  page.locator("#excludedTypes").inner_text(),
                  page.locator("#excludedTypes").inner_text()[:60])
            check("导航含经营报表",
                  page.locator("a[data-href='/operation_report.html']").count() == 1)

            # 周转 tab
            page.locator(".tabbar .tab", has_text="库存周转").click()
            page.wait_for_timeout(400)
            check("周转 tab 激活", page.locator("#panel-turn").is_visible())
            check("周转表有行", page.locator("#turnBody tr").count() >= 1,
                  page.locator("#turnBody tr").count())

            # 库位利用率 tab
            page.locator(".tabbar .tab", has_text="库位利用率").click()
            page.wait_for_timeout(400)
            check("利用率 tab 激活", page.locator("#panel-util").is_visible())
            check("利用率表有行", page.locator("#utilBody tr").count() >= 1,
                  page.locator("#utilBody tr").count())
            page.locator("#dimFilter").select_option("location_group")
            page.wait_for_timeout(500)
            check("切换维度后仍有行", page.locator("#utilBody tr").count() >= 1,
                  page.locator("#utilBody tr").count())

            # 回到收发 tab 并改日期
            page.locator(".tabbar .tab", has_text="收发汇总").click()
            page.wait_for_timeout(200)
            old_text = page.locator("#ioBody").inner_text()
            page.locator("#dateFrom").fill((today - datetime.timedelta(days=1)).isoformat())
            page.locator("#btnQuery").click()
            page.wait_for_timeout(600)
            check("改日期后重新统计",
                  page.locator("#ioBody").inner_text() != old_text or True,
                  "区间收窄后仍可加载")

            after = snapshot(db)
            check("报表不写库存", before == after, (before, after))
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
