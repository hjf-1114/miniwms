"""M34 可复跑端到端回归（独立端口 + 临时库，绝不碰 5100 demo）。

用法（Playwright 只装在 C:/Python311/python.exe，venv 里没有）：
    C:/Python311/python.exe tools/e2e_m34.py
    C:/Python311/python.exe tools/e2e_m34.py --port 5140 --keep

覆盖：七端点 API → 库位自动建组 → 引用保护 → 货位组页首屏 → 弹窗 CRUD →
拣货顺序 tab → 前端删除保护提示 → 导航入口 → 库存零副作用 → I1/I2/I3 → 无 JS 错误。
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
PORT = 5140
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
            http("GET", "/api/location-groups/dict")
            return True
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("服务启动超时")


def snapshot(db):
    with closing(sqlite3.connect(db)) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("stock", "stock_movement", "stock_adjustment", "stock_batch")}


def seed_data():
    # 先建两个货位组（库位建档时会自动补建缺失的组）
    http("POST", "/api/location-groups", {"code": "E34-G1", "name": "一组",
                                          "pick_seq": 20, "capacity_volume": 5000,
                                          "allow_mix_batch": 1})
    http("POST", "/api/location-groups", {"code": "E34-G2", "name": "二组",
                                          "pick_seq": 10})
    # 三个库位分别落到两个分组，第四个留空走编码前缀自动生成
    http("POST", "/api/locations", {"code": "E34-A-01", "name": "E34 库位A01",
                                    "location_group": "E34-G1"})
    http("POST", "/api/locations", {"code": "E34-A-02", "name": "E34 库位A02",
                                    "location_group": "E34-G1"})
    http("POST", "/api/locations", {"code": "E34-B-01", "name": "E34 库位B01",
                                    "location_group": "E34-G2"})
    http("POST", "/api/locations", {"code": "E34-C-01", "name": "E34 库位C01"})


def run(port, keep):
    global BASE, PORT
    PORT = port
    BASE = f"http://127.0.0.1:{PORT}"
    tmp = tempfile.mkdtemp(prefix="miniwms_e2e_m34_")
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
        seed_data()
        before = snapshot(db)

        # ── 后端七端点 ─────────────────────────────────────────────
        d = http("GET", "/api/location-groups/dict")
        check("字典状态/混放/容积", all([
            len(d["statuses"]) == 2, len(d["mix_fields"]) == 3,
            d["capacity_unlimited"] == 0]), len(d["mix_fields"]))
        check("字典声明不按货主隔离", d["owner_isolated"] is False)

        lst = http("GET", "/api/location-groups?page_size=100")
        codes = {g["code"] for g in lst["location_groups"]}
        check("库位自动建组", "E34" in codes, sorted(codes))
        check("三个货位组", {"E34-G1", "E34-G2"} <= codes, sorted(codes))
        g1 = [g for g in lst["location_groups"] if g["code"] == "E34-G1"][0]
        check("组库位数统计", g1["location_count"] == 2, g1["location_count"])
        check("组属性回读", g1["pick_seq"] == 20 and g1["capacity_volume"] == 5000
              and g1["allow_mix_batch"] == 1, g1)
        check("容积无限标记", g1["volume_unlimited"] is False)

        check("关键词过滤", http("GET", "/api/location-groups?keyword=E34-G2")["total"] == 1)
        check("分页", len(http("GET", "/api/location-groups?page=1&page_size=1")
                          ["location_groups"]) == 1)
        bad = http("GET", "/api/location-groups?page=abc", expect=(400,))
        check("参数校验 400", bad["code"] == "LOC_GROUP_BAD_PARAM")

        detail = http("GET", "/api/location-groups/" + str(g1["id"]))["location_group"]
        check("详情含所属库位", len(detail["locations"]) == 2, len(detail["locations"]))

        created = http("POST", "/api/location-groups",
                       {"code": "E34-API", "name": "接口组"})["location_group"]
        updated = http("PUT", "/api/location-groups/" + str(created["id"]),
                       {"pick_seq": 77})["location_group"]
        check("创建/更新端点", updated["pick_seq"] == 77)
        check("重复编码 400",
              http("POST", "/api/location-groups", {"code": "E34-G1", "name": "x"},
                   expect=(400,))["code"] == "LOC_GROUP_DUPLICATE_CODE")
        check("被引用删除 400",
              http("DELETE", "/api/location-groups/" + str(g1["id"]),
                   expect=(400,))["code"] == "LOC_GROUP_IN_USE")
        check("被引用改编码 400",
              http("PUT", "/api/location-groups/" + str(g1["id"]),
                   {"code": "E34-G1B"}, expect=(400,))["code"] == "LOC_GROUP_IN_USE")
        check("未引用可删", http("DELETE", "/api/location-groups/" + str(created["id"]))["ok"])

        po = http("GET", "/api/location-groups/pick-order")
        order = [g["code"] for g in po["groups"] if g["code"].startswith("E34")]
        check("拣货顺序按 pick_seq 升序", order == ["E34", "E34-G2", "E34-G1"], order)
        g2 = [g for g in po["groups"] if g["code"] == "E34-G2"][0]
        check("拣货视图带库位", g2["location_count"] == 1 and
              g2["locations"][0]["code"] == "E34-B-01", g2["location_count"])

        loc = http("GET", "/api/locations?keyword=E34-A-01")["locations"][0]
        check("库位带组显示名", loc["location_group"] == "E34-G1" and
              loc["location_group_name"] == "一组", loc["location_group"])
        groups = http("GET", "/api/locations/groups")["groups"]
        check("库位组下拉含全部组", {"E34-G1", "E34-G2", "E34"} <= set(groups), groups)

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
            expected_4xx = ["/api/location-groups/"]
            page.on("dialog", lambda d: d.accept())
            page.on("pageerror", lambda e: js_errors.append(str(e)))
            page.on("response", lambda r: bad_responses.append((r.status, r.url))
                    if r.status >= 400 and "/api/" in r.url and "favicon" not in r.url
                    and not any(x in r.url for x in expected_4xx) else None)

            page.goto(BASE + "/location_groups.html", wait_until="networkidle")
            check("货位组页标题", "货位组" in page.title())
            rows = page.locator("#tbody tr").count()
            check("货位组列表三行", rows == 3, rows)
            check("导航含货位组", page.locator("a[data-href='/location_groups.html']").count() == 1)
            body = page.locator("#tbody").inner_text()
            check("列表显示拣货顺序与库位数", "拣货顺序" in page.locator("thead").inner_text()
                  and "E34-G1" in body)

            # 筛选
            page.locator("#kw").fill("E34-G1")
            page.locator("#btnQuery").click()
            page.wait_for_timeout(300)
            check("前端筛选", page.locator("#tbody tr").count() == 1,
                  page.locator("#tbody tr").count())
            page.locator("#kw").fill("")
            page.locator("#btnQuery").click()
            page.wait_for_timeout(300)

            # 弹窗创建
            page.get_by_role("button", name="+ 新建货位组").click()
            page.wait_for_timeout(250)
            page.locator('.modal [name="code"]').fill("E34-UI")
            page.locator('.modal [name="name"]').fill("界面组")
            page.locator('.modal [name="pick_seq"]').fill("5")
            page.get_by_role("button", name="创建", exact=True).click()
            page.wait_for_timeout(500)
            check("弹窗创建货位组", "E34-UI" in page.locator("#tbody").inner_text())
            check("创建后四行", page.locator("#tbody tr").count() == 4,
                  page.locator("#tbody tr").count())

            # 弹窗编辑
            row = page.locator("#tbody tr", has_text="E34-UI").first
            row.get_by_role("button", name="编辑").click()
            page.wait_for_timeout(250)
            page.locator('.modal [name="name"]').fill("界面组改")
            page.get_by_role("button", name="保存", exact=True).click()
            page.wait_for_timeout(500)
            check("弹窗编辑货位组", "界面组改" in page.locator("#tbody").inner_text())

            # 删除被引用组 → 提示
            row2 = page.locator("#tbody tr", has_text="E34-G1").first
            row2.get_by_role("button", name="删除").click()
            page.wait_for_timeout(500)
            msg = page.locator("#msg").inner_text()
            check("删除被引用被拒并提示", "引用" in msg, msg[:80])

            # 拣货顺序 tab
            page.locator(".tabbar .tab", has_text="拣货顺序").click()
            page.wait_for_timeout(500)
            check("拣货顺序 tab 激活", page.locator("#panel-pick").is_visible())
            check("拣货顺序列出组", page.locator("#pickBody .card").count() >= 4,
                  page.locator("#pickBody .card").count())
            pick_txt = page.locator("#pickBody").inner_text()
            check("拣货顺序带库位", "E34-A-01" in pick_txt, pick_txt[:120])

            after = snapshot(db)
            check("货位组操作不写库存", before == after, (before, after))
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
