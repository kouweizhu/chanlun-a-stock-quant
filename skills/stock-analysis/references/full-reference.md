# A股三维分析 — 完整参考手册

> 本文件是 `stock-analysis` SKILL.md 的配套参考手册，包含代码模板、历史记录、故障排查等详细内容。仅在需要边缘情况处理时按需加载（`skill_view(name='stock-analysis', file_path='references/full-reference.md')`）。

---

## 目录

1. [30分钟补充分析（完整代码模板）](#1-30分钟补充分析完整代码模板)
2. [段级别信号分析（完整代码模板）](#2-段级别信号分析完整代码模板)
3. [EastMoney Q1修正（回退方案）](#3-eastmoney-q1修正回退方案)
4. [资金面数据](#4-资金面数据)
5. [东方财富定性分析](#5-东方财富定性分析)
6. [公告预扫描（应急后备）](#6-公告预扫描应急后备)
7. [季报点评模板](#7-季报点评模板)
8. [内存管理详细说明](#8-内存管理详细说明)
9. [路径清理与HTML显示调整](#9-路径清理与html显示调整)
10. [历史版本变更记录](#10-历史版本变更记录)

---

## 1. 30分钟补充分析（完整代码模板）

当用户明确说"看一下30分钟K线"或"30分钟是否有买点"时，在完成日线评分后追加此步骤。**不参与评分公式**，仅用于报告描述。

```python
import sys
sys.path.insert(0, 'D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core')
from data_manager import DataManager
dm = DataManager()
bars_30 = dm.get_klines('600298', level='30min')
df_30 = bars_30.sort_values('date')

# 计算30分钟MACD（12/26/9标准参数）
closes_30 = df_30['close']
ema12_30 = closes_30.ewm(span=12).mean()
ema26_30 = closes_30.ewm(span=26).mean()
dif_30 = ema12_30 - ema26_30
dea_30 = dif_30.ewm(span=9).mean()
macd_30 = 2 * (dif_30 - dea_30)
df_30['dif'] = dif_30
df_30['dea'] = dea_30
df_30['macd'] = macd_30

# ① MACD金叉/死叉检测
latest_30 = df_30.iloc[-1]
prev_30 = df_30.iloc[-2]
if prev_30['dif'] < prev_30['dea'] and latest_30['dif'] >= latest_30['dea']:
    signal = "✅ 30分钟MACD金叉！短线反弹动能转正"
elif prev_30['dif'] > prev_30['dea'] and latest_30['dif'] <= latest_30['dea']:
    signal = "❌ 30分钟MACD死叉！短线动能转空"
else:
    signal = "⚪ MACD状态稳定: " + ("多头" if latest_30['dif'] > latest_30['dea'] else "空头")

# ② 底分型/顶分型检测（最近3根K线）
for i in [len(df_30)-2]:
    k1, k2, k3 = df_30.iloc[i-1], df_30.iloc[i], df_30.iloc[i+1]
    if k1['low'] > k2['low'] and k3['low'] > k2['low']:
        print(f"🔵 底分型: {k2['date']} L={k2['low']:.2f}")
    if k1['high'] < k2['high'] and k3['high'] < k2['high']:
        print(f"🔴 顶分型: {k2['date']} H={k2['high']:.2f}")

# ③ 近期支撑/阻力（最近40根K线）
recent_40 = df_30.tail(40)
min_low = recent_40['low'].min()
max_high = recent_40['high'].max()
print(f"近期支撑: {min_low:.2f}")
print(f"近期阻力: {max_high:.2f}")
```

### 多级别矛盾判断

| 日线状态 | 30分钟状态 | 判断 |
|:--------|:----------|:-----|
| 日线向下/三卖 | 30分钟金叉+反弹 | ⚠️ 小级别反弹，非反转 |
| 日线向上/买点 | 30分钟死叉+回调 | ⚠️ 正常回调 |
| 日线向下/三卖 | 30分钟死叉+新低 | 🔴 共振下跌 |
| 日线向上/买点 | 30分钟金叉+突破 | 🟢 共振上涨 |

---

## 2. 段级别信号分析（完整代码模板）

当用户问到SB1/SB2/段中枢时执行。

### 2.1 运行段中枢测试脚本

```bash
cd D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core
python test_segment_zhongshu.py {代码} 1200
```

### 2.2 SB1（段级别一买）三因子可信度检查

```python
import sys
sys.path.insert(0, 'D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core')
from data_manager import DataManager
dm = DataManager()
bars = dm.get_klines('{代码}', level='daily')
df = bars.sort_values('date')

# MACD计算
ema12 = df['close'].ewm(span=12).mean()
ema26 = df['close'].ewm(span=26).mean()
dif = ema12 - ema26
dea = dif.ewm(span=9).mean()
macd_bar = 2 * (dif - dea)

# 对比最近两个下跌段的MACD面积
mask_seg22 = (df['date'] >= '段22_start') & (df['date'] <= '段22_end')
mask_seg20 = (df['date'] >= '段20_start') & (df['date'] <= '段20_end')
area_22 = macd_bar[mask_seg22].abs().sum()
area_20 = macd_bar[mask_seg20].abs().sum()
ratio = area_22 / area_20  # < 0.7 → 底背驰确认
```

| 因子 | 条件 | 达标 |
|:----|:-----|:----:|
| 价格新低 | 最新下跌段低点 < 前一个下跌段低点 | ✅/❌ |
| MACD底背驰 | MACD面积 < 前一段×70% | ✅ 面积<70% |
| 回归中枢 | 当前价 > 段中枢ZD | ✅ 已站上ZD |

### 2.3 段级别信号与笔级别评分关系

| 情况 | 处理 |
|:----|:-----|
| SB1三因子全部达标 | 技术分可+10~15作为段级别底背驰奖励 |
| SB1仅价格新低达标 | 不改分，标注"段级别假信号" |
| 段三卖+笔三卖共振 | 技术分-5~8 |

---

## 3. EastMoney Q1修正（回退方案）

仅当 `hithink_fundamental.py` 返回 confidence=1（同花顺API异常）时使用。

```python
import requests, json
headers = {'User-Agent': 'Mozilla/5.0'}
url = (
    'https://datacenter.eastmoney.com/securities/api/data/v1/get'
    '?reportName=RPT_LICO_FN_CPD'
    '&columns=ALL'
    f'&filter=(SECUCODE=%22{代码}.SH%22)'
    '&pageNumber=1&pageSize=5'
    '&sortTypes=-1&sortColumns=NOTICE_DATE'
)
r = requests.get(url, headers=headers, timeout=10)
data = r.json()
```

关键字段：`TOTAL_OPERATE_INCOME`（营收）、`PARENT_NETPROFIT`（归母净利）、`XSMLL`（毛利率）、`WEIGHTAVG_ROE`（ROE）、`YSTZ`（营收同比）、`SJLTZ`（净利同比）。

---

## 4. 资金面数据

使用同花顺 `hithink-market-query` 接口：
```bash
python -c "
from hithink_api import query2data
result = query2data('300015', indicators=['main_force_net_inflow', 'north_bound_net_inflow', 'ddx'])
print(result)
"
```

如果MCP不可用，跳过此步骤，标注"资金面数据暂缺"。

---

## 5. 东方财富定性分析

```bash
python C:/Users/13120/.agents/skills/mx-financial-assistant/scripts/generate_answer.py \
  --query "{股票名}({代码})的护城河分析、行业竞争格局和行业景气度"
```

输出 JSON 中的 `answer` 字段为 Markdown 格式定性分析，`references` 为溯源参考。耗时约3-5秒。

---

## 6. 公告预扫描（应急后备）

仅当同花顺API不可用时的应急方案：
```python
import requests
headers = {'User-Agent': 'Mozilla/5.0'}
r = requests.get(
    'https://np-anotice-stock.eastmoney.com/api/security/ann',
    params={'sr': -1, 'page_size': 20, 'page_index': 1,
            'ann_type': 'A', 'stock_list': '{代码}',
            'f_node': '0', 's_node': '0'},
    headers=headers, timeout=10)
anns = r.json().get('data', {}).get('list', [])
```

---

## 7. 季报点评模板

### 7指标评估表

```
### Q1 2026 季报点评

**核心判断一句话：** [营收加速/放缓？利润改善/恶化？格局是否改变？]

| 指标 | Q1 2026 | 同比/环比变化 | 评价 |
|:----|:--------:|:------------:|:-----|
| 营收 | XX亿 | +XX% | 🟢/🟡/🔴 |
| 归母净利 | XX亿 | +XX% | 🟢/🟡/🔴 |
| 扣非净利 | XX亿 | — | 🟢/🟡/🔴 |
| 毛利率 | XX% | ±Xpct | 🟢/🟡/🔴 |
| 净利率 | XX% | ±Xpct | 🟢/🟡/🔴 |
| 经营现金流/营收 | XX% | 正/负 | 🟢/🟡/🔴 |
| ROE(年化) | ≈X% | ±Xpct | 🟢/🟡/🔴 |
```

**点评要点**：营收增速走向、利润是否跟上、毛利率趋势、现金流质量、是否改变核心判断。

---

## 8. 内存管理详细说明

### SQLite 数据库

```bash
python ~/work/chanlun_core/stock_db.py init            # 初始化
python ~/work/chanlun_core/stock_db.py trend {代码}    # 趋势
python ~/work/chanlun_core/stock_db.py list            # 最近记录
```

### Hermes Memory 指针（仅保留一行，约80字）

```markdown
{股票名}({代码}) YYYY-MM-DD 分析: 综合XX,技术XX,基本面XX,消息面XX,概率X%,决策=XX。DB: stock_db trend {代码}
```

### Memory 溢出处理

当 memory 占用>85%（约1870/2200字）时，替换已有条目：
1. 优先替换最长的条目
2. 其次替换时间最久的（>7天）
3. 避免替换强力推荐股票（海康威视/潍柴动力等）

---

## 9. 路径清理与HTML显示调整

### HTML ~ 展开陷阱

```bash
# quick_html.py 输出可能因 ~ 展开到深层嵌套路径
# 复制到标准路径：
cp "/path/from/expanded/home/{代码}_chanlun.html" D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core/reports_html/
```

### HTML 显示范围

默认从2020-01-01开始显示。如需调整，修改 `generate_analysis.py` 中 `HTMLVisualizer` 的 `display_start_date` 参数。

---

## 10. 历史版本变更记录

### v5.0 (当前)
- SKILL.md 精简为紧凑版，详细内容移至 references/full-reference.md
- 新增 single_stock_analysis.py（合并5个子进程为1次执行）

### v4.2.3
- 报告强制加入最新季报数据列+季报点评章节
- 新增 references/quarterly-report-commentary-template.md

### v4.2.2
- 消息面数据改用同花顺API单票模式（news_detail_report.py --code --name）
- check_negative_news.py --json 支持任意代码
- 不再依赖Tavily/web搜索作为主消息源

### v4.2.1
- 修复中炬高新趋势修正-8分案例，新增 references/zhongju-gaoxin-trend-correction-case.md

### v4.2
- hithink_fundamental.py v2.0：新增扣非净利润、4年趋势分析、趋势修正评分
- 基本面评分不再需要子Agent参与

### v4.0
- 不再使用 delegate_task，所有分析由主Agent本地执行
- 新增东方财富定性分析、同花顺资金面

### v3.0
- 评分历史从 Hermes memory 迁移到 SQLite（stock_db.py）
- 技术面评分改为-30到100范围

---

> 参考文件列表（位于 stock-analysis/references/ 目录）：
> - cross-system-dependency-map.md — 跨系统依赖审计清单
> - segment-level-sb1-detection.md — 段级别买卖点检测
> - fundamental-trend-analysis.md — 趋势修正方法论
> - yi-mai-signal-handling.md — 一买信号处理
> - news-api-output-schema.md — 消息面API输出格式
> - zhongju-gaoxin-trend-correction-case.md — 中炬高新趋势修正案例
> - report-date-data-reliability.md — 报告期数据可靠性
> - hithink-finance-query-usage.md — 同花顺API用法
> - panbei-transsion-case-study-2026-05-01.md — 盘背传音案例
> - 30min-analysis-pattern.md — 30分钟分析模板
> - eastmoney-api-usage.md — 东方财富回退方案
> - yunda-contradiction-case-2026-05-14.md — 运达股份矛盾案例
> - quarterly-report-commentary-template.md — 季报点评模板
