---
name: quant-grid-search-and-automation
description: 量化策略参数网格搜索 + 定时自验证自动化——对权重方案、阈值、锚定规则等参数进行暴力搜索寻找最优组合，并通过 cron 定期重新验证防止模型漂移。
tags: [网格搜索, 参数优化, 自动化验证, cron, 量化策略, 模型漂移, 多维监控, 2σ检测]
version: 1.4
created: 2026-04-27
updated: 2026-04-29
---

# 参数网格搜索与自动化重验（v1.1 — 含已确认最优参数）

## 已实测确认的最优参数

### 修复前（使用 confidence*20 替代变量，2026-04-27）
- 权重方案: (0.30, 0.40, 0.30)
- 置信度锚定: 启用
- 评分阈值: ≥60分
- 20日收益率: +16.43%, 胜率: 100%, Sharpe: 1.67

### 修复后（使用真实基本面评分，2026-04-29，18只自选股/71信号）
- 最优权重: **(0.35, 0.35, 0.30)** 均衡方案（Sharpe=0.596, 63信号, +11.81%, 78.3%胜率）
- 次优: (0.30, 0.40, 0.30) 原方案（Sharpe=0.578, 59信号, +11.34%, 78.9%胜率）
- 所有方案的 Sharpe 差距在 0.02 以内（0.57-0.60）——无统计显著差异，权重不敏感
- 均衡方案微幅领先，说明当前市场技术面与基本面预测力相当

### 修复后的参数建议
- 保持当前权重不变（变化在噪声范围内）
- 每季度重跑网格搜索监控权重漂移
- 基本面评分分布: min=42 median=69 max=87（区分度良好）
- **关键修复**: `get_fund_score()` 按回测日期季度回溯真实财务数据（Baostock year/quarter参数），替代了 `confidence*20` 的错误代理

## 注意事项
- 所有方案的 Sharpe 差距通常在 0.02 以内（当前市场下权重不敏感）
- 基本面权重越高 → 信号数量基本不变（真实基本面评分分布广，不集中）
- 网格搜索建议每季度一次（与回测频率对齐），过频会导致过拟合
- **绝对不能使用替代变量**（如 confidence*20 替代 fund_score）——这会导致参数优化在错误的维度上进行
- Baostock 季度查询必须使用字段名 (`dict(zip(fields, row))`) 而非位置索引——不同查询返回字段顺序不同

## 适用场景

当以下条件同时满足时使用：

1. 你有一个**可回测的量化策略**（如缠论择时系统），能产出历史信号
2. 你的策略有**可调整的参数**（权重方案、阈值、锚定规则、评分因子权重等）
3. 你需要找到这些参数的**最优组合**（通过历史数据验证）
4. 你希望**定期自动重新验证**参数是否仍然有效（防模型漂移）

**典型触发词：**
- "跑一下参数优化，看看什么权重最好"
- "这个权重方案是不是最优的？帮我做个网格搜索"
- "建一个定时任务，每周自动验证评分模型是否还靠谱"
- "帮我自动化验证流程"

**不适用场景：**
- 一次性验证评分模型是否有效 → 用 `tech-score-backtest-validation`
- 诊断信号消失/异常 → 用 `chanlun-signal-disappearance`
- 构建评分模型本身 → 用 `daily-chanlun-timing-system`

## 参数网格搜索实现

### 脚本结构

```python
# grid_search.py — 标准模板

import sys, os, json
import pandas as pd
import numpy as np

# 1. 定义参数网格
WEIGHT_SCHEMES = [                      # 权重方案 (tech/fund/news)
    (0.30, 0.40, 0.30),                # 默认
    (0.35, 0.35, 0.30),                # 均衡
    (0.40, 0.30, 0.30),                # 技术面优先
    (0.25, 0.50, 0.25),                # 基本面优先
]

ANCHORING_OPTIONS = [True, False]      # 置信度锚定开关

SCORE_THRESHOLD = 60                   # 综合评分阈值（低于此值的信号被过滤）

# 2. 遍历网格，评估每种组合
def evaluate_combination(W_tech, W_fund, W_news, use_anchoring, signals):
    """对一组参数计算绩效指标"""
    composite_scores = []
    for s in signals:
        score = s['tech_score'] * W_tech + s['confidence'] * 20 * W_fund + 50 * W_news
        composite_scores.append(score)
    
    # 过滤 low-score 信号
    valid_mask = np.array(composite_scores) >= SCORE_THRESHOLD
    valid_signals = [s for s, v in zip(signals, valid_mask) if v]
    
    if len(valid_signals) < 3:
        return None  # 样本不足
    
    # 计算指标
    fwd_20d = [s['fwd_20d'] for s in valid_signals if s.get('fwd_20d') is not None]
    win_rate = sum(1 for r in fwd_20d if r > 0) / len(fwd_20d) if fwd_20d else 0
    
    return {
        'signal_count': len(valid_signals),
        'avg_fwd_20d': np.mean(fwd_20d),
        'win_rate': win_rate,
        'sharpe': np.mean(fwd_20d) / np.std(fwd_20d) if len(fwd_20d) >= 5 and np.std(fwd_20d) > 0 else 0,
    }

# 3. 排序输出
# 按 Sharpe 降序排列
```

### 性能指标选择

| 指标 | 计算方式 | 选谁？ |
|:-----|:---------|:------|
| **Sharpe 比率** | mean(fwd_return) / std(fwd_return) | 最佳综合指标，兼顾收益和稳定性 |
| 胜率 | 正收益信号数 / 总信号数 | 辅助判断 |
| 信号数 | 通过阈值的信号总数 | 辅助判断（太少的信号=过度拟合） |
| 20日平均收益 | mean(fwd_20d) | 辅助判断 |

### 注意事项

**过拟合风险（最重要！）：**
- 网格搜索找到的"最优参数"在样本外数据上可能无效
- 信号数过少（<5个）的组合即使Sharpe高也不可信
- 不同排名之间的Sharpe差距<0.2时，视为"无显著差异"，选最简单的方案（奥卡姆剃刀）
- 建议保留一个"验证集"（如最新的20%数据）用于样本外测试

**A股特点：**
- 权重方案的变化对信号筛选的影响大于对评分本身的影响
- 基本面权重高的组合通常会过滤更多信号，剩余信号质量更高但数量更少
- 技术面权重高的组合信号更多但Sharpe可能更低

## 定时自验证实现

### cronjob 配置

```yaml
名称: 策略定时自验证
频率: 每周一/三/五 20:00（0 20 * * 1,3,5）
技能: [daily-chanlun-timing-system]
任务: 运行 auto_validate.py，汇总关键指标，异常时预警
报告格式: Markdown (.md)
报告路径: auto_reports/*.md
```

### 自验证脚本结构 (v2.0 — 2026-05-01 升级)

`auto_validate.py` v2.0 从 82 行扩展到 ~220 行，新增多维指标漂移监控。

**核心流程**：
```python
# 1. 运行 validate_tech_score.py 获取信号
# 2. 提取核心指标 → extract_metrics(json_path)
# 3. 加载 90 天历史 → load_history() from metrics_history.json
# 4. 追加今日指标 + 回存
# 5. 漂移检测 → check_drift(history, window=30)
# 6. 输出告警（ALERT/WARN/OK）
```

**监控指标**（6 维）：

| 指标 | 含义 | 用途 |
|------|------|------|
| `tech_score_mean` | 技术评分均值 | 评分分布漂移 |
| `tech_score_std` | 技术评分标准差 | 评分区分度变化 |
| `signal_count` | 买点信号总数 | 信号频率异常 |
| `buy_type_1_count` | 一类买点数量 | 极端信号频率 |
| `grade_A_rate` | A+/A 级占比 | 乐观/悲观倾向 |
| `error_rate` | 分析失败率 | 数据源健康度 |

### 漂移检测 (v2.0 新增 — 替代旧版静态阈值)

**旧版**：固定阈值（信号<10→告警，B级>50%→告警）——不适应市场结构变化。

**新版**：30 日滚动窗口的 2σ 动态检测：
- Z > 2.0 → `[ALERT]` — 显著异常
- Z > 1.5 → `[WARN]` — 值得关注
- Z ≤ 1.5 → `[OK]` — 正常
- 额外：signal_count == 0 时无条件 ALERT（数据源可能故障）

**告警示例**：
```
[ALERT] 买点信号数: 今日 3 ↓ (30日均值 24.0, σ=3.7, Z=5.6)
[ALERT] 一类买点数: 今日 1 ↓ (30日均值 7.5, σ=1.8, Z=3.7)
```

### 文件组织 (v2.0)
```text
工作目录/

├── validate_tech_score.py       # 验证脚本
├── grid_search.py               # 网格搜索
├── auto_validate.py             # 自验证入口 v2.0
├── auto_reports/                # 定时报告存档 (Markdown格式)
│   ├── metrics_history.json     # 90天指标历史（新） ← 漂移检测数据源
│   ├── 2026-05-01_validation.md
│   ├── 2026-05-01_grid_search.md
│   └── ...
└── tech_score_validation.json   # 最新验证结果（覆盖）
```

**cron 用法**：
```bash
python3 auto_validate.py                    # 默认：验证 + 指标 + 漂移检测
python3 auto_validate.py --all              # 加网格搜索
python3 auto_validate.py --metrics-only     # 仅更新指标（不跑全量验证）
```

## 实施示例

参考 `/mnt/c/Users/13120/WorkBuddy/Claw/生活/缠论/` 中的实现：

```bash
# 运行网格搜索
python3 grid_search.py

# 手动触发自验证
python3 auto_validate.py

# 全量验证 + 网格搜索
python3 auto_validate.py --all
```

## 常见陷阱

### 陷阱5：替代变量偏差（2026-04-29 实战重大发现）

**现象**：`compute_composite_score()` 中使用 `conf_score = confidence * 20.0` 作为 W_fund 权重对应的评分。网格搜索结果显示 (0.30, 0.40, 0.30) 最优。

**根因**：`confidence * 20` 不是基本面评分，而是技术面的多级别确认强度。用代理变量跑网格搜索，找到的"最优参数"实际上是在优化"置信度权重"而非"基本面权重"。

**修复步骤**：
1. 引入真实基本面评分：`get_fund_score(symbol, date_str, stock_price=price)` 查询 Baostock 历史财务数据
2. 估值维度 PE 按信号日股价实时计算（缓存仅缓存财务数据，不缓存 PE）
3. Baostock 字段必须用 `dict(zip(fields, row))` 按名称取值，不能用 `row[N]` 位置索引
4. 重新运行后确认：真实基本面数据下方差显著增大（min=42, median=69, max=87），替代变量时代全部为 52

**关键教训**：网格搜索中的任何一个变量，如果是 `hardcoded_constant * N` 或与其他维度不独立的值，搜索出来的参数就是垃圾。

### 陷阱7：Windows/WSL 路径混用与目录创建（2026-05-06 实战）

**现象**：用户指定报告路径 `/mnt/D:\常用文件\回测报告\定时自验证报告`（混合写法：WSL挂载前缀+Windows盘符）。直接传递给 Python 的 `open()` 会失败。

**正确做法**：
```python
# 用户给的路径可能是混合格式，统一转为 WSL 风格
user_path = "/mnt/D:\\常用文件\\回测报告\\定时自验证报告"
# 转换为: /mnt/d/常用文件/回测报告/定时自验证报告
import re
if re.match(r'/mnt/[A-Za-z]:', user_path):
    # /mnt/D:\path → /mnt/d/path
    wsl_path = re.sub(r'/mnt/([A-Za-z]):', lambda m: f'/mnt/{m.group(1).lower()}', user_path)
    wsl_path = wsl_path.replace('\\', '/')
else:
    wsl_path = user_path

# 关键：确保目录存在
os.makedirs(wsl_path, exist_ok=True)
REPORT_DIR = wsl_path
```

**正确模式**（已在 auto_validate.py 中使用）：
```python
REPORT_DIR = "/mnt/d/常用文件/回测报告/定时自验证报告"
os.makedirs(REPORT_DIR, exist_ok=True)  # 必须！防止目录不存在报错
```

**教训**：
- 用户说"保存在 D:\xxx"时，在 WSL 中必须用 `/mnt/d/xxx`
- 任何自定义报告路径，**必须在设置后立即 `os.makedirs(..., exist_ok=True)`**
- 不要假设目录已存在，cron 任务第一次运行时常遇到目录缺失

### 陷阱6：股票池同步陷阱（2026-04-29 实战发现并修复）

**现象一（数量不同步）**: `auto_validate.py` cron 调用 `validate_tech_score.py` 无 `--full` 参数，只跑了 11 只硬编码默认池。而负面消息扫描 cron 读 xlsx 跑了 18 只。同样的"自选股"，两个 cron 跑不同的池子。

**现象二（分类不同步）**: `validate_tech_score.py` 用硬编码的 `cyclical_codes/growth_codes/blue_codes` 集合分类，而 `quick_fundamental.py` 有独立的 `classify_stock_type()` 数据驱动分类。两份分类互不通信，导致验证报告中的"按类型表现"统计基于部分股票且分类不一致。

**修复方案 —— 单一真相源**:
1. `check_negative_news.py::MONITOR_LIST` 改为从 xlsx 自动加载（`_load_monitor_list()`），xlsx 无行业时从 `_INDUSTRY_MAP` 硬编码映射表补全
2. `validate_tech_score.py`、`score_backtest.py`、`fund_backtest.py`、`grid_search.py` 全部从 `check_negative_news.MONITOR_LIST` 导入股票池
3. 新增 `quick_fundamental.classify_by_industry()` —— 仅需行业字符串即可分类（无需财务数据），供验证脚本调用
4. `validate_tech_score.py` 报告分组从硬编码集合改为动态聚合 `stock_type` 字段
5. 定时自验证 cron 路径从旧 `/mnt/c/.../生活/缠论` 迁移到新 `~/work/chanlun_core/`

**日常运维**：用户只需编辑一个 xlsx 文件 (`D:\常用文件\自选股负面消息清单\自选股清单.xlsx`)。新增股票时在 `_INDUSTRY_MAP` 中加一行行业（一次性）。所有 Python 模块、cron 任务、回测脚本自动跟随。

### 陷阱2：信号数过少
如果某个参数组合只选出2-3个信号但Sharpe很高，这可能是随机噪声。最低信号数要求：≥5个。

### 陷阱3：参数过拟合
网格搜索的维度越多，找到"恰好"适合历史数据的参数组合的概率越高。限制：一次最多搜索2-3个参数维度，总组合数不超过20种。

### 陷阱4：权重方案影响信号过滤
不同权重方案对信号进行不同的重排序，导致过滤后的信号集不同。对比不同方案时，要区分"方案本身好坏"和"过滤后信号集不同"。

### 陷阱5：Baostock 字段索引偏移（2026-04-29 发现）

**现象**：`grid_search.py` 中 `get_financial_data()` 用 `row[2]` 取 epsTTM，实际 Baostock `query_profit_data` 的列顺序因 year/quarter 而异——`row[2]` 可能是 `statDate` 日期字符串而非 EPS。

**修复**：始终用字段名查找。
```python
fields = [f.strip() for f in rs.fields]
vals = dict(zip(fields, row))
eps = safe_float(vals.get('epsTTM'))  # ✅ 按名取值，不按位
```

**相关文件**：任何调用 `bs.query_profit_data/growth_data/balance_data/cash_flow_data` 的地方。

### 陷阱6：缓存粒度过粗导致评分无区分度（2026-04-29 发现）

**现象**：`get_fund_score()` 缓存了完整的评分结果，但 PE 估值依赖信号日股价（不同信号同季度但不同日期有不同的 PE）。用季度级缓存导致所有同季度信号得到相同的 PE → 评分收敛到 52。

**修复**：分离缓存粒度——财务数据（盈利/成长/健康）按 `(symbol, quarter)` 缓存；估值评分按信号日股价实时计算不入缓存。
```python
# get_financial_data(symbol, date_str) → 缓存返回 {profitability, growth, health}
# get_fund_score(symbol, date_str, stock_price) → 拿缓存数据 + 实时算 PE
```

### 陷阱5：替代变量偏差（2026-04-29实战发现并修复）🔴

**现象**：`grid_search.py::compute_composite_score()` 用 `confidence * 20.0` 作为 W_fund 权重的输入值（代理基本面评分），而非调用 `quick_fundamental.calculate_fundamental_score()` 获取真实四维基本面评分。网格搜索找到的"最优基本面权重"实际上是在优化"置信度权重"。

**根因与影响链**：
```
grid_search.py 评分公式:
  composite = tech_score * w_tech + (confidence * 20) * w_fund + 50 * w_news
                                          ↑
                                    conf_score 替代了 fund_score
```
- `confidence` 来自 30min 级别确认——是技术面概念，不是财务数据
- 权重 (0.30, 0.40, 0.30) 的 W_fund=40% 实际分配给了置信度
- 修复后真实基本面评分代入，最优权重变为 (0.35, 0.35, 0.30)

**修复方案（分步）**：
1. 新增 `get_financial_data(symbol, date_str)` —— 按回测日期季度查询 Baostock（year/quarter参数，无前视偏差）
2. 新增 `get_quarter_for_date(date_str)` —— 日期到财报季度映射（考虑披露截止日: 年报4/30, 半年报8/31, 季报次月内）
3. 新增 `get_fund_score(symbol, date_str, stock_price)` —— 用财务数据 + 信号日股价计算PE估值
4. `compute_composite_score` 签名改为 `(tech_score, fund_score, confidence, w_tech, w_fund, w_news, use_anchoring)`
5. 缓存: 财务数据按 `(symbol, year, quarter)` 缓存，PE 按股价实时计算不缓存

**Baostock 字段映射陷阱（本次修复中的子问题）**：
使用 `dict(zip(fields, row))` 按字段名取值，而非 `row[3]` 等位置索引。不同查询返回的字段顺序和数量可能变化，位置索引在不同季度/股票间不可靠。

**估值维度限制**：历史 PE/PB 无法从 Baostock 直接获取（PB 需 book value per share），`dividend_yield` 同理。当前只计算 PE（EPS + 信号日股价），PB 和股息使用默认值。历史基本面评分因此系统性偏低 ~10 分，但不影响相对排序。

**自查方法**：检查 `compute_composite_score()` 中每个 `*_score` 变量是否来自生产环境的数据获取函数。看到 `confidence * N`、硬编码 `50`、或不调用 `get_fundamentals()` 的变量，就是替代变量偏差。

## 迭代优化循环

## 版本历史
- v1.6 (2026-05-01)：**cron 依赖管理**——新增 `cron_utils.py`（FlagSignals 文件信号 + CronLogger 统一日志）。pool_screener 成功后写 `a500_scan_done_YYYY-MM-DD.flag`，a500_backtest `--cron` 模式检查上游标记。`auto_validate.py` 改用 CronLogger 统一输出。日志路径：`logs/YYYY-MM-DD/<script_name>.log`。详见 `references/cron-dependency-pattern.md`。
- v1.5 (2026-05-01)：**auto_validate v2.0 漂移检测升级**——从固定阈值改为 30日滚动窗口 2σ 动态检测；新增 6 维监控指标（tech_score_mean/std、signal_count、buy_type_1_count、grade_A_rate、error_rate）；metrics_history.json 持久化 90 天历史；告警分级 ALERT/WARN/OK。
- v1.4 (2026-04-29)：**股票池统一管理**——陷阱6重写为股票池同步实操指南；新增 MONITOR_LIST xlsx 自动加载模式、`classify_by_industry()` 统一分类、单文件运维入口（`check_negative_news.py::_load_monitor_list()`）。
- v1.3 (2026-04-29)：**auto_validate 路径修复**——`auto_validate.py` cron 必须传 `--full` 跑全部18只；`validate_tech_score.py --full` 应从 `check_negative_news.MONITOR_LIST` 加载。
- v1.2 (2026-04-29)：**替代变量偏差**——陷阱5发现修复。

```
1. 实现基础评分模型
         │
         ▼
2. 运行 validate_tech_score.py → 发现评分无区分度
         │
         ▼
3. 调整评分公式（加因子、调权重）
         │
         ▼
4. 重新验证 → 相关系数提升？
         │           │
         ▼           ▼
       是           否
         │           │
         ▼           ▼
  5a. 运行 grid_search    5b. 换方向选因子
     → 找最优参数组合         → 重新设计评分框架
         │
         ▼
  6. 设置 auto_validate cronjob
     → 持续监控模型漂移
```
