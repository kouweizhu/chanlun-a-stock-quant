"""
data_source_helper.py — 数据源优先级调度与故障转移中枢

本模块定义了缠论系统所有数据类型的完整数据源优先级链，
并提供了 Agent MCP 工具 ↔ Python 脚本之间的桥接机制。

⚠️ 口径澄清（v5.4, 终审P3死链项, 2026-08-24）：
  标注 [Agent层] 的数据源（investoday MCP / Tavily / DuckDuckGo / Metaso /
  ddg_search 等）**不在 Python 侧的自动故障转移链内**——它们需要 Agent
  会话上下文才能调用，Python 脚本无法在源失败时"自动切换"过去。
  Python 侧的真实降级链仅包含标注 (Python库) 的条目；
  "自动后备/自动切换"字样仅指 Agent 会话内的手动/提示性接续。
  Metaso 为 .env 可选通道（METASO_API_KEY），同样属 Agent 层。

============================================================
    数据源优先级链（全系统统一）
============================================================

┌─ 1. K线行情数据 (K-line)
│   Baostock (Python库, 主源, 前复权adjustflag='2')
│   → efinance (Python库, 备选1)
│   → AkShare Sina (Python库, 备选2, 绕过东方财富限流)
│   → AkShare EM (Python库, 备选3)
│   → [Agent层] investoday MCP (list_stock_adjusted_quotes)  ← 新增
│     (通过 MCP 工具获取，需要 Agent 上下文)
│
├─ 2. 实时股价 (Real-time Price)
│   Baostock (Python库, 主源, query_history_k_data_plus 最近5日)
│   → [Agent层] investoday MCP (get_stock_quote_realtime)  ← 新增
│   → [Agent层] Tavily Search (行情搜索, 有月度配额)
│   → [Agent层] DuckDuckGo Search (ddg_search)  ← 新增
│     (免费，无配额限制，Tavily 配额用尽时的自动后备)
│
├─ 3. 新闻/消息搜索 (News Search)
│   [Agent层] Tavily Search (主源, 有月度配额)
│   → [Agent层] DuckDuckGo Search (ddg_search)  ← 新增
│     (免费无限制，Tavily配额用尽自动切换)
│   → [Agent层] investoday MCP (list_entity_related_news)  ← 新增
│     (有免费额度，可搜索个股相关新闻)
│
│   公司公告 (Company Announcements) — 消息面第一道防线
│   AKShare stock_notice_report (全市场公告, 按日期缓存, 免费无限额)
│     → akshare_scanner.py: 标题级关键词匹配 (减持/质押/诉讼/回购等)
│     → scan_news() 合并: 公告delta + 新闻搜索score → 最终消息面分
│
└─ 4. 财务数据 (Financials)
    AKShare 同花顺 (stock_financial_abstract_ths, 25项指标, 主源)  ← v1.2升级
    → Baostock (个股基本信息/PE/PB, fallback)
    → AKShare 研报评级 (stock_research_report_em, ±5分微调)  ← v1.3新增
    → [Agent层] investoday MCP (财务指标、利润表、资产负债表、现金流)

============================================================
    Agent ↔ Python 文件桥接机制
============================================================

Python 脚本（data_manager.py 等）在 terminal 中独立运行，
无法直接调用 Agent 的 MCP 工具。

当所有 Python 库数据源均失败时，Python 脚本会在特定路径
写入一个 ".source_failed" 标记文件。Agent（cron 任务）检测
到该文件后，使用 MCP 工具获取数据，写入临时文件供 Python 脚本读取。

文件桥接协议（约定即可，避免竞态）：
  .source_failed_{symbol}_{level}.flag  — 标记Python源全部失败
  .source_fallback_{symbol}_{level}.parquet  — Agent放下fallback数据
"""

import os
from date_utils import date_to_str, parse_date_to_datetime
import json
from enum import Enum
from typing import Optional, List, Dict


# ============================================================
# 数据源优先级枚举
# ============================================================

class DataSourceType(Enum):
    """数据源类型"""
    KLINE = "kline"          # K线行情
    PRICE = "price"          # 实时股价
    NEWS = "news"            # 新闻搜索
    FINANCIAL = "financial"  # 财务数据


# 工作目录（项目根目录，.source_failed_*.flag 和 .source_fallback_*.parquet 写在这里）
WORK_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# 数据源优先级链定义（供 Agent 使用的说明）
# ============================================================

SOURCE_CHAINS = {
    DataSourceType.KLINE: {
        "name": "K线行情数据",
        "priority": [
            {
                "name": "Baostock (Python主源)",
                "type": "python",
                "detail": "adjustflag='2'(前复权), 日线+30min均支持",
                "note": "首选，稳定可靠，已内置在 data_manager.py"
            },
            {
                "name": "efinance (Python备选1)",
                "type": "python",
                "detail": "东方财富源，klt=101日线/30半小时",
                "note": "已内置在 data_manager.py"
            },
            {
                "name": "AkShare Sina (Python备选2)",
                "type": "python",
                "detail": "新浪源，绕过东方财富网络限流",
                "note": "已内置在 data_manager.py"
            },
            {
                "name": "AkShare EM (Python备选3)",
                "type": "python",
                "detail": "东方财富源 stock_zh_a_hist",
                "note": "已内置在 data_manager.py"
            },
            {
                "name": "investoday MCP (Agent兜底) ★ 新增",
                "type": "agent_mcp",
                "detail": "mcp_investoday_list_stock_adjusted_quotes",
                "note": "有免费额度，需要 Agent 手动调用后转存文件供 Python 读取"
            },
        ]
    },

    DataSourceType.PRICE: {
        "name": "实时股价",
        "priority": [
            {
                "name": "Baostock (Python主源)",
                "type": "python",
                "detail": "query_history_k_data_plus最近5日取最新收盘",
                "note": "已在 check_price_levels.py 中实现"
            },
            {
                "name": "investoday MCP (Agent备选1) ★ 新增",
                "type": "agent_mcp",
                "detail": "mcp_investoday_get_stock_quote_realtime",
                "note": "返回实时行情，含 currentPrice/changeRatio"
            },
            {
                "name": "Tavily Search (Agent备选2)",
                "type": "agent_mcp",
                "detail": "mcp_tavily_search '股票名 股价 行情' time_range='day'",
                "note": "有月度配额，建议优先 investoday"
            },
            {
                "name": "DuckDuckGo Search (Agent备选3) ★ 新增",
                "type": "agent_mcp",
                "detail": "mcp_ddg_search_search '股票名 最新股价'",
                "note": "完全免费，无配额限制，Tavily 吃紧时的尾选"
            },
        ]
    },

    DataSourceType.NEWS: {
        "name": "新闻/消息搜索",
        "priority": [
            {
                "name": "Tavily Search (Agent主源)",
                "type": "agent_mcp",
                "detail": "mcp_tavily_search time_range='day'/'week'",
                "note": "结果质量好，但有月度配额限制"
            },
            {
                "name": "DuckDuckGo Search (Agent备选1) ★ 新增",
                "type": "agent_mcp",
                "detail": "mcp_ddg_search_search 关键词搜索",
                "note": "免费无配额限制，Tavily 配额用尽时自动切换"
            },
            {
                "name": "investoday MCP 新闻 (Agent备选2) ★ 新增",
                "type": "agent_mcp",
                "detail": "mcp_investoday_list_entity_related_news 或 list_news",
                "note": "有免费额度，支持按股票代码/行业查新闻，情绪分类"
            },
        ]
    },

    DataSourceType.FINANCIAL: {
        "name": "财务数据",
        "priority": [
            {
                "name": "investoday MCP (主源) ★ 新增",
                "type": "agent_mcp",
                "detail": "get_stock_finance_* / list_stock_income_statements / list_stock_balance_sheet",
                "note": "提供完整财务指标、估值、盈利能力等数据"
            },
            {
                "name": "Baostock (备选)",
                "type": "python",
                "detail": "query_stock_basic 获取基本信息",
                "note": "只有基础信息，无详细财务指标"
            },
        ]
    },
}


# ============================================================
# Agent 层桥接文件路径
# ============================================================

def _flag_path(symbol: str, level: str) -> str:
    """所有 Python 库源都失败后的标记文件"""
    return os.path.join(WORK_DIR, f".source_failed_{symbol}_{level}.flag")


def _fallback_path(symbol: str, level: str) -> str:
    """Agent 放下 fallback 数据的文件路径"""
    return os.path.join(WORK_DIR, f".source_fallback_{symbol}_{level}.parquet")


def save_fallback_data(symbol: str, level: str, data: List[Dict]) -> str:
    """Agent 将 MCP 工具获取的数据保存为 Python 可读取的文件

    Args:
        symbol: 股票代码
        level: 'daily' 或 '30min'
        data: K线数据列表，每项含 date/open/high/low/close/volume 等字段

    Returns:
        保存的文件路径
    """
    import pandas as pd
    path = _fallback_path(symbol, level)
    df = pd.DataFrame(data)
    df.to_parquet(path)
    return path


def check_agent_fallback(symbol: str, level: str) -> Optional[str]:
    """检查 Agent 是否已准备好 fallback 数据

    Returns:
        如果 Agent fallback 文件存在，返回路径；否则返回 None
    """
    path = _fallback_path(symbol, level)
    return path if os.path.exists(path) else None


def mark_python_sources_failed(symbol: str, level: str):
    """标记所有 Python 库数据源均失败

    写入标记文件，Agent 的 cron 任务检测到后会尝试用 MCP 工具获取数据。
    """
    path = _flag_path(symbol, level)
    with open(path, 'w') as f:
        json.dump({
            "symbol": symbol,
            "level": level,
            "timestamp": str(__import__('datetime').datetime.now()),
            "message": "所有 Python 库数据源均失败，请使用 investoday MCP 工具获取数据"
        }, f)


def clear_agent_fallback(symbol: str, level: str):
    """清理 Agent fallback 标记文件和数据"""
    for path in [_flag_path(symbol, level), _fallback_path(symbol, level)]:
        if os.path.exists(path):
            os.remove(path)


# ============================================================
# 打印数据源链（供脚本显式输出，Agent 可读取）
# ============================================================

def print_source_chain(data_type: DataSourceType):
    """打印指定类型的数据源优先级链"""
    chain = SOURCE_CHAINS.get(data_type)
    if not chain:
        return

    print(f"\n{'=' * 55}")
    print(f"  {chain['name']} — 数据源优先级链")
    print(f"{'=' * 55}")
    for i, src in enumerate(chain['priority']):
        marker = "●" if src['type'] == 'python' else "○"
        new_marker = " ★NEW" if "新增" in src.get('note', '') else ""
        print(f"  {i+1}. {marker} {src['name']}{new_marker}")
        print(f"     {src['detail']}")
    print(f"{'=' * 55}\n")


# ============================================================
# Agent 层使用 investoday MCP 获取 K 线数据的模板说明
# ============================================================

INVESTODAY_KLINE_TEMPLATE = """
# Agent 层使用 investoday MCP 获取 K 线数据的流程

当 data_manager.py 的所有 Python 数据源均失败时，Agent 可以：
1. 检测到 .source_failed_{symbol}_{level}.flag 标记文件
2. 使用 mcp_investoday_list_stock_adjusted_quotes 获取复权行情
3. 调用 data_source_helper.save_fallback_data() 保存为 parquet
4. Python 脚本自动读取 fallback 数据继续分析

mcp_investoday_list_stock_adjusted_quotes 返回字段：
  tradeDate   — 交易日期 (格式: "YYYY-MM-DD HH:MM:SS")
  openPrice   — 开盘价
  highPrice   — 最高价
  lowPrice    — 最低价
  closePrice  — 收盘价
  volume      — 成交量（股）
  amount      — 成交金额（元）
  vwap        — 加权均价
  changePct   — 涨跌幅
"""


if __name__ == "__main__":
    import sys
    from datetime import datetime

    print(f"\n{'#' * 55}")
    print(f"#  data_source_helper — 数据源优先级调度中枢")
    print(f"#  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'#' * 55}")

    # 根据参数打印特定数据源链
    data_type_map = {
        'kline': DataSourceType.KLINE,
        'price': DataSourceType.PRICE,
        'news': DataSourceType.NEWS,
        'financial': DataSourceType.FINANCIAL,
    }

    if len(sys.argv) > 1:
        dt = data_type_map.get(sys.argv[1])
        if dt:
            print_source_chain(dt)
        else:
            print(f"未知类型: {sys.argv[1]}")
            print(f"可用: kline, price, news, financial")
    else:
        for dt in DataSourceType:
            print_source_chain(dt)
