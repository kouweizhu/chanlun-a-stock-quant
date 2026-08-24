# Alpha Zoo 因子系统（已合并至 a500-multi-factor-selection）

## 来源

从 HKUDS/Vibe-Trading（MIT License）抽离，只取了核心部分：
- 19 个基础算子（纯 pandas/numpy，零外部依赖）
- 4 个 GTJA 幸存因子（CSI300 2018-2025 验证存活的 top 4）

## 代码位置

`~/work/alpha-zoo/`

| 文件 | 说明 |
|------|------|
| `base.py` | 19 个算子：rank, scale, ts_rank, ts_corr, ts_cov, ts_mean, ts_std, ts_max, ts_min, ts_argmax, ts_argmin, delta, decay_linear, signed_power, safe_div, vwap |
| `zoo.py` | 4 个 GTJA 因子 compute 函数 + FACTORS 注册表 |
| `dbhub_panel.py` | DBHub SQLite → 宽表 panel 适配 |

## 4 个 GTJA 幸存因子

| 因子 | IC | IR | 数据需求 |
|------|----|----|---------|
| gtja191_171 | 0.0432 | 0.2690 | o/h/l/c |
| gtja191_111 | 0.0349 | 0.2232 | o/h/l/c/vol |
| gtja191_054 | 0.0272 | 0.1606 | c/o |
| gtja191_002 | 0.0262 | 0.1619 | c/h/l |

## Pitfalls

- qlib158 形态因子已排除（截面描述统计，无单因子预测力，拉不开差距）
- 需 amount 列的因子（gtja191_163）跳过，DBHub 无成交额
- 单只股票无法计算 alpha_score（需要跨截面 rank）
