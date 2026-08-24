# EastMoney API — A股财务数据获取

## 概述

东方财富数据中心提供免费、无需认证的REST API，可直接通过 HTTP GET 获取A股基本面数据和财报信息。当 Tavily MCP / crawl4ai 不可用时，这是最可靠的后备数据源。

## 基础URL

```
https://datacenter.eastmoney.com/securities/api/data/v1/get
```

## 通用参数

| 参数 | 说明 | 示例 |
|:-----|:-----|:-----|
| reportName | 报表名称 | RPT_LICO_FN_CPD（业绩报表） |
| columns | 返回字段 | ALL 或逗号分隔列表 |
| filter | 过滤条件 | (SECUCODE=%22688036.SH%22) |
| pageNumber | 页码 | 1 |
| pageSize | 每页条数 | 5 |
| sortTypes | 排序方向(-1降序) | -1 |
| sortColumns | 排序字段 | NOTICE_DATE 或 REPORT_DATE |

> `%22` 是双引号的URL编码，等效于 `"`。在Python字符串中写到URL时直接写 `%22` 或使用 `urllib.parse.quote('"')`。

## 核心端点

### 1. 业绩报表 (RPT_LICO_FN_CPD)

获取公司各报告期的营收、利润、毛利率、EPS等核心数据。

```python
import requests
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
url = (
    'https://datacenter.eastmoney.com/securities/api/data/v1/get'
    '?reportName=RPT_LICO_FN_CPD'
    '&columns=ALL'
    '&filter=(SECUCODE=%22' + stock_code + '.SH%22)'
    '&pageNumber=1&pageSize=5'
    '&sortTypes=-1&sortColumns=NOTICE_DATE'
)
r = requests.get(url, headers=headers, timeout=10)
data = r.json()
```

**返回的 result.data[0] 字段映射：**

| JSON字段 | 含义 | 类型/单位 |
|:---------|:-----|:----------|
| DATATYPE | 报告期说明 | 如"2026年 一季报" |
| REPORTDATE | 报告截止日期 | 2026-03-31 |
| NOTICE_DATE | 披露日期 | 2026-04-28 |
| TOTAL_OPERATE_INCOME | 总营收 | float（元） |
| PARENT_NETPROFIT | 归母净利润 | float（元） |
| BASIC_EPS | 基本每股收益 | float |
| WEIGHTAVG_ROE | 加权平均ROE | float(%) |
| XSMLL | 销售毛利率 | float(%) |
| YSTZ | 营收同比增速 | float(%) |
| SJLTZ | 归母净利润同比增速 | float(%) |
| BPS | 每股净资产 | float |
| MGJYXJJE | 每股经营现金流 | float（可为负） |
| KCFJCXSYJLR | 扣非净利润 | float（元） |
| KCFJCXSYJLRTZ | 扣非净利润同比 | float(%) |
| ZCFZL | 资产负债率 | float(%) |
| LD | 流动比率 | float |
| SD | 速动比率 | float |

### 2. 公司公告API（当Tavily不可用时替代消息面数据源）

获取公司近期公告列表（无需认证）。适用于 Tavily/crawl4ai 不可用时主Agent直接获取公告数据做消息面评分。

```python
import requests
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
r = requests.get(
    'https://np-anotice-stock.eastmoney.com/api/security/ann',
    params={
        'sr': -1,              # 排序方向(-1=降序)
        'page_size': 20,       # 每页条数
        'page_index': 1,       # 页码
        'ann_type': 'A',       # 公告类型(A=全部)
        'stock_list': '688036', # 股票代码（无需.SH后缀）
        'f_node': '0',         # 起始节点
        's_node': '0',         # 结束节点
    },
    headers=headers,
    timeout=10
)
data = r.json()
anns = data.get('data', {}).get('list', [])
for ann in anns:
    print(f"[{ann['notice_date'][:10]}] {ann.get('title_ch','')[:60]}")
```

**返回字段说明：**

| 字段 | 含义 | 示例 |
|:-----|:-----|:-----|
| notice_date | 公告日期 | "2026-04-30 00:00:00" |
| title_ch | 公告标题(中文) | "传音控股2026年第一季度报告" |
| art_code | 公告唯一编码 | "AN202604281821732928" |
| columns[0].column_name | 公告类型 | "一季度报告全文" / "调研活动" / "分配预案" |
| display_time | 发布时间 | "2026-04-28 21:30:21:371" |

**常见公告类型及其市场含义：**

| 公告类型 | 含义 | 消息面影响 |
|:---------|:-----|:----------|
| 一季度报告全文 | Q1季报发布 | 强正面/负面（取决于数据） |
| 调研活动 | 机构投资者调研 | 正面（机构关注度高） |
| 分配预案 | 分红方案 | 正面（特别是有中期分红规划） |
| 业绩说明会 | 集体业绩说明会 | 中性偏正面（积极沟通） |
| 股东大会通知 | 年度股东会 | 中性 |
| 内部控制报告 | 内控评价 | 正面（无缺陷为佳） |
| ESG公告 | 可持续发展报告 | 中性偏正面 |
| 审计报告 | 年度审计 | 中性（无保留意见为佳） |
| 续聘会计师事务所 | 续聘审计机构 | 中性（正常操作） |
| 会计政策变更 | 会计政策调整 | 中性（需看具体内容） |
| 保荐/核查意见 | 保荐机构意见 | 中性偏正面 |

**从公告判断消息面偏好的实战技巧：**

1. **季报后次日有调研** = 强烈正面信号（机构抢着约管理层）
2. **首次中期分红规划** = 超正面信号（回报意识超科创板平均）
3. **连续季度业绩改善** = 拐点确认（如毛利率连续提升+营收增速转正）
4. **审计无保留意见+内控无缺陷** = 财务健康基础保障

### 3. 备用端点（当RPT_LICO_FN_CPD失败时）

```python
# 用REPORT_DATE排序（而非NOTICE_DATE）
url2 = (
    'https://datacenter.eastmoney.com/securities/api/data/v1/get'
    '?reportName=RPT_LICO_FN_CPD'
    '&columns=ALL'
    '&filter=(SECUCODE=%22' + stock_code + '.SH%22)'
    '&pageNumber=1&pageSize=5'
    '&sortTypes=-1&sortColumns=REPORT_DATE'
)
```

如果上述端点都失败，尝试：
```python
# 主板是 .SH，创业板是 .SZ
filter_secucode = '(SECUCODE=%22' + stock_code + '.SH%22)'
# 科创板也是 .SH，深圳主板/创业板/中小板都是 .SZ
```

## 实战案例：传音控股(688036) 季度数据

从本次会话中实际获取的数据：

### 2026年一季报（2026-04-28披露）

```json
{
  "DATATYPE": "2026年 一季报",
  "TOTAL_OPERATE_INCOME": 16200109629.19,
  "PARENT_NETPROFIT": 700357725.49,
  "BASIC_EPS": 0.61,
  "WEIGHTAVG_ROE": 3.35,
  "XSMLL": 22.0046685119,
  "YSTZ": 24.5795363273,
  "SJLTZ": 42.904909923434,
  "BPS": 18.452390220718,
  "MGJYXJJE": -3.556293295433,
  "ZCFZL": 55.1491477964,
  "KCFJCXSYJLR": 619060800,
  "KCFJCXSYJLRTZ": 80.371925929487
}
```

### 历史对比数据

| 报告期 | 营收(亿) | 净利润(亿) | 毛利率(%) | EPS |
|:-------|:---------|:-----------|:----------|:----|
| 2023年报 | 622.95 | 55.37 | 23.55 | 4.91 |
| 2024年报 | 687.15 | 55.49 | 21.28 | 4.90 |
| 2025年报 | 655.91 | 26.05 | 19.15 | 2.26 |
| **2026Q1** | **162.00** | **7.00** | **22.00** | **0.61** |

**从这些数据可以发现的规律：**
- 毛利率从2023年的23.55%一路下滑至2025年的19.15%，2026Q1反弹至22%是重要拐点信号
- 净利润2025年腰斩（-53.49%），但2026Q1同比+42.9%确认反转
- 经营现金流Q1为负（-3.56元/股）是季节性特征（备货季+应收增加），不必然代表全年趋势

## 场景：Tavily 不可用时完整替换方案

当子Agent彻底无法工作（Tavily MCP失败 + crawl4ai失败）时，主Agent可以自己完成所有数据获取：

```
# 1. 本地脚本（技术面+基本面）
quick_chanlun.py     ← 正常运行（不依赖网络）
quick_fundamental.py ← 正常运行，但注意数据可能过时
quick_html.py        ← 正常运行

# 2. 财报补充（替代子Agent的消息面功能）
EastMoney API → 获取最新季度财报数据
                + 获取历史对比数据
                + 辅助判断业绩拐点

# 3. 搜索替代
如果curl可用：curl -s "https://www.google.com/search?q=..." 
但注意：Google/Baidu 可能会封频繁请求

# 4. 评分折价
如果完全无法获取实时消息面信息：
- 综合评分 ×0.8 折价
- 决策标注"缺少消息面排雷，严禁重仓"
- 在报告中明确标注数据来源限制
```

## 已知问题

1. EastMoney API 偶尔返回 `success:false, message:"END_DATE排序列不存在"` — 换用 `NOTICE_DATE` 或 `REPORT_DATE` 排序即可
2. 科创板股票代码后缀为 `.SH`（同主板），深圳交易所股票后缀为 `.SZ`
3. headers 必须设置 `User-Agent`，否则可能被拒
4. Q1单季的经营现金流为负是常见现象（备货增加+应收账款增加），不必然代表全年现金流恶化，需结合季节性判断
