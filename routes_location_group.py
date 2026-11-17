"""M34 货位组主数据：/api/location-groups 的增删改查与拣货顺序视图。

端点
----
- ``GET    /api/location-groups``            列表（keyword/status/warehouse_id + 分页）
- ``POST   /api/location-groups``            新建（201）
- ``GET    /api/location-groups/dict``       字典（状态 / 混放标志 / 容积语义）
- ``GET    /api/location-groups/pick-order`` 拣货顺序视图（只读，按组带库位）
- ``GET    /api/location-groups/<id>``        详情（含所属库位）
- ``PUT    /api/location-groups/<id>``        编辑（可部分）
- ``DELETE /api/location-groups/<id>``        删除（仍被库位引用则拒绝）

``LocationGroupError → 400``。货位组属仓库布局控制面，不按货主隔离（Q53）。
"""
from flask import Blueprint, jsonify, request

import location_group_service as LGS
from main import get_db, request_payload

location_group_bp = Blueprint("location_group", __name__,
                              url_prefix="/api/location-groups")


def _error(e):
    return jsonify(ok=False, error=str(e), code=e.code), 400


@location_group_bp.get("/dict")
def dict_data():
    return jsonify({"ok": True, **LGS.dictionary()})


@location_group_bp.get("/pick-order")
def pick_order():
    db = get_db()
    try:
        data = LGS.pick_order(db, request.args)
    except LGS.LocationGroupError as e:
        return _error(e)
    return jsonify({"ok": True, **data})


@location_group_bp.route("", methods=["GET"])
def list_groups():
    db = get_db()
    try:
        data = LGS.list_records(db, request.args)
    except LGS.LocationGroupError as e:
        return _error(e)
    return jsonify({"ok": True, **data})


@location_group_bp.route("", methods=["POST"])
def create_group():
    db = get_db()
    try:
        item = LGS.create(db, request_payload())
    except LGS.LocationGroupError as e:
        return _error(e)
    return jsonify({"ok": True, "location_group": item}), 201


@location_group_bp.route("/<int:rid>", methods=["GET"])
def get_group(rid):
    db = get_db()
    try:
        item = LGS.get(db, rid)
    except LGS.LocationGroupError as e:
        return _error(e)
    return jsonify({"ok": True, "location_group": item})


@location_group_bp.route("/<int:rid>", methods=["PUT"])
def update_group(rid):
    db = get_db()
    try:
        item = LGS.update(db, rid, request_payload())
    except LGS.LocationGroupError as e:
        return _error(e)
    return jsonify({"ok": True, "location_group": item})


@location_group_bp.route("/<int:rid>", methods=["DELETE"])
def delete_group(rid):
    db = get_db()
    try:
        LGS.delete(db, rid)
    except LGS.LocationGroupError as e:
        return _error(e)
    return jsonify({"ok": True})
