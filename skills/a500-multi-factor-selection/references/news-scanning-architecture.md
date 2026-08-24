# 消息面扫描架构与数据源

**代码位置**: `news_scanner.py`（独立共享模块，`pool_screener.py` 和 `news_detail_report.py` 均委托此模块）

## 全量采集 + 加权融合策略

消息面扫描采用全量采集模式：所有数据源独立执行，各自拿到各自的结果，失败不影响其他源。最终对各源结果加权融合评分。

```
S0  AKShare 公告预扫描（始终执行，独立于新闻链）
S1  东方财富个股新闻 (akshare stock_news_em)
S2  涨停池情绪 (akshare stock_zt_pool_em)
S3  雪球热搜 (akshare stock_hot_follow_xq)
S4  同花顺新闻（问财 OpenAPI）
S5  同花顺公告（问财 OpenAPI）
S6  新浪财经新闻（HTML解析）
S7  CCTV 财经新闻 (akshare news_cctv)
S8  Tavily（通用搜索）
```

## 评分架构（双层）

### 第一层：关键词评分（快速通道，不需 API Key）

各新闻类源独立做关键词匹配评分，情绪类源给中性分 50。

**负面关键词**（29个）：
- 财务/经营类：亏损、暴跌、违约、诉讼、处罚、退市、暴雷、减值、减持、爆仓、造假、停产、重组失败、预警、跌停、st、*st、戴帽、退市风险、净亏损
- 监管/制裁类：制裁、SDN、列入、黑名单、调查、立案、冻结、查封、限制、打压、出口管制、处罚决定、通报批评、监管措施、立案调查、立案侦查

**正面关键词**（13个）：
- 增长、超预期、回购、增持、中标、突破、利好、分红、盈利、扩产、净利润增长、大涨、扭亏、预增、高增

**评分映射**（单源）：

| net (正-负) | 评分区间 | 含义 |
|-------------|----------|------|
| >= 4 | 60-75 | 明显利好 |
| >= 2 | 58-70 | 中度利好 |
| >= 1 | 54-60 | 轻微利好 |
| -2 ~ 0 | 35-50 | 轻微影响 |
| -4 ~ -2 | 25-43 | 明显利空 |
| < -4 | 15-38 | 严重利空 |

**加权融合**：

| 数据源 | 权重 | 理由 |
|--------|------|------|
| eastmoney (东财新闻) | 1.2 | A股专用，覆盖好 |
| zt_pool (涨停池) | 0.8 | 情绪参考 |
| xueqiu (雪球热搜) | 0.6 | 散户情绪，噪音大 |
| iwencai_news (同花顺新闻) | 1.0 | 通用搜索 |
| iwencai_ann (同花顺公告) | 1.2 | 官方+搜索双保险 |
| sina (新浪财经) | 0.8 | 免费，质量一般 |
| cctv (CCTV财经) | 1.0 | 宏观权威 |
| tavily (Tavily) | 0.7 | 通用搜索，A股覆盖弱 |

最终评分 = Σ(weight_i × score_i) / Σ(weight_i) + ann_delta（公告偏移）

### 第二层：LLM 语义分析（增强通道，待实施）

**目标**：用 LLM 对新闻文本做语义理解，弥补关键词匹配的盲区（如公司治理类事件：董事长缺席、高管变动等）。

**设计**：
- 收集所有数据源拿到的新闻文本，截断到 2000 字以内
- 通过 curl 调 OpenAI 兼容 API（可配硅基流动、DeepSeek 等）
- LLM 判断整体倾向（利好/利空/中性）+ 置信度
- 最终评分 = 0.4 × 关键词分 + 0.6 × LLM 分
- LLM 调用失败时降级到纯关键词评分

**环境变量**（待配置）：
- `LLM_API_ENDPOINT` — API 端点 URL
- `LLM_API_KEY` — API Key
- `LLM_MODEL` — 模型名称

## 消息明细输出

`scan_news()` 返回的 `detail_str` 包含每条消息的摘要，格式：

```
[6源] 8条70分 | 5条45分 | 14条50分 | 5条50分
[同花顺新闻][正面] 2026-06-04 东方雨虹拟收购两家印尼建材企业: 近日...
[同花顺公告][负面] 2026-05-08 关于控股股东权益变动触及1%的公告...
[CCTV财经][中性] 李强主持召开国务院常务会议...
```

每条消息标注：`[数据源][倾向] 标题摘要`
- 数据源：东财新闻/同花顺新闻/同花顺公告/新浪财经/CCTV财经/Tavily
- 倾向：正面/负面/中性/混合

## 各级详情

### S0：AKShare 公告预扫描
- **函数**: `akshare_scanner.scan_announcements(code, name, lookback_days=3)`
- **特点**: 公司官方披露，最权威，作为第一道防线
- **输出**: `ann_delta`（利空/利好偏移值），合并到最终评分

### S1：东方财富个股新闻
- **接口**: `https://search-api-web.eastmoney.com/search/jsonp`（GET，直接调用）
- **参数**: `param={"uid":"","keyword":code,"type":["cmsArticleWebOld"],"client":"web","clientType":"web","clientVersion":"curr","param":{"cmsArticleWebOld":{"searchScope":"default","sort":"default","pageIndex":1,"pageSize":20,"preTag":"<em>","postTag":"<em>"}}}`
- **取前 20 条**
- **需要**: requests 库，无需 API Key
- **注意**: 不使用 `akshare.stock_news_em()` — 该函数在新版 pyarrow 下报 `ArrowInvalid: Invalid regular expression: invalid escape sequence: \u`。直接调 JSONP 接口可绕过此问题

### S2：涨停池情绪
- **接口**: `akshare.stock_zt_pool_em(date=YYYYMMDD)`
- **输出**: 当日涨停家数 + 连板分布
- **用途**: 市场整体情绪温度
- **评分**: 情绪类源，给中性分 50，不参与关键词评分

### S3：雪球热搜
- **接口**: `akshare.stock_hot_follow_xq(symbol="最热门")`
- **输出**: 雪球热搜前 20 只股票
- **用途**: 散户关注度风向标
- **评分**: 情绪类源，给中性分 50，不参与关键词评分

### S4：同花顺新闻
- **接口**: `https://openapi.iwencai.com/v1/comprehensive/search`
- **参数**: `channels: ["news"]`, `app_id: "AIME_SKILL"`
- **认证**: `Authorization: Bearer *** **取前8条**
- **需要**: `IWENCAI_API_KEY`

### S5：同花顺公告
- **接口**: 同上
- **参数**: `channels: ["announcement"]`
- **取前5条**
- **需要**: `IWENCAI_API_KEY`

### S6：新浪财经新闻
- **接口**: `https://vip.stock.finance.sina.com.cn/corp/go.php/VCB_AllNewsStock/symbol/{prefix}{code}.phtml`
- **编码**: GBK
- **解析**: HTML 正则提取 `<div class="datelist">` 中的新闻
- **取前10条**
- **免费，无需 Key**

### S7：CCTV 财经新闻
- **接口**: `akshare.news_cctv(date=YYYYMMDD)`
- **窗口**: 当天，无数据时回退最近 3 天
- **取前 15 条**
- **需要**: akshare，无需 API Key

### S8：Tavily
- **接口**: `https://api.tavily.com/search`
- **认证**: `Authorization: Bearer *** **参数**: `search_depth: "basic"`, `max_results: 5`, `time_range: "week"`
- **需要**: `TAVILY_API_KEY`

## 环境变量

| 变量 | 用途 | 必需 |
|------|------|------|
| `IWENCAI_API_KEY` | 同花顺问财 | 否（跳过 S4/S5） |
| `TAVILY_API_KEY` | Tavily 搜索 | 否（跳过 S8） |
| `LLM_API_ENDPOINT` | LLM API 端点 | 否（第二层，待实施） |
| `LLM_API_KEY` | LLM API Key | 否（第二层，待实施） |
| `LLM_MODEL` | LLM 模型名 | 否（第二层，待实施） |

## 调用间隔

无固定间隔。各源串行执行，每个源独立 try/except，失败不影响其他源。

## 调用入口

- **A500 选股**: `pool_screener.py` 中 `_process_batch()` 对每只候选调用 → `scan_news(code, name)`
- **三维分析**: `news_detail_report.py` 中 `analyze_single_stock()` → `_ns_scan_news(code, name)`
- **负面监控**: `check_negative_news.py` 中 `--full` 模式 → `_ns_scan_news(code, name)`
- **Phase 3**: `rescore_news.py` 对 Top 30 补扫，重算四维综合分
