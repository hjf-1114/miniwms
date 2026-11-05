# 开源 WMS 设计参考笔记

记录对三个开源 WMS 的调研结论，以及 MiniWMS 在后续里程碑中采纳的设计决策。
只借鉴设计思想，不复制代码；每条注明出处与落点里程碑。

参考项目（调研日期 2026-10-30）：

1. **GreaterWMS** — https://github.com/GreaterWMS/GreaterWMS
   Django + Quasar/Vue，作者出自福特亚太售后物流，2190+ commits，工业界口径
2. **Sentry WMS** — https://github.com/hightower-systems/sentry-wms
   Flask + PostgreSQL + React，同栈（Flask），生产级并发/安全设计，v1.37
3. **Aardvarkland WMS** — https://github.com/MaxmilianBaron/Aardvarkland-WMS
   NestJS + Prisma + React，覆盖波次拣选/打印代理/多角色，UI 六国语言

## 采纳的设计决策

### 1. 单据驱动库存（来源：GreaterWMS 的 ASN/DN，Sentry 的 PO/SO）

库存不允许直接改数字，一切变动由单据产生：到货通知单(ASN) → 收货 → 上架；
出库通知单(DN) → 分配 → 拣货 → 发货。**落点：M4/M5**。MiniWMS 已有此原则
（stock 只由单据驱动），M4 起单据表命名为 `asn` / `dn` 与业界口径一致。

### 2. 库位三属性分类（来源：Sentry 的 Staging / PickableStaging / Pickable）

- `receiving`(收货暂存，不可拣) → `storage`(存储，可上架) → `picking`(拣货位)
- 上架流程：先入收货暂存位，再由系统推荐/人工确认目标存储位
- **落点：M4 上架**。MiniWMS 的 loc_type 与此一一对应，M4 收货默认落 receiving 位，
  putaway 接口校验目标位必须为 storage/picking 类型。

### 3. 库存行三数量字段（来源：Sentry 的 on_hand / ordered / allocated）

`stock` 表除 `qty`(在手) 外增加 `allocated_qty`(已分配)。
超卖拦截 = 发货数量校验 `qty - allocated_qty`，而不是裸 qty。
**落点：M4 建 stock 时引入（M3 预建的 stock 表在 M4 实装时加列），M5 拣货分配使用。**

### 4. 并发安全（来源：Sentry 的行级锁 FOR UPDATE）

所有库存变更（收货、分配、拣货、移库、盘点调整）放在一个显式事务里，
先 `BEGIN IMMEDIATE`（SQLite 版行级锁语义）再读写，防止并发双扣。
**落点：M4 起统一封装在 `inventory_service.py`，路由层不直接写库存 SQL
（Sentry 的服务层分层：API → service → SQL）。**

### 5. 零库存保留行（来源：Sentry v1.35：bin 清空后保留零行）

库位清空后保留 qty=0 的库存行，用于区分「曾经有货、现在为 0」与「从未入过库」。
**落点：M4 收货写入 / M5 拣货清零时不删行，置 0。** 好处：M8 盘点天然覆盖历史库位。

### 6. 状态机显式集中（来源：Sentry 的 constants.py；其 v1.11 删冗余中间态）

单据状态枚举集中定义，状态少而明确：ASN: `open → receiving → putaway → closed`；
DN: `open → allocated → picked → shipped`（+ `cancelled`）。
**落点：M4 建 `constants.py`，M4/M5/M8 单据状态只从枚举取。**

### 7. 盘点三步与差异自动调整（来源：GreaterWMS cyclecount + Sentry bin 级盘点）

冻结范围 → 盲盘录入 → 差异生成 adjustment 流水 → 解冻。
**落点：M8（原规划已一致，补充：差异调整必须走 inventory_service 与普通流水同构）。**

### 8. 角色分级（来源：Aardvarkland 的 worker / manager / admin）

**落点：M11 鉴权**——X-API-Key 绑定角色：worker 只能操作单据动作，
manager 可改主数据，admin 可删数据/导出。

### 9. 批次 / 效期追溯（M19，医疗器械 GSP 口径）

调研笔记此前**没有批次条目**（GreaterWMS 的属性维度表在当年被列为「暂不采纳」），本轮
按用户新增的医疗器械/GSP 需求（UDI、灭菌批号、注册证号、患者、货品属性）独立设计，
**不复制参考项目代码**，只沿用本仓库已确立的三条原则：单据驱动、服务层分层、状态/枚举集中。

- **两层批次模型（本次采纳）**
  - `batch`（批次档案）：按 `货主 + 物料 + 配置为「关键批次信息」的字段值` 做 SHA-256 指纹去重。
    配置的关键字段值一致 → 同一档案，即「关键批次信息是一致的」。
  - `batch_receipt`（验收入库批次流水）：**每次收货一条、`batch_no` 唯一**；由 `receive()`
    在既有 `BEGIN IMMEDIATE` 事务内与库存增加一起写入（延续第 4 条并发安全原则）。
  - 不采纳 GreaterWMS 那种把品牌/颜色/产地拆成十余张维度表的做法：批次信息用
    `info` JSON 快照 + 一张可配置字段表即可，避免轻量项目过度设计。
- **批次字段配置**：`batch_config` 决定哪些字段算关键批次信息、是否启用、默认值；
  空字段在验收时自动套默认值。改配置只影响**之后**的验收，既有档案指纹不重算
  （历史留痕不可被配置变更篡改）。
- **内部批次号规则**：外部数据传入优先 → 销退沿用原批次内部批次号 →
  首次入库 = 本次批次号。与 Sentry 的「历史单据不可变」口径一致：退货不新造业务主键，
  而是回流到出库时的内部批次号。
- **落点：M19**（`batch_fields.py` / `batch_service.py` / `routes_batch.py`）。
- **v2 口径修正（2026-11-04）**：用户明确「销退一般都是外部传入内部批次号的」，故上条
  「销退沿用历史内部批次号」降级为**外部未传时的兜底并需告警**，正常路径以外部传入为准。
  详见第 10 节与 `PUTAWAY_RECOMMENDATION.md` 第 7.5 节。

### 10. 批次级库存 / 库存转移 / 客户专属库存（v2 调研，2026-11-04）

本轮由用户提出「批次是大头，库存与批次息息相关」「修改批次信息叫库存转移、保证留痕」
「入库和批次带指定客户、只能该客户出」「上架规则要有过滤条件」「zone 升级储区主数据」，
重新调研了富勒 / SAP EWM / Dynamics 365 / Odoo / 领星 / 顶妙 / 通天晓 / 账本模式。
完整提案见 `docs/PUTAWAY_RECOMMENDATION.md` v2，本节只记借鉴结论。

- **批次不是字段，是库存维度（最重要的结论）**：领星 WMS 的批次库存颗粒度 =
  「入库单号 + SKU + 客户 + 上架时间 + 上架批次号 + 生产日期 + 失效日期 + 库位 + 库存属性」。
  本项目 M19 的 `batch` / `batch_receipt` 目前是「收货时写入、之后再无人读取」的档案孤岛，
  必须补 `stock_batch` 明细层，让上架、移库、盘点、发货都继承批次。
- **两层模型，不改主账主键**：`stock`（汇总层，货主×物料×库位）保持不变，
  新增 `stock_batch`（明细层）。业界标准做法是「先明细后汇总 + 定期校验」；
  直接给 `stock` 加批次列会破坏 M6 reconcile 不变式与 M5 超卖拦截。
- **不要建第二套批次流水**：账本模式与 SAP 物料凭证都强调「余额与流水必须能互相核对」。
  v1 曾提议独立批次流水表，v2 改为**给现有 `stock_movement` 加维度列**（老流水填默认值、
  主账 SUM 不变；一次动作涉及 N 个批次写 N 条流水，Σ 等于主账变动量）。
- **库存转移 = 记事件，不是改字段**：顶妙 WMS 在「批次库存」页直接提供「调整库存 / 修改仓位」
  并全量生成库存变动明细；SAP 用 309 物料互转、D365 用库存转移日记账，
  共同点是**原批次记录保留、生成正负一对流水、过账后不可修改只能冲销**。
  故 `batch.info` / `batch_receipt.info` 定为只读历史快照，改关键字段 =
  指纹变化 = 新建/复用 `batch` 档案 + 数量从旧明细转到新明细。
- **三类变更分三套机制**：批次属性修正（错录补正）/ 质量状态变更（本身即一次过账，
  不合格单向不可回合格）/ 库位·数量·客户归属变更。另需区分 `stock_status`（非限制/质检/冻结）
  与 `goods_property`（合格/不合格）——SAP 明确警告两者不能合成一个字段。
- **变更留痕两套账**：库存事务管「量」（append-only 流水 + 成对冲销），
  变更文档管「属性」（字段级 old→new、操作人/审批人、业务时间与系统时间双存、原因码）。
  溯源页要能「查看关联单据」并「发起业务动作」（冻结/复核/盘点/异常工单），只查不能办会漏冻结。
- **客户专属库存 = 所有权 ≠ 存放位置**：SAP 销售订单库存 E / 项目库存 Q 是「从通用库存
  切出虚拟专属子库存」，Odoo 用 owned stock 专属库位 + ownership 校验，
  D365 用 `Prevent multiple owners` 把 owner 加入上架分组维度。
  本项目不引 SAP 全套特殊库存（K/W/E/Q/O/V），只做 `designated_customer_id` + 出库校验的最小实现；
  绑定/解绑必须走库存转移单留痕。
- **上架规则是条件分流 + 多级 fallback，不是纯加权打分**：富勒的「策略包头 + 明细行 +
  筛选条件 + 限制条件」、每维单选但维度间 AND、优先级 10/20/30 命中即止、兜底行、
  限制条件按「库位状态→空间→混放→复杂业务」短路。用户描述的「默认货品储区 →
  同产品位置 → 同产品分类」是**递降 fallback 链**，score 只用于同储区内候选排序；
  纯加权会让不合格品因同品集中得分高而混进正常库位。
- **不合格 / 虚拟隔离**：大数跨境 WMS 全景按批次记录质检结果，不合格与特采品进入虚拟库存区
  做逻辑隔离；本项目对应 `loc_type` 新增 `defective / quarantine / virtual` 与储区白名单。
- **出库分配留好地基但本版不做**：库管易的 FEFO 排序兜底（失效日期 → 生产日期/入库时间
  → 批次号）、「先锁定再拣货」、跳批审批留痕，是阶段 G 的参考；本项目现有
  `allocated_qty` + `qty_delta=0` 的 `dn_pick` 流水已是锁定雏形。
- **对照后发现的现有代码隐患**（沟通素材，详见提案第 3.4 节）：
  ① `batch_receipt.batch_no`（系统 BAT 流水号）与 `asn_line.extra.batch_no`（供应商批号）同名不同义；
  ② `batch_receipt.location_id` 是收货位快照，上架后失真；
  ③ `stock_movement` 无批次维度；④ `product.stock_qty` 与 `stock` 主账无联动；
  ⑤ `GOODS_PROPERTIES` 仅两值；  ⑥ `dn_pick` 无 UNIQUE 且流水不记 `allocated_delta`。

**v3 口径确认（2026-11-05，用户逐条答复 4 个待确认项）**

- 「虚拟」不是单据种类而是**单据标识位**：`asn` / `dn` 各加 `is_virtual` / `is_direct` /
  `is_cold_chain` / `is_hazard` 四个布尔标识 + `external_doc_type` 外部单据类别，
  全部纳入上架条件组维度。对应天工 WMS「按订单类型 / 产品属性（正常品·残损品·重量体积）
  限定生效库区」与 SAP EWM 库存类型分区；冷链/危化按 SAP 的 `Restricted-use / Quality
  inspection stock 自动导向独立 storage type` 处理为**硬约束**，虚拟/直调/外部类别为**条件分流**。
  原 `doc_kind` 枚举设计作废。
- 库存转移单改 **5 个动作位**：制单 → 分配库存 → 审核 → 关闭单据 → 删除单据，
  `draft→allocated→approved→closed`。「分配库存」复用 M5 `allocated_qty` 的「先锁定再过账」
  （库管易 / SAP EWM 同款），锁的是明细层不动主账；「关闭单据」才产生库存事实。
  权限只留 `@require_permission` 钩子，角色矩阵按用户要求后置。
- 指定客户**只在明细行、默认空、验收时人工填写**，属于 `stock_batch` 库存明细维度，
  不加入 M19 `batch_key` 关键字段指纹（避免同批货分给两客户被拆成两个档案）。
- 老库回填：用户「目前还在编码阶段」→ 不做兼容灰度，I2/I3 从第一天硬校验，演示库 `--reset` 重建。
  少了「豁免→收紧」两套逻辑，测试可直接断言三个不变式。

### 11. 暂不采纳（避免过度设计）

- Sentry 的 outbox/事件管道、ERP 连接器、哈希链审计日志——规模不适合
- GreaterWMS 的商品属性维度表拆分（品牌/颜色/产地等十余张表）——MiniWMS
  保持 `category` 单字段，主数据够用即可
- ZPL 打印代理、RF 扫码工作流——无硬件场景
- v2 新增不采纳：SAP 全套特殊库存（K/W/E/Q/O/V）、完整双时态查询、3PL 计费引擎、
  LPN/容器/序列号/波次、通用规则引擎（Drools/Grule）、WMS/ERP 双向库存同步——
  本项目无 ERP、无成本核算、无硬件，且上架是「一次求值选最优库位」的无状态决策，
  表驱动 + 显式优先级更可审计，也守住「仅 Flask、零依赖」硬约束

## 与现有代码的对照

| 参考实现 | MiniWMS 现状 | 差距与计划 |
|---|---|---|
| GreaterWMS 每域一个 App | 每域一个 routes_*.py | 一致，无需改 |
| Sentry 三类库位 | loc_type 四类 | M4 落实收货→上架流转 |
| Sentry allocated_qty | stock 仅 qty | M4 加列，M5 使用 |
| Sentry 事务行锁 | 无显式事务 | M4 inventory_service 封装 |
| GreaterWMS ASN/DN 命名 | 未建单据表 | M4 asn / M5 dn |
| Aardvarkland 角色 | 无鉴权 | M11 三角色 API Key |
| GreaterWMS 批次 / 属性维度表 | 收货明细只有商品档案快照 | M19 独立批次子系统（配置 + 档案 + 每次入库唯一批次流水），不拆维度表 |
| 领星 / 顶妙批次库存明细层 | `stock` 只有货主×物料×库位聚合账 | v2 新增 `stock_batch` + `stock_movement` 维度列，主账粒度不动 |
| SAP 物料凭证 / 309 互转 | 无批次信息修改功能 | v2 库存转移单（成对流水 + 冲销 + 字段级变更文档） |
| SAP EWM 客户库存 / D365 多货主约束 | 无客户专属库存 | v2 `designated_customer_id` 最小实现 + 出库校验 |
| 富勒上架策略条件组 | 上架库位全由人传 | v2 条件组分流 + fallback 链 + 储区主数据表 |
