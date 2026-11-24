"""M36 出库复核服务层（用户 2026-11-10 4B 定稿）。

独立复核单 + 独立作业页 + 「登记差异」只留痕：
- 不生成库存调整单、不改库存、不写 stock_movement、不改 dn 状态机（Q18）；
- 按 ``dn_pick`` 行逐条复核（Q17 最细粒度），快照完整 8 维批次 + wave_no / rule_id；
- 差异写入 ``dn_review_diff_log``，可定位到批次 + 库位；人工改判为通过也留痕。

所有写操作由路由层 ``BEGIN IMMEDIATE`` 包裹；本模块只做业务校验与数据落库。
"""
import re

import constants as C


BATCH_COLS = (
    "batch_no", "batch_key", "internal_batch_no", "designated_customer_id",
    "goods_property", "stock_status", "expiry_date", "production_date",
)


class ReviewError(Exception):
    """复核业务错误，路由层统一转 400。"""

    def __init__(self, message, code="REVIEW_ERROR"):
        super().__init__(message)
        self.code = code


# ── 字典 ──────────────────────────────────────────────────────────────────
def dictionary():
    return {
        "statuses": list(C.REVIEW_STATUSES),
        "results": list(C.REVIEW_RESULTS),
        "result_labels": dict(C.REVIEW_RESULT_LABELS),
        "diff_reasons": list(C.REVIEW_DIFF_REASONS),
        "diff_labels": dict(C.REVIEW_DIFF_LABELS),
        "actions": [C.REVIEW_LOG_ACTION_DIFF, C.REVIEW_LOG_ACTION_CLEARED],
        "engine": C.REVIEW_ENGINE_MANUAL,
        "permissions": list(C.REVIEW_PERMISSIONS),
        "notes": [
            "差异只写 dn_review_diff_log，不生成调整单、不改库存（4B）",
            "复核独立于发货，不拦截 dn.ship（Q18）",
            "按 dn_pick 行逐条复核，差异定位到批次 + 库位（Q17）",
        ],
    }


# ── 内部工具 ──────────────────────────────────────────────────────────────
def _review_row(db, review_id):
    return db.execute("SELECT * FROM dn_review WHERE id = ?", (review_id,)).fetchone()


def _line_row(db, line_id):
    return db.execute("SELECT * FROM dn_review_line WHERE id = ?", (line_id,)).fetchone()


def _operator_safe(user):
    return (user.get("username", "") if isinstance(user, dict) else "") or ""


def _num(value, label="数量"):
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ReviewError(f"{label}必须为数字")
    if v != v or v in (float("inf"), float("-inf")):
        raise ReviewError(f"{label}必须为有限数字")
    return v


def _delta_counts(db, review_id):
    """重算复核单汇总计数（唯一事实来源，避免多处 +1/-1 漂移）。"""
    row = db.execute(
        """SELECT COUNT(*) AS total,
                  COUNT(actual_qty) AS reviewed,
                  SUM(CASE WHEN result = ? THEN 1 ELSE 0 END) AS diffs
           FROM dn_review_line WHERE review_id = ?""",
        (C.REVIEW_RESULT_DIFF, review_id),
    ).fetchone()
    return int(row["total"]), int(row["reviewed"] or 0), int(row["diffs"] or 0)


# ── 建单 ──────────────────────────────────────────────────────────────────
def create_review(db, dn_id, operator="", note=""):
    """从 DN 的拣货明细生成复核单（快照 dn_pick 当前行，不改库存）。"""
    dn = db.execute("SELECT * FROM dn WHERE id = ?", (dn_id,)).fetchone()
    if dn is None:
        raise ReviewError("DN 不存在", "DN_NOT_FOUND")
    if dn["status"] in (C.DN_CANCELLED,):
        raise ReviewError("已取消的 DN 不可复核", "DN_CANCELLED")
    if dn["status"] == C.DN_REVIEWED:
        raise ReviewError("出库单已完成复核", "DN_REVIEWED")

    picks = db.execute(
        """SELECT dp.*, l.code AS location_code, dl.product_id,
                  p.sku, p.name AS product_name
           FROM dn_pick dp
           JOIN location l ON l.id = dp.location_id
           JOIN dn_line dl ON dl.id = dp.line_id
           JOIN product p ON p.id = dl.product_id
           WHERE dp.dn_id = ? ORDER BY dp.id""",
        (dn_id,),
    ).fetchall()
    if not picks:
        raise ReviewError("该 DN 无拣货明细，无法生成复核单", "NO_PICKS")
    if dn["workflow_required"] and dn["status"] != C.DN_PICKED:
        raise ReviewError("出库单须先完成拣货", "DN_NOT_PICKED")

    exists = db.execute(
        "SELECT id FROM dn_review WHERE dn_id = ? AND status NOT IN (?, ?)",
        (dn_id, C.REVIEW_COMPLETED, C.REVIEW_CANCELLED),
    ).fetchone()
    if exists:
        raise ReviewError("该 DN 已有进行中的复核单", "DUPLICATE_REVIEW")

    # 单号 RV-YYYYMMDD-NNN（当日计数 +1）
    import datetime
    day = datetime.date.today().strftime("%Y%m%d")
    like = f"{C.REVIEW_CODE_PREFIX}-{day}-%"
    n = db.execute(
        "SELECT COUNT(*) AS n FROM dn_review WHERE code LIKE ?", (like,)
    ).fetchone()["n"]
    code = f"{C.REVIEW_CODE_PREFIX}-{day}-{n + 1:03d}"
    while db.execute("SELECT 1 FROM dn_review WHERE code = ?", (code,)).fetchone():
        n += 1
        code = f"{C.REVIEW_CODE_PREFIX}-{day}-{n + 1:03d}"

    cur = db.execute(
        """INSERT INTO dn_review
           (code, dn_id, dn_code, owner_id, customer_id, wave_no, status,
            total_lines, note, operator)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (code, dn_id, dn["code"], dn["owner_id"], dn["customer_id"],
         dn["wave_no"] or "", C.REVIEW_DRAFT, len(picks), str(note or ""),
         _operator_safe(operator)),
    )
    review_id = cur.lastrowid

    for p in picks:
        db.execute(
            """INSERT INTO dn_review_line
               (review_id, dn_id, line_id, pick_id, product_id, sku, product_name,
                location_id, location_code, batch_no, batch_key, internal_batch_no,
                designated_customer_id, goods_property, stock_status, expiry_date,
                production_date, wave_no, rule_id, expected_qty)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (review_id, dn_id, p["line_id"], p["id"], p["product_id"],
             p["sku"], p["product_name"], p["location_id"], p["location_code"],
             p["batch_no"], p["batch_key"], p["internal_batch_no"],
             p["designated_customer_id"], p["goods_property"], p["stock_status"],
             p["expiry_date"], p["production_date"], p["wave_no"], p["rule_id"],
             p["qty"]),
        )
    return _review_out(db, review_id)


# ── 逐行复核 ──────────────────────────────────────────────────────────────
def record(db, review_id, line_id, actual_qty, result, reason_code="",
           note="", operator=""):
    """复核单行。差异必填原因码；只写复核行 + 差异日志，绝不碰库存/调整单。"""
    review = _review_row(db, review_id)
    if review is None:
        raise ReviewError("复核单不存在", "REVIEW_NOT_FOUND")
    if review["status"] in C.REVIEW_TERMINAL:
        raise ReviewError(f"复核单状态为 {review['status']}，不可复核", "REVIEW_CLOSED")
    if db.execute("SELECT status FROM dn WHERE id = ?", (review["dn_id"],)).fetchone()[0] == C.DN_CANCELLED:
        raise ReviewError("出库单已取消", "DN_CANCELLED")

    line = _line_row(db, line_id)
    if line is None or line["review_id"] != review_id:
        raise ReviewError("复核行不存在或不属于该复核单", "LINE_NOT_FOUND")

    actual = _num(actual_qty, "实拣数量")
    if actual < 0:
        raise ReviewError("实拣数量不能为负数")
    if result not in C.REVIEW_RESULTS:
        raise ReviewError(f"复核结果只能是 {'/'.join(C.REVIEW_RESULTS)}")
    reason_code = str(reason_code or "").strip()
    if result == C.REVIEW_RESULT_DIFF and not reason_code:
        raise ReviewError("登记差异必须选择原因码", "REASON_REQUIRED")
    if reason_code and reason_code not in C.REVIEW_DIFF_REASONS:
        raise ReviewError(f"原因码非法：{reason_code}")
    if result == C.REVIEW_RESULT_PASS and reason_code:
        # 通过时不强制清空原因码，但差异日志不应出现：避免「通过 + 原因码」歧义
        reason_code = ""

    diff = round(actual - line["expected_qty"], 6)
    if result == C.REVIEW_RESULT_PASS and abs(diff) > 1e-9:
        raise ReviewError("实拣与应拣不一致时不能判定为通过，请登记差异", "PASS_QTY_MISMATCH")

    db.execute(
        """UPDATE dn_review_line
           SET actual_qty = ?, result = ?, diff_qty = ?, reason_code = ?,
               note = ?, operator = ?, reviewed_at = datetime('now','localtime')
           WHERE id = ?""",
        (actual, result, diff, reason_code, str(note or ""),
         _operator_safe(operator), line_id),
    )

    # 差异留痕：一条复核行一条日志，重复复核更新最近一条而不是累积（统计不重复计数）
    if result == C.REVIEW_RESULT_DIFF:
        old = db.execute(
            "SELECT id FROM dn_review_diff_log WHERE pick_id = ? AND review_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (line["pick_id"], review_id),
        ).fetchone()
        if old:
            db.execute("DELETE FROM dn_review_diff_log WHERE id = ?", (old["id"],))
        db.execute(
            """INSERT INTO dn_review_diff_log
               (review_id, review_code, dn_id, dn_code, owner_id, line_id, pick_id,
                product_id, sku, product_name, location_id, location_code,
                batch_no, batch_key, designated_customer_id, expiry_date,
                goods_property, stock_status, wave_no, expected_qty, actual_qty,
                diff_qty, reason_code, reason_label, note, action, operator)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?)""",
            (review_id, review["code"], review["dn_id"], review["dn_code"],
             review["owner_id"], line["line_id"], line["pick_id"],
             line["product_id"], line["sku"], line["product_name"],
             line["location_id"], line["location_code"], line["batch_no"],
             line["batch_key"], line["designated_customer_id"],
             line["expiry_date"], line["goods_property"], line["stock_status"],
             line["wave_no"], line["expected_qty"], actual, diff, reason_code,
             C.REVIEW_DIFF_LABELS.get(reason_code, reason_code),
             str(note or ""), C.REVIEW_LOG_ACTION_DIFF, _operator_safe(operator)),
        )
    else:
        # 由差异改判为通过：删原差异日志，另写一条 cleared 留痕保留审计轨迹
        old = db.execute(
            "SELECT id FROM dn_review_diff_log WHERE pick_id = ? AND review_id = ? "
            "AND action = ? ORDER BY id DESC LIMIT 1",
            (line["pick_id"], review_id, C.REVIEW_LOG_ACTION_DIFF),
        ).fetchone()
        if old:
            db.execute("DELETE FROM dn_review_diff_log WHERE id = ?", (old["id"],))
            db.execute(
                """INSERT INTO dn_review_diff_log
                   (review_id, review_code, dn_id, dn_code, owner_id, line_id, pick_id,
                    product_id, sku, product_name, location_id, location_code,
                    batch_no, batch_key, designated_customer_id, expiry_date,
                    goods_property, stock_status, wave_no, expected_qty, actual_qty,
                    diff_qty, note, action, operator)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?, ?)""",
                (review_id, review["code"], review["dn_id"], review["dn_code"],
                 review["owner_id"], line["line_id"], line["pick_id"],
                 line["product_id"], line["sku"], line["product_name"],
                 line["location_id"], line["location_code"], line["batch_no"],
                 line["batch_key"], line["designated_customer_id"],
                 line["expiry_date"], line["goods_property"], line["stock_status"],
                 line["wave_no"], line["expected_qty"], actual, diff,
                 str(note or ""), C.REVIEW_LOG_ACTION_CLEARED,
                 _operator_safe(operator)),
            )

    total, reviewed, diffs = _delta_counts(db, review_id)
    status = C.REVIEW_REVIEWING
    db.execute(
        """UPDATE dn_review SET status = ?, reviewed_lines = ?, diff_lines = ?,
               updated_at = datetime('now','localtime') WHERE id = ?""",
        (status, reviewed, diffs, review_id),
    )
    return {
        "ok": True, "review_id": review_id, "line_id": line_id,
        "result": result, "diff_qty": diff, "reason_code": reason_code,
        "total_lines": total, "reviewed_lines": reviewed, "diff_lines": diffs,
        "status": status,
    }


# ── 完成 / 取消 ───────────────────────────────────────────────────────────
def complete(db, review_id, operator=""):
    review = _review_row(db, review_id)
    if review is None:
        raise ReviewError("复核单不存在", "REVIEW_NOT_FOUND")
    if review["status"] in C.REVIEW_TERMINAL:
        raise ReviewError(f"复核单状态为 {review['status']}，不可重复完成", "REVIEW_CLOSED")
    dn = db.execute("SELECT * FROM dn WHERE id = ?", (review["dn_id"],)).fetchone()
    if dn["status"] == C.DN_CANCELLED:
        raise ReviewError("出库单已取消", "DN_CANCELLED")
    total, reviewed, diffs = _delta_counts(db, review_id)
    if reviewed < total:
        raise ReviewError(f"还有 {total - reviewed} 行未复核，不可完成", "UNREVIEWED_LINES")
    if db.execute("SELECT 1 FROM dn_package WHERE review_id = ?", (review_id,)).fetchone():
        mismatch = db.execute("""
            SELECT 1 FROM dn_review_line l WHERE l.review_id = ? AND
            ABS(l.actual_qty - COALESCE((SELECT SUM(qty) FROM dn_package_line WHERE review_line_id = l.id), 0)) > 1e-9
        """, (review_id,)).fetchone()
        empty = db.execute("""SELECT 1 FROM dn_package b WHERE b.review_id = ?
            AND NOT EXISTS (SELECT 1 FROM dn_package_line WHERE package_id = b.id)""", (review_id,)).fetchone()
        if mismatch or empty:
            raise ReviewError("每件复核货品都须装箱，空箱请先移除", "PACK_QTY_MISMATCH")
    db.execute(
        """UPDATE dn_review SET status = ?, reviewed_lines = ?, diff_lines = ?,
               reviewed_at = datetime('now','localtime'), operator = ?,
               updated_at = datetime('now','localtime') WHERE id = ?""",
        (C.REVIEW_COMPLETED, reviewed, diffs, _operator_safe(operator), review_id),
    )
    if dn["status"] != C.DN_SHIPPED:
        db.execute("UPDATE dn SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                   (C.DN_REVIEWED, review["dn_id"]))
    return _review_out(db, review_id)


def cancel(db, review_id, operator=""):
    review = _review_row(db, review_id)
    if review is None:
        raise ReviewError("复核单不存在", "REVIEW_NOT_FOUND")
    if review["status"] in C.REVIEW_TERMINAL:
        raise ReviewError(f"复核单状态为 {review['status']}，不可取消", "REVIEW_CLOSED")
    db.execute(
        """UPDATE dn_review SET status = ?, operator = ?,
               updated_at = datetime('now','localtime') WHERE id = ?""",
        (C.REVIEW_CANCELLED, _operator_safe(operator), review_id),
    )
    return _review_out(db, review_id)


# ── 查询输出 ──────────────────────────────────────────────────────────────
def _review_out(db, review_id):
    review = _review_row(db, review_id)
    d = dict(review)
    lines = [dict(r) for r in db.execute(
        "SELECT * FROM dn_review_line WHERE review_id = ? ORDER BY id", (review_id,)
    ).fetchall()]
    d["lines"] = lines
    logs = [dict(r) for r in db.execute(
        "SELECT * FROM dn_review_diff_log WHERE review_id = ? ORDER BY id DESC",
        (review_id,),
    ).fetchall()]
    d["diff_logs"] = logs
    d["packages"] = [dict(row) for row in db.execute("""
        SELECT b.*, p.name AS material_name, COALESCE(SUM(l.qty), 0) AS packed_qty
        FROM dn_package b LEFT JOIN product p ON p.id = b.material_id
        LEFT JOIN dn_package_line l ON l.package_id = b.id
        WHERE b.review_id = ? GROUP BY b.id ORDER BY b.id
    """, (review_id,))]
    return d


def _packing_review(db, review_id):
    review = _review_row(db, review_id)
    if review is None or review["status"] in C.REVIEW_TERMINAL:
        raise ReviewError("复核单不存在或已关闭")
    dn = db.execute("SELECT status FROM dn WHERE id = ?", (review["dn_id"],)).fetchone()
    if dn is None or dn["status"] in (C.DN_CANCELLED, C.DN_SHIPPED):
        raise ReviewError("出库单已取消或发货")
    return review


def open_package(db, review_id, code, material_barcode=""):
    import uuid

    review = _packing_review(db, review_id)
    code = str(code or "").strip() or f"BOX-{uuid.uuid4().hex[:12].upper()}"
    if len(code) > 80 or db.execute("SELECT 1 FROM dn_package WHERE code = ?", (code,)).fetchone():
        raise ReviewError("箱码过长或已经使用")
    material = None
    if material_barcode:
        rows = db.execute("""
            SELECT p.id FROM product p WHERE p.owner_id = ?
              AND COALESCE(NULLIF(p.category_l1, ''), p.category) = '包装耗材'
              AND (p.barcode = ? OR EXISTS (
                SELECT 1 FROM product_barcode WHERE product_id = p.id AND barcode = ?
              ))
        """, (review["owner_id"], material_barcode, material_barcode)).fetchall()
        if len(rows) != 1:
            raise ReviewError("耗材条码须唯一匹配当前货主的「包装耗材」分类物料")
        material = rows[0]["id"]
    cur = db.execute("INSERT INTO dn_package (dn_id, review_id, code, material_id) VALUES (?, ?, ?, ?)",
                     (review["dn_id"], review_id, code, material))
    return {"id": cur.lastrowid, "code": code}


def remove_empty_package(db, review_id, package_id):
    _packing_review(db, review_id)
    removed = db.execute("""
        DELETE FROM dn_package WHERE id = ? AND review_id = ?
          AND NOT EXISTS (SELECT 1 FROM dn_package_line WHERE package_id = dn_package.id)
    """, (package_id, review_id))
    if removed.rowcount != 1:
        raise ReviewError("只可移除本单的空箱")
    return {"ok": True}


def scan_product(db, review_id, barcode, line_id=None, package_id=None, operator=""):
    review = _packing_review(db, review_id)
    code = str(barcode or "").strip()
    if not code:
        raise ReviewError("请扫描商品条码")
    rows = db.execute("""
        SELECT l.*, CASE WHEN p.barcode = ? OR EXISTS (
            SELECT 1 FROM product_barcode b WHERE b.product_id = p.id AND b.barcode = ? AND b.is_primary = 1
        ) THEN 1 ELSE COALESCE((
            SELECT k.qty_per_package FROM product_barcode b
            JOIN product_package k ON k.id = b.package_id
            WHERE b.barcode = ? AND b.product_id = p.id ORDER BY b.id LIMIT 1
        ), 1) END AS scan_qty
        FROM dn_review_line l JOIN product p ON p.id = l.product_id
        WHERE l.review_id = ? AND (l.id = ? OR ? IS NULL)
          AND (p.barcode = ? OR EXISTS (SELECT 1 FROM product_barcode b WHERE b.product_id = p.id AND b.barcode = ?))
        ORDER BY l.id
    """, (code, code, code, review_id, line_id, line_id, code, code)).fetchall()
    eligible = [row for row in rows if row["result"] != C.REVIEW_RESULT_DIFF
                and (row["actual_qty"] or 0) + row["scan_qty"] <= row["expected_qty"] + 1e-9]
    if not eligible:
        raise ReviewError("条码不匹配，或本次扫码会超过待复核数量")
    if len(eligible) > 1:
        raise ReviewError("同一商品对应多个拣货批次，请先选择明细行")
    line = eligible[0]
    boxes = db.execute("SELECT id FROM dn_package WHERE review_id = ?", (review_id,)).fetchall()
    if boxes and package_id not in {row["id"] for row in boxes}:
        raise ReviewError("请先选中本复核单的箱码")
    if package_id and not boxes:
        raise ReviewError("当前复核单没有该箱")
    if package_id:
        db.execute("""
            INSERT INTO dn_package_line (package_id, review_line_id, qty) VALUES (?, ?, ?)
            ON CONFLICT(package_id, review_line_id) DO UPDATE SET qty = qty + excluded.qty
        """, (package_id, line["id"], line["scan_qty"]))
    total = (line["actual_qty"] or 0) + line["scan_qty"]
    if abs(total - line["expected_qty"]) <= 1e-9:
        record(db, review_id, line["id"], line["expected_qty"], C.REVIEW_RESULT_PASS, operator=operator)
    else:
        db.execute("UPDATE dn_review_line SET actual_qty = ?, operator = ? WHERE id = ?",
                   (total, _operator_safe(operator), line["id"]))
    return {"line_id": line["id"], "added_qty": line["scan_qty"], "actual_qty": total}


def get_detail(db, review_id):
    review = _review_row(db, review_id)
    if review is None:
        raise ReviewError("复核单不存在", "REVIEW_NOT_FOUND")
    return _review_out(db, review_id)
