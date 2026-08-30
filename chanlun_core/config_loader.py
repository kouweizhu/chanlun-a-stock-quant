"""
config_loader.py — 统一配置读取模块

优先从 config.yaml 读取，文件不存在时回退到硬编码默认值。
所有默认值与 config.yaml 注释中的值保持一致。

用法:
    from config_loader import get_config
    cfg = get_config()
    tech_buy = cfg['scoring']['tech_buy_threshold']
    # 或便捷属性:
    from config_loader import W_TECH, W_FUND, W_ALPHA, W_NEWS
"""

import os
from date_utils import date_to_str, parse_date_to_datetime
import yaml

_CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_CONFIG_DIR, "config.yaml")

# 硬编码默认值（与 config.yaml 完全一致）
_DEFAULTS = {
    "weights": {
        # v5.3.4(审计A5/P1): 修正为标准五维口径（与 config.yaml 完全一致）。
        # 旧默认值是错误的四维(fund0.30/alpha0.25/缺fund_factor)，与下方:150注释
        # 矛盾——yaml 缺失时曾静默按错权重计算全批次 composite。
        "tech": 0.35, "fund": 0.25, "alpha": 0.20, "news": 0.10,
        "fund_factor": 0.10,
    },
    "scoring": {
        "min": -30,
        "max": 100,
        "tech_buy_threshold": 60,
        "fund_heavy_threshold": 60,
        "fund_light_threshold": 40,
        "resonance_penalty_threshold": 60,  # 共振惩罚阈值
        "alpha_buy_threshold": 40,        # Alpha因子最低分（低于此值仅轻仓）v5.3.2(D-4): 收缩映射后与旧30等价
        "sell_signal_suppress_days": 10,  # v5.3.3(E-1): 近N日一卖/二卖压制买入信号
        "veto_keywords": [             # 一票否决关键词
            "立案调查", "被立案", "证监会立案", "证监会调查",
            "财务造假", "虚增收入", "虚增利润", "虚假记载",
            "*ST", "退市风险警示",
            "非标审计", "保留意见", "无法表示意见",
            "涉嫌", "违法违规", "操纵市场",
        ],
        "severe_keywords": [          # 严重降级关键词
            "行政处罚", "公开谴责", "纪律处分",
            "减持计划", "减持公告", "大股东减持",
            "业绩预告变脸", "业绩修正", "由盈转亏",
            "监管措施", "监管关注", "问询函",
        ],
    },
    "grades": {"A": 70, "B": 60, "C": 50},
    "position": {"heavy": 0.50, "normal": 0.30, "light": 0.15, "none": 0.0},
    "a500": {
        "score_threshold": 3,
        # 终审C-10(2026-08-24): 默认4→3 与 config.yaml 对齐——yaml 缺失时回退值
        # 应与实际生效口径一致，避免"声明4实际3"的文档漂移
        "rev_score_threshold": 3,
        # 终审A5(2026-08-23): 40→60 与 config.yaml:98 对齐。原默认值是地雷：
        # yaml 文件缺失(非解析失败)时静默回退40，报告阈值悄悄放宽。
        "composite_threshold": 60,
        "top_n_report": 30,
        "batch_count": 5,
        "batch_pause": 20,
        "news_top_n": 30,
    },
    "backtest": {
        "initial_capital": 2000000.0,
        "commission": 0.0003,
        "max_position_pct": 0.30,
        "position_ladder": [0.09, 0.06, 0.03],
        "enable_slippage": True,
    },
    "banned": {
        "codes": [
        ],
        "sectors": [
        ],
        "auto_ban": {
            "st_stock": True,
            "negative_equity": False,
            "revenue_decline_3y": False,
        },
        "manual_blacklist": {
        }
    },
    "thresholds": {
        "segment_beichi_threshold": 0.8,
        "segment_second_buy_tolerance": 0.01,
        "backtest_tp_first": 0.30,
        "backtest_tp_second": 0.20,
        "backtest_tp_third": 0.15,
        "backtest_m30_filter": 3,
        "backtest_risk_free_rate": 0.025,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个字典"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_yaml():
    """从 config.yaml 加载，不存在则返回空"""
    if not os.path.exists(_CONFIG_PATH):
        return {}
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        # v5.3.4(审计A5/P1): 解析失败必须致命——静默回退错误默认权重会让全批次
        # composite 在零报错下算错（"看起来正常"事故同构）。文件不存在返回空才是合法路径。
        raise RuntimeError(
            f"[config_loader] config.yaml 存在但解析失败: {e}\n"
            f"拒绝静默回退到默认权重。请修复 {_CONFIG_PATH}，或暂时移走该文件后重试。"
        ) from e


# 模块加载时解析一次
_config = None


def get_config(refresh: bool = False) -> dict:
    """获取合并后的配置（默认值 + YAML 覆盖）
    
    Args:
        refresh: 强制重新读取 config.yaml
    
    Returns:
        dict: 完整配置
    """
    global _config
    if _config is None or refresh:
        yaml_config = _load_yaml()
        _config = _deep_merge(_DEFAULTS, yaml_config)
    return _config


# ============================================================
# 便捷模块级常量（保持向後兼容）
# ============================================================

def _cfg():
    return get_config()


# 权重（五维标准 v5.0：tech 0.35 + fund 0.25 + alpha 0.20 + news 0.10 + ff 0.10 = 1.00）
W_TECH = _cfg()["weights"]["tech"]
W_FUND = _cfg()["weights"]["fund"]
W_ALPHA = _cfg()["weights"]["alpha"]
W_NEWS = _cfg()["weights"]["news"]
W_FUND_FACTOR = _cfg()["weights"].get("fund_factor", 0.10)

# 评分阈值
TECH_BUY_THRESHOLD = _cfg()["scoring"]["tech_buy_threshold"]
FUND_HEAVY_THRESHOLD = _cfg()["scoring"]["fund_heavy_threshold"]
FUND_LIGHT_THRESHOLD = _cfg()["scoring"]["fund_light_threshold"]
ALPHA_BUY_THRESHOLD = _cfg()["scoring"]["alpha_buy_threshold"]
SELL_SIGNAL_SUPPRESS_DAYS = _cfg()["scoring"].get("sell_signal_suppress_days", 10)  # v5.3.3(E-1)
VETO_KEYWORDS = _cfg()["scoring"]["veto_keywords"]
SEVERE_KEYWORDS = _cfg()["scoring"]["severe_keywords"]
RESONANCE_PENALTY_THRESHOLD = _cfg()["scoring"]["resonance_penalty_threshold"]
SEVERE_PENALTY = _cfg()["scoring"].get("severe_penalty", 15)
SCORE_MIN = _cfg()["scoring"]["min"]
SCORE_MAX = _cfg()["scoring"]["max"]

# 等级门槛
COMPOSITE_A = _cfg()["grades"]["A"]
COMPOSITE_B = _cfg()["grades"]["B"]
COMPOSITE_C = _cfg()["grades"]["C"]

# 仓位
POSITION_HEAVY = _cfg()["position"]["heavy"]
POSITION_NORMAL = _cfg()["position"]["normal"]
POSITION_LIGHT = _cfg()["position"]["light"]
POSITION_NONE = _cfg()["position"]["none"]

# A500 系统
A500_SCORE_THRESHOLD = _cfg()["a500"]["score_threshold"]
A500_REV_SCORE_THRESHOLD = _cfg()["a500"]["rev_score_threshold"]
A500_COMPOSITE_THRESHOLD = _cfg()["a500"]["composite_threshold"]
A500_TOP_N_REPORT = _cfg()["a500"]["top_n_report"]
A500_BATCH_COUNT = _cfg()["a500"]["batch_count"]
A500_BATCH_PAUSE = _cfg()["a500"]["batch_pause"]
A500_NEWS_TOP_N = _cfg()["a500"]["news_top_n"]

# 回测
BACKTEST_INITIAL_CAPITAL = _cfg()["backtest"]["initial_capital"]
BACKTEST_COMMISSION = _cfg()["backtest"]["commission"]
BACKTEST_STAMP_DUTY = _cfg()["backtest"].get("stamp_duty", 0.0005)  # 卖出印花税 0.05%（v4.2）
BACKTEST_MAX_POSITION_PCT = _cfg()["backtest"]["max_position_pct"]
BACKTEST_POSITION_LADDER = _cfg()["backtest"]["position_ladder"]
BACKTEST_ENABLE_SLIPPAGE = _cfg()["backtest"]["enable_slippage"]

# 黑名单
BANNED_CODES = set(_cfg()["banned"].get("codes") or [])
BANNED_SECTORS = set(_cfg()["banned"].get("sectors") or [])
AUTO_BAN_ST = _cfg()["banned"].get("auto_ban", {}).get("st_stock", True)
AUTO_BAN_NEGATIVE_EQUITY = _cfg()["banned"].get("auto_ban", {}).get("negative_equity", False)
AUTO_BAN_REVENUE_DECLINE_3Y = _cfg()["banned"].get("auto_ban", {}).get("revenue_decline_3y", False)
MANUAL_BLACKLIST = _cfg()["banned"].get("manual_blacklist") or {}

# 阈值配置（原硬编码参数）
# v4.2 新增：笔级别背驰/二买容差 + 线段中枢魔数（原 generate_analysis/segment_analyzer 硬编码）
THRESHOLD_DIVERGENCE = _cfg()["thresholds"].get("divergence", 0.7)
THRESHOLD_SECOND_CLASS_TOLERANCE = _cfg()["thresholds"].get("second_class_tolerance", 0.01)
THRESHOLD_SEGMENT_BEICHI = _cfg()["thresholds"]["segment_beichi_threshold"]
THRESHOLD_SEGMENT_SECOND_BUY_TOLERANCE = _cfg()["thresholds"]["segment_second_buy_tolerance"]
THRESHOLD_SEGMENT_MAX_ZHONGSHU_BI = _cfg()["thresholds"].get("segment_max_zhongshu_bi", 27)
THRESHOLD_SEGMENT_MAX_ZHONGSHU_DAYS = _cfg()["thresholds"].get("segment_max_zhongshu_days", 120)
THRESHOLD_SEGMENT_MIN_FLUCTUATION_PCT = _cfg()["thresholds"].get("segment_min_fluctuation_pct", 0.05)
THRESHOLD_BACKTEST_TP_FIRST = _cfg()["thresholds"]["backtest_tp_first"]
THRESHOLD_BACKTEST_TP_SECOND = _cfg()["thresholds"]["backtest_tp_second"]
THRESHOLD_BACKTEST_TP_THIRD = _cfg()["thresholds"]["backtest_tp_third"]
THRESHOLD_BACKTEST_M30_FILTER = _cfg()["thresholds"]["backtest_m30_filter"]
THRESHOLD_BACKTEST_RISK_FREE_RATE = _cfg()["thresholds"]["backtest_risk_free_rate"]
