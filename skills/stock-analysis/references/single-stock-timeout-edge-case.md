# single_stock_analysis.py 超时边缘案例

> 本文件记录 `single_stock_analysis.py --report` 在复杂股票上超时的案例，用于指导回退策略选择。

## 案例 A：东方财富 (300059) — 2026-05-15

### 超时表现
- `single_stock_analysis.py --code 300059 --name "东方财富" --report` → 120s timeout, exit_code=124
- 30分钟数据源失败（AkShare EM: `'date' not in index`）
- 日线数据成功获取（3856行）

### 根因分析
1. **买卖点数量过大**：16年历史共产生47个买卖点，JSON序列化+HTML渲染叠加耗时
2. **30分钟数据卡死**：AkShare EM 30min失败后无有效兜底，但Baostock实际可用（后续单独调用成功）
3. **中枢数量过多**：42个中枢遍历影响评分和报告生成速度
4. **HTML生成bug**：`quick_html.py` 报 `strptime() argument 1 must be str, not datetime.date`

### 什么股票容易超时（类型1：数据量过大）
- 上市超过10年（2010年上市）→ 数据量大
- 股性活跃、频繁出现买卖点（券商龙头，波动大、结构多）
- 行业属性导致财务数据计算复杂（券商/金融股收入结构特殊）

### 回退策略实测有效
```bash
# 分别运行各脚本，均能在30s内完成
python quick_chanlun.py 300059          # 成功，JSON输出
python hithink_fundamental.py 300059    # 成功，含Q1数据
python news_detail_report.py --code 300059 --name "东方财富"  # 成功
python check_negative_news.py --stocks 300059 --name "东方财富" --json  # 成功
```

## 案例 B：珀莱雅 (603605) — 2026-06-01

### 超时表现
- `single_stock_analysis.py --code 603605 --name "珀莱雅" --report` → 180s timeout (exit_code=124)
- 30分钟数据源全部失败：Baostock→efinance→AkShare Sina→AkShare EM 均无返回
- 日线数据通过 Baostock→AkShare Sina 成功获取（2070行）
- 下载的 `.source_failed_603605_30min.flag` 文件确认30min数据源耗尽

### 根因分析
1. **30分钟数据全部失败**：不是单一数据源问题，而是所有4个数据源（Baostock/efinance/AkShare Sina/AkShare EM）全部超时/断开。30min数据在 `single_stock_analysis.py` 中是同步阻塞环节，一旦卡死整个进程无法继续
2. **与股票复杂度无关**：珀莱雅仅30个买卖点+18个中枢，远少于300059的47点+42中枢，理论上应在15-30s内完成。超时完全由30min数据拉取阻塞导致
3. **HTML报告已先行生成**：`quick_html.py` 排在 pipeline 早期，即使后续阻塞，HTML（150KB）已成功保存

### 什么股票容易超时（类型2：30min数据源耗尽）
- **任何股票都可能**：与上市年限、买卖点数量无关
- 30min数据缓存过期（>6h）时风险最高—每次都会重新获取，每次都可能失败
- 网络环境不佳时（WSL2下或代理切换时）概率增大

### 恢复策略验证
超时后，已有部分产出可复用：
| 产出 | 状态 | 路径 |
|:----|:----:|:-----|
| HTML缠论报告 | ✅ 已生成 | `reports_html/603605_chanlun.html` (150KB) |
| parquet数据缓存 | ✅ 已缓存 | `data_cache/603605_daily.parquet` |
| 30min源码标志 | ✅ 可读 | `.source_failed_603605_30min.flag` |
| 缠论JSON | ❌ 需补跑 | `quick_chanlun.py 603605` (利用已有缓存在5s内返回) |
| 基本面JSON | ❌ 需补跑 | `hithink_fundamental.py 603605` |
| 消息面JSON | ❌ 需补跑 | `news_detail_report.py --code 603605 --name "珀莱雅"` |
| 负面检查JSON | ❌ 需补跑 | `check_negative_news.py --stocks 603605 --name "珀莱雅" --json` |

补跑总耗时约20s（远快于重试 single_stock_analysis.py）。

## 案例 C：海康威视 (002415) — 2026-06-10

### 超时表现
- `single_stock_analysis.py --code 002415 --name "海康威视" --report` → 180s timeout (exit_code=124)
- 日线缓存HIT（1h old），30min缓存EXPIRED但成功重获（12464行）
- 30min数据源全部成功，无 `.source_failed_*_30min.flag`
- HTML报告**未生成**（超时发生在报告渲染阶段）

### 根因分析
1. **笔数过多（核心根因）**：54笔 + 208个分型。`quick_chanlun.py` 的 JSON 序列化和 `generate_report.py` 的 Jinja2 渲染在大笔数下异常缓慢。与买卖点数量无关（海康仅9个买卖点，远少于东方财富的47个）
2. **分型递归遍历耗时**：208个分型在缠论计算中需要更大规模的顶底处理、特征序列匹配，是超时的独立因素
3. **HTML渲染卡死**：`quick_html.py` 需逐笔绘制54条线段+208个分型标记，加上12个中枢区域，Canvas/SVG渲染密集

### 什么股票容易超时（类型3：笔数/分型数过多）
- **笔数 > 40** 即高风险（与买卖点数量无关）
- **分型数 > 150** 是辅助信号
- 上市超过10年+股价波动频繁（每年产生10+笔）
- 典型特征：`fenxing_count` 远大于 `bi_count` 的4倍（海康208分型/54笔≈3.85倍，比率越高渲染越慢）

### 恢复策略实测有效
| 产出 | 状态 | 备注 |
|:----|:----:|:-----|
| 日线parquet缓存 | ✅ 已有 | 1h old，587行 |
| 30min parquet缓存 | ✅ 已更新 | 12464行 |
| 缠论JSON | ✅ 补跑成功 | `quick_chanlun.py 002415` (利用缓存~5s) |
| 基本面JSON | ✅ 补跑成功 | `hithink_fundamental.py 002415` (~8s) |
| 消息面JSON | ✅ 补跑成功 | `news_detail_report.py --code 002415 --name "海康威视"` (~5s) |
| 负面检查JSON | ✅ 补跑成功 | `check_negative_news.py --stocks 002415 --name "海康威视" --json` (~5s) |
| HTML缠论报告 | ✅ 补跑成功 | `quick_html.py 002415` (~8s) |
| **补跑总计** | **~30s** | 远快于重试 single_stock_analysis.py |

## 超时类型的快速判别

| 信号 | 类型1：数据量过大 | 类型2：30min数据源耗尽 | 类型3：笔数/分型过多 |
|:----|:-----------------:|:---------------------:|:-------------------:|
| 股票上市年限 | >10年 | 不限 | >5年 |
| 买卖点数量 | >40个 | 不限 | 不限（可<10） |
| **笔数** | >40（伴生） | 不限 | **>40（独立风险）** |
| **分型数** | >150（伴生） | 不限 | **>150（独立风险）** |
| 30min flag文件 | 通常无 | ✅ 必有 `.source_failed_*_30min.flag` | 通常无 |
| HTML报告 | 可能失败（生成阶段超时） | ✅ 通常已生成 | **可能失败（渲染卡死）** |
| data_cache | 日线数据很大（>3000行） | 日线数据正常（~2000行） | 日线数据中等（~600行） |

## 快速判断是否该走回退（无需等待超时）

- 股票代码是300/000/002开头且上市>5年 → 高风险（类型1）
- 上次分析该股时 single_stock_analysis.py 耗时>60s → 走回退
- 30分钟数据缓存过期（>6h）→ 30min拉取可能卡死（类型2）

## 超时后避免重复劳动的检查顺序

```
1. ls reports_html/{代码}_chanlun.html   → HTML已生成则跳过 quick_html
2. ls data_cache/{代码}_daily.parquet    → 日线缓存已有则skip
3. ls .source_failed_{代码}_30min.flag   → 30min失败确认，不用重试
4. 补跑 quick_chanlun → hithink_fundamental → news → check_negative
```

## known bugs
- `quick_html.py`: 当 `buy_sell_points` 中的 `date` 字段是 `datetime.date` 类型而非字符串时，`strptime()` 报错。临时方案：跳过HTML生成，不影响评分。
