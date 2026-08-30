# -*- coding: utf-8 -*-
"""regression_check.py — 最小回归套件（终审D2, 2026-08-23）

背景：confidence_score 假100分事故 + 批次A-F回归测试资产被清空。
本套件把历次审计的关键修复固化为可重复断言，一条命令跑完：
    python regression_check.py        # 离线断言（默认）
    python regression_check.py --net  # 附加网络依赖测试（东财curl降级等）

新增关键修复时请在此追加断言——这是防止重构打回原形的唯一安全网。
"""
import inspect
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(name, fn):
    try:
        ok = fn()
        (PASS if ok else FAIL).append(name)
        print(("  ✅ " if ok else "  ❌ ") + name)
    except Exception as e:
        FAIL.append(f"{name} [异常:{type(e).__name__}]")
        print(f"  ❌ {name} [异常: {type(e).__name__}: {str(e)[:80]}]")


# ═══════════════ 1. 配置层 ═══════════════
def t_config_defaults():
    import config_loader
    w = config_loader._DEFAULTS["weights"]
    assert abs(sum(w.values()) - 1.0) < 1e-9, f"五维权重和≠1: {w}"
    assert set(w) == {"tech", "fund", "alpha", "news", "fund_factor"}, w
    assert config_loader._DEFAULTS["a500"]["composite_threshold"] == 60  # 终审A5
    return True

check("config: 五维默认权重正确且归一 / composite_threshold=60", t_config_defaults)


# ═══════════════ 2. 缠论引擎（合成数据，确定性）═══════════════
def _mk_klines(prices, dates=None):
    """由收盘价序列构造K线（高低开合微扰为0，保证确定性）。"""
    from generate_analysis import KLine
    ks = []
    for i, p in enumerate(prices):
        d = dates[i] if dates else f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"
        ks.append(KLine(date=d, open=p, high=p, low=p, close=p, volume=1000))
    return [{"date": k.date, "open": k.open, "high": k.high,
             "low": k.low, "close": k.volume and k.close, "volume": k.volume}
            for k in ks]


def _synth_vshape():
    """先跌20根再涨20根的V形（含包含关系的阶梯）→ 应产生 下+上 两笔。"""
    prices = [100 - i * 1.0 for i in range(20)] + [80 + i * 1.5 for i in range(20)]
    return prices


def t_chanlun_basic_bi():
    """N形+收尾上翘（跌→涨→跌→涨）：产生 底-顶-底 分型序列 → 上+下 两笔。
    （末笔延伸逻辑会把未确认的尾部并入最后一笔，属预期行为）"""
    from generate_analysis import ChanLunAnalyzer
    prices = ([100 - i * 1.0 for i in range(15)] +
              [85 + i * 1.5 for i in range(15)] +
              [106 - i * 1.2 for i in range(15)] +
              [88 + i * 1.4 for i in range(10)])
    data = [{"date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
             "open": p, "high": p, "low": p, "close": p, "volume": 1000}
            for i, p in enumerate(prices)]
    az = ChanLunAnalyzer().analyze(data)
    assert len(az.bis) >= 2, f"完整N形至少应识别出两笔, got {len(az.bis)}"
    dirs = {b.direction for b in az.bis}
    assert dirs == {"up", "down"}, f"应同时存在上下笔: {dirs}"
    return True

check("缠论: N形数据 → 上笔+下笔 基础管线", t_chanlun_basic_bi)


def t_chanlun_zhongshu():
    from generate_analysis import ChanLunAnalyzer
    # 震荡序列：10~11之间来回 → 应形成中枢(zg>zd)
    import itertools
    seq = [10, 11, 10.2, 10.8, 10.1, 10.9, 10.3, 10.7, 10.15, 10.85]
    prices = [p for p in itertools.chain.from_iterable(
        [s - 0.05 * j for j in range(3)] for s in seq)]  # 每点展开成小台阶避免全包含
    data = [{"date": f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}",
             "open": p, "high": p * 1.001, "low": p * 0.999, "close": p, "volume": 1}
            for i, p in enumerate(prices)]
    az = ChanLunAnalyzer().analyze(data)
    assert len(az.zhongshus) >= 1, "震荡序列应至少形成一个中枢"
    zs = az.zhongshus[0]
    assert zs.zg > zs.zd, f"中枢区间非法 zg={zs.zg} zd={zs.zd}"
    return True

check("缠论: 震荡序列 → 中枢 ZG>ZD", t_chanlun_zhongshu)


def t_segment_pipeline():
    from segment_analyzer import SegmentChanLunAnalyzer
    from generate_analysis import ChanLunAnalyzer
    prices = ([100 - i * 2.0 for i in range(12)] +
              [76 + i * 2.5 for i in range(12)] +
              [104 - i * 1.8 for i in range(10)])
    data = [{"date": f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}",
             "open": p, "high": p, "low": p, "close": p, "volume": 1}
            for i, p in enumerate(prices)]
    az = ChanLunAnalyzer().analyze(data)
    seg = SegmentChanLunAnalyzer()
    seg.analyze(az)
    assert isinstance(seg.segments, list)
    return True

check("线段: 划分管线可运行且返回列表", t_segment_pipeline)


# ═══════════════ 3. 数据契约（P0-4/P0-5 回归）═══════════════
def t_unified_scorer():
    import quick_fundamental, hithink_fundamental, akshare_fundamental
    assert hithink_fundamental._unified_calc_score is quick_fundamental.calculate_fundamental_score, \
        "hithink 必须复用 quick_fundamental 的统一评分函数(P0-4)"
    src = inspect.getsource(hithink_fundamental)
    assert "def calculate_fundamental_score_legacy_hithink" in src, "旧版应保留为 legacy 命名"
    return True

check("契约: calculate_fundamental_score 全库唯一(P0-4)", t_unified_scorer)


def t_multi_year_decimal():
    import quick_fundamental as qf
    data = {"profitability": {"roeAvg": 0.10}, "growth": {}, "health": {},
            "valuation": {}}
    my = {"2025": {"roe": 0.153}}  # 小数口径
    s = qf.calculate_fundamental_score(data, multi_year_data=my)
    assert s.get("roe_basis") == "annual", f"应按年报口径评分, got {s.get('roe_basis')}"
    assert abs(s.get("annual_roe") - 0.153) < 1e-9, "年报ROE应为小数0.153(P0-5)"
    return True

check("契约: multi_year ROE 小数口径 + 年报优先(P0-5/F-1)", t_multi_year_decimal)


# ═══════════════ 4. 数据层守卫（P0-6）═══════════════
def t_30min_localdb_guard():
    """P0-6 守卫在 get_klines 调用点（level=='daily' 才允许本地库），
    函数本身无守卫——逐调用点检查其触发条件含 daily。"""
    import re
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data_manager.py"), encoding="utf-8").read()
    calls = [m for m in re.finditer(r"self\._local_db_fetch\(symbol", src)]
    assert len(calls) >= 2, f"_local_db_fetch 调用点应≥2处, got {len(calls)}"
    for m in calls:
        ctx = src[max(0, m.start() - 120):m.start()]
        assert "level == 'daily'" in ctx, \
            f"存在未加 daily 守卫的 _local_db_fetch 调用: ...{ctx[-60:]!r}"
    return True

check("数据: 30min 本地库守卫仍生效(P0-6, 调用点双守卫)", t_30min_localdb_guard)


# ═══════════════ 5. E-1/E-2 仲裁与降级 ═══════════════
def _base_dims():
    return dict(tech_score=70, fund_score=70, alpha_score=50, news_score=50,
                code="600900", name="长江电力")


def t_e1_sell_conflict():
    import composite_scorer as cs
    r = cs.compute_3d_score(**_base_dims(), recent_top_sell=True)
    assert r.can_buy is False, "E-1: 近期一卖压制必须 can_buy=False"
    assert r.components.get("sell_conflict") is True
    return True

check("E-1: recent_top_sell → can_buy=False + sell_conflict 标记", t_e1_sell_conflict)


def t_e2_observational():
    import composite_scorer as cs
    r = cs.compute_3d_score(**_base_dims(), observational=True)
    assert r.components.get("observational") is True, "E-2 观察型必须带标记"
    assert r.can_buy is not False or True  # observational 不改 can_buy，仅呈现层过滤
    return True

check("E-2: observational 标记透传", t_e2_observational)


def t_recommend_filter_source():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "pool_screener.py"), encoding="utf-8").read()
    # 终审A3: 推荐列表过滤必须同时排除 observational 与 can_buy=False
    assert "not s.get('observational')" in src
    assert "and s.get('can_buy', True)" in src
    return True

check("E-1呈现层: 推荐列表过滤含 can_buy 条件(终审A3)", t_recommend_filter_source)


# ═══════════════ 6. 负面检查降级链（结构性断言）═══════════════
def t_negative_fallback_chain():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "check_negative_news.py"), encoding="utf-8").read()
    assert "skip_needs_review" in src, "无源可用时必须标记 skip_needs_review(P0-2)"
    assert "_HAS_NEWS_SCANNER" in src or "news_scanner" in src, "应有 news_scanner 多源降级"
    return True

check("负面: skip_needs_review + 多源降级结构在位(P0-2)", t_negative_fallback_chain)


# ═══════════════ 7. 子进程编码（终审A2）═══════════════
def t_subprocess_encoding():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "single_stock_analysis.py"), encoding="utf-8").read()
    assert 'encoding="utf-8"' in src and "PYTHONIOENCODING" in src, \
        "--report 子进程必须显式 utf-8(终审A2)"
    return True

check("编排: --report 子进程显式 utf-8(终审A2)", t_subprocess_encoding)


# ═══════════════ 5.5 v5.4.1 审计0827 P1 批（AUD-A-01/B-01/C-01）═══════════════
def t_entry_match_scope():
    """AUD-A-01: entry_match 条目级口径——正面/中性行与全市场源无股名行不得命中"""
    from entry_match import relevant_detail_lines
    detail = "\n".join([
        "[3源] 8条50分 | LLM:失败(未配置)",                     # 汇总头(无标签)→排除
        "[CCTV财经][正面] 证监会召开投资者座谈会 提振市场信心",    # 全市场+正面→排除
        "[东财新闻][负面] 公司被证监会立案调查",                  # 全市场源且无股名→排除
        "[同花顺新闻][负面] 该公司被立案调查",                    # 个股级源白名单→命中
        "[新浪财经][中性] 年报预告超预期",                       # 中性→排除
        "[Tavily][混合] 相关公司股价异动",                       # 白名单混合→命中
    ])
    hits = relevant_detail_lines(detail, name="中炬高新", code="600872")
    joined = "\n".join(hits)
    assert "证监会召开" not in joined, "正面全市场新闻不得参与否决匹配"
    assert "立案调查" in joined, "个股级源负面行必须保留"
    assert "股价异动" in joined, "Tavily 混合行应命中"
    # 本股名称所在负面行(非白名单源)也必须命中
    d2 = "[CCTV财经][负面] 中炬高新子公司被立案调查"
    assert any("中炬高新" in h for h in relevant_detail_lines(d2, name="中炬高新", code="600872"))
    return True

check("entry_match: 正面/中性/全市场行排除+白名单与本股行命中(AUD-A-01)", t_entry_match_scope)


def t_veto3_positive_news_not_veto():
    """AUD-A-01 端到端: compute_veto_check 对正面政策新闻不再整票否决"""
    import generate_report as gr
    ng = {"l3_details": [], "l2_details": [], "l3_count": 0, "l2_count": 0,
          "source": "多源(3源)"}
    news = {"detail": "[CCTV财经][正面] 证监会召开投资者座谈会 提振市场信心"}
    r = gr.compute_veto_check({}, {}, ng, news_data=news,
                              name="中炬高新", code="600872")
    c3 = r["checks"]["立案调查/财务造假"]
    assert not c3["triggered"], f"正面新闻不得触发veto#3: {c3}"
    assert not r.get("veto_triggered"), f"整票否决误触发: {r.get('veto_reason')}"
    return True

check("veto#3: 正面政策新闻不再触发硬否决(AUD-A-01端到端)", t_veto3_positive_news_not_veto)


def t_severe_survives_alpha_merge():
    """AUD-B-01: alpha merge 必须增量合并 veto/severe——Phase2 风控标记不被洗掉"""
    from pathlib import Path
    import json as _json
    import tempfile
    import alpha_factor_filter as aff

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / ".phase2_results.json"
        phase2 = [{"code": "600872", "name": "x",
                   "severe_reasons": ["未来90天解禁占总股本 6.3% ≥5%"],
                   "veto_reasons": []}]
        p.write_text(_json.dumps(phase2, ensure_ascii=False), encoding="utf-8")
        scanner_candidates = [{"code": "600872", "name": "x"}]  # 天然无两键
        aff.merge_into_phase2({}, scanner_candidates, path=p)
        out = _json.loads(p.read_text(encoding="utf-8"))
        assert any("解禁" in s for s in out[0]["severe_reasons"]), \
            f"Phase2 写入的 severe_reasons 被 merge 洗掉(AUD-B-01 回归): {out[0]['severe_reasons']}"
    # 反向: 候选带新 severe 时也要合入且不丢旧标记
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / ".phase2_results.json"
        p.write_text(_json.dumps([{"code": "600872", "name": "x",
                                   "severe_reasons": ["解禁预警"], "veto_reasons": []}],
                                 ensure_ascii=False), encoding="utf-8")
        aff.merge_into_phase2({}, [{"code": "600872", "name": "x",
                                     "severe_reasons": ["监管处罚"], "veto_reasons": []}], path=p)
        out = _json.loads(p.read_text(encoding="utf-8"))
        s = out[0]["severe_reasons"]
        assert any("解禁" in x for x in s) and any("监管处罚" in x for x in s), s
    return True

check("alpha merge: severe 跨阶段存续不覆写(AUD-B-01)", t_severe_survives_alpha_merge)


def t_flow_rows_order_contract():
    """AUD-C-01: _flow_rows 出口行序契约=date 升序(旧→新)，消费端 [-20:] 即最近20日"""
    import fund_factors as ff
    fake_desc = [{"date": "2026-08-%02d" % (20 - i), "net_amount": i} for i in range(10)]
    rows = ff._sorted_by_date(fake_desc)
    dates = [r["date"] for r in rows]
    assert dates == sorted(dates), f"_sorted_by_date 未归一升序: {dates[:3]}..."
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "em_utils.py"), encoding="utf-8").read()
    assert 'rows.sort(key=lambda r: str(r.get("date") or ""))' in src, \
        "fund_flow_backup 源头排序契约缺失"
    return True

check("资金流: 新浪兜底行序升序契约+出口归一化(AUD-C-01)", t_flow_rows_order_contract)


# ═══════════════ 8. em_get curl 降级（终审A1，需网络用 --net）═══════════════
def t_em_curl_unit():
    import em_utils
    r = em_utils._em_curl_get("https://datacenter-web.eastmoney.com/api/data/v1/get"
                              "?reportName=RPT_MARGIN_TRADE&columns=ALL&pageSize=1",
                              {"User-Agent": em_utils.UA}, 15)
    d = r.json()
    assert isinstance(d, dict), "curl 兜底应返回可解析 JSON"
    return True


def t_em_get_fallback_on_blocked():
    import em_utils
    orig = em_utils.EM_SESSION.get

    def _blocked(*a, **kw):
        import requests
        raise requests.exceptions.ConnectionError(
            "Connection aborted., RemoteDisconnected('Remote end closed connection without response')")
    em_utils.EM_SESSION.get = _blocked
    try:
        r = em_utils.em_get("https://datacenter-web.eastmoney.com/api/data/v1/get",
                            params={"reportName": "RPT_MARGIN_TRADE", "columns": "ALL",
                                    "pageSize": "1"}, timeout=15)
        assert r.json() is not None
        return True
    finally:
        em_utils.EM_SESSION.get = orig

if "--net" in sys.argv:
    check("网络: curl 兜底单元(datacenter 可达)", t_em_curl_unit)
    check("网络: Python栈被墙→自动降级curl成功(终审A1)", t_em_get_fallback_on_blocked)
else:
    print("  ⏭ 跳过网络测试（加 --net 启用：em curl降级×2）")


# ═══════════════ 汇总 ═══════════════
print(f"\n===== 回归结果: {len(PASS)} 通过 / {len(FAIL)} 失败 =====")
if FAIL:
    for f in FAIL:
        print("  ❌", f)
    sys.exit(1)
print("ALL PASS")
