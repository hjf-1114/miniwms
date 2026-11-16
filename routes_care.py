"""M37 养护管理 API（服务层见 ``care_service.py``）。

- GET  /api/care/dict                  字典（项目 / 周期 / 状态 / 原因码 / 权限码）
- GET  /api/care/overview              首页统计
- GET  /api/care/logs                  全链路留痕查询（分页 + 过滤 + 统计）
- GET  /api/care-plans                 计划列表（分页 / 状态 / 项目 + 货主隔离）
- POST /api/care-plans                 新建计划
- GET  /api/care-plans/<id>            计划详情（含最近任务）
- POST /api/care-plans/<id>/update     编辑计划
- POST /api/care-plans/<id>/toggle     启用 / 停用
- POST /api/care-tasks/generate        按到期日生成任务（幂等）
- GET  /api/care-tasks                 任务列表（分页 / 状态 / 计划 + 货主隔离）
- GET  /api/care-tasks/<id>            任务详情（含留痕）
- POST /api/care-tasks/<id>/execute    执行养护（正常 / 异常）
- POST /api/care-tasks/<id>/handle     异常处置
- POST /api/care-tasks/<id>/cancel     取消待执行任务

异常只写 ``care_log``：不改库存、不生成调整单、不写 stock_movement。
写操作统一 ``BEGIN IMMEDIATE``；按 ID 的资源详情/动作先过 owner 归属守门。
"""
import sqlite3

from flask import Blueprint, jsonify, request

import care_service as CS
import scope as S
from main import get_db, request_payload

care_bp = Blueprint("care", __name__)


def _run(action, *args, status=200, **kwargs):
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        result = action(db, *args, **kwargs)
        db.commit()
    except CS.CareError as e:
        db.rollback()
        code = 404 if e.code.endswith("_NOT_FOUND") else 400
        return jsonify(ok=False, error=str(e), code=e.code), code
    except (ValueError, TypeError) as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), 400
    except sqlite3.IntegrityError as e:
        db.rollback()
        return jsonify(ok=False, error=f"数据冲突：{e}"), 409
    return result, status


def _operator():
    from flask import g
    user = getattr(g, "current_user", None) or {}
    return user.get("username", "") if isinstance(user, dict) else ""


def _guard(db, table, row_id):
    """按 ID 访问前校验归属；上下文缺失时由 scope 的 403 错误处理器统一返回。"""
    row = db.execute(f"SELECT owner_id FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        return False
    S.assert_owner_allowed(S.current_scope(), row["owner_id"])
    return True


def _page_args(max_size=100):
    try:
        page = max(1, int(request.args.get("page", 1)))
        size = min(max_size, max(1, int(request.args.get("page_size", 20))))
    except ValueError:
        return None, None
    return page, size


# ── 字典 / 统计 / 留痕 ────────────────────────────────────────────────────
@care_bp.route("/api/care/dict", methods=["GET"])
def dictionary():
    return jsonify(ok=True, **CS.dictionary())


@care_bp.route("/api/care/overview", methods=["GET"])
def overview():
    db = get_db()
    where, params = [], []
    S.scoped_where(where, params, table="t")
    clause = "WHERE " + " AND ".join(where) if where else ""
    tasks = {r["status"]: r["n"] for r in db.execute(
        f"SELECT status, COUNT(*) AS n FROM care_task t {clause} GROUP BY status", params
    ).fetchall()}
    plans_where, plans_params = [], []
    S.scoped_where(plans_where, plans_params, table="p")
    plans_clause = "WHERE " + " AND ".join(plans_where) if plans_where else ""
    plans = {r["status"]: r["n"] for r in db.execute(
        f"SELECT status, COUNT(*) AS n FROM care_plan p {plans_clause} GROUP BY status",
        plans_params,
    ).fetchall()}
    from datetime import date
    today = date.today().isoformat()
    due_today = db.execute(
        f"SELECT COUNT(*) AS n FROM care_task t {clause} "
        + ("AND " if clause else "WHERE ") + "t.due_date = ?",
        params + [today],
    ).fetchone()["n"]
    return jsonify(ok=True, plans=plans, tasks=tasks, due_today=due_today,
                   abnormal_open=tasks.get("abnormal", 0), today=today)


@care_bp.route("/api/care/logs", methods=["GET"])
def logs():
    where, params = [], []
    S.scoped_where(where, params, table="l")
    for key in ("action", "plan_id", "task_id", "project", "reason_code"):
        if (v := (request.args.get(key) or "").strip()):
            where.append(f"l.{key} = ?")
            params.append(v)
    if (v := (request.args.get("keyword") or "").strip()):
        where.append("(l.task_code LIKE ? OR l.plan_code LIKE ? OR l.note LIKE ?)")
        params += [f"%{v}%", f"%{v}%", f"%{v}%"]
    clause = "WHERE " + " AND ".join(where) if where else ""
    page, size = _page_args(200)
    if page is None:
        return jsonify(ok=False, error="page/page_size 必须为整数"), 400
    db = get_db()
    total = db.execute(f"SELECT COUNT(*) AS n FROM care_log l {clause}", params).fetchone()["n"]
    rows = [dict(r) for r in db.execute(
        f"SELECT * FROM care_log l {clause} ORDER BY l.id DESC LIMIT ? OFFSET ?",
        params + [size, (page - 1) * size],
    ).fetchall()]
    stats = {r["action"]: r["n"] for r in db.execute(
        "SELECT action, COUNT(*) AS n FROM care_log GROUP BY action"
    ).fetchall()}
    return jsonify(ok=True, total=total, page=page, page_size=size,
                   logs=rows, stats=stats)


# ── 养护计划 ──────────────────────────────────────────────────────────────
@care_bp.route("/api/care-plans", methods=["POST"])
def create_plan():
    return _run(CS.create_plan, request_payload() or {}, _operator(), status=201)


@care_bp.route("/api/care-plans", methods=["GET"])
def list_plans():
    where, params = [], []
    S.scoped_where(where, params, table="p")
    for key in ("status", "project", "owner_id"):
        if (v := (request.args.get(key) or "").strip()):
            where.append(f"p.{key} = ?")
            params.append(v)
    if (v := (request.args.get("keyword") or "").strip()):
        where.append("(p.code LIKE ? OR p.location_code LIKE ? OR p.note LIKE ?)")
        params += [f"%{v}%", f"%{v}%", f"%{v}%"]
    clause = "WHERE " + " AND ".join(where) if where else ""
    page, size = _page_args()
    if page is None:
        return jsonify(ok=False, error="page/page_size 必须为整数"), 400
    db = get_db()
    total = db.execute(f"SELECT COUNT(*) AS n FROM care_plan p {clause}", params).fetchone()["n"]
    rows = [dict(r) for r in db.execute(
        f"""SELECT p.*, o.code AS owner_code, o.name AS owner_name,
                   w.code AS warehouse_code
            FROM care_plan p
            JOIN owner o ON o.id = p.owner_id
            LEFT JOIN warehouse w ON w.id = p.warehouse_id
            {clause} ORDER BY p.id DESC LIMIT ? OFFSET ?""",
        params + [size, (page - 1) * size],
    ).fetchall()]
    labels = CS.dictionary()["project_labels"]
    for r in rows:
        r["project_label"] = labels.get(r["project"], r["project"])
    return jsonify(ok=True, total=total, page=page, page_size=size, plans=rows)


@care_bp.route("/api/care-plans/<int:plan_id>", methods=["GET"])
def get_plan(plan_id):
    db = get_db()
    if not _guard(db, "care_plan", plan_id):
        return jsonify(ok=False, error="养护计划不存在"), 404
    return _run(CS.plan_detail, plan_id)


@care_bp.route("/api/care-plans/<int:plan_id>/update", methods=["POST"])
def update_plan(plan_id):
    db = get_db()
    if not _guard(db, "care_plan", plan_id):
        return jsonify(ok=False, error="养护计划不存在"), 404
    return _run(CS.update_plan, plan_id, request_payload() or {}, _operator())


@care_bp.route("/api/care-plans/<int:plan_id>/toggle", methods=["POST"])
def toggle_plan(plan_id):
    db = get_db()
    if not _guard(db, "care_plan", plan_id):
        return jsonify(ok=False, error="养护计划不存在"), 404
    return _run(CS.toggle_plan, plan_id,
                (request_payload() or {}).get("status"), _operator())


# ── 养护任务 ──────────────────────────────────────────────────────────────
@care_bp.route("/api/care-tasks/generate", methods=["POST"])
def generate_tasks():
    body = request_payload() or {}
    return _run(CS.generate_tasks, body.get("plan_id"), body.get("due_date"),
                _operator(), status=201)


@care_bp.route("/api/care-tasks", methods=["GET"])
def list_tasks():
    where, params = [], []
    S.scoped_where(where, params, table="t")
    for key in ("status", "plan_id", "project", "owner_id"):
        if (v := (request.args.get(key) or "").strip()):
            where.append(f"t.{key} = ?")
            params.append(v)
    if (v := (request.args.get("keyword") or "").strip()):
        where.append("(t.code LIKE ? OR t.plan_code LIKE ? OR t.location_code LIKE ?)")
        params += [f"%{v}%", f"%{v}%", f"%{v}%"]
    if (v := (request.args.get("due_date") or "").strip()):
        where.append("t.due_date = ?")
        params.append(v)
    clause = "WHERE " + " AND ".join(where) if where else ""
    page, size = _page_args()
    if page is None:
        return jsonify(ok=False, error="page/page_size 必须为整数"), 400
    db = get_db()
    total = db.execute(f"SELECT COUNT(*) AS n FROM care_task t {clause}", params).fetchone()["n"]
    rows = [dict(r) for r in db.execute(
        f"""SELECT t.*, o.code AS owner_code, o.name AS owner_name
            FROM care_task t JOIN owner o ON o.id = t.owner_id
            {clause} ORDER BY t.id DESC LIMIT ? OFFSET ?""",
        params + [size, (page - 1) * size],
    ).fetchall()]
    labels = CS.dictionary()["project_labels"]
    for r in rows:
        r["project_label"] = labels.get(r["project"], r["project"])
    stats = {r["status"]: r["n"] for r in db.execute(
        "SELECT status, COUNT(*) AS n FROM care_task GROUP BY status"
    ).fetchall()}
    return jsonify(ok=True, total=total, page=page, page_size=size,
                   tasks=rows, stats=stats)


@care_bp.route("/api/care-tasks/<int:task_id>", methods=["GET"])
def get_task(task_id):
    db = get_db()
    if not _guard(db, "care_task", task_id):
        return jsonify(ok=False, error="养护任务不存在"), 404
    return _run(CS.task_detail, task_id)


@care_bp.route("/api/care-tasks/<int:task_id>/execute", methods=["POST"])
def execute_task(task_id):
    db = get_db()
    if not _guard(db, "care_task", task_id):
        return jsonify(ok=False, error="养护任务不存在"), 404
    return _run(CS.execute_task, task_id, request_payload() or {}, _operator())


@care_bp.route("/api/care-tasks/<int:task_id>/handle", methods=["POST"])
def handle_task(task_id):
    db = get_db()
    if not _guard(db, "care_task", task_id):
        return jsonify(ok=False, error="养护任务不存在"), 404
    return _run(CS.handle_task, task_id, request_payload() or {}, _operator())


@care_bp.route("/api/care-tasks/<int:task_id>/cancel", methods=["POST"])
def cancel_task(task_id):
    db = get_db()
    if not _guard(db, "care_task", task_id):
        return jsonify(ok=False, error="养护任务不存在"), 404
    return _run(CS.cancel_task, task_id, _operator())
