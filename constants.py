"""全局状态枚举与常量（参考 Sentry WMS 的 constants.py 设计：状态机显式集中）。

单据状态少而明确，所有模块只从这里取状态值，禁止散落字符串。
"""

# ASN（到货通知/收货单）状态机: open → receiving → putaway → closed
ASN_OPEN = "open"              # 已创建，未收货
ASN_RECEIVING = "receiving"    # 部分或全部已收货，未（完全）上架
ASN_PUTAWAY = "putaway"        # 已收满，上架进行中
ASN_CLOSED = "closed"          # 已收满且上架完成
ASN_CANCELLED = "cancelled"    # 已取消（未收货部分作废）
ASN_STATUSES = (ASN_OPEN, ASN_RECEIVING, ASN_PUTAWAY, ASN_CLOSED, ASN_CANCELLED)

# DN（出库单/发货单）状态机: open → picking → shipped
DN_OPEN = "open"            # 已创建，未拣货
DN_PICKING = "picking"      # 拣货进行中（部分或全部已分配）
DN_SHIPPED = "shipped"      # 已发货（库存已扣减）
DN_CANCELLED = "cancelled"  # 已取消（仅未拣货可取消）
DN_STATUSES = (DN_OPEN, DN_PICKING, DN_SHIPPED, DN_CANCELLED)

# ASN（收货单）单据类型
ASN_DOC_TYPES = ("调拨", "销退", "采购", "期初", "其他")

# DN（出库单）单据类型
DN_DOC_TYPES = ("销售", "调拨", "退货", "其他")

# 库存动作流水类型
MOVEMENT_ASN_RECEIVE = "asn_receive"    # 收货入暂存位（+）
MOVEMENT_ASN_PUTAWAY_OUT = "asn_putaway_out"  # 上架出暂存位（-）
MOVEMENT_ASN_PUTAWAY_IN = "asn_putaway_in"    # 上架入目标位（+）
MOVEMENT_DN_PICK = "dn_pick"            # 拣货分配（M5）
MOVEMENT_DN_SHIP = "dn_ship"            # 发货（M5）
MOVEMENT_MOVE_OUT = "move_out"          # 移库出（M16）
MOVEMENT_MOVE_IN = "move_in"            # 移库入（M16）
MOVEMENT_ADJUSTMENT = "adjustment"      # 盘点调整（M17）

# 移库单（Stock Transfer）状态机: open → done（含 cancelled）
TRANSFER_OPEN = "open"              # 已创建，未执行
TRANSFER_DONE = "done"              # 已执行（库存已转移）
TRANSFER_CANCELLED = "cancelled"    # 已取消（仅未执行可取消）
TRANSFER_STATUSES = (TRANSFER_OPEN, TRANSFER_DONE, TRANSFER_CANCELLED)

# 盘点单（Stocktake）状态机: open → frozen → adjusted → closed
STOCKTAKE_OPEN = "open"          # 已创建，未冻结
STOCKTAKE_FROZEN = "frozen"      # 已冻结并快照账存（盲盘阶段）
STOCKTAKE_ADJUSTED = "adjusted"  # 已按盘点差异生成调整流水
STOCKTAKE_CLOSED = "closed"      # 已解冻结案
STOCKTAKE_STATUSES = (STOCKTAKE_OPEN, STOCKTAKE_FROZEN, STOCKTAKE_ADJUSTED, STOCKTAKE_CLOSED)

# ── M22 库存调整单 ──
ADJUSTMENT_PREFIX = "ADJ"            # ADJ-YYYYMMDD-NNN
ADJUSTMENT_DRAFT = "draft"           # 已制单，未过账
ADJUSTMENT_APPROVED = "approved"     # 已审核过账（终态）
ADJUSTMENT_CANCELLED = "cancelled"   # 已取消（仅 draft 可取消，盘点单可重新生成）
ADJUSTMENT_STATUSES = (ADJUSTMENT_DRAFT, ADJUSTMENT_APPROVED, ADJUSTMENT_CANCELLED)
ADJUSTMENT_MANUAL = "manual"             # 手工报损 / 报溢 / 数量修正
ADJUSTMENT_STOCKTAKE_DIFF = "stocktake_diff"  # 盘点差异人工生成
ADJUSTMENT_REVERSAL = "reversal"         # 冲销（反向单，仍需人工审核）
ADJUSTMENT_SOURCE_TYPES = (ADJUSTMENT_MANUAL, ADJUSTMENT_STOCKTAKE_DIFF,
                           ADJUSTMENT_REVERSAL)
REASON_CODES = ("报损", "报溢", "盘点差异", "错账更正", "冲销")

# ── M22 盘点方案 ──
SCOPE_FULL = "full"          # 全盘
SCOPE_PARTIAL = "partial"    # 部分盘点（范围条件组）
SCOPE_DYNAMIC = "dynamic"    # 动碰盘点
SCOPE_TYPES = (SCOPE_FULL, SCOPE_PARTIAL, SCOPE_DYNAMIC)
BLIND_NONE = "none"              # 明盘
BLIND_HIDE_BOOK = "hide_book"    # 录入时隐藏账存，复核可见差异
BLIND_HIDE_DIFF = "hide_diff"    # 录入时隐藏差异
BLIND_MODES = (BLIND_NONE, BLIND_HIDE_BOOK, BLIND_HIDE_DIFF)
BASELINE_LAST_CLOSED = "last_closed"    # 上次结案时间（默认）
BASELINE_SINCE_DAYS = "since_days"      # 近 N 天
BASELINE_EXPLICIT = "explicit"          # 创建盘点单时手工指定
BASELINE_MANUAL_WINDOW = "manual_window"  # 方案里的手工时间窗
BASELINE_TYPES = (BASELINE_LAST_CLOSED, BASELINE_SINCE_DAYS, BASELINE_EXPLICIT,
                  BASELINE_MANUAL_WINDOW)
# 部分盘点范围条件组允许的维度（维度间 AND；null / 空数组 = 不限）
SCOPE_DIMENSIONS = (
    "owner_id", "warehouse_id", "zone_id", "location_group", "location_ids",
    "loc_type", "product_ids", "category_l1", "category_l2", "main_category",
    "product_type", "batch_no", "batch_key", "goods_property", "has_stock",
)
# 动碰交易类型白名单可选项（来自 MOVEMENT_*，空数组 = 不限）
DYNAMIC_MOVEMENT_TYPES = (
    MOVEMENT_ASN_RECEIVE, MOVEMENT_ASN_PUTAWAY_OUT, MOVEMENT_ASN_PUTAWAY_IN,
    MOVEMENT_DN_PICK, MOVEMENT_DN_SHIP, MOVEMENT_MOVE_OUT, MOVEMENT_MOVE_IN,
    MOVEMENT_ADJUSTMENT,
)

# 批次（M19，医疗器械 / GSP 口径）：每次验收入库生成唯一批次号
BATCH_RECEIPT_PREFIX = "BAT"          # 入库批次流水号：BAT-YYYYMMDD-NNN
BATCH_PROFILE_PREFIX = "BP"           # 批次档案号（关键批次信息主档）
GOODS_PROPERTIES = ("合格", "不合格")  # 收货明细 / 批次流水「货品属性」枚举

# 内部批次号来源（用户口径：首次入库=批次号；销退=出库内部批次号；一般为数据传入）
INTERNAL_BATCH_FIRST_IN = "first_in"
INTERNAL_BATCH_RETURN = "return_trace"
INTERNAL_BATCH_EXTERNAL = "external"
INTERNAL_BATCH_SOURCES = (
    INTERNAL_BATCH_FIRST_IN, INTERNAL_BATCH_RETURN, INTERNAL_BATCH_EXTERNAL,
)

# ── M20 储区 / 混放 / 上架推荐 ──
TEMP_CLASSES = ("常温", "阴凉", "冷藏", "冷冻")
MIX_FIELDS = ("allow_mix_product", "allow_mix_batch", "allow_mix_owner")
MIX_LABELS = {
    "allow_mix_product": "允许产品混放",
    "allow_mix_batch": "允许批号混放",
    "allow_mix_owner": "允许货主混放",
}
# 推荐理由码：前端与日志共用，避免散落中文字符串
PUTAWAY_R_BIND = "bound_location"        # 货品绑定货位
PUTAWAY_R_ZONE = "default_zone"          # 货品推荐储区
PUTAWAY_R_SAME_PRODUCT = "same_product"  # 同产品聚拢
PUTAWAY_R_SAME_BATCH = "same_batch"      # 同批号聚拢
PUTAWAY_R_EMPTY = "empty_location"       # 空库位
PUTAWAY_R_CAPACITY = "capacity_fit"      # 剩余容量充足
PUTAWAY_REASON_LABELS = {
    PUTAWAY_R_BIND: "货品绑定货位",
    PUTAWAY_R_ZONE: "货品推荐储区",
    PUTAWAY_R_SAME_PRODUCT: "同产品聚拢",
    PUTAWAY_R_SAME_BATCH: "同批号聚拢",
    PUTAWAY_R_EMPTY: "空库位优先",
    PUTAWAY_R_CAPACITY: "剩余容量充足",
}

# ── M20 剩余：ASN/DN 单据标识位与外部单据类别 ──
# v3.3 口径：这里只落字段与解析，**不做任何混放 / 冷链 / 危险业务校验**；
# 是否拦截、拦到哪些货位，全部由 M25/M26 的上架规则行配置决定。
DOC_FLAG_FIELDS = ("is_virtual", "is_direct", "is_cold_chain", "is_hazard")
DOC_FLAG_LABELS = {
    "is_virtual": "虚拟",
    "is_direct": "直调",
    "is_cold_chain": "冷链",
    "is_hazard": "危险",
}
EXTERNAL_DOC_TYPE_MAXLEN = 32


def parse_doc_flags(payload):
    """解析 ASN/DN 的 4 个标识位与 external_doc_type。

    返回 ``(flags, external_doc_type, errors)``。兼容 0/1 整数、布尔、``'0'``/``'1'``
    字符串、``None`` 与空串；缺省按 0 / 空串处理。非法值只报字段格式错误，
    **不附加任何业务约束**（v3.3）。
    """
    flags, errors = {}, []
    for key in DOC_FLAG_FIELDS:
        raw = payload.get(key)
        if raw is None or raw == "":
            flags[key] = 0
        elif raw is True or raw == 1 or raw == "1":
            flags[key] = 1
        elif raw is False or raw == 0 or raw == "0":
            flags[key] = 0
        else:
            errors.append(f"{key} 只能是 0/1")
    ext = str(payload.get("external_doc_type") or "").strip()
    if len(ext) > EXTERNAL_DOC_TYPE_MAXLEN:
        errors.append(f"external_doc_type 最长 {EXTERNAL_DOC_TYPE_MAXLEN} 字符")
    return flags, ext, errors


# ── M23 指定客户专属库存 / 销退口径（用户 2026-11-09 确认）──
# designated_customer_id 是「出库对象锁定」，与 owner_id（物权归属）正交。
# 本轮只落规则与链路，出库分配校验不接入 routes_dn.py（用户选 A）。
CUSTOMER_RESERVED_OK = "OK"
CUSTOMER_RESERVED_FORBIDDEN = "CUSTOMER_RESERVED_FORBIDDEN"
RESERVATION_CONSUME_OWN_FIRST = "own_first"  # 建议先专属后通用，仅为规则定义
# 用户 2026-11-09 二次确认：专属库存发给其他客户「要提示 + 要留痕，但允许继续」。
# 规则层仍不拦截、不实现分配；下面两个值只用于风险放行日志的 action。
RESERVATION_RISK_ALLOWED = "risk_allowed"          # 前端二次确认后继续
RESERVATION_RISK_ALLOWED_UNACK = "risk_allowed_unack"  # API 直调，未经界面确认

# 销退单外部批次号（＝外部传入的原内部批次号）：必填；确实没有时显式填 '/'
EXTERNAL_BATCH_NO_ABSENT = "/"
EXTERNAL_BATCH_NO_MAXLEN = 64
WARN_MISSING_EXTERNAL_BATCH_NO = "MISSING_EXTERNAL_BATCH_NO"

# 质量状态按用户选 B：两个独立字段，GOODS_PROPERTIES 保持两值不变。
#   goods_property（批次维度）：合格 / 不合格
#   stock_status  （库位维度）：非限制 / 质检 / 冻结；'' = 老数据未设置
STOCK_STATUSES = ("非限制", "质检", "冻结")
STOCK_STATUS_UNSET = ""
QUALITY_SPECIAL_APPROVAL = "special_approval"  # 不合格 → 合格 的特采审批标记

# ── M24 库存转移单（用户 2026-11-09 四条 A 口径）──
# A1 只做同库位内的批次维度变更，跨库位一律走 M17 移库单；主账 stock.qty 零 UPDATE。
# A2 七类转移类型一次做全，但底层统一为「源维度组合 → 目标维度组合」的净零搬迁，
#    类型只作展示与校验标签，不写独立分支。
# A3 只做 IT 单自身的 unallocate，不做 DN 拣货分配取消。
# A4 权限码只落常量/注释（不挂装饰器）；特采只接布尔标记 + reason_code=特采。
INVENTORY_TRANSFER_PREFIX = "IT"        # IT-YYYYMMDD-NNN
IT_DRAFT = "draft"                       # 已制单，未分配
IT_ALLOCATED = "allocated"               # 已分配库存（真实占用可用库存）
IT_APPROVED = "approved"                 # 审核生效（终态，不可改不可删）
IT_CLOSED = "closed"                     # 空单关闭（无任何库存动作）
IT_CANCELLED = "cancelled"               # 空单删除标记
IT_STATUSES = (IT_DRAFT, IT_ALLOCATED, IT_APPROVED, IT_CLOSED, IT_CANCELLED)

IT_TYPE_BATCH_CORRECT = "batch_correct"      # 批号 / 效期 / 产地等错录补正
IT_TYPE_QUALITY_CHANGE = "quality_change"    # goods_property / stock_status 变更
IT_TYPE_BATCH_SPLIT = "batch_split"          # 同库位内拆成不同维度组合
IT_TYPE_CUSTOMER_BIND = "customer_bind"      # 通用 → 专属
IT_TYPE_CUSTOMER_UNBIND = "customer_unbind"  # 专属 → 通用
IT_TYPE_CUSTOMER_REBIND = "customer_rebind"  # 专属 → 另一专属（M23 章追加）
IT_TYPE_LOCATION_MOVE = "location_move"      # 保留标签；M24 内库位恒等，跨库位走 M17
IT_TYPES = (IT_TYPE_BATCH_CORRECT, IT_TYPE_QUALITY_CHANGE, IT_TYPE_BATCH_SPLIT,
            IT_TYPE_CUSTOMER_BIND, IT_TYPE_CUSTOMER_UNBIND,
            IT_TYPE_CUSTOMER_REBIND, IT_TYPE_LOCATION_MOVE)

IT_REASON_CODES = ("错录补正", "质检判定", "客户要求", "盘点差异", "特采", "冲销", "其他")
IT_SPECIAL_APPROVAL_REASON = "特采"          # 不合格 → 合格 的唯一放行原因码
IT_ERROR_QTY_NOT_ZERO = "TRANSFER_QTY_MUST_BE_ZERO"
IT_QTY_FORBIDDEN_FIELDS = ("delta_qty", "qty_adjust", "to_qty", "qty_delta")

# 6 个动作权限码：按用户选 A4，只落常量与注释，不挂 require_permission 装饰器。
IT_PERM_CREATE = "inventory_transfer:create"
IT_PERM_ALLOCATE = "inventory_transfer:allocate"
IT_PERM_UNALLOCATE = "inventory_transfer:unallocate"
IT_PERM_APPROVE = "inventory_transfer:approve"
IT_PERM_CLOSE = "inventory_transfer:close"
IT_PERM_DELETE = "inventory_transfer:delete"
IT_PERMISSIONS = (IT_PERM_CREATE, IT_PERM_ALLOCATE, IT_PERM_UNALLOCATE,
                  IT_PERM_APPROVE, IT_PERM_CLOSE, IT_PERM_DELETE)

# M24 流水类型（qty_delta 恒为 0 的成对 / 分配流水）
MOVEMENT_IT_ALLOCATE = "it_allocate"        # 分配占用（allocated_delta > 0）
MOVEMENT_IT_UNALLOCATE = "it_unallocate"    # 取消分配（allocated_delta < 0）
MOVEMENT_IT_TRANSFER = "it_transfer"        # 审核生效：源 -Q / 目标 +Q

# ── M25 上架推荐规则引擎（v3.3 配置化；用户 2026-11-10 1A/2A/3A 定稿）──
# 3A：出库分配规则与上架规则共用 putaway_rule 表，用 rule_type 分两类，M35 直接复用。
RULE_TYPE_PUTAWAY = "putaway"
RULE_TYPE_OUTBOUND = "outbound"
RULE_TYPES = (RULE_TYPE_PUTAWAY, RULE_TYPE_OUTBOUND)
RULE_SET_DEFAULT_CODE = "PUTAWAY-DEFAULT"
RULE_STATUS_ACTIVE = "active"
RULE_STATUS_DISABLED = "disabled"
RULE_STATUSES = (RULE_STATUS_ACTIVE, RULE_STATUS_DISABLED)
RULE_FALLBACK_PRIORITY = 999

# 13 维条件组（v3.3 4A：首批 13 维起步，M26 编辑器预留扩展位）
PUTAWAY_CONDITION_DIMS = (
    "doc_type", "goods_property", "is_virtual", "is_direct", "is_cold_chain",
    "is_hazard", "external_doc_type", "abc_class", "owner_id", "is_designated",
    "zone_id", "product_id", "category_l1",
)
PUTAWAY_CONDITION_LABELS = {
    "doc_type": "单据类型", "goods_property": "货品属性", "is_virtual": "虚拟标识",
    "is_direct": "直调标识", "is_cold_chain": "冷链标识", "is_hazard": "危险标识",
    "external_doc_type": "外部单据类别", "abc_class": "ABC 分类", "owner_id": "货主",
    "is_designated": "指定客户专属", "zone_id": "储区", "product_id": "物料",
    "category_l1": "一级分类",
}

# 9 种寻址步骤（业界 6 种 + 本项目 zone_by_product / same_category + M20 兼容型 all_active）
PUTAWAY_STEP_TYPES = (
    "zone_by_product", "zone_by_code", "fixed_location", "empty_in_zone",
    "same_product", "same_category", "last_putaway", "batch_location",
    # 第 9 种为 M20 兼容型：全部启用库位一起打分，避免规则一上线就改变既有排序口径。
    # 删掉它、改用上面的递降链，即按 v3.3 §10.3「三者递降 fallback」执行。
    "all_active",
)
PUTAWAY_STEP_LABELS = {
    "zone_by_product": "货品默认储区找位", "zone_by_code": "指定储区找位",
    "fixed_location": "固定库位", "empty_in_zone": "推荐空位",
    "same_product": "同品集中", "same_category": "同分类聚拢",
    "last_putaway": "最近一次上架位", "batch_location": "同内部批次号原库位",
    "all_active": "全部启用库位（M20 兼容）",
}

# 9 条内置评分规则（R07–R15，只用于同一 fallback 步骤内候选排序，不参与硬约束）
PUTAWAY_SCORE_RULES = {
    "R07": ("同关键批次信息 batch_key", 30),
    "R08": ("同批号 / 内部批次号", 25),
    "R09": ("同产品集中", 25),
    "R10": ("产品分类聚拢", 15),
    "R11": ("效期分档（FEFO 友好）", 15),
    "R12": ("ABC 周转 / 黄金位", 20),
    "R13": ("容积 best-fit", 10),
    "R14": ("动线距离", 10),
    "R15": ("零散库位合并", 10),
}

# 8 步硬约束检查项（§9.4 流水线）；每项 enabled 缺省 False＝不检查、不拦截
PUTAWAY_LIMIT_KEYS = (
    "loc_status", "storage_class", "quality_state", "capacity",
    "mix_product", "mix_batch", "mix_owner", "designated_customer",
)
PUTAWAY_REJECT_CODES = {
    "loc_status": "LOC_DISABLED",
    "storage_class": "STORAGE_CLASS_MISMATCH",
    "quality_state": "QUALITY_STATE_CONFLICT",
    "capacity": "CAPACITY_EXCEEDED",
    "mix_product": "MIX_PRODUCT_FORBIDDEN",
    "mix_batch": "MIX_BATCH_FORBIDDEN",
    "mix_owner": "MIX_OWNER_FORBIDDEN",
    "designated_customer": "DESIGNATED_CUSTOMER_CONFLICT",
}
PUTAWAY_REJECT_LABELS = {
    "LOC_DISABLED": "库位停用或类型不符",
    "STORAGE_CLASS_MISMATCH": "储区属性不匹配",
    "QUALITY_STATE_CONFLICT": "质量状态冲突",
    "CAPACITY_EXCEEDED": "剩余容量不足",
    "MIX_PRODUCT_FORBIDDEN": "该货位禁止产品混放",
    "MIX_BATCH_FORBIDDEN": "该货位禁止批号混放",
    "MIX_OWNER_FORBIDDEN": "该货位禁止货主混放",
    "DESIGNATED_CUSTOMER_CONFLICT": "该货位禁止指定客户混放",
    "NO_CANDIDATE": "无可用货位，待人工处理",
}
PUTAWAY_RECOMMEND_ACTIONS = ("preview", "adopt", "reject")

# ── M26 规则配置编辑器与推荐交互（2026-11-13）──
# 采纳 / 驳回是对一条 preview 日志的后续动作，不新增第二条日志，统计口径才不重复计数。
PUTAWAY_ACTION_PREVIEW = "preview"
PUTAWAY_ACTION_ADOPT = "adopt"
PUTAWAY_ACTION_REJECT = "reject"
# 「改位必填原因码」：用户绕过推荐货位时必须给出可审计的原因（设计 §14 阶段 F）。
# 这里只落可选清单，不限制自由文本，避免老单据 / API 直调被卡住。
PUTAWAY_OVERRIDE_REASONS = (
    "现场货位已满", "客户指定货位", "设备临时故障", "批次隔离要求", "人工判断更优", "其他",
)
# external_doc_type 字典（v3.3 4A 两步法的第二步）：只是可选值与默认储区配置，
# ASN/DN 上的 external_doc_type 仍是自由文本字段，不作外键或强制枚举校验。
EXTERNAL_DOC_TYPE_DICT_SEED = (
    ("PO", "采购订单"), ("SO", "销售订单"), ("EDI", "EDI 推送单"),
    ("RET", "销退回仓"), ("TR", "调拨入仓"), ("OTH", "其他外部单据"),
)

# ── M35 出库波次分配 + FEFO（用户 2026-11-10 3A 定稿）──────────────────
# 与 M25/M26 共用 putaway_rule 表，rule_type='outbound'；不另建规则体系。
WAVE_PREFIX = "WV"                       # WV-YYYYMMDD-NNN，轻波次不建主表
WAVE_NO_MAXLEN = 32

# 排序策略：先专属后通用（默认）/ 严格 FEFO（忽略专属分组）
OUTBOUND_STRATEGY_OWN_FIRST = "own_first"
OUTBOUND_STRATEGY_STRICT_FEFO = "strict_fefo"
OUTBOUND_STRATEGIES = (OUTBOUND_STRATEGY_OWN_FIRST, OUTBOUND_STRATEGY_STRICT_FEFO)
OUTBOUND_STRATEGY_LABELS = {
    OUTBOUND_STRATEGY_OWN_FIRST: "先专属后通用（组内 FEFO）",
    OUTBOUND_STRATEGY_STRICT_FEFO: "严格 FEFO（忽略专属分组）",
}
OUTBOUND_ENGINE_RULES = "rules"           # 命中 outbound 规则行
OUTBOUND_ENGINE_LEGACY = "legacy"         # 零启用 outbound 规则，按安全默认分配

# 分配日志 action（allocated / skipped / short / unallocated / preview）
OUTBOUND_ALLOC_ACTIONS = ("allocated", "skipped", "short", "unallocated", "preview")
OUTBOUND_ACTION_LABELS = {
    "allocated": "已分配", "skipped": "跳批", "short": "短拣",
    "unallocated": "取消分配", "preview": "试算",
}

# 跳批原因码：不合格 / 冻结 / 停用库位 / 专属冲突 / 无可用量
OUTBOUND_SKIP_QUALITY = "QUALITY_NOT_ALLOCATABLE"
OUTBOUND_SKIP_STATUS = "STOCK_STATUS_BLOCKED"
OUTBOUND_SKIP_LOCATION = "LOCATION_DISABLED"
OUTBOUND_SKIP_RESERVED = "CUSTOMER_RESERVED_FORBIDDEN"
OUTBOUND_SKIP_CODES = (OUTBOUND_SKIP_QUALITY, OUTBOUND_SKIP_STATUS,
                       OUTBOUND_SKIP_LOCATION, OUTBOUND_SKIP_RESERVED)
OUTBOUND_SKIP_LABELS = {
    OUTBOUND_SKIP_QUALITY: "不合格批次不可分配",
    OUTBOUND_SKIP_STATUS: "质检 / 冻结库存不可分配",
    OUTBOUND_SKIP_LOCATION: "库位停用或类型不符",
    OUTBOUND_SKIP_RESERVED: "专属客户不匹配（自动分配跳过）",
    "SHORTAGE": "可用量不足，已部分分配",
}

# 质量红线：goods_property / stock_status 的可分配取值（3A「不合格冻结不可分配」）
OUTBOUND_ALLOCATABLE_GOODS = ("", "合格")
OUTBOUND_BLOCKED_STATUSES = ("质检", "冻结")

# 6 个权限码：沿用 M24 口径，只落常量不挂装饰器（见 docs/OPEN_QUESTIONS.md Q08）
OUTBOUND_PERM_PLAN = "outbound_allocation:plan"
OUTBOUND_PERM_ALLOCATE = "outbound_allocation:allocate"
OUTBOUND_PERM_UNALLOCATE = "outbound_allocation:unallocate"
OUTBOUND_PERM_WAVE = "outbound_allocation:wave"
OUTBOUND_PERM_LOG = "outbound_allocation:log"
OUTBOUND_PERM_CONFIG = "outbound_allocation:config"
OUTBOUND_PERMISSIONS = (OUTBOUND_PERM_PLAN, OUTBOUND_PERM_ALLOCATE,
                        OUTBOUND_PERM_UNALLOCATE, OUTBOUND_PERM_WAVE,
                        OUTBOUND_PERM_LOG, OUTBOUND_PERM_CONFIG)

# ── M36 出库复核（用户 2026-11-10 4B 定稿）───────────────────────────────
# 独立复核单 + 独立作业页 + 登记差异只留痕（不生成调整单、不改库存）。
# 复核单状态机：draft → reviewing → completed；draft / reviewing → cancelled。
REVIEW_DRAFT = "draft"
REVIEW_REVIEWING = "reviewing"
REVIEW_COMPLETED = "completed"
REVIEW_CANCELLED = "cancelled"
REVIEW_STATUSES = (REVIEW_DRAFT, REVIEW_REVIEWING, REVIEW_COMPLETED, REVIEW_CANCELLED)
REVIEW_TERMINAL = (REVIEW_COMPLETED, REVIEW_CANCELLED)

# 复核行判定：pass = 实拣与应拣一致；diff = 登记差异（只写日志、不回填库存/单据）
REVIEW_RESULT_PASS = "pass"
REVIEW_RESULT_DIFF = "diff"
REVIEW_RESULTS = (REVIEW_RESULT_PASS, REVIEW_RESULT_DIFF)
REVIEW_RESULT_LABELS = {REVIEW_RESULT_PASS: "通过", REVIEW_RESULT_DIFF: "差异"}

# 差异原因码（Q19：先落常量，不做字典表；登记差异时必选）
REVIEW_DIFF_SHORT = "SHORT"
REVIEW_DIFF_OVER = "OVER"
REVIEW_DIFF_BATCH = "BATCH_MISMATCH"
REVIEW_DIFF_EXPIRY = "EXPIRY_MISMATCH"
REVIEW_DIFF_QUALITY = "QUALITY_MISMATCH"
REVIEW_DIFF_OTHER = "OTHER"
REVIEW_DIFF_REASONS = (REVIEW_DIFF_SHORT, REVIEW_DIFF_OVER, REVIEW_DIFF_BATCH,
                       REVIEW_DIFF_EXPIRY, REVIEW_DIFF_QUALITY, REVIEW_DIFF_OTHER)
REVIEW_DIFF_LABELS = {
    REVIEW_DIFF_SHORT: "少货（实拣少于应拣）",
    REVIEW_DIFF_OVER: "多货（实拣多于应拣）",
    REVIEW_DIFF_BATCH: "批号不符",
    REVIEW_DIFF_EXPIRY: "效期不符",
    REVIEW_DIFF_QUALITY: "质量状态不符",
    REVIEW_DIFF_OTHER: "其他（填备注说明）",
}

# 审计动作与引擎标识
REVIEW_CODE_PREFIX = "RV"
REVIEW_CODE_MAXLEN = 32
REVIEW_ENGINE_MANUAL = "manual"           # M36 首版：人工逐行复核
REVIEW_LOG_ACTION_DIFF = "diff_registered"
REVIEW_LOG_ACTION_CLEARED = "diff_cleared"  # 差异被人工改判为通过时留痕

# 6 个权限码：沿用 M24/M35 口径，只落常量不挂装饰器
REVIEW_PERM_CREATE = "dn_review:create"
REVIEW_PERM_CHECK = "dn_review:check"
REVIEW_PERM_COMPLETE = "dn_review:complete"
REVIEW_PERM_CANCEL = "dn_review:cancel"
REVIEW_PERM_LOG = "dn_review:log"
REVIEW_PERM_CONFIG = "dn_review:config"
REVIEW_PERMISSIONS = (REVIEW_PERM_CREATE, REVIEW_PERM_CHECK, REVIEW_PERM_COMPLETE,
                      REVIEW_PERM_CANCEL, REVIEW_PERM_LOG, REVIEW_PERM_CONFIG)

# ── M37 养护管理（温湿度 / 养护项目 / 周期 / 异常，GSP）──────────────────
# 细节未拍板项登记 OPEN_QUESTIONS Q21–Q25，按下列默认实现，答复后以答复为准。
CARE_PROJECT_TEMP = "TEMP_HUMIDITY"
CARE_PROJECT_APPEARANCE = "APPEARANCE"
CARE_PROJECT_PACKAGE = "PACKAGE"
CARE_PROJECT_EXPIRY = "EXPIRY"
CARE_PROJECT_STACKING = "STACKING"
CARE_PROJECTS = (CARE_PROJECT_TEMP, CARE_PROJECT_APPEARANCE, CARE_PROJECT_PACKAGE,
                 CARE_PROJECT_EXPIRY, CARE_PROJECT_STACKING)
CARE_PROJECT_LABELS = {
    CARE_PROJECT_TEMP: "温湿度",
    CARE_PROJECT_APPEARANCE: "外观检查",
    CARE_PROJECT_PACKAGE: "包装检查",
    CARE_PROJECT_EXPIRY: "效期检查",
    CARE_PROJECT_STACKING: "堆码检查",
}

CARE_CYCLE_DAILY = "daily"
CARE_CYCLE_WEEKLY = "weekly"
CARE_CYCLE_MONTHLY = "monthly"
CARE_CYCLE_QUARTERLY = "quarterly"
CARE_CYCLE_CUSTOM = "custom"
CARE_CYCLES = (CARE_CYCLE_DAILY, CARE_CYCLE_WEEKLY, CARE_CYCLE_MONTHLY,
               CARE_CYCLE_QUARTERLY, CARE_CYCLE_CUSTOM)
CARE_CYCLE_LABELS = {
    CARE_CYCLE_DAILY: "每日", CARE_CYCLE_WEEKLY: "每周",
    CARE_CYCLE_MONTHLY: "每月", CARE_CYCLE_QUARTERLY: "每季度",
    CARE_CYCLE_CUSTOM: "自定义天数",
}
CARE_CYCLE_DAYS = {
    CARE_CYCLE_DAILY: 1, CARE_CYCLE_WEEKLY: 7,
    CARE_CYCLE_MONTHLY: 30, CARE_CYCLE_QUARTERLY: 90,
}

CARE_PLAN_ENABLED = "active"
CARE_PLAN_DISABLED = "disabled"
CARE_PLAN_STATUSES = (CARE_PLAN_ENABLED, CARE_PLAN_DISABLED)

CARE_TASK_PENDING = "pending"
CARE_TASK_DONE = "done"
CARE_TASK_ABNORMAL = "abnormal"
CARE_TASK_HANDLED = "handled"
CARE_TASK_CANCELLED = "cancelled"
CARE_TASK_STATUSES = (CARE_TASK_PENDING, CARE_TASK_DONE, CARE_TASK_ABNORMAL,
                      CARE_TASK_HANDLED, CARE_TASK_CANCELLED)
CARE_RESULT_NORMAL = "normal"
CARE_RESULT_ABNORMAL = "abnormal"
CARE_RESULTS = (CARE_RESULT_NORMAL, CARE_RESULT_ABNORMAL)
CARE_RESULT_LABELS = {CARE_RESULT_NORMAL: "正常", CARE_RESULT_ABNORMAL: "异常"}

CARE_REASON_TEMP_HIGH = "TEMP_HIGH"
CARE_REASON_TEMP_LOW = "TEMP_LOW"
CARE_REASON_HUM_HIGH = "HUM_HIGH"
CARE_REASON_HUM_LOW = "HUM_LOW"
CARE_REASON_PACKAGE = "PACKAGE_DAMAGE"
CARE_REASON_EXPIRY = "EXPIRY_NEAR"
CARE_REASON_PEST = "PEST"
CARE_REASON_OTHER = "OTHER"
CARE_REASONS = (CARE_REASON_TEMP_HIGH, CARE_REASON_TEMP_LOW, CARE_REASON_HUM_HIGH,
                CARE_REASON_HUM_LOW, CARE_REASON_PACKAGE, CARE_REASON_EXPIRY,
                CARE_REASON_PEST, CARE_REASON_OTHER)
CARE_REASON_LABELS = {
    CARE_REASON_TEMP_HIGH: "温度超限（偏高）",
    CARE_REASON_TEMP_LOW: "温度超限（偏低）",
    CARE_REASON_HUM_HIGH: "湿度超限（偏高）",
    CARE_REASON_HUM_LOW: "湿度超限（偏低）",
    CARE_REASON_PACKAGE: "包装破损",
    CARE_REASON_EXPIRY: "临近效期",
    CARE_REASON_PEST: "虫蛀鼠害",
    CARE_REASON_OTHER: "其他（备注说明）",
}

CARE_LOG_GENERATED = "generated"
CARE_LOG_CHECKED = "checked"
CARE_LOG_ABNORMAL = "abnormal"
CARE_LOG_HANDLED = "handled"
CARE_LOG_CANCELLED = "cancelled"
CARE_LOG_ACTIONS = (CARE_LOG_GENERATED, CARE_LOG_CHECKED, CARE_LOG_ABNORMAL,
                    CARE_LOG_HANDLED, CARE_LOG_CANCELLED)

CARE_PLAN_CODE_PREFIX = "CP"
CARE_TASK_CODE_PREFIX = "CT"
CARE_CODE_MAXLEN = 32
CARE_THRESHOLD_EPS = 1e-9

# 权限码：沿用 M24/M35/M36 口径，只落常量不挂装饰器（Q08 统一处置）
CARE_PERM_PLAN = "care:plan"
CARE_PERM_GENERATE = "care:generate"
CARE_PERM_EXECUTE = "care:execute"
CARE_PERM_HANDLE = "care:handle"
CARE_PERM_LOG = "care:log"
CARE_PERM_CONFIG = "care:config"
CARE_PERMISSIONS = (CARE_PERM_PLAN, CARE_PERM_GENERATE, CARE_PERM_EXECUTE,
                    CARE_PERM_HANDLE, CARE_PERM_LOG, CARE_PERM_CONFIG)

# ── M38 出库单拆单 / 合单 ──────────────────────────────────────────────────
# 只操作未拣货（picked_qty=0 且无 dn_pick）的 open 出库单：不动库存、不写流水，
# 只做单据层数量重分配，全程 dn_order_log 留痕（延续 M24/M36「净零 / 只留痕」精神）。
SPLIT_ACTION_SPLIT = "split"
SPLIT_ACTION_MERGE = "merge"
SPLIT_ACTIONS = (SPLIT_ACTION_SPLIT, SPLIT_ACTION_MERGE)
SPLIT_ACTION_LABELS = {SPLIT_ACTION_SPLIT: "拆单", SPLIT_ACTION_MERGE: "合单"}

SPLIT_MODE_BY_QTY = "by_qty"        # 拆单：按指定数量拆出若干份
SPLIT_MODE_BY_LINE = "by_line"      # 拆单：按整行拆出
SPLIT_MODES = (SPLIT_MODE_BY_QTY, SPLIT_MODE_BY_LINE)
SPLIT_MODE_LABELS = {SPLIT_MODE_BY_QTY: "按数量拆分", SPLIT_MODE_BY_LINE: "按整行拆分"}

# 拆单/合单只允许的源单状态（未拣货）
SPLIT_ALLOWED_STATUS = (DN_OPEN,)
SPLIT_CODE_PREFIX = "DN"
SPLIT_CODE_MAXLEN = 32
SPLIT_QTY_EPS = 1e-9                # 数量守恒容差，与 M21 I1/I2/I3 一致

# 权限码：沿用 M24/M35/M36/M37 口径，只落常量不挂装饰器（Q08 统一处置）
SPLIT_PERM_EXECUTE = "dn:split_merge"
SPLIT_PERM_LOG = "dn:split_merge_log"
SPLIT_PERMISSIONS = (SPLIT_PERM_EXECUTE, SPLIT_PERM_LOG)

# ── M39 GSP 合规报表（只读：批次追溯 / 效期 / 养护 / 验收复核留痕）────────
# 细节未拍板项登记 OPEN_QUESTIONS Q30–Q33，按下列默认实现，答复后以答复为准。
# 纯只读、不新建表、不改库存：全部复用现有表（batch_receipt / stock_batch /
# stock_movement / care_log / dn_review_diff_log），逐端点注入货主隔离。
GSP_REPORT_TRACE = "trace"          # 批次追溯
GSP_REPORT_EXPIRY = "expiry"        # 效期预警
GSP_REPORT_CARE = "care"            # 养护留痕
GSP_REPORT_RECEIPT = "receipt"      # 验收留痕
GSP_REPORT_REVIEW = "review"        # 复核差异留痕
GSP_REPORTS = (GSP_REPORT_TRACE, GSP_REPORT_EXPIRY, GSP_REPORT_CARE,
               GSP_REPORT_RECEIPT, GSP_REPORT_REVIEW)
GSP_REPORT_LABELS = {
    GSP_REPORT_TRACE: "批次追溯", GSP_REPORT_EXPIRY: "效期预警",
    GSP_REPORT_CARE: "养护记录", GSP_REPORT_RECEIPT: "验收留痕",
    GSP_REPORT_REVIEW: "复核差异",
}

# 效期分级（按 stock_batch.expiry_date 与今天比较）
GSP_EXPIRY_EXPIRED = "expired"      # 已过期（expiry_date < today）
GSP_EXPIRY_SOON = "soon"            # 近效期（today <= expiry_date <= today+N）
GSP_EXPIRY_NORMAL = "normal"        # 正常（expiry_date > today+N）
GSP_EXPIRY_NONE = "none"            # 无有效期（expiry_date 空 / NULL）
GSP_EXPIRY_LEVELS = (GSP_EXPIRY_EXPIRED, GSP_EXPIRY_SOON,
                     GSP_EXPIRY_NORMAL, GSP_EXPIRY_NONE)
GSP_EXPIRY_LABELS = {
    GSP_EXPIRY_EXPIRED: "已过期", GSP_EXPIRY_SOON: "近效期",
    GSP_EXPIRY_NORMAL: "正常", GSP_EXPIRY_NONE: "无有效期",
}
GSP_EXPIRY_SOON_DAYS_DEFAULT = 30   # 近效期默认阈值（天），可在查询参数 days 覆盖
GSP_EXPIRY_SOON_DAYS_MAX = 365

# 追溯链环节（收货 → 验收 → 上架库存 → 库存流水 → 出库复核）
GSP_TRACE_RECEIPT = "receipt"
GSP_TRACE_STOCK = "stock"
GSP_TRACE_MOVEMENT = "movement"
GSP_TRACE_STAGES = (GSP_TRACE_RECEIPT, GSP_TRACE_STOCK, GSP_TRACE_MOVEMENT)
GSP_TRACE_STAGE_LABELS = {
    GSP_TRACE_RECEIPT: "收货验收", GSP_TRACE_STOCK: "在库批次",
    GSP_TRACE_MOVEMENT: "库存流水",
}

# 权限码：沿用 M24/M35/M36/M37/M38 口径，只落常量不挂装饰器（Q08 统一处置）
GSP_PERM_VIEW = "gsp:view"
GSP_PERM_EXPORT = "gsp:export"
GSP_PERMISSIONS = (GSP_PERM_VIEW, GSP_PERM_EXPORT)

# ── M40 工作量统计（只读聚合：按人 / 按节点 / 按时间段）──────────────────
# 细节未拍板项登记 OPEN_QUESTIONS Q34–Q37，按下列默认实现，答复后以答复为准。
# 不新建表、不改业务数据：从现有操作留痕抽事件，统一口径后再分组。
WL_NODE_RECEIVE = "receive"          # 收货验收（stock_movement.asn_receive）
WL_NODE_PUTAWAY = "putaway"          # 上架作业（asn_putaway_out / in）
WL_NODE_PICK = "pick"                # 拣货分配（dn_pick）
WL_NODE_SHIP = "ship"                # 发货出库（dn_ship）
WL_NODE_MOVE = "move"                # 移库（move_out / in）
WL_NODE_ADJUST = "adjust"            # 库存调整（adjustment）
WL_NODE_IT = "it"                    # 库存转移（it_allocate/unallocate/transfer）
WL_NODE_OTHER = "other_movement"     # 其他库存流水（未映射 ref_type）
WL_NODE_CARE = "care"                # 养护执行留痕（care_log）
WL_NODE_CARE_HANDLE = "care_handle"  # 养护异常 / 处置留痕
WL_NODE_REVIEW = "review"            # 出库复核行（dn_review_line）
WL_NODE_REVIEW_DIFF = "review_diff"  # 复核差异登记（dn_review_diff_log）
WL_NODE_DN_ORDER = "dn_order"        # 拆单 / 合单（dn_order_log）
WL_NODE_PUTAWAY_REC = "putaway_rec"  # 上架推荐决策（putaway_recommendation）
WL_NODE_OUTBOUND = "outbound_alloc"  # 波次分配留痕（outbound_allocation_log）
WL_NODE_IT_CHANGE = "it_change"      # 库存转移字段变更文档（inventory_change_log）
WORKLOAD_NODES = (
    WL_NODE_RECEIVE, WL_NODE_PUTAWAY, WL_NODE_PICK, WL_NODE_SHIP,
    WL_NODE_MOVE, WL_NODE_ADJUST, WL_NODE_IT, WL_NODE_OTHER,
    WL_NODE_CARE, WL_NODE_CARE_HANDLE, WL_NODE_REVIEW, WL_NODE_REVIEW_DIFF,
    WL_NODE_DN_ORDER, WL_NODE_PUTAWAY_REC, WL_NODE_OUTBOUND, WL_NODE_IT_CHANGE,
)
WORKLOAD_NODE_LABELS = {
    WL_NODE_RECEIVE: "收货验收", WL_NODE_PUTAWAY: "上架作业",
    WL_NODE_PICK: "拣货分配", WL_NODE_SHIP: "发货出库",
    WL_NODE_MOVE: "移库作业", WL_NODE_ADJUST: "库存调整",
    WL_NODE_IT: "库存转移", WL_NODE_OTHER: "其他库存流水",
    WL_NODE_CARE: "养护执行", WL_NODE_CARE_HANDLE: "养护异常处置",
    WL_NODE_REVIEW: "出库复核", WL_NODE_REVIEW_DIFF: "复核差异登记",
    WL_NODE_DN_ORDER: "拆单合单", WL_NODE_PUTAWAY_REC: "上架推荐决策",
    WL_NODE_OUTBOUND: "波次分配", WL_NODE_IT_CHANGE: "转移变更文档",
}

WL_PERIOD_DAY = "day"
WL_PERIOD_WEEK = "week"
WL_PERIOD_MONTH = "month"
WORKLOAD_PERIODS = (WL_PERIOD_DAY, WL_PERIOD_WEEK, WL_PERIOD_MONTH)
WORKLOAD_PERIOD_LABELS = {
    WL_PERIOD_DAY: "按日", WL_PERIOD_WEEK: "按周（ISO 周一起）", WL_PERIOD_MONTH: "按月",
}
WORKLOAD_UNKNOWN_OPERATOR = "未记录"  # 匿名演示 / 老数据 operator 为空时的归并标签
WORKLOAD_PAGE_SIZE_DEFAULT = 20
WORKLOAD_PAGE_SIZE_MAX = 200

# 权限码：沿用 M24–M39 口径，只落常量不挂装饰器（Q08 统一处置）
WL_PERM_VIEW = "workload:view"
WL_PERMISSIONS = (WL_PERM_VIEW,)

# ── M41 打印模板配置 + PDF 预览与内容编辑（方案 v2）──────────────────────
# 细节未拍板项登记 OPEN_QUESTIONS Q38–Q40，按下列默认实现，答复后以答复为准。
# 零安装：系统 Chrome headless 出 PDF；预览页临时改字段只作用于本次打印件，不回写业务单据。
PRINT_NODE_RECEIVE = "receive"      # 收货单
PRINT_NODE_PUTAWAY = "putaway"      # 上架作业
PRINT_NODE_PICK = "pick"            # 拣货作业
PRINT_NODE_CHECK = "check"          # 出库复核
PRINT_NODE_SHIP = "ship"            # 发货出库
PRINT_NODE_LABEL = "label"          # 批次标签
PRINT_NODES = (PRINT_NODE_RECEIVE, PRINT_NODE_PUTAWAY, PRINT_NODE_PICK,
               PRINT_NODE_CHECK, PRINT_NODE_SHIP, PRINT_NODE_LABEL)
PRINT_NODE_LABELS = {
    PRINT_NODE_RECEIVE: "收货单", PRINT_NODE_PUTAWAY: "上架单",
    PRINT_NODE_PICK: "拣货单", PRINT_NODE_CHECK: "复核单",
    PRINT_NODE_SHIP: "发货单", PRINT_NODE_LABEL: "批次标签",
}

PRINT_SOURCE_ASN = "asn"
PRINT_SOURCE_DN = "dn"
PRINT_SOURCE_REVIEW = "dn_review"
PRINT_SOURCES = (PRINT_SOURCE_ASN, PRINT_SOURCE_DN, PRINT_SOURCE_REVIEW)
PRINT_SOURCE_LABELS = {
    PRINT_SOURCE_ASN: "收货单 ASN", PRINT_SOURCE_DN: "出库单 DN",
    PRINT_SOURCE_REVIEW: "出库复核单",
}

PRINT_BLOCK_HEADER = "header"
PRINT_BLOCK_TABLE = "table"
PRINT_BLOCK_TOTAL = "total"
PRINT_BLOCK_SIGNATURE = "signature"
PRINT_BLOCK_TEXT = "text"
PRINT_BLOCK_TYPES = (PRINT_BLOCK_HEADER, PRINT_BLOCK_TABLE, PRINT_BLOCK_TOTAL,
                     PRINT_BLOCK_SIGNATURE, PRINT_BLOCK_TEXT)
PRINT_BLOCK_LABELS = {
    PRINT_BLOCK_HEADER: "单据信息", PRINT_BLOCK_TABLE: "明细表",
    PRINT_BLOCK_TOTAL: "合计", PRINT_BLOCK_SIGNATURE: "签核栏",
    PRINT_BLOCK_TEXT: "自由文本",
}

# 字段目录：scope=header/line；editable=预览页可临时微调（不回写业务单据）
PRINT_FIELD_DEFS = {
    "code": {"label": "单号", "scope": "header"},
    "owner_name": {"label": "货主", "scope": "header"},
    "customer_name": {"label": "客户/供应商", "scope": "header"},
    "doc_type": {"label": "单据类型", "scope": "header"},
    "created_at": {"label": "制单时间", "scope": "header"},
    "total_qty": {"label": "总数量", "scope": "header"},
    "note": {"label": "备注", "scope": "header", "editable": True},
    "receiver": {"label": "收货人", "scope": "header", "editable": True},
    "operator": {"label": "操作人", "scope": "header"},
    "line_no": {"label": "行号", "scope": "line"},
    "product_code": {"label": "物料编码", "scope": "line"},
    "product_name": {"label": "物料名称", "scope": "line"},
    "qty": {"label": "数量", "scope": "line", "editable": True},
    "batch_no": {"label": "批号", "scope": "line"},
    "location_code": {"label": "库位", "scope": "line"},
    "line_note": {"label": "行备注", "scope": "line", "editable": True},
}
PRINT_EDITABLE_FIELDS = ("note", "receiver", "qty", "line_note")
PRINT_DEFAULT_TEMPLATE_PREFIX = "DEFAULT-"
PRINT_TEMPLATE_VERSION_DEFAULT = "1.0"
PRINT_TEMPLATE_CODE_MAXLEN = 48
PRINT_PDF_TIMEOUT_SECONDS = 30

# 权限码：只落常量不挂装饰器（Q08 口径延续）
PRINT_PERM_CONFIG = "print:template_config"
PRINT_PERM_VIEW = "print:view"
PRINT_PERM_PDF = "print:pdf"
PRINT_PERMISSIONS = (PRINT_PERM_CONFIG, PRINT_PERM_VIEW, PRINT_PERM_PDF)

# ── M31 司机与车辆主数据（第 1 层基础资料，编号后移、本轮补做）────────────
# 细节未拍板项登记 OPEN_QUESTIONS Q41–Q44，按下列默认实现，答复后以答复为准。
FLEET_STATUS_ACTIVE = "active"
FLEET_STATUS_INACTIVE = "inactive"
FLEET_STATUSES = (FLEET_STATUS_ACTIVE, FLEET_STATUS_INACTIVE)
FLEET_STATUS_LABELS = {FLEET_STATUS_ACTIVE: "启用", FLEET_STATUS_INACTIVE: "停用"}

DRIVER_LICENSE_TYPES = ("A1", "A2", "B1", "B2", "C1", "C2", "D", "E")
DRIVER_LICENSE_TYPE_LABELS = {
    "A1": "大型客车", "A2": "牵引车", "B1": "中型客车", "B2": "大型货车",
    "C1": "小型汽车", "C2": "小型自动挡", "D": "普通三轮摩托车", "E": "普通二轮摩托车",
}
VEHICLE_TYPES = ("厢式货车", "平板货车", "冷藏车", "集装箱车", "高栏车", "其他")

FLEET_EXPIRY_EXPIRED = "expired"
FLEET_EXPIRY_SOON = "soon"
FLEET_EXPIRY_NORMAL = "normal"
FLEET_EXPIRY_NONE = "none"
FLEET_EXPIRY_LEVELS = (FLEET_EXPIRY_EXPIRED, FLEET_EXPIRY_SOON,
                       FLEET_EXPIRY_NORMAL, FLEET_EXPIRY_NONE)
FLEET_EXPIRY_LABELS = {
    FLEET_EXPIRY_EXPIRED: "已过期", FLEET_EXPIRY_SOON: "临近到期",
    FLEET_EXPIRY_NORMAL: "正常", FLEET_EXPIRY_NONE: "未登记日期",
}
FLEET_EXPIRY_SOON_DAYS_DEFAULT = 30
FLEET_EXPIRY_SOON_DAYS_MAX = 365
FLEET_EXPIRY_KINDS = ("driver_license", "vehicle_insurance", "vehicle_transport")
FLEET_EXPIRY_KIND_LABELS = {
    "driver_license": "驾驶证", "vehicle_insurance": "车辆保险",
    "vehicle_transport": "道路运输证",
}
FLEET_CODE_MAXLEN = 32
FLEET_PAGE_SIZE_DEFAULT = 20
FLEET_PAGE_SIZE_MAX = 100

# 权限码：只落常量不挂装饰器（Q08 口径延续）
FLEET_PERM_VIEW = "fleet:view"
FLEET_PERM_CONFIG = "fleet:config"
FLEET_PERMISSIONS = (FLEET_PERM_VIEW, FLEET_PERM_CONFIG)
