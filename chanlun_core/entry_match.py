"""entry_match.py — 新闻明细条目级相关性匹配（两链统一口径工具）

v5.4.1(AUD-A-01, 2026-08-27): C-01 条目级纪律此前只在 composite_scorer(A500链)
落地, generate_report(个股链)的 veto#3 仍对 detail 全量行裸匹配——泛词"证监会"
命中 CCTV 正面头条即把健康个股整票回避(探针实锤)。为绝 B-17 式双实现漂移,
pipeline 否决层(composite_scorer.apply_veto)与个股报告否决层
(generate_report.compute_veto_check#3)统一共用本模块。

detail 行格式(news_scanner.scan_news 组装): "[来源][标签] 标题文本"
首行为汇总头 "[N源] ... | LLM:..."(无标签, 天然被过滤)。

否决/severe 关键词适用规则:
  1) 仅 [负面]/[混合] 标签行参与——[正面]/[中性]行情新闻永不触发硬事件词;
  2) 行内须含本股名称/代码/别称; 或来源属"天然个股级"白名单
     (采集请求本身即按本股发起: 同花顺新闻/同花顺公告/Tavily/新浪财经;
      东财新闻/CCTV财经/涨停池/雪球热搜为全市场面源, 必须行内见股名/代码);
  3) v5.4.1(AUD-B-03): 名称形态归一——"*ST 中炬"/"ST中炬"/大小写折叠等
     变体均参与匹配, 压缩"标题用简称而配置用全称"的召回盲区。
设计取向"宁缺勿滥": 少报一条比误杀一票安全(veto 后果=composite=0+仓位0)。
   4) v2026-08-28(川投能源误杀实锤): 治理制度类公告行排除——"重大信息内部报告
      制度"等公告的正文是制度条文引用(如"涉嫌严重违法违纪...被纪检监察机关采取"),
      并非本股实质负面事件; 该行参与 veto 匹配会把制度建设本身误判为违法违纪
      信号(composite=0+仓位0)。此类行按标题模式排除出 veto/severe 匹配,
      常规 news 评分不受影响。排除模板行=少报一条, 与宁缺勿滥取向一致。
"""

import re

# 天然个股级来源标签——该源的检索请求本身按本股 code/name 发起,
# 即使行文本不含股名也可参与匹配
STOCK_LEVEL_SOURCE_LABELS = frozenset({
    "同花顺新闻", "同花顺公告", "Tavily", "新浪财经",
})

_NEG_TAG_RE = re.compile(r"\[(?:负面|混合)\]")
_SRC_RE = re.compile(r"^\[([^\]]+)\]\[")

# 治理制度/规则类公告标题模式(v2026-08-28 规则4)。命中即视为条文引用而非事件。
# 采用列举式保守清单: 只列"制度/办法/规则/准则/章程"的高频组合, 不做单字泛匹配
# (如裸"制度"), 防止把"公司治理制度存在重大缺陷"这类真负面标题误排除。
_TEMPLATE_TITLE_RE = re.compile(
    r'(报告制度|管理制度|工作制度|议事规则|管理办法|实施细则|'
    r'行为准则|公司章程|信息披露管理制度|募集资金管理办法)'
)


def _name_aliases(name: str) -> set:
    """由本股简称构造匹配别名集（含 ST 前缀增减与大小写折叠变体）。"""
    n = (name or "").strip()
    if not n:
        return set()
    aliases = {n, n.casefold()}
    # 去 ST/*ST 前缀（正反两个方向: 配置名可能带或不含前缀）
    stripped = re.sub(r"^(\*ST|ST|S*ST)", "", n).strip()
    if stripped and stripped != n:
        aliases.add(stripped)
        aliases.add(stripped.casefold())
    # 标题常见"*ST 中炬"(带空格)形态
    if stripped:
        for pre in ("*ST ", "ST ", "*ST", "ST"):
            aliases.add(pre + stripped)
    return aliases


def relevant_detail_lines(news_detail: str, name: str = "", code: str = "",
                          exclude_template: bool = True) -> list:
    """返回可参与 veto/severe 关键词匹配的相关明细行列表。

    Args:
        news_detail: scan_news 返回的多行 detail 文本
        name: 本股简称(如"中炬高新"，允许带/不带 ST 前缀)
        code: 本股6位代码(如"600872")
        exclude_template: 排除治理制度/规则类公告行(规则4, 默认开启)——
            其正文为制度条文引用而非实质负面事件

    Returns:
        通过条目级相关性的行列表(可能为空)
    """
    aliases = _name_aliases(name)
    code = (code or "").strip()

    def _hit_stock(ln: str) -> bool:
        if code and code in ln:
            return True
        return any(a in ln for a in aliases)

    def _is_template_line(ln: str) -> bool:
        # 生产格式行 "[来源][标签] 日期 标题: 摘要" —— 取首个英文冒号+空格之前的
        # 标题段判模板词; 标题内部含英文冒号的边界情形漏判时保持旧行为(不更糟)。
        title_part = ln.split(': ', 1)[0]
        return bool(_TEMPLATE_TITLE_RE.search(title_part))

    hits = []
    for ln in (news_detail or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if exclude_template and _is_template_line(ln):
            continue
        m_src = _SRC_RE.match(ln)
        if not (_NEG_TAG_RE.search(ln) or m_src):
            # v5.4.1 兼容回退: 非结构化行(调用方传裸文本 detail, 无 [来源][标签]
            # 前缀)——退回 C-01 原始口径: 仅本股名称/代码命中的行参与。
            # 全市场头条通常不含本股名, 误杀面仍闭合; 与生产格式互不干扰。
            if _hit_stock(ln):
                hits.append(ln)
            continue
        if not _NEG_TAG_RE.search(ln):
            continue  # 生产格式的[正面]/[中性]行与汇总头一律排除
        if _hit_stock(ln):
            hits.append(ln)
            continue
        if m_src and m_src.group(1) in STOCK_LEVEL_SOURCE_LABELS:
            hits.append(ln)
    return hits
