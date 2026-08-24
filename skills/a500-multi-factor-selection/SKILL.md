---
name: a500-multi-factor-selection
description: A500 多因子选股 Pipeline —— pool_scanner → alpha_factor_filter → composite_scorer(4D) → rescore_news 完整链路。基于缠论买卖点+Alpha因子截面排名+基本面+消息面四维评分，含风控否决层。
version: 4.6.5
author: Hermes Agent
updated: 2026-06-12
created: 2026-05-26
domain: trading
tags:
  - a500
  - scoring
  - alpha-factors
  - veto
  - risk-management
category: trading
triggers:
  - "A500选股"
  - "多因子评分"
  - "四维评分"
  - "pool_scanner.*流程"
  - "alpha.*因子.*选股"
  - "风控否决"
  - "rescore_news"
  - "评分报告.*alpha"
  - "综合评分.*升级"
---

## 📦 前置依赖

在执行本 skill 的任一流程前，请确保以下依赖已就绪：

### Python 环境
- **Python 3.10+**（推荐 3.11）
- 依赖包（通过 pip 安装）：
  ```bash
  pip install pandas numpy akshare baostock tavily-python pyyaml matplotlib pyarrow fastparquet
  ```
  
  > **⚠️ pyarrow/fastparquet 缺失后果**：DataManager 的 parquet 缓存引擎无法写入，`data_cache/` 目录下所有 K 线缓存文件失效。每次运行 Phase 2+3 时，45-168 只候选股的每只需要重新从 Baostock 拉取日线 + 30分钟线，导致 Phase 2+3 从 ~12-15 min 延长至 ~34 min。实盘运行前务必确认已安装。

### API Keys
- **Tavily API Key**（消息面评分）：设置环境变量 `TAVILY_API_KEY`，或在 `config.yaml` 中配置
- **同花顺 AKShare**：无需额外 key，但需稳定的国内网络环境

### 数据库路径
- **DBHub K 线库**：`/home/zjj1990/work/chanlun_core/data_cache/chanlun_klines.db`（SQLite，需含 `kline_daily` 表）
- **Phase 2 中间结果**：`/home/zjj1990/work/chanlun_core/.phase2_results.json`
- **输出报告目录**：`/home/zjj1990/work/chanlun_core/reports_md/`（MD 报告）和 `reports_html/`（HTML 报告）

### 工作目录
- 所有命令必须在 `/home/zjj1990/work/chanlun_core` 下执行
- PYTHONPATH 需包含 `/home/zjj1990/work/alpha-zoo`

# A500 多因子选股 Pipeline Skill

## 定位

本 skill 覆盖 A500 成分股从技术面扫描到最终评分报告的全链路。基于缠论买卖点检测（pool_scanner）+ Alpha 因子截面排名（alpha_factor_filter）+ 四维综合评分（composite_scorer 4D）+ 消息面补扫（rescore_news），最终生成带 Alpha 列和风控标记的 MD 报告。

**最近审计**：2026-05-31 v4.0 深度审计，修复3个P0+4个P1（三买结构评分反转、divergence_threshold下调、潜一买代理映射、二买回调惩罚、三买形成中上限等），详见审计报告 `/home/zjj1990/work/chanlun_core/审计报告_2026-05-31_全面深度审计.md`。

## 架构总览

```
A500 Pool (510只)
    ↓
pool_scanner.py  ← 缠论日线扫描，检测一二三类买点/盘整底背驰
    ↓
.phase2_results.json  ← 技术分+基本面分+消息分 (3D原始)
    ↓
alpha_factor_filter.py  ← 从 DBHub 加载 OHLCV panel
    │                      跑4个GTJA幸存因子
    │                      cross-sectional rank → alpha_score [0,100]
    │                      风控检查：人工黑名单/ST/名称检查
    ↓
merge_into_phase2()  ← 将 alpha_score + 风控信息写回 phase2_results
    ↓
rescore_news.py  ← 对 Top 30 补扫消息面
    │              重算四维综合分（含 veto）
    ↓
MD 评分报告  ← 含 Alpha 列 + ⛔/⚠ 风控标记
```

## 四维评分权重（当前配置）

| 维度 | 权重 | 数据来源 | 说明 |
|------|------|---------|------|
| tech (技术面) | 0.35 | pool_scanner / validate_tech_score | 缠论买卖点评分 |
| fund (基本面) | 0.30 | AKShare / Baostock 财报 | ROE/利润率/负债率等 |
| **alpha** (因子排名) | **0.25** | alpha_factor_filter ← DBHub K线 | 4个 GTJA 幸存因子的截面排名 |
| news (消息面) | 0.10 | 全量采集（东财/涨停池/雪球/同花顺/新浪/CCTV/Tavily）+ LLM语义分析（占位） | 多源加权融合评分 + LLM语义评分，detail 含每条消息的源标签+倾向+标题摘要 |

权重定义在 `config.yaml` 的 `weights` 段，通过 `config_loader.py` 读取。

## 核心文件

### Alpha Zoo 因子包 (`~/work/alpha-zoo/`)

| 文件 | 职责 |
|------|------|
| `base.py` | 19 个基础算子（rank/scale/ts_corr/ts_mean/delta/vwap 等），纯 pandas/numpy |
| `dbhub_panel.py` | DBHub SQLite 长表 → 宽表 panel 适配器 |
| `zoo.py` | 4 个 GTJA 幸存因子的 compute 函数 + FACTORS 注册表 |

### ChanLun 系统集成 (`~/work/chanlun_core/`)

| 文件 | 职责 |
|------|------|
| `pool_scanner.py` | A500 缠论扫描 + Phase 2 评分（3D）。compute_3d_score 调用已更新为 4D+veto |
| `alpha_factor_filter.py` | Alpha 因子引擎：从 DBHub 加载 panel → 4 因子截面排名 → 风控检查 → merge phase2_results |
| `composite_scorer.py` | 四维综合评分 + Veto 否决层。`apply_veto()` 两级否决机制 |
| `rescore_news.py` | 消息面补扫 + 生成 MD 评分报告。表格含 Alpha 列和风控标记。详见 `references/news-scanning-architecture.md` |
| `news_scanner.py` | **共享消息面扫描引擎**（多维数据源采集 + 关键词评分 + LLM 占位）。pool_screener.py 和 news_detail_report.py 均委托此模块。详见 `news-scanner-architecture` skill |
| `config_loader.py` | 系统配置。新增 W_ALPHA/ALPHA_BUY_THRESHOLD/VETO_KEYWORDS/SEVERE_KEYWORDS |
| `config.yaml` | 用户配置。含 weights/alpha_factor/veto_keywords/severe_keywords |
| `risk_filter.py` | 7 项风控检查（可选，AKShare 深度） |

### 参考文档

| 文件 | 内容 |
|------|------|
| `references/news-scanning-architecture.md` | 消息面扫描完整架构、数据源清单、评分逻辑、关键词列表 |
| `references/eastmoney-news-api.md` | 东方财富新闻 JSONP API 参考（绕过 akshare pyarrow 问题的直接调用方式） |

## 4 个 GTJA 幸存因子

来自国泰君安 2014 年《191 个短周期交易型 alpha 因子》，经 Vibe-Trading 在 CSI300 2018-2025 验证存活：

| 因子 ID | Mean IC | IR | 主题 | 所需列 |
|---------|---------|----|------|-------|
| gtja191_171 | **0.0432** | 0.2690 | 微观结构/形态 | o/h/l/c |
| gtja191_111 | 0.0349 | 0.2232 | 量价结构 | o/h/l/c/vol |
| gtja191_002 | 0.0262 | 0.1619 | 反转/日内位置变化 | c/h/l |
| gtja191_054 | 0.0272 | 0.1606 | 波动率形态 | c/o |

各因子详细说明见 `D:\常用文件\Alpha_Zoo_因子系统说明.md`

## Veto 风控否决层

两步检查，在 `compute_3d_score()` 内自动执行：

### 一票否决（Veto → grade=D, position=0）
关键词（16 个，可配置于 config.yaml）：
- 立案调查、被立案、证监会调查、财务造假
- \*ST、退市风险警示
- 非标审计、保留意见、无法表示意见
- 涉嫌、违法违规、操纵市场

### 严重降级（Severe → 扣 20 分, 最多轻仓）
关键词（12 个，可配置于 config.yaml）：
- 行政处罚、公开谴责、纪律处分
- 减持计划、大股东减持
- 业绩预告变脸、由盈转亏
- 监管措施、问询函

## 一键全流程（推荐）

```bash
cd /home/zjj1990/work/chanlun_core
HOME=/home/zjj1990 PYTHONPATH=/home/zjj1990/work/alpha-zoo \\
  python3 run_full_4d_pipeline.py
```

脚本步骤：清理旧报告 → Phase 1（缓存命中则跳过） → Phase 2+3（评分+HTML+MD，analyzers 在内存中） → Alpha因子过滤 → 四维重算+覆盖MD/Excel。缓存命中时约 **17-22 分钟**（Phase 1 省掉），缓存过期时约 **40-45 分钟**。

**⚠️ Phase 1 超时风险**：wrapper 脚本内部对 Phase 1 设置了 `subprocess.run(timeout=600)`（10分钟）。当缓存过期/缺失时（所有 510 只股票需从 Baostock 重新拉取日线数据），Phase 1 可能超过 600s 导致超时退出。实测直接单独运行 `python3 pool_scanner.py` 在同一环境下仅需 ~231s（~4分钟），但通过子进程运行时环境差异可能导致速度变慢。**修复方式**：单独跑 `python3 pool_scanner.py`（无时间限制），确认完成后继续一键脚本的后续步骤或手动执行 Phase 2+3+Alpha+4。如频繁遇到，可修改 `run_full_4d_pipeline.py` 第13行的 `timeout=600` 提升到 `timeout=1200`。

**注意事项**：
- 四维重算阶段（`resocre_with_alpha()`）从 JSON 加载数据，无内存 analyzer，只能生成 MD 无法生成 HTML。Phase 2+3 已生成的 HTML 文件会保留不受影响
- 如果四维重算后 Top 30 发生变化（新股票进入），新入围者缺少 HTML，需手动补打（见下方 Step 4b）
- 脚本路径：`/home/zjj1990/work/chanlun_core/run_full_4d_pipeline.py`
- 输出标记：`/home/zjj1990/work/chanlun_core/signals/a500_scan_done_*.flag`

## 运行顺序（分步手动版）

```bash
cd /home/zjj1990/work/chanlun_core
# 注意：所有命令必须在 /home/zjj1990/work/chanlun_core 下执行，
# 而不是 ~/work/chanlun_core — 因为 Hermes background 进程的 ~ 展开
# 可能被 profile 改写指向 ~/.hermes/profiles/commander/home/

# ── Step 1: 缠论扫描 A500 全池 ──
# 510只成分股 → 123只候选(score≥3)
python3 pool_scanner.py
# ✅ 验证：检查 .phase2_results.json 是否生成且有候选股票: python3 -c "import json; d=json.load(open('.phase2_results.json')); print(f'候选 {len(d)} 只'); assert len(d)>0"

# ── Step 2: 3D 综合评分 + 报告生成 ──
# 对候选做技术面+基本面+消息面三维评分，TOP30生成HTML+MD报告
# 注意：此时 alpha=50（中性），第4维宽度尚未激活
python3 pool_screener.py --from-cache
# ✅ 验证：检查 reports_md/ 下生成了扫描汇总_*.md，且内容含"技术"和"基本面"列:
md_file=$(ls reports_md/扫描汇总_*.md 2>/dev/null | head -1)
if [ -n "$md_file" ] && head -20 "$md_file" | grep -q '技术' && head -20 "$md_file" | grep -q '基本面'; then
  echo "✅ MD 报告已生成，含技术/基本面列"
  # 同时检查候选数范围是否合理
  cand_count=$(python3 -c "import json; d=json.load(open('.phase2_results.json')); print(len(d))" 2>/dev/null)
  if [ "$cand_count" -gt 0 ] 2>/dev/null && [ "$cand_count" -lt 300 ] 2>/dev/null; then
    echo "✅ 候选数量 $cand_count 在合理范围 (1~300)"
  else
    echo "⚠️ 候选数量异常: $cand_count"
  fi
else
  echo '❌ 未找到完整 MD 报告'
fi

# ── Step 3: Alpha 因子截面排名 ──
# 从 DBHub 读 K 线 panel，跑4个 GTJA 幸存因子 → alpha_score [0,100]
# 合并回 .phase2_results.json
# ⚠️ 必须在 background 进程中给 HOME 和 PYTHONPATH 覆盖：
HOME=/home/zjj1990 PYTHONPATH=/home/zjj1990/work/alpha-zoo \
  python3 alpha_factor_filter.py
# ✅ 验证：检查 .phase2_results.json 中 Top3 股票的 alpha_score 在 [0,100] 范围:
python3 -c "
import json
d=json.load(open('.phase2_results.json'))
d.sort(key=lambda x:-x.get('alpha_score',0))
print('Top3:')
for s in d[:3]:
    a = s.get('alpha_score',50)
    code = s['code']
    name = s.get('name','')
    assert 0 <= a <= 100, f'{code} alpha_score={a} 超出[0,100]'
    print(f'  {code} {name}: alpha={a:.1f}')
print('✅ 所有 alpha_score 在合法范围 [0,100]')
"

# ── Step 4: 重算四维综合分 + 重生成报告 ──
# 从 .phase2_results.json 读入（含 alpha_score），
# 用 W_TECH/W_FUND/W_ALPHA/W_NEWS 权重重算 composite，
# 生成含 Alpha 列的汇总报告
# ⚠️ 此步骤从 JSON 加载数据，无内存 analyzer 对象，只能生成 MD 不能生成 HTML
python3 -c "
import sys, json, os
sys.path.insert(0, '.')
from pool_screener import generate_reports
from composite_scorer import compute_3d_score
from config_loader import W_TECH, W_FUND, W_ALPHA, W_NEWS

with open('.phase2_results.json') as f:
    scored = json.load(f)
for s in scored:
    r = compute_3d_score(
        tech_score=s.get('tech_score',50),
        fund_score=s.get('fund_score',50),
        alpha_score=s.get('alpha_score',50),
        news_score=s.get('news_score',50),
        w_tech=W_TECH, w_fund=W_FUND, w_alpha=W_ALPHA, w_news=W_NEWS,
        code=s['code'], name=s['name'],
        news_detail=s.get('news_detail',''),
        resonance_penalty=True)
    s['composite'] = r.composite
    s['grade'] = r.grade
    s['can_buy'] = r.can_buy
    s['position'] = r.position
scored.sort(key=lambda s: -s['composite'])
print('四维评分完成，Top 3:', scored[0]['name'], scored[1]['name'], scored[2]['name'])
generate_reports(scored)
"
# ✅ 验证：检查 reports_md/ 下新生成的扫描汇总_*.md 包含 Alpha 列，且 composite 在合理范围:
md_file=$(ls reports_md/扫描汇总_*.md 2>/dev/null | head -1)
if [ -n "$md_file" ]; then
  if head -5 "$md_file" | grep -q 'Alpha'; then
    echo '✅ 含 Alpha 列(四维生效)'
    # 验证 composite 评分范围 [0,100]
    top_composite=$(python3 -c "
import json
with open('.phase2_results.json') as f:
    scored = json.load(f)
scored.sort(key=lambda s: -s.get('composite',0))
if scored:
    print(f\"{scored[0].get('composite',0):.1f}\")" 2>/dev/null)
    if [ -n "$top_composite" ] && python3 -c "assert 0 <= $top_composite <= 100" 2>/dev/null; then
      echo "✅ Top1 composite=$top_composite 在合法范围 [0,100]"
    else
      echo "⚠️ Top1 composite=$top_composite 超出预期范围"
    fi
    # 检查候选数
    python3 -c "import json; d=json.load(open('.phase2_results.json')); print(f'✅ 共 {len(d)} 只候选完成四维评分')" 2>/dev/null
  else
    echo '⚠️ 无 Alpha 列(仅三维)'
  fi
else
  echo '❌ 未找到 MD 报告'
fi

# ── Step 4b (可选): 修补 HTML 缠论报告 ──
# Step 4 无法生成 HTML。如果 Top 30 股票中有些没有 HTML 文件，
# 用 quick_html.py 生成并复制到输出目录：
#   cd /home/zjj1990/work/chanlun_core
#   python3 quick_html.py <code1> <code2> ...
#   然后 for c in ...; do cp reports_html/${c}_chanlun.html /mnt/d/常用文件/股票池推荐股/*${c}*/; done
# ✅ 验证：检查 reports_html/ 下目标 HTML 是否生成: for c in 601318 002432 603986; do [ -s reports_html/${c}_chanlun.html ] && echo "✅ $c" || echo "❌ $c"; done

Step 3+4 也可以合并为一个后台步骤（先等 alpha_factor_filter 完成）。

## 注意事项

1. **Alpha 因子是截面排名**：必须同时有多只股票才能计算。单只个股分析时 alpha 维度不可用
2. **DBHub 数据依赖**：alpha_factor_filter 需要 DBHub SQLite K 线库（`/home/zjj1990/work/chanlun_core/data_cache/chanlun_klines.db`），需含 kline_daily 表，字段 stock_code/date/open/high/low/close/volume
3. **股票代码格式**：6 位无后缀（如 300750），pool_scanner 和 DBHub 保持一致
4. **科创板（688xxx）**：数据量较少时因子退回到 NaN，获得中性分 50
5. **amount 缺失**：gtja191_163 因子因需要成交额列被排除。如需启用，需补充 amount 字段
6. **veto 关键词不区分大小写**，写入 config.yaml 后无需重启
7. **qlib158 形态因子已排除**：8 个 K线形态因子（kup/kup2/kmid/kmid2/klow/klow2/ksft/ksft2）因无单因子预测力，不在 ACTIVE_FACTORS 中
8. **消息面未跑通的标注规范**：扫描新闻时若数据源全部失败（news_detail 含异常/降级提示），news_score 保持 50 中性分，但**必须在 MD 报告的消息面摘要区如实标注**（如"数据源异常，消息面评分不可用"），不得静默跳过。若个股新闻采集全部失败，应在汇总表的「📰 消息面摘要」列注明"⚠️ 采集失败"
9. **报告必须用四维重算后数据生成**：Step 4 完成后必须从 `.phase2_results.json` 读取最新四维评分数据重新生成 MD/Excel，不得复用 Phase 2+3 的旧报告（详细分析见 P1 bug 条目）
10. **消息面补扫范围**：Phase 2+3 仅对 3D 排名的 Top 30 补扫消息面，加入 Alpha 后排名会洗牌。Step 4 重新生成报告时，对 news_score=50 且 news_detail 为"跳过(非Top30)"的股票，应在报告中标注"⚠️ 未补扫消息面（Alpha 重排后新入围）"

## 买点类型速查

推荐列表中常见买点类型的评分和优先级：

| 买点类型 | score | 平均综合分(实跑) | 说明 |
|----------|-------|------------------|------|
| 标准一买 | 5 | ~68.8 | 趋势背驰+确认，最可靠 |
| 标准二买 | 5 | — | 极少出现（需一买后4-8周回调） |
| 标准三买 | 5 | ~62.4 | 中枢突破后回踩不破ZG |
| 类一买(盘整底背驰) | 3-4 | ~68.8 | 盘整中MACD衰减，较可靠 |
| 反转后三买 | 3 | ~65.0 | 趋势反转后新中枢的三买 |
| **类二买(反转后)** | **3** | **~65.4** | **2026-05-30 已禁用** — 噪点太多，趋势判断过于机械 |
| 三买形成中 | 3-5 | ~65.0 | 突破ZG后回踩未成笔 |
| 中枢下沿机会 | 2 | — | 结构位置，非买点 |

## 🛑 反例与黑名单

以下是被系统明确排除或应避免的股票/模式/操作案例。这些反例来自实际运行中的失败记录和审计结论。

### ❌ 黑名单股票特征

| 特征 | 触发条件 | 处理方式 | 实例 |
|------|---------|---------|------|
| ST / *ST | 股票名称含 ST 标记 | Veto 否决 | — |
| 立案调查/财务造假 | 新闻中匹配 VETO_KEYWORDS | Veto 否决（grade=D） | — |
| 行政处罚/公开谴责 | 新闻中匹配 SEVERE_KEYWORDS | 严重降级（扣20分） | — |
| 人工黑名单 | config.yaml 中 blacklist 段配置 | alpha_score=0，风控标记 | — |

### ❌ 已禁用的选股策略

| 策略 | 禁用版本 | 禁用原因 | 参考 |
|------|---------|---------|------|
| **类二买（反转后）** | v4.1 (2026-05-30) | 噪点太多（曾占推荐列表 10/30 只），趋势判断过于机械。以三七互娱(002555)为例，从30.41元跌至19.39元（-36.23%），系统仍误判为"趋势反转"。 | `references/lei-er-mai-disabled-reasoning.md` |
7. **qlib158 形态因子已排除**：8 个 K线形态因子（kup/kup2/kmid/kmid2/klow/klow2/ksft/ksft2）因无单因子预测力，不在 ACTIVE_FACTORS 中
8. **消息面未跑通的标注规范**：扫描新闻时若数据源全部失败（news_detail 含异常/降级提示），news_score 保持 50 中性分，但**必须在 MD 报告的消息面摘要区如实标注**（如"数据源异常，消息面评分不可用"），不得静默跳过。若个股新闻采集全部失败，应在汇总表的「📰 消息面摘要」列注明"⚠️ 采集失败"
9. **报告必须用四维重算后数据生成**：Step 4 完成后必须从 `.phase2_results.json` 读取最新四维评分数据重新生成 MD/Excel，不得复用 Phase 2+3 的旧报告（详细分析见 P1 bug 条目）
10. **消息面补扫范围**：Phase 2+3 仅对 3D 排名的 Top 30 补扫消息面，加入 Alpha 后排名会洗牌。Step 4 重新生成报告时，对 news_score=50 且 news_detail 为"跳过(非Top30)"的股票，应在报告中标注"⚠️ 未补扫消息面（Alpha 重排后新入围）"

### ❌ 应避免的操作陷阱

| 陷阱 | 说明 | 正确做法 |
|------|------|---------|
| **单只个股跑 Alpha 因子** | Alpha 是截面排名，单只无法计算排名百分位 | 必须同时有 ≥2 只候选股票 |
| **Step 4 直接期待 HTML** | 四维重算从 JSON 加载，无 analyzer 对象无法生成 HTML | 先用 quick_html.py 补打，或用 --from-cache 重跑 Phase 2+3 |
| **忽略 HOME 覆盖** | Hermes background 进程改写 HOME → profile 路径 | 执行时加 `HOME=/home/zjj1990` |
| **跳过 Step 2 直接跑 Step 3** | Step 3 依赖 .phase2_results.json 必须已存在 | 按顺序执行 Step 1→2→3→4 |
| **权重修改后不重启** | 修改 config.yaml 后 config_loader.py 缓存未刷新 | 新开进程自动读最新配置 |
| **科创板 688xxx 默认有效** | 数据量少 → 因子 NaN → 中性分 50 | 需额外关注科创板数据覆盖 |

### ❌ 已知失效/需避免的因子

| 因子 | 问题 | 状态 |
|------|------|------|
| gtja191_163 | 需要 amount（成交额）列，当前 panel 无此字段 | 已排除 |
| qlib158 kup/kup2/kmid/... | 无单因子预测力 | 已排除 |
| 类二买（反转后） | 噪点多，误判率高 | 已禁用 |

## 📊 常见失败模式汇总表

以下汇总了系统运行中已知的失败模式、症状、根因及解决方案，按严重程度排列：

| 优先级 | 失败模式 | 症状 | 根因 | 修复/处理 | 影响范围 |
|--------|---------|------|------|----------|---------|
| **P0** | 共振惩罚方向错误 | 弱tech+弱fund股票反而加分 | `composite_scorer.py` 中 `+=` 应为 `-=` | 已修复: `+=` → `-=` | 评分完全反转 |
| **P0** | 三买结构评分逻辑反转 | 三买在中枢下沿仍得高分 | 结构评分对一买/三买不做区分 | 建议修复: 加 `if point_level==3: structure_score=10` | 三买评分准确度 |
| **P0** | divergence_threshold=1.0 | 9/39只"一买"背驰比>70%属假信号 | 阈值不要求"明显减弱" | 建议改为 0.7，需回测确认 | 候选池质量 |
| **P1** | 类一买(潜在一买)评分未区分 | 类一买获得与确认一买相同评分 | 3处代码未感知 `confirmed` 字段 | 已修复: 4处改动 | buy_type显示+评分 |
| **P1** | 消息面补扫丢失 alpha_score | Phase 3 composite 与 Phase 2 不一致 | `compute_3d_score()` 未传 alpha_score | 已修复 | 评分一致性 |
| **P1** | Path.home() profile 拦截 | 报 No module named 'dbhub_panel' | Hermes 改写 HOME 环境变量 | 执行时覆盖 `HOME=/home/zjj1990` | alpha_factor_filter |
| **P1** | 一买/二买跌破买点无惩罚 | 惠泰医疗(-14.4%)、长春高新(-21.7%)、视源股份(-8.5%)跌破买价后技术分仍>50 | `validate_tech_score.py` 仅有三买 zs_break_penalty，一买/二买无跌破买价检查 | 已修复: 新增 buy_price_penalty 维度 8（一买-30~-10，二买-20~-5）于 validate_tech_score.py | 所有一买/二买候选的评分 |
| **P2** | 三买跌破中枢无惩罚 | 天山铝业现价<中枢下沿仍得100分 | 无 zs_break_penalty | 已修复: -20/-10 分惩罚 | 三买评分精确性 |
| **P2** | 潜一买代理映射缺失 | 少部分潜一买获得"未知类型"评分 | 映射表缺 `'潜一买(等待确认)'` | 建议修复 | 极少数情况 |
| **P3** | stock_announcement_report 不存在 | 每只股票打印模块属性错误 | AKShare 版本移除了该函数 | 异常已捕获，无实质影响 | 运行日志噪音 |
| **P3** | 四维重算无法生成 HTML | 新入围 Top 30 股票缺 HTML 报告 | 从 JSON 加载无 analyzer 对象 | 用 quick_html.py 补打 | 报告完整性 |
| **P3** | 汇总表缺少 Alpha 列 | 汇总表只显示3维 | 3个函数模板未含 Alpha 列 | 已修复: 表头加 Alpha 列 | 报告显示完整性 |
| **P1** | 消息面补扫未跟随 Alpha 重排 | MD 报告中消息面打分全为 50（非 Top30 股票未补扫） | Phase 2+3 补扫仅覆盖 3D 分 Top 30，加入 Alpha 因子后排名洗牌，新入围 Top 26 从未补扫 | 已修复: Step 4 四维重算后必须从 JSON 重新生成 MD/Excel 报告（而非复用 Phase 2+3 旧报告） | 消息面评分失真 + A 级股票被遗漏 |

**严重程度定义**：
- **P0**：评分逻辑错误，直接影响选股质量。必须修复。
- **P1**：流程阻塞或评分偏差 ≥5 分。应及时修复。
- **P2**：特定场景评分偏差 ≤5 分。计划内修复。
- **P3**：展示/文档/性能问题。不影响核心逻辑。

## ⏱ 实际观测运行时间

全流程耗时约 **40-45 分钟**（缓存过期场景），各阶段实测（2026-06-10）：

| 阶段 | 预估 | 实测 | 说明 |
|------|------|:----:|------|
| Phase 1 (pool_scanner) | 3-5 min | **~4 min** | 510只无缓存首次扫描，231s |
| Phase 2+3 (pool_screener) | 12-15 min | **~34 min** | 含30只个股HTML+MD报告生成，每只需Baostock拉数据 |
| Step 3 (alpha_factor_filter) | ~1 min | **~24s** | 纯计算，全截面仅需24s |
| Step 4 (四维重算+报告覆盖) | ~1 min | **~6 min** | generate_reports()重新生成30只报告，纯计算+少量Baostock |
| **总计** | **~17-22 min** | **~40-45 min** | 缓存缺失时显著延长 |

### 候选池规模波动

技能中引用的 ~123-168 只候选来自 2026-05-29 基准测试。2026-06-10 实际仅 **45 只**（score≥3）：

| 评分 | 数量 |
|:----:|:----:|
| 5.0 | 10 |
| 4.5 | 2 |
| 4.0 | 16 |
| 3.5 | 1 |
| 3.0 | 16 |

差异因素：(a) 市场环境变化——买点信号随时间衰减/新生；(b) divergence_threshold=1.0 假一买未修复（见 P0-2），可能同时影响候选数量和质量；(c) 类二买禁用后减少约 10 只。**预期候选池波动范围为 40-170 只**，不应视为固定基准。

## 已知坑 & 修复

### ⚠️ Path.home() 在 Hermes background 进程中被 profile 拦截
- **症状**：`alpha_factor_filter.py` 报 `No module named 'dbhub_panel'` 或 `DBHub 数据库不存在: .../profiles/commander/home/work/...`
- **原因**：Hermes 会改写 `HOME` 环境变量指向 `~/.hermes/profiles/commander/home/`。`Path.home()` 调用返回的是 profile 内的 home，而非真实 home（`/home/zjj1990/`）。
- **波及文件**：`/home/zjj1990/work/alpha-zoo/dbhub_panel.py`（第24行 `Path.home() / "work" / ...`）和 `alpha_factor_filter.py`（第33行 `Path.home() / "work" / "alpha-zoo"`）
- **修复**：执行时覆盖 `HOME=/home/zjj1990`；PYTHONPATH 也需要显式设置为 `/home/zjj1990/work/alpha-zoo`

### ⚠️ AKShare stock_news_em pyarrow 兼容问题（2026-06-12 已修复）
- **症状**：`akshare.stock_news_em(symbol=code)` 抛 `pyarrow.lib.ArrowInvalid: Invalid regular expression: invalid escape sequence: \\\\u`
- **根因**：akshare 内部 `str.replace(r\\\"\\\\u3000\\\", \\\"\\\", regex=True)` 在新版 pyarrow 下报正则转义错误
- **修复**：`scan_news()` 不再调用 `akshare.stock_news_em()`，改用 `requests` 直接调东方财富 JSONP 搜索接口 `https://search-api-web.eastmoney.com/search/jsonp`（akshare 底层同一接口），完全绕过 pyarrow。参数格式：`param={"uid":"","keyword":code,"type":["cmsArticleWebOld"],"client":"web","clientType":"web","clientVersion":"curr","param":{"cmsArticleWebOld":{"searchScope":"default","sort":"default","pageIndex":1,"pageSize":20,"preTag":"<em>","postTag":"</em>"}}}`
- **验证**：东方雨虹(002271) 成功获取 20 条新闻，包含"董事长李卫国因身体原因连续两次未亲自出席董事会会议"等关键消息
- **注意**：此修复是临时方案。升级 akshare 到修复版本后可恢复使用 `akshare.stock_news_em()`

### ⚠️ AKShare HTTP 挂死
- **机制**：AKShare 底层 `urllib` 请求没有 timeout 参数，网络不畅时 `socket.recv()` 无限阻塞。`concurrent.futures.Future.result(timeout=300)` 在 socket 级别无法中断。
- **症状**：进程运行中（PID 存在），但无新 stdout 输出，uptime 持续增长，进度卡在某只股票的 AKShare progress bar 处。
- **修复**（2026-05-29 已应用至 pool_screener.py）：
  - 全局 `socket.setdefaulttimeout(60)` — 任何 socket 请求 60s 无响应即抛异常
  - `_process_batch()` max_workers 从 8 降至 4（8线程并发打崩同花顺接口）
  - 新闻补扫 max_workers 从 8 降至 4

### ⚠️ run_full_4d_pipeline.py Phase 1 超时

- **症状**：一键脚本在 Phase 1（缠论扫描）阶段退出，报 `subprocess.TimeoutExpired: Command 'python3 pool_scanner.py' timed out after 600 seconds`
- **根因**：wrapper 脚本内部对 Phase 1 设硬编码 `subprocess.run(cmd, timeout=600)`。缓存过期/缺失时，510 只股票均需从 Baostock 重新拉取日线数据，Baostock 首次连接 + 全量数据拉取可能超过 600s（10分钟）。实测直接运行 `pool_scanner.py` 在同一环境下仅需 ~231s（~4分钟），说明超时与首次连接额外开销有关（Baostock 会话初始化、网络抖动等）。
- **修复**：不要依赖一键脚本的 Phase 1 超时。改为分步执行：
  1. 先单独跑 `python3 pool_scanner.py`（无时间限制）
  2. 确认完成后，再跑一键脚本的剩余步骤或手动执行 Phase 2+3+Alpha+4
- **预防**：如频繁遇到，可修改 `run_full_4d_pipeline.py` 第13行的 `timeout=600` 提升到 `timeout=1200`

### ⚠️ stock_announcement_report 不存在
- **症状**：`risk_filter.py` 每只股票打印 `module 'akshare' has no attribute 'stock_announcement_report'`
- **原因**：当前安装的 AKShare 版本移除了该函数
- **影响**：无 — 异常被捕获后仅打印警告，筛选逻辑不受影响

### ⚠️ 四维重算（Step 4）无法生成 HTML 缠论报告

- **症状**：Step 4 执行后，新进入 Top 30 的股票目录下缺少 HTML 文件（仅有 MD 报告）。
- **原因**：`generate_reports()` 在 Step 4 中是从 JSON 文件（`.phase2_results.json`）加载数据，没有内存中的 analyzer 对象（analyzer 是 `pool_screener.py` Phase 2+3 运行时创建的 Python 对象）。`generate_reports()` 的 HTML 分支需要 analyzer 来计算缠论买卖点/中枢/笔，从 JSON 加载时没有这些数据。
- **影响范围**：Step 4 重算后，仅有那些**同时在第一版 Phase 2+3 的 Top 30 和四维 Top 30 中都出现**的股票才有 HTML。新入围的股票缺少 HTML。
- **修复**（分两步）：
  1. 批量生成 HTML：`cd /home/zjj1990/work/chanlun_core && python3 quick_html.py <code1> <code2> ...`
  2. 复制到输出目录：
     ```bash
     for c in 601318 002432 603986; do
       dst=$(ls -d /mnt/d/常用文件/股票池推荐股/*${c}*/ 2>/dev/null)
       [ -n "$dst" ] && cp reports_html/${c}_chanlun.html "$dst/" && echo "✅ $c"
     done
     ```
- **注意**：`quick_html.py` 内置了股票名称映射表（见脚本第17-26行），少量股票名称不对应（如002432显示为代码而非"九安医疗"），但不影响 HTML 内容质量。

### ⚠️ 汇总表缺少 Alpha 列（已修复 2026-05-29）

- **症状**：四维重算后，`扫描汇总_*.md` 和 `扫描汇总_*.xlsx` 以及个股 `*_score_report.md` 中均无 Alpha 维度列/行。综合评分是四维的，但汇总表只显示 技术/基本面/消息 三维。
- **原因**：`pool_screener.py` 中 `generate_summary_md()`、`generate_summary_excel()`、`generate_md_report()` 三个函数的模板只有 3 个维度，没有包含 Alpha 列。
- **修复位置**（`pool_screener.py`）：
  - `generate_summary_md()`：表头从 `| 技术 | 基本面 | 消息 |` → `| 技术 | 基本面 | Alpha | 消息 |`，数据行加 `{s.get('alpha_score', 50):.1f}`
  - `generate_summary_excel()`：headers 插入 `'Alpha分'`，两处数据行（Top10 + 全部候选）加 `s.get('alpha_score', 50)`，列宽数组加对应宽度
  - `generate_md_report()`：评分表加 `| Alpha因子 | {s.get('alpha_score', 50):.1f} | {W_ALPHA*100:.0f}% | ... |` 行
  - 标题/脚注从"三维"→"四维"
- **注意事项**：3 个函数都在同一文件中，每次改完要跑一次语法检查。`generate_summary_excel()` 有两个数据段（Top10 sheet + 全部候选 sheet），代码结构相同，patch 时用 `replace_all=True` 一次改两处。`generate_reports()` 是总入口，调用上述三个函数。

### ⚠️ P1：报告生成顺序错误 — 消息面全 50 + A 级遗漏（2026-07-29 发现并修复）

**症状**：MD 汇总表中所有 news_score = 50.0，且无 A 级股票。但 JSON 数据中 30 只有真实消息面评分（42~64），且有 3 只 A 级。

**根因**（严重）：Phase 2+3 生成的 MD 报告是**3D 评分版本**（不含 Alpha 因子），消息面补扫仅覆盖当时 3D 排名的 Top 30。加入 Alpha 因子后综合分重新洗牌，新入围的股票全是未补扫的（news=50），且 A 级股票被排到后面无法显示。Step 4 四维重算只更新了 JSON 数据，但**没有重新生成 MD/Excel 报告**（或重新生成时复用了旧排序）。

**修复**：Step 4 完成后**必须**从 JSON 重新读取最新四维评分数据，调用 `generate_summary_md()` + `generate_summary_excel()` 重新生成报告。关键代码：
```python
# 正确做法：Step 4 后从 JSON 重新生成
with open('.phase2_results.json') as f:
    scored = json.load(f)
scored.sort(key=lambda s: -s['composite'])
generate_summary_md(scored)    # 用四维重算后的 composite/grade/news 覆盖
generate_summary_excel(scored) # 同上
```

**验证**：重新生成后的 MD 报告应满足：
- Top 1 的 news_score ≠ 50（说明消息面补扫生效）
- 存在 A 级股票（composite ≥ 70）
- Alpha 列数值显示正确

**与 "消息面补扫丢失 alpha_score" 的关系**：该 bug 是 Phase 3 内部 alpha 传参缺失。本 bug 是 Step 4 完成后报告未重新生成的流程级错误。两者独立但叠加后导致 MD 报告完全不可信。

### ⚠️ patch 工具多行字符串陷阱

- **风险**：用 `patch` 工具修改 `pool_screener.py` 的多行字符串（如 lines 数组的列表项）时，`\\n` 字符会被解释器视为字面量而非换行符，导致文件中出现 `\"...\",\\n        \"...\"` 这样的单行内容，引发 SyntaxError。
- **症状**：语法检查报 `unexpected character after line continuation character`
- **修复**：用 `python3 -c` 读取文件二进制，精确定位 `\\n` 替换为真正的 `\n`：
  ```python
  with open('/path/file.py', 'rb') as f:
      content = f.read()
  content = content.replace(b'\\\\n', b'\n')
  with open('/path/file.py', 'wb') as f:
      f.write(content)
  ```
- **预防**：对于 Python 列表中的多行字符串，确保 patch 的 old_string/new_string 不包含 `\\n` 字面量。如果 patch 输出了 \"unexpected character\" 错误，直接用 `write_file` 重写整段更可靠。

### ⚠️ 共振惩罚方向错误（2026-05-29发现并修复）

**症状**：弱tech+弱fund的股票composite反而高于基础分。

**根因**：`composite_scorer.py:238` 的 `composite += penalty * 0.5` 应为 `-=`。

**修复**：`+=` → `-=`。验证：tech=40, fund=40 → composite从50.0降至37.0。

### ⚠️ 消息面补扫丢失alpha_score（2026-05-29发现并修复）

**症状**：Phase 3 消息面更新后，composite与Phase 2不一致。

**根因**：`pool_screener.py:571-577` 的 `compute_3d_score()` 调用未传 `alpha_score` 和 `w_alpha`，alpha被重置为50中性。

**修复**：补传 `alpha_score=s.get('alpha_score', 50.0)` 和 `w_alpha=W_ALPHA`。

### ⚠️ 类一买代理confirmed判断错误（2026-05-29发现并修复）

**症状**：盘整底背驰在Phase 2重建分析器后获得与标准一买相同的高评分。

**根因**：`pool_screener.py:698` 用 `!= '类一买'` 精确匹配，但实际buy_type是 `"类一买(盘整底背驰)"`。

**修复**：`!= '类一买'` → `'类一买' not in c.get('buy_type', '')`。

### ⚠️ 三买技术评分100分的三个隐藏 bug（2026-05-29 发现，当日修复）

**症状**：天山铝业(002532) 评级流程：扫描模式为"反转后三买"（非标准买点），当前价 ¥15.06 已跌破中枢[5]下沿 ¥17.90，三买实质已失效。但技术评分给了 **100 分**，报告无任何警告。

详见参考文件 `references/tianshan-aluminum-bug-session-2026-05-29.md`（含完整排查记录）和 `references/three-bug-fixes-2026-05-29.md`（含修复代码）。

**Bug 1：非标准买点 fallback 满分**（`pool_screener.py` Phase 2）

- **机制**：Phase 2 扫描 `analyzer.buy_sell_points` 找120天内标准买点。对于"反转后三买"（来自 `_detect_post_reversal_buy()`），没有对应 buy_point 对象 → `recent_buy = None` → `compute_technical_score(analyzer, None, None)` 因 `buy_point` 是 None 报 AttributeError → **静默 fallback** 到 `c['score'] * 20 = 5 × 20 = 100`。
- **修复**：在 `recent_buy = None` 后插入 scanner 缓存买点代理逻辑（`SimpleNamespace` 构造，使用 scanner 缓存中的 `buy_type/buy_price/buy_date`），将买点类型映射为 1/2/3 级传递给评分函数。修复后非标准买点不再 fallback 满分，而是获得真实评分。
- **文件**：`pool_screener.py` 第689-705行

**Bug 2：三买中枢引用错误**（`pool_scanner.py` _detect_post_reversal_buy）

- **机制**：`_detect_post_reversal_buy()` 在找反向中枢（counter_zs）时用 `break` 只取**第一个**匹配中枢。对天山铝业：中枢序列 [0]→[1]→[2]（下跌趋势末）=down_last → [3](ZG=8.66) 是第一个 ZG>down_last.ZG 的中枢 → 取为 counter_zs。但实际上中枢[4](ZG=14.01)和[5](ZG=19.10)是更近期的反向中枢。
- **修复**：去掉 `break`，循环走到底，取**最后一个**（最新的）匹配中枢。修复后 counter_zs 从中枢[3]（ZG=8.66/ZD=7.38）变为中枢[4]/[5]，更贴近当前价格。
- **文件**：`pool_scanner.py` 第98-104行

**Bug 3：跌破中枢无惩罚**（`validate_tech_score.py`）

- **机制**：`compute_technical_score()` 的结构评分中，`entry_price <= latest_zs.zd * 1.03` 给 structure_score = 35（中枢下沿附近买入: 安全边际高）。这个逻辑对一买正确，但对三买完全反向——三买跌回中枢下沿意味着突破失败。
- **修复**：新增第7评分维度 `zs_break_penalty`（-20/-10分磁盘）。仅三买模式触发：当前价 < 最新中枢ZD×0.98 → -20分（结构已坏），当前价 < 最新中枢ZD → -10分（逼近警戒）。修复后天山铝业现价¥15.06 < 中枢[5]ZD=17.90×0.98 → 触发-20分惩罚。
- **文件**：`validate_tech_score.py` 第320-343行

**紧急程度**：三处已全部修复，下一轮全流程自动生效。

**验证效果**（2026-05-29 重新运行全流程后，旧缓存已清空）：

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| Top1 | 天山铝业(002532) 91分 ✗ | 艾力斯(688578) 86分 ✅ |
| Top2 | 云铝股份(000807) 89分 ✗ | 恒瑞医药(600276) 83分 ✅ |
| A级(≥70) 数量 | 16只 | 24只 |
| 全流程耗时 | 713s | 780s ✅ |

天山铝业修复后技术分 ≈ 43 分(C+级)，三买结构已坏(跌破中枢[5]ZD=17.90)得到正确反映。

### ⚠️ 一买/二买跌破买价无惩罚（2026-06-10 发现并修复）

**症状**：惠泰医疗(688617) 二买@¥227.46，现价¥194.60（-14.4%）仍得技术分70/A级；长春高新(000661) 二买@¥84.58，现价¥66.21（-21.7%）仍得技术分74/A级。

**根因**：`validate_tech_score.py` 的 `compute_technical_score()` 有7个评分维度，其中维度6（`zs_break_penalty`）仅对**三买**做「跌破中枢ZD」检查（见第345-368行），一买/二买完全无「跌破买点」的保护。评分引擎不知道买点已失效。

**影响范围**：所有一买/二买候选。此漏洞导致候选股价跌破买点后仍获得高评分。

**修复**（2026-06-10）：在 `zs_break_penalty` 后新增维度8 `buy_price_penalty`，在 `validate_tech_score.py` 第369-413行：

| 场景 | 阈值 | 扣分 | 理由 |
|------|:----:|:----:|------|
| 一买跌破 >10% | >10% | -30 | 底部完全没撑住 |
| 一买跌破 >5% | 5-10% | -20 | 趋势反转存疑 |
| 一买跌破 >2% | 2-5% | -10 | 轻微跌破 |
| 二买跌破 >10% | >10% | -20 | 回调严重过深 |
| 二买跌破 >5% | 5-10% | -15 | 回调幅度超预期 |
| 二买跌破 >2% | 2-5% | -10 | 回调偏深 |
| ≤2% | <2% | -5 | 噪声范围，买点边缘 |

**修复验证**（2026-06-10 用实盘数据）：

| 股票 | 旧技术分 | 跌买价 | 惩罚 | 新技术分 | 评级变化 |
|------|:-------:|:------:|:----:|:-------:|:--------:|
| 长春高新 | 74 | -21.7% | -20 | **50** | A → C+ |
| 惠泰医疗 | 70 | -14.4% | -20 | **50** | A → C+ |
| 视源股份 | 52 | -8.5% | -15 | **33** | B → C |
| 华海药业 | 76 | -5.1% | -15 | **55** | A → B |
| 吉祥航空 | 70 | -0.7% | -5 | **65** | A → B+ |

**详细分析**：见 `references/buy-price-breach-penalty-analysis.md`

v3.5.5 新增 `BuySellPoint.confirmed` 字段后，HTML 已正确显示"类一买"（潜在一买），但汇总 MD/XLSX 仍显示"一买"且评分无区别。

**表现**：汇总表买点类型列显示"一买"但 HTML 显示"类一买"，类一买获得与确认一买相同的技术评分。

**根因**：三处代码未同步感知 `confirmed` 字段：① `pool_scanner.py` 的 `buy_type_str` 映射只看 `level` 不看 `confirmed`；② `validate_tech_score.py` 的 signal_quality 评分对 level=1 无 confirmed 分支；③ `pool_screener.py` 的买点代理映射表缺 `'类一买'`。

**修复**（四处，2026-05-29）：
1. `pool_scanner.py` L518-521：`best_buy.level == 1` 且 `confirmed=False` → `buy_type_str="类一买"`，`best_score -= 1`
2. `pool_scanner.py` L403：`best_pattern` 同步改为 `类一买(近期,X天前)`（汇总MD表"模式"列使用此字段，不修改则显示"一买"）
3. `validate_tech_score.py` L131-155：`confirmed=False` 时 signal_quality 降 8 分（30→22, 28→20, 25→18），`point_type_str = "类一买"`
4. `pool_screener.py` L696-698：映射表加 `'类一买': 1`，`_bp.confirmed = c.get('buy_type','') != '类一买'`

**验证效果**（重跑全流程后）：
- 汇总 MD/XLSX 买点类型列：潜在一买显示"类一买" ✅
- 类一买 tech_score≈76（确认一买≈84）✅
- 两类类一买（潜在一买 vs 盘整底背驰）在汇总表中均有区分 ✅

**注意事项**：修复后需重跑 pool_scanner.py + pool_screener.py --from-cache 才能更新汇总表。

**现象**：Phase 1 扫描报告中出现大量一买信号（39只），用户质疑"一买应该是最难出现的"。

**根因**：`generate_analysis.py:64` 中 `divergence_threshold=1.0`，即离幵段 MACD 面积只要小于进入段就判定为背驰，不要求"明显减弱"。实测 9/39 只一买背驰比 > 70%（近乎平背驰），属假信号。

**影响**：假一买抬高 Phase 1 候选数量，但 Phase 2 的技术评分会修正（计算结构评分而非只看买点类型得分），最终影响是候选池中多出9只弱信号股票。

**建议修复**：`generate_analysis.py:62` 中 `divergence_threshold` 改为 0.7。详见 `chanlun-quant-system` skill 的 `references/divergence-threshold-tuning.md`。

**2026-05-31 审计确认**: 此问题为 P0-2 级——当前 threshold=1.0 导致 9/39 只"一买"的背驰比超过 70%（非实质背驰），稀释候选池质量。建议优先修复但需回测验证。

**A500 筛选受影响的具体股票**（背驰比 > 70%，假一买）：

平安银行(89.1%)、中国平安(87.5%)、南方航空(84.6%)、中国宝安(81.5%)、珀莱雅(80.9%)、上海机场(76.5%)、浦发银行(72.1%)、奥瑞金(71.0%)、宝钢股份(71.0%)

**待处理**：等待用户确认后修改参数并重跑全流程。

### ⚠️ P0-1: 三买结构评分逻辑反转（2026-05-31 审计发现）

**症状**：三买在中枢下沿附近时，技术评分仍给"安全边际高(+15)"的结构评分。

**根因**：`validate_tech_score.py:105-107` 的结构评分对所有买点类型统一处理，
不区分一买/二买/三买。三买在中枢下沿意味着突破失败、结构已坏，不应获得高分。

**缓解**：v1.5 的 zs_break_penalty（第 327 行）对此场景扣 -20 分做补偿，但结构评分先给 +15 再扣 -20，净效果仅 -5，不足以反映结构恶化。

**建议修复**：结构评分段加 `if point_level == 3: structure_score = 10` 分支。
详见 `chanlun-quant-system` skill 的 `references/audit-session-2026-05-31.md`。

### ⚠️ P0-3: 潜一买(等待确认) Phase 2 代理映射缺失（2026-05-31 审计发现）

**症状**：当 Phase 2 分析器无法匹配买点时，潜一买以 level=0 进入评分引擎，获得通用"未知类型"评分。

**根因**：`pool_screener.py:697-698` 的代理映射表缺少 `'潜一买(等待确认)'` 键。

**触发概率**：低（大多数情况 Phase 2 分析器能匹配到同一买点），但一旦触发评分失真。

**建议修复**：映射表添加 `'潜一买(等待确认)': 1`，confirmed 判断改为 `'类一买' not in buy_type and '潜一买' not in buy_type`。

### ⚠️ 深度审计发现的3个P0/P1级bug（2026-05-29）

**Bug A：共振惩罚方向错误** — composite_scorer.py:238
```python
composite += penalty * 0.5  # ← 应为 -=，弱tech+弱fund反而加分
```
影响：评分机制完全反转，弱票被奖励。1行修复。

**Bug B：消息面补扫丢失alpha_score** — pool_screener.py:571-577
`_update_news()` 中 `compute_3d_score()` 未传 `alpha_score=s.get('alpha_score',50)` 和 `w_alpha=W_ALPHA`，导致alpha被重置为50中性。2行修复。

**Bug C：类一买代理confirmed判断错误** — pool_screener.py:698
```python
_bp.confirmed = c.get('buy_type', '') != '类一买'  # buy_type实际是"类一买(盘整底背驰)"，!=永远True
# 应改为：
_bp.confirmed = '类一买' not in c.get('buy_type', '')
```
影响：盘整底背驰通过代理路径获得与标准一买相同评分（signal_score多8分）。1行修复。

详见 `chanlun-quant-system` skill 的 `references/full-system-audit-2026-05-29.md`。

### ⚠️ 第4维（Alpha）默认不激活
- **症状**：跑完 `pool_screener.py` 后，所有股票的 alpha_score=50（中性），`W_ALPHA=0.25` 权重实际作废
- **原因**：`pool_screener.py` 第761行 `alpha_score = c.get("alpha_score", 50.0)` — 从候选字典读 alpha_score，未运行 `alpha_factor_filter.py` 前不存在
- **修复**：必须在 `pool_screener.py` 之后显式执行 Step 3（alpha_factor_filter.py）+ Step 4（四维重算）

### ⚠️ 类二买（反转后类二买）已禁用（2026-05-30）
- **症状**：推荐列表中出现大量类二买股票（10/30只），但实际价格走势与趋势反转判断矛盾
- **典型案例**：三七互娱(002555) 从2026-01-13高点30.41元跌至5月28日19.39元（跌幅-36.23%），系统仍判断为"趋势反转后类二买"
- **根因**：`pool_scanner.py:_detect_post_reversal_buy()` 的趋势判断过于机械——只看中枢ZG/ZD比较，不考虑价格实际走势
  - 中枢2（下跌趋势末端）ZG=11.91
  - 中枢3 ZG=15.95 > 11.91 → 系统认为趋势反转
  - 但实际价格仍在下跌，中枢3/4可能只是下跌趋势中的大级别反弹
- **修复**：注释掉 `pool_scanner.py` 第159-181行的类二买检测代码
- **影响**：类二买信号不再生成，推荐列表中不再出现此类买点
- **回退方案**：如需重新启用，取消注释相关代码即可
- **相关文件**：
  - `pool_scanner.py` 第159-181行（已注释）
  - `pool_screener.py` 第698行（映射表保留，不影响逻辑）
  - `slow_bull_backtest.py` 第128-129行（回测统计，不影响实盘）
- **详细分析**: 见 `references/lei-er-mai-disabled-reasoning.md`

### ✅ P1 已修复 (2026-05-30): 基本面评分从快照升级为5年趋势修正

**状态**：已修复并验证通过。`a500-multi-factor-selection` 评分系统现在包含5年多年度趋势修正。

**改动**：3个文件，0额外API调用：
1. **`akshare_fundamental.py`** — `get_fundamentals_akshare()` 从已有 AKShare DataFrame（116+行）提取5年年报（2021-2025）→ `result["multi_year_data"]`
2. **`quick_fundamental.py`** — 新增 `trend_direction(v2)` + `analyze_trend()` 函数，移植自 `hithink_fundamental.py`；`calculate_fundamental_score()` 新增可选参数 `multi_year_data`，计算 trend_correction(±15) + roe_std + revenue_volatility；无数据时自动回退
3. **`pool_screener.py`** — 评分段传 multi_year_data；候选字典新增趋势字段；`generate_md_report()` 基本面详情段新增「当前快照」+「5年趋势」双表格式

**核心数据流**：
```
ak.stock_financial_abstract_ths → DataFrame(116行)
  → 过滤-12-31年报 → 提取2021-2025数据 → multi_year_data dict
  → analyze_trend() → trend_direction(v2)
    → ROE趋势(±5) + 营收趋势(±3) + 毛利率趋势(±4) + 负债率趋势(±3)
    → overall_score(-15 ~ +15)
  → total_score = base_total + trend_correction
```

**trend_direction(v2) 判定树**：
1. 纯单边 → `持续上升/持续下降` (分数±1)
2. 混合方向 → `震荡上升/震荡下降` (分数±0.5) / `先升后降/先降后升` (分数0)
3. 幅度优先 → 整体变化>5%时幅度决定方向
4. 近期优先 → 最后变化方向

**验证结果**（中炬高新600872）：
- ROE趋势：震荡下降至9.4%，波动较大 (score=-1)
- 营收趋势：先升后降→营收增速放缓 (score=-2)
- 毛利率趋势：先升后降至39.2% (score=-1)
- 负债率趋势：稳定在25.7% (score=0)
- 综合趋势：趋势走弱 (total=-4)
- 最终 fund_score：79（快照83 + 趋势修正-4）
- roe_std=0.220, revenue_volatility=0.101

**报告展示**（score_report.md 基本面详情段）：
```
### 5年趋势
| 指标 | 2021 | 2022 | 2023 | 2024 | 2025 |
|------|:---:|:---:|:---:|:---:|:---:|
| ROE(%) | 17.5 | -17.4 | 44.0 | 18.2 | 9.4 |
| 营收(亿) | 51.2 | 53.4 | 51.4 | 55.2 | 42.0 |
| 毛利率(%) | 34.9 | 31.7 | 32.7 | 39.8 | 39.2 |
| 负债率(%) | 28.2 | 44.3 | 22.6 | 29.9 | 25.7 |

| 项目 | 数值 |
|------|------|
| ROE标准差 | 22.0% |
| 营收波动率 | 10.1% |
| 趋势修正 | -4分 (趋势走弱) |
```

**向下兼容**：Baostock路径（`quick_fundamental.get_fundamentals()`）无 multi_year_data，trend_correction=0，roe_std=None，行为不变。

**注意事项**：
- `trend_direction` 可能返回"震荡下降"/"震荡上升"等混合方向，4个维度的评分分支必须覆盖所有可能方向（`analyze_trend()` 的 if/elif 链已含 else fallback）
- 详细实施记录见 `references/fundamental-trend-correction-plan.md`

## 📖 CHANGELOG

### 4.6.5 (2026-06-12)
- **重构**: `scan_news()` 从 `pool_screener.py` 提取为独立 `news_scanner.py` 模块 — pool_screener.py 和 news_detail_report.py 均委托此模块，A500选股与三维分析共享同一套消息面数据源
- **新增**: `check_negative_news.py` 增加 `--full` 参数 — 使用 `news_scanner` 多数据源扫描（默认仍仅同花顺，保持向后兼容）
- **更新**: `generate_report.py` Jinja2 模板消息面部分 — 适配新 `detail` 字符串格式（数据源汇总 + 消息明细），每条消息标注 `[来源][正/负/中性]`
- **更新**: `stock-analysis/SKILL.md` — 消息面数据源描述更新为与 A500 一致
- **版本**: 4.6.4 → 4.6.5

### 4.6.4 (2026-06-12)
- **修复**: S1 东方财富新闻改用 `requests` 直接调 JSONP 接口 — 绕过 akshare pyarrow 兼容问题（`ArrowInvalid: Invalid regular expression`），不再有降级逻辑
- **新增**: 负面关键词扩充公司治理类（+16个）：缺席、未亲自出席、代为行使、身体原因、高管变动、董事会异常、董事辞职、高管离职、减持计划、大股东减持、控股股东减持、实控人减持、质押、平仓、强制平仓、被动减持、信披违规、信息披露、违规担保、资金占用、被立案、被调查、被处罚、被谴责、被问询、业绩变脸、由盈转亏、商誉减值、资产减值
- **新增**: LLM 语义分析通道占位 — 预留 `_call_llm_sentiment()` 接口 + `LLM_API_ENDPOINT`/`LLM_API_KEY`/`LLM_MODEL` 环境变量，评分公式 `final = 0.4 * keyword + 0.6 * llm`，LLM 不可用时降级纯关键词
- **新增**: detail 字符串标注 LLM 状态（`LLM:未启用` / `LLM:XX.X`）
- **验证**: 东方雨虹(002271) 评分 49.4（之前 52.6），东财新闻正确识别董事长缺席为负面
- **版本**: 4.6.3 → 4.6.4

### 4.6.3 (2026-06-12)
- **新增**: `scan_news()` 消息明细输出 — `detail_str` 包含每条消息的源标签+倾向(正/负/中性/混合)+标题摘要，不再黑盒
- **新增**: `generate_md_report()` 和 `generate_summary_md()` 消息面部分展示具体消息明细
- **新增**: 汇总报告新增「📰 消息面摘要」区块，每只 Top 30 列出数据源+关键消息前5条
- **修复**: `scan_news()` 新增 `import re` — 修复新浪财经 HTML 解析报 `name 're' is not defined`
- **修复**: 东方财富新闻 `stock_news_em` 增加内层 try/except — 降级处理 akshare pyarrow 兼容问题
- **移除**: CHANGELOG 重复条目（4.6.2 重复、4.5 重复）
- **版本**: 4.6.2 → 4.6.3

### 4.6.1 (2026-06-12)
- **修复**: `scan_news()` 新增 `import re` — 修复新浪财经 HTML 解析报 `name 're' is not defined` 错误
- **修复**: 东方财富新闻 `stock_news_em` 增加内层 try/except — 降级处理 akshare pyarrow 兼容问题（`ArrowInvalid: Invalid regular expression`）
- **新增**: SKILL.md 和 news-scanning-architecture.md 补充 akshare pyarrow 兼容问题说明
- **版本**: 4.6 → 4.6.1

### 4.6 (2026-06-12)
- **重构**: `scan_news()` 从 6 级降级链改为全量采集 + 加权融合评分
- **新增数据源**: 东方财富个股新闻(S1)、涨停池情绪(S2)、雪球热搜(S3)、CCTV财经(S7)
- **去掉**: Metaso（原第4级）
- **评分**: 各源独立关键词评分 × 权重加权平均 + 公告偏移合并
- **接口兼容**: 返回值 `(score, detail_str)` 不变，所有调用点无需修改
- **文档**: `references/news-scanning-architecture.md` 重写，`references/news-data-source-optimization.md` 更新为已实施状态
- **版本**: 4.5 → 4.6

### 4.5 (2026-06-10)
- **修复**: ⚠️ 一买/二买跌破买点无惩罚 — 新增第8评分维度 `buy_price_penalty`（一买-30~-10，二买-20~-5），验证通过。长春高新从 74→50 分，惠泰医疗从 70→50 分
- **已知坑**: 从「待修复」→「已修复」，含修复代码位置和实盘验证数据
- **前置依赖**: 新增 pyarrow/fastparquet 安装要求及缺失后果警告（Parquet 缓存失效致 Phase 2+3 从 12min→34min）
- **Phase 1 超时**: 新增一键脚本 `timeout=600` 超时处理指引
- **版本**: 4.4 → 4.5

### 4.4 (2026-06-10)
- **新增**: 「⏱ 实际观测运行时间」节——各阶段实测耗时表（Phase 1: ~4min, Phase 2+3: ~34min, Step3: ~24s, Step4: ~6min），合计 ~40-45 min
- **新增**: 「候选池规模波动」节——实测 45 只（vs 基准 123-168），标注预期波动范围 40-170 只，记录差异因素
- **修正**: 一键全流程「总耗时约 10.5 分钟」→ 标明「缓存命中时」前提；补充 Phase 1 超时风险的快捷修复提示
- **版本**: 4.3 → 4.4

### 4.3 (2026-06-10)
- **已知坑**: 新增「⚠️ run_full_4d_pipeline.py Phase 1 超时」条目，含症状/根因/修复/预防。实测直接运行 pool_scanner.py 仅需 ~231s 但 wrapper 内 timeout=600 仍会超时（首次连接额外开销）
- **一键全流程**: 「推荐」段新增 ⚠️ Phase 1 超时风险警告，建议缓存过期时先单独跑 pool_scanner.py
- **版本**: 4.2 → 4.3

### 4.5 (2026-06-10)
- **新增**: 跌破买点惩罚（一买/二买） — `validate_tech_score.py` 新增第8维度 `buy_price_penalty`，按跌幅分级扣分。见 `references/buy-price-penalty-fix-2026-06-10.md`
- **安装**: pyarrow/fastparquet 修复 parquet 缓存写入问题，Phase 2+3 从34min降至20min

### 4.2 (2026-06-02)
- **frontmatter**: 补充 `created: 2026-05-26` 字段
- **文档**: 新增「🛑 反例与黑名单」独立节，集中汇总黑名单股票特征、禁用的选股策略、操作陷阱、失效因子
- **文档**: 新增「📊 常见失败模式汇总表」，含12条已知失败模式按P0-P3分级，含症状/根因/修复/影响范围
- **检查点**: Step 2/3/4 验证增强为语义检查（MD列内容验证、composite范围[0,100]验证、alpha_score范围验证、候选数合理性验证）
- **文档**: 新增「严重程度定义」段（P0/P1/P2/P3）
- **版本**: 4.1 → 4.2

### 4.1 (2026-06-02)
- **frontmatter**: 补充 `version: 4.1` / `author` / `updated` 字段
- **文档**: 新增「📦 前置依赖」区块，集中声明 Python 版本、pip 包、API Keys、数据库路径
- **可操作性**: 「运行顺序（分步手动版）」每步后添加 `# ✅ 验证：` 命令，支持执行后快速自查
- **文档**: 新增「📖 CHANGELOG」区块，追踪版本变更
