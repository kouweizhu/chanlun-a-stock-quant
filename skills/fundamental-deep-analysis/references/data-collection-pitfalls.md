# 数据采集常见坑点

本文件记录基本面深度分析中数据采集环节的已知陷阱和修复方案。

---

## 坑1：报告期硬编码上限，遗漏最新季度数据 ⭐ 最重要

### 问题
```python
# ❌ 错误：硬编码 target 列表，上限写到去年年报
targets = ['2023-12-31', '2024-12-31', '2025-03-31', '2025-06-30', '2025-09-30', '2025-12-31']
```

当最新季度（如2026Q1）已披露，但 `targets` 中没有包含，数据就漏掉了。AKShare 实际返回了完整数据（34行），但查询时被硬编码的日期上限截断。

### 正确做法
```python
# ✅ 第一步：取全部数据，确认最新可用报告期
df = ak.stock_financial_abstract_ths(symbol=code, indicator='按报告期')
latest_date = df['报告期'].iloc[-1]  # AKShare 按报告期升序，最后即最新
print(f"最新可用报告期: {latest_date}")

# ✅ 第二步：构建 targets 时动态包含最新
base_targets = ['2023-12-31', '2024-12-31', '2025-03-31', '2025-06-30', '2025-09-30', '2025-12-31']
if latest_date not in base_targets:
    base_targets.append(latest_date)  # 追加最新
targets = sorted(base_targets)
```

### 经验教训
- **永远不要假设"最新就是上一期年报"**。A股季报披露节奏：Q1(4月底前)、半年报(8月底前)、Q3(10月底前)、年报(次年4月底前)。当前日期在4月底之后时，Q1数据大概率已可获取。
- **数据源的 `rows` 计数是重要信号**。本次 AKShare 返回 34 行但只取了 6 个日期的数据——如果打印了行数但没有充分利用，应该警觉。
- **报告顶部标注"最新财报数据截止日期"**，让用户一眼看出数据时效性。

---

## 坑2：`python -c "..."` 内联模式引号冲突

### 问题
```bash
# ❌ Python 单引号字符串内的单引号与 shell 冲突
python -c "import json; print(json.dumps({'key': 'value'}))"
```
报错：`SyntaxError: invalid syntax`

Python 字符串内使用了与 shell 引号相同的引号类型时，bash 会提前截断。

### 正确做法
```bash
# ✅ 方案A：写入临时文件再运行
write_file("/tmp/script.py", content)
terminal("python /tmp/script.py")
```

不要使用 `python -c "..."` 内联模式进行任何复杂查询。

---

## 坑3：Baostock `ResultData` 没有 `data_count` 属性

### 问题
```python
r = bs.query_stock_basic(code='sh.688036')
if r.data_count > 0:  # ❌ AttributeError: 'ResultData' object has no attribute 'data_count'
```

Baostock 新版本中 `query_*()` 返回的 `ResultData` 对象没有 `data_count` 属性。

### 正确做法
```python
r = bs.query_stock_basic(code='sh.688036')
rows = []
while r.next():  # ✅ 直接 while r.next() 遍历
    rows.append(r.get_row_data())
if rows:
    print(rows[0])
```

---

## 坑4：`execute_code` 内的 `terminal()` 也受引号嵌套限制

### 问题
在 `execute_code` 中调用 `terminal("python -c '...'")` 时，Python 代码内的引号仍需与 shell 层协调，`execute_code` 并不会自动转义。

### 正确做法
即便是 `execute_code` 内部，也不要用 `python -c`。用 `write_file` + `terminal("python /tmp/script.py")` 模式。

---

## 坑5：Baostock `query_history_k_data_plus` 的代码前缀要求

### 问题
- 上海交易所：`sh.688036`
- 深圳交易所：`sz.000001`
- **必须**带 `sh.` 或 `sz.` 前缀
- 科创板用 `sh.`（688开头的代码也是上海交易所）

### 正确做法
```python
if code.startswith(('6', '68')):  # 上海
    bs_code = f'sh.{code}'
elif code.startswith(('0', '3', '00')):  # 深圳
    bs_code = f'sz.{code}'
```

---

## 坑7：Baostock `query_stock_basic` get_row_data() 返回列表长度不可靠

### 问题
```python
rs = bs.query_stock_basic(code='sz.002415')
while rs.next():
    data = rs.get_row_data()
    name = data[1]  # ❌ IndexError: 返回列表可能少于预期列数
```

Baostock `query_stock_basic()` 返回的 `get_row_data()` 列表元素数量取决于接口版本。老版本返回7列（含代码、名称、上市日期、退市日期、类型、状态、额外），新版本可能只返回6列。硬编码索引（如 `data[1]`）在列数变化时静默崩溃。

### 正确做法
```python
rs = bs.query_stock_basic(code='sz.002415')
rows = []
while rs.next():
    data = rs.get_row_data()
    # ✅ 防御性访问：用 if len(data) > N 检查边界
    row = {
        'code': data[0] if len(data) > 0 else '',
        'name': data[1] if len(data) > 1 else '',
        'ipoDate': data[2] if len(data) > 2 else '',
        'outDate': data[3] if len(data) > 3 else '',
        'type': data[4] if len(data) > 4 else '',
        'status': data[5] if len(data) > 5 else '',
        'extra': data[6] if len(data) > 6 else '',
    }
    rows.append(row)
```
**永远对 `get_row_data()` 的索引做防御性访问。**

---

## 坑8b：Baostock `get_data()` 返回 DataFrame 而非 numpy 数组（2026-06-12 实测，002271 东方雨虹）

### 问题
```python
# ❌ 按旧文档假设 get_data() 返回 numpy structured array
rs = bs.query_stock_basic(code='sz.002271')
while rs.next():
    d = rs.get_data()
    for name in d.dtype.names:  # ❌ AttributeError: 'DataFrame' object has no attribute 'dtype'
        print(d[name])
```

Baostock 当前版本（2026-06 实测）中 `query_stock_basic()` 和 `query_history_k_data_plus()` 的 `get_data()` 返回的是 **pandas DataFrame**（单行），不是 numpy structured array。

### 诊断方法
```python
rs = bs.query_stock_basic(code='sz.002271')
while rs.next():
    d = rs.get_data()
    print(type(d))            # <class 'pandas.DataFrame'>
    print(d.columns)          # Index(['code', 'code_name', 'ipoDate', ...])
    print(d['code'].iloc[0])  # ✅ 用列名 + iloc 访问
    break
```

### 正确做法
```python
# query_stock_basic: get_data() 返回单行 DataFrame
rs = bs.query_stock_basic(code='sz.002271')
while rs.next():
    d = rs.get_data()
    code = d['code'].iloc[0]
    name = d['code_name'].iloc[0]
    ipo_date = d['ipoDate'].iloc[0]
    break

# query_history_k_data_plus: 同理，每次 get_data() 返回单行 DataFrame
# 必须用 d['列名'].iloc[0] 访问，不能用 d['列名']（返回的是 Series 不是标量）
krs = bs.query_history_k_data_plus(
    'sz.002271', 'date,close,peTTM,pbMRQ',
    start_date='2019-01-01', end_date='2026-06-12',
    frequency='d', adjustflag='2'
)
dates, pes = [], []
while krs.next():
    d = krs.get_data()
    dates.append(d['date'].iloc[0])
    try:
        v = float(d['peTTM'].iloc[0])
        if v > 0:
            pes.append(v)
    except (ValueError, TypeError):
        pass
```

### 注意
- `get_row_data()` 返回的是 list（旧接口），`get_data()` 返回的是 DataFrame（当前接口）
- 两者可能共存于不同 Baostock 版本，**优先用 `get_data()` + DataFrame 列名访问**
- 列名可通过 `d.columns` 查看
- `query_stock_basic` 列名（2026-06-12 实测）：`['code', 'code_name', 'ipoDate', 'outDate', 'type', 'status']`
  - 注意是 `code_name` 不是 `name`，没有 `industry`、`area` 等字段，只有 6 列
- `query_history_k_data_plus` 列名：`['date', 'close', 'peTTM', 'pbMRQ']`（取决于请求的 fields）

### 坑8c：Baostock `query_history_k_data_plus` 有时只返回1条数据（2026-06-12 实测）

#### 问题
```python
krs = bs.query_history_k_data_plus(
    'sz.002271', 'date,close,peTTM,pbMRQ',
    start_date='2019-01-01', end_date='2026-06-12',
    frequency='d', adjustflag='2'
)
# 实测只返回 1 条记录（2019-01-02），而非预期的 ~1800 条
```

Baostock 的 K 线接口在部分股票/时间段下可能只返回极少数据，原因不明（可能是权限限制或数据源问题）。

#### 正确做法
- **不要依赖 Baostock K 线数据作为唯一估值数据源**
- 用 AKShare `stock_zh_a_spot_em()` 获取实时 PE/PB 作为替代
- 用 AKShare `stock_zh_a_hist()` 获取历史 K 线数据（更可靠）
- 如果 Baostock 返回数据不足，在报告中标注"估值数据源受限"

---

## 坑8：Baostock K线数据中的空值导致浮点解析崩溃

### 问题
```python
for k in krows:
    if k['peTTM'] and float(k['peTTM']) > 0:  # ❌ ''无法转float
```

Baostock `query_history_k_data_plus()` 在停牌日或不满足计算条件时返回空字符串 `''` 而非 `NaN`。直接 `float('')` 会抛出 `ValueError`。

### 正确做法
```python
for k in krows:
    try:
        if k['peTTM'] and k['peTTM'].strip():  # ✅ 先strip去空白
            pe_val = float(k['peTTM'])
            if pe_val > 0:
                pe_vals.append(pe_val)
    except (ValueError, TypeError):
        pass  # 跳过异常值
```

---

## 坑9：搜索工具链降级路径缺失

### 问题
定性信息搜索（研报、护城河、管理层评估）依赖外部搜索工具，但各工具均有失效场景：
- **Tavily**：有月度调用限额，超限返回 `{"error":"This request exceeds your plan's set usage limit"}`
- **DuckDuckGo**：国内网络环境可能被墙，返回 `"No results were found...bot detection"`
- **Bing中文**：对研报类信息覆盖不足，常返回官网/百科而非券商研报

若未准备降级路径，定性信息收集环节将完全失败。

### 正确做法（降级链）
```
首选：Tavily（质量最高，有结构化输出）
    ↓ 超限
备选1：DuckDuckGo（免费，无额度限制）
    ↓ 被墙/无结果
备选2：Bing中文搜索（mcp_bing_cn_bing_search）
    ↓ 研报覆盖不足；城市名股票完全失效（见坑10）
备选3：直接 web_extract 抓取东方财富个股页面
    ↓ 页面结构变化
备选4：同花顺问财（iwencai）—— 若本地OpenClaw服务可用
    ↓ 服务未启动
备选4：基于结构化数据（财务+估值）独立完成分析，在报告中明确标注"定性信息未获取"
```

### 代码模板
```python
# 尝试链
search_results = None
for fn, name in [(tavily_search, "Tavily"), (ddg_search, "DDG"), (bing_search, "Bing")]:
    try:
        result = fn(query)
        if result and not result.get('error'):
            search_results = result
            break
    except Exception as e:
        continue

if not search_results:
    # 标记为未获取，继续执行
    print("⚠️ 定性信息搜索未获取有效结果，将基于结构化数据独立分析")
```

### 报告标注规范
当定性信息（研报观点、护城河深度、管理层评估）未获取时，必须在报告"置信度"章节明确标注：
> **置信度：中** — 财务数据完整（来自AKShare结构化数据），但缺乏最新券商研报观点交叉验证（搜索未获取到有效研报），估值判断基于历史数据和行业对标。

---

## 坑10：Bing中文搜索对"城市名股票"完全失效 ⭐ 2026-05-30 实测确认

### 问题
当股票名称包含城市名时（如"唐山港"、"连云港"、"日照港"、"宁波港"、"上海机场"等），Bing中文搜索会优先匹配城市级别的旅游、百科、政府网站信息，完全忽略公司/股票语义。

**实测案例**：搜索 "唐山港 竞争优势 护城河 港口业务"，返回结果全部是：
- 唐山市百度百科
- 唐山市旅游攻略
- 唐山市行政区划
- QQ邮箱（?!）

加入股票代码也无效：搜索 "601000 唐山港股份 港口 吞吐量" 同样返回无关结果。

### 受影响的股票特征
- 名称包含地级市及以上城市名：唐山港、宁波港、日照港、连云港、上海机场、深圳机场等
- 名称与城市/省份同名或高度相似

### 正确做法
对城市名股票，搜索策略需调整：

1. **搜索关键词去城市名**：用 "601000 港口 业务分析" 替代 "唐山港 竞争优势"
2. **直接抓取财经网站**：跳过搜索引擎，直接用 `web_extract` 抓取东方财富/新浪财经的个股页面
3. **优先用结构化数据**：AKShare + Baostock 足以完成80%的分析，定性信息可标注"未获取"
4. **东方财富个股页面URL模板**：
   - 公司概况：`https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/Index?type=web&code=sh{code}`
   - 财务分析：`https://emweb.securities.eastmoney.com/PC_HSF10/FinanceAnalysis/Index?type=web&code=sh{code}`

### 降级策略（更新）
```
首选：Tavily（质量最高，有结构化输出）
    ↓ 超限
备选1：DuckDuckGo（免费，无额度限制）
    ↓ 被墙/无结果
备选2：Bing中文搜索（mcp_bing_cn_bing_search）
    ↓ 研报覆盖不足 / 城市名股票完全失效
备选3：直接 web_extract 抓取东方财富个股页面
    ↓ 页面结构变化
备选4：同花顺问财（iwencai）—— 若本地OpenClaw服务可用
    ↓ 服务未启动
备选5：基于结构化数据（财务+估值）独立完成分析，在报告中标注"定性信息未获取"
```

**用途**：计算当前估值在历史区间中的位置（min/p25/median/p75/max），为"估值评分"提供量化依据。

### 代码模板
```python
import baostock as bs

bs.login()
rs = bs.query_history_k_data_plus(
    'sh.600886', 'date,close,peTTM,pbMRQ',
    start_date='2019-01-01', end_date='2026-05-30',
    frequency='d', adjustflag='2'
)

pe_vals, pb_vals = [], []
while rs.next():
    row = rs.get_row_data()
    try:
        if row[2] and row[2].strip():
            pe = float(row[2])
            if pe > 0:
                pe_vals.append(pe)
        if row[3] and row[3].strip():
            pb = float(row[3])
            if pb > 0:
                pb_vals.append(pb)
    except:
        pass

bs.logout()

# 计算分位
pe_sorted = sorted(pe_vals)
n = len(pe_sorted)
stats = {
    'min': pe_sorted[0],
    'p25': pe_sorted[int(n*0.25)],
    'median': pe_sorted[int(n*0.5)],
    'p75': pe_sorted[int(n*0.75)],
    'max': pe_sorted[-1],
    'latest': pe_vals[-1] if pe_vals else None
}

# 当前分位
current_pe = stats['latest']
percentile = sum(1 for v in pe_vals if v < current_pe) / len(pe_vals) * 100
print(f"PE当前处于历史 {percentile:.1f}% 分位")
```

### 输出示例
| 指标 | 当前值 | 历史分位 | 评估 |
|------|--------|---------|------|
| PE-TTM | 15.4x | 34.2% | 中等偏低 |
| PB | 1.66x | 40.1% | 中等 |

**解读**：PE处于历史34%分位 → 比历史上66%的时间便宜，但未必"便宜 enough"（需结合PEG、行业对标综合判断）。

## 坑6：AKShare 同花顺数据解析陷阱

| 陷阱 | 描述 | 修复 |
|------|------|------|
| 数据升序 | `stock_financial_abstract_ths` 按报告期升序排列 | 取最新用 `df.iloc[-1]` |
| 百分比字符串 | 返回 `'31.08%'` 而非 `0.3108` | 分析时直接用作标签即可，数值计算需 strip `%` |
| 中文数值 | 营收返回 `'687.15亿'` | strip `亿` 再乘系数 |
| 同比比较 | 必须找去年同期（如 2026Q1 vs 2025Q1） | 不能直接用 `iloc[-2]`（可能是上一季度） |
| **默认升序** | `stock_financial_abstract_ths` 返回的 DataFrame 默认按报告期**升序**排列（2007→最新），`head(10)` 拿到的是最老的10条 | 取最新数据前必须 `df = df.sort_values('报告期', ascending=False)` |

---

## 📐 技巧1：PE/PB 分年统计法（估值趋势分析）

**用途**：单看 PE/PB 的5年汇总统计（中位数/分位）会掩盖**估值中枢的漂移**。例如海康威视在2021年AI概念高峰期 PE 中位数达38x，而2024-2026年 PE 中枢已稳定在21x附近。只看"5年分位"会误以为"处于低位"，但实际上估值中枢已经系统性下移。

### 代码模板
```python
from collections import defaultdict

by_year = defaultdict(list)
for k in krows:
    try:
        if k['pe'] and float(k['pe']) > 0:
            yr = k['date'][:4]
            by_year[yr].append(float(k['pe']))
    except:
        pass

print("=== 分年PE中位数 ===")
for yr in sorted(by_year.keys()):
    vals = sorted(by_year[yr])
    print(f"  {yr}: 中位数={vals[len(vals)//2]:.1f} | 范围=[{min(vals):.1f}, {max(vals):.1f}]")
```

### 解读指南
| 发现 | 含义 |
|------|------|
| PE中枢逐年下降 | 市场对公司的**增长预期已重新定价**→需检查ROE是否同步下降 |
| 年度波动率收敛 | 市场分歧减小→公司进入"确定性"阶段，估值更稳定 |
| PE中枢 vs ROE趋势 | ROE下降但PE不变→估值泡沫；ROE企稳但PE下降→过度悲观 → 可能是机会 |
