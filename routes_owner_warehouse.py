"""货主 × 仓库业务关系（格子）配置。

用户口径：「货主和仓库之间不是一对一关系，而是多对多关系」——
货主 A 在杭州仓 / 上海仓有业务，在南京仓无业务；这类「格子」不能靠
「账号可见仓集合 × 账号可见货主集合」的笛卡尔积表达。

本模块只维护**业务关系登记**（控制面，中央库权威）。真正的过滤由
``scope.apply_owner_warehouse_grid()`` 在 ``before_request`` 里完成，
13 个列表接口、写路径守门、按 ID 守门自动跟着收紧，无需逐个路由改代码。

兼容口径：某个仓**没有任何 active 登记行** = 该仓未启用格子管控，
行为与增强前完全一致（clone 即用、老库零迁移）。一旦有登记行，
该仓即按白名单严格执行。清空该仓的所有勾选即关闭。
"""
from flask import Blueprint, jsonify

from main import get_control_db, request_payload
from auth import require_permission

ow_bp = Blueprint("owner_warehouse", __name__)


def _matrix(db):
    owners = [dict(r) for r in db.execute(
        "SELECT id, code, name, status FROM owner ORDER BY id"
    )]
    warehouses = [dict(r) for r in db.execute(
        "SELECT id, code, name, status FROM warehouse ORDER BY id"
    )]
    cells = [dict(r) for r in db.execute(
        "SELECT owner_id, warehouse_id, status FROM owner_warehouse "
        "WHERE status = 'active' ORDER BY warehouse_id, owner_id"
    )]
    enabled = sorted({c["warehouse_id"] for c in cells})
    return {
        "ok": True, "owners": owners, "warehouses": warehouses,
        "cells": cells, "enabled_warehouse_ids": enabled,
    }


def _parse_owner_ids(payload):
    raw = payload.get("owner_ids", [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("owner_ids 必须是数组")
    out = []
    for v in raw:
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise ValueError("owner_ids 元素必须是整数")
        if n not in out:
            out.append(n)
    return out


@ow_bp.route("/api/owner-warehouses", methods=["GET"])
@require_permission("user:manage")
def get_owner_warehouses():
    """矩阵页数据：全部货主 × 全部仓库 + 已勾选的格子 + 已启用格子的仓。"""
    db = get_control_db()
    return jsonify(**_matrix(db))


@ow_bp.route("/api/owner-warehouses/<int:wid>", methods=["PUT"])
@require_permission("user:manage")
def save_owner_warehouses(wid):
    """全量替换某个仓的登记行；``owner_ids=[]`` 表示关闭该仓的格子管控。

    用「按仓全量替换」而不是逐格 PATCH，避免前端保存一半留下中间状态；
    请求体不带顶层 ``owner_id``，因此不会误触发写路径归属守门。
    """
    db = get_control_db()
    if db.execute("SELECT id FROM warehouse WHERE id = ?", (wid,)).fetchone() is None:
        return jsonify(ok=False, error="仓库不存在"), 404
    try:
        owner_ids = _parse_owner_ids(request_payload())
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    for oid in owner_ids:
        if db.execute("SELECT id FROM owner WHERE id = ?", (oid,)).fetchone() is None:
            return jsonify(ok=False, error=f"货主 {oid} 不存在"), 400

    db.execute("DELETE FROM owner_warehouse WHERE warehouse_id = ?", (wid,))
    if owner_ids:
        db.executemany(
            "INSERT INTO owner_warehouse (owner_id, warehouse_id, status) "
            "VALUES (?, ?, 'active')",
            [(oid, wid) for oid in owner_ids],
        )
    db.commit()
    return jsonify(**_matrix(db))
