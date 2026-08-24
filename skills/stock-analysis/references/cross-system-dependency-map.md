# 跨系统依赖映射 — 审计清单

## 概述

`stock-analysis`（个股三维分析）和 `a500-screening-workflow`（A500批量选股）共享同一套核心缠论脚本（`generate_analysis.py`、`data_manager.py`、`composite_scorer.py`、`config_loader.py`）。修改共享代码时必须审计所有消费者。

## 共享脚本依赖图

```
generate_analysis.py (ChanLunAnalyzer, RecursiveTimingSystem, HTMLVisualizer)
├── quick_chanlun.py          ← stock-analysis 入口
├── quick_html.py             ← stock-analysis 可视化
├── pool_scanner.py           ← A500 Phase 1 技术扫描
├── pool_screener.py          ← A500 Phase 2+3 评分报告
├── position_monitor.py       ← 持仓监控
├── score_backtest.py         ← 回测验证
├── segment_analyzer.py       ← 段级别分析
├── market_regime.py          ← 大盘状态判断
├── validate_tech_score.py    ← 技术评分验证
├── test_segment_zhongshu.py  ← 段中枢测试
├── backtest_engine.py        ← 回测引擎
├── trading_strategy.py       ← 交易策略
├── multi_stock_scanner.py    ← 多股扫描
├── report_generator.py       ← 报告生成
├── full_rescore.py           ← 批量重新评分
├── portfolio_backtest.py     ← 组合回测
├── generate_hs300_html_v2.py ← HS300 HTML 报告
└── generate_hs300_monthly.py ← HS300 月报

composite_scorer.py (compute_3d_score, Score3D)
├── pool_screener.py          ← A500 评分
└── 个股分析报告生成

config_loader.py (W_TECH, W_FUND, W_NEWS 等)
├── pool_screener.py          ← A500 评分权重
└── composite_scorer.py       ← 权重配置

quick_fundamental.py (Baostock 基本面)
├── pool_screener.py          ← A500 基本面主数据源
├── grid_search.py            ← 参数搜索
├── fund_backtest.py          ← 基本面回测
├── validate_tech_score.py    ← 行业分类
├── score_backtest.py         ← 回测
└── full_rescore.py           ← 重新评分

hithink_fundamental.py (同花顺 基本面) ← stock-analysis v4.2 专用
└── 仅通过 python hithink_fundamental.py {code} 调用，无 import 消费者
```

## 修改审计清单

修改以下文件前，必须检查对应的消费者：

| 修改的文件 | 必须检查的系统 | 检查内容 |
|:----------|:-------------|:---------|
| `generate_analysis.py` | **全部18个消费者** | API兼容性、参数签名、返回值格式 |
| `data_manager.py` | 所有 `from data_manager import` 文件 | 方法签名、返回值类型 |
| `composite_scorer.py` | pool_screener.py + 个股报告 | 评分公式、权重、阈值 |
| `config_loader.py` | pool_screener.py + composite_scorer.py | 权重默认值、键名 |
| `quick_fundamental.py` | pool_screener.py + grid_search + fund_backtest | 函数签名、字段名、返回结构 |
| `hithink_fundamental.py` | 无 import 消费者（仅CLI调用） | 语法检查即可 |
| `stock-analysis SKILL.md` | 无代码冲突 | 引用文件路径正确性 |
| `a500-screening-workflow SKILL.md` | 无代码冲突 | Phase 1/2/3 脚本参数正确性 |

## 典型冲突模式

### 1. 函数签名不兼容
```python
# 旧签名
def get_fundamentals(code): ...
# 新签名
def get_fundamentals(code, source='auto'): ...  # ← pool_screener 不传 source，默认行为一致则安全
```

### 2. 返回值结构变化
```python
# 旧返回
{'roeAvg': 15.2, 'gpMargin': 48.6}
# 新返回
{'roe': 15.2, 'grossMargin': 48.6}  # ← 键名改了！pool_screener 会读到 None
```

### 3. 新增依赖未安装
在 `generate_analysis.py` 顶部新增 `import some_new_package` → 所有18个消费者都会报 ImportError。

### 4. 数据源切换导致评分偏差
`quick_fundamental.py` (Baostock) vs `hithink_fundamental.py` (同花顺) 可能对同一只股票给出不同评分。个股分析使用 hithink，A500 使用 Baostock+akshare — **这是设计差异，非 Bug**，但推理链中应注明数据源。

## 快速审计命令

```bash
# 1. 语法检查所有依赖文件
cd D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core
for f in generate_analysis.py data_manager.py composite_scorer.py config_loader.py quick_fundamental.py; do
  python -c "import py_compile; py_compile.compile('$f', doraise=True)" && echo "$f: OK" || echo "$f: FAIL"
done

# 2. 检查 import 是否全部正常
python -c "from generate_analysis import ChanLunAnalyzer, RecursiveTimingSystem, HTMLVisualizer; print('generate_analysis imports: OK')"
python -c "from composite_scorer import compute_3d_score, Score3D; print('composite_scorer imports: OK')"

# 3. 检查 pool_screener 是否能加载
python -c "import ast; ast.parse(open('pool_screener.py').read()); print('pool_screener syntax: OK')"

# 4. 运行关键函数测试
python quick_chanlun.py 600519 2>&1 | head -3
python hithink_fundamental.py 600519 2>&1 | python -c "import sys,json; d=json.load(sys.stdin); print(f'fund confidence={d.get(\"confidence\")}, score={d.get(\"fundamental_score\",{}).get(\"total_score\")}')"
```