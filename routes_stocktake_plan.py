"""盘点方案（Stocktake Plan）模块：/api/stocktake-plans。

M22：方案只是「怎么选行、怎么盘」的模板；盘点单是「这一次的实例」。
``freeze()`` 会把生效范围写进 ``stocktake.scope_snapshot``，之后改方案不影响历史单据。
"""
from flask import Blueprint, jsonify, request

import constants as C
from main import get_db, request_payload
from stocktake_service import PlanError, clean_scope, dumps, effective_config, loads, trial

plan_bp = Blueprint("stocktake_plan", __name__, url_prefix="/api/stocktake-plans")

_FIELDS = ("code", "name", "scope_type", "blind_mode", "dynamic_baseline",
           "lookback_days", "movement_from", "movement_to", "auto_adjust",
           "include_zero", "enabled", "note")


def _validate(payload, *, creating):
    """校验方案字段，返回可落库的 dict；非法值抛 PlanError。

    POST 给全套默认值；PUT 只更新请求体里出现的字段（避免漏传把 enabled 置 0）。
    """
    errors, out = [], {}

    def has(key):
        return creating or key in payload

    code = str(payload.get("code") or "").strip()
    name = str(payload.get("name") or "").strip()
    if creating and not code:
        errors.append("code 必填")
    if creating and not name:
        errors.append("name 必填")
    if has("code"):
        out["code"] = code
    if has("name"):
        out["name"] = name
    if has("scope_type"):
        v = payload.get("scope_type") or C.SCOPE_FULL
        if v not in C.SCOPE_TYPES:
            errors.append(f"scope_type 非法: {v}")
        out["scope_type"] = v
    if has("blind_mode"):
        # 与 DDL 默认值和方案页表单默认值保持一致：新建方案默认「录入隐藏账存」
        v = payload.get("blind_mode") or C.BLIND_HIDE_BOOK
        if v not in C.BLIND_MODES:
            errors.append(f"blind_mode 非法: {v}")
        out["blind_mode"] = v
    if has("dynamic_baseline"):
        v = payload.get("dynamic_baseline") or C.BASELINE_LAST_CLOSED
        if v not in C.BASELINE_TYPES:
            errors.append(f"dynamic_baseline 非法: {v}")
        out["dynamic_baseline"] = v
    if has("lookback_days"):
        try:
            out["lookback_days"] = max(0, int(payload.get("lookback_days") or 0))
        except (TypeError, ValueError):
            errors.append("lookback_days 必须为整数")
    if has("movement_types"):
        types = payload.get("movement_types") or []
        if isinstance(types, str):
            types = loads(types, [])
        if not isinstance(types, list):
            errors.append("movement_types 必须为数组")
        else:
            bad = [t for t in types if t not in C.DYNAMIC_MOVEMENT_TYPES]
            if bad:
                errors.append(f"movement_types 含未知交易类型: {bad}")
            out["movement_types"] = dumps(types)
    if has("scope"):
        scope = payload.get("scope") or {}
        if not isinstance(scope, dict):
            errors.append("scope 必须为对象")
        else:
            out["scope"] = dumps(clean_scope(scope))
    for key in ("movement_from", "movement_to", "note"):
        if has(key):
            out[key] = str(payload.get(key) or "").strip()
    for key in ("auto_adjust", "include_zero", "enabled"):
        raw = payload.get(key)
        if has(key) and raw is not None:
            out[key] = 1 if raw in (1, True, "1") else 0
        elif creating:
            out[key] = 1 if key == "enabled" else 0
    if errors:
        raise PlanError("；".join(errors))
    return out


def _dict(row):
    d = dict(row)
    d["scope"] = loads(d.get("scope"), {})
    d["movement_types"] = loads(d.get("movement_types"), [])
    return d


@plan_bp.route("", methods=["GET"])
def list_plans():
    keyword = (request.args.get("keyword") or "").strip()
    where, params = [], []
    if keyword:
        where.append("(code LIKE ? OR name LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    sql = ("WHERE " + " AND ".join(where)) if where else ""
    db = get_db()
    rows = db.execute(
        f"SELECT * FROM stocktake_plan {sql} ORDER BY id", params
    ).fetchall()
    return jsonify(ok=True, plans=[_dict(r) for r in rows], total=len(rows))


@plan_bp.route("", methods=["POST"])
def create_plan():
    db = get_db()
    try:
        row = _validate(request_payload(), creating=True)
    except PlanError as e:
        return jsonify(ok=False, error=str(e)), 400
    if db.execute("SELECT id FROM stocktake_plan WHERE code = ?",
                  (row["code"],)).fetchone() is not None:
        return jsonify(ok=False, error=f"方案编码已存在: {row['code']}"), 409
    cols = ",".join(row)
    marks = ",".join("?" for _ in row)
    cur = db.execute(
        f"INSERT INTO stocktake_plan ({cols}) VALUES ({marks})", list(row.values())
    )
    db.commit()
    return jsonify(ok=True, plan=_dict(
        db.execute("SELECT * FROM stocktake_plan WHERE id = ?", (cur.lastrowid,)).fetchone()
    )), 201


@plan_bp.route("/<int:plan_id>", methods=["GET"])
def get_plan(plan_id):
    db = get_db()
    row = db.execute("SELECT * FROM stocktake_plan WHERE id = ?", (plan_id,)).fetchone()
    if row is None:
        return jsonify(ok=False, error="盘点方案不存在"), 404
    return jsonify(ok=True, plan=_dict(row))


@plan_bp.route("/<int:plan_id>", methods=["PUT"])
def update_plan(plan_id):
    db = get_db()
    if db.execute("SELECT id FROM stocktake_plan WHERE id = ?", (plan_id,)).fetchone() is None:
        return jsonify(ok=False, error="盘点方案不存在"), 404
    try:
        row = _validate(request_payload(), creating=False)
    except PlanError as e:
        return jsonify(ok=False, error=str(e)), 400
    if "code" in row and db.execute(
        "SELECT id FROM stocktake_plan WHERE code = ? AND id <> ?",
        (row["code"], plan_id),
    ).fetchone() is not None:
        return jsonify(ok=False, error=f"方案编码已存在: {row['code']}"), 409
    row["updated_at"] = None  # 由 SQL 表达式生成，不参与参数绑定
    sets = ",".join(
        "updated_at = datetime('now','localtime')" if k == "updated_at" else f"{k} = ?"
        for k in row
    )
    params = [v for k, v in row.items() if k != "updated_at"] + [plan_id]
    db.execute(f"UPDATE stocktake_plan SET {sets} WHERE id = ?", params)
    db.commit()
    return jsonify(ok=True, plan=_dict(
        db.execute("SELECT * FROM stocktake_plan WHERE id = ?", (plan_id,)).fetchone()
    ))


@plan_bp.route("/<int:plan_id>", methods=["DELETE"])
def delete_plan(plan_id):
    db = get_db()
    used = db.execute(
        "SELECT COUNT(*) AS n FROM stocktake WHERE plan_id = ?", (plan_id,)
    ).fetchone()["n"]
    if used:
        return jsonify(ok=False, error=f"已有 {used} 张盘点单引用该方案，请改用「停用」"), 409
    cur = db.execute("DELETE FROM stocktake_plan WHERE id = ?", (plan_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify(ok=False, error="盘点方案不存在"), 404
    return jsonify(ok=True, deleted=plan_id)


def _run_trial(db, plan_id, payload):
    owner_raw = payload.get("owner_id")
    if owner_raw in (None, ""):
        raise PlanError("试算需要指定 owner_id")
    owner_id = int(owner_raw)
    if db.execute("SELECT id FROM owner WHERE id = ?", (owner_id,)).fetchone() is None:
        raise PlanError(f"货主不存在: id={owner_id}")
    cfg = effective_config(db, plan_id=plan_id, override=payload)
    return trial(db, cfg, owner_id)


@plan_bp.route("/trial", methods=["POST"])
def trial_unsaved():
    """方案编辑页「试算行数」：不落库，直接按请求体里的配置试算。"""
    db = get_db()
    try:
        return jsonify(ok=True, **_run_trial(db, None, request_payload()))
    except (PlanError, TypeError, ValueError) as e:
        return jsonify(ok=False, error=str(e)), 400


@plan_bp.route("/<int:plan_id>/trial", methods=["POST"])
def trial_saved(plan_id):
    db = get_db()
    try:
        return jsonify(ok=True, **_run_trial(db, plan_id, request_payload()))
    except (PlanError, TypeError, ValueError) as e:
        return jsonify(ok=False, error=str(e)), 400
