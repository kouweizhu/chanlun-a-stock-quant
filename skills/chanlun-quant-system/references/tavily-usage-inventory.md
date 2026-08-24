# Tavily 搜索使用全景 (2026-05-01)

最后一次完整梳理，用于配额管理和优化决策。

## 调用入口总览

### A. 消息面评分（直接 HTTP API）

核心函数: `pool_screener.py:scan_news()`
- 搜索词: `"{name} {code} 利空 公告 风险"`
- 接口: `POST https://api.tavily.com/search`
- 参数: `max_results=5, time_range="week"`
- 密钥: `TAVILY_API_KEY` 环境变量

被 4 个入口调用:
1. `pool_screener.run_phase2()` → Top 30 只消息面补扫
2. `_process_one_stock()` → 逐只评分（skip_news=False 时）
3. `full_rescore.py` → Tavily 重评全量
4. `renews.py` → 独立重跑脚本

月调用量: ~90 次

### B. 负面消息 cronjob (Agent MCP)

cronjob `b1f25e25e1aa` — 周一至五 09:15
- 调用方式: `mcp_tavily_search`（Hermes Agent）
- 18 只自选股 × 22 天 ≈ 396 次/月

👉 **已切换到 DuckDuckGo (2026-05-01)**，不再消耗 Tavily 配额。

### C. 实时价格 fallback (Agent MCP)

`check_price_levels.py:get_current_price_tavily()`
- 优先级链: Baostock → Tavily → DDG
- 仅在数据源故障时触发，月调用量极低。

## 配额优化后 (v3.3)

| 组别 | 原来 | 现在 | 节省 |
|------|:--:|:--:|:--:|
| A组 | ~90 | ~90 | — |
| B组 | ~396 | **0** | 396 次/月 |
| 合计 | ~500 | ~90 | **82%↓** |

## 降级架构

```
A组 (A500):
  scan_news(code, name)
    ├─ Tavily 成功 → 评分
    └─ Tavily 失败 → signals/.news_fallback_{code}.json
                    → Metaso MCP 补扫

B组 (cron):
  Tavily — 唯一来源
```

## 相关文件

- `pool_screener.py` — `scan_news()`, `_write_news_fallback()`, `list_news_fallbacks()`
- `config.yaml` — `a500.search_provider: auto`
- `data_source_helper.py` — 数据源优先级文档
- `check_negative_news.py` — cronjob 指南
- `check_price_levels.py` — `get_current_price_tavily()`
