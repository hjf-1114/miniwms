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

from flask import Blueprint, jsonify, request

import constants as C
import outbound_allocation as OA
from main import get_db, request_payload

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
    except (ValueError, TypeError) as e:
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


@oa_bp.route("/plan", methods=["POST"])
def plan():
    """只读试算：不开启写事务、不写日志，前端可反复点「试算」对比策略。"""
    payload = request_payload() or {}
    db = get_db()
    try:
        result = OA.plan(
            db, payload.get("dn_ids"), line_ids=payload.get("line_ids"),
            strategy=payload.get("strategy"), operator=_operator(),
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
            dry_run=False, apply=True,
        )
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
