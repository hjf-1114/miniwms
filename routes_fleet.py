"""M31 司机与车辆主数据 API（服务层见 ``fleet_service.py``）。

- GET  /api/fleet/dict                    字典（状态 / 驾照 / 车型 / 效期分级）
- GET  /api/fleet/expiry                  证照效期预警（只读，按货主隔离）
- GET/POST   /api/drivers                 司机列表 / 新建
- GET/PUT/DELETE /api/drivers/<id>        司机详情 / 编辑 / 删除
- GET/POST   /api/vehicles                车辆列表 / 新建
- GET/PUT/DELETE /api/vehicles/<id>       车辆详情 / 编辑 / 删除

司机 / 车辆的 owner_id 为可空（NULL＝全系统共享），查询 allow_null=True；
写操作对非空 owner 过 assert_owner_allowed() 守门。
"""
from flask import Blueprint, jsonify, request

import fleet_service as FS
from main import get_db

fleet_bp = Blueprint("fleet", __name__)


def _error(e):
    return jsonify(ok=False, error=str(e), code=e.code), 400


def _run(action, *args):
    try:
        return jsonify(action(get_db(), *args))
    except FS.FleetError as e:
        return _error(e)


def _one(kind, data):
    return jsonify(ok=True, **{kind: data})


def _list(kind, args):
    return _run(FS.list_records, kind, args)


def _create(kind):
    try:
        return _one(kind, FS.create(get_db(), kind, request.get_json(silent=True) or {})), 201
    except FS.FleetError as e:
        return _error(e)


def _get(kind, rid):
    return _run(FS.get, kind, rid)


def _update(kind, rid):
    try:
        return _one(kind, FS.update(get_db(), kind, rid,
                                    request.get_json(silent=True) or {}))
    except FS.FleetError as e:
        return _error(e)


def _delete(kind, rid):
    return _run(FS.delete, kind, rid)


@fleet_bp.get("/api/fleet/dict")
def fleet_dict():
    return jsonify(FS.dictionary())


@fleet_bp.get("/api/fleet/expiry")
def fleet_expiry():
    return _run(FS.expiry_report, request.args)


@fleet_bp.route("/api/drivers", methods=["GET", "POST"])
def drivers():
    return _list("driver", request.args) if request.method == "GET" else _create("driver")


@fleet_bp.route("/api/drivers/<int:rid>", methods=["GET", "PUT", "DELETE"])
def driver_detail(rid):
    if request.method == "GET":
        return _get("driver", rid)
    if request.method == "PUT":
        return _update("driver", rid)
    return _delete("driver", rid)


@fleet_bp.route("/api/vehicles", methods=["GET", "POST"])
def vehicles():
    return _list("vehicle", request.args) if request.method == "GET" else _create("vehicle")


@fleet_bp.route("/api/vehicles/<int:rid>", methods=["GET", "PUT", "DELETE"])
def vehicle_detail(rid):
    if request.method == "GET":
        return _get("vehicle", rid)
    if request.method == "PUT":
        return _update("vehicle", rid)
    return _delete("vehicle", rid)
