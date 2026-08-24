# MCP-DBHub 集成分析 (2026-05-20)

## 背景

用户向 Hermes Agent 咨询 [punkpeye/awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) 中的可用 MCP 服务，然后专门问了 **DBHub (bytebase/dbhub)** 如何与其缠论量化交易系统配合。

## DBHub 概况

- 轻量级 MCP 数据库服务器，零依赖，Token 高效
- 支持数据库：PostgreSQL, MySQL, SQL Server, MariaDB, SQLite
- 配置方式：`dbhub.toml` 文件
- 安装：`npx @bytebase/dbhub` 或 Docker
- 提供 Web Workbench 可视化查询界面
- 内置 MCP 工具：SQL 查询、表浏览、schema 发现

## 系统数据架构现状

| 数据类型 | 存储方式 | DBHub 兼容？ |
|----------|----------|-------------|
| K 线缓存（日/30分钟） | Parquet（`data_cache/*.parquet`） | ❌ 不支持 Parquet |
| 评分历史（score_history） | SQLite（`~/.hermes/data/stock_scores.db`） | ✅ |
| 配置 | YAML / JSON | ❌ 不是数据库 |
| 回测记录 | 运行时内存 + 屏幕输出 | ❌ 无持久化 |

## 集成方案对比

### 方案 A：零改动，只连评分 SQLite

**改动量**：0（纯配置）
**效果**：能用自然语言跨股聚合查询评分趋势

示例查询（Hermes 通过 DBHub MCP 执行）：
```
近30天综合评分最高的10只股，要求评过至少3次，按均值排序
```
或：
```
查看002415的评分走势，最近5次评分的变化
```

### 方案 B：K 线数据也进 SQLite（推荐发展路径）

**改动量**：~150 行，集中在 `data_manager.py`
**效果**：整个缠论系统可通过自然语言查询

示例查询：
```
哪些股票在最近5天出现了日线底分型，且成交量大于5日均量的1.5倍
```
```
当前持仓中，谁的30分钟级别出现背驰信号
```

**实现思路**：
1. 在 `data_manager.py` 加 `save_to_sqlite()`，每次下载/更新 K 线后写入 SQLite
2. 统一 Parquet 做高性能回测算子，SQLite 做实时查询层
3. 一次性迁移脚本处理已有的 `data_cache/*.parquet`

## 分析方法论（可复用）

当评估新 MCP 服务器或外部工具与该系统的集成时，按此流程：

1. **查工具能力边界**：支持的数据库类型/数据格式/协议
2. **对照系统数据流转**：核心数据存在哪里（Parquet/JSON/SQLite），是怎么流通的
3. **映射匹配度**：工具读什么，系统有什么，中间有没有格式断层
4. **给出分级建议**：
   - 🟢 零改动立即可用：纯配置，不改代码
   - 🟡 小改动提升价值：~150 行内，集中在 1 个文件
   - 🔴 架构级改造：需要重设计数据流

## 关键结论

- DBHub 本身是好工具，只是缠论系统的核心数据（K 线 Parquet）不在数据库里
- 评分 SQLite 零改动可连，但价值有限（仅用于历史评分回顾）
- 真正释放 DBHub 价值需要将 K 线数据同步一份到 SQLite
- 不改动的情况下，**安装优先级低于系统内部优化**

## 相关参考

- `data-cache-architecture.md` — Parquet 缓存架构详解
- GitHub: [bytebase/dbhub](https://github.com/bytebase/dbhub)
- Awesome MCP Servers: [punkpeye/awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) (86.9k stars)