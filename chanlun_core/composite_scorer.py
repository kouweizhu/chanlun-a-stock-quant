#!/usr/bin/env python
"""
composite_scorer.py — 四维综合评分模块 + Veto 否决层

将 技术面 + 基本面 + Alpha因子 + 消息面 四个维度加权合并，
输出综合评分 [-30, 100] 和仓位建议。

Veto 否决层：
  某些特定利空消息会直接否决开仓，不经过权重计算。
  分为两级：
    - Veto（一票否决）：立案调查、*ST、财务造假 → grade=D, position=0
    - 严重降级：行政处罚、减持计划、业绩变脸 → composite 降 20 分

权重配置（v5.0 五维，从 config.yaml 读取）:
  tech=0.35  fund=0.25  alpha=0.20  news=0.10  fund_factor=0.10

用法：
  from composite_scorer import compute_3d_score, apply_veto

  # 先检查否决
  veto = apply_veto(news_detail=news_detail, risk_reasons=risk_reasons)
  if veto:
      result = veto
  else:
      result = compute_3d_score(tech_score=75, fund_score=62, alpha_score=80, news_score=50)
"""

from dataclasses import dataclass, field
from date_utils import date_to_str, parse_date_to_datetime
from typing import Optional, List

# 从统一配置读取（config.yaml 或硬编码默认值）
from config_loader import (
    W_TECH, W_FUND, W_ALPHA, W_NEWS, W_FUND_FACTOR,
    SCORE_MIN, SCORE_MAX,
    TECH_BUY_THRESHOLD, FUND_HEAVY_THRESHOLD, FUND_LIGHT_THRESHOLD,
    ALPHA_BUY_THRESHOLD,
    COMPOSITE_A, COMPOSITE_B, COMPOSITE_C,
    POSITION_HEAVY, POSITION_NORMAL, POSITION_LIGHT, POSITION_NONE,
    RESONANCE_PENALTY_THRESHOLD,
    SEVERE_PENALTY,
    VETO_KEYWORDS, SEVERE_KEYWORDS,
)


# ============================================================
# 数据类（Score3D 必须先定义，apply_veto 使用它）
# ============================================================

@dataclass
class Score3D:
    """四维综合评分结果（名称保持 3D 保证向后兼容）"""
    composite: float                    # 综合评分 [-30, 100]
    grade: str                          # 等级: A/B/C/D
    position: float                     # 建议仓位比例
    can_buy: bool                       # 是否可建仓
    components: dict = field(default_factory=dict)  # 各维度细节

    @property
    def decision(self) -> str:
        if self.components.get('veto_level') == 'veto':
            reasons = '; '.join(self.components.get('veto_reasons', []))
            return f'否决 — 触发风控: {reasons}'
        elif self.components.get('veto_level') == 'severe':
            reasons = '; '.join(self.components.get('severe_reasons', []))
            return f'严重降级 — {reasons}'
        if self.grade == 'A':
            return '推荐 — 技术面强+基本面好+因子排名优，可重仓'
        elif self.grade == 'B':
            return '关注 — 可买入，仓位适中'
        elif self.grade == 'C':
            return '观望 — 信号不够强，等更好机会'
        else:
            return '回避 — 不满足买入条件'


# ============================================================
# 公共工具
# ============================================================

def buy_level_from_type(buy_type: str) -> int:
    """从买点类型字符串提取缠论级别（1=一买 2=二买 3=三买 0=其他）。

    v5.3.1(F1): 自 pool_screener 提升为公共函数——所有 compute_3d_score
    调用点必须传入 buy_level, 否则缺省 0 会被当作"反转后买点"降一档,
    重算阶段(resocre/ff_rescore)曾因此把全体股票仓位系统性压低。
    """
    if not buy_type:
        return 0
    if "一买" in buy_type:
        return 1
    if "二买" in buy_type:
        return 2
    if "三买" in buy_type:
        return 3
    return 0


# ============================================================
# Veto 否决层
# ============================================================

def apply_veto(
    code: str = "",
    name: str = "",
    news_detail: str = "",
    risk_reasons: Optional[List[str]] = None,
    manual_blacklist: Optional[dict] = None,
) -> Optional[Score3D]:
    """检查是否触发否决，返回 Score3D（否决）或 None（放行）。

    Args:
        code: 股票代码
        name: 股票名称
        news_detail: scan_news 返回的详细文本
        risk_reasons: risk_filter.check_risk() 返回的 reasons
        manual_blacklist: config.yaml 的 manual_blacklist 字典

    Returns:
        Score3D（否决时）或 None（放行）
    """
    veto_reasons = []
    severe_reasons = []

    # ═══ 1. 人工黑名单 ═══
    if manual_blacklist and code in manual_blacklist:
        veto_reasons.append(f"人工黑名单: {manual_blacklist[code]}")

    # ═══ 2. risk_filter 检查结果 ═══
    if risk_reasons:
        for r in risk_reasons:
            if any(kw in r for kw in ["立案", "ST", "*ST", "退市", "财务造假",
                                       "非标审计", "资不抵债", "连亏"]):
                veto_reasons.append(r)
            else:
                severe_reasons.append(r)

    # ═══ 3. ST/*ST 名称检查 ═══
    if '*ST' in name or 'ST' in name.upper():
        veto_reasons.append(f"ST股({name})")

    # ═══ 4. 新闻详情关键词匹配 ═══
    # v5.0.1 修复：*ST/ST 不能放这里全文匹配！
    # 新闻里常出现其他股票的名字（如"300716 *ST泉为"），全文匹配会误杀本股票。
    # ST 状态应只通过自身名称判断（见第3步），此处跳过 *ST。
    # v5.4(C-01·用户拍板2026-08-24): 再升级为"条目级相关性匹配"——旧实现虽已
    # 排除 *ST 词, 但仍对整篇 detail 做子串搜索, 而 detail 混有全市场源(CCTV/
    # 涨停池/雪球)的头条, 任意头条含"立案调查/退市"即误杀本股(风控反噬通道,
    # 审计C-01)。新口径: 仅【本股名称或代码出现在同一消息行】的条目才参与
    # veto/severe 匹配, 与评分段 name 过滤(news_scanner)同口径;
    # 无任何相关条目 → 新闻侧不触发(宁缺勿滥)。
    if news_detail:
        # v5.4.1(AUD-A-01/AUD-B-03): 统一走 entry_match.relevant_detail_lines——
        # 与个股链 veto#3 同源(别称/ST变体/大小写折叠归一 + [负面]/[混合]限定 +
        # 个股级源白名单)。旧内联切行实现与本模块注释"与评分段同口径"的历史
        # 不一致就此消除, 防止 B-17 式双实现漂移。
        try:
            from entry_match import relevant_detail_lines as _rdl
        except ImportError:
            from chanlun_core.entry_match import relevant_detail_lines as _rdl
        _rel_lines = _rdl(news_detail, name=name, code=code)
        for kw in VETO_KEYWORDS:
            if kw.lower() in ("*st", "st"):  # ST 只能看名称，不能看新闻
                continue
            if any(kw.lower() in ln.lower() for ln in _rel_lines):
                veto_reasons.append(f"触发否决关键词: {kw}")
                break
        for kw in SEVERE_KEYWORDS:
            if any(kw.lower() in ln.lower() for ln in _rel_lines):
                severe_reasons.append(f"触发降级关键词: {kw}")
                break

    if not veto_reasons and not severe_reasons:
        return None

    # ── Veto 级：一票否决 ──
    if veto_reasons:
        return Score3D(
            composite=0.0,
            grade='D',
            position=POSITION_NONE,
            can_buy=False,
            components={
                'tech_score': 0,
                'fund_score': 0,
                'alpha_score': 0,
                'news_score': 0,
                'veto_level': 'veto',
                'veto_reasons': veto_reasons,
                'severe_reasons': severe_reasons,
            }
        )

    # ── 严重降级级：返回标记，由调用方在 compute_3d_score 后调整 ──
    return Score3D(
        composite=0.0,  # 占位，实际会重新算
        grade='D',
        position=POSITION_LIGHT,
        can_buy=False,
        components={
            'veto_level': 'severe',
            'severe_reasons': severe_reasons,
        }
    )


# ============================================================
# 四维评分
# ============================================================

def compute_3d_score(
    tech_score: float,
    fund_score: float,
    alpha_score: float = 50.0,
    news_score: float = 50.0,
    fund_factor_score: float = None,
    w_tech: float = W_TECH,
    w_fund: float = W_FUND,
    w_alpha: float = W_ALPHA,
    w_news: float = W_NEWS,
    w_fund_factor: float = W_FUND_FACTOR,
    resonance_penalty: bool = True,
    # Veto 参数（可选，传入后自动检查）
    code: str = "",
    name: str = "",
    news_detail: str = "",
    risk_reasons: Optional[List[str]] = None,
    manual_blacklist: Optional[dict] = None,
    # v5.0 P1：买点类型（1=一买 2=二买 3=三买 其他=反转后买点）
    # 影响仓位：一买可重仓（反转确认），三买降档（追高风险）
    buy_level: int = 0,
    # v5.3.3(E-1): 买卖信号冲突仲裁——近N日内存在一卖/二卖时, 买入信号
    # 被压制: can_buy=False(仓位0), 与"评级再高也不买"的tech闸门同级。
    # 缠论依据(29课): 一卖后的正确操作是卖出/观望, 同级别"类三买"形态
    # 大概率是二卖构造前的反弹中继。
    recent_top_sell: bool = False,
    # v5.3.3(E-2): 观察型几何信号("三买形成中/突破延续", 非真实缠论买点)
    # 可入池观察但不进推荐: 仓位强制不超过轻仓。
    observational: bool = False,
) -> Score3D:
    """计算五维综合评分，含 Veto 否决检查。

    Args:
        tech_score: 技术评分 [0, 100] 或 [-30, 100]
        fund_score: 基本面评分 [0, 100]
        alpha_score: Alpha 因子评分 [0, 100]，默认 50 中性
        news_score: 消息面评分 [0, 100]，默认 50 中性
        fund_factor_score: 资金面评分 [0, 100]，默认 None（四维模式不启用）
        w_tech, w_fund, w_alpha, w_news, w_fund_factor: 权重，自动归一化
        resonance_penalty: 是否启用共振惩罚
        code, name: 股票代码和名称（用于 veto 检查）
        news_detail: 消息详情文本（用于 veto 关键词匹配）
        risk_reasons: risk_filter 返回的理由列表
        manual_blacklist: 人工黑名单字典

    Returns:
        Score3D 结果
    """
    # ── Veto 检查（优先于评分）──
    veto = apply_veto(
        code=code, name=name,
        news_detail=news_detail,
        risk_reasons=risk_reasons,
        manual_blacklist=manual_blacklist,
    )
    if veto is not None:
        if veto.components.get('veto_level') == 'veto':
            return veto  # 一票否决
        # severe 降级：走评分但标记

    # ── 归一化 ──
    tech_norm = max(0, min(100, tech_score)) if tech_score >= 0 else max(SCORE_MIN, tech_score)
    fund_norm = max(0, min(100, fund_score))
    alpha_norm = max(0, min(100, alpha_score))
    news_norm = max(0, min(100, news_score))
    if fund_factor_score is None:
        ff_norm = None
    else:
        ff_norm = max(0, min(100, fund_factor_score))

    # 权重归一化（含资金面维度时按五维归一化）
    if ff_norm is None:
        total_w = w_tech + w_fund + w_alpha + w_news
        w_t, w_f, w_a, w_n = w_tech, w_fund, w_alpha, w_news
    else:
        total_w = w_tech + w_fund + w_alpha + w_news + w_fund_factor
        w_t, w_f, w_a, w_n = w_tech, w_fund, w_alpha, w_news
    if total_w <= 0:
        total_w = 1.0
    w_t = w_t / total_w
    w_f = w_f / total_w
    w_a = w_a / total_w
    w_n = w_n / total_w
    w_ff = w_fund_factor / total_w if ff_norm is not None else 0.0

    # 加权合成（四维或五维）
    composite = tech_norm * w_t + fund_norm * w_f + alpha_norm * w_a + news_norm * w_n
    if ff_norm is not None:
        composite += ff_norm * w_ff

    # 共振惩罚（v5.0 P2：改为"双弱比例惩罚"，系数可解释）
    # 只有当 tech 和 fund 都低于阈值时触发（技术面+基本面双弱 = 高不确定性）
    # penalty = 0.5 × (tech缺口×w_t + fund缺口×w_f)，即最多扣 50% 的缺口
    if resonance_penalty and tech_norm < RESONANCE_PENALTY_THRESHOLD and fund_norm < RESONANCE_PENALTY_THRESHOLD:
        weak_tech = max(0, (RESONANCE_PENALTY_THRESHOLD - tech_norm) * w_t)
        weak_fund = max(0, (RESONANCE_PENALTY_THRESHOLD - fund_norm) * w_f)
        penalty = weak_tech + weak_fund
        composite -= penalty * 0.5

    # 严重降级标记：额外扣分（v5.0 P2：20 → 15，可配置）
    has_severe = veto is not None and veto.components.get('veto_level') == 'severe'
    if has_severe:
        composite -= SEVERE_PENALTY

    composite = max(SCORE_MIN, min(SCORE_MAX, composite))

    # 等级
    if composite >= COMPOSITE_A:
        grade = 'A'
    elif composite >= COMPOSITE_B:
        grade = 'B'
    elif composite >= COMPOSITE_C:
        grade = 'C'
    else:
        grade = 'D'

    # 仓位（v5.0 P1：买点类型影响仓位）
    # 一买（反转确认）→ 可重仓；二买（回调确认）→ 标准；三买（追高）→ 降一档
    can_buy = tech_norm >= TECH_BUY_THRESHOLD
    if not can_buy:
        position = POSITION_NONE
    elif has_severe:
        position = POSITION_LIGHT  # 严重降级：最多轻仓
    elif alpha_norm < ALPHA_BUY_THRESHOLD:
        position = POSITION_LIGHT
    elif fund_score >= FUND_HEAVY_THRESHOLD:
        position = POSITION_HEAVY if grade == 'A' else POSITION_NORMAL
    elif fund_score >= FUND_LIGHT_THRESHOLD:
        # v5.3.1(M1/F9): 修复字符串比较反向——'A'(65) < 'B'(66)，原写法
        # grade>='B' 对 A 级判 False → A级被压轻仓而 B/C/D 反而正常仓。
        # 语义应为"评级达 B 及以上给正常仓"。
        position = POSITION_NORMAL if grade in ('A', 'B') else POSITION_LIGHT
    else:
        position = POSITION_LIGHT

    # 买点类型降档/升档（v5.0 P1）
    # 一买：底部反转确认，风险释放充分 → 允许重仓（升一档）
    # 三买：中枢突破追高，风险偏高 → 降一档
    # 反转后买点（level=0）：结构不明确 → 降一档
    if can_buy and not has_severe:
        if buy_level == 1:
            # 一买升档（但不超过重仓）
            if position == POSITION_LIGHT:
                position = POSITION_NORMAL
            elif position == POSITION_NORMAL:
                position = POSITION_HEAVY
        elif buy_level == 3 and fund_score >= FUND_HEAVY_THRESHOLD and alpha_norm >= ALPHA_BUY_THRESHOLD:
            # v5.4(M-03方案②·用户拍板2026-08-24): 高质量三买豁免降档——
            # 基本面强(fund≥60)+Alpha达标(≥40)的中枢突破, 追高的边际风险有
            # 基本面托底, 不再额外降档。背景: 当前候选池结构性单一化
            # (一二买零出现), 无差别降档曾致全员15%轻仓、仓位维度失效
            # (08-23实证11只全light)。level=0 反转后买点保留无条件降档。
            # 封顶 normal: 三买终究是中枢上沿追高, 即便质量达标也不给重仓
            # (A级基础仓位heavy在此被主动压回normal, 风控保守侧)。
            # TODO(回测标定): 阈值60/40 待 tech-score-backtest-validation
            # 用历史截面验证后再校准。
            if position == POSITION_HEAVY:
                position = POSITION_NORMAL
        elif buy_level in (3, 0):
            # 三买/反转后买点降档（但不低于轻仓）
            if position == POSITION_HEAVY:
                position = POSITION_NORMAL
            elif position == POSITION_NORMAL:
                position = POSITION_LIGHT

    # ── v5.3.3(E-1): 近期一卖/二卖压制 —— 高于一切仓位逻辑 ──
    if recent_top_sell:
        can_buy = False
        position = POSITION_NONE

    # ── v5.3.3(E-2): 观察型信号仓位封顶轻仓（可入池观察, 不进推荐）──
    if observational and not (veto is not None and veto.components.get('veto_level') == 'veto'):
        if position > POSITION_LIGHT:
            position = POSITION_LIGHT

    components_extra = {
        # v5.3.3(E-1/E-2): 冲突/观察标记透传到报告层
        'sell_conflict': bool(recent_top_sell),
        'observational': bool(observational),
    }

    return Score3D(
        composite=round(composite, 1),
        grade=grade,
        position=position,
        can_buy=can_buy,
        components={
            'tech_score': tech_norm,
            'fund_score': fund_norm,
            'alpha_score': alpha_norm,
            'news_score': news_norm,
            'fund_factor_score': ff_norm,
            'weights': (w_t, w_f, w_a, w_n, w_ff),
            'resonance_penalty_applied': (
                resonance_penalty
                and tech_norm < RESONANCE_PENALTY_THRESHOLD
                and fund_norm < RESONANCE_PENALTY_THRESHOLD
            ),
            'veto_level': 'severe' if has_severe else 'none',
            'severe_reasons': (veto.components.get('severe_reasons', [])
                               if has_severe else []),
            **components_extra,
        }
    )


def position_reason(result: Score3D) -> str:
    """生成仓位建议的人类可读说明"""
    parts = []
    c = result.components

    # Veto/严重降级
    if c.get('veto_level') == 'veto':
        reasons = '; '.join(c.get('veto_reasons', []))
        return f'⛔ 风控否决: {reasons}'
    if c.get('veto_level') == 'severe':
        reasons = '; '.join(c.get('severe_reasons', []))
        parts.append(f'⚠ 严重降级: {reasons}')

    if not result.can_buy:
        if result.components.get('sell_conflict'):
            parts.append("近期存在一卖/二卖信号 -> 买卖冲突仲裁, 不建仓 (v5.3.3 E-1)")
        else:
            parts.append(f"技术面{c['tech_score']:.0f}分 < {TECH_BUY_THRESHOLD} -> 不建仓")
    else:
        parts.append(f"技术面{c['tech_score']:.0f}分 >= {TECH_BUY_THRESHOLD} -> 可建仓")

        alpha = c.get('alpha_score', 50)
        if alpha < ALPHA_BUY_THRESHOLD:
            parts.append(f"Alpha因子{alpha:.0f}分 < {ALPHA_BUY_THRESHOLD} -> 仅轻仓")
        else:
            parts.append(f"Alpha因子{alpha:.0f}分 >= {ALPHA_BUY_THRESHOLD} -> 因子通过")

        fund = c['fund_score']
        if fund >= FUND_HEAVY_THRESHOLD:
            parts.append(f"基本面{fund:.0f}分 >= {FUND_HEAVY_THRESHOLD} -> 敢重仓")
        elif fund < FUND_LIGHT_THRESHOLD:
            parts.append(f"基本面{fund:.0f}分 < {FUND_LIGHT_THRESHOLD} -> 仅轻仓")
        else:
            parts.append(f"基本面{fund:.0f}分适中 -> 正常仓位")

        news = c['news_score']
        if news >= 70:
            parts.append("消息面正面")
        elif news <= 30:
            parts.append("消息面负面")

    if c.get('resonance_penalty_applied'):
        parts.append("(共振惩罚已应用)")

    return '; '.join(parts)


# ============================================================
# 自测
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  四维综合评分 + Veto 测试")
    print("=" * 60)
    print(f"  权重: T={W_TECH} F={W_FUND} A={W_ALPHA} N={W_NEWS}")
    print(f"  Veto关键词: {VETO_KEYWORDS}")
    print(f"  降级关键词: {SEVERE_KEYWORDS}")
    print()

    test_cases = [
        # (tech, fund, alpha, news, code, name, news_detail, risk_reasons, desc)
        (85, 75, 90, 50, "002415", "海康威视", "", None,
         "正常 -> 无否决"),
        (85, 75, 90, 50, "600999", "某公司", "公司被证监会立案调查", None,
         "Veto测试 -> 立案调查在新闻中"),
        (85, 75, 90, 50, "000001", "ST华业", "", None,
         "Veto测试 -> ST股名称"),
        (85, 75, 90, 50, "300888", "某公司", "大股东减持计划公告", None,
         "降级测试 -> 减持计划"),
        (75, 62, 70, 50, "600000", "浦发银行", "", ["被立案调查"],
         "Veto测试 -> risk_filter 结果"),
    ]

    for tech, fund, alpha, news, code, name, news_d, risk_r, desc in test_cases:
        r = compute_3d_score(
            tech, fund, alpha, news,
            code=code, name=name,
            news_detail=news_d, risk_reasons=risk_r,
        )
        pos_pct = f"{r.position*100:.0f}%"
        print(f"  {desc}")
        print(f"    T={tech} F={fund} A={alpha} N={news} -> "
              f"composite={r.composite:.1f} grade={r.grade} position={pos_pct}")
        print(f"    {position_reason(r)}")
        print()
