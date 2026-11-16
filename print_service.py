"""M41 打印模板配置 + PDF 预览与内容编辑（方案 v2：零安装）。

三层职责：
  1. 模板配置（``print_template`` 表）：6 类作业节点各可挂多模板、一个生效；
     ``layout_json`` 同时存 L1 占位符文本（``content_template``）与 L2 区块数组
     （``blocks``），``field_map`` 存字段标签 / 宽度 / 是否可临时编辑。
  2. 单据取值：从 ASN / DN / 复核单读真实业务数据，统一经 scope 注入货主条件，
     只读，不改任何业务表。
  3. 渲染与 PDF：套模板生成内联 CSS 的纯 HTML，调系统 Chrome headless
     ``--print-to-pdf --no-pdf-header-footer`` 出 PDF 到临时目录；预览页的临时
     微调只作用于本次打印件，**不回写业务单据**（Q38/Q39 登记）。

依赖仅有标准库 + flask.g + scope/constants，不反向 import main，避免循环依赖。
"""
import copy
import html
import json
import os
import re
import shutil
import subprocess
import tempfile

import constants as C
import scope as S


class PrintError(Exception):
    """模板 / 打印参数错误。"""

    def __init__(self, message, code="PRINT_BAD_PARAM"):
        super().__init__(message)
        self.code = code


def _json_loads(raw, name):
    if isinstance(raw, dict):
        return copy.deepcopy(raw)
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        raise PrintError(f"{name} 不是合法 JSON")
    if not isinstance(data, dict):
        raise PrintError(f"{name} 必须是 JSON 对象")
    return data


def _json_dumps(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _default_field_map():
    return {k: {"label": v["label"], "editable": bool(v.get("editable"))}
            for k, v in C.PRINT_FIELD_DEFS.items()}


def default_layout():
    """内置默认排版（无模板 / 模板被删时兜底，仍是区块级 L2 结构）。"""
    return {
        "blocks": [
            {"id": "header", "type": C.PRINT_BLOCK_HEADER, "title": "单据信息",
             "fields": ["code", "owner_name", "customer_name", "created_at",
                        "total_qty", "note", "receiver"], "columns": 2},
            {"id": "lines", "type": C.PRINT_BLOCK_TABLE, "title": "商品明细",
             "fields": ["line_no", "product_code", "product_name", "qty",
                        "batch_no", "location_code", "line_note"]},
            {"id": "total", "type": C.PRINT_BLOCK_TOTAL, "title": "合计",
             "fields": ["total_qty"]},
            {"id": "sign", "type": C.PRINT_BLOCK_SIGNATURE, "title": "签核栏",
             "fields": ["operator", "receiver"]},
        ],
        "content_template": "",
    }


def seed_templates(conn):
    """新库 / 老库幂等补默认模板；用户删光某节点模板后不再复活。"""
    for node in C.PRINT_NODES:
        row = conn.execute(
            "SELECT id FROM print_template WHERE node = ? LIMIT 1", (node,)
        ).fetchone()
        if row is not None:
            continue
        code = C.PRINT_DEFAULT_TEMPLATE_PREFIX + node.upper()
        conn.execute(
            "INSERT INTO print_template (node, code, name, version, is_active, "
            "layout_json, field_map, note) VALUES (?,?,?,?,?,?,?,?)",
            (node, code, "默认" + C.PRINT_NODE_LABELS[node],
             C.PRINT_TEMPLATE_VERSION_DEFAULT, 1,
             _json_dumps(default_layout()), _json_dumps(_default_field_map()),
             "系统默认模板，可复制后修改"),
        )


def dictionary():
    return {
        "ok": True,
        "nodes": [{"code": n, "label": C.PRINT_NODE_LABELS[n]} for n in C.PRINT_NODES],
        "sources": [{"code": s, "label": C.PRINT_SOURCE_LABELS[s]}
                    for s in C.PRINT_SOURCES],
        "blocks": [{"code": b, "label": C.PRINT_BLOCK_LABELS[b]}
                   for b in C.PRINT_BLOCK_TYPES],
        "fields": [
            {"code": k, "label": v["label"], "scope": v["scope"],
             "editable": bool(v.get("editable"))}
            for k, v in C.PRINT_FIELD_DEFS.items()],
        "editable_fields": list(C.PRINT_EDITABLE_FIELDS),
        "no_install": True,
        "temp_edit_only": True,
    }


# ─────────────────────────── 模板配置 ───────────────────────────

def _template_dict(r):
    d = dict(r)
    d["layout"] = _json_loads(d.pop("layout_json", "{}"), "layout_json")
    d["field_map"] = _json_loads(d.get("field_map", "{}"), "field_map")
    d["is_active"] = bool(d.get("is_active"))
    return d


def list_templates(db, node=""):
    where, params = [], []
    if node:
        if node not in C.PRINT_NODES:
            raise PrintError(f"node 无效：{node}")
        where.append("node = ?")
        params.append(node)
    sql = "SELECT * FROM print_template"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY node, is_active DESC, id DESC"
    rows = db.execute(sql, params).fetchall()
    return {"ok": True, "total": len(rows),
            "templates": [_template_dict(r) for r in rows]}


def get_template(db, tid):
    r = db.execute("SELECT * FROM print_template WHERE id = ?", (tid,)).fetchone()
    if r is None:
        raise PrintError(f"模板不存在：{tid}", "PRINT_TEMPLATE_NOT_FOUND")
    return _template_dict(r)


def _validate_template(payload, *, partial=False):
    data = {}
    if not partial or "node" in payload:
        node = payload.get("node")
        if node not in C.PRINT_NODES:
            raise PrintError(f"node 无效：{node}")
        data["node"] = node
    if not partial or "code" in payload:
        code = str(payload.get("code") or "").strip()
        if not code or len(code) > C.PRINT_TEMPLATE_CODE_MAXLEN:
            raise PrintError("code 必填且不能超过 48 字符")
        data["code"] = code
    if not partial or "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise PrintError("name 必填")
        data["name"] = name
    if "version" in payload:
        data["version"] = str(payload.get("version") or C.PRINT_TEMPLATE_VERSION_DEFAULT)
    if "note" in payload:
        data["note"] = str(payload.get("note") or "")
    if "is_active" in payload:
        data["is_active"] = 1 if str(payload["is_active"]).lower() in (
            "1", "true", "yes", "on") else 0
    if "layout_json" in payload:
        layout = _json_loads(payload["layout_json"], "layout_json")
        if not isinstance(layout.get("blocks", []), list):
            raise PrintError("layout_json.blocks 必须是数组")
        data["layout_json"] = _json_dumps(layout)
    elif not partial:
        data["layout_json"] = _json_dumps(default_layout())
    if "field_map" in payload:
        data["field_map"] = _json_dumps(_json_loads(payload["field_map"], "field_map"))
    elif not partial:
        data["field_map"] = _json_dumps(_default_field_map())
    return data


def create_template(db, payload):
    data = _validate_template(payload)
    cols = list(data)
    try:
        cur = db.execute(
            f"INSERT INTO print_template ({','.join(cols)}) "
            f"VALUES ({','.join('?' for _ in cols)})",
            [data[c] for c in cols],
        )
    except Exception as e:  # noqa: BLE001
        if "UNIQUE" in str(e).upper():
            raise PrintError(f"模板编码已存在：{data['code']}", "PRINT_DUPLICATE_CODE")
        raise
    tid = cur.lastrowid
    if data.get("is_active"):
        _only_active(db, tid, data["node"])
    db.commit()
    return get_template(db, tid)


def update_template(db, tid, payload):
    get_template(db, tid)
    data = _validate_template(payload, partial=True)
    if not data:
        return get_template(db, tid)
    node = data.get("node")
    cols = list(data)
    try:
        db.execute(
            f"UPDATE print_template SET {','.join(c + '=?' for c in cols)}, "
            "updated_at=datetime('now','localtime') WHERE id=?",
            [data[c] for c in cols] + [tid],
        )
    except Exception as e:  # noqa: BLE001
        if "UNIQUE" in str(e).upper():
            raise PrintError(f"模板编码已存在：{data['code']}", "PRINT_DUPLICATE_CODE")
        raise
    if data.get("is_active"):
        _only_active(db, tid, node)
    db.commit()
    return get_template(db, tid)


def delete_template(db, tid):
    t = get_template(db, tid)
    db.execute("DELETE FROM print_template WHERE id=?", (tid,))
    db.commit()
    return {"ok": True, "deleted": tid, "node": t["node"]}


def activate_template(db, tid):
    t = get_template(db, tid)
    _only_active(db, tid, t["node"])
    db.commit()
    return get_template(db, tid)


def _only_active(db, tid, node):
    db.execute("UPDATE print_template SET is_active=0, "
               "updated_at=datetime('now','localtime') WHERE node=? AND id<>?",
               (node, tid))
    db.execute("UPDATE print_template SET is_active=1, "
               "updated_at=datetime('now','localtime') WHERE id=?", (tid,))


def active_template(db, node, tid=None):
    if tid:
        return get_template(db, tid)
    r = db.execute(
        "SELECT * FROM print_template WHERE node=? AND is_active=1 "
        "ORDER BY id DESC LIMIT 1", (node,)
    ).fetchone()
    return _template_dict(r) if r else None


# ─────────────────────────── 单据取值 ───────────────────────────

def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _asn_document(db, node, doc_id):
    w, p = ["a.id=?"], [doc_id]
    S.scoped_where(w, p, table="a")
    d = db.execute(
        f"""SELECT a.*, o.name AS owner_name
              FROM asn a JOIN owner o ON o.id=a.owner_id
             WHERE {' AND '.join(w)}""", p).fetchone()
    if d is None:
        raise PrintError("单据不存在或无权查看", "PRINT_DOC_NOT_FOUND")
    qty_expr = "COALESCE(al.putaway_qty, 0)" if node == C.PRINT_NODE_PUTAWAY \
        else "COALESCE(al.received_qty, al.expected_qty)"
    lines = db.execute(
        f"""SELECT al.id, p.sku AS product_code, p.name AS product_name,
                   {qty_expr} AS qty,
                   (SELECT br.batch_no FROM batch_receipt br
                     WHERE br.asn_id=a.id AND br.product_id=al.product_id
                    ORDER BY br.id DESC LIMIT 1) AS batch_no,
                   (SELECT l.code FROM batch_receipt br
                     JOIN location l ON l.id=br.location_id
                     WHERE br.asn_id=a.id AND br.product_id=al.product_id
                    ORDER BY br.id DESC LIMIT 1) AS location_code
              FROM asn_line al
              JOIN product p ON p.id=al.product_id
              JOIN asn a ON a.id=al.asn_id
             WHERE al.asn_id=? ORDER BY al.id""", (doc_id,)).fetchall()
    fields = {
        "code": d["code"], "owner_name": d["owner_name"],
        "customer_name": d["customer_name"] or d["supplier_name"] or "",
        "doc_type": d["doc_type"], "created_at": d["created_at"],
        "total_qty": _num(d["total_qty"]) or sum(_num(r["qty"]) for r in lines),
        "note": d["note"], "receiver": "", "operator": "",
    }
    return _build_doc(node, C.PRINT_SOURCE_ASN, d["code"], fields, lines)


def _dn_document(db, node, doc_id):
    w, p = ["d.id=?"], [doc_id]
    S.scoped_where(w, p, table="d")
    d = db.execute(
        f"""SELECT d.*, o.name AS owner_name, c.name AS customer_name
              FROM dn d JOIN owner o ON o.id=d.owner_id
              LEFT JOIN customer c ON c.id=d.customer_id
             WHERE {' AND '.join(w)}""", p).fetchone()
    if d is None:
        raise PrintError("单据不存在或无权查看", "PRINT_DOC_NOT_FOUND")
    lines = db.execute(
        """SELECT dl.id, p.sku AS product_code, p.name AS product_name,
                  CASE WHEN dl.picked_qty>0 THEN dl.picked_qty ELSE dl.ordered_qty END AS qty,
                  (SELECT dp.batch_no FROM dn_pick dp WHERE dp.line_id=dl.id
                    ORDER BY dp.id DESC LIMIT 1) AS batch_no,
                  (SELECT l.code FROM dn_pick dp JOIN location l ON l.id=dp.location_id
                    WHERE dp.line_id=dl.id ORDER BY dp.id DESC LIMIT 1) AS location_code
             FROM dn_line dl JOIN product p ON p.id=dl.product_id
            WHERE dl.dn_id=? ORDER BY dl.id""", (doc_id,)).fetchall()
    fields = {
        "code": d["code"], "owner_name": d["owner_name"],
        "customer_name": d["customer_name"] or "", "doc_type": d["doc_type"],
        "created_at": d["created_at"],
        "total_qty": _num(d["total_qty"]) or sum(_num(r["qty"]) for r in lines),
        "note": d["note"], "receiver": "", "operator": "",
    }
    return _build_doc(node, C.PRINT_SOURCE_DN, d["code"], fields, lines)


def _review_document(db, node, doc_id):
    w, p = ["r.id=?"], [doc_id]
    S.scoped_where(w, p, table="r")
    d = db.execute(
        f"""SELECT r.*, o.name AS owner_name, c.name AS customer_name
              FROM dn_review r JOIN owner o ON o.id=r.owner_id
              LEFT JOIN customer c ON c.id=r.customer_id
             WHERE {' AND '.join(w)}""", p).fetchone()
    if d is None:
        raise PrintError("单据不存在或无权查看", "PRINT_DOC_NOT_FOUND")
    lines = db.execute(
        """SELECT rl.id, rl.sku AS product_code, rl.product_name,
                  COALESCE(rl.actual_qty, rl.expected_qty) AS qty,
                  rl.batch_no, rl.location_code, rl.note AS line_note
             FROM dn_review_line rl WHERE rl.review_id=? ORDER BY rl.id""",
        (doc_id,)).fetchall()
    fields = {
        "code": d["code"], "owner_name": d["owner_name"],
        "customer_name": d["customer_name"] or "", "doc_type": "出库复核",
        "created_at": d["created_at"],
        "total_qty": sum(_num(r["qty"]) for r in lines),
        "note": d["note"], "receiver": "", "operator": d["operator"],
    }
    return _build_doc(node, C.PRINT_SOURCE_REVIEW, d["code"], fields, lines)


def _build_doc(node, source, code, fields, lines):
    out_lines = []
    for i, r in enumerate(lines, 1):
        d = dict(r)
        out_lines.append({
            "line_no": i,
            "product_code": d.get("product_code", ""),
            "product_name": d.get("product_name", ""),
            "qty": _num(d.get("qty")),
            "batch_no": d.get("batch_no", ""),
            "location_code": d.get("location_code", ""),
            "line_note": d.get("line_note", ""),
        })
    return {"ok": True, "node": node, "source": source, "code": code,
            "fields": fields, "lines": out_lines,
            "editable": list(C.PRINT_EDITABLE_FIELDS)}


def load_document(db, node, source, doc_id):
    if node not in C.PRINT_NODES:
        raise PrintError(f"node 无效：{node}")
    if source not in C.PRINT_SOURCES:
        raise PrintError(f"source 无效：{source}")
    try:
        did = int(doc_id)
    except (TypeError, ValueError):
        raise PrintError("doc_id 必须是整数")
    if source == C.PRINT_SOURCE_ASN:
        return _asn_document(db, node, did)
    if source == C.PRINT_SOURCE_DN:
        return _dn_document(db, node, did)
    return _review_document(db, node, did)


# ─────────────────────────── 预览编辑 / 渲染 ───────────────────────────

def merge_overrides(doc, overrides):
    """预览页临时微调合并进文档副本；只认白名单字段，不触碰业务表。"""
    merged = copy.deepcopy(doc)
    if not overrides:
        return merged
    if isinstance(overrides, str):
        try:
            overrides = json.loads(overrides)
        except (TypeError, ValueError):
            raise PrintError("overrides 不是合法 JSON")
    if not isinstance(overrides, dict):
        raise PrintError("overrides 必须是 JSON 对象")
    for key, value in (overrides.get("fields") or {}).items():
        if key in C.PRINT_FIELD_DEFS and C.PRINT_FIELD_DEFS[key].get("editable"):
            merged["fields"][key] = value
    for i, line_override in enumerate(overrides.get("lines") or []):
        if i >= len(merged["lines"]) or not isinstance(line_override, dict):
            continue
        for key, value in line_override.items():
            if key in C.PRINT_FIELD_DEFS and C.PRINT_FIELD_DEFS[key].get("editable"):
                merged["lines"][i][key] = value
    return merged


def _esc(v):
    return html.escape(str(v if v is not None else ""), quote=True)


def _lines_table(doc, field_map):
    fields = ["line_no", "product_code", "product_name", "qty", "batch_no",
              "location_code", "line_note"]
    head = "".join(
        f"<th style='width:{(field_map.get(f) or {}).get('width', '')}'>"
        f"{_esc((field_map.get(f) or {}).get('label', C.PRINT_FIELD_DEFS[f]['label']))}</th>"
        for f in fields)
    rows = "".join(
        "<tr>" + "".join(f"<td>{_esc(line.get(f, ''))}</td>" for f in fields) + "</tr>"
        for line in doc["lines"])
    if not rows:
        rows = f"<tr><td colspan='{len(fields)}' class='empty'>无明细行</td></tr>"
    return f"<table class='lines'><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"


def _substitute(text, doc, field_map):
    flat = dict(doc["fields"])
    flat["lines_table"] = _lines_table(doc, field_map)
    return re.sub(
        r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}",
        lambda m: flat["lines_table"] if m.group(1) == "lines_table"
        else _esc(flat.get(m.group(1), "")),
        str(text or ""),
    )


def _render_block(block, doc, field_map):
    typ = block.get("type")
    title = f"<h3>{_esc(block.get('title') or '')}</h3>" if block.get("title") else ""
    fields = block.get("fields") or []
    if typ == C.PRINT_BLOCK_TABLE:
        return f"<section>{title}{_lines_table(doc, field_map)}</section>"
    if typ == C.PRINT_BLOCK_TOTAL:
        total = sum(_num(line.get("qty")) for line in doc["lines"])
        return (f"<section>{title}<div class='total'>合计数量："
                f"{_esc(doc['fields'].get('total_qty', total))}</div></section>")
    if typ == C.PRINT_BLOCK_SIGNATURE:
        return ("<section>" + title +
                "<div class='sign'>操作人：____________　收货人：____________"
                "　日期：____________</div></section>")
    if typ == C.PRINT_BLOCK_TEXT:
        return f"<section>{_substitute(block.get('text', ''), doc, field_map)}</section>"
    cols = max(1, min(4, int(block.get("columns") or 2)))
    cells = []
    for f in fields:
        if f not in C.PRINT_FIELD_DEFS:
            continue
        label = (field_map.get(f) or {}).get("label", C.PRINT_FIELD_DEFS[f]["label"])
        cells.append(f"<div><span class='lbl'>{_esc(label)}：</span>"
                     f"{_esc(doc['fields'].get(f, ''))}</div>")
    grid = "".join(cells) or "<div>（未配置字段）</div>"
    return (f"<section>{title}<div class='grid' "
            f"style='grid-template-columns:repeat({cols},1fr)'>{grid}</div></section>")


_HTML = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>{title}</title><style>
@page {{ size: A4; margin: 14mm 12mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: "Microsoft YaHei", "SimSun", sans-serif; color:#111;
        font-size:12px; margin:0; }}
h1 {{ font-size:22px; text-align:center; margin:0 0 6px; letter-spacing:2px; }}
.meta {{ text-align:center; color:#555; margin-bottom:14px; }}
section {{ margin:0 0 14px; break-inside:avoid; }}
h3 {{ font-size:13px; margin:0 0 6px; padding-left:7px;
      border-left:3px solid #2563eb; }}
.grid {{ display:grid; gap:5px 14px; }}
.grid div {{ border-bottom:1px dotted #cbd5e1; padding:3px 0; }}
.lbl {{ color:#64748b; }}
table.lines {{ width:100%; border-collapse:collapse; }}
table.lines th, table.lines td {{ border:1px solid #94a3b8; padding:5px 6px;
                                text-align:left; font-size:11px; }}
table.lines th {{ background:#eef2f7; }}
.empty {{ text-align:center; color:#94a3b8; }}
.total {{ text-align:right; font-size:14px; font-weight:600; padding:6px 0; }}
.sign {{ margin-top:18px; font-size:13px; }}
.foot {{ margin-top:20px; text-align:center; color:#94a3b8; font-size:10px; }}
</style></head><body><h1>{title}</h1>
<div class="meta">{meta}</div>{body}
<div class="foot">MiniWMS 打印件 · 临时编辑不回写业务单据</div>
</body></html>"""


def render_html(doc, template=None):
    layout = (template or {}).get("layout") or default_layout()
    field_map = (template or {}).get("field_map") or _default_field_map()
    content = (layout.get("content_template") or "").strip()
    if content:
        body = _substitute(content, doc, field_map)
    else:
        blocks = layout.get("blocks") or default_layout()["blocks"]
        body = "".join(_render_block(b, doc, field_map) for b in blocks)
    meta = (f"{_esc(doc['source'])} · {_esc(doc['code'])} · "
            f"{_esc(doc['fields'].get('owner_name', ''))}")
    title = _esc(C.PRINT_NODE_LABELS.get(doc["node"], "打印件"))
    return _HTML.format(title=title, meta=meta, body=body)


# ─────────────────────────── Chrome headless 出 PDF ───────────────────────────

def chrome_path():
    """零安装：优先环境变量，其次 PATH 与常见 Windows 安装路径。"""
    env = os.environ.get("MINIWMS_CHROME")
    candidates = [env] if env else []
    for name in ("chrome", "google-chrome", "chrome.exe"):
        candidates.append(shutil.which(name))
    candidates += [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        "/usr/bin/google-chrome", "/usr/bin/chromium",
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    raise PrintError("未找到系统 Chrome，请安装或设置 MINIWMS_CHROME", "PRINT_CHROME_NOT_FOUND")


def generate_pdf(html_text, filename="document.pdf"):
    """HTML → PDF；返回 bytes。临时目录随调用结束清理，不落业务库。"""
    exe = chrome_path()
    tmp = tempfile.mkdtemp(prefix="miniwms_print_")
    html_path = os.path.join(tmp, "document.html")
    pdf_path = os.path.join(tmp, "document.pdf")
    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_text)
        url = "file:///" + html_path.replace("\\", "/")
        base = [exe, "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
                "--no-pdf-header-footer", "--user-data-dir=" + os.path.join(tmp, "profile"),
                f"--print-to-pdf={pdf_path}"]
        try:
            subprocess.run(base + ["--headless=new", url], capture_output=True,
                           timeout=C.PRINT_PDF_TIMEOUT_SECONDS, check=False)
        except subprocess.TimeoutExpired:
            raise PrintError("Chrome 生成 PDF 超时", "PRINT_PDF_TIMEOUT")
        if not os.path.isfile(pdf_path):
            # 老版本 Chrome 不认 --headless=new，退回经典 headless 再试一次
            try:
                subprocess.run(base + ["--headless", url], capture_output=True,
                               timeout=C.PRINT_PDF_TIMEOUT_SECONDS, check=False)
            except subprocess.TimeoutExpired:
                raise PrintError("Chrome 生成 PDF 超时", "PRINT_PDF_TIMEOUT")
        if not os.path.isfile(pdf_path):
            raise PrintError("Chrome 未生成 PDF 文件", "PRINT_PDF_FAILED")
        with open(pdf_path, "rb") as f:
            data = f.read()
        if not data.startswith(b"%PDF"):
            raise PrintError("Chrome 生成的文件不是 PDF", "PRINT_PDF_FAILED")
        return data, filename
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def preview(db, args):
    """预览页面取数：单据 + 生效模板 + 合并后的字段（含临时编辑默认空）。"""
    node = (args.get("node") or "").strip()
    source = (args.get("source") or "").strip()
    doc = load_document(db, node, source, args.get("doc_id"))
    t = active_template(db, node, args.get("template_id"))
    overrides = args.get("overrides")
    if overrides:
        doc = merge_overrides(doc, overrides)
    return {"ok": True, "document": doc,
            "template": t,
            "html": render_html(doc, t),
            "chrome": bool(_safe_chrome())}


def _safe_chrome():
    try:
        return chrome_path()
    except PrintError:
        return ""


def pdf_args(db, args):
    """PDF 端点：与 preview 共用同一份取数 / 渲染，避免两边口径漂移。"""
    node = (args.get("node") or "").strip()
    source = (args.get("source") or "").strip()
    doc = load_document(db, node, source, args.get("doc_id"))
    doc = merge_overrides(doc, args.get("overrides"))
    t = active_template(db, node, args.get("template_id"))
    return generate_pdf(render_html(doc, t),
                        f"{doc['node']}-{doc['code']}.pdf")
