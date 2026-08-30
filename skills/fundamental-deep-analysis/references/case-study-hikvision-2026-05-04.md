# 实战案例：海康威视(002415) 基本面深度分析

> 分析日期：2026-05-04
> 数据源：AKShare + Baostock + Tavily搜索
> 最新财报：2026-03-31（一季报）

---

## 案例价值

本次分析是 `fundamental-deep-analysis` 技能框架的完整演练。以下记录关键经验供后续复用。

---

## 环节0：数据采集实录

### Baostock 基本信息获取

```python
# 首次尝试 —— 失败
rs = bs.query_stock_basic(code='sz.002415')
rows = []
while rs.next():
    rows.append({
        'name': rs.get_row_data()[1],  # ❌ IndexError
    })

# 修复 —— 使用防御性索引
while rs.next():
    data = rs.get_row_data()
    rows.append({
        'code': data[0] if len(data) > 0 else '',
        'name': data[1] if len(data) > 1 else '',
        'ipoDate': data[2] if len(data) > 2 else '',
        'outDate': data[3] if len(data) > 3 else '',
        'type': data[4] if len(data) > 4 else '',
        'status': data[5] if len(data) > 5 else '',
    })
```
**教训**：`query_stock_basic().get_row_data()` 返回列表长度约6-7列，不同接口版本有差异。永远用 `if len(data) > N` 做防御性访问。

### AKShare 数据提取

AKShare `stock_financial_abstract_ths` 返回2006年至今全部数据（~70行），只需提取近3年关键报告期。最佳实践：

```python
target_dates = ['2023-03-31','2023-06-30','2023-09-30','2023-12-31',
                '2024-03-31','2024-06-30','2024-09-30','2024-12-31',
                '2025-03-31','2025-06-30','2025-09-30','2025-12-31',
                '2026-03-31']  # 2026Q1一定要包含！
```

### 搜索定性信息

5个并行的Tavily搜索主题（全部成功）：

| 搜索词 | 价值产出 |
|--------|---------|
| 竞争优势 护城河 市场份额 | 全球市占率37.9%连续14年第一、全栈自研芯片、观澜大模型 |
| 管理层 减持 分红 诚信记录 | 龚虹嘉累计减持超200亿、2019年信披违规立案、冯柳2026Q1减持5700万股 |
| 2025年报 2026一季报 业绩 | 营收925亿+0.01%、利润141.95亿+18.52%、Q1利润+36.42% |
| 商誉 收购 潜在风险 | 商誉规模可控、无重大在途收购 |
| 券商研报 评级 | 一致预期2026年净利润约160亿（EPS 1.75元） |

---

## 环节1-5：分析要点实录

### 行业分析评分8.0
- 核心数据：全球市占率37.9%（连续14年第一），是第二名大华的2倍+
- 关键在于**区分"安防"和"AIoT"**：传统安防存量市场~3000亿，AIoT增量市场万亿级
- 护城河五层评估：技术(⭐⭐⭐⭐⭐) + 规模(⭐⭐⭐⭐⭐) + 转换成本(⭐⭐⭐⭐⭐) + 生态(⭐⭐⭐⭐) + 品牌(⭐⭐⭐⭐)

### 财务分析核心发现
- **非经常性损益剪刀差检查**：净利润vs扣非净利润的gap<3% → 利润增长100%由主营驱动，满分通过
- **毛利率趋势**是核心先行指标：43.83%(2024)→45.88%(2025)→49.09%(2026Q1)，连续提升
- 注意Q1经营现金流为负(-22亿)是季节性因素（Q1回款少+备货），需等Q2验证
- 资产负债率30%-34%极其健康

### 估值分析
- **分年PE中位数**技巧最有价值：揭示估值中枢从38x(2021)漂移到21x(2024-2026)
- 前瞻PE = 股价36.29 / 一致预期EPS 1.75 ≈ 20.7x（vs TTM PE 22.3x）
- PEG = 22.3 / 18.5 ≈ 1.2（合理偏低）

---

## 环节6-7：综合结论

### 概率化分类
| 类型 | 概率 | 依据 |
|:----:|:----:|------|
| 蓝筹 | 65% | 央企+ROE>15%+分红74%+消费/科技 |
| 成长 | 30% | 创新业务13%增速+AI大模型+机器人/汽车电子 |
| 周期 | 5% | 传统安防有周期+制裁扰动 |

### 最终判定
**关注（偏推荐）**。核心矛盾：好公司、低估值、但外部扰动大（美国制裁+营收停滞）。建议在28-30元区间（PE 18x）获得更好安全边际时介入。

---

## 可复用模板

### Baostock 脚本模板（估值 + 基本信息）
参考 `/tmp/fund_bs_002415_v2.py`（本次成功运行的最终版本），关键结构：

```python
import baostock as bs
lg = bs.login()

# 基本信息（防御性索引）
rs = bs.query_stock_basic(code='sz.{CODE}')
while rs.next():
    data = rs.get_row_data()

# K线估值数据
rs_k = bs.query_history_k_data_plus('sz.{CODE}', 'date,close,peTTM,pbMRQ',
    start_date='2019-09-30', end_date='{TODAY}', frequency='d', adjustflag='2')
while rs_k.next():
    krows.append({'date': row[0], 'close': row[1], 'pe': row[2], 'pb': row[3]})

# PE分年统计
from collections import defaultdict
by_year = defaultdict(list)
for k in krows:
    try:
        if k['pe'] and float(k['pe']) > 0:
            by_year[k['date'][:4]].append(float(k['pe']))
    except: pass

bs.logout()
```

### AKShare 财务数据模板
```python
import akshare as ak
df = ak.stock_financial_abstract_ths(symbol='{CODE}', indicator='按报告期')
key_fields = ['净资产收益率','销售毛利率','销售净利率',
              '营业总收入同比增长率','净利润同比增长率','扣非净利润同比增长率',
              '资产负债率','流动比率','速动比率','每股经营现金流',
              '基本每股收益','每股净资产','存货周转天数','应收账款周转天数']
```
