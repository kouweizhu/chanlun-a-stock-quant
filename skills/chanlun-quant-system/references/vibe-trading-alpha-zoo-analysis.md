# Vibe-Trading Alpha Zoo 分析 + 集成实现

**项目**: HKUDS/Vibe-Trading (github.com)  
**分析日期**: 2026-05-24  
**Stars**: 7.8k | **Forks**: 1.6k | **License**: MIT  
**最新版本**: v0.1.8 (2026-05-23)  
**PyPI**: `vibe-trading-ai`

---

## 项目定位

开源的自然语言驱动的研究式交易智能体。不做实盘执行，定位是研究和模拟。港大数据科学实验室出品。

## Alpha Zoo 核心结构

### 目录布局

```
agent/src/factors/
├── __init__.py      # 暴露 19 个基础算子
├── base.py          # 算子实现 + Market + Alpha + AlphaCompute
├── registry.py      # AST 扫描 + 懒加载 + 输出校验
├── bench_runner.py  # 批量跑 IC 统计
└── zoo/
    ├── alpha101/   # 101个因子 (alpha_001.py ~ alpha_101.py)
    ├── gtja191/    # 191个因子 (alpha_001.py ~ alpha_191.py)
    ├── qlib158/    # 154个因子 (ma5.py, std20.py, ...)
    └── academic/   # 6个因子 (smb.py, hml.py, ...)
```

### 每个因子的标准模式

```python
# gtja191/alpha_001.py
from src.factors.base import rank, delta, safe_div, ts_corr

__alpha_meta__ = {
    "id": "gtja191_001",
    "theme": ["volume", "reversal"],
    "columns_required": ["volume", "close", "open"],
    "universe": ["equity_cn"],
    "frequency": ["1d"],
}

def compute(panel: dict) -> pd.DataFrame:
    v = panel["volume"]
    c = panel["close"]
    o = panel["open"]
    x = rank(delta(np.log(v.where(v > 0)), 1))
    y = rank(safe_div(c - o, o))
    return -1.0 * ts_corr(x, y, 6)
```

### Panel 合约（数据输入格式宽表）

panel = {"open": DF(index=date, columns=stock), "high": ..., "low": ..., "close": ..., "volume": ..., "amount": ..., "vwap": ...}
- 行索引 DatetimeIndex，列是 instrument_code
- NaN 严格传播，无 silent fillna(0)
- 所有算子返回同形状 DataFrame

### 19 个基础算子

rank / scale / ts_rank / ts_corr / ts_cov / ts_mean / ts_std / ts_max / ts_min /
ts_argmax / ts_argmin / delta (d>=1) / decay_linear / signed_power / safe_div / vwap

### Registry 注册表模式

Registry.list(zoo, theme, universe) → list[alpha_id]
Registry.get(alpha_id) → Alpha
Registry.compute(alpha_id, panel) → pd.DataFrame
Registry.health() → dict

设计要点：AST 扫描元数据（不 import），懒加载模块，进程单例 get_default_registry()。

### Bench Runner

run_bench(zoo, universe, period, top=20) → dict
- alive: IC_mean>0.02 + pos_ratio≥0.55 + |t|>2
- reversed: IC_mean<-0.02 + |t|>2
- dead: 其他

---

## GTJA191 CSI300 bench 结果（已验证因子子集）

GTJA 191 个因子在 CSI300 (2018-2025) 上存活仅 **10 个（5%）**，反转 **15 个**。

### 存活因子（Top 5 已知 IC/IR）

| ID | IC_mean | IR | 主题 | 数据需求 |
|:--:|:------:|:--:|------|---------|
| gtja191_171 | 0.0432 | 0.2690 | 微观结构 | OHLCV |
| gtja191_111 | 0.0349 | 0.2232 | 微观结构 | OHLCV+volume |
| gtja191_163 | 0.0347 | 0.2008 | 微观结构 | 需 amount(vwap) |
| gtja191_002 | 0.0262 | 0.1619 | 价格反转 | OHL | C |
| gtja191_054 | 0.0272 | 0.1606 | 微观结构 | OHLCV |

### 主题存活率

微观结构(22%) > 反转(11%) > 波动率(8%) > 量价(5%) > 动量(2%) > 换手率(0%)

### A 股可用因子分布

gtja191: 191个全部 equity_cn → 10 存活
qlib158: 154个有 equity_cn 标签 → 无公开 A 股 bench
alpha101: 101个全部 equity_us → A 股不可用
academic: 6个跨市场 → 需 market_cap

---

## 实际集成实现（本轮会话）

### 抽取独立包 ~/work/alpha-zoo/

从 Vibe-Trading 抽离成无外部依赖的独立包：

```
~/work/alpha-zoo/
├── base.py          # 19 个算子（纯 pandas/numpy）
├── zoo.py           # 13 个因子 compute 函数 + FACTORS 注册表
├── dbhub_panel.py   # DBHub SQLite → 宽表 panel 适配器
└── test.py          # 验证脚本
```

base.py 将 Vibe-Trading 的 `from src.factors.base import ...` 全部去依赖，成为纯函数库。
zoo.py 包含 13 个因子：
- GTJA 幸存 4 个: 171, 111, 002, 054（跳过了需 amount 的 163）
- qlib158 形态 8 个: kup/kup2/kmid/kmid2/klow/klow2/ksft/ksft2
- FACTORS 注册表 dict 记录每因子的 data columns、meta 信息

### DBHub 适配器 dbhub_panel.py

```python
def load_panel(stock_codes, start_date, end_date, db_path, table="kline_daily"):
    """从 DBHub SQLite 读取数据，构建 Alpha Zoo 宽表 panel"""
    # 长表 -> pivot -> 宽表 dict
    # 列映射: open/high/low/close/volume

def get_stock_codes(limit=50, min_days=200):
    """获取数据最多的 N 只股票作为候选"""
```

DBHub 路径：`~/work/chanlun_core/data_cache/chanlun_klines.db`
K 线格式：6位代码（无 .SZ/.SH 后缀），date TEXT，open/high/low/close REAL，volume INTEGER

### alpha_factor_filter.py（chanlun_core 新增）

**位置**: ~/work/chanlun_core/alpha_factor_filter.py

**流程**:
1. 从 pool_scanner 的 .scanner_cache.json 读取候选股（score>=3，通常 30-80 只）
2. 从 DBHub 加载全部候选股的 OHLCV panel（近 2 年数据）
3. 跑 4 个 GTJA 幸存因子（gtja191_002/054/111/171），取最新交易日值
4. 每个因子做 cross-sectional percentile rank
5. 聚合为平均 rank → alpha_score [0, 100]
6. 返回 {code: alpha_score}

**关键设计**:
- **只用 GTJA 4 个因子**（2026-05-24 决定移除 qlib158 形态因子）。原因：qlib158 是截面描述统计量，横截面区分度低，聚合后大量股票落在中位数 50，稀释了 GTJA 微观结构因子的区分力。
- gtja191_163 因缺 amount 列跳过
- 每只股票至少要有 1/3 的因子有效才给分，否则 50 中性
- 聚合方式：mean_rank（多因子等权平均排名）
- 计算量：140 只 × 483 天 × 4 因子 ≈ 5 秒

**实际测试输出**（2026-05-14 缓存，140 只候选股，移除 qlib158 后）:
- 覆盖均匀：0-20(26只) 20-40(29) 40-60(28) 60-80(26) 80-100(31)
- 缠论5分+Alpha前20：格林美(99.3)、京沪高铁(98.6)、江特电机(97.9)、招商轮船(97.1)...
- 缠论5分+Alpha垫底（需要警惕但不一定能避免）：蓝色光标(1.4)、中国中车(2.9)、东阳光(5.0)
- 完整分布见 `references/active-gtja-factors-explained.md`

### 4D 综合评分系统

**修改**: composite_scorer.py

从三维(tech+fund+news)升级为四维(tech+fund+alpha+news):

```python
def compute_3d_score(tech_score, fund_score, alpha_score=50.0, news_score=50.0,
                     w_tech=0.35, w_fund=0.30, w_alpha=0.25, w_news=0.10):
    # 权重自动归一化确保和为 1
    # 共振惩罚：tech<60 AND fund<60 时扣分减半
    # 仓位逻辑新增：alpha<30 → 仅轻仓
```

**权重**: tech=0.35, fund=0.30, alpha=0.25, news=0.10

**config.yaml 新增**:
```yaml
weights:
  tech: 0.35
  fund: 0.30
  alpha: 0.25
  news: 0.10
```

### 仓位逻辑（四维）

```
tech < 60 → 不建仓
tech >= 60 but alpha < 30 → 仅轻仓（因子排名垫底）
tech >= 60 + alpha >= 30 + fund >= 60 → 重仓/正常
tech >= 60 + alpha >= 30 + fund < 40 → 轻仓
```

### 与缠论系统的关系

| 维度 | 缠论信号 | Alpha 因子 |
|------|---------|-----------|
| 信号来源 | 分型→笔→线段→中枢→买卖点 | cross-sectional 因子排名 |
| 视角 | 个股自身结构（纵向） | 全市场横截面（横向） |
| 核心能力 | 判断价格处于什么阶段 | 判断在同类中是否突出 |
| 典型场景 | 三买确认+仓位建议 | 候选股中优中选优 |

### 坑点与限制

1. **amount 缺失**: gtja191_163（IC=0.0347）因需成交额数据被跳过。如需解锁，需从东方财富/AKShare 补充 amount 到 DBHub
2. ~~**qlib158 形态因子区分度低**: 8 个形态因子是截面描述统计，rank 后大部分股票落在中位数附近。真正的区分力来自 GTJA 微观结构因子~~ ✅ **2026-05-24 已移除 qlib158，只用 4 个 GTJA 幸存因子**
3. **科创板数据缺失**: 688xxx 股票因上市时间短，DBHub 数据量少，因子回退到中性分 50
4. **候选池时效性**: 当前依赖 .scanner_cache.json（pool_scanner 的缓存），若缓存过期或候选股变化需重新扫描
5. **宽表内存**: 140 只 × 2 年 ≈ 200MB，在可控范围内。全 A 5000 只不建议直接转宽表
6. **单只个股不适用**: alpha_score 是 cross-sectional rank（跨截面排名），必须同时有 N 只股票才能计算。单只股票分析时 alpha 维度无效，退回到原有的 3D 评分(tech+fund+news)。详见 `references/active-gtja-factors-explained.md`

---

## 与原有系统的关系

| 原有系统 | Alpha Zoo 补充点 |
|---------|----------------|
| 缠论多级别递归 | 无缠论；因子可作为缠论信号的过滤器 |
| 基本面四层框架 | 无基本面；因子补充量化/技术维度 |
| 三维评分(tech+fund+news) | 变为四维评分(tech+fund+alpha+news) |
| A500 选股(池扫描→综合评分) | 在池扫描和评分之间插入 Alpha 因子过滤器 |
| DBHub SQLite | 通过 pivot 转宽表适配 |

---

## 参考链接

- GitHub: https://github.com/HKUDS/Vibe-Trading
- PyPI: `pip install vibe-trading-ai`
- Bench wiki: https://vibetrading.wiki/research-lab/posts/alpha-191-in-2026.html
- 独立包路径: ~/work/alpha-zoo/
- chanlun_core 集成: ~/work/chanlun_core/alpha_factor_filter.py
- 4个GTJA因子详解: `references/active-gtja-factors-explained.md`
