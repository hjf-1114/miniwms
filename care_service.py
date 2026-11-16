"""M37 养护管理服务层（温湿度 / 养护项目 / 周期 / 异常，GSP）。

- 计划（``care_plan``）：定义货主 / 仓 / 库位范围、养护项目、周期与阈值；
- 任务（``care_task``）：按到期日生成并快照阈值，``UNIQUE(plan_id, due_date)`` 防重复；
- 留痕（``care_log``）：生成 / 执行 / 异常 / 处置 / 取消全链路留痕。

延续 M36 4B 的克制边界：养护异常**只留痕**，不改库存、不生成调整单、
不写 ``stock_movement``，也不改 DN / ASN 单据状态。
所有写操作由路由层 ``BEGIN IMMEDIATE`` 包裹；本模块只做业务校验与数据落库。
"""
import datetime

import constants as C


class CareError(Exception):
    """养护业务错误，路由层统一转 400。"""

    def __init__(self, message, code="CARE_ERROR"):
        super().__init__(message)
        self.code = code


def dictionary():
    return {
        "projects": list(C.CARE_PROJECTS),
        "project_labels": dict(C.CARE_PROJECT_LABELS),
        "cycles": list(C.CARE_CYCLES),
        "cycle_labels": dict(C.CARE_CYCLE_LABELS),
        "cycle_days": dict(C.CARE_CYCLE_DAYS),
        "plan_statuses": list(C.CARE_PLAN_STATUSES),
        "task_statuses": list(C.CARE_TASK_STATUSES),
        "results": list(C.CARE_RESULTS),
        "result_labels": dict(C.CARE_RESULT_LABELS),
        "reasons": list(C.CARE_REASONS),
        "reason_labels": dict(C.CARE_REASON_LABELS),
        "actions": list(C.CARE_LOG_ACTIONS),
        "permissions": list(C.CARE_PERMISSIONS),
        "notes": [
            "养护异常只写 care_log，不改库存、不生成调整单（GSP 留痕边界）",
            "任务按到期计划生成并快照阈值，同一计划同一天只生成一次",
            "温湿度项目越界时不允许判定为正常，必须登记异常",
        ],
    }


# ── 内部工具 ──────────────────────────────────────────────────────────────
def _row(db, table, row_id):
    return db.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()


def _operator_safe(user):
    return (user.get("username", "") if isinstance(user, dict) else "") or ""


def _date(value, label="日期"):
    text = str(value or "").strip()
    try:
        return datetime.date.fromisoformat(text).isoformat()
    except ValueError:
        raise CareError(f"{label}格式应为 YYYY-MM-DD")


def _today():
    return datetime.date.today().isoformat()


def _num(value, label, required=False):
    if value is None or str(value).strip() == "":
        if required:
            raise CareError(f"{label}必填")
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise CareError(f"{label}必须为数字")
    if v != v or v in (float("inf"), float("-inf")):
        raise CareError(f"{label}必须为有限数字")
    return v


def _range(low, high, low_label, high_label):
    if low is not None and high is not None and low > high + C.CARE_THRESHOLD_EPS:
        raise CareError(f"{low_label}不能大于{high_label}")


def _code(db, table, prefix):
    day = datetime.date.today().strftime("%Y%m%d")
    like = f"{prefix}-{day}-%"
    n = db.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE code LIKE ?", (like,)).fetchone()["n"]
    while True:
        n += 1
        code = f"{prefix}-{day}-{n:03d}"
        if not db.execute(f"SELECT 1 FROM {table} WHERE code = ?", (code,)).fetchone():
            return code


def _resolve_cycle(payload, old=None):
    cycle = str((payload.get("cycle") if payload.get("cycle") is not None
                 else (old["cycle"] if old else C.CARE_CYCLE_WEEKLY)) or "").strip()
    if cycle not in C.CARE_CYCLES:
        raise CareError(f"养护周期只能是 {'/'.join(C.CARE_CYCLES)}")
    if cycle == C.CARE_CYCLE_CUSTOM:
        raw = payload.get("cycle_days")
        if raw is None or str(raw).strip() == "":
            raw = old["cycle_days"] if old else None
        try:
            days = int(raw)
        except (TypeError, ValueError):
            raise CareError("自定义周期必须填写 cycle_days（正整数）")
        if days <= 0:
            raise CareError("cycle_days 必须为正整数")
    else:
        days = C.CARE_CYCLE_DAYS[cycle]
    return cycle, days


def _resolve_location(db, payload, old=None):
    raw = payload.get("location_id")
    if raw is None or str(raw).strip() == "":
        return (old["location_id"], old["location_code"]) if old else (0, "")
    try:
        loc_id = int(raw)
    except (TypeError, ValueError):
        raise CareError("location_id 必须为整数")
    if loc_id == 0:
        return 0, ""
    row = db.execute("SELECT code FROM location WHERE id = ?", (loc_id,)).fetchone()
    if row is None:
        raise CareError("库位不存在", "LOCATION_NOT_FOUND")
    return loc_id, row["code"]


def _resolve_warehouse(db, payload, old=None):
    raw = payload.get("warehouse_id")
    if raw is None or str(raw).strip() == "":
        return (old["warehouse_id"], "") if old else (0, "")
    try:
        wh_id = int(raw)
    except (TypeError, ValueError):
        raise CareError("warehouse_id 必须为整数")
    if wh_id == 0:
        return 0, ""
    row = db.execute("SELECT code FROM warehouse WHERE id = ?", (wh_id,)).fetchone()
    if row is None:
        raise CareError("仓库不存在", "WAREHOUSE_NOT_FOUND")
    return wh_id, row["code"]


def _plan_values(db, payload, old=None):
    """汇总计划字段（新增 / 编辑共用），只校验，不写库。"""
    owner_raw = payload.get("owner_id")
    if old is None:
        try:
            owner_id = int(owner_raw)
        except (TypeError, ValueError):
            raise CareError("owner_id 必填且为整数")
        if db.execute("SELECT 1 FROM owner WHERE id = ?", (owner_id,)).fetchone() is None:
            raise CareError("货主不存在", "OWNER_NOT_FOUND")
    else:
        # 归属不可改：owner_id / warehouse_id / location_id 变更等同换单据
        owner_id = old["owner_id"]

    project = str(payload.get("project") if payload.get("project") is not None
                  else (old["project"] if old else "") or "").strip()
    if project not in C.CARE_PROJECTS:
        raise CareError(f"养护项目只能是 {'/'.join(C.CARE_PROJECTS)}")

    cycle, days = _resolve_cycle(payload, old)
    loc_id, loc_code = _resolve_location(db, payload, old)
    wh_id, _wh_code = _resolve_warehouse(db, payload, old)

    def threshold(name, old_row):
        raw = payload.get(name)
        if raw is None and old is not None:
            return old[name]
        return _num(raw, name)

    temp_min, temp_max = threshold("temp_min", old), threshold("temp_max", old)
    hum_min, hum_max = threshold("hum_min", old), threshold("hum_max", old)
    _range(temp_min, temp_max, "temp_min", "temp_max")
    _range(hum_min, hum_max, "hum_min", "hum_max")

    if payload.get("next_due_date") in (None, ""):
        due = old["next_due_date"] if old is not None else _today()
    else:
        due = _date(payload["next_due_date"], "next_due_date")

    status = str(payload.get("status") if payload.get("status") is not None
                 else (old["status"] if old else C.CARE_PLAN_ENABLED) or "").strip()
    if status not in C.CARE_PLAN_STATUSES:
        raise CareError(f"计划状态只能是 {'/'.join(C.CARE_PLAN_STATUSES)}")

    return {
        "owner_id": owner_id, "warehouse_id": wh_id,
        "location_id": loc_id, "location_code": loc_code,
        "project": project, "cycle": cycle, "cycle_days": days,
        "temp_min": temp_min, "temp_max": temp_max,
        "hum_min": hum_min, "hum_max": hum_max,
        "next_due_date": due, "status": status,
        "note": str(payload.get("note", "") if payload.get("note") is not None
                    else (old["note"] if old else "")),
    }


# ── 计划 ──────────────────────────────────────────────────────────────────
def create_plan(db, payload, operator=""):
    payload = payload or {}
    v = _plan_values(db, payload)
    code = _code(db, "care_plan", C.CARE_PLAN_CODE_PREFIX)
    cur = db.execute(
        """INSERT INTO care_plan
           (code, owner_id, warehouse_id, location_id, location_code, project,
            cycle, cycle_days, temp_min, temp_max, hum_min, hum_max,
            next_due_date, status, note, operator)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (code, v["owner_id"], v["warehouse_id"], v["location_id"], v["location_code"],
         v["project"], v["cycle"], v["cycle_days"], v["temp_min"], v["temp_max"],
         v["hum_min"], v["hum_max"], v["next_due_date"], v["status"],
         v["note"], _operator_safe(operator)),
    )
    return plan_detail(db, cur.lastrowid)


def update_plan(db, plan_id, payload, operator=""):
    old = _row(db, "care_plan", plan_id)
    if old is None:
        raise CareError("养护计划不存在", "PLAN_NOT_FOUND")
    v = _plan_values(db, payload or {}, old)
    db.execute(
        """UPDATE care_plan SET location_id=?, location_code=?, project=?, cycle=?,
               cycle_days=?, temp_min=?, temp_max=?, hum_min=?, hum_max=?,
               next_due_date=?, status=?, note=?, operator=?,
               updated_at=datetime('now','localtime') WHERE id=?""",
        (v["location_id"], v["location_code"], v["project"], v["cycle"],
         v["cycle_days"], v["temp_min"], v["temp_max"], v["hum_min"], v["hum_max"],
         v["next_due_date"], v["status"], v["note"], _operator_safe(operator), plan_id),
    )
    return plan_detail(db, plan_id)


def toggle_plan(db, plan_id, status, operator=""):
    old = _row(db, "care_plan", plan_id)
    if old is None:
        raise CareError("养护计划不存在", "PLAN_NOT_FOUND")
    status = str(status or "").strip()
    if status not in C.CARE_PLAN_STATUSES:
        raise CareError(f"计划状态只能是 {'/'.join(C.CARE_PLAN_STATUSES)}")
    db.execute(
        """UPDATE care_plan SET status=?, operator=?,
               updated_at=datetime('now','localtime') WHERE id=?""",
        (status, _operator_safe(operator), plan_id),
    )
    return plan_detail(db, plan_id)


def plan_detail(db, plan_id):
    row = _row(db, "care_plan", plan_id)
    if row is None:
        raise CareError("养护计划不存在", "PLAN_NOT_FOUND")
    d = dict(row)
    d["project_label"] = C.CARE_PROJECT_LABELS.get(row["project"], row["project"])
    d["cycle_label"] = C.CARE_CYCLE_LABELS.get(row["cycle"], row["cycle"])
    d["tasks"] = [dict(r) for r in db.execute(
        "SELECT * FROM care_task WHERE plan_id = ? ORDER BY due_date DESC, id DESC LIMIT 20",
        (plan_id,),
    ).fetchall()]
    return d


# ── 任务生成 ──────────────────────────────────────────────────────────────
def _log(db, task, action, operator="", **extra):
    db.execute(
        """INSERT INTO care_log
           (task_id, task_code, plan_id, plan_code, owner_id, project, due_date,
            action, result, temperature, humidity, reason_code, reason_label,
            note, operator)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (task["id"], task["code"], task["plan_id"], task["plan_code"],
         task["owner_id"], task["project"], task["due_date"], action,
         extra.get("result", ""), extra.get("temperature"), extra.get("humidity"),
         extra.get("reason_code", ""), extra.get("reason_label", ""),
         extra.get("note", ""), _operator_safe(operator)),
    )


def generate_tasks(db, plan_id=None, due_date=None, operator=""):
    """生成到期养护任务；同一计划同一天幂等，生成后推进计划下次到期日。

    目标日默认今天。为避免补造大量历史任务，逾期计划的任务到期日记为**目标日**
    而不是原始到期日（Q22 登记）；阈值与养护项目在生成时快照到任务。
    """
    target = _date(due_date, "due_date") if due_date else _today()
    if plan_id not in (None, ""):
        try:
            pid = int(plan_id)
        except (TypeError, ValueError):
            raise CareError("plan_id 必须为整数")
        plan = _row(db, "care_plan", pid)
        if plan is None:
            raise CareError("养护计划不存在", "PLAN_NOT_FOUND")
        if plan["status"] != C.CARE_PLAN_ENABLED:
            raise CareError("停用的养护计划不能生成任务", "PLAN_DISABLED")
        plans = [plan]
    else:
        plans = db.execute(
            "SELECT * FROM care_plan WHERE status = ? AND next_due_date <= ? ORDER BY id",
            (C.CARE_PLAN_ENABLED, target),
        ).fetchall()

    generated, skipped, codes = 0, 0, []
    for plan in plans:
        if plan["next_due_date"] > target:
            skipped += 1
            continue
        exists = db.execute(
            "SELECT 1 FROM care_task WHERE plan_id = ? AND due_date = ?",
            (plan["id"], target),
        ).fetchone()
        if exists:
            skipped += 1
            continue
        code = _code(db, "care_task", C.CARE_TASK_CODE_PREFIX)
        cur = db.execute(
            """INSERT INTO care_task
               (code, plan_id, plan_code, owner_id, warehouse_id, location_id,
                location_code, project, due_date, temp_min, temp_max, hum_min,
                hum_max, status, operator)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, plan["id"], plan["code"], plan["owner_id"], plan["warehouse_id"],
             plan["location_id"], plan["location_code"], plan["project"], target,
             plan["temp_min"], plan["temp_max"], plan["hum_min"], plan["hum_max"],
             C.CARE_TASK_PENDING, _operator_safe(operator)),
        )
        task = _row(db, "care_task", cur.lastrowid)
        _log(db, task, C.CARE_LOG_GENERATED, operator,
             note=f"由计划 {plan['code']} 生成（原到期 {plan['next_due_date']}）")
        codes.append(code)
        generated += 1

        # 推进到期日：按周期步进直到超过目标日，避免同一计划重复生成
        days = max(1, int(plan["cycle_days"]))
        next_due = _date(plan["next_due_date"], "next_due_date")
        while next_due <= target:
            next_due = (datetime.date.fromisoformat(next_due)
                        + datetime.timedelta(days=days)).isoformat()
        db.execute(
            """UPDATE care_plan SET next_due_date=?,
                   updated_at=datetime('now','localtime') WHERE id=?""",
            (next_due, plan["id"]),
        )
    return {"ok": True, "generated": generated, "skipped": skipped,
            "due_date": target, "codes": codes}


# ── 执行 / 处置 ───────────────────────────────────────────────────────────
def _threshold_breaks(task, temperature, humidity):
    breaks = []
    for value, low, high, name in (
        (temperature, task["temp_min"], task["temp_max"], "温度"),
        (humidity, task["hum_min"], task["hum_max"], "湿度"),
    ):
        if value is None or (low is None and high is None):
            continue
        if low is not None and value < low - C.CARE_THRESHOLD_EPS:
            breaks.append(f"{name}低于下限")
        elif high is not None and value > high + C.CARE_THRESHOLD_EPS:
            breaks.append(f"{name}高于上限")
    return breaks


def execute_task(db, task_id, payload, operator=""):
    payload = payload or {}
    task = _row(db, "care_task", task_id)
    if task is None:
        raise CareError("养护任务不存在", "TASK_NOT_FOUND")
    if task["status"] != C.CARE_TASK_PENDING:
        raise CareError(f"任务状态为 {task['status']}，不可重复执行", "TASK_CLOSED")

    temp_project = task["project"] == C.CARE_PROJECT_TEMP
    temperature = _num(payload.get("temperature"), "实测温度", required=temp_project)
    humidity = _num(payload.get("humidity"), "实测湿度", required=temp_project)

    result = str(payload.get("result") or "").strip()
    if result not in C.CARE_RESULTS:
        raise CareError(f"养护结果只能是 {'/'.join(C.CARE_RESULTS)}")
    breaks = _threshold_breaks(task, temperature, humidity)
    if breaks and result == C.CARE_RESULT_NORMAL:
        raise CareError("实测值超出养护阈值，必须登记异常：" + "、".join(breaks),
                        "THRESHOLD_EXCEEDED")

    reason_code = str(payload.get("reason_code") or "").strip()
    if result == C.CARE_RESULT_ABNORMAL:
        if not reason_code:
            raise CareError("登记异常必须选择原因码", "REASON_REQUIRED")
        if reason_code not in C.CARE_REASONS:
            raise CareError(f"异常原因码非法：{reason_code}")
    else:
        reason_code = ""

    status = C.CARE_TASK_ABNORMAL if result == C.CARE_RESULT_ABNORMAL else C.CARE_TASK_DONE
    note = str(payload.get("note") or "")
    db.execute(
        """UPDATE care_task SET status=?, result=?, temperature=?, humidity=?,
               reason_code=?, note=?, operator=?, executed_at=datetime('now','localtime'),
               updated_at=datetime('now','localtime') WHERE id=?""",
        (status, result, temperature, humidity, reason_code, note,
         _operator_safe(operator), task_id),
    )
    updated = _row(db, "care_task", task_id)
    _log(db, updated,
         C.CARE_LOG_ABNORMAL if result == C.CARE_RESULT_ABNORMAL else C.CARE_LOG_CHECKED,
         operator, result=result, temperature=temperature, humidity=humidity,
         reason_code=reason_code,
         reason_label=C.CARE_REASON_LABELS.get(reason_code, ""), note=note)
    return task_detail(db, task_id)


def handle_task(db, task_id, payload, operator=""):
    task = _row(db, "care_task", task_id)
    if task is None:
        raise CareError("养护任务不存在", "TASK_NOT_FOUND")
    if task["status"] != C.CARE_TASK_ABNORMAL:
        raise CareError("只有异常待处置的任务可以登记处置", "TASK_NOT_ABNORMAL")
    note = str((payload or {}).get("handled_note") or "").strip()
    if not note:
        raise CareError("异常处置说明必填", "HANDLED_NOTE_REQUIRED")
    handler = _operator_safe(operator)
    db.execute(
        """UPDATE care_task SET status=?, handled_note=?, handler=?,
               handled_at=datetime('now','localtime'),
               updated_at=datetime('now','localtime') WHERE id=?""",
        (C.CARE_TASK_HANDLED, note, handler, task_id),
    )
    updated = _row(db, "care_task", task_id)
    _log(db, updated, C.CARE_LOG_HANDLED, operator, result=task["result"],
         reason_code=task["reason_code"],
         reason_label=C.CARE_REASON_LABELS.get(task["reason_code"], ""), note=note)
    return task_detail(db, task_id)


def cancel_task(db, task_id, operator=""):
    task = _row(db, "care_task", task_id)
    if task is None:
        raise CareError("养护任务不存在", "TASK_NOT_FOUND")
    if task["status"] != C.CARE_TASK_PENDING:
        raise CareError("只有待执行任务可以取消", "TASK_CLOSED")
    db.execute(
        """UPDATE care_task SET status=?, operator=?,
               updated_at=datetime('now','localtime') WHERE id=?""",
        (C.CARE_TASK_CANCELLED, _operator_safe(operator), task_id),
    )
    updated = _row(db, "care_task", task_id)
    _log(db, updated, C.CARE_LOG_CANCELLED, operator, note="任务取消")
    return task_detail(db, task_id)


def task_detail(db, task_id):
    task = _row(db, "care_task", task_id)
    if task is None:
        raise CareError("养护任务不存在", "TASK_NOT_FOUND")
    d = dict(task)
    d["project_label"] = C.CARE_PROJECT_LABELS.get(task["project"], task["project"])
    d["status_label"] = {
        C.CARE_TASK_PENDING: "待执行", C.CARE_TASK_DONE: "已完成",
        C.CARE_TASK_ABNORMAL: "异常待处置", C.CARE_TASK_HANDLED: "已处置",
        C.CARE_TASK_CANCELLED: "已取消",
    }.get(task["status"], task["status"])
    d["logs"] = [dict(r) for r in db.execute(
        "SELECT * FROM care_log WHERE task_id = ? ORDER BY id", (task_id,)
    ).fetchall()]
    return d
