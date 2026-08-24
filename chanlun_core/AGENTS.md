# 缠论量化系统 — 项目规则

## 项目概述

A 股量化交易系统，基于缠论理论（分型→笔→线段→中枢→买卖点）进行多级别递归分析，结合三维辅助分析（筹码分布、资金面、市场情绪）、基本面筛选和组合回测。

## 技术栈

- Python 3.12，无 venv（直接使用系统 Python）
- 数据源：AKShare、Baostock、东方财富、同花顺 iFind
- 数据格式：Parquet 缓存（`data_cache/`），JSON 交换
- YAML 配置（`config.yaml`），Python 导入（`config_loader.py`）
- 无 pytest 测试（手动分析脚本验证）

## 架构地图

```
chanlun_core/
├── backtest_engine.py         # 漏斗过滤法回测引擎
├── generate_analysis.py       # 缠论多级别递归分析库（核心）
├── segment_analyzer.py        # 线段分析
├── trading_strategy.py        # 交易策略 + 信号定义
├── data_manager.py            # 数据管理（K线、财报、筹码）
├── config_loader.py           # YAML 配置加载
├── composite_scorer.py        # 综合评分
├── stock_pool.py / pool_screener.py   # 股票池筛选
├── analyze.py                 # 三维辅助分析入口（筹码/资金/情绪）
├── multi_stock_scanner.py     # 多股并行扫描
├── slippage_model.py          # 滑点模型
├── market_regime.py           # 市场状态识别
├── risk_filter.py             # 风险过滤器
├── akshare_scanner.py         # AKShare 扫描器
├── hithink_fundamental.py     # 同花顺基本面
├── config.yaml                # 用户配置
└── docs/                      # 文档
```

## 编码约定

### 导入规则
```python
# 标准库优先，第三方次之，项目模块最后
import json
from datetime import datetime
from typing import List, Optional
import pandas as pd
from date_utils import date_to_str, parse_date_to_datetime
from config_loader import THRESHOLD_XXX
```

### 命名规范
- 文件名：snake_case
- 函数/变量：snake_case
- 类：PascalCase
- 常量：UPPER_SNAKE_CASE（位于 `config_loader.py`）
- 私有函数/方法：_leading_underscore

### 文档字符串
```python
def function_name(param: str) -> bool:
    \"\"\"简短描述做什么
    
    Args:
        param: 参数说明
        
    Returns:
        返回值说明
    \"\"\"
```

### 回测开发黄金法则（不可违反）
1. **先回测，后实盘** — 任何策略改动必须在 `backtest_engine.py` 或 `run_backtest.py` 中回测验证
2. **不改非目标代码** — 修改只针对用户指定的文件/功能，不重构没坏的东西
3. **数据溯源** — 所有 K 线数据、财务数据必须来源明确（AKShare/Baostock/东方财富），不硬编码魔数
4. **双面验证** — 修改参数或阈值时，同时用 A 组（上涨市）和 B 组（震荡/下跌市）数据验证

### 关键文件职责

| 文件 | 职责 | 不要放什么 |
|------|------|-----------|
| `generate_analysis.py` | 分型→笔→线段→中枢递归分析逻辑 | 策略逻辑、数据获取 |
| `backtest_engine.py` | 回测执行、统计、绩效报告 | 信号定义、分析算法 |
| `trading_strategy.py` | 买卖点信号定义、建仓/加仓/清仓规则 | 数据获取、回测执行 |
| `data_manager.py` | 数据获取、缓存、清洗 | 分析算法、交易逻辑 |
| `config_loader.py` | YAML 配置加载 + 常量 | 运行时逻辑、业务代码 |

### 配置优先原则
所有可调参数（阈值、比例、开关）必须先放入 `config.yaml`，通过 `config_loader.py` 导入，**不许硬编码在业务代码中**。

### 回测命令
```bash
# 单只股票回测
python3 backtest_engine.py 600519

# 批量回测
python3 backtest_engine.py 600519 000858 000568

# 组合回测
python3 portfolio_backtest.py

# 网格搜索优化参数
python3 grid_search.py
```

## 常见操作

1. **增加新指标**：先在 `composite_scorer.py` 中添加计算逻辑，再在 `config.yaml` 中配置权重
2. **修改回测参数**：只改 `config.yaml`，不改 `backtest_engine.py` 里的常量
3. **添加新数据源**：在 `data_source_helper.py` 中添加适配器，不要在调用方硬编码
4. **修改缠论分析逻辑**：改 `generate_analysis.py`，不影响 `backtest_engine.py`

## 回测结果评估标准

- 基准：沪深 300（`000300.SH`）
- 核心指标：总收益率、年化收益率、最大回撤、夏普比率、胜率、盈亏比
- 最低要求：年化 > 15%，最大回撤 < 20%，夏普 > 0.8
