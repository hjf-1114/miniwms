"""M39 可复跑端到端回归（独立端口 + 临时库，绝不碰 5100 demo）。

用法（Playwright 只装在 C:/Python311/python.exe，venv 里没有）：
    C:/Python311/python.exe tools/e2e_m39.py
    C:/Python311/python.exe tools/e2e_m39.py --port 5135 --keep

覆盖：页面首屏 → 概览卡 → 五个 tab → 效期分级与阈值 → 批次追溯三段链 →
养护/验收/复核留痕 → 过滤 → 只读无副作用 → 导航入口 → I1/I2/I3 → 无 JS 错误。
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
PORT = 5135
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
            http("GET", "/api/gsp/dict")
            return True
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("服务启动超时")


def inventory_snapshot(db):
    with closing(sqlite3.connect(db)) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("stock", "stock_movement", "stock_adjustment", "stock_batch")}


def seed_data(db):
    """造 GSP 演示数据：货主 / 物料 / 库位 / 批次 / 效期四级 / 追溯链 / 三类留痕。"""
    ow = http("POST", "/api/owners", {"code": "E39-OW", "name": "E39 货主"})["owner"]["id"]
    pid = http("POST", "/api/products", {
        "sku": "E39-P", "name": "E39 物料", "owner_id": ow})["product"]["id"]
    lid = http("POST", "/api/locations", {
        "code": "E39-L", "name": "E39 库位", "owner_id": ow})["location"]["id"]
    today = datetime.date.today()
    with closing(sqlite3.connect(db)) as c:
        c.row_factory = sqlite3.Row
        # 批次档案
        cur = c.execute(
            "INSERT INTO batch (code, batch_key, owner_id, product_id, key_info, info, "
            "receipt_count, created_at, updated_at) VALUES (?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
            ("E39-BATCH", "E39-KEY", ow, pid, "key", "info", 1))
        bid = cur.lastrowid
        # 收货验收
        c.execute(
            "INSERT INTO batch_receipt (batch_no, batch_id, internal_batch_no, internal_source, "
            "owner_id, product_id, location_id, asn_id, asn_line_id, qty, doc_type, goods_property, "
            "info, created_at, external_batch_no, warning) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),?,?)",
            ("E39-BATCH", bid, "IN-E39", "asn", ow, pid, lid, 1, 1, 10, "采购", "合格",
             "info", "EXT-E39", ""))
        # 批次明细 + 同维度流水（做平 I2）；qty=0 的追溯批次不进效期统计
        # (批号, 效期, batch_key, 数量)
        batches = [
            ("E39-BATCH", "", "E39-KEY", 0),  # 追溯链专用
            ("E39-EXP", (today - datetime.timedelta(days=5)).isoformat(), "KEY-0", 5),
            ("E39-SOON", (today + datetime.timedelta(days=10)).isoformat(), "KEY-1", 5),
            ("E39-NORM", (today + datetime.timedelta(days=100)).isoformat(), "KEY-2", 5),
            ("E39-NONE", "", "KEY-3", 5),
        ]
        total_qty = 0
        for bno, ed, key, qty in batches:
            total_qty += qty
            c.execute(
                "INSERT INTO stock_batch (owner_id, product_id, location_id, batch_no, batch_key, "
                "expiry_date, qty, allocated_qty, goods_property, stock_status) "
                "VALUES (?,?,?,?,?,?,?,0,'合格','非限制')",
                (ow, pid, lid, bno, key, ed, qty))
            c.execute(
                "INSERT INTO stock_movement (owner_id, product_id, location_id, qty_delta, ref_type, "
                "ref_id, note, batch_no, batch_key, internal_batch_no, goods_property, stock_status, "
                "expiry_date, allocated_delta, operator, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                (ow, pid, lid, qty, "asn", 1, "", bno, key, "IN-E39",
                 "合格", "非限制", ed, 0, ""))
        # 主账（做平 I1/I3）：stock.qty = SUM(stock_batch.qty) = SUM(movement.qty_delta)
        c.execute(
            "INSERT INTO stock (owner_id, product_id, location_id, qty, allocated_qty, updated_at) "
            "VALUES (?,?,?,?,0,datetime('now'))",
            (ow, pid, lid, total_qty))
        # 养护留痕（异常 + 正常）
        for code, project, action, result, reason, label in (
            ("CT-A", "TEMP_HUMIDITY", "abnormal", "abnormal", "TEMP_HIGH", "温度超限"),
            ("CT-B", "EXPIRY", "checked", "normal", "", ""),
        ):
            c.execute(
                "INSERT INTO care_log (task_id, task_code, plan_id, plan_code, owner_id, project, "
                "due_date, action, result, reason_code, reason_label, note, operator, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                (1, code, 1, "CP-E39", ow, project, today.isoformat(), action, result,
                 reason, label, "", ""))
        # 复核差异
        c.execute(
            "INSERT INTO dn_review_diff_log (review_id, review_code, dn_id, dn_code, owner_id, sku, "
            "product_name, location_code, batch_no, batch_key, expiry_date, goods_property, "
            "stock_status, wave_no, expected_qty, actual_qty, diff_qty, reason_code, reason_label, "
            "note, action, operator, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
            (1, "RV-E39", 1, "DN-E39", ow, "E39-P", "E39 物料", "E39-L", "E39-BATCH",
             "E39-KEY", "", "合格", "非限制", "", 10, 8, -2, "SHORT", "少货", "",
             "diff_registered", ""))
        c.commit()
    return ow, pid, lid


def run(port, keep):
    global BASE, PORT
    PORT = port
    BASE = f"http://127.0.0.1:{PORT}"
    tmp = tempfile.mkdtemp(prefix="miniwms_e2e_m39_")
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
        ow, pid, lid = seed_data(db)
        before = inventory_snapshot(db)

        # 后端断言
        d = http("GET", "/api/gsp/dict")
        check("字典五类报表", len(d["reports"]) == 5, len(d["reports"]))
        check("字典四级效期", len(d["expiry_levels"]) == 4, len(d["expiry_levels"]))
        ov = http("GET", "/api/gsp/overview")
        check("概览四张卡", len(ov["cards"]) == 4, len(ov["cards"]))
        check("概览养护异常计数", ov["care_abnormal"] == 1, ov["care_abnormal"])
        exp = http("GET", "/api/gsp/expiry")
        stats = {s["level"]: s["count"] for s in exp["stats"]}
        check("效期四级各一", all(stats[k] == 1 for k in ("expired", "soon", "normal", "none")), stats)
        exp5 = http("GET", "/api/gsp/expiry?days=5")
        check("days 阈值生效", {s["level"]: s["count"] for s in exp5["stats"]}["soon"] == 0)
        check("level 过滤", http("GET", "/api/gsp/expiry?level=expired")["total"] == 1)
        tr = http("GET", "/api/gsp/trace?batch_no=E39-BATCH")
        check("追溯到批次档案", tr["total"] == 1, tr["total"])
        if tr["batches"]:
            b = tr["batches"][0]
            check("追溯三段链齐全",
                  len(b["receipts"]) == 1 and len(b["stocks"]) >= 1 and len(b["movements"]) == 1,
                  b["stage_counts"])
        care = http("GET", "/api/gsp/care")
        check("养护留痕两条", care["total"] == 2, care["total"])
        check("养护异常统计", care["stats"]["abnormal"] == 1, care["stats"])
        rec = http("GET", "/api/gsp/receipt")
        check("验收留痕一条", rec["total"] == 1, rec["total"])
        rev = http("GET", "/api/gsp/review")
        check("复核差异一条", rev["total"] == 1, rev["total"])
        check("参数校验 400",
              http("GET", "/api/gsp/expiry?days=999", expect=(400,)).get("code") == "GSP_BAD_PARAM")

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
            page.goto(BASE + "/gsp.html", wait_until="networkidle")

            # ── 首屏 ────────────────────────────────────────────────
            check("GSP 页标题", "GSP" in page.title())
            check("五个 tab", page.locator("#tabbar .tab").count() == 5,
                  page.locator("#tabbar .tab").count())
            check("概览卡渲染", page.locator("#overview .card").count() == 1)
            check("效期统计四档", page.locator("#expiryStats .stat").count() == 4,
                  page.locator("#expiryStats .stat").count())
            check("效期表有数据", page.locator("#expiryBody tr").count() == 4,
                  page.locator("#expiryBody tr").count())
            check("导航含 GSP 报表", page.locator("a[data-href='/gsp.html']").count() == 1)

            # ── tab 切换 ────────────────────────────────────────────
            page.locator("button[data-tab='trace']").click()
            check("追溯面板激活", page.locator("#panel-trace").is_visible())
            page.locator("#traceNo").fill("E39-BATCH")
            page.get_by_role("button", name="追溯", exact=True).click()
            page.wait_for_timeout(400)
            check("追溯链渲染", page.locator("#traceList .chain").count() == 1,
                  page.locator("#traceList .chain").count())
            check("追溯三段标题", page.locator("#traceList .chain .seg").count() >= 3)

            page.locator("button[data-tab='care']").click()
            check("养护面板激活", page.locator("#panel-care").is_visible())
            check("养护表两行", page.locator("#careBody tr").count() == 2,
                  page.locator("#careBody tr").count())

            page.locator("button[data-tab='receipt']").click()
            check("验收面板激活", page.locator("#panel-receipt").is_visible())
            check("验收表一行", page.locator("#receiptBody tr").count() == 1)

            page.locator("button[data-tab='review']").click()
            check("复核面板激活", page.locator("#panel-review").is_visible())
            check("复核表一行", page.locator("#reviewBody tr").count() == 1)

            # ── 只读与一致性 ────────────────────────────────────────
            after = inventory_snapshot(db)
            check("报表未改库存流水", before == after, (before, after))
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
