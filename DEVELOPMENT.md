# MiniWMS 开发记录

每个里程碑记录：目标、改动清单、可验证结果。里程碑保持单目标内聚，按开发顺序演进。

## M1 物料主数据（2026-10-30）

**目标**：建立项目骨架与物料（商品）主数据完整 CRUD，作为后续收货/上架/拣货/盘点的参照数据。

**改动清单**：
- `main.py`：Flask 入口、`get_db()` 请求级连接、`init_db()` 建 `product` 表（SKU 唯一索引、分类/状态索引）、Blueprint 聚合
- `routes_product.py`：`/api/products` 增删改查 + 分页 + 关键词/分类/状态过滤 + SKU 去重 + 价格非负校验 + 库存非 0 禁删保护
- `tests/test_products.py`：12 个用例（创建校验/重复 SKU/分页搜索过滤/部分更新/删除保护）
- `README.md` / `requirements.txt`（仅 Flask）/ `.gitignore`

**可验证结果**：
- `python -m unittest discover -s tests` 全部通过
- SKU 重复创建返回 409；负价格返回 400；库存非 0 删除返回 409
- 库存字段建档默认 0，M1 不提供直接改库存入口（保证库存只能由单据驱动）

## 七天交付规划（2026-10-30 制定）

节奏：每天 1-2 个真实提交（后端+测试一个提交、页面一个提交），目标 7 天内同时满足
平台门槛（文件≥100 / commit≥30 / milestone≥6）与「功能能当真产品用」。前后端同步推进，
每个后端里程碑交付后紧跟对应 Web demo 页面，本地 `python main.py` 即可演示。

| 天 | 里程碑 | 内容 |
|---|---|---|
| D1 (10/30) | M3 | 库位主数据(后端+10测) + Web demo 骨架(首页/货主/物料/库位 4 页) |
| D2 (10/31) | M4 | ~~收货单+上架~~ 实际 11/03 完成（自动化未触发，手动补进度） |
| D3 (11/1) | M5 | ~~出库单+拣货+发货(超卖拦截) + 出库 demo 页~~ 实际 11/03 完成（M4 顺延后按序推进） |
| D4 (11/2) | M6 | ~~库存账查询+全部动作流水(余额=流水累加可核对) + 库存查询页~~ 实际 11/03 完成（顺延） |
| D5 (11/3) | M7 | 客户主数据 + 出库单关联客户（补「出库需要客户资料」遗漏维度） |
| D6 (11/4) | M8/M9 | 移库(原子一减一增) + 盘点(冻结/盲盘/差异调整) + 对应页面 |
| D7 (11/5) | M10/M11 | 补货预警 + 按货主报表统计 + 首页仪表盘 |
| D8 (11/6) | M12 | CSV 导入导出 + 简易鉴权(X-API-Key) + API 文档 + 全量回归收尾 |

> 注：D2/D3/D4 的 M4/M5/M6 实际在 11/03 完成（自动化未触发顺延）；D5 用户指出「出库需要客户资料」此前缺失，故插入 M7 客户主数据，原 M7 移库/M8 盘点顺延为 M8/M9。

## 规划（Roadmap）

- M4 收货单（ASN 收货 → 上架 putaway：写 stock(货主×物料×库位) + 入库流水，库存增加）
- M5 出库单（拣货 pick → 发货：按库位扣减库存，超卖拦截）
- M6 库存账（按货主+库位的库存余额 + 全部动作流水，余额 = 流水累加可核对）
- M7 客户主数据（客户归属货主，出库单关联客户，多货主隔离）
- M8 移库（库位间转移：同数量一减一增两条流水，原子事务）
- M9 盘点（冻结 → 盲盘 → 差异生成调整流水 → 解冻）
- M10 补货预警（min_stock 对比 + 拣货位缺料提示）
- M11 报表统计（按货主的收发汇总、周转率、库位利用率）
- M12 CSV 导入导出 + 简易鉴权 + API 文档收尾

## M7 客户主数据与出库关联（2026-11-03）

**目标**：补上此前遗漏的关键维度——**出库是发给客户的**，原 DN 仅挂货主（委托方），
缺少客户（收货方）主数据。落地客户主数据并让出库单强制关联客户，多货主下客户归属货主、
彼此隔离。

**改动清单**：
- `main.py`：`customer` 表（code 唯一/name/owner_id 归属货主/contact/phone/address/status/启用）
  + `dn.customer_id` 列（新建库 DDL 自带、旧库 ALTER 迁移）
- `routes_customer.py`：客户 CRUD + 编码去重 409 + 货主归属校验 + 列表 keyword/status/owner_id
  过滤 + 分页 + 被出库单引用时禁删 409
- `routes_dn.py`：创建必填 customer_id（存在且 owner 一致才接受）、列表/详情联表返回
  customer_code/customer_name
- `tests/test_customers.py`：10 例；`tests/test_dn.py` 补 3 例（必填/归属隔离/详情返回）、
  夹具同步自动建客户，存量用例按新校验顺序修正

**可验证结果**：
- `python -m unittest discover -s tests` 87 例全绿
- 客户编码重复 409；归属货主不存在 400；被 DN 引用禁删 409
- 出库必填客户：缺 customer_id 400；选了别家货主的客户 400（多货主隔离）
- 列表/详情正确返回 customer_code/customer_name



**目标**：交付只读「库存账房」——余额查询、全部动作流水查询、余额与流水累加的
独立核对接口，让 M4/M5 以来的「每笔变动必落流水」承诺可被事后验证。

**改动清单**：
- `routes_stock.py`：`/api/stock`（余额列表，含在手/已分配/可用三数量，
  owner_id/product_id/location_id/has_stock 过滤 + 分页）、
  `/api/movements`（流水列表，联表带货主编码/SKU/库位编码/单据引用，
  同维度 + ref_type/ref_id 过滤 + 分页）、
  `/api/stock/reconcile`（按 货主×物料×库位 核对余额 = 流水累加，
  覆盖有行无流水/无行有流水两类异常，浮点容差 1e-9）
- `main.py`：注册 stock 蓝图
- `tests/test_stock.py`：14 例

**可验证结果**：
- `python -m unittest discover -s tests` 77 例全绿
- 余额口径正确：可用 = 在手 - 已分配（拣 4 后 10/4/6）；暂存位零行保留可见，
  `has_stock=1` 可只看有货行
- 入库链路流水可追溯：receive(+10) / putaway_out(-10) / putaway_in(+10)
  联表带出 owner_code/sku/location_code/ref_id
- 收货→上架→拣货→发货全程后 reconcile 一致（checked=2，净流水 = 余额）；
  篡改 stock.qty=99 或删除库存行后 reconcile 精确报出不一致与两侧数值

## M5 出库单与拣货发货（2026-11-03）

**目标**：落地第一个出库单据——出库单（DN）拣货分配、发货扣减与超卖拦截，
库存出库从此只能经「拣货→发货」单据链路完成。

**改动清单**：
- `constants.py`：DN 状态机（open→picking→shipped/cancelled）
- `inventory_service.py`：`adjust_stock` 新增分支——无库存行时禁止扣减/纯分配
  （防负库存行/超配行凭空产生）
- `main.py`：`dn`/`dn_line`/`dn_pick` 表（拣货明细记录每行从哪些库位各拣多少）
- `routes_dn.py`：创建（货主×物料归属一致性校验）、拣货（可用量=在手-已分配，
  BEGIN IMMEDIATE 锁行，分配落 `allocated_qty` + `dn_pick` 明细 + qty_delta=0 审计流水）、
  发货（要求所有行拣满，按明细逐库位同步扣减 qty 与 allocated_qty，写 dn_ship 流水）、
  取消（仅未拣货）；状态自动推进
- `tests/test_dn.py`：15 例

**可验证结果**：
- `python -m unittest discover -s tests` 63 例全绿
- 拣货只分配不动在手：拣 4 后库位 qty=10 不变、allocated_qty=4，流水 dn_pick qty_delta=0
- 超卖拦截：拣超可用量 400；两张单抢同一份库存，第二张可用归零被拒
- 发货按明细逐库位扣减：qty 与 allocated_qty 同步清减，一行拆多库位拣货各自正确扣减
- 状态保护：未拣满发货 400、已发货重复发货 409、已拣货取消 409

## M4 收货单与上架（2026-11-03）

**目标**：落地第一个库存单据——ASN 收货单与上架，库存从此只由单据驱动并可由流水核对。
设计依据 `docs/REFERENCES.md`（GreaterWMS 的 ASN 口径 + Sentry 的服务层分层/暂存位流转/零行保留）。

**改动清单**：
- `constants.py`：ASN 状态机（open→receiving→putaway→closed/cancelled）与流水类型枚举显式集中
- `inventory_service.py`：库存变更服务层——`BEGIN IMMEDIATE` 事务、stock upsert（零行保留）、
  `allocated_qty` 字段、每笔变动写 `stock_movement` 流水
- `main.py`：`asn`/`asn_line`/`stock_movement` 表 + 旧库 `stock.allocated_qty` 列迁移
- `routes_asn.py`：创建（货主×物料归属一致性校验）、收货（限 receiving 位、部分收货）、
  上架（暂存位→storage/picking，一减一增同事务）、取消（仅未收货）；状态自动推进
- `web_app/asns.html`：收货管理页（建单/明细/收货/上架/取消）+ 首页导航
- `tests/test_asn.py`：11 例

**可验证结果**：
- `python -m unittest discover -s tests` 48 例全绿
- 端到端冒烟：建货主→物料→收货位/存储位→ASN(100)→收货 100→上架 100→
  暂存位 qty=0（零行保留）、存储位 qty=100、流水 3 条（receive +100 / putaway_out -100 / putaway_in +100）
- 越界保护：收超剩余 400、错库位类型 400、货主不一致 400、已收货取消 409

## M3 库位主数据 + Web demo 骨架（2026-10-30）

**目标**：建立库位主数据（收发货坐标）与前后端一体 demo 骨架，本地一键启动可演示。

**改动清单**：
- `main.py`：`location` 表（编码唯一/库区/类型/容量/状态）+ `stock` 表预建
  （货主×物料×库位 唯一，M3 供库位删除保护，M4 起写入）+ Flask 静态托管 `web_app/` +
  **修复 `python main.py` 直跑时的 `__main__`/`main` 双实例循环导入**（入口模式改为以 main
  模块身份重导入自身，测试路径不受影响）
- `routes_location.py`：`/api/locations` 增删改查 + 分页 + 关键词/库区/类型/状态过滤 +
  编码去重 409 + 有库存禁删 409 + 类型中文标签
- `web_app/`：index（导航+里程碑进度）/ owners / products / locations 四页 +
  共享 css/style.css + js/api.js（原生 fetch 封装、HTML 转义防注入）
- `tests/test_locations.py`：10 例
- README 补 demo 启动方式

**可验证结果**：
- `python -m unittest discover -s tests` 37 例全绿
- 本地冒烟：`python main.py` 后首页/4 静态页 200；建货主→建库位→建物料挂货主链路 API 全通
  （物料响应带 owner_code/owner_name）

## M2 货主主数据与物料归属（2026-10-30）

**目标**：落地多货主（3PL）核心模型——货主主数据 CRUD + 物料挂接货主，后续所有库存单据将带 owner 维度。

**改动清单**：
- `main.py`：`owner` 表（编码唯一）+ `product.owner_id` 列迁移（PRAGMA 检查 + ALTER，NULL=自营）
- `routes_owner.py`：`/api/owners` 增删改查 + 分页 + 关键词/状态过滤 + 编码去重 + 名下有物料禁删
- `routes_product.py`：物料支持 `owner_id`（创建/更新时校验货主存在且启用；列表支持 `owner_id` 过滤，`none`=自营；响应附带 `owner_code`/`owner_name`）
- `tests/test_owners.py`：10 例；`tests/test_products.py` 补 5 例（挂接/不存在/停用/过滤/转移与清空）
- README 同步多货主口径

**可验证结果**：
- `python -m unittest discover -s tests` 27 例全绿
- 货主编码重复 409；名下有物料删除 409；物料挂接不存在的货主 400；挂接停用货主 400
- `owner_id=none` 过滤出自营物料；物料可在货主间转移或显式清空回自营

**测试隔离教训（与 BabyTalk M44 同源）**：`main.DB_PATH` 首次 import 冻结——多测试模块必须用
`os.environ.setdefault("MINIWMS_DB", ...)` 共享同一临时库路径，且每个 setUp 需清空所有关联表（先子后主）。

## M1 物料主数据（2026-10-30）
