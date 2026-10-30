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

### 9. 暂不采纳（避免过度设计）

- Sentry 的 outbox/事件管道、ERP 连接器、哈希链审计日志——规模不适合
- GreaterWMS 的商品属性维度表拆分（品牌/颜色/产地等十余张表）——MiniWMS
  保持 `category` 单字段，主数据够用即可
- ZPL 打印代理、RF 扫码工作流——无硬件场景

## 与现有代码的对照

| 参考实现 | MiniWMS 现状 | 差距与计划 |
|---|---|---|
| GreaterWMS 每域一个 App | 每域一个 routes_*.py | 一致，无需改 |
| Sentry 三类库位 | loc_type 四类 | M4 落实收货→上架流转 |
| Sentry allocated_qty | stock 仅 qty | M4 加列，M5 使用 |
| Sentry 事务行锁 | 无显式事务 | M4 inventory_service 封装 |
| GreaterWMS ASN/DN 命名 | 未建单据表 | M4 asn / M5 dn |
| Aardvarkland 角色 | 无鉴权 | M11 三角色 API Key |
