"""
sentiment_analyzer.py — 轻量级宏观文本情感分析器

不依赖 ML 模型，基于词典 + 否定翻转 + 强度修饰，做极性判断。

用法:
    from sentiment_analyzer import SentimentAnalyzer
    sa = SentimentAnalyzer()
    score, signals = sa.analyze(report_text)
    # score: 整数 (正=利好, 负=利空)
    # signals: [{'keyword':'降息','weight':2,'context':'央行降息25bp',...}]

设计原则:
    1. 词典驱动 — 所有关键词和权重在 sentiment_lexicon.json
    2. 否定翻转 — "暂缓降息" 的降息从 +2 翻为 -2
    3. 强度修饰 — "大幅降息" 从 +2 加强到 +3
    4. 聚合评分 — 累加所有匹配信号，映射到四档风险等级
"""

import json
from date_utils import date_to_str, parse_date_to_datetime
import os
import re
from typing import List, Dict, Tuple

# ─── 默认路径 ─────────────────────────────────────
_LEXICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "sentiment_lexicon.json")

# ─── 内置降级（文件不存在时的最小词典）────────────
_FALLBACK_LEXICON = [
    {"keyword": "降息", "weight": 2, "category": "货币政策"},
    {"keyword": "加息", "weight": -2, "category": "货币政策"},
    {"keyword": "降准", "weight": 2, "category": "货币政策"},
]


class SentimentAnalyzer:
    """宏观文本情感分析器"""

    # 否定窗口（关键词前后各 N 字符内检测否定词）
    NEGATION_WINDOW = 8

    def __init__(self, lexicon_path: str = None):
        path = lexicon_path or _LEXICON_PATH
        self.negation_words = []
        self.intensifiers_boost = []
        self.intensifiers_dampen = []
        self.lexicon = []

        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.negation_words = data.get("negation_words", [])
            self.intensifiers_boost = data.get("intensifiers_boost", [])
            self.intensifiers_dampen = data.get("intensifiers_dampen", [])
            self.lexicon = data.get("lexicon", _FALLBACK_LEXICON)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"[SentimentAnalyzer] 词典加载失败({e})，使用内置降级词典")
            self.lexicon = _FALLBACK_LEXICON

    def _window_has_negation(self, text: str, pos: int, kw_len: int) -> bool:
        """检查关键词前后窗口内是否有否定词"""
        start = max(0, pos - self.NEGATION_WINDOW)
        end = min(len(text), pos + kw_len + self.NEGATION_WINDOW)
        window = text[start:end]
        for nw in self.negation_words:
            if nw in window:
                return True
        return False

    def _get_intensity_modifier(self, text: str, pos: int, kw_len: int) -> float:
        """检测强度修饰词，返回乘数 (boost=1.5, dampen=0.5, normal=1.0)"""
        start = max(0, pos - self.NEGATION_WINDOW)
        end = min(len(text), pos + kw_len + self.NEGATION_WINDOW)
        window = text[start:end]
        for iw in self.intensifiers_boost:
            if iw in window:
                return 1.5
        for dw in self.intensifiers_dampen:
            if dw in window:
                return 0.5
        return 1.0

    def analyze(self, text: str) -> Tuple[int, List[Dict]]:
        """
        分析文本情感

        Args:
            text: 宏观早报全文

        Returns:
            (total_score, signals_list)
            total_score: 累加情感分（正=利好，负=利空）
            signals_list: 每条匹配信号的详情
        """
        macro_score = 0
        signals = []

        # 按关键词长度降序排列（优先匹配长词，如"北向资金净流入"优先于"净流入"）
        sorted_lexicon = sorted(self.lexicon, key=lambda x: len(x["keyword"]), reverse=True)

        # 已匹配的位置集合（避免"净流入"和"北向资金净流入"重复匹配）
        matched_spans = set()

        for entry in sorted_lexicon:
            kw = entry["keyword"]
            weight = entry["weight"]
            category = entry.get("category", "")

            for m in re.finditer(re.escape(kw), text):
                pos = m.start()
                end = m.end()
                span = (pos, end)

                # 跳过已被长词覆盖的位置
                if any(s[0] <= pos < s[1] or s[0] < end <= s[1]
                       for s in matched_spans):
                    continue

                matched_spans.add(span)

                # 否定检测
                negated = self._window_has_negation(text, pos, len(kw))
                effective_weight = -weight if negated else weight

                # 强度修饰
                multiplier = self._get_intensity_modifier(text, pos, len(kw))
                if multiplier != 1.0:
                    effective_weight = int(effective_weight * multiplier)

                # 上下文片段
                ctx_start = max(0, pos - 10)
                ctx_end = min(len(text), end + 10)
                context = text[ctx_start:ctx_end].replace('\n', ' ')

                macro_score += effective_weight

                signals.append({
                    "keyword": kw,
                    "weight": effective_weight,
                    "original_weight": weight,
                    "negated": negated,
                    "intensity": multiplier,
                    "category": category,
                    "position": pos,
                    "context": context.strip(),
                })

        return macro_score, signals

    def risk_level(self, score: int) -> str:
        """分数 → 风险等级"""
        if score >= 3:
            return "favorable"
        elif score >= 0:
            return "neutral"
        elif score >= -2:
            return "caution"
        else:
            return "risk_off"


# ─── 单例（供 market_regime.py 直接 import）─────────
_analyzer = None


def get_analyzer() -> SentimentAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = SentimentAnalyzer()
    return _analyzer


def analyze_text(text: str) -> Tuple[int, List[Dict], str]:
    """便捷函数：分析文本，返回 (分数, 信号列表, 风险等级)"""
    sa = get_analyzer()
    score, signals = sa.analyze(text)
    level = sa.risk_level(score)
    return score, signals, level
