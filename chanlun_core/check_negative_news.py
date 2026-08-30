"""
check_negative_news.py — 负面消息监控脚本（cronjob 调用）

扫描监控列表中的股票，搜索最近 24 小时的重大负面消息。
L2/L3 级消息立即标红推送。

用法：
  python check_negative_news.py                        # 扫描全部18只
  python check_negative_news.py --stocks 601318,300059  # 指定部分
  python check_negative_news.py --hours 48              # 搜索48小时内
"""

import sys, json, os, argparse
from date_utils import date_to_str, parse_date_to_datetime
from datetime import datetime, timedelta
from typing import List, Dict

# 多数据源新闻扫描（与 A500/三维分析共享）
# v5.4.1(AUD-A-06): _HAS_NEWS_SCANNER 仅作可用性探测；实际调用走
# scan_news_cached（见 search_negative 内惰性导入）
try:
    import news_scanner as _ns_mod  # noqa: F401
    _HAS_NEWS_SCANNER = True
except ImportError:
    _HAS_NEWS_SCANNER = False


def _load_monitor_list():
    """加载监控列表。优先从 xlsx 读取，行业从映射表或股票名称自动推断。"""
    xlsx_path = "D:/常用文件/自选股负面消息清单/自选股清单.xlsx"
    
    # 行业映射表（精确覆盖，优先使用）
    _INDUSTRY_MAP = {
        "600309": "化工", "600346": "化工", "600486": "化工", "000830": "化工",
        "600298": "食品", "300783": "消费", "601888": "消费",
        "002714": "农牧", "601615": "新能源", "300772": "新能源",
        "601155": "地产", "000002": "地产", "002271": "建材",
        "601601": "保险", "601318": "保险", "300059": "金融",
        "000001": "银行", "002415": "安防",
    }
    
    # 行业关键词（从股票名称自动推断，映射表未命中时使用）
    _NAME_INDUSTRY_HINTS = [
        ("银行", "银行"), ("保险", "保险"), ("证券", "金融"),
        ("化工", "化工"), ("钢铁", "钢铁"), ("煤炭", "煤炭"),
        ("医药", "医药"), ("医疗", "医药"), ("生物", "医药"),
        ("新能源", "新能源"), ("风电", "新能源"), ("光伏", "新能源"), ("锂电", "新能源"),
        ("地产", "地产"), ("置地", "地产"), ("城建", "地产"),
        ("建材", "建材"), ("水泥", "建材"), ("玻璃", "建材"),
        ("食品", "食品"), ("酒", "食品"), ("乳", "食品"), ("饮料", "食品"),
        ("农牧", "农牧"), ("牧原", "农牧"), ("农业", "农牧"), ("养殖", "农牧"),
        ("科技", "科技"), ("软件", "科技"), ("微电子", "科技"), ("信息", "科技"),
        ("汽车", "汽车"), ("客车", "汽车"), ("动力", "汽车"),
        ("家电", "家电"), ("电器", "家电"),
        ("有色", "有色"), ("矿业", "有色"), ("黄金", "有色"),
        ("石油", "石油"), ("石化", "石油"),
        ("交通", "交通运输"), ("航空", "交通运输"), ("铁路", "交通运输"), ("港口", "交通运输"),
        ("电力", "公用事业"), ("燃气", "公用事业"), ("水务", "公用事业"),
        ("消费", "消费"), ("百货", "消费"), ("零售", "消费"), ("免税", "消费"),
        ("环保", "环保"),
    ]
    
    def _infer_industry(name: str, code: str) -> str:
        """从股票名称 + 代码 推断行业"""
        if code in _INDUSTRY_MAP:
            return _INDUSTRY_MAP[code]
        for keyword, industry in _NAME_INDUSTRY_HINTS:
            if keyword in name:
                return industry
        # 知名股票精确匹配（名称不含行业关键词的）
        _WELL_KNOWN = {
            "宁德时代": "新能源", "比亚迪": "汽车", "隆基绿能": "新能源",
            "药明康德": "医药", "中芯国际": "科技", "韦尔股份": "科技",
            "美的集团": "家电", "格力电器": "家电", "海尔智家": "家电",
            "迈瑞医疗": "医药", "恒瑞医药": "医药", "片仔癀": "医药",
            "海螺水泥": "建材", "三一重工": "制造",
        }
        if name in _WELL_KNOWN:
            return _WELL_KNOWN[name]
        return "其他"
    
    try:
        import openpyxl
        wb = openpyxl.load_workbook(xlsx_path, read_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        wb.close()
        result = []
        for row in rows:
            if row[0] is None:
                continue
            code = str(int(row[0])).zfill(6) if isinstance(row[0], (int, float)) else str(row[0]).strip()
            name = str(row[1]).strip() if len(row) > 1 and row[1] else code
            # 行业：xlsx第三列 > 映射表 > 名称推断
            if len(row) > 2 and row[2] and str(row[2]).strip():
                industry = str(row[2]).strip()
            else:
                industry = _infer_industry(name, code)
            result.append((code, name, industry))
        if result:
            return result
    except Exception:
        pass
    
    # 回退：硬编码默认值
    return [
        ("600309", "万华化学", "化工"), ("600346", "恒力石化", "化工"),
        ("600486", "扬农化工", "化工"), ("000830", "鲁西化工", "化工"),
        ("600298", "安琪酵母", "食品"), ("300783", "三只松鼠", "消费"),
        ("601888", "中国中免", "消费"), ("002714", "牧原股份", "农牧"),
        ("601615", "明阳智能", "新能源"), ("300772", "运达股份", "新能源"),
        ("601155", "新城控股", "地产"), ("000002", "万科A", "地产"),
        ("002271", "东方雨虹", "建材"), ("601601", "中国太保", "保险"),
        ("601318", "中国平安", "保险"), ("300059", "东方财富", "金融"),
        ("000001", "平安银行", "银行"), ("002415", "海康威视", "安防"),
    ]


# 监控列表（自动从 xlsx 加载，失败则用硬编码默认值）
MONITOR_LIST = _load_monitor_list()

# v5.3.4(C1): 负面关键词库提取为模块常量（iwencai 与多源降级共用）
# v5.3.4(C1): 负面关键词库提取为模块常量（iwencai 与多源降级共用）
# v5.4(B-05①): "st" → "*st"——裸"st"是小写子串匹配, 英文 best/first/most/stock
# 全部误中('*ST泉为'教训的另一半)。"*ST".lower()='*st' 精确命中戴帽股名。
NEG_KEYWORDS = ["亏损", "暴跌", "违约", "诉讼", "处罚", "退市", "暴雷", "减值", "减持",
                "爆仓", "造假", "停产", "重组失败", "预警", "跌停", "*st", "戴帽",
                "制裁", "SDN", "黑名单", "调查", "立案", "冻结", "查封",
                "通报批评", "监管措施", "立案调查", "立案侦查"]

# L3/L2 分级关键词（与 main() --full 分支保持一致）
_LEVEL3_KW = ["立案调查", "立案侦查", "造假", "退市", "暴雷"]
_LEVEL2_KW = ["减持", "处罚", "诉讼", "制裁", "立案", "冻结"]


def search_iwencai(code: str, name: str, hours: int = 24) -> dict:
    """同花顺问财 API 搜索负面消息"""
    api_key = os.environ.get("IWENCAI_API_KEY", "")
    if not api_key:
        return {"source": "skip", "results": [], "error": "无IWENCAI_API_KEY"}
    try:
        import secrets, urllib.request, json as _json
        trace_id = secrets.token_hex(32)
        payload = _json.dumps({
            "channels": ["news"],
            "app_id": "AIME_SKILL",
            "query": f"{name} {code} 财经新闻",
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://openapi.iwencai.com/v1/comprehensive/search",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "X-Claw-Call-Type": "normal",
                "X-Claw-Skill-Id": "news-search",
                "X-Claw-Skill-Version": "1.0.0",
                "X-Claw-Plugin-Id": "none",
                "X-Claw-Plugin-Version": "none",
                "X-Claw-Trace-Id": trace_id,
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        if data.get("status_code") != 0:
            return {"source": "同花顺", "results": [], "error": data.get("status_msg", "?")}
        hits = data.get("data", [])
        results = []
        neg_kw = NEG_KEYWORDS
        for item in hits[:10]:
            title = item.get("title", "")
            summary = item.get("summary", "")
            date = str(item.get("publish_date", ""))[:10]
            # ⚠️ v4.2 修复：--hours 时间过滤真正生效
            # 原实现 hours 参数传入但从未使用 → 24小时监控实际返回全部
            # 历史新闻，旧闻（数月前的处罚/诉讼）反复报警。
            # 现在：新闻日期距今 > hours 小时 → 跳过
            if date and len(date) >= 10:
                try:
                    pub_dt = datetime.strptime(date, "%Y-%m-%d")
                    if (datetime.now() - pub_dt).total_seconds() > hours * 3600:
                        continue  # 超出时间窗口的旧闻跳过
                except Exception:
                    pass  # 日期解析失败不拦截（保守）
            # v5.4(B-05①): 个股相关性过滤——iwencai 查询"{name} {code} 财经新闻"
            # 会召回行业综述/他股新闻, '*ST泉为被立案'出现在标题即可误杀本股
            # (审计C1实证)。与多源分支(v5.3.4-C1)同口径: 名称或代码必须出现在标题。
            if (name not in title) and (code not in title):
                continue
            text = f"{title} {summary}".lower()
            neg_hits = [kw for kw in neg_kw if kw in text]
            if neg_hits:
                results.append({
                    "title": title, "summary": summary[:200],
                    "date": date, "neg_hits": neg_hits,
                })
        return {"source": "同花顺", "results": results, "error": ""}
    except Exception as e:
        return {"source": "同花顺", "results": [], "error": str(e)[:60]}


def search_negative(code: str, name: str, hours: int = 24) -> dict:
    """统一负面搜索入口（v5.3.4-C1 审计P0-2：无 key 不再直接跳过）。

    降级链：同花顺 iwencai（有 key 时）→ news_scanner 多源
    （东财/雪球/新浪/CCTV/Tavily，零 key 可用，与 A500/三维分析共享）
    → skip_needs_review。

    返回结构与 search_iwencai 一致：{source, results, error}。
    ⚠️ source 以 "skip" 开头 = 负面检查**未执行**（审计C3：skip≠无负面），
    消费方必须按"需人工复核"处理，不得当作"无负面信号"。
    """
    api_key = os.environ.get("IWENCAI_API_KEY", "")
    if api_key:
        r = search_iwencai(code, name, hours)
        if r.get("source") == "同花顺" and not r.get("error"):
            return r
        # 有 key 但调用失败/接口报错 → 继续走多源降级

    if _HAS_NEWS_SCANNER:
        try:
            # v5.4.1(AUD-A-06): 单飞缓存——三维编排中 news 线程已在扫同一股时
            # 复用其结果, 不再双跑八源采集+双LLM；独立运行时首飞行为不变。
            from news_scanner import scan_news_cached as _ns_cached
            _score, detail = _ns_cached(code, name)
            # v5.4(B-03): 全源采集失败拦截——scan_news 失败时返回 (50,"⚠️采集失败:…"),
            # 旧实现照常解析, source="多源(⚠️采集失败…)"不以 skip 开头 → 下游渲染成
            # "已检查·无负面信号"(否决层盲区, 审计B-03)。显式转为需人工复核。
            if not detail or detail.startswith("⚠️采集失败") or detail.startswith("[0源]"):
                return {
                    "source": "skip_needs_review",
                    "results": [],
                    "error": f"多源扫描全源失败(score={_score})——负面检查未执行，需人工复核",
                }
            lines = detail.split(chr(10)) if detail else []
            src_first = lines[0].strip() if lines else "多源"
            results = []
            for msg_line in lines[1:]:
                # 明细行格式："[来源][正面/负面/混合] 标题"
                if ("负面" not in msg_line) and ("混合" not in msg_line):
                    continue
                parts = msg_line.split("] ", 1)
                title = (parts[1] if len(parts) > 1 else msg_line).strip()
                # v5.3.4(C1修正): 个股相关性过滤——多源的 CCTV/市场频道会返回
                # 全市场新闻（实测把"一带一路"类消息标成"混合"混入），与个股
                # 无关的一律不计入本股负面（负面检查宁缺勿滥，避免误报否决）
                if (name not in title) and (code not in title):
                    continue
                neg_hits = [kw for kw in NEG_KEYWORDS if kw in title.lower()]
                level = ("L3" if any(kw in msg_line for kw in _LEVEL3_KW)
                         else "L2" if any(kw in msg_line for kw in _LEVEL2_KW)
                         else "L1")
                date = title[:10] if title[:10].count("-") == 2 else ""
                # v5.4(B-22 否定词窗口): 与 iwencai 分支 v4.2 过滤同口径——
                # 明细行带日期且距今超过 hours 窗口的旧闻不计入本股负面，
                # 防止陈年处罚/诉讼反复触发否决（旧实现取了日期却从不过滤，
                # 窗口向前无界）。日期解析失败不拦截（保守，与 v4.2 一致）。
                if date and hours and hours > 0:
                    try:
                        _pub_dt = datetime.strptime(date, "%Y-%m-%d")
                        if (datetime.now() - _pub_dt).total_seconds() > hours * 3600:
                            continue
                    except ValueError:
                        pass
                results.append({
                    "title": title[:120], "summary": "", "date": date,
                    "neg_hits": neg_hits, "level": level,
                })
            return {
                "source": f"多源({src_first})",
                "results": results,
                "error": "" if lines else "多源扫描无返回",
            }
        except Exception as e:
            _downgrade_err = str(e)[:60]
    else:
        _downgrade_err = "news_scanner 未安装"

    return {
        "source": "skip_needs_review",
        "results": [],
        "error": f"无IWENCAI_API_KEY且多源降级失败({_downgrade_err})——负面检查未执行，需人工复核",
    }


def format_report(all_news: List[dict], hours: int = 24) -> str:
    """格式化新闻监控报告"""
    if not all_news:
        return ""
    
    l3 = [n for n in all_news if n.get('level') == 'L3']
    l2 = [n for n in all_news if n.get('level') == 'L2']
    l1 = [n for n in all_news if n.get('level') == 'L1']
    info = [n for n in all_news if n.get('level') not in ('L3', 'L2', 'L1')]
    
    lines = []
    lines.append(f"═══════════════════════════════")
    lines.append(f"  负面消息监控 — 最近{hours}小时")
    lines.append(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"═══════════════════════════════")
    
    if l3:
        lines.append(f"\n🚨 L3 致命级（{len(l3)}条）:")
        for n in l3:
            lines.append(f"  • [{n['symbol']} {n['name']}] {n['title']}")
            lines.append(f"    来源: {n.get('source', '未知')} | {n.get('date', '')}")
            lines.append(f"    {n.get('summary', '')}")
    
    if l2:
        lines.append(f"\n🔴 L2 重大级（{len(l2)}条）:")
        for n in l2:
            lines.append(f"  • [{n['symbol']} {n['name']}] {n['title']}")
            lines.append(f"    来源: {n.get('source', '未知')}")
    
    if l1:
        lines.append(f"\n🟡 L1 中等（{len(l1)}条）:")
        for n in l1:
            lines.append(f"  • [{n['symbol']} {n['name']}] {n['title']}")
    
    if info:
        lines.append(f"\nℹ 其他消息（{len(info)}条）:")
        for n in info:
            lines.append(f"  • [{n['symbol']} {n['name']}] {n['title']}")
    
    if not all_news:
        lines.append(f"\n✅ 最近{hours}小时无负面消息")
    
    lines.append("\n═══════════════════════════════")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="负面消息监控")
    parser.add_argument('--stocks', help='逗号分隔的股票代码列表')
    parser.add_argument('--name', help='股票名称（与 --stocks 配对使用，逗号分隔）')
    parser.add_argument('--hours', type=int, default=24, help='搜索时间范围（小时）')
    parser.add_argument('--json', action='store_true', help='输出 JSON 格式（供主Agent消费）')
    parser.add_argument('--full', action='store_true',
                        help='使用多数据源扫描（news_scanner: 东财/涨停池/雪球/同花顺/新浪/CCTV/Tavily），默认仅同花顺')
    args = parser.parse_args()
    
    # 筛选股票
    monitor = MONITOR_LIST
    if args.stocks:
        codes = set(s.strip() for s in args.stocks.split(','))
        names = {}
        if args.name:
            name_list = [n.strip() for n in args.name.split(',')]
            for i, code in enumerate(codes):
                if i < len(name_list) and name_list[i]:
                    names[code] = name_list[i]
        # 先过滤已有列表
        monitor = [m for m in MONITOR_LIST if m[0] in codes]
        # 补充不在列表中的股票（动态创建）
        known_codes = {m[0] for m in MONITOR_LIST}
        for code in codes:
            if code not in known_codes:
                stock_name = names.get(code, code)
                monitor.append((code, stock_name, "其他"))
    
    if not monitor:
        if args.json:
            print(json.dumps({"error": "监控列表为空", "results": []}, ensure_ascii=False))
        else:
            print("⚠ 监控列表为空")
        sys.exit(0)
    
    if not args.json:
        print(f"负面消息监控 — 最近{args.hours}小时 ({len(monitor)}只股票)")
        print()
    
    all_news = []
    for code, name, industry in monitor:
        if args.full and _HAS_NEWS_SCANNER:
            # ★ 多数据源扫描（东财/涨停池/雪球/同花顺/新浪/CCTV/Tavily）
            # v5.4.1(AUD-A-07·B-17教训): 解析统一委托 search_negative——旧内联
            # 循环无条目级过滤、无 hours 窗口(CCTV 全市场"混合"宏观行会被打成
            # 负面警报), 与本文件 search_negative 已修口径长期并存必漂移。
            neg_result = search_negative(code, name, hours=args.hours)
            src = neg_result.get("source") or "多源"
            for item in neg_result.get("results", []):
                all_news.append({
                    "symbol": code, "name": name, "industry": industry,
                    "title": str(item.get("title", ""))[:80],
                    "summary": item.get("summary", ""),
                    "date": item.get("date", ""),
                    "source": src, "level": item.get("level", "L2"),
                    "neg_hits": item.get("neg_hits", []),
                })
            if not args.json:
                _cnt = len(neg_result.get("results", []))
                status = f"✅ {src}" if _cnt == 0 else f"⚠ {src} ({_cnt}条负面)"
                print(f"  {code} {name:<6} {status}")
        else:
            # ★ 同花顺 API 搜索负面消息（默认）
            result = search_iwencai(code, name, args.hours)
            src = result["source"]
            err = result.get("error", "")
            for item in result.get("results", []):
                neg_hits = item.get("neg_hits", [])
                level = "L3" if any(kw in ["立案调查", "立案侦查", "造假", "退市", "暴雷"] for kw in neg_hits) else \
                        "L2" if any(kw in ["减持", "处罚", "诉讼", "制裁", "立案", "冻结"] for kw in neg_hits) else "L1"
                all_news.append({
                    "symbol": code, "name": name, "industry": industry,
                    "title": item["title"], "summary": item.get("summary", ""),
                    "date": item.get("date", ""), "source": src,
                    "level": level, "neg_hits": neg_hits,
                })
            if not args.json:
                status = f"✅ {src}" if result.get("results") else f"⚠ {src}({err or '无结果'})"
                print(f"  {code} {name:<6} {status}")
    
    if args.json:
        # JSON 模式：输出结构化结果
        output = {
            "total_negative": len(all_news),
            "l3_count": len([n for n in all_news if n['level'] == 'L3']),
            "l2_count": len([n for n in all_news if n['level'] == 'L2']),
            "l1_count": len([n for n in all_news if n['level'] == 'L1']),
            "results": all_news,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if len([n for n in all_news if n['level'] == 'L3']) > 0:
            sys.exit(2)
        if len([n for n in all_news if n['level'] == 'L2']) > 0:
            sys.exit(1)
        return
    
    print()
    report = format_report(all_news, args.hours)
    print(report if report else "✅ 最近24小时无负面消息")
    
    # L3/L2 级输出额外警报
    l3_count = len([n for n in all_news if n.get('level') == 'L3'])
    l2_count = len([n for n in all_news if n.get('level') == 'L2'])
    if l3_count > 0:
        print(f"\n🚨🚨🚨 发现 {l3_count} 条 L3 致命级信号，立即关注!")
        sys.exit(2)
    if l2_count > 0:
        print(f"\n🔴 发现 {l2_count} 条 L2 重大级信号，建议处理")
        sys.exit(1)


if __name__ == "__main__":
    main()
