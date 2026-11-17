"""M31 可复跑端到端回归（独立端口 + 临时库，绝不碰 5100 demo）。

用法（Playwright 只装在 C:/Python311/python.exe，venv 里没有）：
    C:/Python311/python.exe tools/e2e_m31.py
    C:/Python311/python.exe tools/e2e_m31.py --port 5137 --keep

覆盖：九端点 API → 司机/车辆两页首屏 → CRUD 弹窗 → 证效期 tab 与分级 →
筛选 → 全系统共享显示 → 导航入口 → 库存零副作用 → I1/I2/I3 → 无 JS 错误。
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
PORT = 5137
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
            http("GET", "/api/fleet/dict")
            return True
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("服务启动超时")


def snapshot(db):
    with closing(sqlite3.connect(db)) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("stock", "stock_movement", "stock_adjustment", "stock_batch")}


def day(offset):
    return (datetime.date.today() + datetime.timedelta(days=offset)).isoformat()


def seed_data():
    ow = http("POST", "/api/owners", {"code": "E31-OW", "name": "E31 货主"})["owner"]["id"]
    other = http("POST", "/api/owners", {"code": "E31-OW9", "name": "E31 其他货主"})["owner"]["id"]
    car = http("POST", "/api/carriers", {
        "code": "E31-CAR", "name": "E31 承运商"})["carrier"]["id"]

    drivers = [
        ("E31-DEX", "临期外司机", "LEX", day(-5), ow, car),
        ("E31-DSO", "临期司机", "LSO", day(7), ow, car),
        ("E31-DNO", "正常司机", "LNO", day(100), ow, car),
        ("E31-DNN", "无日期司机", "LNN", "", ow, car),
        ("E31-DSH", "共享司机", "LSH", "", None, None),
    ]
    for code, name, lic, exp, oid, cid in drivers:
        http("POST", "/api/drivers", {
            "code": code, "name": name, "license_no": lic,
            "license_expiry": exp, "owner_id": oid, "carrier_id": cid})
    vehicles = [
        ("E31-VSO", "沪E31111", day(9), day(200), ow, car),
        ("E31-VEX", "沪E32222", day(-3), day(-2), ow, car),
        ("E31-VNN", "沪E33333", "", "", None, None),
    ]
    for code, plate, ins, trans, oid, cid in vehicles:
        http("POST", "/api/vehicles", {
            "code": code, "plate_no": plate, "vehicle_type": "厢式货车",
            "max_load_kg": 1200, "insurance_expiry": ins, "transport_expiry": trans,
            "owner_id": oid, "carrier_id": cid})
    return ow, other, car


def run(port, keep):
    global BASE, PORT
    PORT = port
    BASE = f"http://127.0.0.1:{PORT}"
    tmp = tempfile.mkdtemp(prefix="miniwms_e2e_m31_")
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
        ow, other, car = seed_data()
        before = snapshot(db)

        # ── 后端九端点 ─────────────────────────────────────────────
        d = http("GET", "/api/fleet/dict")
        check("字典状态/驾照/车型/分级/证照", all([
            len(d["statuses"]) == 2, len(d["license_types"]) == 8,
            len(d["vehicle_types"]) == 6, len(d["expiry_levels"]) == 4,
            len(d["expiry_kinds"]) == 3]), len(d["license_types"]))
        check("字典声明共享与未派工", d["owner_null_means_shared"] and not d["dn_binding"])
        drivers = http("GET", "/api/drivers")
        vehicles = http("GET", "/api/vehicles")
        check("司机列表五条", drivers["total"] == 5, drivers["total"])
        check("车辆列表三条", vehicles["total"] == 3, vehicles["total"])
        shared = [x for x in drivers["drivers"] if x["owner_id"] is None]
        check("共享运力可见", len(shared) == 1 and shared[0]["code"] == "E31-DSH", len(shared))
        detail = http("GET", "/api/drivers/" + str(drivers["drivers"][0]["id"]))
        check("司机详情端点", detail["code"].startswith("E31-"), detail["code"])
        check("关键词过滤", http("GET", "/api/drivers?keyword=E31-DSO")["total"] == 1)
        check("状态过滤", http("GET", "/api/vehicles?status=active")["total"] == 3)
        check("分页", len(http("GET", "/api/drivers?page=1&page_size=2")["drivers"]) == 2)

        rep = http("GET", "/api/fleet/expiry")
        stats = {s["level"]: s["count"] for s in rep["stats"]}
        check("效期四档统计", stats == {"expired": 3, "soon": 2, "normal": 2, "none": 4}, stats)
        check("效期总条数", rep["total"] == 11, rep["total"])
        check("证照类型过滤", http("GET", "/api/fleet/expiry?kind=driver_license")["total"] == 5)
        check("分级过滤", http("GET", "/api/fleet/expiry?level=expired")["total"] == 3)
        check("阈值生效", {s["level"]: s["count"] for s in
              http("GET", "/api/fleet/expiry?days=5")["stats"]}["soon"] == 0)
        check("参数校验 400",
              http("GET", "/api/fleet/expiry?days=999", expect=(400,))["code"] == "FLEET_BAD_PARAM")

        created = http("POST", "/api/drivers", {
            "code": "E31-DAPI", "name": "接口司机", "owner_id": ow, "carrier_id": car})["driver"]
        updated = http("PUT", "/api/drivers/" + str(created["id"]),
                       {"phone": "13700000000"})["driver"]
        check("创建/更新端点", updated["phone"] == "13700000000")
        check("删除端点", http("DELETE", "/api/drivers/" + str(created["id"]))["ok"])
        check("重复编码 400",
              http("POST", "/api/drivers", {"code": "E31-DEX", "name": "x"},
                   expect=(400,))["code"] == "FLEET_DUPLICATE_CODE")

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
            page.on("pageerror", lambda e: js_errors.append(str(e)))
            page.on("response", lambda r: bad_responses.append((r.status, r.url))
                    if r.status >= 400 and "/api/" in r.url and "favicon" not in r.url else None)

            # 司机页
            page.goto(BASE + "/drivers.html", wait_until="networkidle")
            check("司机页标题", "司机" in page.title())
            check("司机页两个 tab", page.locator("#tabbar .tab").count() == 2)
            check("司机列表五行", page.locator("#driverBody tr").count() == 5,
                  page.locator("#driverBody tr").count())
            check("共享运力标签", "全系统共享" in page.locator("#driverBody").inner_text())
            check("导航含司机", page.locator("a[data-href='/drivers.html']").count() == 1)
            page.locator("#kw").fill("E31-DSO")
            page.locator("#btnQuery").click()
            page.wait_for_timeout(250)
            check("司机前端筛选", page.locator("#driverBody tr").count() == 1,
                  page.locator("#driverBody tr").count())
            page.locator("button[data-tab='expiry']").click()
            check("司机效期面板激活", page.locator("#panel-expiry").is_visible())
            check("司机效期四档卡", page.locator("#expiryStats .stat").count() == 4)
            check("司机效期五行", page.locator("#expiryBody tr").count() == 5,
                  page.locator("#expiryBody tr").count())
            page.locator("#expLevel").select_option("expired")
            page.get_by_role("button", name="应用", exact=True).click()
            page.wait_for_timeout(250)
            check("司机效期分级过滤", page.locator("#expiryBody tr").count() == 1,
                  page.locator("#expiryBody tr").count())

            # 新建司机弹窗（先清掉上一步关键词，保证新行可见）
            page.locator("button[data-tab='list']").click()
            page.locator("#kw").fill("")
            page.locator("#btnQuery").click()
            page.wait_for_timeout(200)
            page.locator("#btnCreate").click()
            page.locator('.modal [name="code"]').fill("E31-DUI")
            page.locator('.modal [name="name"]').fill("界面司机")
            page.locator('.modal [name="license_no"]').fill("LUI")
            page.get_by_role("button", name="创建", exact=True).click()
            page.wait_for_timeout(400)
            check("弹窗创建司机", page.locator("#driverBody").inner_text().find("界面司机") >= 0)
            check("创建后列表六行", page.locator("#driverBody tr").count() == 6,
                  page.locator("#driverBody tr").count())

            # 车辆页
            page.goto(BASE + "/vehicles.html", wait_until="networkidle")
            check("车辆页标题", "车辆" in page.title())
            check("车辆列表三行", page.locator("#vehicleBody tr").count() == 3,
                  page.locator("#vehicleBody tr").count())
            check("导航含车辆", page.locator("a[data-href='/vehicles.html']").count() == 1)
            page.locator("button[data-tab='expiry']").click()
            check("车辆效期面板激活", page.locator("#panel-expiry").is_visible())
            check("车辆效期四档卡", page.locator("#expiryStats .stat").count() == 4)
            page.locator("#expKind").select_option("vehicle_insurance")
            page.get_by_role("button", name="应用", exact=True).click()
            page.wait_for_timeout(250)
            check("保险效期三行", page.locator("#expiryBody tr").count() == 3,
                  page.locator("#expiryBody tr").count())
            page.locator("#expKind").select_option("vehicle_transport")
            page.get_by_role("button", name="应用", exact=True).click()
            page.wait_for_timeout(250)
            check("运输证切换", page.locator("#expiryBody tr").count() == 3,
                  page.locator("#expiryBody tr").count())

            page.locator("button[data-tab='list']").click()
            page.locator("#btnCreate").click()
            page.locator('.modal [name="code"]').fill("E31-VUI")
            page.locator('.modal [name="plate_no"]').fill("沪E39999")
            page.get_by_role("button", name="创建", exact=True).click()
            page.wait_for_timeout(400)
            check("弹窗创建车辆", page.locator("#vehicleBody").inner_text().find("沪E39999") >= 0)
            check("创建后车辆四行", page.locator("#vehicleBody tr").count() == 4,
                  page.locator("#vehicleBody tr").count())

            after = snapshot(db)
            check("主数据不写库存", before == after, (before, after))
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
