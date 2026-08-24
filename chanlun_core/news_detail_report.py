#!/usr/bin/env python
"""
个股消息面分析报告

两种模式：
  batch（默认）: 读取 .phase2_results.json 扫描 Top 30
  single:       --code 600872 --name 中炬高新 扫描单只股票

输出：
  batch → Markdown 文件（固定路径）
  single → stdout JSON（可指定 --output 写文件）
"""
import sys, os, json, time, urllib.request, urllib.parse, argparse
from date_utils import date_to_str, parse_date_to_datetime
from datetime import datetime
from dotenv import load_dotenv

# ── 加载 .env ──
_hermes_home = os.environ.get("HERMES_HOME", "")
if _hermes_home:
    _parent = os.path.dirname(os.path.dirname(_hermes_home.rstrip("/")))
    load_dotenv(os.path.join(_parent, ".env"))
else:
    load_dotenv(os.path.expanduser("~/.hermes/.env"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 统一消息面扫描（与 A500 pool_scanner 共享数据源）
from news_scanner import scan_news as _ns_scan_news

# 评分关键词（与 pool_screener.py scan_news 一致）
NEGATIVE_KEYWORDS = [
    # 财务/经营类利空
    "亏损", "暴跌", "违约", "诉讼", "处罚", "退市", "暴雷",
    "减值", "减持", "爆仓", "造假", "停产", "重组失败",
    "预警", "跌停", "st ", "*st", "戴帽", "退市风险", "净亏损",
    # 监管/制裁/地缘政治类利空（2026-05-01 补充）
    "制裁", "SDN", "列入", "黑名单", "调查", "立案",
    "冻结", "查封", "限制", "打压", "出口管制", "处罚决定",
    "通报批评", "监管措施", "立案调查", "立案侦查",
]
POSITIVE_KEYWORDS = [
    "增长", "超预期", "回购", "增持", "中标", "突破",
    "利好", "分红", "盈利", "扩产", "净利润增长", "大涨",
    "扭亏", "预增", "高增",
]

def search_iwencai(code: str, name: str, channel: str = "news") -> dict:
    """同花顺问财 API 搜索（主源）"""
    api_key = os.environ.get("IWENCAI_API_KEY", "")
    if not api_key:
        return {"source": "skip", "articles": [], "error": "无IWENCAI_API_KEY"}
    try:
        import secrets
        trace_id = secrets.token_hex(32)
        query = f"{name} {code} 财经新闻"
        payload = json.dumps({
            "channels": [channel],
            "app_id": "AIME_SKILL",
            "query": query,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://openapi.iwencai.com/v1/comprehensive/search",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "X-Claw-Call-Type": "normal",
                "X-Claw-Skill-Id": f"{channel}-search",
                "X-Claw-Skill-Version": "1.0.0",
                "X-Claw-Plugin-Id": "none",
                "X-Claw-Plugin-Version": "none",
                "X-Claw-Trace-Id": trace_id,
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data.get("status_code") != 0:
            return {"source": "同花顺", "articles": [], "error": data.get("status_msg", "?")}

        hits = data.get("data", [])
        articles = []
        for item in hits[:8]:
            articles.append({
                "title": item.get("title", ""),
                "content": item.get("summary", "")[:400],
                "url": item.get("source_url", item.get("url", "")),
            })
        return {"source": f"同花顺{channel}", "articles": articles, "error": ""}
    except Exception as e:
        return {"source": "同花顺", "articles": [], "error": str(e)[:60]}

def search_tavily(code: str, name: str) -> dict:
    """调用 Tavily 搜索，返回完整结果"""
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return {"source": "skip", "articles": [], "error": "无TAVILY_API_KEY"}
    try:
        query = f"{name} {code} 利空 公告 风险"
        payload = json.dumps({
            "query": query, "search_depth": "basic",
            "max_results": 5, "time_range": "week",
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.tavily.com/search", data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        articles = []
        for r in data.get("results", []):
            articles.append({
                "title": r.get("title", ""),
                "content": r.get("content", "")[:400],
                "url": r.get("url", ""),
            })
        return {"source": "Tavily", "articles": articles, "error": ""}
    except Exception as e:
        return {"source": "Tavily", "articles": [], "error": str(e)[:60]}

def search_metaso(code: str, name: str) -> dict:
    """Metaso HTTP API 备源"""
    api_key = os.environ.get("METASO_API_KEY", "")
    if not api_key:
        return {"source": "skip", "articles": [], "error": "无METASO_API_KEY"}
    try:
        query = f"{name} {code} 利空 公告 风险"
        payload = json.dumps({
            "q": query, "scope": "webpage", "size": 5,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://metaso.cn/api/v1/search", data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        articles = []
        for r in data.get("webpages", []):
            articles.append({
                "title": r.get("title", ""),
                "content": r.get("snippet", "")[:400],
                "url": r.get("url", ""),
            })
        return {"source": "Metaso", "articles": articles, "error": ""}
    except Exception as e:
        return {"source": "Metaso", "articles": [], "error": str(e)[:60]}

def analyze_articles(articles, name):
    """对文章逐条标注关键词命中"""
    results = []
    for art in articles:
        text = (art["title"] + " " + art["content"]).lower()
        name_lower = name.lower()
        neg_hits = []
        pos_hits = []
        for kw in NEGATIVE_KEYWORDS:
            if kw in text:
                neg_hits.append(kw)
        for kw in POSITIVE_KEYWORDS:
            if kw in text:
                pos_hits.append(kw)
        results.append({
            "title": art["title"],
            "content": art["content"][:300],
            "url": art["url"],
            "relevant": name_lower in text,
            "neg_hits": neg_hits,
            "pos_hits": pos_hits,
        })
    return results

# v5.4(B-12): 原 compute_score_detail(文章级去重评分) 已删除——旧采集管线
# 换成 news_scanner 共享引擎后该函数零调用，属死代码；其"分段公式"逻辑
# 由 news_scanner 内部评分承接。需要查阅时看 git 历史（v5.3.x 及之前）。

def analyze_single_stock(code: str, name: str, output_path: str = "") -> dict:
    """单只股票分析：使用 news_scanner 多源采集 + 评分，返回 JSON"""
    # 使用统一消息面扫描（与 A500 pool_scanner 共享数据源）
    score, detail = _ns_scan_news(code, name)

    # 解析 detail 字符串获取各源信息
    source = detail.split("]")[0] + "]" if "]" in detail else detail

    output = {
        "code": code,
        "name": name,
        "source": source,
        "score": score,
        "reason": detail,
        "detail": detail,
        "error": "",
    }

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"✅ 已保存: {output_path}")

    return output


def run_batch_mode():
    """批量模式（Top 30）

    v5.4(B-12) 重写：旧实现引用已删除采集管线的 articles/analyzed/rel_arts/
    neg_count/pos_count/reason 等未定义变量，一进循环即 NameError 必崩。
    现基于 _ns_scan_news 的真实返回（score/detail）渲染；消息明细直接取
    detail 的行，不再伪造计数。输出文件名动态日期化（旧硬编码 2026-05-01）。"""
    with open(".phase2_results.json", encoding="utf-8") as f:
        all_stocks = json.load(f)

    top30 = all_stocks[:30]
    lines = []
    lines.append("# A500 Top 30 个股消息面详情报告")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("**扫描引擎**: news_scanner 共享引擎（多源采集 + 关键词评分 + Agnes LLM 语义混合）")
    lines.append("**评分规则**: 关键词分×0.4 + LLM分×0.6；数据源失败显式标注不静默")
    lines.append("")
    lines.append("---")
    lines.append("")

    for i, s in enumerate(top30):
        code = s["code"]
        name = s.get("name") or code
        tech = s.get("tech_score", "—")
        fund = s.get("fund_score", "—")
        composite = s.get("composite", "—")

        print(f"  [{i+1:2d}/{len(top30)}] {code} {name}...", flush=True)

        # 统一消息面扫描（与 A500 pool_scanner 共享数据源）
        try:
            score, detail = _ns_scan_news(code, name)
        except Exception as e:
            score, detail = None, f"⚠️扫描异常: {str(e)[:80]}"

        dlines = [ln for ln in str(detail or "").splitlines()]
        source = dlines[0] if dlines else ""
        msg_lines = dlines[1:]

        print(f"  → score={score}", flush=True)

        lines.append(f"## {i+1}. {name}（{code}）")
        lines.append("")
        lines.append("| 维度 | 数值 |")
        lines.append("|------|:----:|")
        lines.append(f"| 技术分 | {tech} |")
        lines.append(f"| 基本面分 | {fund} |")
        score_disp = "—" if score is None else int(score)
        lines.append(f"| 消息分 | **{score_disp}** |")
        lines.append(f"| 综合分 | **{composite}** |")
        lines.append(f"| 数据源摘要 | {source} |")
        lines.append("")

        if not msg_lines:
            lines.append("> 无消息明细返回")
        else:
            lines.append("### 消息明细")
            lines.append("")
            for ml in msg_lines[:10]:
                lines.append(f"- {ml}")
            if len(msg_lines) > 10:
                lines.append(f"- ...共 {len(msg_lines)} 条，详见个股报告")
        lines.append("")
        lines.append("---")
        lines.append("")

        time.sleep(1)  # 东财系限流纪律（akshare-quirks 第7章）

    # 附录：关键词表
    lines.append("## 附录")
    lines.append("")
    lines.append("### 利空关键词（news_scanner NEG_KEYWORDS 同源）")
    lines.append(f"`{'`, `'.join(NEGATIVE_KEYWORDS[:40])}`")
    lines.append("")
    lines.append("### 过滤规则")
    lines.append("- 仅统计与本公司相关的条目参与评分（公司名/代码匹配）")
    lines.append("- 全市场源（如 CCTV 宏观）不参与个股负面否决")

    content = "\n".join(lines)

    output_path = ("D:/常用文件/股票池推荐股/A500_Top30_消息面详情_"
                   + datetime.now().strftime("%Y-%m-%d") + ".md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"\n✅ 已生成: {output_path}")
    print(f"   文件大小: {len(content)} 字符")


def main():
    parser = argparse.ArgumentParser(description="个股消息面分析")
    parser.add_argument("--code", help="股票代码（单票模式）")
    parser.add_argument("--name", help="股票名称（单票模式，省略则用代码）")
    parser.add_argument("--output", help="输出文件路径（单票模式，省略则输出到 stdout）")
    args = parser.parse_args()

    if args.code:
        # 单票模式
        name = args.name or args.code
        result = analyze_single_stock(args.code, name, args.output or "")
        if not args.output:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        # 批量模式（原行为）
        run_batch_mode()


if __name__ == "__main__":
    main()
