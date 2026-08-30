"""akshare_scanner.py — AKShare 公司公告扫描模块

利用 stock_notice_report 获取全市场公告，按股票代码过滤，
检测风险/利好关键词，输出公告维度的消息面评分调整。

设计原则:
  - 调用一次缓存全天公告，避免每只股票重复请求
  - 标题级关键词匹配（每条公告只计一次，同 scan_news 的文章级去重）
  - 返回 (score_adjustment, details) 供 scan_news() 合并
"""

import os
from date_utils import date_to_str, parse_date_to_datetime
import json
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd


# 公告关键词（比新闻更精准，因为是公司官方披露）
NEGATIVE_KEYWORDS = [
    # 核心风险
    "减持", "质押", "诉讼", "仲裁", "处罚", "罚款", "违规",
    "退市", "风险提示", "ST", "*ST", "戴帽",
    # 财务风险
    "亏损", "减值", "计提", "坏账", "债务违约",
    # 监管
    "立案", "调查", "监管", "警示函", "通报批评", "整改",
    "冻结", "查封", "限制",
    # 经营
    "停产", "停工", "安全事故", "召回",
    # 股权
    "司法拍卖", "被动减持",
]

POSITIVE_KEYWORDS = [
    "回购", "增持", "分红", "派息", "送转",
    "预增", "扭亏", "业绩增长", "超预期",
    "中标", "签约", "战略合作", "合作协议",
    "获批", "专利", "突破", "创新",
    "增持计划", "回购计划",
]

# 全量公告缓存: {date_str: DataFrame}
_announcement_cache = {}


def scan_announcements(code: str, name: str, lookback_days: int = 3) -> tuple:
    """扫描指定股票的近N天公告

    Args:
        code: 股票代码 (6位)
        name: 股票名称
        lookback_days: 回溯天数 (默认3天，覆盖周末)

    Returns:
        (score_delta: int, details: str)
        score_delta: 评分调整值 (正=利好, 负=利空, 0=无影响)
        details: 详情字符串
    """
    all_neg_titles = []
    all_pos_titles = []
    matched_announcements = []

    today = datetime.now()

    for day_offset in range(lookback_days):
        target_date = today - timedelta(days=day_offset)
        date_str = target_date.strftime("%Y%m%d")

        # 跳过周末
        if target_date.weekday() >= 5:
            continue

        try:
            df = _get_announcements_cached(date_str)
            if df is None or df.empty:
                continue

            # 按代码过滤
            stock_anns = df[df["代码"].astype(str) == str(code)]
            if stock_anns.empty:
                continue

            for _, row in stock_anns.iterrows():
                title = str(row.get("公告标题", ""))
                ann_type = str(row.get("公告类型", ""))
                ann_date = str(row.get("公告日期", ""))

                if not title:
                    continue

                title_lower = title.lower()

                # 检查关键词
                neg_hits = [kw for kw in NEGATIVE_KEYWORDS if kw.lower() in title_lower]
                pos_hits = [kw for kw in POSITIVE_KEYWORDS if kw.lower() in title_lower]

                if neg_hits:
                    all_neg_titles.append(f"[{ann_date}] {title[:60]} ({','.join(neg_hits)})")
                if pos_hits:
                    all_pos_titles.append(f"[{ann_date}] {title[:60]} ({','.join(pos_hits)})")

                if neg_hits or pos_hits:
                    matched_announcements.append({
                        "date": ann_date,
                        "title": title[:80],
                        "type": ann_type,
                        "neg": neg_hits,
                        "pos": pos_hits,
                    })
        except Exception as e:
            continue

    # 计算评分调整
    neg_count = len(all_neg_titles)
    pos_count = len(all_pos_titles)

    if neg_count == 0 and pos_count == 0:
        return 0, "[公告] 无重大公告"

    # 公告是官方披露，权重比新闻搜索更高
    # 严重利空公告 → 大幅扣分
    # 利好公告 → 适度加分
    score_delta = 0
    details_parts = []

    if neg_count > 0:
        # 减持/质押/诉讼等扣分更重
        severe_neg = any(
            kw in " ".join(all_neg_titles)
            for kw in ["减持", "质押", "诉讼", "处罚", "退市", "立案", "ST"]
        )
        if severe_neg:
            score_delta -= min(15, neg_count * 5)
        else:
            score_delta -= min(10, neg_count * 3)
        details_parts.append(f"利空{neg_count}条")

    if pos_count > 0:
        score_delta += min(10, pos_count * 3)
        details_parts.append(f"利好{pos_count}条")

    # 构建详情
    detail = f"[公告] {'/'.join(details_parts)}"
    if all_neg_titles:
        detail += f" 最严重: {all_neg_titles[0][:50]}"
    elif all_pos_titles:
        detail += f" 最重要: {all_pos_titles[0][:50]}"

    return score_delta, detail


def _get_announcements_cached(date_str: str) -> pd.DataFrame:
    """获取指定日期的全市场公告（带缓存）

    同一日期只请求一次，后续从内存缓存读取。
    """
    if date_str in _announcement_cache:
        return _announcement_cache[date_str]

    try:
        df = ak.stock_notice_report(symbol="全部", date=date_str)
        if df is not None and not df.empty:
            _announcement_cache[date_str] = df
            return df
        else:
            _announcement_cache[date_str] = pd.DataFrame()
            return pd.DataFrame()
    except Exception as e:
        print(f"[akshare_scanner] 公告获取失败({date_str}): {e}")
        _announcement_cache[date_str] = pd.DataFrame()
        return pd.DataFrame()


def clear_cache():
    """清理缓存（用于测试或内存释放）"""
    global _announcement_cache
    _announcement_cache = {}


if __name__ == "__main__":
    import sys

    symbols = sys.argv[1:] if len(sys.argv) > 1 else [("002415", "海康威视")]
    for sym in symbols:
        if isinstance(sym, tuple):
            code, name = sym
        else:
            code, name = sym, ""
        delta, detail = scan_announcements(code, name)
        print(f"{code} {name}: delta={delta:+d} {detail}")
