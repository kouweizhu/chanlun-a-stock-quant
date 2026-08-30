# 消息面数据源优化方案（已实施）

## 背景

a500-multi-factor-selection 的 `scan_news()` 原采用 6 级降级链（前面的返回空才搜索下一个），对 A 股新闻覆盖不足。

**2026-06-12 重构完成**: 降级链 → 全量采集 + 加权融合评分。

## 已实施变更

### 架构：降级链 → 全量采集

旧逻辑：`results is None` 门控，某级成功即停止，后续全跳过。
新逻辑：9 个数据源独立执行，各自 try/except，失败不影响其他源。最终加权融合。

### 新增数据源（4个）

| 编号 | 数据源 | 接口 | 说明 |
|------|--------|------|------|
| S1 | 东方财富个股新闻 | `akshare.stock_news_em(symbol=code)` | 14天窗口，取20条，A股专用 |
| S2 | 涨停池情绪 | `akshare.stock_zt_pool_em(date=YYYYMMDD)` | 涨停家数+连板分布 |
| S3 | 雪球热搜 | `akshare.stock_hot_follow_xq(symbol="最热门")` | 散户关注度排行 |
| S7 | CCTV 财经新闻 | `akshare.news_cctv(date=YYYYMMDD)` | 宏观权威，无数据回退3天 |

### 去掉的数据源

- Metaso（原第4级）— 用户要求移除

### 评分：多源加权融合

各源独立评分（关键词匹配），然后加权平均：

| 数据源 | 权重 | 类型 |
|--------|------|------|
| 东财新闻 (eastmoney) | 1.2 | 新闻 |
| 涨停池 (zt_pool) | 0.8 | 情绪（固定50分） |
| 雪球热搜 (xueqiu) | 0.6 | 情绪（固定50分） |
| 同花顺新闻 (iwencai_news) | 1.0 | 新闻 |
| 同花顺公告 (iwencai_ann) | 1.2 | 新闻 |
| 新浪财经 (sina) | 0.8 | 新闻 |
| CCTV (cctv) | 1.0 | 新闻 |
| Tavily (tavily) | 0.7 | 新闻 |

公告偏移 (ann_delta) 最后合并：`score = max(15, min(75, score + ann_delta))`

### 代码位置（v4.6.5 更新）

- **主逻辑**: `news_scanner.py`（独立共享模块）
- `pool_screener.py` 和 `news_detail_report.py` 均通过薄包装函数委托 `news_scanner.scan_news()`
- `check_negative_news.py` 的 `--full` 模式也调用 `news_scanner`
- 文档: `references/news-scanning-architecture.md`

### 接口兼容性

返回值保持 `(score: float, detail_str: str)` 元组不变，所有调用点无需修改。

detail 格式变化：
- 旧: `[Tavily] 负面2个,正面3个`
- 新: `[6源] 12条55分 | 8家50分 | 20条50分 | 0条(no_key) | 10条45分 | 15条58分 | 5条62分`

### 消息明细输出（v4.6.2 补充）

`detail_str` 现在包含每条消息的摘要，标注来源和倾向：

```
[同花顺新闻][正面] 2026-06-04 东方雨虹拟收购两家印尼建材企业: 近日，东方雨虹发布公告称...
[同花顺公告][负面] 2026-05-08 关于控股股东、实际控制人及一致行动人权益变动触及1%的公告: ...
[CCTV财经][中性] 李强主持召开国务院常务会议: ...
```

格式：`[{数据源标签}][{正面/负面/中性/混合}] {消息标题/摘要(前80字符)}`

- 情绪类源（涨停池/雪球热搜）固定中性分50，不做关键词评分
- 新闻类源逐条做关键词匹配判断正/负/中性/混合
- 消息标题截取前80字符，换行符替换为空格

## 已知限制

- **akshare stock_news_em pyarrow 兼容**：东方财富个股新闻（S1）在新版 pyarrow 下抛 `ArrowInvalid: Invalid regular expression: invalid escape sequence: \u`（akshare 内部 `str.replace(r"\u3000", "", regex=True)` 触发）。当前降级跳过，不影响其他源。修复方向：升级 akshare 或降级 pyarrow
- **CCTV 日期格式**：`news_cctv(date=YYYYMMDD)` 需要 `YYYYMMDD` 格式，节假日/周末可能无数据，自动回退 3 天

## 未实施（长期优化）

- LLM 语义评分通道（P1）— 2026-06-12 与用户讨论方案，待配置 LLM provider 后实施
  - 设计详见 `references/news-scanning-architecture.md` 的「第二层：LLM 语义分析」章节
  - 需要环境变量：`LLM_API_ENDPOINT`、`LLM_API_KEY`、`LLM_MODEL`
  - 评分公式：`final = 0.4 × keyword_score + 0.6 × llm_score`
  - 失败降级到纯关键词评分
- 预计算+缓存层（P2）

## 参考项目

- **TradingAgents-AShare**: `D:\常用文件\TradingAgents-AShare\`
  - 数据源报告: `数据源报告.md`
  - 新闻分析师: `tradingagents/agents/analysts/news_analyst.py`
  - 情绪分析师: `tradingagents/agents/analysts/social_media_analyst.py`
  - 数据收集器: `tradingagents/graph/data_collector.py`
  - 供应商层: `tradingagents/dataflows/providers/cn_akshare_provider.py`
