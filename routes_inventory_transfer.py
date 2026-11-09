"""库存转移单（Inventory Transfer）模块：/api/inventory-transfers。

M24：同库位内只改批次信息、数量净零；制单 → 分配库存 → 审核生效终态，
空单可关闭 / 删除，已分配单须先取消分配。跨库位请走 M17 移库单。
"""
from flask import Blueprint, jsonify, request

import constants as C
import inventory_transfer_service as SVC
import scope as S
from inventory_transfer_service import TransferError
from inventory_service import StockError
from main import get_db, request_payload

it_bp = Blueprint("inventory_transfer", __name__,
                  url_prefix="/api/inventory-transfers")

# 6 个动作权限码按用户选 A4 只落常量/注释，不挂 require_permission 装饰器：
# create / allocate / unallocate / approve / close / delete → C.IT_PERMISSIONS


def _current_user_name():
    from flask import g
    user = getattr(g, "current_user", None) or {}
    return user.get("username", "") if isinstance(user, dict) else ""


def _guard_qty_fields(payload, lines=None):
    SVC.check_forbidden_qty_fields(payload)
    for i, line in enumerate(lines or []):
        if isinstance(line, dict):
            try:
                SVC.check_forbidden_qty_fields(line)
            except TransferError as e:
                raise TransferError(f"lines[{i}] {e}")


def _error(e):
    status = 404 if "不存在" in str(e) else 400
    body = {"ok": False, "error": str(e)}
    if e.code:
        body["code"] = e.code
    return jsonify(body), status


def _run(action, *args, **kwargs):
    """统一事务边界：所有动作在 BEGIN IMMEDIATE 内完成，异常整单回滚。"""
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        result = action(db, *args, **kwargs)
        db.commit()
    except TransferError as e:
        db.rollback()
        return _error(e)
    except (StockError, ValueError) as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), 400
    except S.ScopeError as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), e.status
    return result


def _list_where(args):
    where, params = [], []
    for key, col in (("status", "it.status"),
                     ("transfer_type", "it.transfer_type")):
        value = (args.get(key) or "").strip()
        if value:
            where.append(f"{col} = ?")
            params.append(value)
    keyword = (args.get("keyword") or "").strip()
    if keyword:
        where.append("(it.code LIKE ? OR it.note LIKE ?)")
        params.extend((f"%{keyword}%", f"%{keyword}%"))
    # 单据头无 owner_id：按明细行的货主归属隔离（与 M22 调整单同一口径）
    cond, p = S.owner_where(S.current_scope(), table="itl")
    if cond:
        where.append(
            "EXISTS (SELECT 1 FROM inventory_transfer_line itl "
            f"WHERE itl.transfer_id = it.id AND {cond})"
        )
        params.extend(p)
    return where, params


@it_bp.route("", methods=["GET"])
def list_transfers():
    args = request.args
    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(100, max(1, int(args.get("page_size", 20))))
    except ValueError:
        return jsonify(ok=False, error="page/page_size 必须为整数"), 400
    where, params = _list_where(args)
    sql = ("WHERE " + " AND ".join(where)) if where else ""
    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM inventory_transfer it {sql}", params
    ).fetchone()["n"]
    rows = db.execute(
        f"""SELECT it.*,
                   (SELECT COUNT(*) FROM inventory_transfer_line itl
                    WHERE itl.transfer_id = it.id) AS line_count,
                   (SELECT COALESCE(SUM(itl.qty), 0) FROM inventory_transfer_line itl
                    WHERE itl.transfer_id = it.id) AS total_qty
            FROM inventory_transfer it {sql}
            ORDER BY it.id DESC LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify(ok=True, total=total, page=page, page_size=page_size,
                   transfers=[dict(r) for r in rows])


@it_bp.route("", methods=["POST"])
def create_transfer():
    """制单：只生成 draft 空单，不含明细、不过账。"""
    payload = request_payload() or {}

    def action(db):
        _guard_qty_fields(payload)
        tid, code = SVC.create_transfer(
            db, transfer_type=str(payload.get("transfer_type") or "").strip(),
            reason_code=str(payload.get("reason_code") or "").strip(),
            creator=_current_user_name(),
            note=str(payload.get("note") or ""),
            business_at=str(payload.get("business_at") or "").strip(),
        )
        return jsonify(ok=True, id=tid, code=code, status=C.IT_DRAFT,
                       transfer=SVC.get_transfer(db, tid)), 201

    return _run(action)


@it_bp.route("/<int:tid>", methods=["GET"])
def detail_transfer(tid):
    db = get_db()
    try:
        return jsonify(ok=True, transfer=SVC.get_transfer(db, tid))
    except TransferError as e:
        return _error(e)


@it_bp.route("/<int:tid>/allocate", methods=["POST"])
def allocate(tid):
    """分配库存：建明细行 + 两层 allocated_qty 真实占用。"""
    payload = request_payload() or {}
    lines = payload.get("lines")

    def action(db):
        _guard_qty_fields(payload, lines)
        transfer = SVC.allocate(db, tid, lines, _current_user_name())
        return jsonify(ok=True, status=C.IT_ALLOCATED, transfer=transfer)

    return _run(action)


@it_bp.route("/<int:tid>/unallocate", methods=["POST"])
def unallocate(tid):
    """取消分配：对称释放两层占用，单据回 draft。"""
    payload = request_payload() or {}

    def action(db):
        _guard_qty_fields(payload)
        transfer = SVC.unallocate(db, tid, _current_user_name())
        return jsonify(ok=True, status=C.IT_DRAFT, transfer=transfer)

    return _run(action)


@it_bp.route("/<int:tid>/approve", methods=["POST"])
def approve(tid):
    """审核生效：净零成对流水 + 释放分配 + 变更文档，进入 approved 终态。"""
    payload = request_payload() or {}

    def action(db):
        _guard_qty_fields(payload)
        transfer = SVC.approve(db, tid, _current_user_name())
        return jsonify(ok=True, status=C.IT_APPROVED, transfer=transfer,
                       change_log_count=len(transfer["change_logs"]))

    return _run(action)


@it_bp.route("/<int:tid>/close", methods=["POST"])
def close(tid):
    """关闭空单：无任何库存动作。"""
    def action(db):
        transfer = SVC.close(db, tid, _current_user_name())
        return jsonify(ok=True, status=C.IT_CLOSED, transfer=transfer)

    return _run(action)


@it_bp.route("/<int:tid>/delete", methods=["POST"])
def delete(tid):
    """删除空单：标记 cancelled，保留审计。"""
    def action(db):
        transfer = SVC.delete(db, tid)
        return jsonify(ok=True, status=C.IT_CANCELLED, transfer=transfer)

    return _run(action)


@it_bp.route("/<int:tid>/reverse", methods=["POST"])
def reverse(tid):
    """冲销：生成反向 draft 空单，返回建议明细，仍需分配 + 审核。"""
    def action(db):
        new_id, code, suggested = SVC.reverse(db, tid, _current_user_name())
        return jsonify(ok=True, id=new_id, code=code, status=C.IT_DRAFT,
                       suggested_lines=suggested,
                       transfer=SVC.get_transfer(db, new_id)), 201

    return _run(action)
