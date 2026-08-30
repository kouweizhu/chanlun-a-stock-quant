#!/usr/bin/env python
"""
news_scanner.py — 多源消息面扫描引擎

全量采集 + 加权融合评分，供 pool_screener.py (A500选股) 和 news_detail_report.py (三维分析) 共享使用。

数据源:
  S0: AKShare 公告预扫描（akshare_scanner）
  S1: 东方财富个股新闻（直接调 JSONP API，绕过 pyarrow 问题）
  S2: 涨停池情绪 (akshare stock_zt_pool_em)
  S3: 雪球热搜 (akshare stock_hot_follow_xq)
  S4: 同花顺新闻（问财 OpenAPI）
  S5: 同花顺公告（问财 OpenAPI）
  S6: 新浪财经新闻（HTML解析）
  S7: CCTV 财经新闻 (akshare news_cctv)
  S8: Tavily（通用搜索）

公开接口:
  scan_news(code, name) -> (score: float, detail: str)
"""
import os, sys, json, re, time
import urllib.request, urllib.parse
from datetime import datetime, timedelta

# ── 路径确保 ──
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

# ── v5.4.1(主审M-3): LLM 环境接线自持——本模块消费 LLM_API_KEY/ENDPOINT/MODEL,
# 但此前 dotenv 加载责任在 news_detail_report/pool_screener 的导入副作用。
# check_negative_news 独立 CLI 运行时无人加载 → LLM 通道静默关闭
# (有"LLM:失败"标注但区分度退化无人知)。此处幂等自载(fail-silent),
# 与其他入口的重复加载无副作用(load_dotenv 默认不覆盖已存在值)。
try:
    from dotenv import load_dotenv as _ld
    for _p in (os.path.join(os.path.dirname(_SCRIPT_DIR), ".env"),
               os.path.expanduser("~/.hermes/.env")):
        if _p and os.path.exists(_p):
            try:
                _ld(_p)
            except Exception:
                pass
except ImportError:
    pass


# ── v5.4.1(AUD-A-06): 进程级"单飞"扫描缓存 ──
# 三维编排里 news(analyze_single_stock)与 negative(search_negative 多源降级)
# 是并行任务, iwencai key 失败时两者会对同一 (code,name) 各跑一次完整八源
# 采集+LLM(实测多付 ~10 次 HTTP + 1 次 LLM + 5-15s)。lru_cache 无法防并发
# 双 miss, 故用 Event 单飞: 首个线程实扫, 后来者等待复用。进程短命, 不做 TTL。
_SCAN_MEMO: dict = {}
_SCAN_MEMO_LOCK = None  # 惰性初始化(模块导入期创建 threading 对象在某些嵌入场景过早)


def scan_news_cached(code: str, name: str, timeout_wait: float = 300.0) -> tuple:
    """scan_news 的进程级单飞包装——同 (code,name) 只实扫一次，其余等待共享。

    Returns:
        与 scan_news 相同的 (score, detail)
    """
    global _SCAN_MEMO_LOCK
    if _SCAN_MEMO_LOCK is None:
        import threading as _th
        _SCAN_MEMO_LOCK = _th.Lock()
    import threading as _th

    key = (str(code), str(name))
    with _SCAN_MEMO_LOCK:
        ent = _SCAN_MEMO.get(key)
        if ent is None:
            ent = {"event": _th.Event(), "score": None, "detail": None}
            _SCAN_MEMO[key] = ent
            owner = True
        else:
            owner = False

    if not owner:
        ent["event"].wait(timeout=timeout_wait)
        # owner 实扫失败也不重试(与 scan_news 自身降级哲学一致), 复用其结果
        return ent["score"], ent["detail"]

    try:
        sc, dt = scan_news(code, name)
        return sc, dt
    finally:
        with _SCAN_MEMO_LOCK:
            # 注: sc/dt 在异常时不存在——保持 None 并放行等待者由其走 skip 语义
            try:
                ent["score"], ent["detail"] = sc, dt
            except NameError:
                pass
            ent["event"].set()


def scan_news(code: str, name: str) -> tuple:
    """多源消息面扫描 — 全量采集 + 加权融合评分

    Returns:
        (score: float, detail: str)
        score: 0-100, 50=中性, <50=负面, >50=利好
    """
    import json as _json
    import re as _re
    # ── S0: AKShare 公告预扫描 ──
    ann_delta = 0
    ann_detail = ""
    try:
        from akshare_scanner import scan_announcements
        ann_delta, ann_detail = scan_announcements(code, name, lookback_days=3)
        if ann_delta != 0:
            print(f" {ann_detail}", end="")
    except Exception as e:
        ann_detail = f"[公告扫描异常:{str(e)[:30]}]"

    source_results = {}
    name_lower = name.lower()

    # ── S1: 东方财富个股新闻（直接调 JSONP API，走 em_get 限流防封）──
    try:
        import em_utils  # 东财统一限流入口（em_get：串行≥1s+抖动+会话复用）
        _inner_param = {
            "uid": "",
            "keyword": code,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": 20,
                    "preTag": "<em>",
                    "postTag": "</em>",
                }
            },
        }
        _params = {
            "cb": "jQuery35101792940631092459_1764599530165",
            "param": _json.dumps(_inner_param, ensure_ascii=False),
            "_": str(int(datetime.now().timestamp() * 1000)),
        }
        _headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": f"https://so.eastmoney.com/news/s?keyword={code}",
            "Accept": "*/*",
        }
        _r = em_utils.em_get("https://search-api-web.eastmoney.com/search/jsonp",
                             params=_params, headers=_headers, timeout=15)
        _raw = _r.text
        _json_match = _re.search(r'jQuery\d*_\d*\((.*)\)$', _raw, _re.DOTALL)
        if _json_match:
            _data = _json.loads(_json_match.group(1))
        else:
            _data = _json.loads(_raw)
        _items = _data.get("result", {}).get("cmsArticleWebOld", [])
        if _items:
            articles = []
            for _item in _items[:20]:
                _title = _item.get("title", "").replace("<em>", "").replace("</em>", "")
                _src = _item.get("mediaName", "")
                _content = _item.get("content", "")[:300]
                _dt = _item.get("date", "")[:19]
                text = f"{_dt} {_title} (source: {_src})"
                if _content:
                    text += f": {_content}"
                articles.append(text)
            source_results["eastmoney"] = {"articles": articles, "label": "东财新闻", "status": "ok"}
            print(f"[东财新闻] {len(articles)}条", end=" ")
        else:
            source_results["eastmoney"] = {"articles": [], "label": "东财新闻", "status": "empty"}
    except Exception as e:
        source_results["eastmoney"] = {"articles": [], "label": "东财新闻", "status": f"error:{str(e)[:30]}"}

    # ── S2: 涨停池情绪 ──
    try:
        import akshare as ak
        today_str = datetime.now().strftime("%Y%m%d")
        df = ak.stock_zt_pool_em(date=today_str)
        if df is not None and not df.empty:
            count = len(df)
            text = f"今日涨停家数：{count}"
            if "连板数" in df.columns:
                lianban = df["连板数"].value_counts().sort_index()
                text += f"\n连板分布：\n{lianban.head(10).to_string()}"
            articles = [text]
            source_results["zt_pool"] = {"articles": articles, "label": "涨停池", "status": "ok"}
            print(f"[涨停池] {count}家", end=" ")
        else:
            source_results["zt_pool"] = {"articles": [], "label": "涨停池", "status": "empty"}
    except Exception as e:
        source_results["zt_pool"] = {"articles": [], "label": "涨停池", "status": f"error:{str(e)[:30]}"}

    # ── S3: 雪球热搜 ──
    try:
        import akshare as ak
        df = ak.stock_hot_follow_xq(symbol="最热门")
        if df is not None and not df.empty:
            articles = [f"雪球热搜前20：\n{df.head(20).to_string(index=False)}"]
            source_results["xueqiu"] = {"articles": articles, "label": "雪球热搜", "status": "ok"}
            print(f"[雪球热搜] {len(df)}条", end=" ")
        else:
            source_results["xueqiu"] = {"articles": [], "label": "雪球热搜", "status": "empty"}
    except Exception as e:
        source_results["xueqiu"] = {"articles": [], "label": "雪球热搜", "status": f"error:{str(e)[:30]}"}

    # ── S4: 同花顺 新闻搜索 ──
    iwencai_key = os.environ.get("IWENCAI_API_KEY", "")
    if iwencai_key:
        try:
            import secrets
            trace_id = secrets.token_hex(32)
            query = f"{name} {code} 财经新闻"
            payload = _json.dumps({"channels": ["news"], "app_id": "AIME_SKILL", "query": query}).encode('utf-8')
            req = urllib.request.Request("https://openapi.iwencai.com/v1/comprehensive/search", data=payload,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {iwencai_key}",
                         "X-Claw-Call-Type": "normal", "X-Claw-Skill-Id": "news-search",
                         "X-Claw-Skill-Version": "1.0.0", "X-Claw-Plugin-Id": "none",
                         "X-Claw-Plugin-Version": "none", "X-Claw-Trace-Id": trace_id})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = _json.loads(resp.read().decode('utf-8'))
            if data.get('status_code') == 0:
                hits = data.get('data', [])
                articles = []
                for item in hits[:8]:
                    title = item.get('title', '')
                    summary = item.get('summary', '')
                    dt = str(item.get('publish_date', ''))[:10]
                    text = f"{dt} {title}"
                    if summary:
                        text += f": {summary[:200]}"
                    articles.append(text)
                if articles:
                    source_results["iwencai_news"] = {"articles": articles, "label": "同花顺新闻", "status": "ok"}
                    print(f"[同花顺新闻] {len(articles)}条", end=" ")
                else:
                    source_results["iwencai_news"] = {"articles": [], "label": "同花顺新闻", "status": "empty"}
            else:
                source_results["iwencai_news"] = {"articles": [], "label": "同花顺新闻", "status": f"error:{data.get('status_msg','?')}"}
        except Exception as e:
            source_results["iwencai_news"] = {"articles": [], "label": "同花顺新闻", "status": f"error:{str(e)[:30]}"}
    else:
        source_results["iwencai_news"] = {"articles": [], "label": "同花顺新闻", "status": "no_key"}

    # ── S5: 同花顺 公告搜索 ──
    if iwencai_key:
        try:
            import secrets
            trace_id = secrets.token_hex(32)
            query = f"{name} {code} 公告"
            payload = _json.dumps({"channels": ["announcement"], "app_id": "AIME_SKILL", "query": query}).encode('utf-8')
            req = urllib.request.Request("https://openapi.iwencai.com/v1/comprehensive/search", data=payload,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {iwencai_key}",
                         "X-Claw-Call-Type": "normal", "X-Claw-Skill-Id": "announcement-search",
                         "X-Claw-Skill-Version": "1.0.0", "X-Claw-Plugin-Id": "none",
                         "X-Claw-Plugin-Version": "none", "X-Claw-Trace-Id": trace_id})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = _json.loads(resp.read().decode('utf-8'))
            if data.get('status_code') == 0:
                hits = data.get('data', [])
                articles = []
                for item in hits[:5]:
                    title = item.get('title', '')
                    summary = item.get('summary', '')
                    dt = str(item.get('publish_date', ''))[:10]
                    text = f"{dt} {title}"
                    if summary:
                        text += f": {summary[:200]}"
                    articles.append(text)
                if articles:
                    source_results["iwencai_ann"] = {"articles": articles, "label": "同花顺公告", "status": "ok"}
                    print(f"[同花顺公告] {len(articles)}条", end=" ")
                else:
                    source_results["iwencai_ann"] = {"articles": [], "label": "同花顺公告", "status": "empty"}
            else:
                source_results["iwencai_ann"] = {"articles": [], "label": "同花顺公告", "status": f"error:{data.get('status_msg','?')}"}
        except Exception as e:
            source_results["iwencai_ann"] = {"articles": [], "label": "同花顺公告", "status": f"error:{str(e)[:30]}"}
    else:
        source_results["iwencai_ann"] = {"articles": [], "label": "同花顺公告", "status": "no_key"}

    # ── S6: 新浪财经新闻 ──
    try:
        prefix = "sz" if code.startswith(('0', '3', '2')) else "sh"
        sina_url = f"https://vip.stock.finance.sina.com.cn/corp/go.php/VCB_AllNewsStock/symbol/{prefix}{code}.phtml"
        req = urllib.request.Request(sina_url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            sina_html = resp.read().decode('gbk', errors='ignore')
        dl_match = _re.search(r'<div class="datelist"[^>]*>(.*?)</div>', sina_html, _re.DOTALL)
        articles = []
        if dl_match:
            raw = dl_match.group(1)
            dates = _re.findall(r'(\d{4}-\d{2}-\d{2})', raw)
            links = _re.findall(r"<a[^>]*href=['\"]([^'\"]*)['\"][^>]*>([^<]+)</a>", raw)
            for i in range(min(len(dates), len(links), 10)):
                href, title = links[i]
                articles.append(f"{dates[i]} {title.strip()}")
        if articles:
            source_results["sina"] = {"articles": articles, "label": "新浪财经", "status": "ok"}
            print(f"[新浪财经] {len(articles)}条", end=" ")
        else:
            source_results["sina"] = {"articles": [], "label": "新浪财经", "status": "empty"}
    except Exception as e:
        source_results["sina"] = {"articles": [], "label": "新浪财经", "status": f"error:{str(e)[:30]}"}

    # ── S7: CCTV 财经新闻 ──
    try:
        import akshare as ak
        if hasattr(ak, "news_cctv"):
            today_str = datetime.now().strftime("%Y%m%d")
            df = ak.news_cctv(date=today_str)
            if df is None or df.empty:
                target_dt = datetime.now()
                for back in range(1, 4):
                    probe_dt = target_dt - timedelta(days=back)
                    probe_df = ak.news_cctv(date=probe_dt.strftime("%Y%m%d"))
                    if probe_df is not None and not probe_df.empty:
                        df = probe_df
                        break
            if df is not None and not df.empty:
                articles = []
                for _, row in df.head(15).iterrows():
                    title = str(row.get("title", row.get("标题", "")))
                    content = str(row.get("content", row.get("内容", "")))
                    text = f"{title}"
                    if content and content != "nan":
                        text += f": {content[:300]}"
                    articles.append(text)
                if articles:
                    source_results["cctv"] = {"articles": articles, "label": "CCTV财经", "status": "ok"}
                    print(f"[CCTV财经] {len(articles)}条", end=" ")
                else:
                    source_results["cctv"] = {"articles": [], "label": "CCTV财经", "status": "empty"}
            else:
                source_results["cctv"] = {"articles": [], "label": "CCTV财经", "status": "empty"}
        else:
            source_results["cctv"] = {"articles": [], "label": "CCTV财经", "status": "no_api"}
    except Exception as e:
        source_results["cctv"] = {"articles": [], "label": "CCTV财经", "status": f"error:{str(e)[:30]}"}

    # ── S8: Tavily ──
    tavily_key = os.environ.get("TAVILY_API_KEY", "")
    if tavily_key:
        try:
            query = f"{name} {code} 消息 公告 新闻"
            payload = _json.dumps({"query": query, "search_depth": "basic", "max_results": 5, "time_range": "week"}).encode('utf-8')
            req = urllib.request.Request("https://api.tavily.com/search", data=payload,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {tavily_key}"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = _json.loads(resp.read().decode('utf-8'))
            articles = []
            for r in data.get("results", []):
                title = r.get("title", "")
                content = r.get("content", "")
                articles.append(f"{title}: {content[:300]}")
            if articles:
                source_results["tavily"] = {"articles": articles, "label": "Tavily", "status": "ok"}
                print(f"[Tavily] {len(articles)}条", end=" ")
            else:
                source_results["tavily"] = {"articles": [], "label": "Tavily", "status": "empty"}
        except Exception as e:
            source_results["tavily"] = {"articles": [], "label": "Tavily", "status": f"error:{str(e)[:30]}"}
    else:
        source_results["tavily"] = {"articles": [], "label": "Tavily", "status": "no_key"}

    # ── 全部失败检查 ──
    all_articles = []
    all_news_parts = []
    for src in source_results.values():
        for art in src.get("articles", []):
            all_articles.append(art)
            all_news_parts.append(art)

    if not all_articles:
        error_detail = "; ".join(f"{v['label']}:{v['status']}" for k, v in source_results.items() if v["status"] not in ("no_key", "no_api"))
        _write_news_fallback(code, name, error_detail)
        # v4.2 修复：全源失败不再伪装成中性50分
        # 原实现 return (50, "全部数据源失败") → 下游无法区分"真中性"与
        # "没数据"，负面新闻可能被漏掉（数据缺失被当成中性放行）。
        # 现在返回 (50, 明确标记)，调用方可按 _NEWS_SOURCE_FAILED 识别，
        # 选择降级为更保守的处理（如降低置信度或标记采集失败）。
        return 50, f"⚠️采集失败:{error_detail}"

    all_news_text = "\n".join(all_news_parts[:50])  # LLM input cap

    # ── 关键词评分 ──
    negative_keywords = [
        # 财务/经营类利空
        "亏损", "暴跌", "违约", "诉讼", "处罚", "退市", "暴雷",
        "减值", "减持", "爆仓", "造假", "停产", "重组失败",
        "预警", "跌停", "st ", "*st", "戴帽", "退市风险", "净亏损",
        # 监管/制裁类利空
        "制裁", "SDN", "列入", "黑名单", "调查", "立案",
        "冻结", "查封", "限制", "打压", "出口管制", "处罚决定",
        "通报批评", "监管措施", "立案调查", "立案侦查",
        # 公司治理类利空
        "缺席", "未亲自出席", "代为行使", "身体原因",
        "高管变动", "董事会异常", "董事辞职", "高管离职",
        "减持计划", "大股东减持", "控股股东减持", "实控人减持",
        "质押", "平仓", "强制平仓", "被动减持",
        "信披违规", "信息披露", "违规担保", "资金占用",
        "被立案", "被调查", "被处罚", "被谴责", "被问询",
        "业绩变脸", "由盈转亏", "商誉减值", "资产减值",
    ]
    positive_keywords = [
        "增长", "超预期", "回购", "增持", "中标", "突破",
        "利好", "分红", "盈利", "扩产", "净利润增长", "大涨",
        "扭亏", "预增", "高增",
    ]
    # v5.0：否定词检测 — 避免"终止减持"（利好）被"减持"误判为利空
    # 以及"增速放缓"（利空）被"增长"误判为利好
    negation_patterns = [
        # (否定前缀 + 关键词) → 反转极性
        ("终止", ["减持", "回购", "质押"]),
        ("取消", ["减持", "回购"]),
        ("停止", ["减持", "回购", "质押"]),
        ("未", ["亏损", "违约", "减持", "造假"]),
        ("不会", ["退市", "ST"]),
        ("无", ["处罚", "诉讼", "违规"]),
    ]
    # 语境反转：某些负面词前有"终止/取消"等 → 实际是利好
    # 某些正面词前有"放缓/下滑"等 → 实际是利空
    positive_negation = ["放缓", "下滑", "回落", "减少", "下降", "低于预期", "不达预期", "暂缓", "终止", "取消"]

    def _keyword_polarity(text: str):
        """对单条新闻判断关键词极性：1=正面, -1=负面, 0=中性。含否定语境处理。"""
        t = text.lower()
        # 先检查"正面词被否定" → 利空
        for neg in positive_negation:
            if neg in t:
                # 找否定词后面的正面关键词
                idx = t.find(neg)
                for pk in ["增长", "超预期", "突破", "净利润增长", "预增", "高增", "扭亏"]:
                    if idx >= 0 and pk in t[max(0, idx-20):]:
                        return -1  # "增速放缓/低于预期" → 利空
        # 再检查"负面词被终止/取消" → 利好
        for neg_prefix, kws in negation_patterns:
            if neg_prefix in t:
                idx = t.find(neg_prefix)
                for nk in kws:
                    if idx >= 0 and nk in t[max(0, idx-15):idx+15]:
                        return 1  # "终止减持/取消回购计划" → 利好
        has_neg = any(kw in t for kw in negative_keywords)
        has_pos = any(kw in t for kw in positive_keywords)
        if has_neg and has_pos:
            return 0  # 混合，交给LLM
        if has_neg:
            return -1
        if has_pos:
            return 1
        return 0

    SOURCE_WEIGHTS = {
        "eastmoney": 1.2, "zt_pool": 0.8, "xueqiu": 0.6,
        "iwencai_news": 1.0, "iwencai_ann": 1.0, "sina": 0.8,
        "cctv": 0.8, "tavily": 0.7,
    }
    SENTIMENT_SOURCES = {"zt_pool", "xueqiu"}

    weighted_score_sum = 0.0
    weight_sum = 0.0
    source_scores = {}

    for src_key, src_data in source_results.items():
        articles = src_data.get("articles", [])
        if not articles or src_data["status"] in ("no_key", "no_api"):
            continue

        if src_key in SENTIMENT_SOURCES:
            src_score = 50.0
            src_neg = 0
            src_pos = 0
        else:
            src_neg = 0
            src_pos = 0
            for article in articles:
                article_lower = article.lower()
                if name_lower not in article_lower:
                    continue
                pol = _keyword_polarity(article_lower)
                if pol > 0:
                    src_pos += 1
                elif pol < 0:
                    src_neg += 1

            net = src_pos - src_neg
            # v5.0：线性连续映射，避免分段跳跃（net=3 → 65, net=4 → 74 的不连续）
            src_score = max(15, min(85, 50.0 + net * 5.0))

        w = SOURCE_WEIGHTS.get(src_key, 1.0)
        weighted_score_sum += src_score * w
        weight_sum += w
        source_scores[src_key] = {
            "score": round(src_score, 1), "neg": src_neg, "pos": src_pos,
            "articles": len(articles), "weight": w,
        }

    # 关键词通道分
    if weight_sum > 0:
        keyword_score = weighted_score_sum / weight_sum
    else:
        keyword_score = 50.0

    # ── LLM 语义分析通道 ──
    # 环境变量: LLM_API_ENDPOINT, LLM_API_KEY, LLM_MODEL
    # 评分公式: final = 0.4 * keyword + 0.6 * llm（LLM 不可用时降级到纯关键词）
    llm_score = None
    llm_detail = ""
    try:
        llm_score, llm_detail = _call_llm_sentiment(all_news_text, name, code)
    except Exception as e:
        llm_detail = f"LLM异常:{str(e)[:40]}"

    if llm_score is not None:
        score = 0.4 * keyword_score + 0.6 * llm_score
        llm_status = f"LLM:{llm_score:.1f}"
    else:
        score = keyword_score
        # v5.0：LLM 失败时标记而非静默降级，让下游知道消息面置信度低
        llm_status = f"LLM:失败({llm_detail or '未配置'})"

    if ann_delta != 0:
        score = max(15, min(90, score + ann_delta))

    # ── 构建 detail 字符串 ──
    source_parts = []
    for src_key in ["eastmoney", "zt_pool", "xueqiu", "iwencai_news", "iwencai_ann", "sina", "cctv", "tavily"]:
        if src_key in source_scores:
            info = source_scores[src_key]
            source_parts.append(f"{info['articles']}条{info['score']}分")
        elif src_key in source_results:
            st = source_results[src_key]["status"]
            if st not in ("no_key", "no_api"):
                source_parts.append(f"0条({st})")

    active_count = sum(1 for s in source_results.values() if s["status"] == "ok")
    detail = f"[{active_count}源] " + " | ".join(source_parts)
    detail += f" | {llm_status}"
    if ann_detail and ann_delta != 0:
        detail += f" {ann_detail}"

    # ── 消息明细 ──
    msg_lines = []
    for src_key in ["eastmoney", "iwencai_news", "iwencai_ann", "sina", "cctv", "tavily"]:
        src_data = source_results.get(src_key)
        if not src_data or src_data["status"] in ("no_key", "no_api"):
            continue
        articles = src_data.get("articles", [])
        if not articles:
            continue
        label = src_data["label"]
        for article in articles:
            article_lower = article.lower()
            has_neg = any(kw in article_lower for kw in negative_keywords)
            has_pos = any(kw in article_lower for kw in positive_keywords)
            if has_neg and not has_pos:
                tag = "负面"
            elif has_pos and not has_neg:
                tag = "正面"
            elif has_neg and has_pos:
                tag = "混合"
            else:
                tag = "中性"
            title = article[:80].replace("\n", " ")
            msg_lines.append(f"[{label}][{tag}] {title}")

    if msg_lines:
        detail += "\n" + "\n".join(msg_lines)

    # v5.4(B-23): fallback 标记"只写不清"修复——旧实现全源失败时写
    # .news_fallback_{code}.json，但 clear_news_fallback 零调用方，后续补扫
    # 成功也不清，幽灵 pending 永久堆积误导 list_news_fallbacks 消费方。
    # 对齐数据链 B-04 纪律：清理必须挂在成功路径。本函数能走到 return 即
    # 视为本次扫描完成（含部分源失败但整体有产出），清除该股历史标记。
    try:
        clear_news_fallback(code)
    except Exception:
        pass  # 清理失败不阻断扫描结果返回

    return round(score, 1), detail


def _call_llm_sentiment(all_news_text: str, name: str, code: str):
    """LLM语义分析通道 — 调用SenseTime DeepSeek-V4-Flash进行情感评分"""
    import os

    api_endpoint = os.environ.get("LLM_API_ENDPOINT", "https://token.sensenova.cn/v1/chat/completions")
    api_key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "deepseek-v4-flash")

    if not api_key:
        return None, "LLM未配置(需LLM_API_KEY)"

    # 截断输入以控制token成本
    truncated = all_news_text[:3000]

    prompt = f"""你是一位A股消息面分析专家。请基于以下{name}({code})的新闻资讯，评估消息面情绪。

新闻资讯：
{truncated}

请综合考虑：业绩增速、政策环境、行业趋势、机构态度、分红回购、负面事件等因素。

只输出一个0-100的数字评分（50=中性，>50=利好，<50=利空），不要输出任何解释或额外文字。"""

    try:
        import urllib.request
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 200,
            "temperature": 0.1,
        }).encode("utf-8")

        # v5.4(DS-02·用户拍板2026-08-24): 鉴权头按端点自动适配——旧实现硬编码
        # sensenova 的 "api-key" 前缀, 而所有 OpenAI 兼容端点(Agnes/DeepSeek
        # 官方/OpenRouter等)要求标准 "Bearer", 这些 key 一律 401, LLM 通道
        # 名存实亡(审计DS-02)。sensenova 域名保留 api-key, 其余用 Bearer。
        if "sensenova" in api_endpoint:
            _auth = f"api-key {api_key}"
        else:
            _auth = f"Bearer {api_key}"

        req = urllib.request.Request(
            api_endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": _auth,
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        choices = data.get("choices", [{}])
        if not choices:
            return None, "LLM返回空choices"
        
        msg = choices[0].get("message", {})
        content = msg.get("content", "").strip()
        reasoning = msg.get("reasoning_content", "").strip()
        
        # Try content first, then reasoning
        score = None
        for text in [content, reasoning]:
            if not text:
                continue
            try:
                score = float(text)
                break
            except ValueError:
                import re
                numbers = re.findall(r'\d+\.?\d*', text)
                if numbers:
                    score = float(numbers[-1])
                    break
        
        if score is None:
            return None, f"无法解析LLM输出: content='{content[:50]}', reasoning='{reasoning[:50]}'"
        
        score = max(0, min(100, score))
        return score, f"LLM评分={score}"
    except Exception as e:
        return None, f"LLM调用失败:{str(e)[:40]}"


def _write_news_fallback(code: str, name: str, error_detail: str):
    """全部数据源失败时写 fallback 标记"""
    signals_dir = os.path.join(_SCRIPT_DIR, "signals")
    os.makedirs(signals_dir, exist_ok=True)
    fallback_path = os.path.join(signals_dir, f".news_fallback_{code}.json")
    with open(fallback_path, 'w') as f:
        json.dump({
            "code": code, "name": name, "error": error_detail,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "pending",
        }, f, ensure_ascii=False, indent=2)


def list_news_fallbacks() -> list:
    """列出所有待补扫的 fallback 标记"""
    signals_dir = os.path.join(_SCRIPT_DIR, "signals")
    if not os.path.exists(signals_dir):
        return []
    import glob
    fallbacks = []
    for fp in glob.glob(os.path.join(signals_dir, ".news_fallback_*.json")):
        with open(fp) as f:
            fallbacks.append(json.load(f))
    return fallbacks


def clear_news_fallback(code: str):
    """清除已处理的 fallback 标记"""
    fallback_path = os.path.join(_SCRIPT_DIR, "signals", f".news_fallback_{code}.json")
    if os.path.exists(fallback_path):
        os.remove(fallback_path)
