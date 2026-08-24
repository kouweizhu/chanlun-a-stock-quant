# AKShare API 实测结果 (2026-05-01)

环境：AKShare v1.18.56，Python 3.x，WSL2

## 已验证可用 API

### 财务数据
| API | 数据内容 | 示例行数 | 数据源 | 备注 |
|:----|:---------|:--------:|:-------|:-----|
| `stock_financial_abstract_ths(symbol, indicator)` | 财务摘要: ROE/净利率/资产负债率/每股收益等 | 121 | 同花顺 | **主力接口**，字段丰富 |
| `stock_financial_report_sina(stock, symbol)` | 三大报表: 利润表/资产负债表/现金流量表 | 102 | 新浪 | `stock` 需带 sh/sz 前缀, `symbol` 选报表类型 |
| `stock_a_indicator_lg` / `stock_a_lg_indicator` | 估值指标 PE/PB/PS | - | 乐咕 | **API 已变更**，v1.18.56 中不存在 |

### 公告
| API | 数据内容 | 示例行数 | 数据源 | 备注 |
|:----|:---------|:--------:|:-------|:-----|
| `stock_notice_report(symbol, date)` | 公司公告(全量) | 12924 | 东财 | `symbol='全部'`, `date='YYYYMMDD'`，返回单日全量 |

### 研报
| API | 数据内容 | 示例行数 | 数据源 | 备注 |
|:----|:---------|:--------:|:-------|:-----|
| `stock_research_report_em(symbol)` | 个股研报(东财评级+目标价) | 224 | 东财 | 字段: 序号/股票代码/股票简称/报告名称/东财评级 |

### 宏观经济
| API | 数据内容 | 示例行数 | 频率 |
|:----|:---------|:--------:|:----:|
| `macro_china_gdp()` | GDP (季度/同比) | 81 | 季 |
| `macro_china_cpi()` | CPI (当月/同比/环比/累计) | 219 | 月 |
| `macro_china_pmi()` | PMI (制造业+非制造业) | 220 | 月 |
| `macro_china_money_supply()` | M2/M1 (数量+同比) | 219 | 月 |
| `macro_china_shrzgm()` | 社融规模增量 | 132 | 月 |

### K线行情 (data_manager.py 已用)
| API | 数据内容 | 备注 |
|:----|:---------|:-----|
| `stock_zh_a_daily(symbol, adjust)` | 日线(新浪源) | 已集成，备选2 |
| `stock_zh_a_minute(symbol, period)` | 分钟线(新浪源) | 已集成，备选2 |
| `stock_zh_a_hist(symbol, period, adjust)` | 日线(东财源) | 已集成，备选3 |
| `stock_zh_a_hist_min_em(symbol, period, adjust)` | 分钟线(东财源) | 已集成，备选3 |

## 已验证不可用 API

| API | 错误 | 原因 |
|:----|:-----|:-----|
| `stock_financial_analysis_indicator` | 返回空 DataFrame | API 已废弃，v1.18.56 中无数据 |
| `stock_news_em` | `Invalid regular expression: invalid escape sequence: \u` | AKShare v1.18.56 bug |
| `stock_board_industry_name_em` | `RemoteDisconnected` | 东财限流 |
| `stock_zh_a_spot_em` | `RemoteDisconnected` | 东财限流 |
| `stock_profit_forecast` | `module has no attribute` | API 已移除/改名 |
| `stock_a_indicator_lg` | `module has no attribute` | API 已移除/改名 |

## 集成风险

1. **东财源限流**: `stock_zh_a_spot_em`, `stock_board_industry_name_em` 等东财接口批量请求会连接中断。500只批量需 sleep(0.5~1s) + 重试
2. **API 变更频繁**: AKShare 版本迭代快，废弃接口多。所有调用必须 try/except 包裹，失败 fallback
3. **同花顺源相对稳定**: `stock_financial_abstract_ths` 适合做主力财务数据源
4. **公告接口慢**: `stock_notice_report` 全量扫描 13000+ 行需 ~19 秒，建议只扫特定股票
