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
# 可选：MINIWMS_PORT=5101 / MINIWMS_DB=/path/to/db 覆盖端口与库路径（便于多实例/测试）
# 可选：MINIWMS_ALLOW_ANONYMOUS=0 关闭匿名演示模式，强制登录后才能调用 API
```

浏览器打开 `http://localhost:5100` 即可使用 Web demo：导航栏可进入
货主/物料/库位/收货/出库/移库/库存等多个管理页，创建、搜索、启停、删除与
收货/上架/拣货/发货/移库/账目核对均直接调用后端 API。
也可打开 `http://localhost:5100/app.html` 进入**工作台**：左侧为**多层内嵌导航**（3 个一级域 / 7 个二级分类 / 24 个页面；默认只展开当前页所在分支，其余折叠，手动展开状态记忆在 localStorage），菜单区可独立上下滚动，顶部「收起导航 / 展开导航」按钮可伸缩侧栏（状态同样记忆）+
多标签页，多个页面在同一窗口并存（工作台自身不会被嵌套进标签）。验收、上架各有独立作业页，可同时打开多张收货单的标签。
前端为零依赖原生 HTML/JS/CSS（`web_app/` 目录，由 Flask 静态托管）。

想直接体验完整流程，可先灌一套演示数据再启动：

```bash
python tools/seed_demo.py --reset   # 写入 3 货主/8 物料/8 库位/4 客户 + 收货上架→拣货发货全链路
python tools/seed_module_demo.py    # 再补 M17–M26 各模块可查数据（幂等、走真实 API，不覆盖现有数据）
python main.py                      # 浏览器打开 http://localhost:5100 即可操作
```

排查演示数据时用只读盘点，不会写库：

```bash
python tools/seed_module_demo.py --check   # 各模块表行数 + /api/stock/reconcile 与 batch-reconcile 一致性体检
cp miniwms.db miniwms_backup_$(date +%Y%m%d).db   # 改动前先备份
```

> `miniwms.db` 及备份文件被 `.gitignore`（`*.db`）忽略，**不入库**；换机器或重建库后按上面两条命令即可复现同一套演示数据。
> 2026-11-13 播种前的备份为 `miniwms_backup_20261113.db`（仅本地）。

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
- **M18 盘点单（Stocktake）**（`routes_stocktake.py` + `inventory_service.adjust_stock`）：账实核对——冻结快照账存 → 盲盘录入实盘 → 差异处理 → 解冻结案，全程余额仍可由流水核对；含未盘行拦截、负数拦截、状态机守卫。**M22 起差异不再直接写 adjustment 流水**，改为人工生成库存调整单、审核后过账（见下文 M22 章）
- **M19 批次与验收入库追溯**（`batch_fields.py` / `batch_service.py` / `routes_batch.py` / `web_app/batches.html`）：
  - **批次字段配置**：20 个批次字段可配置「是否关键 / 启用 / 默认值 / 标签 / 备注」；默认关键字段＝批号、生产日期、失效日期、灭菌批号、UDI、产地、生产企业
  - **批次档案**：按 `货主 + 物料 + 关键字段值` 指纹去重，配置的关键批次信息一致即同一档案；支持手动「预配置批次」
  - **每次入库唯一批次号**：验收入库 `receive()` 在原有事务内写入完整批次信息并生成 `BAT-日期-流水`；内部批次号＝外部数据传入优先 → 退货沿用出库内部批次号 → 首次入库等于批次号
  - 收货明细新增灭菌批号 / 产地 / 患者名称 / 内部批次号 / 货品属性(合格·不合格) / UDI / 生产企业，且**所有自动带出的档案字段均可人工编辑**（自动带出只是默认值，单据留存最终填写值）
- **收货单体验增强**（2026-11-04 用户反馈，`web_app/asns.html` + `routes_asn.py`）：单据类型默认「采购」、来源仓库表单必填且默认选中（无仓库时自动建默认仓库）；货主默认不填，选定后客户/供应商/商品**全量可选**并按归属排前打 ✓，**选错即时红字报错**（不再预先过滤）；供应商/客户联系方式可在下单时覆盖；收货明细选商品自动带出**货品名称/规格/货号/单位/品牌/厂家/生产企业/产地/注册证号/注册人备案人/生产许可证/效期/UDI**，并支持批号、灭菌批号、生产日期、失效日期、内部批次号、货品属性、患者名称补录，全部字段可人工改写，随单据留档并在验收入库时写入批次信息
- **M20 储区与上架作业基础（阶段 A，2026-11-05）**（`routes_zone.py` / `routes_product_location.py` / `putaway_service.py` / `routes_putaway.py` + `web_app/zones.html`、`asn_receive.html`、`asn_putaway.html`）：
  - **储区主数据**：编码/名称/温区分类（常温·阴凉·冷藏·冷冻）/所属仓库（留空＝全仓共享）/启用/备注 + 产品·批号·货主三个混放开关；改仓库时若已有其它仓库库位则拒绝，删储区时若已挂库位或已被货品设为推荐储区则拒绝
  - **仓库维度打通**：`location` 增加 `warehouse_id` / `zone_id` 与三个可空混放开关（留空＝继承储区，实值＝覆盖）；库位与储区仓库必须一致或储区为全仓共享；旧 `zone` 文本列保留兼容、不再参与业务判断；列表按仓库/储区筛选并返回 `effective_mix`
  - **货品上架偏好**：`product.default_zone_id`「推荐储区」下拉 + `product_location` 绑定表（一个 SKU 可绑多个固定货位，仅允许启用中的存储位/拣货位）
  - **简版上架推荐**：`GET /api/putaway/recommend` 只读计算，硬过滤（启用/目标位类型/温区/剩余容量/三类混放）后按「绑定货位 > 推荐储区 > 同产品聚拢 > 同批号聚拢 > 空库位 > 容量贴合」打分，返回 Top-N 货位、理由与被排除原因；**不写库存**，一键采纳只是把 `location_id` 填进原有 `/api/asns/<id>/putaway`
  - **验收 / 上架独立作业页**：`asn_receive.html` / `asn_putaway.html` 均带 `?asn_id=`（可带 `&line_id=`），在工作台里各开一个标签页、多张单据可并行；验收页录数量/收货位/内部批次号并展示已生成批次流水，上架页展示推荐与排除清单并支持一键采纳；`asns.html` 明细行的验收/上架按钮改为打开对应标签页，独立打开页面时回退为新窗口
  - 工作台侧栏恢复为**默认展开 + 显式收起/展开按钮 + 记住状态**；收起状态下 hover 不再意外弹开
- **M20 剩余 · 单据标识位（阶段 B，2026-11-05）**（`constants.py` / `main.py` / `routes_asn.py` / `routes_dn.py` + `web_app/asns.html`、`dns.html`）：
  - ASN / DN 各增加 4 个 0/1 标识位 `is_virtual`（虚拟）/ `is_direct`（直调）/ `is_cold_chain`（冷链）/ `is_hazard`（危险）与自由文本 `external_doc_type`（外部单据类别，≤32 字符）；新建表单可填、列表与明细可查、明细页以中文标签展示；老库自动补列，旧单据默认 0 / 空串
  - `constants.parse_doc_flags()` 集中解析，兼容整数 / 布尔 / 字符串 `"0"`/`"1"`；非法值或超长返回 400
  - **仅记录、不校验（蓝图 v3 口径，M20→M25 过渡期）**：`receive()` / `putaway()` 完全不因这些标识拦截，冷链 + 危险单据仍可上架到常温货位；混放 / 温区 / 危化拦截将在 M25 由**可配置上架规则**决定、M26 提供条件组编辑器，代码中无 hardcoded 红线。**（M25 起过渡期已结束：冷链 / 危险 / 不合格按命中规则行 `limits` 拦截，见下方 M25 章；本行保留 M20 时点原文用于追溯）**
- **Web demo**（`web_app/`）
  - 首页导航 + 货主/物料/库位/储区/客户/供应商/承运商/仓库/收货/验收作业/上架作业/出库/移库/盘点/盘点方案/库存调整/批次/批次库存/库存/角色/用户/货主仓库关系/上架规则配置 多个管理页，零依赖原生 JS，直接调用后端 API
  - **工作台（`/app.html`）**：默认展开的左侧垂直一级菜单 + 内嵌可折叠子菜单 + 多标签页，顶部按钮收起/展开导航并记忆状态；单个页面以 iframe 标签并存；单据「明细」按钮与验收/上架作业在工作台内以新标签打开，独立打开页面时回退为新窗口；含防嵌套守卫，工作台不会被套进自己的标签；**顶栏账号区**显示当前登录账号与角色并提供「退出登录」（清除 token 与用户摘要后由顶层窗口跳登录页，避免登录页被嵌进 iframe），未登录时显示「登录」入口（详见 `app.html`）

### 架构增强：仓库 / 货主双维数据隔离（2026-11-05，非里程碑，不占用 M 编号）

> 用户确认口径：「直接做完整版」；「看所有仓库」采用 **A 作业视角**（账号可切换到任意有权限的仓，
> 但一次只查看 / 操作一个仓，不做一屏跨仓汇总）；基础资料需预留货主维度便于后续权限管控。

- **连接层（仓库容器隔离）**：`main.py` 拆出中央库（控制面）与业务库（数据面）。`warehouse.db_path`
  留空＝全部仓共用主库（默认，**clone 下载即用**）；填写项目目录内以 `.db` 结尾的相对路径后，
  该仓业务数据写入独立 SQLite 文件，保存时自动建库并把「货主 / 仓库」跨仓主数据同步进去。
  切仓由前端统一附加 `X-WMS-Warehouse` header，服务端无会话状态；**17 个 `routes_*` 一行未改**
  （全部只依赖 `get_db()`），按仓分库只是换掉这个连接。
- **SQL 层（货主 MANDT 式隔离）**：`scope.py` 集中生成 `owner_id IN (...)` 条件，
  13 个业务列表接口（货主 / 物料 / 客户 / 供应商 / 承运商 / ASN / DN / 库存 / 流水 / 移库 /
  盘点 / 批次档案 / 批次流水）统一调用 `S.scoped_where()`；公共表走白名单不注入。
  `before_request` 再做两道集中守门：请求体里的 `owner_id` 写入校验、按 ID 访问的资源归属校验
  （含 `/api/batch/profiles|receipts/<id>` 二级路径），覆盖详情 / 更新 / 删除 / 单据动作全部端点。
- **权限模型（全部 / 部分可配）**：`user_warehouse_grant` + `user_owner_grant` 两张授权表，
  配合 `user.warehouse_scope` / `user.owner_scope` 的 `all|grant` 显式标志；
  在「系统管理 → 用户 → 数据权限」弹窗里可勾选部分仓 / 部分货主，或一键切「全部」。
  默认 `admin` 显式 `all/all`，新建账号默认 `grant/grant`（未配置授权行即 403 拒绝）。
- **铁律**：`ALL` 必须是授权解析后**显式**给出的标志；上下文缺失一律拒绝，绝不用
  「没解析到就不加条件」放行。`auth.py::has_permission()` 也去掉了「角色名叫管理员」的隐式提权，
  改由角色 `permissions` 里显式的 `*` 决定。
- **匿名演示模式**：`MINIWMS_ALLOW_ANONYMOUS` 默认 `1`，未登录时显式返回
  `mode='anonymous-demo'` 的 ALL 上下文（不影响 clone 即用）；设为 `0` 即强制登录。
  注意这不是「上下文缺失放行」，而是配置明确给出的演示开关。
- **基础资料预留**：`carrier.owner_id` 新增（`NULL`＝全系统共享物流资源，非空＝归属货主、
  仅该货主可见），与已有的 `customer.owner_id` / `supplier.owner_id` 一起，
  为后续按货主做权限管控留好挂点；列表支持 `owner_id=<id>` 与 `owner_id=none` 过滤。
- **前端**：独立页侧栏与工作台顶栏各有「当前仓库」选择器（共用 `api.js::mountWarehousePicker()`），
  切换后工作台会重载全部已打开标签，避免旧仓数据残留；首次确定当前仓会自动重载一次页面数据。
- **业务关系格子（2026-11-05 二次优化）**：新增 `owner_warehouse(owner_id, warehouse_id, status)`
  多对多关系表与「系统管理 → 权限 → 货主仓库关系」矩阵页，登记「哪个货主在哪个仓有业务」。
  `scope.apply_owner_warehouse_grid()` 在 `before_request` 里把账号授权与当前仓登记取**交集**，
  消掉「可见仓 × 可见货主」笛卡尔积里业务上并不存在的组合；13 个列表接口、写路径守门、
  按 ID 守门自动跟着收紧（逐个路由一行未改）。某仓**无任何 active 登记行**＝该仓未启用格子过滤，
  行为与优化前完全一致（clone 即用、老库零迁移）；清空该仓勾选即关闭。
  未指定仓库（不带 `X-WMS-Warehouse`）的请求不启用格子，这是明确的兼容边界。
- **数据库选型（用户 2026-11-05 确认）**：现阶段**继续 SQLite + 应用层隔离**；
  「多 Tenant 3PL SaaS + PostgreSQL + RLS」写作**未来演进路线、非当前实现**，
  详见 `docs/DOMAIN_BLUEPRINT.md` §9。触发条件是「第二家物流公司入驻」或「上真实生产」。

### M21 批次级库存明细层（2026-11-06 里程碑 ✅）

- **两层模型**：`stock` 汇总层的列与唯一键**一个不动**，新增 `stock_batch` 明细层
  （批号 / 批次指纹 / 内部批次号 / 指定客户 / 货品属性 / 质量状态 / 效期 / 生产日期 +
  `qty` / `allocated_qty`），表达式唯一索引用 `IFNULL(designated_customer_id,0)` 处理通用库存；
  明细层同样**零行保留**。
- **统一流水**：`stock_movement` 扩 9 列（全部带默认值，老流水不参与批次分组但仍参与主账 SUM）。
  一次动作涉及 N 个批次写 N 条流水，主账只 `UPDATE` 一次；`allocated_delta` 只做审计不进核对。
- **五动作同步钩子**：全部收在 `inventory_service.py`，**禁止路由层直写 `stock_batch`**。
  验收入库显式传 `record_receipt()` 的真实批号；上架/移库由 `stock_move()` 按 FEFO 分摊并原样搬维度；
  拣货分配下沉 `allocated_qty`；发货同步释放；盘盈走占位、盘亏按 FEFO 分摊。
  不传 `batches` 时 service 自动推断维度（与 v3.3 提案的「无维度老流水」差异见 `DEVELOPMENT.md` M21 章）。
- **三层不变式（硬校验，无灰度豁免）**：I1 主账＝流水累加（`/api/stock/reconcile`，语义不变）、
  I2 批次明细＝批次流水累加、I3 主账＝批次明细累加（后两者在新增的
  `/api/stock/batch-reconcile`，容差 `1e-9`，任何不一致直接 `consistent=false`）。
- **老库回填**：`init_db()` 在明细层为空时一次性把历史流水归到 `LEGACY-UNKNOWN` 占位维度、
  按流水聚合生成明细（含零行），保证 I2/I3 立即通过；回填幂等。
- **页面**：「仓储作业 → 库存与追溯 → 批次库存」查询页，支持批号 / 货品属性 / 仅非零库存过滤，
  一键「账目核对」分三段显示 I1 / I2 / I3。新增 `batch_location` 记录上架实物分布。
- 验证：unittest **331 绿**（新增 22 例）、真实 Chrome E2E **10/10**；M6 的 I1 与 309 例旧测试零改动全绿。

### M22 库存调整单 + 盘点增强（2026-11-06 里程碑 ✅）

- **数量变化唯一入口**：新增 `stock_adjustment` / `stock_adjustment_line`（`ADJ-YYYYMMDD-NNN`）。
  手工报损报溢、盘点差异、冲销都必须有调整单；制单只产生 `draft`，**审核通过才过账**
  （`MOVEMENT_ADJUSTMENT`，`ref_id = adjustment_id`）。原 `/api/stocktakes/<id>/adjust` 直接改账旁路
  已移除（返回 410）；冲销生成反向 draft 单，仍需人工审核。
- **状态机**：`open → frozen → adjusted → closed`。盘点差异由明细页人工按钮生成调整单；
  差异全 0 直接结案；调整单审核成功自动把盘点单置 `closed`；调整单取消后可重新生成。
- **盘点方案**：新增 `stocktake_plan`，支持全盘 / 部分 / 动碰三类范围、明盘 / 隐藏账存 / 隐藏差异
  三种盲盘模式；动碰可选「上次结案 / 近 N 天 / 手工时间窗」，并支持 8 种交易类型白名单。
  方案在创建盘点单时快照，`freeze()` 落 `scope_snapshot`，**改方案不影响历史单据**。
- **按批号盘点**（依赖 M21）：`stocktake_line` 唯一键扩到含 `batch_no`，带批次条件时按
  `stock_batch` 分行；指定批号的差异过账会补齐完整批次维度，盘亏在该批号内按 FEFO 分摊。
- **真盲盘分级接口**：`GET /<id>/count-sheet` 与 `POST /<id>/count` 是作业录入视角
  （不返回账存 / 已录数 / 差异，`count` 只回 `counted_lines`）；`GET /<id>` 与
  `GET /<id>/diff-preview` 是复核视角，照常返回差异。
- **页面**：新增「盘点方案」「库存调整」两页，增强「盘点」页（三级导航 20 → 22 页）。
- 验证：unittest **356 绿**（新增 25 例）、真实 Chrome E2E **28/28**；既有 331 例中 10 例断言
  按新状态机适配，M21 批次库存 / 格子矩阵 / 数据隔离 E2E 复跑无回归。

### M23 指定客户专属库存 + 销退口径（2026-11-09 里程碑 ✅）

- **指定客户**：`asn_line.extra.designated_customer_id` 只在明细行、默认空、验收时可改；
  贯通 `batch_receipt → stock_batch → stock_movement`，专属与通用库存按 `uq_stock_batch`
  唯一键**永不合并**。客户必须归属该货主；换客户 / 解绑须走 M24 库存转移单留痕。
- **销退外部批次号**（用户 2026-11-09 口径）：销退单创建 / 验收**必填**，缺号一律 400；
  确实没有请显式填 `/`，系统回溯历史内部批次号并写 `MISSING_EXTERNAL_BATCH_NO` 告警。
  单据头可填一次作为全单默认值，验收时可逐行改。设计 §7.5 的「静默兜底」已被此口径覆盖。
- **质量状态按 B 口径**：`goods_property`（合格/不合格）与 `stock_status`
  （非限制/质检/冻结）两个独立字段，枚举不改；验收时可录质量状态。
- **出库校验只落规则**：新增 `reservation_rules.py` 纯函数（`CUSTOMER_RESERVED_FORBIDDEN`
  规则表、先专属后通用建议、质量状态转换矩阵），始终不接批次级分配（留 M35）；
  **v1 时点**不接 `routes_dn.py`、不做页面提示，同日用户二次确认后已补「事前提示 +
  风险放行日志」（仍不拦截），见下方「M23 口径修订补充」。
- **前端**：收货单条件字段 + 内联校验 + 一键 `/`；验收页上下文提示条 / 草稿值带出 /
  指定客户与质量状态下拉；批次库存页专属徽章（显示客户名）与客户 / 质量状态筛选。
- **v1 时点无新增端点 / 无新增表**：只扩 `POST /api/asns`、`POST /api/asns/<id>/receive`、
  `GET /api/asns/<id>` 的请求与响应字段，`main.py::_migrate_m23()` 幂等补 4 列；
  后续补充新增 2 个端点与 1 张日志表，见下方「M23 口径修订补充」。
- 验证：unittest **375 绿**（新增 19 例 + 按新口径适配 1 例销退既有用例）、
  真实 Chrome E2E **28/28**；隔离 / 格子 / M21 / M22 四组基线 E2E 复跑无回归。

#### M23 口径修订补充（2026-11-09，同一里程碑，不新增编号）

> 用户二次确认：「专属库存发给其他客户要提示，让用户决定是否继续，可以选择继续也可以
> 不继续，但要补事前提示 + 风险放行日志」。**仍不拦截、不改 FEFO、不实现批次级分配**。

- **事前提示**：`GET /api/dns/<id>/reservation-check` 只读预检；`dns.html` 明细页常驻提示条
  （无风险绿色通过 / 有风险黄色列出条数），点「发货」后 `confirm()` 列出风险明细，
  用户可「继续」或「取消」；取消不发任何写请求，库存与状态不动、日志为空。
- **风险放行日志**：`customer_reservation_log` 表 + `GET /api/reservation-logs`
  （只读、带货主隔离、支持 `dn_id` / `keyword` / 分页）；继续发货才逐条写日志，
  与扣减同事务，发货失败或重复发货不产生脏记录。`action` 区分界面确认后继续
  `risk_allowed` 与 API 直调继续 `risk_allowed_unack`。`ship()` 响应带
  `risk_count` / `risk_logged` / `warnings`。
- **预检与实扣严格一致**：预检在内存余额快照上按 `dn_pick` 顺序模拟逐条扣减，并与发货
  共用 `inventory_service.spread_batches()`；顺带修复分次拣货时「分配按在手、扣减按在手」
  造成的分配／释放不对称（原会在发货时报 400），分配动作现按 `qty - allocated_qty` 取容量。
- **验证**：unittest **390 绿**（新增 15 例 `tests/test_m23_reservation_audit.py`）、
  真实 Chrome E2E **26/26**（独立库 + 端口 5115）；隔离 15/15、格子 13/13、M21 10/10、
  M22 28/28、M23 v1 28/28 五组基线复跑无回归。

### M24 库存转移单（2026-11-09 里程碑 ✅）

- **口径**（用户四条全选 A）：只做**同库位内**的批次维度变更（跨库位走 M17 移库单）；
  7 类转移类型一次做全但统一为「源维度组合 → 目标维度组合」的净零搬迁；
  只做 IT 单自身的 `unallocate`；6 个权限码只落常量不挂装饰器，特采只接布尔标记。
- **数量净零**：`Σ qty_delta = 0` 硬校验，**主账 `stock.qty` 全程零 UPDATE**；
  `delta_qty` / `qty_adjust` / `to_qty` / `qty_delta` 一律 400
  `TRANSFER_QTY_MUST_BE_ZERO`。数量变化唯一入口仍是 M22 库存调整单。
- **状态机**：有分配 `draft → allocated → approved`（审核即生效终态）；
  空单 `draft → closed / cancelled`（无库存动作）；已分配单须先 `unallocate` 才能关闭 / 删除；
  `approved` 不可改不可删，只能生成 `reversal_of_id` 冲销单（仍需分配 + 审核）。
- **分配真实占用**：`allocate` 同步增加主账与明细两层 `allocated_qty`，可用库存减少
  （M5 超卖拦截能感知），`unallocate` / `approve` 对称释放。审核时源维度写 `-Q`、
  目标维度写 `+Q` 两条流水，并逐字段写 `inventory_change_log`。
- **三表**：`inventory_transfer` / `inventory_transfer_line` / `inventory_change_log`；
  `inventory_transfer_service.py` 是业务层，所有库存写入仍经 `inventory_service.adjust_stock()`。
- **前端**：`inventory_transfers.html` 列表 / 制单 / 明细 / 分配表单 / 转移明细 /
  字段级变更文档，冲销单支持「使用冲销建议」一键带入反向维度。
- **验证**：unittest **415 绿**（新增 25 例）、真实 Chrome E2E **35/35**（端口 5116）；
  隔离 15/15、格子 13/13、M21 10/10、M22 28/28、M23 v1 28/28、M23 风险放行 26/26
  六组基线复跑无回归。

### M25 上架推荐规则引擎（2026-11-10 里程碑 ✅）

> 用户口径 1A / 2A / 3A：编号不动，实施顺序改为 `M25 → M26 → M35 → M36`；
> 拦截完全来自规则行 `limits`、代码零 hardcoded 红线；出库分配规则与上架规则
> **共用一张 `putaway_rule` 表**，用 `rule_type` 分两类（M35 复用）。
> 本轮先交付 M25 后端，**M26 编辑器页面另起提交**。

- **三张表**（`main.py::_migrate_m25()`，幂等、老库零迁移）：`putaway_rule_set`（策略包，
  货主+仓库精确 > 仓库默认 > 系统默认）、`putaway_rule`（规则行，13 维 `condition` /
  `steps` fallback 链 / `limits` 硬约束 / `score` 权重 / `priority` / `enabled` / `rule_type`）、
  `putaway_recommendation`（推荐日志，供 M26 统计命中率与驳回率）。
- **规则引擎**（`putaway_rules.py`）：按 `priority` 升序第一行命中即停；fallback 链按步骤
  顺序取第一个产出非空候选的步骤；8 项硬约束按配置执行且**不参与打分**；
  9 条评分规则 R07–R15 只在规则行 `score` 显式配置时叠加，M20 六项排序口径保持不变。
- **硬约束三态**（本轮新设计）：`enabled=true` → 推荐排除候选 **且** 上架过账拦截；
  `recommend_only=true` → 只排除推荐候选、过账放行（M20 混放口径）；两者皆假 → 不检查。
  `validate_location()` 只在过账时执行 enforce 级。种子把 `mix_product` / `mix_owner`
  设为 `recommend_only`，`mix_batch` 连推荐也不排除（M21 明细层支持同库位多批次）；
  `loc_status` / `storage_class` / `capacity` 为 enforce 级。M26 打开 `enabled` 即升级为
  过账硬拦截，**全部强度来自配置**。
- **种子 11 行、启用 7 行**（5 冷链 / 8 危险 / 10 不合格 / 20 虚拟 / 25 直调 /
  50 常规 / 999 兜底），另 4 行配置好但停用；储区编码用库里真实存在的
  `ZONE-SHARED / ZONE-COLD / ZONE-NG`。**删掉规则行重启不复活**；删光后推荐退回
  M20 简版、过账不再拦截（2A「删规则行即不拦截」）。
- **推荐分发与拦截**：`putaway_service.recommend()` 有启用规则走规则引擎、零配置走简版，
  两分支都带 `engine` 字段；`GET /api/putaway/recommend` 响应新增
  `rule_set / hit_rule / hit_priority / hit_condition / hit_step / recommendation_id`
  等可解释字段，M20 原字段全部保留；`POST /api/asns/<id>/putaway` 过账前按命中规则行
  `limits` 硬拦截（如冷链上架常温位 → 400 `STORAGE_CLASS_MISMATCH`）。
- **配置 API**：策略包与规则行 CRUD、`/api/putaway-rule-types` 数据字典、
  `/api/putaway-rules/trial` 条件组只读试算、`/api/putaway-recommendations` 日志分页与统计。
- **验证**：unittest **447 绿**（M25 新增 32 例）、真实 Chrome E2E **34/34**（端口 5117）；
  隔离 15/15、格子 13/13、M21 10/10、M22 28/28、M23 v1 28/28、M23 风险放行 26/26、
  M24 35/35 七组基线复跑无回归。
- **边界**：无编辑器页面（M26）、推荐日志未做货主隔离、`abc_class` 与 R12 暂无数据源、
  `rule_type='outbound'` 只落表与 CRUD（M35 才接入）；详见 `DEVELOPMENT.md` M25 章。

### M26 规则配置编辑器与推荐交互（2026-11-13 里程碑 ✅）

> 用户 2026-11-10 定稿实施顺序 `M25 → M26 → M35 → M36` 的第二棒；设计 §14 阶段 F 验收：
> 多规则增删改排序、试算面板可用、改位必填原因码、E2E 覆盖推荐 → 采纳 → 上架。

- **编辑器页** `web_app/putaway_rules.html`（三级导航 23 → 24 页，路径「仓储作业 → 入库单据与作业 → 上架规则配置」）：
  策略包切换 / 新建 / 删除、规则类型（上架 / 出库）切换、**13 维条件组**（布尔维度是/否、多值逗号分隔、留空通配）、
  fallback 链增删与上下移（附加参数保留原 JSON）、8 项 limits 三态勾选、9 条 R07–R15 扩展评分权重、
  行拖拽与 ↑/↓ 排序、规则行增删改与启停、只读条件组试算面板。
- **后端增量**（`routes_putaway_rules.py` / `putaway_rules.py` / `main.py::_migrate_m26()`）：
  `POST /api/putaway-rules/reorder` 批量优先级（先挪负区间再写目标值，绕开 UNIQUE 中间态）、
  `DELETE /api/putaway-rule-sets/<id>`（默认包 / 含规则行禁删）、
  `POST /api/putaway-recommendations/<id>/action` 采纳 / 驳回同一条日志（不重复计数）、
  日志查询扩 `asn_line_id` / `date_from` / `date_to` / `has_hit` 并返回 `rates`（命中率 / 采纳率 / 驳回率）、
  `external_doc_type_dict` 可配字典 6 条种子 + CRUD（v3.3 4A 两步法第二步，字段仍是自由文本、不做强制枚举）。
- **推荐交互**（`asn_putaway.html`）：一键采纳写 `adopt` 日志、驳回必填原因码、
  **人工改位必留痕**（有推荐候选却选了非采纳货位时先写 `reject` 再过账）；
  顶部展示 `engine / rule_set / hit_rule / hit_priority / hit_step / recommendation_id` 可解释字段，便于按日志排查。
  后端过账接口保持 M25 原样；简版引擎无日志 id 时只提示、不阻断作业。
- **演示与回归**：`tools/seed_module_demo.py` 纳入版本管理并新增 M26 模块，通过真实 API 补字典项、
  示例规则行与 adopt/reject 日志；`tools/e2e_m26.py` 是可复跑的 Playwright 回归（独立端口 + 临时库）。
- **验证**：全量 unittest **460 绿**（M26 新增 13 例，M25 447 例历史回归全绿）、真实 Chrome E2E
  `tools/e2e_m26.py` **23/23**（端口 5118 / 5119 两次复跑）；历史里程碑 E2E 脚本未入库，本轮未复跑。
- **边界**：推荐日志仍属控制面统计、未做货主隔离（如需按货主统计须补 `owner_id` 列并注入 `owner_where()`）；
  `abc_class` / R12 无数据源；`rule_type='outbound'` 仍只落配置、M35 才接入分配逻辑；
  字典不反写单据 `external_doc_type` 字段。

### 规划中（见 DEVELOPMENT.md）

- M25 上架推荐规则引擎 ✅ **已交付 2026-11-10**；M26 规则配置与推荐交互 ✅ **已交付 2026-11-13**（见上方两章）
- **主流程优先**：M35 出库波次分配 + FEFO → M36 独立复核单与作业页（用户 4B：登记差异只留痕，
  不生成调整单、不改库存）；M31–M34 基础资料新域按用户要求**后移**
- 报表统计（M28，归入蓝图第 4 层经营报表）
- **降级暂缓**（编号保留、不排期）：补货预警（M27）、外部对接 API（M29）、CSV 导入导出（M30）
- **M31+ 新域**（保留 M20–M30 编号不动）：M31 司机车辆 / M32 产品类型字典 / M33 序列号台账 /
  M34 货位组主数据 / M35 出库波次与 FEFO 分配 / M36 出库复核 / M37 养护 / M38 拆单合单（可选）/
  M39 GSP 报表 / M40 工作量统计 / M41 打印模板与 PDF 预览（字段映射 + 拖拽排版、预览可临时编辑）
  / M42 打印任务与自动触发（**后置**，全部做好后再补充）

> 完整设计与口径见 `docs/PUTAWAY_RECOMMENDATION.md`（v3.3，2026-11-05 定稿）。
> **5 层领域蓝图、现状对照、实施顺序、硬约束配置化与打印方案定稿**见 `docs/DOMAIN_BLUEPRINT.md`（v3，2026-11-05）。
> 实施顺序（2026-11-10 用户 1A 修订，编号一个不动）：M1–M26 已交付 → **M35 → M36**
> 先把出入库 / 库存主流程跑完，M31–M34 基础资料新域后移。
> **硬约束全部配置化**：混放 / 冷链 / 危险由 M25/M26 的多条上架规则（优先级 + 单号 / 产品信息条件组）决定，
> 不写死、不保留系统级 hardcoded 红线；M20→M25 过渡期 `putaway()` 不拦截、只靠推荐页提示，
> **该过渡期已随 M25 交付结束**（冷链 / 危险 / 不合格按命中规则行 `limits` 拦截）。
> 打印采用演示版零安装路线：系统 Chrome headless 生成 PDF（不额外安装）+ 自动弹出预览人工确认；
> 预览页可临时微调数量/备注/收货人等（不回写业务单据）。

## API 概览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/owners` | 创建货主（编码必填唯一） |
| GET | `/api/owners` | 货主列表，支持 `keyword`/`status`/分页 |
| GET/PUT/DELETE | `/api/owners/<id>` | 详情/更新/删除（名下有物料时拒绝） |
| POST | `/api/locations` | 创建库位（编码必填唯一；可选 `warehouse_id` / `zone_id` / 三个可空混放开关，仓库与储区须一致或储区全仓共享） |
| GET | `/api/locations` | 列表，支持 `keyword`/`zone`/`loc_type`/`status`/`warehouse_id`/`zone_id`/分页；返回仓库名、储区名与 `effective_mix`（生效混放值与来源） |
| GET/PUT/DELETE | `/api/locations/<id>` | 详情/更新/删除（有库存时拒绝） |
| POST | `/api/zones` | 创建储区（编码/名称必填；温区分类、所属仓库可空＝全仓共享、三个混放开关、启停、备注） |
| GET | `/api/zones` | 储区列表，支持 `keyword`/`temp_class`/`status`/`warehouse_id`/分页；传 `warehouse_id` 时返回该仓专属 + 全仓共享 |
| GET/PUT/DELETE | `/api/zones/<id>` | 详情/更新/删除（已挂库位或已被货品设为推荐储区时拒绝；返回库位数与混放标签） |
| GET | `/api/product-locations` | 货品固定货位绑定列表，支持 `product_id`/`location_id` |
| POST | `/api/product-locations` | 新增绑定（一个 SKU 可绑多个；仅启用中的存储位/拣货位，重复绑定 409） |
| DELETE | `/api/product-locations/<id>` | 解绑 |
| GET | `/api/putaway/recommend` | 上架推荐只读预览：`asn_id`/`line_id` 必填，可选 `qty`/`top_n`；返回 Top-N 货位（分数/理由/已用容量/生效混放）、被排除货位及原因；**不写库存** |
| POST | `/api/asns` | 创建收货单（行 = 物料×预期数量，校验货主一致性；单号留空自动生成 `ASN-日期-流水`；单据类型默认「采购」；支持 外部单号/订单来源/来源仓库/供应商/`customer_id`/承运商/自定义字段，自动算 总数量·品类数）。供应商与客户须属同一货主，否则 400 明确报错；`supplier_contact`/`supplier_phone`/`supplier_address`/`customer_contact`/`customer_phone`/`customer_address` 可覆盖主数据快照；明细行可带 20 个商品档案 + 批次信息字段（含灭菌批号/产地/患者名称/内部批次号/货品属性/UDI/生产企业），全部可人工编辑、落库为单据快照；`goods_property` 仅接受 合格/不合格；可选 `is_virtual`/`is_direct`/`is_cold_chain`/`is_hazard`（0/1，默认 0）与 `external_doc_type`（≤32 字符，默认空），**仅记录不拦截** |
| GET | `/api/asns` | 收货单列表，支持 `keyword`/`status`/分页（返回 来源仓库/供应商/总数量/品类数） |
| GET | `/api/asns/<id>` | 收货单详情 + 明细行（返回规格/货号/单位/厂家/生产企业/产地/UDI/批号/灭菌批号/生产日期/失效日期/内部批次号/货品属性/患者名称/注册证号/注册人/生产许可证等快照，老单据回退物料主数据；每行附本行已生成的 `batch_receipts`） |
| POST | `/api/asns/<id>/receive` | 收货到收货区位（部分收货，状态自动推进）；同事务写入批次信息，返回 `batch`（唯一 `batch_no`、批次档案、`internal_batch_no` 及其来源）；可传 `internal_batch_no` 覆盖（退货/外部数据场景） |
| POST | `/api/asns/<id>/putaway` | 上架：暂存位 → 存储/拣货位（同事务一减一增） |
| POST | `/api/asns/<id>/cancel` | 取消（仅未收货） |
| POST | `/api/dns` | 创建出库单（行 = 物料×订购数量，校验货主一致性；单号留空自动生成 `DN-日期-流水`；支持 单据类型/外部单号/订单来源/来源仓库/承运商/自定义字段，自动算 总数量·品类数；可选 `is_virtual`/`is_direct`/`is_cold_chain`/`is_hazard`（0/1，默认 0）与 `external_doc_type`（≤32 字符，默认空），**仅记录不拦截**） |
| GET | `/api/dns` | 出库单列表，支持 `keyword`/`status`/分页 |
| POST | `/api/dns/<id>/pick` | 拣货分配（可用量拦截超卖，明细落库；同一行可分次拣，分配按 `qty - allocated_qty` 取容量） |
| GET | `/api/dns/<id>/reservation-check` | M23 发货前**只读**预检专属库存风险（不拦截、不写库），返回 `risk_count` / `risks` |
| POST | `/api/dns/<id>/ship` | 发货（要求拣满，逐库位扣减库存）；可选 `risk_acknowledged`，风险放行与扣减同事务留痕，响应带 `risk_count` / `risk_logged` / `warnings` |
| POST | `/api/dns/<id>/cancel` | 取消（仅未拣货） |
| POST | `/api/transfers` | 创建移库单（行 = 物料×来源位×目标位×数量，校验货主/库位一致性） |
| GET | `/api/transfers` | 移库单列表，支持 `keyword`/`status`/分页 |
| GET | `/api/transfers/<id>` | 移库单详情（联表带出来源/目标库位编码） |
| POST | `/api/transfers/<id>/move` | 执行移库（同事务一减一增，写 move_out/move_in 流水） |
| POST | `/api/transfers/<id>/cancel` | 取消（仅未执行） |
| POST | `/api/stocktakes` | 创建盘点单（货主×编码唯一；可选 `plan_id` 与一次性 `scope` / `blind_mode` 覆盖，配置在创建时快照） |
| GET | `/api/stocktakes` | 盘点单列表，支持 `keyword`/`status`/分页（带出 `plan_code`） |
| GET | `/api/stocktakes/<id>` | 盘点单详情（复核视角：物料/库位编码 + `book_qty` / `counted_qty` / `diff`） |
| POST | `/api/stocktakes/<id>/freeze` | 按方案范围生成盘点行并落 `scope_snapshot`（状态→frozen）；全盘/部分/按批号/动碰 |
| GET | `/api/stocktakes/<id>/count-sheet` | M22 作业录入视角；`blind_mode != none` 时**不返回账存 / 已录数 / 差异** |
| POST | `/api/stocktakes/<id>/count` | M22 盲盘录入，只回 `counted_lines`（负数 400，响应不含账存 / 已录数 / 差异） |
| GET | `/api/stocktakes/<id>/diff-preview` | M22 复核视角，返回全量行 / `diff_lines` / `diff_count` / `uncounted` |
| POST | `/api/stocktakes/<id>/generate-adjustment` | M22 **人工按钮**：按差异生成一张 `draft` 调整单（不自动过账）；差异全 0 直接结案 |
| POST | `/api/stocktakes/<id>/close` | 结案兜底（`adjusted` 且无待审核调整单时） |
| POST | `/api/stocktakes/<id>/adjust` | **已停用（410）**：M22 起差异不再由盘点路由直接过账 |
| GET/POST | `/api/stock-adjustments` | M22 调整单列表（按明细行货主隔离）/ 手工制单（仅生成 `draft`） |
| GET | `/api/stock-adjustments/<id>` | M22 调整单详情 + 明细行（带出物料 / 库位编码、`qty_before`） |
| POST | `/api/stock-adjustments/<id>/approve` | M22 审核过账：逐行 `adjust_stock()`，成功后来源盘点单自动 `closed` |
| POST | `/api/stock-adjustments/<id>/cancel` | M22 取消 `draft` 单；来源盘点单保持 `adjusted`，可重新生成 |
| POST | `/api/stock-adjustments/<id>/reverse` | M22 生成反向 `draft` 冲销单（仍需人工审核），原单保持 `approved` |
| GET/POST | `/api/stocktake-plans` | M22 盘点方案列表 / 新建 |
| GET/PUT/DELETE | `/api/stocktake-plans/<id>` | M22 方案详情 / 更新 / 删除（被盘点单引用时拒绝，引导停用） |
| POST | `/api/stocktake-plans/trial`、`/<id>/trial` | M22 按请求体或已存方案试算将生成的盘点行数与时间窗 |
| GET | `/api/batch/fields` | 批次字段字典（20 个字段及默认是否关键），前端表单据此动态渲染 |
| GET | `/api/batch/config` | 批次字段配置列表 + 当前生效的关键字段 |
| PUT | `/api/batch/config/<field_name>` | 编辑配置（是否关键/启用/默认值/标签/备注/排序）；至少保留一个关键字段 |
| GET | `/api/batch/profiles` | 批次档案列表（关键批次信息），支持 `keyword`/`owner_id`/`product_id`/分页 |
| POST | `/api/batch/profiles` | 手动预配置批次档案（关键批次信息已存在返回 409，物料归属货主须一致） |
| GET | `/api/batch/profiles/<id>` | 批次档案详情（关键批次信息 + 完整批次信息 + 关联验收入库批次） |
| GET | `/api/batch/receipts` | 验收入库批次流水（每次收货唯一批次号），支持 `keyword`/`owner_id`/`product_id`/`asn_id`/`asn_line_id`/`goods_property`/分页 |
| GET | `/api/batch/receipts/<id>` | 单条入库批次详情（含货主/物料/库位/收货单编码与完整批次信息） |
| POST | `/api/customers` | 创建客户（归属货主，code 必填唯一） |
| GET | `/api/customers` | 客户列表，支持 `keyword`/`status`/`owner_id`/分页 |
| GET/PUT/DELETE | `/api/customers/<id>` | 详情/更新/删除（被出库单引用时拒绝） |
| POST | `/api/suppliers` | 创建供应商（归属货主，code 必填唯一） |
| GET | `/api/suppliers` | 供应商列表，支持 `keyword`/`owner_id`/`status`/分页 |
| GET/PUT/DELETE | `/api/suppliers/<id>` | 详情/更新/删除（被收货单引用时拒绝） |
| POST | `/api/warehouses` | 创建仓库（运营方资产、不绑定货主，code 必填唯一；可选 `db_path` 独立数据文件，留空＝共用主库） |
| GET | `/api/warehouses` | 仓库列表，支持 `keyword`/`status`/分页（返回 `db_path`） |
| GET/PUT/DELETE | `/api/warehouses/<id>` | 详情/更新/删除（跨所有业务库检查 ASN/DN 引用后再拒绝；`db_path` 必须是项目目录内 `.db` 相对路径） |
| GET | `/api/stock` | 库存余额列表，支持 `owner_id`/`product_id`/`location_id`/`has_stock`/分页 |
| GET | `/api/stock/reconcile` | I1 余额核对（主账余额 = 流水累加，返回不一致明细；M6 语义不变） |
| GET | `/api/stock/batch` | M21 批次级库存余额，支持 `batch_no` / `goods_property` / `stock_status` / `designated_customer_id` / `has_stock` / 分页；接入货主隔离 |
| GET | `/api/stock/batch-reconcile` | M21 校验 I2（批次明细＝批次流水累加）+ I3（主账＝批次明细累加），**硬校验无灰度豁免**，分段返回 `i2` / `i3` 的 `checked` 与 `mismatches` |
| GET | `/api/reservation-logs` | M23 风险放行日志（**只读**，带货主隔离，支持 `dn_id` / `keyword` / 分页；JOIN 物料、库位与客户名称） |
| GET | `/api/inventory-transfers` | M24 库存转移单列表，支持 `status` / `transfer_type` / `keyword` / 分页；按明细行货主隔离 |
| POST | `/api/inventory-transfers` | M24 制单（draft 空单，不过账）；`delta_qty` 等数量字段 400 |
| GET | `/api/inventory-transfers/<id>` | M24 详情：单据头 + 转移明细 + 字段级变更文档 |
| POST | `/api/inventory-transfers/<id>/allocate` | M24 分配库存（主账与明细两层 `allocated_qty` 真实占用） |
| POST | `/api/inventory-transfers/<id>/unallocate` | M24 取消分配（对称释放，回 draft） |
| POST | `/api/inventory-transfers/<id>/approve` | M24 审核生效（净零成对流水 + 变更文档，`approved` 终态） |
| POST | `/api/inventory-transfers/<id>/close`、`/delete`、`/reverse` | M24 关闭空单 / 删除空单 / 生成冲销单（反向 draft 仍需分配 + 审核） |
| GET | `/api/putaway-rule-types` | M25 编辑器数据字典：2 类 `rule_type` / 13 维条件 / 9 种寻址 / 8 项 limits / 9 条评分 |
| GET/POST | `/api/putaway-rule-sets` | M25 策略包列表 / 新建 |
| PUT | `/api/putaway-rule-sets/<id>` | M25 修改策略包 |
| GET/POST | `/api/putaway-rules` | M25 规则行列表（可按 `set_id` / `rule_type` 过滤）/ 新建；校验条件维度与寻址类型 |
| PUT/DELETE | `/api/putaway-rules/<id>` | M25 修改 / 删除规则行（**删除即不再拦截**） |
| POST | `/api/putaway-rules/trial` | M25 条件组只读试算，不写库、不查规则表 |
| GET | `/api/putaway-recommendations` | M25 推荐日志分页 + action 统计；M26 扩 `asn_line_id` / `date_from` / `date_to` / `has_hit` 与 `rates` 命中率 |
| POST | `/api/putaway-recommendations/<id>/action` | M26 采纳 / 驳回（改位必填原因码，同一条日志更新、不重复计数） |
| POST | `/api/putaway-rules/reorder` | M26 拖拽排序批量写 priority |
| DELETE | `/api/putaway-rule-sets/<id>` | M26 删除空策略包（默认包 / 含规则行禁删） |
| GET/POST | `/api/external-doc-types` | M26 外部单据类别字典列表（含改位原因码清单）/ 新建 |
| PUT/DELETE | `/api/external-doc-types/<id>` | M26 字典修改 / 删除 |
| GET | `/api/movements` | 库存动作流水，支持 `owner_id`/`product_id`/`location_id`/`ref_type`/`ref_id`/分页 |
| POST | `/api/products` | 创建物料（SKU 必填唯一，可挂 `owner_id`、`default_zone_id` 推荐储区） |
| GET | `/api/products` | 列表，支持 `keyword`/`category`/`status`/`owner_id`/分页（返回 `default_zone_name`） |
| GET/PUT/DELETE | `/api/products/<id>` | 详情/更新/删除（库存非 0 时拒绝） |
| POST | `/api/auth/login` | 登录（用户名+密码），返回无状态 token |
| GET/POST | `/api/roles` | 角色列表 / 创建（需 `user:manage`） |
| GET/PUT/DELETE | `/api/roles/<id>` | 角色详情/更新/删除（被用户引用时拒绝） |
| GET/POST | `/api/users` | 用户列表 / 创建（需 `user:manage`） |
| GET/PUT/DELETE | `/api/users/<id>` | 用户详情/更新（含改密）/删除；列表/详情返回 `warehouse_scope` / `owner_scope` |
| GET | `/api/scope/context` | 当前数据权限上下文 + 可切换仓库清单 + 当前仓（前端启动自检；带失效仓 header 时软解析） |
| GET | `/api/scope/warehouses` | 当前账号可切换的仓库清单（A 作业视角） |
| GET | `/api/scope/owners` | 当前账号可见的货主清单 |
| POST | `/api/scope/warehouse` | 校验一次切仓是否合法并回显目标仓（真正切换靠 `X-WMS-Warehouse` header）；上下文摘要含 `grid_enabled` / `grid_owner_ids` |
| GET/PUT | `/api/users/<id>/grants` | 读取 / 配置账号的仓库·货主可见范围（`all`=全部 / `grant`=按勾选授权，需 `user:manage`） |
| GET | `/api/owner-warehouses` | 货主 × 仓库业务关系矩阵：全部货主、全部仓库、已勾选格子、已启用格子的仓（需 `user:manage`） |
| PUT | `/api/owner-warehouses/<wid>` | 按仓全量替换登记行 `owner_ids: [...]`；空数组＝关闭该仓格子过滤（需 `user:manage`） |
| POST | `/api/carriers` | 创建承运商；可选 `owner_id`（留空＝全系统共享，非空＝归属货主，后期可按货主管控） |
| GET | `/api/carriers` | 承运商列表，支持 `keyword`/`carrier_type`/`status`/`owner_id`（`none`=只看共享）/分页 |

## 项目结构

```
main.py               Flask 入口；中央库/业务库双层连接与按仓选库、跨仓主数据同步、before_request 数据权限守门、DDL 与迁移、静态托管 web_app
scope.py              仓库/货主双维隔离上下文切面：授权解析、owner_where() 注入器、写路径与按 ID 归属校验、ALL 显式化、owner_warehouse 格子交集
auth.py               认证与权限基础（密码哈希、token 签发/校验、require_permission 装饰器；用户/角色只读中央库）
constants.py          状态机、流水类型、批次号前缀、温区/混放字段、推荐理由码、单据标识位与 parse_doc_flags()（显式集中）
inventory_service.py  库存变更服务层（事务/流水/零行保留，路由不直接写库存）；M21 起同时维护 stock_batch 明细层，按 FEFO 自动分摊批次维度，禁止路由直写明细
adjustment_service.py  M22 库存调整单服务层：制单/审核过账/取消/冲销；指定批号时从 stock_batch 补齐完整批次维度
inventory_transfer_service.py M24 库存转移单服务层：7 类统一为「源维度→目标维度」净零搬迁、双层分配占用/释放、变更文档、冲销
stocktake_service.py  M22 盘点方案服务层：生效配置解析、范围条件组编译、动碰时间窗与交易类型、盘点行试算
putaway_service.py    M20 简版上架推荐（零启用规则时的兜底）+ M25 分发器：有启用规则走规则引擎
putaway_rules.py      M25 上架规则引擎：13 维条件组 / priority 命中即停 / fallback 链 / 8 项 limits 三态 / 评分 / 可解释输出 / 推荐日志 / 过账校验 / 配置 CRUD / 11 行种子；M26 追加日志动作 / 统计 / 排序 / 字典 CRUD
routes_putaway_rules.py M25 规则配置 Blueprint；M26 扩采纳驳回 / 排序 / 删包 / 字典 5 类端点
web_app/putaway_rules.html M26 规则配置编辑器（13 维条件组 / fallback 链 / limits 三态 / 试算 / 字典 / 日志统计）
tools/e2e_m26.py      M26 可复跑 Playwright 端到端回归（独立端口 + 临时库）
tools/seed_module_demo.py M17–M26 模块演示数据幂等播种（走真实 API，不直接写业务表）
batch_fields.py       批次信息字段字典（配置播种、关键批次信息指纹）
batch_service.py      批次服务层（验收入库时写批次档案 + 唯一批次流水、内部批次号规则；M23 销退外部批次号校验）
reservation_rules.py  M23 规则层（纯函数）：指定客户出库校验规则表、先专属后通用建议、质量状态转换矩阵；不接路由
reservation_audit.py  M23 补充：专属库存风险只读预检 + 放行日志落库（内存余额快照按发货顺序模拟，不拦截）
routes_reservation_log.py M23 补充：风险放行日志只读查询 Blueprint（/api/reservation-logs，带货主隔离）
routes_inventory_transfer.py M24 库存转移单 Blueprint（/api/inventory-transfers，7 动作 + 统一事务回滚）
routes_owner.py       货主主数据 Blueprint
routes_product.py     物料主数据 Blueprint（含推荐储区）
routes_location.py    库位主数据 Blueprint（含仓库/储区/混放继承）
routes_zone.py        储区主数据 Blueprint
routes_product_location.py 货品固定货位绑定 Blueprint
routes_putaway.py     上架推荐 API Blueprint（M25 起写推荐日志，不改库存）
routes_putaway_rules.py M25 规则配置 Blueprint（/api/putaway-rule-*、/api/putaway-recommendations）
routes_asn.py         收货单与上架 Blueprint（验收入库同事务写批次信息；M25 起上架过账按规则行 limits 拦截）
routes_dn.py          出库单（拣货/发货）Blueprint；M23 补充发货前只读风险预检与放行留痕（不拦截）
routes_stock.py       库存账/流水/余额核对只读 Blueprint
routes_customer.py   客户主数据 Blueprint（出库收货方维度）
routes_user.py        用户与角色（登录/权限）Blueprint，全部走中央库
routes_scope.py       数据权限与切仓上下文（/api/scope/*、/api/users/<id>/grants）Blueprint
routes_owner_warehouse.py 货主 × 仓库业务关系矩阵配置（/api/owner-warehouses）Blueprint
routes_stocktake.py   盘点单 Blueprint（M22 重写：按方案冻结、真盲盘分级接口、人工生成调整单）
routes_stock_adjustment.py M22 库存调整单（/api/stock-adjustments）Blueprint
routes_stocktake_plan.py   M22 盘点方案（/api/stocktake-plans，含试算）Blueprint
routes_batch.py       批次配置/批次档案/入库批次流水 Blueprint
routes_warehouse.py   仓库主数据 Blueprint（控制面；db_path 安全校验、跨库引用检查）
web_app/              Web demo（index/owners/products/locations/zones/customers/suppliers/warehouses/carriers/asns/asn_receive/asn_putaway/dns/transfers/stocktakes/stocktake_plans/stock_adjustments/inventory_transfers/batches/stock_batch/stock/roles/users/owner_warehouses/login + app.html 工作台 + css/js）
web_app/js/layout.js  递归多层导航树（3 级 / 23 页）；独立页与工作台共用 mount() 实现折叠、祖先链自动展开、当前页高亮与 localStorage 记忆；独立页侧栏挂载当前仓库选择器
web_app/js/api.js     原生 fetch 封装 + 当前仓库上下文（X-WMS-Warehouse header / mountWarehousePicker()）+ 登录态与工作台标签工具
tools/seed_demo.py    一键演示数据播种（3 货主/8 物料/8 库位/4 客户 + 储区/推荐储区/固定货位绑定 + 收货上架→拣货发货，支持 --reset/--check）
docs/REFERENCES.md    开源 WMS 设计参考笔记（GreaterWMS/Sentry/Aardvarkland + M19 批次设计）
docs/PUTAWAY_RECOMMENDATION.md  上架推荐 / 批次级库存 / 转移 / 盘点设计提案（v3.3）
docs/DOMAIN_BLUEPRINT.md       自上而下 5 层领域蓝图、现状对照、M31+ 框架与打印方案定稿（v3）+ §9 多租户 PG/RLS 未来演进路线
docs/PRD_TEMPLATE.md           PRD 页面记录模板（11 项 + 业务/功能/实现三层写法）
docs/PRD_SOURCES.md            PRD 素材库：逐页详细记录 / API 与数据模型 / 里程碑↔页面索引
tests/                单元测试（unittest，共享临时库+每用例清表）
DEVELOPMENT.md        里程碑开发记录
```

## 设计约定

- 所有接口返回 JSON：成功 `{"ok": true, ...}`，失败 `{"ok": false, "errors": [...]}` + 对应 4xx 状态码
- 库存只由单据驱动：收货/上架（M4）→ 拣货/发货（M5）→ 移库/盘点（M17/M18），全部经 inventory_service 落流水
- 批次只由验收驱动：批次流水无手工新增接口，由 `receive()` 在同事务内经 batch_service 写入；关键批次信息由 `batch_config` 配置，改配置不重算历史档案
- 上架推荐不写库存：`/api/putaway/recommend` 只算建议与理由，采纳＝把 `location_id` 填进原有上架接口，过账路径唯一；**M25 起会写一条 `putaway_recommendation` 日志**（可解释留痕，不改库存、不参与过账）
- 混放生效值三级作用域：库位自身非空优先 → 继承储区 → 默认全部禁止（保守口径）
- 时间戳由 SQLite `datetime('now','localtime')` 生成
- **库存两层账（M21）**：`stock` 汇总层主键永不改，`stock_batch` 明细层由同一批流水驱动；
  一次动作 N 个批次写 N 条流水、主账只 UPDATE 一次，`Σ qty_delta` 必须等于主账变动量；
  I1 / I2 / I3 三条不变式容差 `1e-9`，`allocated_delta` 只做审计不进核对；
  `batches=None` 时由 `inventory_service` 自动推断维度，不允许「老链路不写明细」造成 I3 漂移
- **数量变化唯一入口（M22）**：手工调整 / 盘点差异 / 冲销都必须有一张 `stock_adjustment`，
  `draft` 不过账、审核通过才写流水（`ref_id = adjustment_id`）；盘点路由不得直接调 `adjust_stock()`，
  冲销是生成反向单而非回改原单；盲盘录入接口不返回账存与差异，复核接口才返回
- **专属库存与销退（M23）**：`designated_customer_id` 只在 ASN 明细行、默认空，验收时取值才写进
  `batch_receipt` / `stock_batch` / `stock_movement`；专属与通用按唯一键分开且永不合并；
  出库校验纯规则层不接批次级分配（M35 才接入），发货前只做只读预检 + 风险放行留痕、不拦截；
  销退外部批次号必填，缺号 400，显式 `/` 才走回溯并告警；
  `goods_property` 与 `stock_status` 两字段独立，绑定解绑 / 状态变更一律走 M24 转移单
- **风险放行不拦截（M23 补充）**：预检与日志都按 `dn_pick` 顺序在内存余额快照上模拟逐条扣减，
  与发货共用 `spread_batches()`；用户取消＝零写请求，继续才在发货同事务内逐条写
  `customer_reservation_log`（界面确认 / API 直调用 `action` 区分）；分配动作按
  `qty - allocated_qty` 取容量，扣减动作按 `qty`，保证分次拣货的分配与释放对称
- **库存转移只改维度（M24）**：审核动作 `qty_delta` 恒为 0，源维度 `-Q` / 目标维度 `+Q`
  成对流水；`adjust_stock()` 在 `qty_delta == 0` 时只 SET `allocated_qty`、不 SET `stock.qty`；
  分配与释放同步作用于主账与明细两层；跨库位走 M17 移库单，数量变化只走 M22 库存调整单；
  `approved` 是终态，只能生成冲销单（仍需分配 + 审核）
- **上架硬约束全部配置化（M25）**：拦截内容只来自命中规则行 `limits`，代码零 hardcoded 红线；
  按 `priority` 升序第一行命中即停，命中的约束按 pass/reject 过滤、**不参与打分**；
  三态 `enabled`（推荐排除 + 过账拦截）/ `recommend_only`（只排除推荐候选）/ 皆假（不检查）；
  种子 11 行启用 7 行，删规则行重启不复活、删光即退回 M20 简版且过账不拦截；
  `putaway_rule.rule_type` 分 `putaway` / `outbound` 两类，M35 出库分配复用同一张表；
  R07–R15 评分只在规则行 `score` 显式配置时叠加，默认不改变 M20 排序口径
- **数据隔离三层**：连接层按 `warehouse.db_path` 选库（仓库），SQL 层注入 `owner_id`（货主），数据层行内带标识列；
  货主 / 仓库主数据以**中央库为唯一权威写入点**，业务库持有只读副本
- **ALL 必须显式**：账号「看全部仓 / 全部货主」由 `user.*_scope='all'` 明确给出；授权行缺失、上下文缺失一律 403 拒绝，
  绝不退化成不加条件；写入路径与按 ID 访问路径同样校验归属
- **可见范围取交集**：最终可见货主 = 账号授权货主 ∩ 当前仓 `owner_warehouse` active 登记货主；
  格子启用时 `owner_all` 强制置 False（「全部货主」也要被业务关系约束）。
  格子表是**业务关系登记**而非授权表：空表＝功能未启用（向下兼容），与「授权缺失一律拒绝」是两层不同语义
- **数据库选型不变**：继续 SQLite + 应用层隔离；多 Tenant / PostgreSQL / RLS 属未来路线，不写进当前架构描述
