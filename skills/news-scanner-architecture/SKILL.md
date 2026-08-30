---
name: news-scanner-architecture
description: news_scanner.py 架构说明 — A500选股与三维分析共享的消息面扫描引擎
version: 1.0
---

# news_scanner.py 架构说明

## 定位
`news_scanner.py` 是 A500 选股和三维分析共享的消息面扫描引擎。从 `pool_screener.py` 中提取，消除与 `news_detail_report.py` 的代码重复。

## 文件位置
`/home/zjj1990/work/chanlun_core/news_scanner.py`

## 数据源清单
- S0: AKShare 公告预扫描（akshare_scanner）
- S1: 东方财富个股新闻（直接调 JSONP API，绕过 pyarrow 问题）
- S2: 涨停池情绪 (akshare stock_zt_pool_em)
- S3: 雪球热搜 (akshare stock_hot_follow_xq)
- S4: 同花顺新闻（问财 OpenAPI）
- S5: 同花顺公告（问财 OpenAPI）
- S6: 新浪财经新闻（HTML解析）
- S7: CCTV 财经新闻 (akshare news_cctv)
- S8: Tavily（通用搜索）

## 评分架构
- 关键词通道：各源独立评分 + 加权融合（SOURCE_WEIGHTS 字典）
- LLM 通道：占位，环境变量 LLM_API_ENDPOINT / LLM_API_KEY / LLM_MODEL
- 合并公式：final = 0.4 * keyword + 0.6 * llm（LLM 不可用时降级到纯关键词）
- 公告偏移：ann_delta 直接加减到最终分

## 消费者
- `pool_screener.py`：thin wrapper（delegates to news_scanner）
- `news_detail_report.py`：analyze_single_stock() 和 run_batch_mode() 直接调用 _ns_scan_news
- `check_negative_news.py`：`--full` 参数模式下调用 _ns_scan_news（默认仍用同花顺单源）
- `generate_report.py`：extract_news_details() 解析新格式（summary_line + msg_lines）

## 输出格式
`scan_news()` 返回 `(score: float, detail: str)`
- `detail` 第一行：`[X源] A条X分 | B条Y分 | ... | LLM:未启用`
- 后续行：`[来源][正/负/混合/中性] 消息标题`
- 调用方通过 `detail.split(chr(10))` 解析

## 关键修复记录
1. akshare.stock_news_em() 在新版 pyarrow 下报 ArrowInvalid（内部 str.replace regex 转义问题）
   - 绕过方案：直接调东方财富 JSONP API `https://search-api-web.eastmoney.com/search/jsonp`
   - 参数格式：type=["cmsArticleWebOld"], param 嵌套结构
   - 参考 akshare 源码 `news/news_stock.py:stock_news_em` 获取完整请求参数
2. 负面关键词扩充：+26 个公司治理类（缺席/未亲自出席/代为行使/身体原因/高管变动/董事会异常/董事辞职/减持计划/大股东减持/控股股东减持/实控人减持/质押/平仓/强制平仓/被动减持/信披违规/信息披露/违规担保/资金占用/被立案/被调查/被处罚/被谴责/被问询/业绩变脸/由盈转亏/商誉减值/资产减值）
3. generate_report.py 模板从旧表格改为"数据源汇总 + 消息明细"格式
   - 旧：表格（搜索源/相关文章/利好命中/利空命中）
   - 新：summary_line + msg_lines（每条消息标注来源和正/负/中性）
