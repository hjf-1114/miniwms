"""M33 序列号台账：/api/serials 的建档、查询、出库与状态变更。

端点
----
- ``GET  /api/serials/dict``              字典（状态 / 流转 / 动作 / 单据来源）
- ``GET  /api/serials``                   列表（keyword/status/owner_id/product_id/batch_no）
- ``POST /api/serials``                   建档（201，同时写入库流水）
- ``GET  /api/serials/stats``             按货品聚合的台账统计（只读）
- ``GET  /api/serials/<id>``              详情（含流水）
- ``PUT  /api/serials/<id>``              编辑非状态字段（批次 / 库位 / 备注 / 归属）
- ``POST /api/serials/<id>/outbound``     出库（在库 → 已出库）
- ``POST /api/serials/<id>/status``       状态变更（受状态机约束）

``SerialError → 400``；货主越权由 scope 层抛 ``ScopeError → 403``。
本模块**不写库存账**（``stock`` / ``stock_movement`` 零副作用，Q52）。
"""
from flask import Blueprint, jsonify, request

import serial_service as SS
from main import get_db, request_payload

serial_bp = Blueprint("serial", __name__, url_prefix="/api/serials")


def _error(e):
    return jsonify(ok=False, error=str(e), code=e.code), 400


@serial_bp.get("/dict")
def dict_data():
    return jsonify({"ok": True, **SS.dictionary()})


@serial_bp.get("/stats")
def stats():
    db = get_db()
    try:
        data = SS.stats(db, request.args)
    except SS.SerialError as e:
        return _error(e)
    return jsonify({"ok": True, **data})


@serial_bp.get("/stats/batch")
def stats_batch():
    """批量取多个货品的台账摘要（货品列表页一次拿当前页，避免 N+1 查询）。

    参数 ``product_ids`` 用英文逗号分隔的货品 ID 列表。
    """
    db = get_db()
    raw = request.args.get("product_ids") or ""
    ids = [x for x in raw.split(",") if x.strip()]
    try:
        data = SS.stats_batch(db, ids)
    except SS.SerialError as e:
        return _error(e)
    return jsonify({"ok": True, "stats": {str(k): v for k, v in data.items()}})


@serial_bp.route("", methods=["GET"])
def list_serials():
    db = get_db()
    try:
        data = SS.list_records(db, request.args)
    except SS.SerialError as e:
        return _error(e)
    return jsonify({"ok": True, **data})


@serial_bp.route("", methods=["POST"])
def create_serial():
    db = get_db()
    try:
        item = SS.create(db, request_payload())
    except SS.SerialError as e:
        return _error(e)
    return jsonify({"ok": True, "serial": item}), 201


@serial_bp.route("/<int:rid>", methods=["GET"])
def get_serial(rid):
    db = get_db()
    try:
        item = SS.get(db, rid)
    except SS.SerialError as e:
        return _error(e)
    return jsonify({"ok": True, "serial": item})


@serial_bp.route("/<int:rid>", methods=["PUT"])
def update_serial(rid):
    db = get_db()
    try:
        item = SS.update(db, rid, request_payload())
    except SS.SerialError as e:
        return _error(e)
    return jsonify({"ok": True, "serial": item})


@serial_bp.route("/<int:rid>/outbound", methods=["POST"])
def outbound_serial(rid):
    db = get_db()
    try:
        item = SS.outbound(db, rid, request_payload())
    except SS.SerialError as e:
        return _error(e)
    return jsonify({"ok": True, "serial": item})


@serial_bp.route("/<int:rid>/status", methods=["POST"])
def change_status(rid):
    db = get_db()
    try:
        item = SS.change_status(db, rid, request_payload())
    except SS.SerialError as e:
        return _error(e)
    return jsonify({"ok": True, "serial": item})
