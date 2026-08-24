# chanlun-a-stock-quant — 缠论 A 股量化交易策略系统

基于缠论（缠中说禅理论）的 A 股量化分析与选股系统：**缠论买点识别 + 五维评分 + 风控否决层**，
覆盖单票三维分析、全市场五维选股、参数网格回测与自动化验证的完整闭环。

> ⚠️ **免责声明**：本项目仅供技术学习与研究，不构成任何投资建议。股市有风险，据此操作盈亏自负。
> 系统输出的买点、评分与仓位分层均为程序化信号，使用前请自行独立判断。

## 系统架构

```
┌─────────────────────────── 数据链 DataManager ───────────────────────────┐
│  Baostock(1) → efinance(2) → AKShare-sina(3) → AKShare-EM(4)            │
│  → 东财 push2his 直连(4.2) → 腾讯K线(4.5) → 本地sqlite库(4.8) → Agent兜底 │
│  日线/30min 前复权 · parquet缓存 · 会话锁 · 失败标记自动清理               │
└──────────────────────────────────────────────────────────────────────┘
        │                                    │
┌───────▼────────────┐              ┌────────▼─────────────────────────┐
│  单票三维分析       │              │  A500 五维选股 Pipeline           │
│  技术(缠论递归)     │              │  pool_scanner → pool_screener    │
│  +基本面(同花顺摘要)│              │  → alpha_factor_filter           │
│  +消息面(多源扫描)  │              │  → fund_factor_rescore(--report) │
│  → 三维报告+缠论HTML│              │  五维加权 composite ≥60 入选      │
└────────────────────┘              └──────────────────────────────────┘
        │                                    │
┌───────▼────────────────────────────────────▼──────────────────────────┐
│  回测与验证：grid_search 参数网格 · tech-score-backtest-validation      │
│  portfolio/slow_bull/extreme_market 回测 · auto_validate 自动复验       │
└───────────────────────────────────────────────────────────────────────┘
```

**五维权重**：技术面 0.35 ／ 基本面 0.25 ／ Alpha 因子 0.20 ／ 消息面 0.10 ／ 资金面 0.10

**技术面评分（v5.x 结构完成度导向，满分100）**
趋势结构40（一买=背驰+确认+中枢数；二买=前低不破+底分型；三买=回踩不破ZG）
＋ 信号质量30 ＋ 多级别共振20（30m类型一致+时间同步+背驰+中枢同向）＋ 量价形态10。
跌破惩罚渐进：三买破中枢 ZD −3/−7/−15；一/二买破买价 −3/−10/−20。

**风控否决层**：*ST 名称匹配、一卖近 10 自然日有效性窗口、负面新闻条目级匹配
（仅本股名称所在行参与，全市场头条不误杀）、采集失败显式 skip_needs_review。

## 目录结构

```
chanlun-a-stock-quant/
├── chanlun_core/          # 核心引擎：缠论递归/买卖点/中枢/背驰、数据链、评分、回测
│   ├── config.yaml        #   全局配置（阈值/权重/黑名单）
│   ├── data_manager.py    #   六级降级数据链
│   ├── recursive_timing*.py  # 缠论递归计时引擎（笔/段/中枢/买卖点）
│   ├── validate_tech_score.py# 技术面结构化评分
│   ├── run_full_4d_pipeline.py # A500 五维选股一键流程
│   └── ...                #   基本面/消息面/资金面/Alpha/回测/报告 全家桶
├── alpha-zoo/             # Alpha 因子库（gtja191 系列，IC 加权）
├── skills/                # 15 个 Agent 技能（SKILL.md 工作流封装，可独立阅读学习）
├── docs/
│   ├── 全体系终审报告_2026-08-24.md   # P0-P3 四级审计与修复全记录
│   └── Hermes-A股五维分析系统.html    # 五维评分体系手册
├── requirements.txt
├── LICENSE                # MIT
└── README.md
```

## 安装

```bash
# Python 3.10+（作者环境 3.12）
pip install -r requirements.txt
```

无需任何 API Key 即可运行主链（Baostock/AKShare/push2his/腾讯均零鉴权）。
可选增强：`IWENCAI_API_KEY`（同花顺问财新闻/公告）、LLM 情感通道（`.env` 配
`LLM_API_ENDPOINT/KEY/MODEL`，OpenAI 兼容格式）。未配置时自动降级，不影响核心功能。

> Windows GBK 控制台建议先 `$env:PYTHONIOENCODING='utf-8'`。
> 首次运行会自动拉取并缓存 K 线（data_cache/），缓存命中后离线可用。

## 快速开始

```bash
cd chanlun_core

# ① 单票缠论分析（输出 JSON 信号 + 可视化 HTML）
python quick_chanlun.py 600872

# ② 单票三维分析（技术+基本面+消息面 → Markdown 报告）
python single_stock_analysis.py --code 600872 --report

# ③ A500 全市场五维选股（完整流水线）
python run_full_4d_pipeline.py

# ④ 技术评分回测验证（评分 vs 后续收益交叉分析）
python score_backtest.py
python grid_search.py          # 权重/阈值参数网格搜索
```

## 数据源纪律（踩坑沉淀）

详见 `skills/a-share-three-dim-analyzer/references/akshare-quirks.md`，要点：

- **东财 WAF 有 IP 级冷却封禁**：短 UA + 高频探测会触发小时级断连（连裸 requests 都断）。
  调试必须低频 + 完整浏览器 UA + Referer=`quote.eastmoney.com` + 调用间隔 ≥2s。
- push2his K线行序为 `date,open,close,high,low,volume`——**close/high 次序极易搞反**。
- 新浪 `_同比` 字段是小数口径（0.0178=1.78%），与 AKShare 主链一致，勿再 ×100。
- 同花顺摘要数值列单位是百分数本体（roe=15 表示 15%，不是 0.15）。

## skills/ 一览

| 技能 | 用途 |
|---|---|
| a500-multi-factor-selection | A500 五维选股全流程 |
| stock-analysis / a-stock-standard-analysis | 单票三维分析（含深度基本面编排） |
| fundamental-deep-analysis | 基本面四层框架深度研究 |
| a-share-three-dim-analyzer | 筹码/资金/情绪辅助过滤 + 数据源 quirks 手册 |
| chanlun-quant-system | 缠论多级别递归量化择时 |
| daily-chanlun-timing-system | 日线级择时 + HTML 可视化 |
| chanlun-third-buy-scanner | 三买专项扫描 |
| quant-grid-search-and-automation | 参数网格搜索 + 定时自验证 |
| tech-score-backtest-validation | 技术评分预测力回测方法论 |
| news-scanner-architecture | 消息面引擎架构说明 |
| a-share-earnings-alert | 全市场业绩预警增量监控 |
| monthly-broker-gold-stock-verification | 券商金股跟投验证 |
| institutional-underweight-screening | 机构低配行业选股 |
| stock-anomaly-triage | 异动归因三管排查 |

## 版本

当前 **v5.4.0**（2026-08-24）：历经四轮体系审计（P0×0 / P1×12 全修 / P2×27 全修 /
P3 收官），全部过程见 `docs/全体系终审报告_2026-08-24.md`——包括每项修复的根因、
行为级验证与踩坑教训，可作为量化系统工程质量建设的参考样本。

## License

[MIT](LICENSE)
