# MiniWMS — 轻量仓库管理系统（WMS）

一个面向小微仓储场景的多货主 WMS REST API 服务：管理货主、物料主数据、库区库位、收货上架、
拣货发货、移库与盘点；每个货主拥有独立的物料与库存，每个库位的余额始终可由流水推导核对。

## 技术栈

- Python 3.8+（仅标准库）
- Flask（唯一第三方依赖，见 `requirements.txt`）
- SQLite（单文件数据库，零配置）

## 快速开始

```bash
pip install -r requirements.txt
python main.py            # 默认监听 0.0.0.0:5100，数据库文件 ./miniwms.db
```

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

### 规划中（见 DEVELOPMENT.md）

- 库区库位、收货+上架、拣货+发货、库位库存流水、移库、盘点、补货预警、报表统计、CSV 导入导出

## API 概览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/owners` | 创建货主（编码必填唯一） |
| GET | `/api/owners` | 货主列表，支持 `keyword`/`status`/分页 |
| GET/PUT/DELETE | `/api/owners/<id>` | 详情/更新/删除（名下有物料时拒绝） |
| POST | `/api/products` | 创建物料（SKU 必填唯一，可挂 `owner_id`） |
| GET | `/api/products` | 列表，支持 `keyword`/`category`/`status`/`owner_id`/分页 |
| GET/PUT/DELETE | `/api/products/<id>` | 详情/更新/删除（库存非 0 时拒绝） |

## 项目结构

```
main.py               Flask 入口、数据库初始化与连接管理
routes_owner.py       货主主数据 Blueprint
routes_product.py     物料主数据 Blueprint
tests/                单元测试（unittest，共享临时库+每用例清表）
DEVELOPMENT.md        里程碑开发记录
```

## 设计约定

- 所有接口返回 JSON：成功 `{"ok": true, ...}`，失败 `{"ok": false, "errors": [...]}` + 对应 4xx 状态码
- 库存数量只允许由业务单据（收货/拣货/移库/盘点）驱动变化，M1 阶段仅随建档初始化为 0
- 时间戳由 SQLite `datetime('now','localtime')` 生成
