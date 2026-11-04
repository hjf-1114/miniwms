# MiniWMS — 轻量仓库管理系统（WMS）

一个面向小微仓储场景的多货主 WMS REST API 服务：管理货主、物料主数据、库区库位、收货上架、
拣货发货、移库与盘点；每个货主拥有独立的物料与库存，每个库位的余额始终可由流水推导核对。

## 技术栈

- Python 3.8+（仅标准库）
- Flask（唯一第三方依赖，见 `requirements.txt`）
- SQLite（单文件数据库，零配置）

## 快速开始（本地 demo）

```bash
pip install -r requirements.txt
python main.py            # 监听 0.0.0.0:5100，数据库文件 ./miniwms.db
```

浏览器打开 `http://localhost:5100` 即可使用 Web demo：导航栏可进入
货主/物料/库位/收货/出库/移库/库存等多个管理页，创建、搜索、启停、删除与
收货/上架/拣货/发货/移库/账目核对均直接调用后端 API。
前端为零依赖原生 HTML/JS/CSS（`web_app/` 目录，由 Flask 静态托管）。

想直接体验完整流程，可先灌一套演示数据再启动：

```bash
python tools/seed_demo.py --reset   # 写入 3 货主/8 物料/8 库位/4 客户 + 收货上架→拣货发货全链路
python main.py                      # 浏览器打开 http://localhost:5100 即可操作
```

> 用户与角色管理（M9）已内置：默认管理员账号 **admin / admin123**（首次使用请到「系统管理 → 用户」修改密码）。
> 受保护页面（角色/用户）需先访问 `http://localhost:5100/login.html` 登录，令牌存于浏览器 localStorage。

运行测试：

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

## 功能范围

### 已实现

- **M1 物料主数据**（`routes_product.py`）
  - 物料增删改查：SKU 唯一约束、名称/分类/单位/采购价/销售价/最低库存
  - 列表：关键词模糊搜索（SKU/名称）、分类/状态/货主过滤、分页（默认 20 条，上限 100）
  - 保护规则：库存不为 0 的物料禁止物理删除，只能停用（`status=inactive`）
- **M2 货主主数据**（`routes_owner.py`）
  - 货主（3PL 委托方）增删改查：编码唯一、联系人/电话、启用状态
  - 多货主模型：物料可归属货主（`owner_id`，空=自营），创建/转移需货主存在且启用
  - 保护规则：货主名下还有物料档案时禁止删除
- **M3 库位主数据**（`routes_location.py`）
  - 库位增删改查：编码唯一、库区、类型（收货/存储/拣货/退货）、容量（0=不限）、启用状态
  - 列表：关键词/库区/类型/状态过滤 + 分页，按编码排序
  - 保护规则：已有库存记录的库位禁止删除（`stock` 表 M3 预建，M4 起写入）
- **M4 收货单与上架**（`routes_asn.py` / `inventory_service.py` / `constants.py`）
  - ASN 收货单：单号唯一、货主与物料归属一致性校验（多货主隔离）、行级部分收货
  - 收货流程：收到「收货区」暂存位 → 上架到「存储/拣货」位（一减一增同事务）
  - 单据状态机：open → receiving → putaway → closed（+cancelled），收满上完自动关闭
  - 库存变更统一走 `inventory_service`：BEGIN IMMEDIATE 事务、零行保留、每笔变动落 `stock_movement` 流水
  - `stock` 表含 `allocated_qty`（已分配），为 M5 拣货分配与超卖拦截预留
- **M5 出库单与拣货发货**（`routes_dn.py`）
  - DN 出库单：单号唯一、货主与物料归属一致性校验（多货主隔离）、行级拆分拣货
  - 拣货流程：从存储/拣货位按可用量（在手-已分配）分配，写 `allocated_qty` 与拣货明细
  - 发货流程：所有行拣满后按拣货明细逐库位扣减在手与分配，写发货流水
  - 超卖拦截：可用量不足即拒绝，全程 BEGIN IMMEDIATE 事务锁行防并发双扣
  - 单据状态机：open → picking → shipped（+cancelled），仅未拣货可取消
- **M6 库存账与流水查询**（`routes_stock.py`，只读）
  - 库存余额：按 货主×物料×库位 查询在手/已分配/可用（可用=在手-已分配），
    支持 owner_id/product_id/location_id 过滤、`has_stock=1` 剔除零行、分页
  - 动作流水：全部库存变动流水（收货/上架/拣货/发货），联表带出
    货主编码/物料 SKU/库位编码/单据引用，支持同维度 + `ref_type`/`ref_id` 过滤、分页
  - 余额核对 `/api/stock/reconcile`：逐 货主×物料×库位 校验「余额 = 流水累加」，
    覆盖有余额无流水、有流水无库存行两类异常，返回不一致明细
- **M7 客户主数据与出库关联**（`routes_customer.py` / `routes_dn.py`）
  - 客户主数据：多货主下客户归属货主（客户是货主的客户），编码唯一、联系人/电话/地址、启用状态
  - 出库单强制关联客户：创建必填 `customer_id`，且客户必须与单据货主一致（多货主隔离），
    列表/详情联表返回 `customer_code`/`customer_name`
  - 保护规则：客户被出库单引用时禁止删除
- **M8 基础资料字段增强**（`routes_owner/customer/product/location.py` + `main.py`）
  - 四类主数据补齐电商化字段：货主增 邮箱/地址；客户增 邮箱/等级（普通·银牌·金牌·VIP）/地区；
    物料增 条码/品牌/规格/重量/长/宽/高/保质期（天）；库位增 巷道/列/层/库位条码
  - 校验层接受新字段、创建写入、详情/列表经 `dict(row)` 自动带出；Web 表单与表格同步展示新字段
  - 一键演示数据：`python tools/seed_demo.py`（默认写入 `./miniwms.db`，支持 `--reset` 重置、`--check` 仅查看），
    预置 3 货主 / 8 物料 / 8 库位 / 4 客户并跑通「收货上架 → 拣货发货」全链路，便于本地模拟测试
- **M9 用户与角色管理**（`auth.py` / `routes_user.py`，RBAC 基础）
  - 角色：权限清单（JSON，形如 `资源:动作`，`*`/资源`*` 通配），内置 管理员/操作员/只读 三角色
  - 用户：归属角色、可绑定货主（空=全局）、密码 salt+sha256 哈希存储、启用/停用
  - 登录：`POST /api/auth/login` 签发 HMAC-SHA256 无状态 token（7 天有效）；后续任何接口想做权限控制，
    只需加 `@require_permission("xxx")`（见 `auth.py`），即与用户/角色联动（**用户明确要求后续功能必须联动**）
  - 角色/用户 CRUD（角色/用户列表、创建、编辑、删除）全部要求 `user:manage` 权限
  - 前端：登录页 + 角色/用户管理页（弹窗式 CRUD）+ 侧边栏「系统管理」分组与退出入口
- **M10 物料主数据深化**（`routes_product.py` + `main.py`）：电商/医药级字段（货号/四级分类/产品类型/产地/经营范围/
  注册证号/注册人备案人/生产企业/生产企业许可证/外部编码/产品主分类/温湿度/危险品类型/序列号/保质期控制/备用字段）+
  `product_barcode`（多条码、可设主条码）+ `product_package`（多层级包装、长宽高体积自动计算）子表
- **M11 货主主数据深化**（`routes_owner.py`）：省市区下拉 + 备用地址/授权仓库/经营范围 + `owner_cert` 证照子表
- **M12 客户主数据深化**（`routes_customer.py`）：省市区下拉 + 详细地址 + 经营范围
- **M13 库位主数据深化**（`routes_location.py`）：库位组（手工维护或按编码前缀自动生成）+ 长宽高 + 体积库容（按长×宽×高自动计算）+ 库位组筛选
- **M14 承运商模块**（`routes_carrier.py`，新增子系统）：编码唯一/类型(快递·零担·整车·其他)/外部编码等，供后续发快递/零担/整车
- **M15 主数据体验增强**（2026-11-04）：物料体积手填/自动换算 · 条码与包装可编辑+包装等级(1-6) · 承运商演示数据(顺丰/京东/跨越/货拉拉等) · 货主合作起止时间(date 选择器)
- **M16 供应链与单据主数据增强**（2026-11-04）：供应商主数据(归属货主/多货主隔离) + 多仓库主数据(全系统共享，此前仅支持多货主) + ASN/DN 自动单号(英文开头+日期流水，留空自动生成) + ASN/DN 单据字段增强(单据类型/外部单号/订单来源/来源仓库/供应商快照/承运商/自定义字段/订单总数量·品类数汇总)
- **M17 移库单（Stock Transfer）**（`routes_transfer.py` + `inventory_service.stock_move`）：单据驱动库位间转移——创建移库单（货主×物料×来源位×目标位×数量，含归属/启停/来源≠目标校验），执行时在同一事务内源位扣减、目标位增加，落 move_out/move_in 两条流水且余额始终可由流水核对；仅未执行单可取消
- **M18 盘点单（Stocktake）**（`routes_stocktake.py` + `inventory_service.adjust_stock`）：账实核对——冻结快照账存 → 盲盘录入实盘 → 差异生成 adjustment 调整流水 → 解冻结案，全程余额仍可由流水核对；含未盘行拦截、负数拦截、状态机守卫
- **Web demo**（`web_app/`）
  - 首页导航 + 货主/物料/库位/客户/供应商/承运商/仓库/收货/出库/移库/库存/角色/用户 多个管理页，零依赖原生 JS，直接调用后端 API

### 规划中（见 DEVELOPMENT.md）

- 补货预警（M19）、报表统计（M20）
- **外部对接（M21）**：出入库单据对外开放——X-API-Key 鉴权（密钥绑定货主、多货主隔离）+ 外部写入端点 + 单据状态变更回传（webhook 回调，带签名）
- CSV 导入导出（M21）

## API 概览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/owners` | 创建货主（编码必填唯一） |
| GET | `/api/owners` | 货主列表，支持 `keyword`/`status`/分页 |
| GET/PUT/DELETE | `/api/owners/<id>` | 详情/更新/删除（名下有物料时拒绝） |
| POST | `/api/locations` | 创建库位（编码必填唯一） |
| GET | `/api/locations` | 列表，支持 `keyword`/`zone`/`loc_type`/`status`/分页 |
| GET/PUT/DELETE | `/api/locations/<id>` | 详情/更新/删除（有库存时拒绝） |
| POST | `/api/asns` | 创建收货单（行 = 物料×预期数量，校验货主一致性；单号留空自动生成 `ASN-日期-流水`；支持 单据类型/外部单号/订单来源/来源仓库/供应商/承运商/自定义字段，自动算 总数量·品类数） |
| GET | `/api/asns` | 收货单列表，支持 `keyword`/`status`/分页（返回 来源仓库/供应商/总数量/品类数） |
| POST | `/api/asns/<id>/receive` | 收货到收货区位（部分收货，状态自动推进） |
| POST | `/api/asns/<id>/putaway` | 上架：暂存位 → 存储/拣货位（同事务一减一增） |
| POST | `/api/asns/<id>/cancel` | 取消（仅未收货） |
| POST | `/api/dns` | 创建出库单（行 = 物料×订购数量，校验货主一致性；单号留空自动生成 `DN-日期-流水`；支持 单据类型/外部单号/订单来源/来源仓库/承运商/自定义字段，自动算 总数量·品类数） |
| GET | `/api/dns` | 出库单列表，支持 `keyword`/`status`/分页 |
| POST | `/api/dns/<id>/pick` | 拣货分配（可用量拦截超卖，明细落库） |
| POST | `/api/dns/<id>/ship` | 发货（要求拣满，逐库位扣减库存） |
| POST | `/api/dns/<id>/cancel` | 取消（仅未拣货） |
| POST | `/api/transfers` | 创建移库单（行 = 物料×来源位×目标位×数量，校验货主/库位一致性） |
| GET | `/api/transfers` | 移库单列表，支持 `keyword`/`status`/分页 |
| GET | `/api/transfers/<id>` | 移库单详情（联表带出来源/目标库位编码） |
| POST | `/api/transfers/<id>/move` | 执行移库（同事务一减一增，写 move_out/move_in 流水） |
| POST | `/api/transfers/<id>/cancel` | 取消（仅未执行） |
| POST | `/api/stocktakes` | 创建盘点单（货主×编码唯一，code 必填） |
| GET | `/api/stocktakes` | 盘点单列表，支持 `keyword`/`status`/分页 |
| GET | `/api/stocktakes/<id>` | 盘点单详情（联表带出 物料/库位编码 + 差异 diff=counted-book） |
| POST | `/api/stocktakes/<id>/freeze` | 冻结并快照账存到盘点行（状态→frozen） |
| POST | `/api/stocktakes/<id>/count` | 盲盘录入实盘（逐行 counted_qty，负数 400，状态保持 frozen） |
| POST | `/api/stocktakes/<id>/adjust` | 差异调整（有未盘行 400；对差异行生成 adjustment 流水，状态→adjusted） |
| POST | `/api/stocktakes/<id>/close` | 解冻结案（仅已调整单，状态→closed） |
| POST | `/api/customers` | 创建客户（归属货主，code 必填唯一） |
| GET | `/api/customers` | 客户列表，支持 `keyword`/`status`/`owner_id`/分页 |
| GET/PUT/DELETE | `/api/customers/<id>` | 详情/更新/删除（被出库单引用时拒绝） |
| POST | `/api/suppliers` | 创建供应商（归属货主，code 必填唯一） |
| GET | `/api/suppliers` | 供应商列表，支持 `keyword`/`owner_id`/`status`/分页 |
| GET/PUT/DELETE | `/api/suppliers/<id>` | 详情/更新/删除（被收货单引用时拒绝） |
| POST | `/api/warehouses` | 创建仓库（全系统共享，code 必填唯一） |
| GET | `/api/warehouses` | 仓库列表，支持 `keyword`/`status`/分页 |
| GET/PUT/DELETE | `/api/warehouses/<id>` | 详情/更新/删除（被 ASN/DN 引用时拒绝） |
| GET | `/api/stock` | 库存余额列表，支持 `owner_id`/`product_id`/`location_id`/`has_stock`/分页 |
| GET | `/api/stock/reconcile` | 余额核对（余额 = 流水累加，返回不一致明细） |
| GET | `/api/movements` | 库存动作流水，支持 `owner_id`/`product_id`/`location_id`/`ref_type`/`ref_id`/分页 |
| POST | `/api/products` | 创建物料（SKU 必填唯一，可挂 `owner_id`） |
| GET | `/api/products` | 列表，支持 `keyword`/`category`/`status`/`owner_id`/分页 |
| GET/PUT/DELETE | `/api/products/<id>` | 详情/更新/删除（库存非 0 时拒绝） |
| POST | `/api/auth/login` | 登录（用户名+密码），返回无状态 token |
| GET/POST | `/api/roles` | 角色列表 / 创建（需 `user:manage`） |
| GET/PUT/DELETE | `/api/roles/<id>` | 角色详情/更新/删除（被用户引用时拒绝） |
| GET/POST | `/api/users` | 用户列表 / 创建（需 `user:manage`） |
| GET/PUT/DELETE | `/api/users/<id>` | 用户详情/更新（含改密）/删除 |

## 项目结构

```
main.py               Flask 入口、数据库初始化与连接管理、静态托管 web_app
auth.py               认证与权限基础（密码哈希、token 签发/校验、require_permission 装饰器）
constants.py          状态机与流水类型枚举（显式集中）
inventory_service.py  库存变更服务层（事务/流水/零行保留，路由不直接写库存）
routes_owner.py       货主主数据 Blueprint
routes_product.py     物料主数据 Blueprint
routes_location.py    库位主数据 Blueprint
routes_asn.py         收货单与上架 Blueprint
routes_dn.py          出库单（拣货/发货）Blueprint
routes_stock.py       库存账/流水/余额核对只读 Blueprint
routes_customer.py   客户主数据 Blueprint（出库收货方维度）
routes_user.py        用户与角色（登录/权限）Blueprint
web_app/              Web demo（index/owners/products/locations/customers/asns/dns/stock/roles/users/login + css/js）
tools/seed_demo.py    一键演示数据播种（3 货主/8 物料/8 库位/4 客户 + 收货上架→拣货发货，支持 --reset/--check）
docs/REFERENCES.md    开源 WMS 设计参考笔记（GreaterWMS/Sentry/Aardvarkland）
tests/                单元测试（unittest，共享临时库+每用例清表）
DEVELOPMENT.md        里程碑开发记录
```

## 设计约定

- 所有接口返回 JSON：成功 `{"ok": true, ...}`，失败 `{"ok": false, "errors": [...]}` + 对应 4xx 状态码
- 库存只由单据驱动：收货/上架（M4）→ 拣货/发货（M5）→ 移库/盘点（M10/M11），全部经 inventory_service 落流水
- 时间戳由 SQLite `datetime('now','localtime')` 生成
