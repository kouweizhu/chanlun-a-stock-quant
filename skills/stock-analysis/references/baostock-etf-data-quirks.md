# Baostock ETF数据前缀 & MACD背驰计算修正

## 问题：ETF代码前缀导致DataManager全部失败

### 症状

`single_stock_analysis.py` 或 `DataManager.get_klines()` 对ETF代码返回空结果：
```
[DataManager] Trying Baostock (daily)...
[DataManager] Baostock: Daily data with adjustflag='2'
[DataManager] Trying efinance (daily)...
[DataManager] efinance fail: ...
[DataManager] Trying AkShare Sina (daily)...
[DataManager] Trying AkShare EM (daily)...
[DataManager] 已写入 .source_failed_{code}_daily.flag
[DataManager] 所有数据源均失败
```

### 根因

DataManager 在调用 Baostock 时使用默认的代码前缀映射规则（`sz.` for 深市/创业板, `sh.` for 沪市主板），但这个映射对 ETF 不生效。Baostock 对沪市 ETF 要求 `sh.{code}` 格式，而 DataManager 传入了错误的格式（如 `sz.513330` 或裸 `513330`），导致 Baostock query_history_k_data_plus 返回 0 行。后续 efinance/AkShare 因前置连接失败也会联锁失败。

### 修复方法

#### 永久修复（2026-06-01）
```python
# data_manager.py L236（已应用）：
bs_symbol = f"sh.{symbol}" if (symbol.startswith('6') or symbol.startswith('5')) else f"sz.{symbol}"

# fund_backtest.py L97（已应用）：
bs_code = f"sh.{symbol}" if symbol.startswith('6') or symbol.startswith('5') else f"sz.{symbol}"
```

核心修改：原逻辑只把 `6xxxxx`（沪市主板股票）映射到 `sh.` 前缀。沪市 ETF 代码以 `5` 开头，漏掉了。增加 `startswith('5')` 后，51xxxx沪市ETF正确映射。

如果遇到新ETF代码仍然失败，检查 Baostock 前缀：

```bash
cd ~/work/chanlun_core
python -c "
import baostock_utils, baostock as bs
baostock_utils.ensure_login()
for prefix in ['sh.', 'sz.']:
    rs = bs.query_history_k_data_plus(prefix + '{code}',
        'date,open,high,low,close,volume',
        start_date='2024-01-01', end_date='2026-06-01',
        frequency='d', adjustflag='2')
    rows = [rs.get_row_data() for _ in iter(lambda: (rs.next(),) if rs.next() else '', None)]
    print(f'{prefix}{{code}}: {len(rows)} rows')
"
```

#### 注意：Baostock ETF 数据覆盖不全

即使代码前缀正确，Baostock 对部分 ETF 仍可能**只有近期数据**。2026-06-01 实测：

| ETF代码 | 上市日期 | Baostock可用数据 | 行数 |
|---------|:---------:|:----------------:|:----:|
| 513330 恒生互联网ETF | 2021-02-08 | 2026-01-05 起 | ~105行 |
| 用户全量CSV对比 | — | 2024-01-02 起 | **~590行 (2.5年)** |

Baostock 数据库对该ETF仅存储了2026年起的日线数据（无论前复权/后复权/不复权均如此），2021~2025年数据全部返回空。这不是代码问题，是数据源本身的覆盖缺口。

**影响严重性（2026-06-15 实测）**：513330 案例对比：

| 数据源 | 行数 | 覆盖期 | 笔数 | 中枢 | 买点 | 趋势背驰判断 |
|:------|:----:|:------:|:----:|:----:|:----:|:-----------:|
| Baostock | 105 | 6个月(2026-01~06) | 9 | 3 | 0买点 | 无(面积比115%) |
| 全量CSV | 590 | 2.5年(2024-01~06) | 28 | 8 | **3个三买** | **确认(面积比54.4%)** |

数据长度直接决定分析结论。Baostock 6个月数据只有3个中枢，无法识别2024年底部平台和2025年的完整上涨周期，背驰判断完全错误。**ETF分析必须优先确认数据覆盖≥2年。若Baostock不足，立即向用户索要CSV完整数据。**

#### 应对方案A：parquet缓存注入法（推荐，2026-06-15新增）

核心思路：将CSV数据直接写入 `data_cache/{代码}_daily.parquet`，替代Baostock缓存，然后复用完整管道（quick_chanlun.py + quick_html.py）。无需创建临时分析脚本。

```bash
cd ~/work/chanlun_core

# 1. 先验证CSV列数/格式
head -1 D:/.../xxx.csv
# 期望: date,open,close,high,low,volume,amount,turnover

# 2. 创建一次性导入脚本
cat > import_csv_temp.py << 'PYEOF'
import pandas as pd, os
csv_path = "D:/常用文件/analysis_reports/恒生互联网ETF/513330_kline_2024_20260612.csv"
df = pd.read_csv(csv_path)

# 标准化至parquet缓存格式（注意列序重排）
df = df[['date', 'open', 'high', 'low', 'close', 'volume']]
df['date'] = df['date'].astype(str)
for col in ['open','high','low','close']:
    df[col] = df[col].astype(float)
df['volume'] = df['volume'].astype(int)

cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
    "data_cache", f"{{代码}}_daily.parquet")
df.to_parquet(cache_path, index=False)
print(f"✅ {len(df)}行: {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")
PYEOF
python import_csv_temp.py && rm import_csv_temp.py

# 3. 直接复用完整管道（此时DataManager会Cache HIT）
python quick_chanlun.py {代码}     # 验买卖点/中枢/背驰
python quick_html.py {代码}        # 生成HTML

# 4. 同步HTML到Windows
mkdir -p "D:/常用文件/analysis_reports/{股票名}/"
cp reports_html/{代码}_chanlun.html "D:/常用文件/analysis_reports/{股票名}/{代码}_chanlun_full.html"
```

**关键细节**：
- CSV列序是 `date,open,close,high,low,volume,...` 而 parquet 是 `date,open,high,low,close,volume`，需重排
- CSV是不复权实际价，parquet是前复权。缠论看形态不看绝对值，差异可接受
- 分析完成后清理临时脚本，parquet缓存保留供后续使用
- **备份原parquet**：CSV替换后原Baostock缓存丢失，建议先 `cp data_cache/{代码}_daily.parquet data_cache/{代码}_daily.parquet.bak`

#### 应对方案B：直接调用 run_analysis()（旧方案，备用）

当 parquet 注入不适用时（如 CSV 列格式特殊或需要保留原始 Baostock 缓存），走传统路径：

1. 告知用户从 Windows 端东方财富/同花顺客户端，导出完整K线CSV到 `D:\\\\常用文件\\\\analysis_reports\\\\{股票名}\\\\`
2. WSL路径为 `D:/常用文件/analysis_reports/{股票名}/{代码}_kline_{起始日期}_{结束日期}.csv`
3. CSV格式要求：`date,open,high,low,close,volume`（日期格式 `YYYY-MM-DD`），可用 `encoding='utf-8-sig'`
4. 创建临时分析脚本从CSV读取并调用 `run_analysis(kline_df)`：
   ```python
   import pandas as pd
   from data_manager import DataManager  # 仅用配置
   from generate_analysis import run_analysis, analyze_macd
   
   csv_path = 'D:/常用文件/analysis_reports/{股票名}/{代码}_kline_{起始}_{结束}.csv'
   kline = pd.read_csv(csv_path, encoding='utf-8-sig')
   kline['date'] = pd.to_datetime(kline['date'])
   kline.sort_values('date', inplace=True)
   kline.reset_index(drop=True, inplace=True)
   
   result = run_analysis(kline)
   print(f"笔数: {len(result['bi_list'])}")
   print(f"中枢数: {len(result['zhongshu_list'])}")
   ```
5. **注意**：需在 `chanlun_core` 项目根目录执行。分析完成后清理临时脚本。
6. 完成后用 `quick_html.py` 从缓存数据（已写入 `data_cache/`）生成可视化HTML，无需重跑。
7. 更新 stock_db 时备注「用户CSV数据源」。
8. **陷阱**：`run_analysis()` 中的 `_find_zhongshus()` 有中枢扩展算法bug（离开笔被错误扩展原中枢，导致漏掉新中枢），分析结果若中枢数偏少需手动检查。详见 `chanlun-quant-system` 技能 `references/zhongshu-extension-bug-2026-06-01.md`。

### 已知ETF的Baostock前缀

| ETF代码 | 名称 | Baostock前缀 |
|--------|------|:------------:|
| 513330 | 恒生互联网ETF | `sh.513330` |
| 513050 | 中概互联ETF | `sz.513050` (需验证) |
| 510050 | 上证50ETF | `sh.510050` (需验证) |
| 159915 | 创业板ETF | `sz.159915` (需验证) |
| 510300 | 沪深300ETF | `sh.510300` (需验证) |
| 518880 | 黄金ETF | `sh.518880` (需验证) |

> 规律：沪市ETF代码以 51 开头 → `sh.` 前缀；深市ETF代码以 159 开头 → `sz.` 前缀。但部分跨境ETF(QDII)可能有特例，需逐个验证。

---

## MACD底背驰面积计算修正

### 问题

手动计算MACD底背驰时，直接比较两段向下笔的MACD柱原始代数和（sum），当两段均为负值时，比值逻辑容易出错。

**错误做法**：
```python
# ❌ 两段均为负值时，area2=-0.1477, area8=-0.0222
# area8/area2 = 0.15，误判为"无背驰"
ratio = area8 / area2
```

### 正确做法

使用**绝对值求和**（衡量下跌总动能），绿柱代数和（衡量空方力度），或同时检查 DIF 是否创新低：

```python
# ✅ 方法1：绝对值求和（推荐，简便可靠）
area2_abs = m2['macd_bar'].abs().sum()   # 下跌笔2的总动能
area8_abs = m8['macd_bar'].abs().sum()   # 下跌笔8的总动能
ratio = area8_abs / area2_abs
# ratio < 0.7 → 背驰确认（下跌动能大幅减弱）

# ✅ 方法2：仅绿柱代数和（更精准）
area2_neg = m2[m2['macd_bar'] < 0]['macd_bar'].sum()
area8_neg = m8[m8['macd_bar'] < 0]['macd_bar'].sum()
# area8_neg > area2_neg（负值更接近0）→ 背驰

# ✅ 方法3：DIF低点对比（辅助验证）
# 价格新低但DIF不创新低 = 底背驰
dif_low_2 = m2['dif'].min()
dif_low_8 = m8['dif'].min()
price_low_2 = m2['close'].min()
price_low_8 = m8['close'].min()
# price_low_8 < price_low_2 且 dif_low_8 > dif_low_2 → 底背驰确认
```

### 背驰三条件（标件）

| 条件 | 判定 | 说明 |
|:----|:-----|:------|
| 价格新低 | 最新下跌段低点 < 前一段低点 | 价格在下跌 |
| DIF不创新低 | 最新DIF低点 > 前一段DIF低点 | 动量背离 |
| MACD柱面积缩小 | 绝对值 < 前一段×70% | 力度衰减 |

三个条件同时满足 = 底背驰确认。

### 实战案例：513330(恒生互联网ETF) 2026-05

| 指标 | 笔2 (01-29~03-04) | 笔8 (05-14~05-28) |
|:-----|:------------------:|:------------------:|
| 价格区间 | 0.560→0.434 | 0.452→0.388 |
| 最低价 | 0.437 | **0.393（新低）** |
| DIF最低 | -0.022950 | **-0.008452（抬高）** |
| MACD柱绝对值总面积 | 0.1495 | **0.0331（缩小77.8%）** |
| 背驰判定 | — | **✅ 三个条件全满足** |

结论：即使Baostock数据仅96根K线，三条件背驰清晰可辨。说明数据长度不足时背驰检测仍然有效，但笔数偏少会让整体结构判断可靠性降低。