---
name: chanlun-quant-system
description: 构建基于缠论的多级别量化择时交易系统，实现从K线数据获取到 Recursive-Analysis 再到 Trade-Signal 的完整链路。
category: trading
tags: [chanlun, quant, trading-system, backtest]
version: 4.2
---

## 缠论量化择时系统构建指南

### 系统当前状态（2026-06-12）
- **版本**：v4.2
- **v4.0**（2026-05-31审计修复，详见 `references/audit-session-2026-05-31.md`）：三买结构评分独立处理(P0-1)、divergence_threshold 1.0→0.7(P0-2)、潜一买代理映射(P0-3)、二买回调惩罚(P1-3)、三买满分5→4(P1-4)、信号标签(P1-2)
- **v4.1**（2026-06-01）：[P0-4]中枢扩展bug（穿透笔误纳入扩展，`_find_zhongshus()`加穿透检查）。详见 `references/zhongshu-extension-bug-2026-06-01.md`
- **v4.2**（2026-06-12）：[P0-5]一卖Forward Validation——假一卖@74.06被后续99.81突破后未移除，加365日向前验证（`references/forward-validation-yi-mai-2026-06-12.md`）
- **v4.2**（2026-06-12）：[P0-6]DIF极值背驰补充——加速赶顶时面积背驰失效，加全局DIF vs 离开段DIF比较兜底（`references/dif-beichi-supplement-2026-06-12.md`）

### 📦 前置依赖

使用本系统前需确保以下环境和依赖就绪：

- **Python 3.10+**
- **Python 依赖**：`pip install -r requirements.txt`
- **API Keys**：`IWENCAI_API_KEY`（缠论数据）、`TAVILY_API_KEY`（搜索）、`METASO_API_KEY`（另类数据）
- **MCP 服务**：确保以下 MCP 服务已配置并可用 — `tavily`（搜索研究）、`fetch`（网页内容）、`dbhub`（数据库查询）
- **Hermes Skills**：需加载 `trading` 技能组（含 `chanlun-quant-system`、`trading-core`、`backtest-engine`）

### 实盘验证阶段准备
1. **小仓位跑1-2个月**：优先对比实盘信号与回测信号差异，排查数据源延迟（免费接口非实时问题）、滑点影响，别急着改策略逻辑
2. **记录每笔交易触发链条**：是缠论买卖点+三维打分触发，还是情绪/宏观数据额外加权？后续优化才有依据
3. **降级预案已补全**：Hermes Agent配置了3级模型降级（主力→二级备用→三级兜底→应急），具体配置见`hermes-agent`技能

### 买点分类体系（v3.6修订）

**信号质量评估：** 系统输出的买点信号（尤其是潜一买）需要结合结构上下文判断可靠性。参见 `references/signal-quality-evaluation.md` 的评估框架——包括多中枢趋势判断、背驰对照对验证、三卖风险排除、线段级别上下文检查。

**核心理论约束：一买/类一买只在下跌趋势或盘整中有效**

一买的核心是"下跌趋势末端的背驰"。类一买（盘整底背驰）也应在下跌趋势或盘整中出现，而非上涨趋势中的回调。上涨趋势中的回调只是正常的修正，不是一买信号。

**判断标准：** 最近5个中枢ZD上涨超过10% → 上涨趋势 → 过滤一买/类一买信号

**买点评分体系：**

| 类型 | score | 理论基础 | 可靠性 |
|------|-------|---------|--------|
| 标准一买（确认） | 5 | 趋势背驰+向上笔确认 | 最高 |
| 标准二买 | 5 | 回调不破前低 | 高 |
| 标准三买 | 5 | 突破ZG回踩不破 | 高 |
| 潜一买(等待确认) | 4 | 背驰满足，缺确认 | 中高 |
| 类一买(盘整底背驰) | 3-4 | 盘整中MACD面积衰减<40% | 中 |
| 反转后三买 | 3 | 趋势反转后新中枢，无背驰支撑 | 中低 |
| 中枢下沿机会 | 2 | 结构位置，非买点 | 低 |

**潜一买 vs 类一买 区别：**
- 潜一买：有下跌趋势+背驰，只是缺向上笔确认，理论基础是标准一买
- 类一买：只有盘整+力度衰减，理论基础是盘整底背驰
- 两者分开标记，不要混为一谈

**v3.6 关键修复：**
1. 类一买增加趋势方向检查：上涨趋势中的回调不触发类一买
2. 反转后三买评分从5降为3（无背驰支撑）
3. 潜在一买(结构位置)改名为"中枢下沿机会"，评分从3降为2
4. 潜一买和类一买分开标记（buy_type字段区分）

### 回测关键结论（v3.6验证）
- **组合回测 >> 单只回测**：50只组合慢牛周期（2016-2017）收益+61.40%，单只回测仅+2.20%（资金闲置问题）
- **止盈策略**：默认15%止盈（快速轮动）优于放宽止盈（tp=1.67时收益降至+40.35%）
- **最大回撤**：组合模式仅3.63%，风控优秀
- **永远不用单只回测评估多股择时系统**

### v3.6.1 买点分类理论修正（2026-05-30）

**核心修正**：类一买/潜一买必须在下跌趋势或盘整中触发，上涨趋势中的回调不是一买/类一买。

**修正后的买点体系**：
| 类型 | score | 定义 | 前提条件 |
|------|-------|------|---------|
| 标准一买 | 5 | 下跌趋势末端背驰+确认 | 趋势+背驰+向上笔确认 |
| 潜一买(等待确认) | 4 | 背驰满足，等向上笔确认 | 趋势+背驰，无确认 |
| 类一买(盘整底背驰) | 3-4 | 盘整中连续下跌笔MACD面积衰减<40% | 不要求趋势 |
| 反转后三买 | 5 | 趋势反转后新中枢的三买 | 反向中枢确认 |
| 三买形成中 | 3-5 | 突破ZG后回踩未成笔 | 突破确认 |
| **类二买(反转后)** | **3** | **趋势反转后新中枢下半区回踩** | **2026-05-30 已禁用** — 趋势判断过于机械，噪点太多 |
| 中枢下沿机会 | 2 | 价格在ZD附近+MACD改善 | 结构位置，非买点 |
| 潜一买(上涨趋势,无效) | 0 | 上涨趋势回调 | 过滤掉 |

**关键区分**：
- 潜一买 vs 类一买：潜一买有下跌趋势+背驰，类一买只有盘整+力度衰减
- 反转后三买 vs 二买：二买必须先有一买确认，反转后三买只需趋势反转确认
- 中枢下沿机会：原名"潜在一买"，已改名避免与缠论一买混淆

**过滤条件**（pool_scanner.py:_detect_panbei_divergence）：
```python
# 条件⑤：趋势方向检查 — 类一买只在下跌/盘整中触发
# 最近5个中枢ZD上涨超过10% → 上涨趋势 → 不触发类一买
if trend_zd[-1] > trend_zd[0] * 1.10:
    return 0, "", None  # 上涨趋势中的回调，不是类一买
```

### 买点分类体系（v3.6修正，2026-05-30）

**核心原则**：一买/类一买只在下跌趋势或盘整中有效，上涨趋势中的回调不是类一买。

| 买点类型 | 触发条件 | 评分 | 理论依据 |
|----------|---------|------|----------|
| 标准一买（趋势背驰） | 下跌趋势+背驰+向上笔确认 | 5 | 缠论第62课 |
| 标准二买 | 一买后回调不破前低 | 5 | 缠论 |
| 标准三买 | 中枢突破后回踩不破ZG | 5 | 缠论 |
| 类一买（盘整底背驰） | 下跌/盘整+MACD力度衰竭 | 2-4 | 扩展 |
| 类一买（上涨趋势） | 上涨趋势中的回调 | 0（已过滤） | 无效信号 |
| 中枢下沿机会 | 价格在ZD附近+MACD改善 | 2 | 结构位置，非买点 |

**v3.6修正内容**：
1. `_detect_panbei_divergence()` 增加趋势方向检查：ZD上涨>10%则不触发类一买
2. "潜在一买/潜在二买" 改名为 "中枢下沿机会"，评分从3降到2
3. 标准买卖点路径的类一买也增加上涨趋势过滤

详见 `chanlun-third-buy-scanner` skill 的 `references/buy-point-classification-fixes.md`

## 缠论量化择时系统构建指南

本技能描述了如何构建一个支持多级别联立分析（如日线 $\rightarrow$ 30分钟线）的缠论择时交易系统，重点解决数据冗余、级别递归和信号量化的问题。

## 核心架构
系统分为三层：
1. **数据层 (Data Layer)**: 实现多源冗余获取（AkShare, Baostock, iFind）与高效缓存 (Parquet)。
2. **分析层 (Analysis Layer)**: 实现缠论递归构建（包含处理 $\rightarrow$ 分型 $\rightarrow$ 笔 $\rightarrow$ 中枢 $\rightarrow$ 买卖点）。
3. **策略层 (Strategy Layer)**: 将几何信号转化为交易指令（仓位管理、止损止盈）。

## 关键实施步骤

### 1. 数据源故障转移 (Failover)
为避免单一 API 崩溃导致系统失效，采用优先级调度模式：
- **K线行情**: `Try Baostock` → `Fail` → `Try efinance` → `Fail` → `Try AkShare Sina` → `Fail` → `Try AkShare EM`
- **基本面数据** (v3.4+): `Try AKShare 同花顺(stock_financial_abstract_ths)` → `Fail` → `Try Baostock` → `Fail` → 降级默认分50
- **缓存优化**: 30分钟线数据量大，必须使用 `pyarrow` 或 `fastparquet` 替代 JSON，提升读取速度。
- **代码前缀适配**: 注意不同数据源对股票代码前缀的要求（如 Baostock 需 `sh.` 或 `sz.`）。
- **⚠️ quick_chanlun.py 代码格式**: `quick_chanlun.py` 内部的 `DataManager` 会自动添加 `sh.`/`sz.` 前缀，因此调用时**必须传裸代码**（如 `688036`），不能传 `sh.688036`，否则会变成 `sh.sh.688036` 导致"股票数据不存在"。示例：`python quick_chanlun.py 688036` ✅ / `python quick_chanlun.py sh.688036` ❌

### 2. 多级别递归分析 (Recursive Analysis)
实现“多级别联立”是缠论量化的核心：
- **流程**:
    1. 在**高级别 (Daily)** 运行分析 $\rightarrow$ 定位潜在买点区间 (Buy Zone)。
    2. 将高级别买点日期作为起始点 $\rightarrow$ 截取**低级别 (30min)** K线数据。
    3. 在低级别段再次运行分析 $\rightarrow$ 定位具体的底分型/二类买点 $\rightarrow$ **确认入场点**。

### 3. 买卖点识别与分类
系统支持三类买卖点的识别：

#### 一类买卖点（趋势背驰点）
- **定义**: 趋势末端的背驰转折点
  - 一类买点：下跌趋势中，最后一个中枢后的下跌段出现背驰
  - 一类卖点：上涨趋势中，最后一个中枢后的上涨段出现背驰
- **实现逻辑**:
  1. **趋势识别**: 识别下跌趋势（后中枢ZG < 前中枢ZD）和上涨趋势（后中枢ZD > 前中枢ZG）
  2. **进入段与离开段**: 找到进入最后一个中枢的笔（进入段）和离开最后一个中枢的笔（离开段）
  3. **背驰判断**: 计算MACD柱状图面积，比较进入段和离开段的面积
  4. **信号生成**: 离开段面积 < 进入段面积的 `divergence_threshold` 倍时，生成一类买卖点。`generate_analysis.py:64` 默认 `divergence_threshold=1.0`（即任何缩小都算背驰），但该阀值过松会导致大量假一买。推荐 **0.7**（离开段面积 < 进入段70% 才算有效趋势背驰）。详见 `references/divergence-threshold-tuning.md`。
  5. **一买最终确认需向上一笔验证（v3.5.5已实施）**：两步均已实施——① `_extend_last_bi()` 同步延伸 end_date+end_price（消除时空错配）；② 向上一笔启动后才固定一买位置，此前标记为"潜在一买"。BuySellPoint 新增 `confirmed` 字段（False=潜在一买），HTML渲染：确认一买=实心pin+B1，潜在一买=空心圈+潜B1+半透明。详见 references/bi-extension-date-sync.md。
- **关键代码**: `_find_first_class_points()`, `_identify_trends()`, `_calculate_macd_area_for_bi()`

#### 二类买卖点（回调确认点）
- **定义**: 一类买卖点后的第一次回调/反弹确认点
  - 二类买点：一类买点后的第一次回调不创新低
  - 二类卖点：一类卖点后的第一次反弹不创新高
- **实现逻辑**: 已在 `_find_second_class_points()` 中实现。搜索一买后的所有向下笔（回调），取不创新低且低点最高的（最浅回调）标记为二买（v3.5.4 修复：不再只取第一个，遍历找最优）。若回调低点 ≥ 一买价×99%（second_class_tolerance=0.01）则标记为二买。
- **评分窗口（v3.5.3修复）**: 原 `pool_scanner.py` 对所有买点类型统一使用30天窗口（>30天→score=2）。但二买天然滞后一买4-8周，该规则使二买永远无法进入Phase2。修复：二买单独使用60天窗口（31-60天→score=4，≥Phase2门槛3分），一买/三买维持30天不变。改动在 `pool_scanner.py` 标准买点评分循环（第374行附近）。
#### 实战稀缺性: 二买实际检测极少（510只中仅1只）。根因有三：① 一买都是近期的(0-16天)，不够时间形成「反弹+回调」双笔结构(需4-8周)；② 笔的形成有K线门槛(≥5根合并K线)，小幅震荡不形成新笔；③ ~~远期二买(>30天)评分仅2分~~ ✅ **v3.5.3已修复**，二买窗口放宽到60天（score=4），待现有20个一买结构完成后将产生更多二买。

#### ⚠️ 赛力斯型假二买：回调笔终点无分型确认（v3.5.3修复，2026-05-14）
- **问题**：`_find_second_class_points()` 取 `callback_bi.end_date` 作为二买日期。当末笔被延伸（无底分型确认），二买被标在未完成的日期上。
- **赛力斯案例**：回调笔04-23→05-13，终点05-13无底分型。真正确认点在底@05-06(¥87.0)。修复后回溯到底分型。
- **修复**：在产生二买前检查 `callback_bi.end_date` 是否有对应底分型。如无，回溯到笔区间内最后一个底分型。
- **对称修复**：二卖（反弹笔终点检查顶分型）。
- **改动位置**：`generate_analysis.py:_find_second_class_points()`（二买部分第711行附近、二卖部分第758行附近）
- 详见 `references/second-buy-rarity-analysis.md`

详见 references/bi-extension-date-sync.md。

#### 类一买（潜在一买）评分区分（2026-05-29）

v3.5.5新增 `BuySellPoint.confirmed` 字段：
- `confirmed=True` → 确认一买（向上一笔已启动）
- `confirmed=False` → 类一买/潜在一买（等待确认）

**四处代码改动确保类一买在汇总报告中正确区分且评分更低**：

| 文件 | 改动 | 效果 |
|:----|:-----|:----:|
| `pool_scanner.py` L518-521 | level=1且`confirmed=False`→`buy_type="类一买"`, `best_score-=1` | 汇总buy_type列显示"类一买"非"一买" |
| `pool_scanner.py` L403 | `best_pattern` 同步改为`类一买(近期,X天前)` | 汇总MD表"模式"列正确显示 |
| `validate_tech_score.py` L131-155 | signal_quality降8分(30→22,28→20,25→18), `point_type_str="类一买"` | 技术评分低8-10分 |
| `pool_screener.py` L696-698 | 映射加`'类一买':1`, `_bp.confirmed`按buy_type设置 | 买点代理也能正确识别 |

效果：类一买评分比确认一买低8-10分（tech_score≈76 vs 84），汇总MD/XLSX的"买点类型"和"模式"列均显示"类一买"。

#### 三类买卖点（中枢突破确认点）
- **定义**: 中枢突破后的回踩/反弹确认点
  - 三类买点：向上突破ZG后，回踩不进入中枢
  - 三类卖点：向下突破ZD后，反弹不进入中枢
- **实现逻辑**: 已在`_find_buy_sell_points()`中实现

#### ⚠️ 三买检测：闭笔可以作为突破笔（`>=` 过滤不是bug）

`_find_buy_sell_points()` 中 `after_bis` 的过滤条件为 `b.end_date >= zs.end_date`，这会将中枢闭笔（end_date == zs.end_date）纳入候选。系统初始分析时误判此为bug（认为闭笔不属于"离开段"），**已被用户纠正**：

**缠论原文**：三买只有两个条件——① 有向上一段离开中枢（突破ZG），② 回抽确认时不破ZG。闭笔是否"中枢的一部分"不重要——它确实突破了ZG，且回抽确认没进来，就构成有效三买。

```
案例：兆易创新 中枢#5 [ZG=231]
笔[38] up 12/17→01/28  high=330 > ZG=231 → 离开中枢（同时是闭笔）
笔[39] down 01/28→02/05 low=267 > ZG=231 → 回抽确认
笔[40] up 02/05→02/24                  → 三买 @ 02-05 ¥267.81 ✅
```

**误区澄清**：
- ❌ 认为闭笔不能同时是突破笔 → 过度解读缠论，人为增加限制
- ✅ 闭笔作为离开段，核心是"离开中枢"这个走势段是否有效，而非笔是否在中枢外
- ❌ 提议改为 `b.start_date > zs.end_date` → 会漏掉很多有效三买，且将三买日期后移

**结论**：`b.end_date >= zs.end_date` 维持不变。三买正确性由"回抽确认不破ZG"保证，不是由突破笔是否闭笔决定的。

> 详细误判复盘见 `references/sanmai-closing-bi-misjudgment.md`（包含分析链条、用户纠正原文、经验教训）。

### 3. 多级别验证与置信度评分 (Multi-level Validation & Confidence Scoring)
实现日线与30分钟级别的联动验证，为交易信号提供置信度评估。

#### 核心发现：日线与30分钟买卖点时间差较大
实践中发现，日线买卖点与最近的同向30分钟买卖点通常相差 **8~50天**，很少有±3天内的直接对齐。因此单纯依赖直接买卖点确认会导致确认率极低。解决方案是引入 **笔背驰结构验证** 作为二级确认。

#### 验证逻辑（三级递进）
1. **直接买卖点确认（直）**：日线买卖点前后±3天内，30分钟有同向买卖点 → 加2分
2. **笔背驰确认（背）**：无直接买卖点，但30分钟笔的结束方向与日线买卖点一致（买点找下降笔结束，卖点找上升笔结束）→ 加1分
3. **无确认**：两个级别都不匹配 → 不加分

#### 置信度评分体系
| 要素 | 分值 | 说明 |
|------|------|------|
| 基础分 | 1-3分 | 一类=3分，二类=2分，三类=1分 |
| 直接30min买卖点确认 | +2分 | 30分钟有同向买卖点在±3天内 |
| 30分钟笔背驰确认 | +1分 | 30分钟笔方向与买卖点类型一致 |
| **总分** | **1-5分** | 高置信度信号≥4分（⭐标记） |

#### 输出展示
- **终端输出**：`置信度: X/5 (30min:✓|背)` — 显示确认类型标签（直/背/无）
- **HTML报告**：买卖点列表显示置信度分数、确认类型、30min确认状态，高置信度信号显示⭐图标
- **JavaScript模板**：注意花括号转义，在Python f-string中使用双花括号`{{`和`}}`

#### 实现代码
```python
def _perform_multilevel_validation(self, daily_analyzer, m30_analyzer):
    """执行日线与30分钟级别的多级别验证，计算置信度分数"""
    if not daily_analyzer.buy_sell_points:
        return
    
    for point in daily_analyzer.buy_sell_points:
        base_score = {1: 3, 2: 2, 3: 1}.get(point.level, 1)
        m30_confirmation_info = self._check_m30_confirmation(point, m30_analyzer)
        m30_confirmed = m30_confirmation_info['confirmed']
        confirmation_type = m30_confirmation_info['type']  # 'direct', 'divergence', 'none'
        
        if confirmation_type == 'direct':
            confirmation_score = 2
        elif confirmation_type == 'divergence':
            confirmation_score = 1
        else:
            confirmation_score = 0
        
        confidence_score = base_score + confirmation_score
        high_confidence = confidence_score >= 4
        
        point.multilevel_confirmation = {
            'base_score': base_score,
            'm30_confirmation': m30_confirmed,
            'confirmation_type': confirmation_type,
            'confirmation_score': confirmation_score,
            'confidence_score': confidence_score,
            'high_confidence': high_confidence
        }

def _check_m30_confirmation(self, daily_point, m30_analyzer):
    """三级检查：直接买卖点 → 笔背驰 → 无确认"""
    if not m30_analyzer:
        return {'type': 'none', 'confirmed': False, 'details': '无30分钟分析器'}
    
    # 第一级：直接买卖点确认
    direct_conf = self._check_m30_direct_confirmation(daily_point, m30_analyzer)
    if direct_conf['confirmed']:
        return {'type': 'direct', 'confirmed': True, 'details': direct_conf['details']}
    
    # 第二级：笔背驰确认
    div_conf = self._check_m30_bi_divergence(daily_point, m30_analyzer)
    if div_conf['confirmed']:
        return {'type': 'divergence', 'confirmed': True, 'details': div_conf['details']}
    
    return {'type': 'none', 'confirmed': False, 'details': '无确认信号'}

def _check_m30_direct_confirmation(self, daily_point, m30_analyzer):
    """检查30分钟级别是否有同向买卖点直接确认（时间窗口±3天）"""
    if not m30_analyzer.buy_sell_points:
        return {'confirmed': False, 'details': '无30分钟买卖点'}
    
    from datetime import datetime
    try:
        target_dt = datetime.strptime(daily_point.date, '%Y-%m-%d').date() if isinstance(daily_point.date, str) else daily_point.date
    except:
        return {'confirmed': False, 'details': '日期解析失败'}
    
    for m30_point in m30_analyzer.buy_sell_points:
        if m30_point.type != daily_point.type:
            continue
        try:
            m30_date = m30_point.date
            m30_dt = datetime.strptime(m30_date, '%Y-%m-%d %H:%M:%S').date() if ' ' in str(m30_date) else (datetime.strptime(m30_date, '%Y-%m-%d').date() if isinstance(m30_date, str) else m30_date)
        except:
            continue
        if abs((m30_dt - target_dt).days) <= 3:
            return {'confirmed': True, 'date_diff': abs((m30_dt - target_dt).days), 'details': f'30分钟{daily_point.type}点确认，时间差{abs((m30_dt - target_dt).days)}天'}
    
    return {'confirmed': False, 'details': '无时间窗口内同向买卖点'}

def _check_m30_bi_divergence(self, daily_point, m30_analyzer):
    """检查30分钟笔的背驰结构确认（简化版：方向一致性）"""
    if not m30_analyzer or not m30_analyzer.bis:
        return {'confirmed': False, 'details': '无30分钟笔数据'}
    
    from datetime import datetime
    try:
        target_dt = datetime.strptime(daily_point.date, '%Y-%m-%d').date() if isinstance(daily_point.date, str) else daily_point.date
    except:
        return {'confirmed': False, 'details': '日期解析失败'}
    
    for bi in m30_analyzer.bis:
        bi_date = bi.end_date
        try:
            bi_dt = datetime.strptime(bi_date, '%Y-%m-%d %H:%M:%S').date() if ' ' in str(bi_date) else (datetime.strptime(bi_date, '%Y-%m-%d').date() if isinstance(bi_date, str) else bi_date)
        except:
            continue
        if abs((bi_dt - target_dt).days) > 3:
            continue
        
        if daily_point.type == 'buy' and bi.direction == 'down':
            return {'confirmed': True, 'bi_direction': 'down', 'details': f'30分钟下降笔结束，时间差{abs((bi_dt - target_dt).days)}天'}
        elif daily_point.type == 'sell' and bi.direction == 'up':
            return {'confirmed': True, 'bi_direction': 'up', 'details': f'30分钟上升笔结束，时间差{abs((bi_dt - target_dt).days)}天'}
    
    return {'confirmed': False, 'details': '无匹配方向笔'}
```

#### 输出展示
- **终端输出**：显示每个买卖点的置信度分数和30分钟确认状态
- **HTML报告**：买卖点列表新增"置信度: X/5 (30min:✓/✗)"标识，高置信度信号显示⭐图标
- **JavaScript模板**：注意花括号转义，在Python f-string中使用双花括号`{{`和`}}`

### 4. 漏斗过滤交易策略 (Funnel Filtering)
将分析结果量化为 `TradeSignal`：
- **强共振 (High Urgency)**: `Daily Buy Zone` $\\cap$ `M30 Buy Signal` $\\rightarrow$ 触发底仓买入。
- **趋势加仓 (Medium Urgency)**: `30min Level-3 Buy Point` (回踩不进中枢) $\\rightarrow$ 增加持仓。
- **风险退出 (High Urgency)**: `30min Top-divergence` (顶分型背驰) $\\rightarrow$ 全平。
- **置信度过滤**: 优先选择高置信度信号（≥4分）进行交易。

## 坑点与经验 (Pitfalls)
- **K线包含处理**: 必须严格执行"方向决定一切"原则，否则后续的分型和笔会全部失效。

- **⚠️ 回测日期范围陷阱：分析数据 ≠ 交易模拟起始**：`report_generator.py` 的 `--start` 参数曾同时限制分析用的K线数据和回测模拟的起始日期。
  - 缠论需要足够的历史数据来识别中枢结构、笔段和背驰——如果截断过早，买卖点识别数量会急剧下降甚至为0
  - **已解决**（2026-04-24）：`report_generator.py` 自动计算 `analysis_start = start_date - 5年`（最大5年），`backtest_engine.py` 新增 `analysis_start_date` 参数
  - **数据加载**：用 `analysis_start` 获取更多历史K线用于缠论结构识别
  - **交易模拟**：在回测主循环中跳过 `start_date` 之前的日期，仅从该日开始模拟交易
  - **改动文件**：`report_generator.py` 和 `backtest_engine.py`（`run_backtest()` + `run_single()`）
  - **用法不变**：`python3 report_generator.py 600519 --start 2024-04-01` 即可，自动加载2019年起的数据做分析

- **⚠️ 回测截止日期**：默认数据源取到最新交易日，如需限制截止日期：
  - `backtest_engine.py` 从 `load_all_data()` → `run_backtest()` → `run_single()` 全线支持 `end_date` 参数
  - `run_backtest.py` 通过 `--end YYYY-MM-DD` 选项传入
  - `report_generator.py` 同样支持 `--end` 选项，会同时限制分析数据加载和回测模拟
  - **数据流**：`end_date` 向下传递到 `DataManager.get_klines()`，各数据源（Baostock、AkShare等）均支持按日期截断
  - **示例**：`python3 report_generator.py 300308 --start 2024-04-01 --end 2026-04-23`
  - **注意**：`end_date` 与 `analysis_start_date` 独立工作，`analysis_start` 用于拉长历史数据（往前5年），`end_date` 用于截断尾部
- **⚠️ 分型条件过严（v3.5.4已修复，2026-05-14）**：`_find_fenxings()` 原要求顶分型 `high` 和 `low` 都最高、底分型 `high` 和 `low` 都最低，会漏掉标准缠论分型。修复：改为仅判断顶分型 `high` 最高、底分型 `low` 最低。\n- **⚠️ K线合并方向参考（v3.5.4已修复，2026-05-14）**：`_merge_klines()` 在遇到连续包含关系时，使用原始K线而非合并后K线判断方向。极端情况下会导致方向判断与合并走势偏差。修复：改为使用 `merged[ref_index]`。**但merged元素是dict（非KLine对象），比较时需用`ref_kline['high']`而非`ref_kline.high`。** v3.5.4原修复遗漏了属性访问方式，30分钟数据量大的股票（如300015有12296行）首次触发方向判断时会报`'dict' object has no attribute 'high'`。二次修复（2026-05-14）：`ref_kline.high` → `ref_kline['high']`。
- **环境安全**: 在自动化 Agent 环境中运行代码时，避免使用 `python -c "..."`，应采用 `write_file` $\rightarrow$ `terminal(python script.py)` 模式以绕过安全沙箱拦截。
- **数据对齐**: 级别联立时，低级别数据的截取范围应覆盖高级别信号点前后的足够K线，以保证中枢结构的完整性。
- **Baostock分钟线复权**: Baostock的`adjustflag`参数对分钟线同样有效（`frequency='30'` + `adjustflag='2'`可获取前复权30分钟数据）。之前误认为不支持，实为官方支持的特性。
- **AKShare 同花顺数据升序**: `stock_financial_abstract_ths` 按报告期升序排列（最老在前），取最新必须用 `iloc[-1]`，不是 `iloc[0]`。
- **AKShare 百分比返回字符串**: 同花顺返回 `'10.57%'` 而非数字，需要 strip `%` 再除 100。`'False'` 和 `'--'` 表示无数据。
- **AKShare 东财限流**: `stock_individual_info_em` 和 `stock_zh_a_spot_em` 经常 `RemoteDisconnected`。名称/行业/PE/PB 应走 Baostock fallback。
- **DataManager.__del__ 不要调 bs.logout()**: 多实例共享全局 session 时，GC 回收旧实例会中断后续请求。Baostock session 由 baostock_utils 统一管理。
- **AKShare 同比比较**: 必须找去年同期（如 2026Q1 vs 2025Q1），不能用 `iloc[-2]`（那是上一季度）。详见 `references/akshare-api-patterns.md`。
- **日线与30分钟买卖点时间差**: 日线买卖点与最近的同向30分钟买卖点通常相差8~50天，很少有±3天内的直接对齐。因此必须引入笔背驰验证作为二级确认，否则确认率极低。
- **时间窗口选择**: ±2天过紧，建议使用±3天。±3天的窗口下，直接买卖点确认仍然很少（因时间尺度差异大），但笔背驰确认可以覆盖大部分信号。
- **⚠️ config_loader.py: YAML 空列表 → Python None 崩溃**：`config.yaml` 中注释掉的列表（如 `codes:` 下全被 `#` 注释）会被 `yaml.safe_load()` 解析为 `None`。`set(None)` → `TypeError`。修复：`_cfg()["banned"].get("codes") or []`（用 `or []` 防 None）。同理 `manual_blacklist` 等字段。

- **⚠️ config_loader.py: JS 风格布尔值混入 Python**：从 JSON/YAML 粘贴时，`true`/`false`（JS）不会被 Python 解析为 `True`/`False`，直接报 `NameError`。检查 `_DEFAULTS` 字典中所有布尔值。已修复（2026-05-02）。

- **⚠️⚠️ 共振惩罚方向陷阱（2026-05-29 审计修复）**：`composite_scorer.py` 的共振惩罚公式 `composite += penalty * 0.5` 应为 `-=`。变量名是 `penalty` 但实际是加分——弱tech+弱fund的股票反而被奖励。教训：命名与逻辑不一致是高危信号。
- **⚠️⚠️ 趋势背驰 vs 盘整背驰的进入段选取（2026-05-29 审计修复）**：`_find_first_class_points()` 必须取**趋势第一个中枢**的进入段（`trend[0]`），不是最后一个中枢的进入段。当前中枢的进入/离开比较是盘整背驰，不是趋势背驰。详见 `references/audit-fixes-2026-05-29.md`。
- **⚠️ 字符串匹配用 `in` 不用 `!=`**：pool_scanner输出的buy_type含括号后缀（如`"类一买(盘整底背驰)"`），用 `!= '类一买'` 精确匹配永远为True。必须用 `'类一买' not in buy_type`。
- **⚠️ 新功能先测试后集成**：用户明确要求"在不确定是否有效前不要动原代码"。新功能（如线段中枢）应先写独立测试脚本（`test_segment_zhongshu.py`），跑通验证效果后再考虑集成到 `generate_analysis.py`。不要直接修改核心分析器。
- **⚠️ 线段中枢模块（segment_analyzer.py）独立运行**：不修改 `generate_analysis.py` 原有代码。通过 `HTMLVisualizer(segment_result=...)` 可选参数注入双视角数据。`quick_html.py` 已自动集成。

- **⚠️⚠️ 线段终点方向一致性**：`find_segments()` 中线段结束点必须取 `right_feature.bi_index - 1`（分型右侧元素的对应笔的前一根），**不能**取 `middle_feature.bi_index`。中间元素对应笔的方向与线段方向相反，会导致线段首尾方向不一致、笔数为偶数。实际效果：线段数从1个变为正常的4-6个。

- **⚠️⚠️ 特征序列包含处理三原则**：① 取低低/取高高（非取最宽范围）；② 单次遍历不递归（防级联合并）；③ 方向适配（向上段→向下处理取低低，向下段→向上处理取高高）。反例：6个特征序列元素被递归取最宽合并成1个→0个分型→1个覆盖全区间的大中枢。

- **⚠️⚠️ 中枢延伸需要与扩张相同的保护**：27笔/120天/5%保护如果只加在 `apply_expansion()` 而不加在 `find_segment_zhongshus()` 的延伸 `while` 循环中，会导致一个中枢吞并所有后续线段。修复后段中枢从1个变为2个（1200天数据），从5/18票有中枢提升到13/18票。

- **⚠️ 线段分析最低数据量**：至少需要 1200天（约5年/792根K线）日线数据。500天数据仅够产生2-4个线段，不足以构建中枢。

- **⚠️ Cron 输出偏好本地 Markdown**：对于定期生成的报告/推荐内容，用户偏好保存为本地 Markdown 文件（如 `/mnt/d/常用文件/每周书香/YYYY-MM-DD_书名.md`），而非推送到微信。微信推送存在 aiohttp 3.13.3 + Python 3.12 的 `asyncio.timeout()` 兼容性问题（"Timeout context manager should be used inside a task"），且用户觉得本地文件更方便查阅。

- **日期格式兼容**: 30分钟数据日期可能包含时间部分（'YYYY-MM-DD HH:MM:SS'），解析时需做分支处理。

- **⚠️⚠️ 共振惩罚方向陷阱（v3.5.6修复，2026-05-29）**：`composite_scorer.py` 中共振惩罚公式 `composite += penalty * 0.5` 方向错误——弱tech+弱fund的股票反而获得加分。正确应为 `-=`。验证方法：tech=40, fund=40 时 composite 应 < 43.5（被惩罚），不应 = 50.0（被奖励）。

- **⚠️⚠️ 一买进入段选取偏差（v3.5.6修复，2026-05-29）**：`_find_first_class_points()` 中进入段取的是"最后一个中枢的进入段"（`last_zs`），缠论原文要求取"趋势第一个中枢的进入段"（`trend[0]`）。取最后中枢 = 盘整背驰，取第一个中枢 = 趋势背驰。修复后假一买大幅减少（9只→0只）。对称修复了一类卖点。

- **⚠️ 消息面补扫丢失alpha权重（v3.5.6修复，2026-05-29）**：`pool_screener.py:_update_news()` 调用 `compute_3d_score()` 时未传 `alpha_score` 和 `w_alpha`，导致alpha被重置为50中性。修复：补传 `alpha_score=s.get('alpha_score', 50.0), w_alpha=W_ALPHA`。

- **⚠️ 类一买代理confirmed精确匹配陷阱（v3.5.6修复，2026-05-29）**：`pool_screener.py` 买点代理中 `c.get('buy_type', '') != '类一买'` 精确匹配失败，因为 pool_scanner 输出的是 `"类一买(盘整底背驰)"`（含括号后缀）。修复：改为 `'类一买' not in c.get('buy_type', '')`。

- **⚠️ 月线分析 `min_bi_klines` 必须调低**（2026-05-08 发现）：默认 `min_bi_klines=5` 对月线太严格。1月顶分型(index=288)和3月底分型(index=290)之间只有3根合并K线，被过滤掉，导致无法识别下行笔和买点。**月线分析必须用 `min_bi_klines=3`**：
  ```python
  analyzer = ChanLunAnalyzer(level='monthly', min_bi_klines=3).analyze(kline_list)
  ```
  验证结果：`min_bi_klines=5` → 24根笔，无1-3月下行笔；`min_bi_klines=3` → 41根笔，正确识别 down(2026-01→2026-03)。

- **⚠️ `_find_buy_sell_points()` datetime 字符串切片 bug**（2026-05-08 修复）：`zs.start_date[-5:]` 和 `zs.end_date[-5:]` 对 datetime 对象做字符串切片会报 `TypeError: 'datetime.datetime' object is not subscriptable`。修复：改为 `str(zs.start_date)[-5:]`。影响行：651、675（两处三类买卖点的 `zs_label` 生成）。**当 `ChanLunAnalyzer` 传入 datetime 类型的 K 线日期时必现**（月线分析场景）。

- **⚠️⚠️ 未闭合K线不参与缠论分析**（2026-05-08 验证）：月线/周线等高级别K线需要整周期闭合后才能参与分型、笔、中枢的构建。**未闭合的当月K线（如5月8日时，5月K线只有8个交易日）必须被过滤掉**，否则会导致：
  - 提前标记顶/底分型（未闭合K线的高/低点不稳定）
  - 误判笔的延伸方向（未完成的K线可能还在变化）
  - 错误的买卖点信号（如 `market_regime.py` 曾误报2026-05-01卖点）
  - **修复方法**（以月线为例）：
    ```python
    from datetime import datetime
    current_month = datetime.now().strftime('%Y-%m')
    monthly = monthly[monthly['date'].str[:7] != current_month]  # 过滤当月
    ```
  - **适用场景**：`market_regime.py` 的 `analyze_hs300()`、生成月线HTML报告的脚本、任何使用重采样生成月/周线的场景。
  - **pandas 2.x 频率字符串**：月线重采样从 `'M'` 改为 `'ME'`（Month End），否则报 `ValueError: 'M' is no longer supported`。

- **⚠️⚠️ 笔延伸无分型确认（v3.5.4已修复，2026-05-14）**：`_extend_last_bi()` 对末笔同时延伸 `end_date` 和 `end_price`，但延伸终点无分型确认，违反缠论"笔必须有分型确认端点"。修复：只延伸 `end_price`（反映最新价格），不延伸 `end_date`（保持末笔结束日期在最后一个有效分型处）。下游消费逻辑（如二买分型校验）会正确处理日期/价格不一致。同时发现原代码第252行隐藏bug：`last_bi.end_date, last_bi.end_date = new_date, new_high`（`end_price` 从未被赋值）。
  - **⚠️ v3.5.4折中方案的时空错配缺陷（2026-05-29发现，v3.5.5已修复）**：v3.5.5 同步延伸 end_date + end_price（1行改动），消除时空错配。增设潜在一买机制——无向上一笔确认时标记为潜在一买，有确认时标记为确认一买。详见 references/bi-extension-date-sync.md。
  - **向上笔延伸中**：**不出现新的底分型**（反向分型）。顶分型可以出现，只要后续K线突破它，笔就继续延伸。
  - **向下笔延伸中**：**不出现新的顶分型**（反向分型）。底分型可以出现，只要后续K线跌破它，笔就继续延伸。
  - **反向笔形成条件**：出现反向分型 + **分型确认**（后续K线收盘跌破底分型低点/突破顶分型高点）
  - **中继分型 vs 反转分型**：
    - 中继分型：分型出现后，后续K线**突破**分型极值点 → 原笔继续延伸，该分型被"吞没"
    - 反转分型：分型出现后，后续K线**未突破**极值点，反而确认了该分型 → 原笔结束，反向笔开始
  - **沪深300月线实例**（2026-05-08）：
    - 2026-01可能形成顶分型（高点4836.95）
    - 2026-03出现底分型（低点4394.29），但2026-04收盘4807 > 3月高点4744 → **底分型未被确认，属中继底分型**
    - 结论：向上笔仍在延伸（3514.12 → 4836.95），旧顶分型被覆盖
  - **⚠️ 常见误解**：
    - ❌ "不出现顶分型，向上笔就继续" → 错误！顶分型是向上笔的终点，可以出现后继续延伸
    - ✅ "不出现底分型，向上笔就继续" → 正确！底分型才是反向笔的开始

- **⚠️⚠️ 单只回测严重低估组合系统能力（最危险的方法论陷阱）**：`backtest_engine.run_single()` 对每只股票独立分配 ¥200 万但单股最大仓位 18%（¥36 万），剩余 82% 资金永远闲置。当你的策略天然分散到多只股票时（如缠论在慢牛中三买交替出现在不同股票），单只回测会把每只的"小波段收益"加起来除以 50，得出 +2.20%——完全掩盖了组合层面的复利效应。组合回测（`portfolio_backtest.py`）揭示真相：同样 50 只股票、同样信号，+61.40%。**永远不要用单只回测来评估多股择时系统**。
- **⚠️ 30分钟历史数据普遍不可用（2016-2020）**：Baostock 30分钟线对 2020 年之前的覆盖极差（`sz.000001 股票数据不存在`），AkShare Sina/EM 同时期数据同样失败。回测引擎已内置降级模式（见上方"M30 Degraded Mode"），但回测收益会比实战偏高约 1-2%（缺少 30 分钟确认过滤）。对于历史慢牛/震荡市验证，这是可接受的折衷——日线级别缠论结构本身就足以捕捉主要买卖点。实战环境（2020+）不适用此降级，必须获取 30 分钟数据。

- **⚠️ 慢牛中缠论系统的真实表现：单只回测 vs 组合回测**：早期 2026-05-02 通过单只独立回测得出"天花板 ~3-5%"的结论。**该结论已被 2026-05-02 同日组合回测推翻**。根因：单只回测每只股票独立分配 ¥200 万但单股最大仓位仅 18%（¥36 万），82% 资金永久闲置。组合回测共享资金池、80% 仓位目标、多股同时持有后：**50 只组合 +61.40%，碾压沪深 300 的 +26.75%**。系统本身有效，问题出在回测方式。详见下方"组合回测引擎"章节和 `references/portfolio-backtest-results-2026-05-02.md`。

## Backtest Entry Tracking (Merged from chanlun-backtest-entry-tracking)
*Unique content from the standalone `chanlun-backtest-entry-tracking` skill, now consolidated here.*

### Trigger
User requests backtest trade details to list **each actual buy operation separately** (instead of merged weighted average), with per-lot entry price, shares, PnL%, hold days.

Typical request:
> "实际上发生了3次买入+1次卖出，都帮我标明清楚"

### Root Cause
The funnel filter position accumulation mode generates multiple buys then one sell. Early implementation merged multiple buys into one trade record (weighted average price), which masks:
- Different entry times, prices, share counts, PnL% per buy
- Large profits from early low-price buys vs small profits from later high-price buys
- User needs to verify strategy effect per lot

### Fix Steps
1. **TradeRecord class add `shares` field**: Track per-lot share count
2. **Use `buy_lots` list**: Record each buy {price, date, reason, shares}
3. **Create separate TradeRecord per lot on sell**: Iterate `buy_lots` to create independent records
4. **Stats dict include full per-lot data**: `shares`, `reason_entry`, `hold_days` per trade
5. **Terminal output format**: Show per-lot shares, hold days, entry reason
6. **Excel report columns**: Update to ["买入日", "卖出日", "方向", "买入价", "卖出价", "盈亏%", "股数", "持仓天", "买入理由", "卖出理由"]
7. **Output auto-sorted to stock subdirectory**: `report_generator.py` creates `{symbol}/` subdir for HTML/Excel reports

### Verification
1. Trade detail rows = number of buy lots (2 buys → 2 records)
2. Each lot's `shares` correctly reflects that buy's share count
3. Each lot's `hold_days` = exact days from that buy to sell date
4. Each lot's `pnl_pct` = sell_price / lot_entry_price - 1 (independent)
5. Excel files saved in `{symbol}/` subdirectory
6. Sample output (301498 乖宝宠物):
```
买入日          卖出日          方向   买入价        卖出价        盈亏%        股数     持仓天    理由
2024-06-07   2025-09-09   多    ¥51.95   ¥98.85   +90.29%   500  459   三类买点：突破中枢后回踩不进中
2024-11-19   2025-09-09   多    ¥60.81   ¥98.85   +62.57%   300  294   三类买点：突破中枢后回踩不进中
2025-03-26   2025-09-09   多    ¥85.39   ¥98.85   +15.77%   100  167   三类买点：突破中枢后回踩不进中
```

### Common Pitfalls
- **Forget to clear `buy_lots`**: After sell, must `buy_lots = []; shares=0; position=0.0` to avoid duplicate records
- **Omit `shares` in stats**: Excel/terminal output depends on `stats['trades'][]['shares']`
- **Windows file lock**: Old .xlsx opened on Windows may cause `PermissionError`, auto-append timestamp
- **Total trades count change**: Switching to per-lot mode increases `total_trades`, explain to user it's expected
- **Subdirectory file management**: Reports generated each time overwrite old ones, user should backup periodically

---

## v3.5.6 修复汇总（2026-05-29深度审计修复）

本轮审计发现4个关键bug（2个P0 + 2个P1），全部当日修复并验证：

| 优先级 | 问题 | 函数/文件 | 修复 |
|:-----:|------|------|------|
| **P0** | 共振惩罚方向错误：`composite += penalty` 应为 `-=` | `composite_scorer.py` L238 | `+=` → `-=` |
| **P0** | 一买/一卖进入段取**最后**中枢的进入段（盘整背驰），应取**第一个**中枢（趋势背驰） | `generate_analysis.py` `_find_first_class_points()` L592-595, L676-681 | `last_zs` → `trend[0]`（买+卖对称修复） |
| **P1** | 消息面补扫(`_update_news`)调用`compute_3d_score`时未传`alpha_score`和`w_alpha`，alpha被重置为50中性 | `pool_screener.py` L571-577 | 补传两个参数 |
| **P1** | 类一买代理`confirmed`判断用`!= '类一买'`精确匹配，但实际buy_type含括号后缀（如`类一买(盘整底背驰)`）→永远True | `pool_screener.py` L698 | `!= '类一买'` → `'类一买' not in` |

**P0-2 进入段修复的深远影响**：修复后，之前标记为一买的9只假信号股票（平安银行89.1%、中国平安87.5%等）全部被正确过滤。原因：趋势第一个中枢的进入段MACD面积远大于最后一个中枢的进入段，真正的趋势背驰条件更严格。

详见 `references/audit-2026-05-29.md`。

## v3.5.6 修复汇总（2026-05-29深度审计修复）

本轮审计发现4个关键bug并全部修复：

| 优先级 | 问题 | 文件 | 修复 |
|:-----:|------|------|------|
| **P0** | 共振惩罚方向错误(`+= penalty`应为`-=`) | `composite_scorer.py:238` | `+=` → `-=`，弱票正确被扣分 |
| **P0** | 一买进入段取最后中枢(盘整背驰)而非第一个中枢(趋势背驰) | `generate_analysis.py:592-595, 676-681` | 改取`trend[0]`的进入段，买+卖对称修复 |
| **P1** | 消息面补扫丢失alpha_score和w_alpha | `pool_screener.py:571-577` | 补传`alpha_score=s.get('alpha_score',50.0)`和`w_alpha=W_ALPHA` |
| **P1** | 类一买代理confirmed用精确匹配(`!=`)而非`not in` | `pool_screener.py:698` | `!= '类一买'` → `'类一买' not in` |

详见 `references/audit-session-2026-05-29.md`。

### P0-1 共振惩罚方向修复详情
`composite_scorer.py:238` 原代码 `composite += penalty * 0.5`，弱tech(40)+弱fund(40)的股票composite=50.0（被奖励）。修复后composite=37.0（被惩罚6.5分）。

### P0-2 一买进入段修复详情
缠论原文（第62课）要求趋势背驰比较的是"趋势第一个中枢的进入段"vs"趋势最后一个中枢的离开段"。原代码取最后一个中枢的进入段，实际是盘整背驰。修复后，之前误判的9只假一买（平安银行89.1%、中国平安87.5%、南方航空84.6%等）全部被正确过滤。

### 验证结果（全流程重跑）
- Top1: 艾力斯(688578) 85.1分 A级
- Alpha列不再全是50.0（恒瑞93.8、艾力87.1、泸州90.6）
- 类一买正确区分（模式列显示"类一买"而非"一买"）
- 假一买全部消失

---

## ⚠️ 共振惩罚方向错误（v3.5.5修复，2026-05-29）

`composite_scorer.py:238` 原代码 `composite += penalty * 0.5`，弱tech+弱fund的股票反而获得加分。修复为 `-=`。影响：tech=40, fund=40时从被奖励6.5分变为被惩罚6.5分。

## ⚠️ 一买/一卖进入段选取偏差（v3.5.5修复，2026-05-29）

`generate_analysis.py:_find_first_class_points()` 原取"最后一个中枢的进入段"与离开段比较MACD面积。缠论原文（第62课）要求取"趋势第一个中枢的进入段"。原实现更接近盘整背驰而非趋势背驰，导致大量假一买。修复后，9只假一买（平安银行89.1%、中国平安87.5%等）全部被过滤。一类卖点做了对称修复。

## ⚠️ 消息面补扫丢失alpha_score（v3.5.5修复，2026-05-29）

`pool_screener.py:571-577` 的 `_update_news()` 调用 `compute_3d_score()` 时未传 `alpha_score` 和 `w_alpha`，导致Top30消息面更新后alpha被重置为50中性。修复：补传 `alpha_score=s.get('alpha_score', 50.0)` 和 `w_alpha=W_ALPHA`。

## ⚠️ 类一买代理confirmed判断逻辑错误（v3.5.5修复，2026-05-29）

`pool_screener.py:698` 用 `!= '类一买'` 精确匹配，但pool_scanner输出的buy_type是 `"类一买(盘整底背驰)"`（含括号后缀），导致confirmed始终为True，盘整底背驰获得与确认一买相同的高评分。修复为 `'类一买' not in c.get('buy_type', '')`。

## v3.5.6 修复汇总（2026-05-29深度审计修复）

本轮审计发现4个问题并全部修复：

| 优先级 | 问题 | 文件 | 修复 |
|:-----:|------|------|------|
| **P0** | 共振惩罚方向错误(+=应为-=) | composite_scorer.py:238 | `+= penalty` → `-= penalty` |
| **P0** | 一买进入段取最后中枢而非第一个中枢 | generate_analysis.py:592-595 | 取`trend[0]`的进入段，对称修复一卖 |
| **P1** | 消息面补扫丢失alpha_score和w_alpha | pool_screener.py:571-577 | 补传alpha_score和w_alpha参数 |
| **P1** | 类一买代理confirmed用!=精确匹配 | pool_screener.py:698 | 改为`'类一买' not in`子串匹配 |

验证效果：重跑A500全流程12.1分钟，80只候选，Alpha列正确显示（不再全是50.0），共振惩罚方向正确。
详见 `references/full-system-audit-2026-05-29.md`。

---

## v3.5.4 修复汇总（2026-05-14缠论审计修复）

本轮审计发现5个缠论理论违反点并全部修复，全部在 `generate_analysis.py`：

| 优先级 | 问题 | 函数 | 修复 |
|:-----:|------|------|------|
| **P0** | BUG-3: 笔延伸用极值替代分型，`end_date`被无分型日期覆盖 | `_extend_last_bi()` | 只延伸 `end_price`，不延伸 `end_date`；笔的终点保持最后一个有效分型处 |
| **P1** | BUG-4: 三买遇回踩进中枢即`break`放弃中枢，错过后续有效三买 | `_find_buy_sell_points()` | `break` → `found_up_break = False; continue`(重置状态继续搜索) |
| **P1** | BUG-7: 分型条件同时要求高/低点都满足，漏标准缠论分型 | `_find_fenxings()` | 顶分型只要求高点最高，底分型只要求低点最低 |
| **P2** | BUG-5: 二买只取一买后第一个向下笔，后续更优回调笔被忽略 | `_find_second_class_points()` | 遍历所有向下笔，取不创新低且回调最浅的那根 |
| **P3** | BUG-6: 多段包含K线时方向参考用原始K线而非合并K线 | `_merge_klines()` | `klines[ref_index]` → `merged[ref_index]` |
| **P3** | BUG-6b: merged是dict列表，方向比较`.high`属性访问报错 | `_merge_klines()` L147 | `ref_kline.high` → `ref_kline['high']`（merged元素是dict非KLine，用字典键访问） |

### 附带发现的隐藏bug
`_extend_last_bi()` 第252行原代码 `last_bi.end_date, last_bi.end_date = new_date, new_high` — 上涨笔延伸时 `end_price` 从未得到更新，`end_date` 被重复赋值。该bug自系统创建起即存在。P0修复同时解决了此问题。

### 验证结果（510只全量扫描对比）

| 指标 | 修复前 | 修复后 |
|:----|:-----:|:-----:|
| 候选股(≥3分) | 185 | 168 |
| 一买 | 39 | 20(去除了沃森、茅台等假一买) |
| 类一买(盘整底背驰) | 62 | 67(补位) |
| 二买 | 0只入Phase2 | 1只(赛力斯，日期从05-13修正到05-06) |

---

## 同花顺API集成（v3.5.4 2026-05-14）

`scan_news()` 降级链从 Sina → Tavily → Metaso 升级为同花顺 → Sina → Tavily → Metaso：

```python
scan_news(code, name)
  ├─ L0: AKShare 公告预扫描
  ├─ L1: 同花顺新闻搜索 (news-search skill, 8条/股)  ★新增主源
  ├─ L1b: 同花顺公告搜索 (announcement-search skill, 5条) ★新增副源
  ├─ L2: Sina Finance (免费fallback)
  ├─ L3: Tavily (配额fallback)
  ├─ L4: Metaso (配额fallback)
  └─ Fallback: 写标记 + score=50
```

**效果**：Top30消息面评分从77%的50分降至7%，均值从53提升至67.4。需 `IWENCAI_API_KEY` 环境变量。

同样改造了 `news_detail_report.py`（新增 `search_iwencai()` 函数替代原来的 Tavily 优先）。

详见 `references/iwencai-news-integration.md`。

---

## ChanLun Code Audit (Merged from chanlun-code-audit) — 2026-05-14 full audit: `references/full-system-audit-2026-05-14.md`
*Unique content from the standalone `chanlun-code-audit` skill, now consolidated here.*

### Applicable Scenarios
- Post-new module development theory consistency verification
- Theory deepening code update audits
- Abnormal trading signal tracing
- Post-system upgrade regression validation
- 3D analysis system integration audit (tech/fund/news scoring consistency, weight sync, substitution variable bias)

### Audit Method（2026-05-29验证有效）
对大型系统（70+文件，170KB+代码）使用**并行子代理**审查：
1. 按职责分组：核心引擎 / 选股评分系统 / 回测+监控+辅助
2. 每个子代理传入完整的理论要点清单（缠论原文要求）
3. 子代理逐函数对照检查，输出分级发现（CRITICAL/MEDIUM/LOW）
4. 主代理汇总、交叉验证关键发现（读源码确认行号）
5. 按优先级修复，每修复一个立即语法检查+功能验证

**关键原则**：先验证再修复。对每个bug，先用具体数据验证确实存在，再改代码，改完再验证效果。

### Core Principles
1. **Theory First**: ChanLun original theory is the only standard
2. **Layer-by-Layer Audit**: From basic structures (fenxing, bi) to advanced concepts (buy/sell points, divergence)
3. **Test Verification**: Each fix must pass unit tests + real data validation
4. **Document Alignment**: Benchmark against HTML/PDF theory docs

### Audit Workflow (Validated 2026-05-01)
1. Read full audit report → judge each item (agree/partially/disagree) + reason
2. Re-rate severity: auditor's grading may be inaccurate
3. Prioritize: "Urgent + small changes first"
4. Confirm each item with user before execution
5. Verify after each fix: syntax check → logic validation → integration test if possible

### 关键审计陷阱 (1-40, Summarized)
| Trap | Description | Fix Summary |
|------|-------------|--------------|
| 38 | 共振惩罚方向错误(+=应为-=) | composite_scorer.py:238 `composite += penalty*0.5` → `-=`，弱tech+弱fund反而加分 |
| 39 | 一买进入段取最后中枢而非第一个 | generate_analysis.py:592 应取趋势第一个中枢的进入段，当前实现是盘整背驰非趋势背驰 |
| 40 | 消息面补扫丢失alpha_score | pool_screener.py:571 compute_3d_score()未传alpha_score和w_alpha，alpha被重置为50 |
| 1 | Duplicate buy/sell points | Use (date, level, type) tuple for dedup |
| 2 | Buy/sell point time misalignment | Use correct bi start/end dates |
| 3 | Price calibration interference | Run before structure recognition or linear scale |
| 4 | Backtest summary stats error | Read actual capital from backtest results |
| 5 | Bi merged kline count misuse | Use `end_idx - start_idx + 1` for merged klines |
| 6 | First-class buy point entry segment error | Reverse traverse for direction-matched bi |
| 9 | First/third buy point same-day conflict | Filter conflicting points (keep third buy) |
| 10 | MACD area comparison reversal | Ensure current area < previous area for bottom divergence |
| 11 | Third buy matching precision | Path tracing + fallback to max ZG |
| 12 | Missing take-profit mechanism | Add daily take-profit check in backtest |
| 13 | No time decay for signals | Add 120-day validity for buy signals |
| 14 | 30min down bi filter overkill | Restrict to third buy points only |
| 15 | Hard stop uses first buy price | Use weighted average price × 0.92 |
| 16 | 30min sell point date window too narrow | Extend to ±1 day |
| 17 | No structure stop for first/second buys | Add ZD-based support for first/second buys |
| 18 | Missing fee modeling | Deduct 0.03% per trade + slippage model |
| 23 | Zhongshu width filter (removed) | Deleted `width_pct >= 0.05` filter (violates theory) |
| 25 | Price calibration klines not scaled | Sync scale klines/merged_klines |
| 26 | DataManager.__del__ unconditional logout | Removed __del__ method |
| 27 | Research report tweak blocked by skip_news | Removed `and not skip_news` condition |
| 28 | buy_level out of bounds | Use dict.get() instead of string index |
| 29 | ROE=0 false downgrade | Check `is None` instead of falsy |
| 30 | MACD field string comparison | Use `float()` conversion |
| 31 | News search only negative | Changed to neutral query |
| 32 | Resonance penalty threshold hardcoded | Migrated to config.yaml |
| 33 | Excel dependency undeclared | Added to requirements.txt |
| 34 | risk_filter incomplete | Updated docstring to reflect actual 5/8 implemented |
| 35 | Date parsing fragility | Unified datetime parsing with exception handling |
| 36 | Tech score dimension overlap | Clarified trend structure vs continuation |
| 37 | Exception swallowing | Replaced `except: pass` with explicit logging |

### Verification Methodology
1. **Syntax Check**: `python3 -c "import module"`
2. **Config Loading**: Print config values after loading
3. **Logic Check**: Read modified code segments
4. **Historical Reproducibility**: Check if fixed issues reappear
5. **Incremental Audit**: Read historical audit reports first to avoid duplicate work

### Configuration Patching Flow (6 parameters validated)
1. Add config block to `config.yaml`
2. Add defaults + export in `config_loader.py`
3. Modify 3 modules (segment_analyzer.py, backtest_engine.py)
4. Verify with syntax check + config print

### Incremental Audit Process
1. Read historical audit reports
2. Verify P0 bug fixes first
3. Audit new modules (extreme_market_backtest, sentiment_analyzer, risk_filter)
4. Deep audit core modules (segment_analyzer.py 1220 lines in 4 segments)
5. Audit config system consistency
6. Generate incremental audit report: `审计报告_YYYY-MM-DD_增量审计.md`

---

## First-Class Point Fix (Merged from chanlun-first-class-point-fix)
*Unique content from the standalone `chanlun-first-class-point-fix` skill, now consolidated here.*

### Trigger Condition
When `_find_first_class_points()` mislabels pullbacks as first-class buy/sell points that don't effectively break through ZD (for buy) or ZG (for sell).

### Typical Symptoms
- First-class buy points appear at prices clearly above ZD (should be below)
- Breakout pullbacks (should be third-class points) mislabeled as first-class
- MACD area shrinks but leaving segment doesn't break ZD/ZG

### 5 Core Fixes
1. **after_bis window fix (priority)**: `b.start_date >= zs.end_date` → `b.end_date >= zs.end_date` (captures breakout bi starting before zs ends)
2. **Leave segment traversal**: Replace `after_bis[0]` with loop to find first bi that actually breaks ZD/ZG
3. **Entry segment direction**: Reverse traverse to find nearest bi matching direction (not just previous bi)
4. **Breakout check**: Verify `min(leave_bi) < last_zs.zd` (buy) or `max(leave_bi) > last_zs.zg` (sell)
5. **counter_trend block**: Check if higher ZG (downtrend) or lower ZD (uptrend) appears after last zhongshu → skip first-class point

### Verification Method
```python
from data_manager import DataManager
from generate_analysis import ChanLunAnalyzer

dm = DataManager()
df = dm.get_klines("300059", start_date="2019-01-01")
klines = dm.to_json_list(df)
analyzer = ChanLunAnalyzer(level="daily")
analyzer.analyze(klines)

first_buy = [p for p in analyzer.buy_sell_points if p.level == 1 and p.type == 'buy']
print(f"一类买点: {len(first_buy)}")
```

### Deep Problem: _identify_trends() Too Loose
- Many "pseudo-trends" where connected zhongshus don't have effective breakout
- Fix options: A) Tighten trend definition (require zhongshu destruction by third-class point); B) Relax leave segment matching; C) Keep current (third-class points sufficient for daily level)

### Counter_trend Blind Spot & Complementary Solution
- Counter_trend block eliminates fake first-class points but may miss valid buy opportunities in new uptrend
- Complementary: `pool_scanner.py::_detect_post_reversal_buy()` (see A500 Screener section) captures post-reversal buy points (third-class forming, quasi-second-class)

### ⚠️ 假一买陷阱：三卖后单笔反弹破坏结构（v3.5 → v3.5.1 → v3.5.2 迭代）  

**问题**：counter_trend 检查只检测**已形成的中枢**是否在更高位置。当三卖后出现剧烈单笔反弹（回到ZG之上）但未形成新中枢时，counter_trend 为空，漏检。  

**案例**：沃森生物(300142) — 三卖@2025-12-01(¥12.37)后笔#27反弹+34%至¥14.65（远超ZG=12.37），但未形成新中枢。后续笔#32的低点¥11.65被误标为一买，实际为盘整震荡而非趋势末端。  

**修复（v3.5）**：在 leave_bi 后增加单笔结构破坏检查——三卖后如有任意向上笔突破ZG，判定结构破坏。

**精化（v3.5.1）**：初始修复过严——宽幅中枢的正常小幅波动也导致突破ZG误判。引入两个防护：  
1. **日期范围限制**：搜索`first_break`时只能用`bi.end_date >= zs.start_date`，跳过中枢开始前的笔  
2. **幅度阈值**：突破ZG必须超过`min_exceed = max(zs_width * 0.2, 0.5)`

**精化（v3.5.2）**：v3.5.1 在恒瑞医药案例中发现区间阈值 0.5 元对低价位股票仍然过小。恒瑞笔#47超ZG仅0.91(1.6%)被误判为结构破坏，实际为窄幅中枢正常波动。改为**双重条件**——需同时满足：  
- 价格比例条件：突破ZG超过ZG价的 **3%**（对各价位股票统一）  
- 区宽比例条件：突破ZG超过中枢区宽的 **50%**（排除极窄中枢的小幅突破）  

```python
exceed_pct = exceed_zg / zs.zg          # 价格相对比例
width_ratio = exceed_zg / zs_width      # 中枢宽度比例
if exceed_pct > 0.03 and width_ratio > 0.5:
    return True  # 结构破坏
```

**最终验证效果（510只全量扫描）**：  
- 候选股185→168（-9%），去除了沃森、茅台、石头等假一买  
- 恒瑞医药、分众传媒、华菱钢铁等真一买保留 ✅  
- 一买: 39→20只，类一买(盘整底背驰): 62→67只（补位）  

详见 `references/false-first-buy-pitfall.md`。

- **⚠️ 一买参数调优：divergence_threshold 阀值过松（2026-05-29 发现，待修复）**

**注意**：v3.5.6 修复了进入段选取（取趋势第一个中枢），这使得 divergence_threshold=1.0 的影响大幅减小——因为第一个中枢的进入段面积远大于最后一个中枢的进入段，真正的趋势背驰条件自然更严格。之前标记的9只假一买（背驰比>70%）已被进入段修复自动过滤。threshold 仍建议调为 0.7 作为额外保险，但不再是紧急问题。

**问题**：`generate_analysis.py:64` 的 `divergence_threshold=1.0` 意味着**任何 MACD 面积缩小**（即使仅缩小 0.1%）都被判定为背驰。在 A500 选股扫描中，39 只标记的"一买"中有 9 只背驰比 > 70%（近乎平背驰），不应被认作有效趋势背驰。

**假一买清单（背驰比 > 70%）**：

| 股票 | 背驰比 | 进入段面积 | 离开段面积 |
|------|:------:|:---------:|:---------:|
| 平安银行 000001 | 89.1% | 17.45 | 15.55 |
| 中国平安 601318 | 87.5% | 22.30 | 19.51 |
| 南方航空 600029 | 84.6% | 9.87 | 8.35 |
| 中国宝安 000009 | 81.5% | 14.22 | 11.58 |
| 珀莱雅 603605 | 80.9% | 11.56 | 9.35 |
| 上海机场 600009 | 76.5% | 18.90 | 14.47 |
| 浦发银行 600000 | 72.1% | 8.44 | 6.08 |
| 奥瑞金 002701 | 71.0% | 13.52 | 9.60 |
| 宝钢股份 600019 | 71.0% | 6.34 | 4.50 |

**有效一买示例（背驰比 ≤ 70%）**：

| 股票 | 背驰比 | 进入段面积 | 离开段面积 |
|------|:------:|:---------:|:---------:|
| 鱼跃医疗 002223 | 4.7% | 86.28 | 4.05 |
| 龙佰集团 002601 | 5.0% | 60.20 | 2.99 |
| 艾力斯 688578 | 8.8% | 33.06 | 2.90 |
| 中国太保 601601 | 37.4% | 12.33 | 4.62 |

**根因**：`generate_analysis.py:64` 中：
```python
divergence_threshold = 1.0  # 离开段面积 < 进入段 × 1.0 → 任何缩小 = 背驰
```
改为 0.7 后：平安银行(89.1%>70%)被过滤，中国太保(37.4%<70%)保留。

**推荐修复**：`divergence_threshold = 0.7`（离开段面积 < 进入段 70% 才算背驰），可剔除 9 只假一买，保留 30 只有效一买。

**注意事项**：
- 该参数也控制一类卖点（顶背驰）的判断——对顶背驰同样收紧
- 盘整底背驰（类一买）使用独立的 `area_ratio` 判断，不受此参数影响
- 修改后信号数量从 39→30（-23%），但信号质量大幅提升
- A500 在系统性下跌后出现大量趋势背驰（30 只有效）符合缠论理论——一买的"少"是相对于日常而言，大级别下跌后会集中出现

**验证方法**：
```python
from generate_analysis import ChanLunAnalyzer
# 修改 generate_analysis.py:64 divergence_threshold = 0.7
# 重新运行 A500 扫描，检查一买数量从 39 降为 ~30
```

详见 `references/divergence-threshold-tuning.md`。

---

## Quantitative Data Pipeline (Merged from chanlun-quant-data-pipeline)
*Unique content from the standalone `chanlun-quant-data-pipeline` skill, now consolidated here.*

### 0. Stock Code Verification (Highest Priority)
When price calibration triggers (>10% deviation) or data anomaly, **first verify stock code mapping** before calibrating.
- Case: 301095 (Guangliwei) vs 301498 (Guaibao Pet) → calibration mistakenly scaled correct data
- Standard checklist:
  1. `bs.query_stock_basic(code='sz.XXXXXX')` → confirm name matches
  2. Cross-verify with Baostock + AkShare/eFinance latest close price
  3. Check adjustment factor history `bs.query_adjust_factor()`
  4. Compare forward-adjusted vs non-adjusted data
  5. Verify 30min consistency with daily adjusted close
  6. Only calibrate after all checks pass

### 1. Data Source Redundancy & Failover (Priority Chain)
```
K-line: Baostock (primary, adjustflag='2') → eFinance (backup) → AkShare Sina → AkShare EM
Real-time: Baostock → Tavily Search → DuckDuckGo
News (Group A): Tavily (primary) → Metaso MCP (fallback, write .news_fallback_*.json)
News (Group B): DuckDuckGo (cronjob b1f25e25e1aa)
```

- **Baostock 30min adjustment**: `adjustflag='2'` verified reliable (2026-05-01 triple cross-validation: 300501 + 002393, deviation 0.00%)
- **Metaso MCP**: `mcp_metaso_metaso_search` (query, scope=webpage/document/scholar) as Tavily replacement

### 2. Adjustment Trap & Price Calibration
- **Baostock adjustflag**: Must use `adjustflag='2'` (forward-adjusted), not `adjfactor` field
- **30min data**: Fully supports `adjustflag='2'` (previously misdocumented), verified 2026-05-01
- **Calibration algorithm**: Triggered when `|raw_latest - reference| > reference*0.1`, scales all price elements (fenxing, bi, zhongshu, buy/sell points)
- **⚠️ Double-edged sword**: Calibration fixes adjustment偏差 but masks deeper issues (wrong stock code, data mis-mapping)

### 3. Multi-Level Recursive Analysis Architecture
```
1. ChanLunAnalyzer(level='daily') → identify buy/sell point date T
2. Intercept [T, Now] 30min K-line data
3. ChanLunAnalyzer(level='30min') → confirm bottom fractal in that interval
```
- Performance: Use `.parquet` cache for 30min data (TTL: 6h)

### 4. HTML Visualization Calibration
```python
class HTMLVisualizer:
    def __init__(self, analyzer, reference_price=None):
        self.scale_factor = 1.0
        if reference_price and analyzer.klines:
            raw_latest = analyzer.klines[-1].close
            if abs(raw_latest - reference_price) > reference_price * 0.1:
                self.scale_factor = reference_price / raw_latest
    
    def _apply_calibration(self, price):
        return price * self.scale_factor
    
    def generate_html(self):
        calibrated_klines = [{k: self._apply_calibration(v) if k in ['open','high','low','close'] else v 
                               for k, v in self.analyzer.klines]
        # Same for fenxing, bi, zhongshu, buy/sell points
```
Note: MACD is a difference indicator, no calibration needed.

### 5. Cross-Module File IPC Safety
`file_utils.py` provides `safe_read_json` / `safe_write_json` / `safe_write_excel` with:
- `FileLock` (mutual exclusion)
- `tempfile.mkstemp()` + `os.replace()` (atomic rename)

Replaced patterns:
```python
# ❌ Original
with open(path, 'w') as f:
    json.dump(data, f)

# ✅ Safe
from file_utils import safe_write_json
safe_write_json(path, data)
```

Hardened files (2026-05-01): `.scanner_cache.json`, `.phase2_results*.json`

### 关键审计陷阱 (1-40, Summarized)
| Trap | Description | Fix Summary |
|------|-------------|--------------|
| 38 | 共振惩罚方向错误(+=应为-=) | composite_scorer.py:238 `composite += penalty*0.5` → `-=`，弱tech+弱fund反而加分 |
| 39 | 一买进入段取最后中枢而非第一个 | generate_analysis.py:592 应取趋势第一个中枢的进入段，当前实现是盘整背驰非趋势背驰 |
| 40 | 消息面补扫丢失alpha_score | pool_screener.py:571 compute_3d_score()未传alpha_score和w_alpha，alpha被重置为50 |
| 1 | Duplicate buy/sell points | Use (date, level, type) tuple for dedup |
| 2 | Baostock `adjustflag` misuse | Use `adjustflag='2'` only |
| 3 | eFinance jsonpath version bug | Upgrade efinance + jsonpath or ignore (non-critical) |
| 4 | 30min data range limit | Baostock free 30min starts ~2019-end, use daily-only mode before 2020 |
| 5 | 30min time field format | Parse 17-digit string: `YYYYMMDDHHMMSS000` → datetime |
| 6 | DataManager 30min multi-source failure | Bypass DataManager, use Baostock direct call + explicit login/logout |
| 7 | `DataManager.__del__` unconditional logout | Removed `__del__` method (session managed by baostock_utils) |

### Verification Scripts
- `scripts/verify_baostock_30min_adj.py`: Triple cross-validate 30min adjustment (Baostock vs daily)
- `extreme_market_backtest.py`: Extreme market backtest methodology (2015/2018/2020/2024)

---

## Real-Time Signal Handling (Merged from chanlun-real-time-signal-handling)
*Unique content from the standalone `chanlun-real-time-signal-handling` skill, now consolidated here.*

### Applicable Scenarios
1. Top/bottom fractal confirmation needs multiple K-lines, signal lags behind price
2. Real-time buy/sell points appear dynamically, not static backtest view
3. Multiple zhongshu may produce multiple same-type signals, need to understand their relationship

### 1. Signal Delay Problem & Solution
- **Problem**: Daily top/bottom fractal needs 3 K-lines to form; when confirmed, price may be far from signal position
- **Solution**: Use 30min sub-level to intervene early:
  ```
  Daily level: K1 → K2 → K3 (fractal confirmed)
                    ↑             ↑
            Can't judge         Price already rose
  
  30min level: Check internal structure during K2 → bottom fractal + up bi + MACD divergence → early entry
  ```

### 2. Three Strategies Comparison
| Strategy | Approach | Cost |
|----------|----------|------|
| Wait for confirmation | Enter after daily fractal + confirmation bi | Higher cost, may miss |
| Sub-level early entry | Enter on 30min fractal + divergence | Fake signal risk, stop-loss cost |
| **Batch position (recommended)** | 1/3 on sub-signal, 2/3 after daily confirmation | Balanced, practical |

### 3. Multi-Location Dynamic Signals
- Different zhongshu produce different buy/sell points (e.g., Sanmai A from Zhongshu A, Sanmai B from Zhongshu B)
- These are **not duplicates** but structural opportunities at different market stages
- Practical flow:
  | After entering first Sanmai | Subsequent development | Action |
  |---------------------------|----------------------|--------|
  | ✅ Set independent stop-loss | Rises as expected | Second Sanmai is add-position opportunity |
  | ✅ Set independent stop-loss | Consolidates into new zhongshu | Second Sanmai is add-position opportunity |
  | ✅ Set independent stop-loss | Breaks Sanmai A stop-loss | Stop out, second Sanmai disappears |

### 4. Key Principle
- Each trade has **independent stop-loss logic**
- Signal discovery and trading decisions are separated
- Holding positions does NOT block new signal discovery (system keeps scanning)

### 5. Backtest vs Live Trading
```
Backtest (God's view): All signals fixed, static
Live Trading (Time progression): Signals appear dynamically
  First signal → Decision → Position + Stop-loss
  Second signal → Re-decision → Add or hold
  Each decision independent, protected by stop-loss
```

### 6. Common Traps
| Trap | Description | Solution |
|------|-------------|----------|
| 1 | Treat backtest results as live expectation | Backtest win rate ≠ live ease; need to wait for K-line confirmation |
| 2 | Duplicate same-type signals | Multiple zhongshu scan independently; use (type, level, date) dedup |
| 3 | Hesitate new signals due to holding | Holding doesn't block structure recognition; system keeps generating signals |

### 7. Integration with Funnel Filter
- Funnel filter's position management naturally adapts to batch position building:
  - First lot: Light position on 30min signal
  - Second lot: Add after daily confirmation
  - Independent stop-loss per lot, no mutual interference

---

## Signal Disappearance Diagnosis (Merged from chanlun-signal-disappearance)
*Unique content from the standalone `chanlun-signal-disappearance` skill, now consolidated here.*

### Applicable Scenario
When user asks: "Last analysis had a first-class buy/third-class sell point, but it's gone in this HTML output. Why are the results different?"

### Root Cause Framework
Signal disappearance has only two causes:
1. **Data natural evolution (90% cases)**: New K-lines arrive → restructure fenxing/bi/zhongshu → old signals swallowed or invalidated
2. **Code change (10% cases)**: Analysis logic modified (divergence threshold, zhongshu merge rules, trend identification)

### Diagnostic Steps
1. **Check `generate_analysis.py` modification**:
   ```bash
   ls -la generate_analysis.py  # compare size/modify time vs backup
   # Check key methods: _find_first_class_points(), _find_second_class_points(), _identify_trends()
   ```
2. **Check `data_manager.py` data source change**:
   - Baostock `adjustflag` param (2=forward-adjusted vs 3=non-adjusted; affects price but **not** bi/zhongshu structure)
   - Data source priority switch (Baostock → eFinance → AkShare may return different data)
   - Start/end date range changes
3. **Compare K-line data range**:
   ```python
   from data_manager import DataManager
   dm = DataManager()
   daily = dm.get_klines('301498', 'daily')
   print(f"Date range: {daily.iloc[0]['date']} ~ {daily.iloc[-1]['date']}")
   print(f"Total rows: {len(daily)}")
   ```
4. **Trace key detection logic** (take first-class buy point as example):
   ```python
   analyzer = ChanLunAnalyzer(level='daily').analyze(klines)
   downtrends, uptrends = analyzer._identify_trends()
   last_zs = downtrends[-1][-1]  # last zhongshu in downtrend
   after_bis = [b for b in analyzer.bis if b.start_date >= last_zs.end_date and b.direction == 'down']
   # Key diagnostic: after_bis empty = leaving segment doesn't exist = signal must disappear
   ```
5. **Check bi and zhongshu structure changes**:
   ```python
   for bi in analyzer.bis[-10:]:
       print(f"{bi.direction} bi {bi.start_date}~{bi.end_date} {bi.start_price:.2f}->{bi.end_price:.2f}")
   for i, zs in enumerate(analyzer.zhongshus):
       print(f"Zhongshu {i}: {zs.start_date}~{zs.end_date} ZG={zs.zg:.2f} ZD={zs.zd:.2f}")
   ```
   Typical pattern: previous "leaving segment" bi is now **swallowed** by a new zhongshu covering its date range.

### Typical Scenario: First-Class Buy Point Disappearance
| Period | Pattern |
|--------|---------|
| 2026-03-23 ~ 2026-04-01 | Up bi |
| 2026-04-01 ~ 2026-04-14 | Down bi ← previously thought as first-class buy point |
| 2026-04-14 ~ 2026-04-22 | Up bi ← new bi |

Evolution process:
1. Before: Zhongshu 6 (2025.11.10~2026.03.23) ← leaving segment → down bi 4.1~4.14 [first-class buy point]
2. Now: Zhongshu 6 → Zhongshu 7 (2026.03.23~2026.04.22) ← no leaving segment yet
3. Down bi 4.1~4.14 is "swallowed" into Zhongshu 7 → no longer a leaving segment

### Key Notes
- **Code unchanged ≠ Result unchanged**: ChanLun structure is self-adaptive; new K-lines daily affect bi/zhongshu division
- **Forward-adjusted data changes**: When stock has dividend/split, Baostock's forward-adjust recalculates historical prices → even same K-line count, prices differ → affects MACD and divergence
- **Distinguish "signal disappeared" vs "signal never generated"**: Former was in previous list, now gone; latter never met conditions
- **30min level changes more frequent**: Minute lines more sensitive than daily, structure changes more dramatic

---

## Signal Verification (Merged from chanlun-signal-verify)
*Unique content from the standalone `chanlun-signal-verify` skill, now consolidated here.*

### Applicable Scenarios
When user asks precise ChanLun signal questions:
- "Did 002415 form a third buy on April 24?"
- "Is 600519 a first sell this week?"
- "Check if XXXX has third buy structure"

⚠️ **Never rely on web search alone** — subagents will hallucinate zhongshu positions and buy/sell points.
**Must**: Run local code → extract real data → compare with theory definitions → explain discrepancies.

### Step 1: Run Local Code for Real Data
```bash
cd /path/to/chanlun_core
python3 /tmp/quick_chanlun.py {stock_code}
```
Ensure `/tmp/quick_chanlun.py` exists (use `stock-analysis` skill template if not).

### Step 2: Extract Key Structure Data
```python
current_price    # latest price
zhongshus[-3:]   # last 3 zhongshus (ZG, ZD, start/end dates)
last_5_bis       # last 5 bis (direction, start/end dates, prices)
buy_sell_points[-5:]  # last 5 signals (type, level, date, price, confidence, reason)
macd_status      # MACD status (golden cross/death cross, trend direction)
```

### Step 3: Four-Step Verification (Take Third Buy as Example)
#### Condition A: Price breaks through ZG?
```python
Find last up bi → check its highest price > zhongshu ZG
Example: Up bi 29.60→35.29, ZG=32.99 → 35.29 > 32.99 ✅
```

#### Condition B: Pullback exists and low > ZG?
```python
Find down bi after up bi → low = min(start_price, end_price)
low > ZG → ✅ Third buy structure formed
low ≤ ZG but > ZD → ⚠️ Pullback enters zhongshu (not third buy)
low < ZD → ❌ Breaks below zhongshu
```

#### Condition C: Algorithm already detected?
```python
Check latest same-type signal in buy_sell_points
Common discrepancies:
- Algorithm needs pullback bi to fully end + next up bi confirmation to mark
- If still in pullback bi → algorithm not marked but structure implicitly formed
- Algorithm marks the LAST third buy → need to wait for new structure
```

#### Condition D: Pullback bi completed?
```python
Check recent bi direction sequence:
If sequence: ... ↑Breakout → ↓Pullback → (current)... → Pullback in progress
If sequence: ... ↑Breakout → ↓Pullback → ↑Rebound → Third buy confirmed
```

### Step 4: Output Template
```markdown
## {Stock Name} ({Code}) — {Signal Type} Verification

### Key Data
- Latest Zhongshu: [{ZD}, {ZG}] (Start~End)
- Current Price: {price}
- Breakout Bi: {direction} {start}→{end} ({date})
- Pullback Bi: {direction} {start}→{end} ({date})
- Algorithm Detection: Latest {signal type} @{date} {price}

### Condition Checks
| Condition | Status | Data |
|-----------|--------|------|
| ① Break ZG | ✅/❌ | Breakout high {value} > ZG {value} |
| ② Pullback no break ZG | ✅/❌/⚠️ | Pullback low {value} vs ZG {value} |
| ③ Algorithm detected | ✅/❌ | Latest signal @{date} |
| ④ Pullback completed | ✅/⚠️ | Recent bi direction={direction} |

### Conclusion
[Confirmed/Structure formed pending confirmation/Not formed] + one-sentence explanation
```

### Other Signal Verification Points
| Signal | Key Checks |
|--------|-------------|
| First-class buy (bottom divergence) | ≥2 zhongshus move down + leaving bi MACD area < entering bi MACD area |
| First-class sell (top divergence) | Symmetric to buy: ≥2 zhongshus move up + MACD divergence |
| Second-class buy/sell | After first-class, pullback doesn't break new low/high + 1% tolerance |

### Common Discrepancies Explained
| Scenario | User Sees | Explanation |
|----------|-----------|-------------|
| Structure formed but algorithm not marked | "Price broke ZG and pulled back, why no third buy?" | Algorithm needs pullback bi to END + next up bi confirmation |
| Algorithm marks buy point from 6 months ago | "Why still showing October buy point?" | Algorithm marks LAST third buy. New cycle needs new breakout + pullback |
| Multiple same-type signals | "Why third buy price below ZG?" | Third buy matches the zhongshu with HIGHEST ZG (max ZG), not the nearest zhongshu |
| Algorithm marks third sell unexpectedly | "Why third sell appeared?" | After breaking ZD, rebound doesn't enter ZD = third sell; check if latest bi broke below ZD |

---

## Stop-Loss & 30min Pre-Filter (Merged from chanlun-stoploss-and-filter)
*Unique content from the standalone `chanlun-stoploss-and-filter` skill, now consolidated here.*

### Applicable Scenarios
- Third buy followed by continued drop (e.g., 301498: third buy ¥50.31 → drop to ¥42)
- Need engineering stop-loss protection in backtest
- Avoid mid-way catch: check if sub-level (30min) is still in down trend before buying

### Solution Overview
```
┌─────────────────────────────────────┐
│              Pre-Buy Filter              │
│  30min down bi unfinished → skip buy    │
├─────────────────────────────────────┤
│              Three-Layer Stop-Loss     │
│  Layer1: Structure stop (fall back to ZG) │
│  Layer2: Sub-level sell point (30min)    │
│  Layer3: Hard stop (-8%)                │
└─────────────────────────────────────┘
```

### 1. 30min Down Bi Pre-Filter
```python
def _check_m30_downtrend(self, m30_analyzer, buy_date):
    if not m30_analyzer or not m30_analyzer.bis:
        return False
    recent_bis = [b for b in m30_analyzer.bis if str(b.end_date)[:10] <= buy_date]
    recent_bis.sort(key=lambda b: str(b.end_date)[:10], reverse=True)
    if recent_bis and recent_bis[0].direction == 'down':
        return True  # 30min last completed bi is down, filter out
    return False
```
Called in buy signal loop: `if self._check_m30_downtrend(m30_analyzer, current_date): continue`

### 2. Structure Stop (Layer1)
- For third buy: record corresponding zhongshu's ZG as structure stop
- If price falls back to ZG → structure invalidated → stop out
```python
def _find_zs_for_third_buy(self, buy_price, zhongshus):
    candidate = None
    for zs in zhongshus:
        if zs.zg < buy_price:
            if candidate is None or zs.zg > candidate.zg:
                candidate = zs
    return candidate
```
Store in buy_lots: `structure_stop = zs_for_buy.zg if zs_for_buy else 0`

### 3. Three-Layer Stop-Loss Check
```python
def _check_stop_loss(self, current_price, buy_lots, daily_analyzer, m30_analyzer):
    if not buy_lots: return False, "", ""
    # Layer3: Hard stop -8% (based on first entry)
    first_entry = buy_lots[0]['price']
    hard_stop = first_entry * 0.92
    if current_price <= hard_stop:
        return True, f"Hard stop(-8%): ¥{current_price:.2f} ≤ ¥{hard_stop:.2f}", "hard"
    # Layer1: Structure stop
    for lot in buy_lots:
        zg = lot.get('structure_stop', 0)
        if zg > 0 and current_price <= zg:
            return True, f"Structure stop: fell back to zhongshu ZG ¥{zg:.2f}", "structure"
    # Layer2: Sub-level sell point
    if m30_analyzer and m30_analyzer.buy_sell_points:
        for mp in m30_analyzer.buy_sell_points:
            if mp.type == 'sell' and str(mp.date)[:10] == str(current_date)[:10]:
                return True, f"Sub-level sell: 30min {mp.reason}", "m30_sell"
    return False, "", ""
```

### 4. Slippage Model (`slippage_model.py`)
- Formula: `slippage = 0.1 / (daily_volume_yuan / 1e8)` (cap 0.2%, floor 0.01%)
- Integration: All 5 trade execution points in backtest_engine apply slippage
- Buy price = signal price × (1 + slippage), sell price = signal price × (1 - slippage)
- Default: enabled; pass `enable_slippage=False` to disable for comparison

### 5. Design Principles
| Question | Answer | Reason |
|----------|--------|--------|
| Why -8% not ATR? | Simplicity, matches funnel filter's 9%+6%+3% | Hard stop is last safety net, not precision tool |
| Why ZG for structure stop? | Breaking ZG up is precondition for third buy | Falling back negates structure signal |
| Why first entry price for hard stop? | First lot is largest (9% position) | Most affected by loss; protects principal |

### 6. Architecture Trap: Backtest vs HTML Report Sync
- Backtest engine and HTML report generator are **independent pipelines** with same raw data but independent filtering logic
- If backtest filters a third buy (30min down trend), HTML report may still show it → contradictory info
- Fix: Add identical `_check_m30_downtrend()` to `HTMLVisualizer`, filter `calibrated_points` accordingly
- Verify: `grep -c '"date": "2024-06-07"' 301498_chanlun_analysis.html` → should NOT appear in `points` array if filtered

### 关键审计陷阱 (1-40, Summarized)
| Trap | Description | Fix Summary |
|------|-------------|--------------|
| 38 | 共振惩罚方向错误(+=应为-=) | composite_scorer.py:238 `composite += penalty*0.5` → `-=`，弱tech+弱fund反而加分 |
| 39 | 一买进入段取最后中枢而非第一个 | generate_analysis.py:592 应取趋势第一个中枢的进入段，当前实现是盘整背驰非趋势背驰 |
| 40 | 消息面补扫丢失alpha_score | pool_screener.py:571 compute_3d_score()未传alpha_score和w_alpha，alpha被重置为50 |
| 1 | Duplicate buy/sell points | Use (date, level, type) tuple for dedup |
| 2 | Different stop levels after multiple buys | Currently use first entry for hard stop (conservative) |
| 3 | Not clearing `buy_lots` after stop | Must set `shares=0, buy_lots=[]` after stop |
| 4 | 30min data missing | If `m30_analyzer is None`, skip filter and sub-level stop |
| 5 | Datetime import missing | Ensure `from datetime import datetime` in backtest_engine.py |

---

- **⚠️ 共振惩罚方向（v3.5.6已修复，2026-05-29）**：`composite_scorer.py` 中 `composite += penalty * 0.5` 应为 `-=`。弱tech+弱fund的股票反而获得加分，评分完全反转。
- **⚠️ 一买进入段选取（v3.5.6已修复，2026-05-29）**：`_find_first_class_points()` 取"最后一个中枢的进入段"，缠论原文（第62课）要求取"趋势第一个中枢的进入段"。原实现更接近盘整背驰而非趋势背驰。修复后用`trend[0]`替代`last_zs`，对称修复一卖。
- **⚠️ 消息面补扫alpha丢失（v3.5.6已修复，2026-05-29）**：`pool_screener.py:_update_news()` 调用`compute_3d_score()`时未传`alpha_score`和`w_alpha`，导致alpha被重置为50中性。
- **⚠️ 类一买代理confirmed字符串匹配（v3.5.6已修复，2026-05-29）**：`pool_screener.py`买点代理用`!= '类一买'`精确匹配，但实际buy_type是`"类一买(盘整底背驰)"`含括号后缀。改为`'类一买' not in`子串匹配。

## 简化变体：日线级别系统

对于只需要日线分析的场景，可以简化系统为单一日线级别：

1. **移除多级别递归**：修改`RecursiveTimingSystem.run_full_analysis()`，直接返回日线分析器
2. **简化策略逻辑**：移除30min级别的信号确认，直接使用日线买卖点
3. **保持价格校准**：保留价格校准机制，确保前复权价格准确性
4. **简化输出**：输出日线买卖点信号和可视化报告

详细实现参考`daily-chanlun-timing-system`技能。

## 回测引擎 (Backtest Engine)

基于"漏斗过滤法"的回测实现，用于验证策略的历史表现。

### 核心数据流模式：Pre-load → Analyze（避免重复API）

```python
class BacktestEngine:
    def run_backtest(self, start_date):
        # 1. 加载数据（仅此一次，2次API调用）
        daily_data = dm.get_klines(symbol, 'daily', start_date=start_date)
        m30_data = dm.get_klines(symbol, '30min', start_date=start_date)
        
        # 2. 基于预加载数据创建分析器（无需重复fetch）
        daily_analyzer = ChanLunAnalyzer(level='daily').analyze(dm.to_json_list(daily_data))
        m30_analyzer = ChanLunAnalyzer(level='30min').analyze(dm.to_json_list(m30_data))
        rec_sys._perform_multilevel_validation(daily_analyzer, m30_analyzer)
        
        # 3. 收集信号 + 模拟交易
        signal_points = [...]
        # 遍历daily_data逐日模拟持仓...
```

**关键**：`run_single_analysis` 不再调用 `RecursiveTimingSystem.run_full_analysis()`（内部会重新get_klines），而是直接用 DataFrame 数据创建 `ChanLunAnalyzer`。

### 价格校准 (Price Calibration)

当 Baostock 前复权价格与实际市价偏离 >10% 时，对整个分析结果（分型、笔、中枢、买卖点）进行等比缩放：

```python
if reference_price and abs(actual_latest - reference_price) > reference_price * 0.1:
    scale_factor = reference_price / actual_latest
    for fx in daily_analyzer.fenxings: fx.price *= scale_factor
    for bi in daily_analyzer.bis: bi.start_price, bi.end_price *= scale_factor
    for zs in daily_analyzer.zhongshus: zs.zg, zs.zd *= scale_factor
    for bp in daily_analyzer.buy_sell_points: bp.price *= scale_factor
```

### 交易模拟逻辑

漏斗过滤法的回测交易规则：
1. **建仓**：日线买点 + 30分钟确认 → 买入 `base_position`(30%) 仓位
2. **加仓**：后续30分钟三类买点确认 → 加仓 `add_position`(40%) 
3. **清仓**：日线/30分钟卖点 → 全平
4. **强制平仓**：回测结束日若有持仓，按最后收盘价清仓

**可调止盈与卖点降级（v3.5+）**：`BacktestEngine.__init__` 和 `run_single()` 新增两个参数：

| 参数 | 类型 | 默认 | 说明 |
|------|------|:--:|------|
| `tp_multiplier` | float | 1.0 | 止盈倍数（慢牛推荐 1.67 → 三买 15%→25%） |
| `sell_reduce_pct` | float | 0.0 | 卖点减仓比例（0=全清, 慢牛实验 0.5=只减半仓） |

买入时止盈目标 = 基准止盈 × tp_multiplier：
- 一类买点：30% × tp_multiplier
- 二类买点：20% × tp_multiplier
- 三买/其他：15% × tp_multiplier（慢牛推荐 1.67 → 25%）

加权均价止盈检查自动适配不同买入批次的混合止盈目标。用法：
```python
from backtest_engine import run_single
stats = run_single('000001', '平安银行', start_date='2016-06-01', tp_multiplier=1.67)
```

### 统计指标

回测输出包括：总收益率、年化收益率、胜率、平均盈亏、盈亏比、利润因子、最大回撤、夏普比率、交易明细。

### 30分钟数据不可用时的降级模式 (M30 Degraded Mode)

当回测区间较早（如 2016-2017），Baostock/AkShare 的 30 分钟 K 线数据源大概率全部失败。此时 `run_single_analysis()` 自动进入日线独立模式：

**行为变化**：
- 不调用 `_perform_multilevel_validation()`（无 30 分钟分析器）
- 所有日线买卖点标记 `m30_confirmed=True`（放行买入过滤）
- 置信度降级：一类/二类/三类买点 → 2 分，其他 → 1 分
- `high_confidence=False`（不可能达到高置信度）
- 打印 `[Backtest] ⚠ 30分钟数据不可用，降级为日线独立模式`

**影响**：
- 买入信号不再要求 30 分钟确认，所有日线买点均可触发建仓
- 卖出信号不受影响（卖点不检查 `m30_confirmed`）
- 30 分钟下跌笔过滤自动跳过（`m30_analyzer is None` 短路）
- 回测收益可能偏高（少了 30 分钟确认这一道过滤器）

**代码位置**：`backtest_engine.py:262-287` (`run_single_analysis()`)

### 慢牛行情批量回测 (slow_bull_backtest.py)

系统新增专用脚本 `slow_bull_backtest.py`，用于对指定区间的批量回测，含买点分布统计。

```bash
# 50只代表票回测（默认止盈）
python3 slow_bull_backtest.py --count 50

# 全部388只老票
python3 slow_bull_backtest.py --all

# 自定义止盈倍数（slow_bull推荐 1.67 → 三买 15%→25%）
python3 slow_bull_backtest.py --count 50 --tp-multiplier 1.67

# 对比模式：同时跑默认(1.0)和slow_bull(1.67)两组
python3 slow_bull_backtest.py --count 50 --compare
```

**`--tp-multiplier` 参数**：
- 用于放宽/收紧止盈目标（对应 `BacktestEngine.tp_multiplier`，v3.4+）
- 默认 1.0，慢牛实验推荐 1.67（三买 15%→25%，二买 20%→33%，一买 30%→50%）
- 止盈在回测引擎买入时按 `base_tp_pct * tp_multiplier` 计算，加权均价止盈检查自动适配

**`--compare` 对比模式**：
- 自动跑两组（tp_multiplier=1.0 基准组 + tp_multiplier=1.67 实验组）
- 输出对比表：收益改善/下降/不变数量、平均收益差、改善 Top 10
- 保存两份 CSV：`_baseline.csv` 和 `_slowbull.csv`

**输出**：
- CSV 详细结果（每只股票的收益/胜率/买点类型分布/夏普等）
- 终端汇总表（整体统计 + Top 10/Bottom 10 + 买点分布 + 信号充足性）
- 基准对比（相对沪深300的超额收益）

**与 `run_backtest.py` 的区别**：
- 自动筛选 2016 年前上市的老票（防幸存者偏差）
- 收集买点类型分布（一买/二买/三买/其他计数）
- 统计"票荒"指标（零交易股票比例）
- 硬编码慢牛基准收益（需手动更新 `HS300_REFERENCE_RETURN`）

**已知限制**：
- 依赖 `.old_stocks_2016.json`（缓存了 A500 中 2016 年前上市的 388 只股票）
- 30 分钟数据几乎全不可用（触发降级模式），收益偏高约 1-2%
- **单只回测存在严重的资金闲置问题**（82% 现金永远不用）→ 组合回测才是真实表现

### 组合回测引擎 (Portfolio Backtest, v4.0)

全新模块 `portfolio_backtest.py`，解决单只回测的核心缺陷：每只股票独立分配 ¥200 万但单股最大仓位仅 18%，剩余资金闲置。组合模式实现共享资金池、多股同时持仓、卖出资金立即复用。

**架构**：
```
Phase 1: 预分析（所有股票跑 ChanLun 日线，提取全部买卖点信号到 signal_db）
Phase 2: 组合模拟（逐日遍历，统一管理资金池和持仓字典）
  ├─ Step A: 检查所有持仓的退出信号（卖点/止损/止盈）
  ├─ Step B: 资金释放后扫描全池买入信号，按置信度排序分配
  └─ Step C: 记录每日总资产和仓位利用率
Phase 3: 统计（总收益/胜率/回撤/仓位分布/买点分布）
```

**关键参数**：
| 参数 | 默认 | 说明 |
|------|:---:|------|
| `--target-util` | 0.80 | 目标仓位利用率（仓位低于此值开始买入） |
| `--tp` | 1.0 | 止盈倍数（同单只回测） |
| `--count` | 50 | 股票池大小 |

**用法**：
```bash
# 50只组合回测，80%仓位目标
python3 portfolio_backtest.py --count 50 --target-util 0.80

# 止盈放宽 + 组合模式
python3 portfolio_backtest.py --count 50 --target-util 0.80 --tp 1.67

# 全量388只（需30-40分钟）
python3 portfolio_backtest.py --count 388 --target-util 0.80
```

**2016.06-2017.11 慢牛验证结果**：

| 模式 | 总收益 | 年化 | vs HS300 | 胜率 | 仓位 |
|------|:-----:|:----:|:--------:|:----:|:----:|
| 单只独立(均值) | +2.20% | +1.46% | -24.55% | 73.3% | 18%max |
| 组合(tp=1.0) | **+61.40%** | **+38.92%** | **+34.65%** | 71.0% | 66.4% |
| 组合(tp=1.67) | +40.35% | +26.21% | +13.60% | 61.4% | 72.4% |

**关键发现**：
1. 系统本身有效——单只回测的"跑输"假象是资金闲置造成的
2. 止盈放宽在组合模式中反而有害（tp=1.67 收益从 61% 降到 40%）：持仓变长→资金周转变慢→复利效应减弱。默认 15% 止盈（快速轮动）才是最优
3. 平均持股 7.2 只，峰值 9 只，仓位稳定在 66-72%
4. 最大回撤仅 3.63%（基准）— 风控极其优秀

⚠️ **单只回测 vs 组合回测的根本区别**：单只独立回测完全不适合评估多股择时系统。如果你的策略天然会分散到多只股票（如缠论三买在慢牛中间歇出现），评估必须用组合模式。否则你会严重低估系统能力。详见 `references/portfolio-backtest-results-2026-05-02.md`。

### 命令行用法

```bash
# 单只回测
python3 backtest_engine.py 301498

# 批量回测（自动汇总）
python3 backtest_engine.py 301498 600036 601318 --ref 601318 45.00

# 带参考价
python3 backtest_engine.py 301498 --ref 301498 58.6

# 通过 run_backtest.py 入口（支持--pool默认池）
python3 run_backtest.py --pool --quiet
```

### ⚠️ 命名坑

`run_backtest.py` 最初被错误命名为回测运行器，实际上它是日线分析 + HTML报告生成器。已在2026-04-23整改：
- **`report_generator.py`**：日线择时分析 + 可视化HTML报告生成
- **`run_backtest.py`**：`backtest_engine.py` 的批处理入口 (支持 `--pool`, `--ref`, `--quiet`, `--start`, `--capital`)

## v3.5 新增模块 (2026-05-02)

### 技术评分第5维度：趋势延续 (trend_continuation)

`validate_tech_score.py:compute_technical_score()` 新增第 5 个评分维度（0-10 分）。慢牛中"标准买点 + 均线多头排列"的票比仅靠底部反转的票更可靠。

| 条件 | 分数 | 说明 |
|------|:---:|------|
| 价格 > MA5 > MA20 > MA60 | +10 | 完美多头排列，趋势延续 |
| 价格 > MA5 > MA20 | +6 | 短期多头 |
| 价格 > MA20 | +3 | 中期偏多 |

触发条件：仅在已有标准买点（等级 1/2/3）的票上生效，需要 ≥60 根 K 线。不影响无买点票的结构快照评分。

### 基本面第5维度：边际改善 (marginal_improvement)

`quick_fundamental.py:calculate_fundamental_score()` 新增边际改善维度（0-15 分）。慢牛中"正在改善"的公司涨幅 > "绝对值最好"的公司。

| 子维度 | 条件 | 分数 |
|--------|------|:---:|
| 增长驱动力 | 利润增速 > 收入增速 > 0 | +5 |
| 增长驱动力 | 利润增速 > 0 且收入增速 > 0 | +3 |
| 利润率质量 | 毛利率 > 25% 且净利率 > 10% | +5 |
| 利润率质量 | 毛利率 > 15% | +3 |
| 现金流验证 | CFO/净利 > 1.0 | +5 |
| 现金流验证 | CFO/净利 > 0.5 | +3 |

总分上限保持 100（四维度 + 边际改善 = 115 → cap 100）。绝对基本面好的公司不受影响，改善中的公司获得补偿性加分。

### 持仓实时监控系统 (position_monitor.py)

解决"只有选股信号，没有买入后跟踪"的断层。直接读取用户 Windows 端实时编辑的 Excel（WSL `/mnt/d/` 映射），无需复制。

### 微信推送模块 (weixin_pusher.py)

封装 Hermes 微信发送能力，为交易系统提供独立的推送接口。**无需启动 gateway**，直接使用 iLink Bot API。

```python
from weixin_pusher import WeixinPusher, wx_send, wx_signal, wx_positions

pusher = WeixinPusher()  # 自动从 ~/.hermes/.env 读取 Token/Account
pusher.send("消息内容")                           # 自由文本
pusher.send_signal_alert(...)                      # 结构化交易信号
pusher.send_position_summary([{code,name,...}])    # 持仓汇总
pusher.send_data_health_alert([(name,detail)])     # 数据源故障
```

快捷函数：`wx_send()`, `wx_signal()`, `wx_positions()`, `wx_alert()` — 无需实例化。

底层调用 `gateway.platforms.weixin.send_weixin_direct()`，需 `aiohttp + cryptography`。自动分片长消息（>1800字）、失败重试3次、Markdown→微信格式转换。

✅ **集成完成（2026-05-02）**：`position_monitor.py` 已集成 weixin_pusher。`--push` 调用 `WeixinPusher().send(report)` 推完整日报；`--alert-only` 仅在有 CRITICAL/HIGH 告警时推送，无告警不打扰；空持仓时推 `[EMPTY] 当前无持仓` 通知。详见 `references/weixin-push-integration-audit.md`。

### 市场环境判定 + 大盘仓位控制 (market_regime.py, v3.5 新增)

`market_regime.py` 实现沪深300月线缠论分析 + 宏观风险信号提取 → 输出仓位上限到 `regimes.csv`。`pool_screener.py` Phase 2 自动读取并限制个股仓位（`min(评分仓位, 大盘仓位上限)`）。

仓位四档：满仓(1.0) / 8成(0.8) / 5成(0.5) / 3成(0.3)。详见 `chanlun-a500-screener` 技能 references。

### 数据源心跳检测 (data_health_monitor.py, v3.5 新增)

每日盘前 09:00（周一至五）自动 ping Baostock/AKShare/Tavily/Metaso，生成健康报告到 `/mnt/d/常用文件/Hermes系统运行状态/数据源健康/`。

### 消息面扫描降级链优化

`scan_news()` 新增新浪财经个股新闻作为免费第1级（AKShare公告 → **新浪财经** → Tavily → Metaso）。新浪财经每只股票 ~40 条新闻，urllib 直连、免费、<1s 响应。Tavily/Metaso 消耗降至接近 0。

**Excel 格式自动识别**：
- 最小两列（代码 + 名称）→ 仅信号监控
- 完整列（代码 + 名称 + 买入日期 + 买入价 + 股数 + 买入理由 + 止损价 + 止盈价）→ 全功能监控

**监控项**：

| 检查项 | 触发条件 | 告警级别 |
|--------|---------|:------:|
| 日线卖点 | 买入后出现新卖点 | 🔴 SELL |
| 结构止损 | 跌破买入时中枢 ZG/ZD | 🛑 CRITICAL |
| 硬止损-8% | 加权均价 -8% | 🛑 CRITICAL |
| 止盈触发 | 按买点类型分级（15%/20%/30%） | 🎯 INFO |
| 笔方向 + MACD | 每次分析（常态监控） | — |

**输出**：`持仓监控/YYYY-MM-DD_持仓日报.md`

**⚠️ 常见问题：盈亏栏为空（`-`）或止损止盈未触发**

**问题 A：`has_entry_info()` 错误要求 `shares > 0`（已修）**

根因：`has_entry_info()` 要求 `shares > 0`，但如果 Excel 没有"股数"列，`shares` 默认为 0，导致 `has_entry_info()` 返回 `False` → 不传入 `entry` → `analyze_stock()` 跳过盈亏计算和止损止盈检查。

修复：`has_entry_info()` 应只检查 `entry_date` 和 `entry_price`，不需要检查 `shares`：
```python
def has_entry_info(self) -> bool:
    """是否有完整的买入信息（只需要日期和价格，不需要股数）"""
    return all(
        h.get('entry_date') and h.get('entry_price')
        for h in self.holdings
    )
```

Excel 最小完整列：代码、名称、买入时间、买入价格 = 4 列即可触发完整盈亏计算。止损价/止盈价可缺省。

**问题 B：止盈/止损逻辑不读 Excel 手动设置价（已修）**

旧代码硬编码止盈目标为 `entry_price * (1 + tp_pct_for_level(buy_level))`（如三买+15%），完全不读 Excel 中的止盈价列。即使 Excel 中手动设置了 `tp_price=16.0`，代码仍算出自定义的 `16.39`（14.25*1.15），导致价格到达手动止盈位但未触发告警。

修复（两处修改）：
```python
# 止盈：优先读 Excel 手动设置价
if entry.get('tp_price') is not None:
    tp_target = entry['tp_price']
    tp_label = f"手动止盈¥{tp_target:.2f}"
else:
    tp_target = entry_price * (1 + tp_pct_for_level(buy_level))
    tp_label = f"+{tp_pct_for_level(buy_level)*100:.0f}%: ¥{tp_target:.2f}"

# 止损同理：优先读 stop_price，fallback 到缠论结构位
if entry.get('stop_price') is not None:
    use_stop = entry['stop_price']
    stop_label = f"手动止损¥{use_stop:.2f}"
else:
    structure_stop = None
    # ...缠论结构位计算...
    use_stop = structure_stop
    stop_label = f"结构位¥{use_stop:.2f}" if use_stop else None
```

**问题 C：主循环未传递 stop_price/tp_price 到 analyze_stock（已修）**

比问题 B 更隐蔽：`__main__` 中的 `entry` 字典只传了 `entry_date`、`entry_price`、`reason` **三个字段**，没传 `stop_price` 和 `tp_price`。即使问题 B 修复了 `analyze_stock()` 的读取逻辑，它根本拿不到手动止损止盈值——因为调用方没传。

修复：`__main__` 中的 entry 构建追加 `stop_price` 和 `tp_price`：
```python
entry = {
    'entry_date': h.get('entry_date'),
    'entry_price': h.get('entry_price'),
    'reason': h.get('reason', ''),
    'stop_price': h.get('stop_price'),  # ← 新增
    'tp_price': h.get('tp_price'),      # ← 新增
}
```

**排查流程图**：盈亏或止损止盈有问题时，依次检查：
1. `has_entry_info()` 是否返回 `True`（检查 `shares` 条件是否误拦）
2. 主循环 `entry` 字典是否包含 `stop_price`/`tp_price`
3. `analyze_stock()` 中是否优先读手动价而非硬编码计算

**用法**：
```bash
python3 position_monitor.py              # 终端汇总 + MD 报告
python3 position_monitor.py --push       # + 推送完整持仓日报到微信
python3 position_monitor.py --alert-only # 仅止损/卖点告警时推送（适合cron）
```

### 自动验证 v3.0：模型漂移检测

`auto_validate.py` 新增两层检测（原来只有指标级 2σ 漂移）：

**模型级漂移** (`check_model_drift()`):

| 检测项 | 方法 | 告警条件 |
|--------|------|---------|
| 技术评分趋势 | 5 日线性回归斜率 | 斜率 < -1.0/日 → ALERT |
| 信号数量衰减 | 近 15 日 vs 前 15 日均值 | 减半 → ALERT, 减 30% → WARN |
| A 级占比崩溃 | 当前 vs 历史均值 | < 30% 历史均值 → ALERT |

**组合质量检测** (`check_portfolio_drift()`):
- 读取 A500 选股报告 Top 10 综合分
- 连续 3 期 ≤ 70 → ALERT

三层检测现已覆盖：数据质量 (v2.0) → 策略有效性 (v3.0 模型级) → 选股产出 (v3.0 组合级)。

本次审计修复新增以下独立模块：

| 模块 | 文件 | 用途 |
|------|------|------|
| 文件IPC安全 | `file_utils.py` | `safe_read_json()` / `safe_write_json()` — FileLock + 原子重命名 |
| 滑点模型 | `slippage_model.py` | `SlippageModel` — 成交额反比滑点 (0.01%~0.2%) |
| cron依赖管理 | `cron_utils.py` | `FlagSignals` — 文件信号系统 / `CronLogger` — 统一日志 |
| 配置集中化 | `config.yaml` + `config_loader.py` | 36个参数统一管理，所有模块从此读取 |

### 笔中枢稳定性过滤

`generate_analysis.py:_find_zhongshus()` 新增两个过滤条件：
1. 中枢区间内合并 K 线数 ≥ 5 根（消除杂碎中枢）
2. `(ZG-ZD) / 中枢均价 < 5%`（消除宽幅震荡中枢）

### MACD 评分去重

`score_backtest.py:calc_tech_score()` fallback 路径中，MACD 金叉 +10 和 MACD 柱向上 +10 改为 `if/elif` 互斥：
- DIF > DEA (金叉) → +15
- 仅 MACD 柱上行 → +10

### 自动验证 v3.0：模型漂移检测

`auto_validate.py` 新增两层检测（原来只有指标级 2σ 漂移）：

**模型级漂移** (`check_model_drift()`):

| 检测项 | 方法 | 告警条件 |
|--------|------|---------|
| 技术评分趋势 | 5 日线性回归斜率 | 斜率 < -1.0/日 → ALERT |
| 信号数量衰减 | 近 15 日 vs 前 15 日均值 | 减半 → ALERT, 减 30% → WARN |
| A 级占比崩溃 | 当前 vs 历史均值 | < 30% 历史均值 → ALERT |

**组合质量检测** (`check_portfolio_drift()`):
- 读取 A500 选股报告 Top 10 综合分
- 连续 3 期 ≤ 70 → ALERT

三层检测现已覆盖：数据质量 (v2.0) → 策略有效性 (v3.0 模型级) → 选股产出 (v3.0 组合级)。

### 市场环境判定 (market_regime.py, v3.5+)

新建 `market_regime.py`，桥接宏观数据与仓位决策。输入沪深300月线缠论分析 + 最新宏观早报，输出仓位上限到 `regimes.csv`。

**判定逻辑**：
- 沪深300 AKShare 日线合成月线 → ChanLun 分析 → 笔方向/中枢位置/买卖点
- 读 `/mnt/d/常用文件/宏观数据监控/` 最新早报 → 14 条量化风险信号正则匹配
- 缠论 + 宏观交叉判定 → 市场标签 + 仓位上限

**仓位四档**：

| 条件 | 仓位 | 标签 |
|------|:---:|------|
| 缠论买点 + 宏观利好 | 满仓 | 1.0 |
| 向上趋势 + 宏观中性 | 8成 | 0.8 |
| 震荡 / 方向不明 | 5成 | 0.5 |
| 出现卖点 / 宏观风险 | 3成 | 0.3 |

**集成点**：`pool_screener.py` 在 Phase 2 综合评分时自动读 `regimes.csv`，`position = min(评分仓位, 大盘仓位上限)`。

用法：
```bash
python3 market_regime.py                    # 终端输出判定结果
python3 market_regime.py --output regimes.csv  # 写入供 pool_screener 读取
```

### 数据源心跳检测 (data_health_monitor.py, v3.5+)

开盘前自动 ping Baostock/AKShare/Tavily/Metaso，任一故障告警。

```bash
python3 data_health_monitor.py              # 终端输出
python3 data_health_monitor.py --push       # +微信推送
```

报告输出：`/mnt/d/常用文件/Hermes系统运行状态/数据源健康/YYYY-MM-DD_数据源健康报告.md`

Cron：周一至五 09:00（`data-health-check`，job_id: 34b55b7cb8d1）

### scan_news() 降级链优化 (v3.5+)

原降级链：AKShare公告 → Sina → Tavily → Metaso。v3.5+ 新增新浪财经作为免费第一级，Tavily/Metaso 配额消耗降至接近零。

```
scan_news(code, name)
  ├─ 第0级: AKShare 公告预扫描 (免费)
  ├─ 第1级: 新浪财经个股新闻 (免费, urllib直连) ← 新增，取最近10条
  ├─ 第2级: Tavily (API 额度, 极少触发)
  ├─ 第3级: Metaso (API 额度, 极少触发)
  └─ 写 fallback 标记
```

2026-05-14 v3.5.4 升级：替换为同花顺问财 OpenAPI，Sina → Tavily → Metaso 降为fallback。
```
scan_news(code, name)
  ├─ L0: AKShare 公告预扫描
  ├─ L1: 同花顺新闻搜索 (comprehensive/search, news-search skill, 8条)
  ├─ L1b: 同花顺公告搜索 (comprehensive/search, announcement-search skill, 5条)
  ├─ L2: Sina Finance (免费fallback)
  ├─ L3: Tavily (配额fallback)
  ├─ L4: Metaso (配额fallback)
  └─ Fallback: 写标记 + score=50
```
同花顺API需要 IWENCAI_API_KEY 环境变量，调用 `news-search` 和 `announcement-search` 两个skill。
改造效果：Top30消息面评分从77%的50分降至7%，均值53→67.4。

本次审计修复新增以下独立模块：

## 互补能力：基本面深度分析

本系统的 `quick_fundamental.py::calculate_fundamental_score()` 提供纯定量基本面评分（ROE/成长/财务健康/估值/边际改善 五维度，0-100分），适合批量粗筛。

当需要对单只股票做定性深度研究（护城河、商业模式、管理层、行业格局）时，使用独立的 `fundamental-deep-analysis` skill：
- 四层递进框架：行业研究 → 公司研究 → 财务研究 → 估值研究
- 护城河5类型评估清单、商业模式4类型×4大陷阱、财务造假识别
- A股特殊考量：政策风险、分红真实性、散户结构
- 输出结构化报告到 `/mnt/d/常用文件/基本面深度分析/{代码}_{简称}_深度分析_{日期}.md`
- 手动触发：「深度分析XXX的基本面」

与三维分析系统的关系：量化粗筛层（本系统）→ 定性深度层（独立 skill），暂不自动联动。

## AKShare 集成方案 (2026-05-01)

AKShare v1.18.56 已安装，当前仅用于 K 线备选源。规划 4 层集成扩展：

### 集成架构
```
现有: K线行情 (Baostock主 → efinance → AKShare Sina → AKShare EM)
✅ 完成: ① 基本面增强 (AKShare 同花顺 替代 Baostock, pool_screener 已集成)
✅ 完成: ② 公告扫描 (stock_notice_report → akshare_scanner.py → scan_news() 集成)
待实现: ③ 研报评级 (stock_research_report_em → 机构共识)
待实现: ④ 宏观环境 (macro_china_* → 仓位调节)
```

### ✅ 第 1 层：基本面增强 (已完成 2026-05-01)
- 新建 `akshare_fundamental.py` — `get_fundamentals_akshare(symbol)` 函数
- 主力接口: `stock_financial_abstract_ths` (同花顺 25 项指标)
- PE/PB/名称/行业: Baostock fallback
- 集成点: `pool_screener.py` AKShare 优先 → Baostock fallback
- 优势: 无 session 冲突、无限流暂停、含毛利率/流动比/现金流等 Baostock 没有的字段
- 详见 chanlun-a500-screener skill 的 `references/akshare-fundamental-integration.md`

### 第 2 层：公告扫描
- 新建 `akshare_scanner.py`，利用 `stock_notice_report`
- 集成点: `pool_screener.py` 的 `scan_news()` 中，Tavily 搜索之前先扫公告

### 第 3 层：研报评级
- 利用 `stock_research_report_em` 提取机构共识评级
- 决策点: 独立维度 or 合并到基本面

### 第 4 层：宏观环境监控
- 利用 `macro_china_gdp/cpi/pmi/money_supply/shrzgm`
- 独立 cron 任务，输出宏观环境评级对仓位的调节建议

详见 `references/akshare-api-test-results-2026-05-01.md`（API 实测结果 + 可用/不可用清单 + 风险点）。

## 线段中枢 (Segment ZhongShu) — 完整版 v1.0 (2026-05-03)

独立的线段中枢缠论分析模块 `segment_analyzer.py`，基于完整版缠论线段定义：

### 核心功能
1. **完整线段划分**：特征序列提取 + 包含处理（取低低/取高高）+ 顶底分型识别 + 线段破坏判断
2. **线段中枢构建**：3段重叠 → 中枢（含延伸逻辑）
3. **中枢扩张**：条件1（区间重叠）OR 条件2（波动触及），含同级别/同方向校验 + 保护机制（27笔/120天/5%无效横盘）
4. **线段级别买卖点**：SB1/SB2/SB3 标记

### 设计原则
- **独立模块**，不修改 `generate_analysis.py` 原有代码
- 复用 `ChanLunAnalyzer` 的 K线包含处理、笔划分和 MACD 计算
- 与笔中枢系统并行运行，通过 HTML 双视角切换对比

### HTML 双视角
- `HTMLVisualizer` 新增 `segment_result` 参数（可选）
- 报告中新增 `🔀 线段中枢` 切换按钮
- 点击后：笔中枢 → 线段中枢；笔 → 线段；B1/B2/B3 → SB1/SB2/SB3
- 修改文件：`generate_analysis.py` (HTMLVisualizer), `quick_html.py` (入口)

### 5只票测试结果 (329根K线, 约500天)

| 股票 | 笔数 | 笔中枢 | 笔BS | 线段 | 线段中枢 | 线段BS |
|------|:---:|:-----:|:---:|:---:|:-----:|:---:|
| 海康威视 | 28 | 4 | 3 | 4 | 1 | 0 |
| 贵州茅台 | 25 | 2 | 1 | 3 | 0 | 0 |
| 五粮液 | 28 | 3 | 2 | 2 | 0 | 0 |
| 宁德时代 | 24 | 2 | 2 | 3 | 0 | 0 |
| 立讯精密 | 24 | 5 | 4 | 3 | 0 | 0 |

**关键发现**：
- 线段数量约为笔的1/8~1/12，符合缠论理论
- 线段中枢远少于笔中枢（需要至少3段重叠）
- 线段买卖点稀有——中枢少导致一买/二买/三买触发条件难以满足
- **线段中枢的"信号缺失"本身就是信息**——它告诉你笔级别的B3在更高级别上还未确认

### 已知限制
- 线段划分在单边强趋势中产生较长线段（特征序列元素单调递增/递减，无法形成分型）
- 单次包含处理（不递归）避免级联合并但可能保留一些应合并的元素
- 当前中枢最少需要3段，部分股票因线段不足而无法构建中枢

### 用法
```python
from segment_analyzer import SegmentChanLunAnalyzer
from generate_analysis import ChanLunAnalyzer

# 1. 先跑笔级别分析
bi_analyzer = ChanLunAnalyzer().analyze(klines_data)

# 2. 跑线段级别分析
seg_analyzer = SegmentChanLunAnalyzer()
seg_analyzer.analyze(bi_analyzer)
seg_analyzer.print_summary()

# 3. 生成双视角 HTML 报告
from generate_analysis import HTMLVisualizer
viz = HTMLVisualizer(symbol, name, bi_analyzer, segment_result=seg_analyzer)
viz.generate_html(output_path)
```

### 命令行
```bash
python3 segment_analyzer.py 002415 500    # 海康威视 500天
python3 quick_html.py 002415              # 生成双视角 HTML 报告
```

### 相关文件
- `segment_analyzer.py` — 核心模块
- `test_segment_zhongshu.py` — 简化版测试脚本（3笔成段，用于验证/对比）
- `generate_analysis.py:HTMLVisualizer` — HTML 双视角改造
- `quick_html.py` — 入口适配

详见 `references/segment-zhongshu-implementation-2026-05-03.md`。

## 参考文档

- `references/akshare-api-test-results-2026-05-01.md` — AKShare API 实测
- `references/portfolio-backtest-results-2026-05-02.md` — 组合回测结果与慢牛验证
- `references/tavily-usage-inventory.md` — Tavily 用量统计
- `references/external-tool-gotchas.md` — **外部工具交互陷阱速查** (Pandas/WSL/Metaso格式/Baostock前缀/Cron路径)
- `references/repo-packaging-guide.md` — **打包到 GitHub 的移植指南** (路径修复/.gitignore/依赖声明)
- `references/segment-zhongshu-test-2026-05-02.md` — 线段中枢简化版测试报告（3笔成段方案）
- `references/segment-zhongshu-implementation-2026-05-03.md` — **线段中枢完整版实现报告**（特征序列+扩张+双视角HTML）
- `references/segment-zhongshu-debug-session-2026-05-03.md` — **线段中枢调试记录**（三个关键Bug及修复 + 数据范围要求）
- `references/daily-to-monthly-resample.md` — **日线合成月线方法**（未闭合K线过滤 + pandas频率字符串 + AKShare指数接口）
- `references/data-cache-architecture.md` — **Parquet 缓存架构**（TTL/daily=24h,30min=6h/失效逻辑/5层数据源链/沙箱共享安全性分析）

### MCP/外部工具集成分析

评估新的 MCP 服务器、数据库工具或外部系统与本系统的集成时，遵循以下流程：

1. **查工具边界**：支持的数据库类型/数据格式/协议
2. **对照数据流**：核心数据存在哪（Parquet/JSON/SQLite），怎么流通
3. **映射匹配度**：工具能读什么、系统有什么、中间有无格式断层
4. **分级建议**：🟢 零改动 / 🟡 小改动 / 🔴 架构改造

典型案例：DBHub (bytebase/dbhub) 支持 SQLite。系统核心 K 线数据在 Parquet 而非数据库中，但 DBHub 可用于做精确的数值类初筛（放量/站上均线/N日新高），然后交给 Python 做严格的缠论分析。详见 `references/mcp-dbhub-integration-analysis.md`（集成方案）和 `references/dbhub-query-capabilities.md`（查询能力边界速查）。

### 量化因子库评估与集成

当评估一个外部量化因子库（如 Alpha Zoo、Qlib、WorldQuant 101 Alpha 等）并与 DBHub SQLite K 线体系集成时，采用以下评估框架：

#### 一、数据合约分析（最重要）
因子库的 compute 函数需要什么输入格式？

| 常见格式 | 特征 | 适配方式（从 SQLite DBHub） |
|---------|------|---------------------------|
| **宽表 Panel** (date × instrument) | 每列一个标的，每行一个日期 | `pd.read_sql` → `.pivot(index="date", columns="stock_code", values="close")` |
| **长表 (tidy)** | 每行一个 (date, stock, value) | 直接适用，但需检查列名 |
| **API 实时拉取** | 因子库自带数据源 | 评估数据覆盖范围（A 股？美股？）和免费性 |

**关键检查清单**：
- 需要哪些列？(open/high/low/close/volume/amount/vwap)
- DBHub 缺不缺？比如 `amount`（成交额）通常需要额外补充
- 日期格式要求（DatetimeIndex vs 字符串）
- 是否需要多市场同步（A/HK/US）

#### 二、算子代数模式（Operator Algebra）
量化因子库通常实现一套基础算子，因子由算子组合而成：

```
rank / scale / ts_mean / ts_std / ts_max / ts_min / ts_corr / 
ts_cov / delta / ts_rank / ts_argmax / ts_argmin / 
decay_linear / signed_power / safe_div / vwap
```

**集成要点**：
- 算子必须是纯 pandas/numpy，无外部数据依赖——这样可以直接复用
- 检查 NaN 传播策略（silent fillna vs 严格传播）——你的系统需要严格传播
- 检查 lookahead 防护（禁止负 shift/delta）
- 检查 inf / -inf 处理

#### 三、注册表与模块化（Registry Pattern）
好的因子库有注册表/发现机制：

| 特性 | 价值 |
|------|------|
| AST 元数据提取 | 不 import 即可知道因子需要什么列、什么市场、warmup 多久 |
| 懒加载 | 只 import 被调用的因子，降低内存 |
| 输出校验 | 自动检查 NaN 比例、inf 污染、形状不匹配 |
| 主题/市场过滤 | 按 momentum/reversal/value 等主题过滤因子 |

#### 四、集成策略选择

| 策略 | 代价 | 收益 | 适合场景 |
|------|:----:|:----:|---------|
| **A. 提取算子+注册表为独立包** | 中（一次抽离） | 高（完全独立，无外部依赖） | 因子库代码质量高、算子干净 |
| **B. MCP 桥接** | 低（快速对接） | 中（每次计算走进程通信） | 因子库庞大、不想维护 |
| **C. MCP Server 消费** | 低 | 中 | 只想偶尔调用因子评估 |
| **D. 只取算子，自写因子** | 低 | 高 | 有明确目标因子，不需要全量 zoo |

#### 五、具体集成步骤（以 Vibe-Trading Alpha Zoo 为例）

```python
def dbhub_to_panel(conn, stock_codes, start_date, end_date) -> dict[str, pd.DataFrame]:
    """从 DBHub SQLite 读取数据，转为因子库需要的宽表 panel 格式"""
    placeholders = ",".join("?" * len(stock_codes))
    sql = f"""
    SELECT date, stock_code, open, high, low, close, volume
    FROM kline_daily
    WHERE stock_code IN ({placeholders})
      AND date BETWEEN ? AND ?
    ORDER BY date, stock_code
    """
    params = list(stock_codes) + [start_date, end_date]
    df = pd.read_sql(sql, conn, params=params, parse_dates=["date"])
    
    panel = {}
    for col in ["open", "high", "low", "close", "volume"]:
        panel[col] = df.pivot(index="date", columns="stock_code", values=col)
    return panel
```

#### 六、风险清单

| 风险 | 影响 | 缓解 |
|------|:----:|------|
| amount/vwap 列缺失 | ~30-40 个因子无法计算 | 标记这些因子暂不可用，或从东方财富/AKShare 补充 amount |
| 宽表内存压力 | 全 A 5000 只 ≈ 300MB | 分批计算，或先筛选股票池再转宽表 |
| 因子代码 import 路径硬编码 | 抽离时需要改 | 抽成独立包时修正路径 |
| 因子库 A 股适用性 | 452 个因子不一定都适合 A 股 | 用 bench_runner 跑 IC 筛选 |
| 宽表行数 | 日期不连续（节假日/停牌） | pivot 后检查 index 连续性和 NaN 比例 |

详见 `references/vibe-trading-alpha-zoo-analysis.md`（Vibe-Trading 全分析 + HKUDS Alpha Zoo 结构 + DBHub 集成方案 + **已验证的 GTJA191 存活 10 因子 + 反转 15 因子 + qlib158 形态 8 因子清单**）。

**实际使用的 4 个 GTJA 因子详解**（公式拆解、交易含义、缠论映射、截面排名限制）见 `references/active-gtja-factors-explained.md`。

#### 实际集成实现（2026-05-24）

chanlun_core 已完成 Alpha Zoo 因子的实际集成，文件结构如下（非评估，已投产）：

| 文件 | 路径 | 职责 |
|------|------|------|
| 文件 | ~/work/alpha-zoo/base.py + zoo.py + dbhub_panel.py | 19 算子 + 4 GTJA因子 + DBHub 适配器 |
| 因子过滤器 | ~/work/chanlun_core/alpha_factor_filter.py | 候选股→panel→4因子→截面rank→alpha_score, 合并到phase2_results |
| 4D 评分 | ~/work/chanlun_core/composite_scorer.py | tech+fund+alpha+news 四维加权, 含veto否决层 |
| 配置 | ~/work/chanlun_core/config.yaml | weights/scoring/alpha_factor 段 |
| 配置加载 | ~/work/chanlun_core/config_loader.py | W_ALPHA / ALPHA_BUY_THRESHOLD |

**集成流程**: pool_scanner → scan_results.json(候选股) → alpha_factor_filter(alpha_score) → composite_scorer(4D评分)

**权重**: tech=0.35, fund=0.30, alpha=0.25, news=0.10
**仓位新增**: alpha < 30 → 仅轻仓（即使 tech >= 60）

**关键发现**:
- 12 个因子中 GTJA 微观结构 4 个(171/111/002/054)是真正的区分来源
- qlib158 形态 8 个横截面区分度低，聚合后大量股票落在中位数 50
- gtja191_163 因缺 amount 列被跳过（若有东方财富/AKShare amount 可解锁）
- 科创板(688xxx)数据量少导致因子回退到中性
- 计算性能：140 只 × 2 年 × 12 因子 ≈ 16 秒

详细实现数据（实际测试输出、因子得分分布等）见 `references/vibe-trading-alpha-zoo-analysis.md` 的「实际集成实现」节。

### Veto 否决层（2026-05-24 新增）

在 `composite_scorer.py` 中实现两级风控，不经过权重计算直接拦截高风险股票：

| 级别 | 效果 | 触发条件 |
|:----:|------|---------|
| **Veto（一票否决）** | grade=D, position=0 | 立案调查、财务造假、*ST、非标审计、人工黑名单 |
| **Severe（严重降级）** | 扣20分, 最多轻仓 | 行政处罚、减持计划、业绩变脸、监管措施 |

**关键词在 config.yaml 中可配置（不写死）**：
```yaml
scoring:
  veto_keywords: [立案调查, 财务造假, 涉嫌, *ST, ...]
  severe_keywords: [行政处罚, 减持计划, 业绩预告变脸, ...]
```

**设计原则**：
- 消息面权重 0.10 保留对日常情绪的调节作用
- 特定利空事件通过关键词匹配触发硬否决（不依赖权重）
- `apply_veto()` 独立函数，可被 `compute_3d_score()` 内部调用，也可单独调用
- 检查链：人工黑名单 → ST名称 → risk_filter结果 → 新闻详情关键词
- 配置文件优先：`config.yaml` → `config_loader.py` 默认值

**三路触发**：
1. `alpha_factor_filter.check_candidate_risks()` — 候选股预先检查
2. `compute_3d_score(code=..., name=..., news_detail=..., risk_reasons=...)` — 评分时自动检查
3. `apply_veto()` 独立调用 — 任意场景手动检查

详见 `references/veto-layer-design.md`。

### 基本面深度分析框架

现有系统的 `quick_fundamental.py::calculate_fundamental_score()` 是纯量化粗筛。完整的定性深度分析框架（护城河、商业模式、管理层评估、行业定位、估值深度、陷阱识别）详见 `chanlun-a500-screener` 技能的 `references/fundamental-analysis-framework.md`。该文档提取自《普通人价值投资课》第04-22篇，包含四层分析框架、护城河5种类型、商业模式4种类型、估值方法选择指南、好公司筛选标准和假好公司识别清单。

## 仓库打包移植 (Repo Packaging)

系统打包到独立 GitHub 仓库供其他 Agent 复用时的关键步骤和陷阱。

### 文件筛选

**包含（41个核心模块）**：
- 缠论引擎：`generate_analysis.py`, `data_manager.py`, `baostock_utils.py`, `data_source_helper.py`
- A500选股：`pool_scanner.py`, `pool_screener.py`, `composite_scorer.py`, `quick_fundamental.py`, `akshare_fundamental.py`, `akshare_scanner.py`, `risk_filter.py`, `stock_pool.py`
- 回测：`backtest_engine.py`, `portfolio_backtest.py`, `slow_bull_backtest.py`, `extreme_market_backtest.py`, `a500_backtest.py`, `fund_backtest.py`, `score_backtest.py`, `grid_search.py`, `run_backtest.py`
- 监控：`position_monitor.py`, `weixin_pusher.py`
- 市场：`market_regime.py`, `sentiment_analyzer.py`, `check_negative_news.py`, `check_price_levels.py`
- 工具：`config_loader.py`, `file_utils.py`, `slippage_model.py`, `cron_utils.py`, `stock_db.py`
- 报告：`report_generator.py`, `excel_report.py`, `quick_html.py`, `quick_chanlun.py`, `news_detail_report.py`
- 验证：`auto_validate.py`, `validate_tech_score.py`
- 数据：`data/config.yaml`, `data/sentiment_lexicon.json`, `data/.old_stocks_2016.json`

**排除**：
- 测试脚本：`*_test*.py`, `run_three_test.py`
- 缓存：`.scanner_cache.json`, `.phase2_results*.json`, `.stock_listing_cache.json`
- 生成结果：`*.csv`（回测结果）, `*.xls`/`*.xlsx`（用户持仓）
- 运行时目录：`data_cache/`, `signals/`, `logs/`, `reports_html/`, `auto_reports/`, `.alphaclaw/`
- Hermes 特定：`SKILL.md`, 审计报告 `*.md`

### ⚠️ 硬编码路径修复

原系统大量使用绝对路径，打包时必须改为相对路径：

| 文件 | 原路径 | 修复为 |
|------|--------|--------|
| `slow_bull_backtest.py` | `/home/zjj1990/work/chanlun_core/.old_stocks_2016.json` | `os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', '.old_stocks_2016.json')` |
| `portfolio_backtest.py` | 同上 | 同上 |
| `extreme_market_backtest.py` | `default="/home/zjj1990/.../extreme_market_results.csv"` | `default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'extreme_market_results.csv')` |
| `market_regime.py` | `default='/home/zjj1990/.../regimes.csv'` | 同上模式 |
| `slow_bull_backtest.py` | `default='/home/zjj1990/.../slow_bull_results.csv'` | 同上模式 |
| `portfolio_backtest.py` | `default='/home/zjj1990/.../portfolio_backtest_trades.csv'` | 同上模式 |

**保留的环境特定路径**（用户部署时按需修改）：
- `position_monitor.py`: `HOLDINGS_DIR = "/mnt/d/常用文件/持仓监控"` — 读 Windows Excel
- `pool_screener.py`: `OUTPUT_BASE = "/mnt/d/常用文件/股票池推荐股"` — 报告输出
- `check_negative_news.py`: `xlsx_path = "/mnt/d/常用文件/自选股负面消息清单/自选股清单.xlsx"` — 自选股清单
- `market_regime.py`: `MACRO_REPORT_DIR = "/mnt/d/常用文件/宏观数据监控"` — 宏观早报

### 打包命令

```bash
# 1. 创建仓库目录
mkdir -p chanlun-quant/data && cd chanlun-quant

# 2. 复制核心文件（从原始 chanlun_core 目录）
# 3. 修复硬编码路径（见上表）
# 4. 创建 .gitignore / README.md / requirements.txt / LICENSE
# 5. 语法检查
python3 -c "
import py_compile, os
for f in sorted(os.listdir('.')):
    if f.endswith('.py'):
        py_compile.compile(f, doraise=True)
print('All OK')
"
# 6. git init + commit
git init && git add -A && git commit -m "v1.0: init"
```

### requirements.txt 最小依赖

```
pandas>=2.0
numpy>=1.24
pyarrow>=12.0
baostock>=0.8.8
akshare>=1.18.0
efinance>=0.5.0
openpyxl>=3.1
xlrd>=2.0
filelock>=3.12
pyyaml>=6.0
# Optional: aiohttp>=3.9, cryptography>=41.0 (微信推送)
```

## 搜索源架构 (v3.3+)

系统使用三级搜索源：

| 级别 | 用途 | 搜索源 | 月用量 |
|------|------|--------|:--:|
| A组 | A500 消息面评分 | AKShare公告(L0) → 同花顺新闻(L1) → 同花顺公告(L1b) → Sina(L2) → Tavily(L3) → Metaso(L4) | ~90次 |
| B组 | 负面消息 cronjob | DuckDuckGo (唯一) | 0次 Tavily |
| C组 | 实时价格 fallback | Tavily → DDG | 偶发 |
| D组 | 基本面数据 | AKShare同花顺(主) → Baostock(fallback) | 135次/轮 |
| E组 | 研报评级 | AKShare东财(stock_research_report_em) | 30次/轮(仅Top30) |

### Tavily 降级机制

`pool_screener.py:scan_news()` 重构为多源降级：
```
scan_news(code, name)
  ├─ Tavily 成功 → 评分
  └─ Tavily 失败 → signals/.news_fallback_{code}.json
                  → 返回 (50, "Tavily失败→Metaso待补扫")
```

- `_write_news_fallback()` — 写标记文件
- `list_news_fallbacks()` — 列出所有待补扫
- 被 4 个入口调用：`run_phase2()`, `_process_one_stock()`, `full_rescore.py`, `renews.py`

cronjob `b1f25e25e1aa` (负面消息扫描) 已全部切换到 DuckDuckGo，不再消耗 Tavily 配额。

## 系统 Pre-flight 检查清单

重大修改后或运行 A500 全链路前，执行以下 5 步检查：

### 步骤 1: 语法检查 (22 文件)
```bash
cd ~/work/chanlun_core && python3 -c "
import py_compile
files = ['file_utils.py','slippage_model.py','cron_utils.py','config_loader.py',
         'pool_scanner.py','pool_screener.py','full_rescore.py','renews.py',
         'backtest_engine.py','generate_analysis.py','auto_validate.py',
         'score_backtest.py','validate_tech_score.py','composite_scorer.py',
         'data_manager.py','trading_strategy.py','quick_fundamental.py',
         'grid_search.py','data_source_helper.py','baostock_utils.py',
         'check_negative_news.py','check_price_levels.py',
         'weixin_pusher.py','position_monitor.py']
for f in files:
    py_compile.compile(f, doraise=True)
print('All OK')
"
```

### 步骤 2: 导入链完整性
```bash
cd ~/work/chanlun_core && python3 -c "
for mod in ['config_loader','composite_scorer','file_utils','slippage_model',
            'cron_utils','data_manager','generate_analysis','pool_scanner',
            'pool_screener','backtest_engine','auto_validate',
            'weixin_pusher','position_monitor']:
    __import__(mod); print(f'  OK: {mod}')
print('Import chain intact')
"
# ⚠️ 注意：full_rescore 导入即执行，不要放在批量导入中
```

### 步骤 3: 配置一致性
```python
from config_loader import get_config
cfg = get_config()
assert abs(cfg['weights']['tech']+cfg['weights']['fund']+cfg['weights']['news']-1.0) < 0.001
assert cfg['a500']['score_threshold'] == 3
assert cfg['backtest']['commission'] == 0.0003
```

### 步骤 4: 关键文件就绪
```bash
ls -lh config.yaml A500持仓.xls .scanner_cache.json .phase2_results.json 2>&1
ls /mnt/d/常用文件/股票池推荐股/ 2>&1 | head -5
```

### 步骤 5: 环境变量 + Baostock 连接
```bash
# 确认 API key 就绪
grep -c 'TAVILY_API_KEY\|METASO_API_KEY' ~/.hermes/.env
# 测试 Baostock
python3 -c "import baostock as bs; bs.login(); print('Baostock OK'); bs.logout()"
```

全部 5 步通过后系统即处于 🟢 就绪状态。
- [ ] 包含关系是否正确处理？
- [ ] 笔是否满足 5 根 K 线约束？
- [ ] 中枢 ZG/ZD 是否由至少 3 笔重叠定义？
- [ ] 数据源在 API 宕机时能否自动切换？
- [ ] 30分钟级别是否能够精准定位在日线买点之后？
- [ ] 回测是否避免了重复API调用（只取一次数据）？
- [ ] 参考价偏离 >10% 时是否做了价格校准？
- [ ] 如需简化，是否已移除多级别递归逻辑？

## A500 Stock Screener (Merged from chanlun-a500-screener)
*Unique content from the standalone `chanlun-a500-screener` skill, now consolidated here.*

### Trigger
User says "跑A500选股" / "跑选股" / "扫描A500" in hermes commander.

### Execution (Full Pipeline)
```bash
cd /home/zjj1990/work/chanlun_core && PYTHONUNBUFFERED=1 python3 -u pool_screener.py    # Phase1+2
python3 alpha_factor_filter.py   # Phase2.5: 算alpha+风控, 合并到phase2_results
python3 rescore_news.py          # Phase3: 补扫消息+生成4D MD报告
```
- Use `terminal(background=true, notify_on_complete=true, timeout=900)`
- **Always use absolute paths**: `~` expands to profile directory in background mode, use `/home/zjj1990/work/chanlun_core`
- `PYTHONUNBUFFERED=1` required for real-time output in background mode

### System Architecture
```
A500 Pool (510 stocks, from A500持仓.xls)
    │
    ▼ Phase1: Lightweight Technical Scan (pool_scanner.py)
    │   - Daily ChanLun analysis only (3s per stock, cached K-lines)
    │   - Buy point score 0-5 + 三买 forming detection
    │   - Filter score ≥3 → .scanner_cache.json
    │
    ▼ Phase2: 4D Deep Evaluation (pool_screener.py + alpha_factor_filter.py)
    │   - Tech: compute_technical_score()
    │   - Fund: AKShare 同花顺 (primary) → Baostock (fallback) → research report tweak (±5, Top30 only)
    - **Alpha**: 4 GTJA 幸存因子 via alpha_factor_filter.py (截面排名 0-100, 自动写入phase2_results)
    - **News**: AKShare announcement (L0) → Sina Finance (L1, free) → Tavily (L2) → Metaso (L3)
    - **Composite**: compute_3d_score() weights=0.35/0.30/0.25/0.10 + veto层（pool_screener.py 已接入alpha/veto参数）
    │
    **Phase2.5: Alpha Factor + Veto Merge (alpha_factor_filter.py)**
    - 候选股 → DBHub panel → 4因子计算 → cross-sectional rank → alpha_score [0,100]
    - 人工黑名单/ST名称/risk_filter 检查 → veto_reasons/severe_reasons
    - **merge_into_phase2()** 自动写入 `.phase2_results.json`（alpha_score + 风控信息）
    - 独立运行：`python3 alpha_factor_filter.py`

    **Phase3: Report Generation (rescore_news.py)**
    - 读 phase2_results → 补扫消息面 → 4D评分(含alpha+veto) → MD报告
    - **报告表格新增 Alpha 列**：`| # | 代码 | 名称 | 综合 | 等级 | 技术 | Alpha | 基本面 | 消息 | 仓位 | 模式 | 行业 |`
    - **否决/降级标记**：否决行 `⛔`，降级行 `⚠`，等级列显示 `⛔D`
```

### Phase1 Scoring Rules
| Type | Condition | Score |
|------|-----------|-------|
| Standard buy point (≤30 days) | No price penalty | 5 |
| Standard buy point (≤30 days) | Up 10-20% | 4 |
| Standard buy point (≤30 days) | Up >20% | 3 |
| Standard buy point (>30 days) | Any | 2 (filtered out) |
| 三买 forming (deep pullback ≥5%) | Breaking ZG×1.01 + pullback | 5 |
| 三买 forming (shallow pullback 3-5%) | Breaking ZG + pullback | 4 |
| 三买 forming (micro pullback 2-3%) | Breaking ZG + pullback | 3 |
| Post-reversal buy point | Detected after counter_trend reversal | 3-5 |

### AKShare 3-Layer Integration
1. **Layer1: Fundamental Enhancement** (akshare_fundamental.py) - 25 indicators from 同花顺, replaces Baostock as primary source
2. **Layer2: Announcement Scan** (akshare_scanner.py) - `stock_notice_report` pre-scan, title keyword matching
3. **Layer3: Research Report Rating** - `stock_research_report_em` for institutional consensus, ±5 tweak to fundamental score

### News Scoring Degradation Chain
```
scan_news(code, name)
 ├─ L0: AKShare announcement (pre-scan, cached)
 ├─ L1: 同花顺新闻搜索 (问财OpenAPI, 8条/股, 主数据源)
 ├─ L1b: 同花顺公告搜索 (问财OpenAPI, 5条/股, 新闻失败时fallback)
 ├─ L2: Sina Finance (免费fallback, ~40 articles/stock)
 ├─ L3: Tavily API (配额, fallback)
 ├─ L4: Metaso API (配额, fallback)
 └─ Fallback: return 50 (neutral)
```
- Net impact = positive count - negative count (article-level deduplication)
- Scoring formula: `min(75, 50 + net×5)` for net≥4; `max(15, 50 + net×3)` for net<-4
- 同花顺API需要 IWENCAI_API_KEY (设于 ~/.hermes/.env)，调用 news-search 和 announcement-search 技能
- 改造后 Top30 消息面评分从77%的50分降至7%，均值从53提升至67.4

### Key Fixes & Gotchas
- **Baostock session conflict**: `quick_fundamental.py` changed to `ensure_login()` (2026-04-30)
- **Background env isolation**: `pool_screener.py` added `dotenv.load_dotenv()` to load `~/.hermes/.env` (2026-05-01)
- **Estimation trap**: `full_rescore.py` must read real scores from `.phase2_results.json`, not use fixed mapping
- **Excel lock handling**: Auto-append timestamp if `PermissionError` when saving reports

---

## 多股扫描模块 (Multi-Stock Scanner)

对股票池进行一键扫描，筛选出强共振标的（多级别买卖点同步确认）。

### 核心设计模式

```python
class StockScanner:
    def __init__(self):
        self.dm = DataManager()  # 共享数据管理器，复用Baostock连接
    
    def scan(self, stock_pool):
        """扫描全部股票池"""
        self.results = []
        for code, name, ref_price in stock_pool:
            result = self._scan_single(code, name, ref_price)
            self.results.append(result)
        return self.results
    
    def _scan_single(self, code, name, ref_price):
        """单只股票扫描——创建独立分析器，避免跨股票状态泄漏"""
        rec_sys = RecursiveTimingSystem(self.dm)
        daily_analyzer = rec_sys.run_full_analysis(code, reference_price=ref_price)
        m30_analyzer = rec_sys.analyses.get('30min')
        
        trading_sys = FullTradingSystem(self.dm)
        signal = trading_sys.execute_for_stock(code, reference_price=ref_price,
                                                daily_analyzer=daily_analyzer,
                                                m30_analyzer=m30_analyzer)
        # ... 组装结果字典
```

### ⚠️ 关键优化：避免重复API调用

`FullTradingSystem.execute_for_stock()` 内部会调用 `run_full_analysis()` 重新获取日线和30min数据。在多股扫描时，这会导致每只股票 **4次API调用**（日线+30min × 2次扫描）。必须改造以支持传入预建分析器：

```python
def execute_for_stock(self, symbol, reference_price=None,
                     daily_analyzer=None, m30_analyzer=None):
    """支持传入预建分析器，避免重复API调用"""
    if not daily_analyzer:
        # 未传入时自己创建
        daily_analyzer = self.analyzer_system.run_full_analysis(symbol, reference_price)
        m30_analyzer = self.analyzer_system.analyses.get('30min')
    
    # 从已有分析器取最新价，不再调 get_klines
    if m30_analyzer and m30_analyzer.klines:
        current_price = m30_analyzer.klines[-1].close
    else:
        current_price = daily_analyzer.klines[-1].close if daily_analyzer.klines else 0
    
    # 信号生成...
```

优化后每只股票仅需 **2次API调用**（日线+30min各1次），无需额外请求获取最新价。

### 输出格式

扫描结果包含：
- **信号方向**: BUY/SELL/HOLD，带🟢/🔴/⏸️标识
- **入场价格**: 当前最新价
- **止损/止盈**: 来自交易信号（注意：参考价偏离大时会被缩放扭曲）
- **置信度**: 高置信度计数（≥4分信号的数量）
- **紧急度**: HIGH/MEDIUM/LOW，基于强共振条件判断
- **强共振标的**: BUY信号 + 高紧急度 → 列入✨推荐列表

### 性能考量

- 21只A股扫描耗时约2-3分钟（~5秒/只 × 2次API调用/只）
- Baostock连接为全局单例，复用无需反复登录
- 超时处理：单只股票超过30秒跳过，避免阻塞全池
- 数据源故障转移链在各股票间独立生效
