# 仓库打包移植指南 — 2026-05-02

## 背景

将 chanlun_core 整个系统打包到独立 GitHub 仓库 `/home/zjj1990/work/chanlun-quant/`，供其他 Agent 复用。

## 环境限制

WSL 中 DNS 把 `github.com` 解析到 `127.0.0.1`（可能是 VPN/代理配置），无法直接 push。解决方案：拷贝到能访问 GitHub 的机器上操作。

## 文件清单

### 核心 Python 模块（41个）

```
# 缠论引擎
generate_analysis.py  data_manager.py  baostock_utils.py  data_source_helper.py

# A500 选股
pool_scanner.py  pool_screener.py  composite_scorer.py  quick_fundamental.py
akshare_fundamental.py  akshare_scanner.py  risk_filter.py  stock_pool.py

# 回测
backtest_engine.py  portfolio_backtest.py  slow_bull_backtest.py
extreme_market_backtest.py  a500_backtest.py  fund_backtest.py
score_backtest.py  grid_search.py  run_backtest.py

# 监控 + 推送
position_monitor.py  weixin_pusher.py

# 市场分析
market_regime.py  sentiment_analyzer.py  check_negative_news.py  check_price_levels.py

# 工具
config_loader.py  file_utils.py  slippage_model.py  cron_utils.py  stock_db.py

# 报告
report_generator.py  excel_report.py  quick_html.py  quick_chanlun.py  news_detail_report.py

# 验证
auto_validate.py  validate_tech_score.py

# 其他
trading_strategy.py  multi_stock_scanner.py
```

### 数据文件

```
data/config.yaml           # 配置模板
data/sentiment_lexicon.json  # 情感词典
data/.old_stocks_2016.json   # 2016年前上市股票池
```

### 排除清单

```
# 测试脚本
_test_techfund_only.py  weixin_pusher_test.py  weixin_pusher_test2.py  run_three_test.py

# 缓存 JSON（可重新生成）
.scanner_cache.json  .phase2_results*.json  .stock_listing_cache.json
.grid_search_results.json  .tech_score_validation.json

# 生成结果
*.csv  *.xls  *.xlsx

# 运行时目录
data_cache/  signals/  logs/  reports_html/  auto_reports/  .alphaclaw/

# Hermes 特定
SKILL.md  审计报告*.md
```

## 硬编码路径修复清单

打包时发现以下文件使用了 `/home/zjj1990/work/chanlun_core/` 绝对路径，已修复为 `os.path.dirname(os.path.abspath(__file__))` 相对路径：

| 文件 | 修改内容 |
|------|---------|
| `slow_bull_backtest.py` | `.old_stocks_2016.json` 路径 + 默认输出路径 |
| `portfolio_backtest.py` | `.old_stocks_2016.json` 路径 + 默认输出路径 + solo_path |
| `extreme_market_backtest.py` | 默认输出路径 |
| `market_regime.py` | 默认输出路径 |

**未修改（环境特定，用户部署时按需改）**：
- `position_monitor.py`: `HOLDINGS_DIR = "/mnt/d/常用文件/持仓监控"`
- `pool_screener.py`: `OUTPUT_BASE = "/mnt/d/常用文件/股票池推荐股"`
- `check_negative_news.py`: `xlsx_path = "/mnt/d/常用文件/自选股负面消息清单/自选股清单.xlsx"`
- `market_regime.py`: `MACRO_REPORT_DIR = "/mnt/d/常用文件/宏观数据监控"`

## 修复的 config_loader.py Bug

打包过程中发现并修复了 2 个阻塞 bug：

1. **JS 风格布尔值**：`true`/`false` → `True`/`False`（第55-57行）
2. **YAML 空列表 None**：`.get("codes", [])` → `.get("codes") or []`（第156-161行）

这两个 bug 会导致所有依赖 config_loader 的模块无法 import。

## .gitignore 要点

```gitignore
__pycache__/
*.csv
*.xls
*.xlsx
.scanner_cache.json
.phase2_results*.json
data_cache/
signals/
logs/
reports_html/
auto_reports/
.alphaclaw/
```

## requirements.txt

```
pandas>=2.0
numpy>=1.24
pyarrow>=12.0
baostock>=0.8.8
akshare>=1.18.0
efinance>=0.5.0
openpyxl>=3.1
xlrd>=2.0
filelock>=3.12
pyyaml>=6.0
# Optional: aiohttp>=3.9, cryptography>=41.0 (微信推送)
```
