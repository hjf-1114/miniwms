"""M35 出库波次分配 API（服务层见 ``outbound_allocation.py``）。

- ``POST /api/outbound/plan``      只读试算：与实分配同形结果，不写任何表
- ``POST /api/outbound/allocate``  生成波次并真实占用 ``allocated_qty``
- ``POST /api/outbound/unallocate`` 取消分配（对称释放，含批次维度）
- ``GET  /api/outbound/logs``      分配/跳批/短拣/取消分配留痕（分页 + 统计）
- ``GET  /api/outbound/waves``     轻波次汇总
- ``GET  /api/outbound/dict``      策略 / 原因码 / 规则字典，供作业页下拉

质量与专属红线在服务层统一收口，路由不写死业务判断；库存写入一律经
``inventory_service.adjust_stock()``，``BEGIN IMMEDIATE`` 由本层统一开启。
"""
import sqlite3

from flask import Blueprint, g, jsonify, request

import constants as C
import outbound_allocation as OA
from main import get_db, request_payload
from inventory_service import StockError
import scope as S
import wave_analysis as WA

oa_bp = Blueprint("outbound_allocation", __name__, url_prefix="/api/outbound")


def _run(action, *args, **kwargs):
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        result = action(db, *args, **kwargs)
        db.commit()
    except OA.AllocationError as e:
        db.rollback()
        return jsonify(ok=False, error=str(e), code="ALLOCATION_ERROR"), 400
    except (ValueError, TypeError, StockError) as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), 400
    except sqlite3.IntegrityError as e:
        db.rollback()
        return jsonify(ok=False, error=f"数据冲突：{e}"), 400
    return result


def _operator():
    from flask import g
    user = getattr(g, "current_user", None) or {}
    return user.get("username", "") if isinstance(user, dict) else ""


@oa_bp.route("/dict", methods=["GET"])
def dictionary():
    return jsonify(**OA.dictionary())


def _warehouse():
    if not g.warehouse_id:
        raise ValueError("请先选择当前仓库")
    return g.warehouse_id


def _owner(args):
    owner = int(args.get("owner_id") or 0)
    if not owner:
        raise ValueError("请先选择货主")
    S.assert_owner_allowed(S.current_scope(), owner)
    return owner


@oa_bp.get("/group-analysis")
def group_analysis():
    try:
        db, warehouse = get_db(), _warehouse()
        owner = _owner(request.args)
        config = WA.settings(db, warehouse)
        plans = [WA.detail(db, warehouse, row["id"]) for row in db.execute(
            """SELECT id FROM wave_preparation WHERE warehouse_id=? AND owner_id=?
               ORDER BY id DESC LIMIT 30""", (warehouse, owner))]
        return jsonify(ok=True, result=WA.analyze(db, warehouse, owner, request.args),
                       settings=config, locations=WA.pick_locations(db, warehouse), plans=plans)
    except (ValueError, TypeError) as e:
        return jsonify(ok=False, error=str(e)), 400


@oa_bp.put("/group-analysis/settings")
def group_analysis_settings():
    def action(db):
        warehouse = _warehouse()
        WA.save_settings(db, warehouse, request_payload())
        return jsonify(ok=True, settings=WA.settings(db, warehouse))
    return _run(action)


@oa_bp.post("/group-analysis")
def create_preparation():
    payload = request_payload()
    return _run(lambda db: (jsonify(ok=True, plan=WA.prepare(
        db, _warehouse(), _owner(payload), payload, _operator())), 201))


@oa_bp.post("/group-analysis/<int:prep_id>/<step>")
def update_preparation(prep_id, step):
    if step == "finish":
        return _run(lambda db: jsonify(ok=True, plan=WA.finish(
            db, _warehouse(), prep_id, request_payload(), _operator())))
    handlers = {"release": WA.release, "cancel": WA.cancel}
    if step not in handlers:
        return jsonify(ok=False, error="未知组波操作"), 404
    return _run(lambda db: jsonify(ok=True, plan=handlers[step](db, _warehouse(), prep_id, _operator())))


@oa_bp.get("/group-analysis/<int:prep_id>/picking-list")
def preparation_picking_list(prep_id):
    try:
        return jsonify(ok=True, **WA.picking_list(get_db(), _warehouse(), prep_id))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@oa_bp.route("/plan", methods=["POST"])
def plan():
    """只读试算：不开启写事务、不写日志，前端可反复点「试算」对比策略。"""
    payload = request_payload() or {}
    db = get_db()
    try:
        result = OA.plan(
            db, payload.get("dn_ids"), line_ids=payload.get("line_ids"),
            strategy=payload.get("strategy"), operator=_operator(),
            pick_group=payload.get("pick_group"),
            wave_no=payload.get("wave_no"), dry_run=True, apply=False,
        )
    except OA.AllocationError as e:
        return jsonify(ok=False, error=str(e), code="ALLOCATION_ERROR"), 400
    except (ValueError, TypeError) as e:
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(**result)


@oa_bp.route("/allocate", methods=["POST"])
def allocate():
    payload = request_payload() or {}
    strategy = payload.get("strategy")
    if strategy and strategy not in C.OUTBOUND_STRATEGIES:
        return jsonify(ok=False, error=f"strategy 只能是 {'/'.join(C.OUTBOUND_STRATEGIES)}"), 400

    def action(db):
        result = OA.plan(
            db, payload.get("dn_ids"), line_ids=payload.get("line_ids"),
            strategy=strategy, operator=_operator(), wave_no=payload.get("wave_no"),
            pick_group=payload.get("pick_group"),
            dry_run=False, apply=True,
        )
        if payload.get("require_complete") and result["shortages"]:
            raise OA.AllocationError("所选货位范围不足以全量分配，整波次未提交；请先试算查看缺货")
        return jsonify(**result), 201

    return _run(action)


@oa_bp.route("/unallocate", methods=["POST"])
def unallocate():
    payload = request_payload() or {}
    try:
        dn_id = int(payload.get("dn_id"))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="dn_id 必填且为整数"), 400

    def action(db):
        return jsonify(**OA.unallocate(
            db, dn_id, pick_ids=payload.get("pick_ids"), operator=_operator(),
        ))

    return _run(action)


@oa_bp.route("/logs", methods=["GET"])
def logs():
    args = request.args
    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(200, max(1, int(args.get("page_size", 20))))
        dn_id = int(args.get("dn_id")) if args.get("dn_id") else None
    except ValueError:
        return jsonify(ok=False, error="page/page_size/dn_id 必须为整数"), 400
    return jsonify(**OA.list_logs(
        get_db(), wave_no=(args.get("wave_no") or "").strip(), dn_id=dn_id,
        action=(args.get("action") or "").strip(),
        date_from=(args.get("date_from") or "").strip(),
        date_to=(args.get("date_to") or "").strip(),
        page=page, page_size=page_size,
    ))


@oa_bp.route("/waves", methods=["GET"])
def waves():
    try:
        limit = min(100, max(1, int(request.args.get("limit", 20))))
    except ValueError:
        return jsonify(ok=False, error="limit 必须为整数"), 400
    return jsonify(**OA.list_waves(get_db(), limit))
