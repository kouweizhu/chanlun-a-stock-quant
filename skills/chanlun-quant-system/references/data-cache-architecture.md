# Parquet 数据缓存架构

## 缓存文件结构

```
data_cache/
├── {code}_daily.parquet     # 日线K线 (TTL: 24h)
├── {code}_30min.parquet     # 30分钟K线 (TTL: 6h)
└── ...
```

- **格式**: Parquet (pyarrow/fastparquet)，非 JSON（30min 数据量大，parquet 压缩率 5-10x，读取快 20x）
- **命名**: `{代码}_{级别}.parquet` — 裸代码（如 `600887_daily.parquet`），不带 sh/sz 前缀
- **位置**: `data_manager.py` 中 `cache_dir` 默认为 `os.path.join(脚本目录, "data_cache")`

## 缓存 TTL（get_klines() 第 310-312 行）

| 级别 | 默认 TTL | 用途 |
|------|:-------:|------|
| daily | 24h | 日线收盘价每日变化一次，24h 覆盖下一个交易日开盘前 |
| 30min | 6h | 30 分钟线日内多次更新，6h 确保盘中有较新数据但不过度拉取 |

可传 `cache_ttl_hours` 参数覆盖默认值。

## 缓存命中逻辑（第 316-340 行）

```python
if use_cache and os.path.exists(cache_path):
    file_mtime = os.path.getmtime(cache_path)
    age_hours = (datetime.now() - file_mtime).total_seconds() / 3600
    if age_hours < cache_ttl_hours:
        df = pd.read_parquet(cache_path)
        if not df.empty:
            # 应用 start_date/end_date 过滤
            return df  # Cache HIT
    # 过期/空 → Cache EXPIRED → 走 API 链重拉
```

**关键行为**:
- 懒刷新 — 仅在调用 `get_klines(code, level)` 时检查 TTL。不自动刷新。
- 过期缓存文件**不会自动删除**，只是被忽略。下次调用会重新从 API 拉取并覆写。
- 如果某标的连续多日未被分析，parquet 文件保留但日期停留在上次写入时。

## 过期场景

当前文件最早一批是 4 月 29 日创建的 — 这些股票的日线/30min 缓存已远超过 TTL（daily=24h, 30min=6h），但因为系统后续没有再调用它们的 `get_klines()`，文件依然存在，未被覆盖。

**结论**: 文件修改时间可以反映"这个标的最近一次被分析的时间"，而非"数据最后更新时间"。

## 5 层数据源链（重拉时）

缓存过期后，`get_klines()` 走完整 failover 链：

1. **Baostock** (主源, adjustflag='2' 前复权)
2. **efinance** (备选1)
3. **AkShare Sina** (备选2, 稳定绕过东方财富限流)
4. **AkShare EM** (备选3, 东方财富源)
5. **Agent MCP Fallback** (兜底, 写 `.need_mcp_fallback_*` 标记文件通知 Agent)

第 5 级通过文件 IPC 交互:
- 当 1-4 全部失败时，写入 `.need_mcp_fallback_{code}_{level}.marker` 标记文件
- Agent 扫描到标记文件 → 使用 investoday MCP 获取数据 → 放下 parquet 文件
- `check_agent_fallback()` 检测到 parquet 后读取并清除标记

## 沙箱共享分析

当 `chanlun_sandbox` 建立软链指向原系统 `data_cache/` 时：

| 方面 | 结论 |
|------|------|
| 数据污染 | 无风险 — 沙箱和原系统调同一条 `get_klines()` 代码路径，写入格式完全一致 |
| 数据预热 | 正面效果 — 沙箱回测覆盖更多标的，会顺手刷新原系统过期的缓存 |
| 并发写入 | 极端情况下两个进程同时写同一 parquet 可能冲突，但缠论回测是串行的，实际不会发生 |
| 建议 | **安全，直接用软链** |

## 与前后端接口

```python
# DataManager → DataFrame（API 获取或缓存读取）
df = dm.get_klines("688036", level="daily", start_date="2020-01-01")

# DataFrame → KLine 对象列表（ChaoLunAnalyzer 输入）
klines = dm.to_json_list(df)

# ChanLunAnalyzer 消费
analyzer = ChanLunAnalyzer(level="daily").analyze(klines)
```

## 与回测的关系

回测引擎 `backtest_engine.py` 使用 `Pre-load → Analyze` 模式，在 `run_single_analysis()` 中预先调用两次 `get_klines()`（daily + 30min），将 DataFrame 直接传给 `ChanLunAnalyzer`，不再二次调用 API。

注意: 回测如果覆盖较早区间（2016-2017），Baostock 30min 数据可能全量不可用，此时自动降级为日线独立模式。详见 SKILL.md "M30 Degraded Mode" 章节。

## 数据量不对称：515只日线 vs 48只30分钟

### 现象

| 级别 | Parquet 文件数 | SQLite 表行数 | 股票数 |
|------|:-------------:|:------------:|:-----:|
| daily | 515 | ~31万 | 515 |
| 30min | 48 | ~33万 | 48 |

### 根因：懒加载 + 不同的触发机制

`get_klines(code, level)` 是**懒加载** —— 只有当某个程序显式调用它时，才会去拉对应股票+级别的数据并缓存。

**日线 515只的来源**：
- `pool_screener.py` 跑完整 A500 选股 → 遍历整个大股票池 → 每只调一次 `get_klines(code, 'daily')`
- `full_rescore.py` / `check_negative_news.py` 的大批量扫描也会触发日线拉取
- **批量触发**，一次跑完全池，日线数据就累到 515 只

**30分钟 48只的来源**：
- 触发点在 `generate_analysis.py:1387`：
  ```python
  m30_data = self.dm.get_klines(symbol, level='30min', start_date=start_date, end_date=end_date)
  ```
  这行代码只在 `run_full_analysis()` 中被调用 —— 也就是**对某只股票做完整缠论多级别分析时才会触发**。
- **逐只触发**，不是批量跑
- 只有实际被 `multi_stock_scanner.py` 或 `report_generator.py` 跑过完整分析的那 48 只，才留下了 30 分钟数据
- 30 分钟 API（Baostock `frequency='30'` / AKShare minute）比日线更不稳定，部分股票可能返回空数据

### 这只 48 只是什么

```
000001 000002 000333 000651 000830 000858 000938 002027
002271 002415 002475 002594 002714 300015 300059 300274
300308 300502 300750 300772 300783 600030 600036 600089
600150 600298 600309 600346 600486 600519 600600 600872
600887 600900 601155 601166 601318 601398 601601 601615
601877 601888 601899 688036 688111 688169 688256 688981
```

包含：
- A500 核心股 + 持仓股（002415/600298/600872/600887/601155/688036）
- TIER1 池（600519/300750/601318 等）
- 部分用户手动添加的自选股（300783 三只松鼠、600486 扬农化工等有个性逻辑的票）

### 如何扩充 30 分钟数据到更多股票

方法一（手动逐只）：
```bash
# 对某只股票跑完整缠论分析 → 自动触发 30 分钟数据拉取 + SQLite 写入
cd ~/work/chanlun_core && python3 quick_chanlun.py 000XXX
```

方法二（批量脚本）：
```python
from data_manager import DataManager
dm = DataManager()
codes = ['000001', '000002', ...]  # 想补的股票列表
for code in codes:
    df = dm.get_klines(code, level='30min')
    print(f"{code}: {'OK' if not df.empty else 'FAIL'}, {len(df)} rows")
```
注意：每只股票需要两次 API 调用（Baostock + 可能的 fallback），批量跑几百只需要几分钟。Baostock 有连接稳定性问题，建议每 20 只暂停 5 秒。