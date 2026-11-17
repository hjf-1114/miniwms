"""M28 经营报表：/api/operation-report 的收发汇总 / 周转率 / 库位利用率。

端点
----
- ``GET /api/operation-report/dict``               字典（期间 / 成本字段 / 利用率维度）
- ``GET /api/operation-report/overview``           经营总览（一次拿齐关键数字）
- ``GET /api/operation-report/io-summary``         按货主的收发汇总
- ``GET /api/operation-report/turnover``           按货主的库存周转
- ``GET /api/operation-report/location-utilization`` 库位利用率

全部**纯只读**：不新建业务表、不写库存流水、不改单据状态。
``OperationReportError → 400``；上下文缺失 → 403。
"""
from flask import Blueprint, jsonify, request

import operation_report_service as ORS
from main import get_db

operation_report_bp = Blueprint("operation_report", __name__,
                                url_prefix="/api/operation-report")


def _error(e):
    return jsonify(ok=False, error=str(e), code=e.code), 400


@operation_report_bp.get("/dict")
def dict_data():
    return jsonify({"ok": True, **ORS.dictionary()})


@operation_report_bp.get("/overview")
def overview():
    db = get_db()
    try:
        data = ORS.overview(db, request.args)
    except ORS.OperationReportError as e:
        return _error(e)
    return jsonify({"ok": True, **data})


@operation_report_bp.get("/io-summary")
def io_summary():
    db = get_db()
    try:
        data = ORS.io_summary(db, request.args)
    except ORS.OperationReportError as e:
        return _error(e)
    return jsonify({"ok": True, **data})


@operation_report_bp.get("/turnover")
def turnover():
    db = get_db()
    try:
        data = ORS.turnover(db, request.args)
    except ORS.OperationReportError as e:
        return _error(e)
    return jsonify({"ok": True, **data})


@operation_report_bp.get("/location-utilization")
def location_utilization():
    db = get_db()
    try:
        data = ORS.location_utilization(db, request.args)
    except ORS.OperationReportError as e:
        return _error(e)
    return jsonify({"ok": True, **data})
