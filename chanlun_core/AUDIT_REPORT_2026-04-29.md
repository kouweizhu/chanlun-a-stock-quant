# 三维分析系统v3.0 — 综合审计报告
# Hermes 3D Analysis System Codebase Audit
# Date: 2026-04-29

## ============================================================
## 1. ARCHITECTURE OVERVIEW
## ============================================================

The system is a quantitative A-share trading system based on 缠论 (ChanLun theory)
with three dimensions: technical (缠论), fundamental, and sentiment.

### Module Dependency Graph:

    check_negative_news.py (MONITOR_LIST — stock universe, cron entry point)
            │
            ├── composite_scorer.py (3D scoring: tech + fund + news → score [-30,100])
            │       ├── TECH: from validate_tech_score.py / score_backtest.py
            │       ├── FUND: from quick_fundamental.py (Baostock data)
            │       └── NEWS: fixed 50 (neutral, unless overridden)
            │
            ├── backtest_engine.py (漏斗过滤法 backtest engine)
            │       ├── trading_strategy.py (buy/sell signal generation)
            │       ├── generate_analysis.py (ChanLunAnalyzer — core tech engine)
            │       └── data_manager.py (multi-source data: Baostock/efinance/AkShare)
            │
            ├── score_backtest.py (tech score → forward returns backtest, HTML)
            │       ├── generate_analysis.py (ChanLunAnalyzer)
            │       └── validate_tech_score.py (compute_technical_score)
            │
            ├── grid_search.py (weight scheme + confidence anchoring grid)
            │       ├── validate_tech_score.py (signal collection)
            │       └── quick_fundamental.py (historical fund scores)
            │
            ├── fund_backtest.py (fundamental score → forward returns, HTML)
            │       └── quick_fundamental.py (Baostock financial data)
            │
            ├── validate_tech_score.py (tech score vs forward returns validation)
            │       └── backtest_engine.py (BacktestEngine)
            │
            ├── run_backtest.py (CLI batch backtest runner)
            │       └── backtest_engine.py
            │
            ├── stock_pool.py (unified A500 stock pool, ~400 stocks)
            │
            ├── data_source_helper.py (data failover docs + bridge protocol)
            │
            └── generate_analysis.py (ChanLunAnalyzer — 分型/笔/中枢/买卖点)

### Key Design Decisions:
- Baostock is the primary data source (前复权 adjustflag='2')
- 30-minute data used for multi-level confirmation (共振)
- Fund score: 4 dimensions × 25 points = 100 points total
- Composite: 0.40×tech + 0.30×fund + 0.30×news


## ============================================================
## 2. CODE LOGIC vs THEORY CONTRADICTIONS
## ============================================================

### 2.1 CRITICAL: tech_norm clamps negative scores to 0
**File**: composite_scorer.py:99
**Code**:
```python
tech_norm = max(0, min(100, tech_score)) if tech_score >= 0 else max(SCORE_MIN, tech_score)
```
**Problem**: When tech_score >= 0, it's clamped to [0,100]. When tech_score < 0,
it becomes max(-30, tech_score), i.e. [-30, 0). But the design says
SCORE_MIN = -30, implying negative tech scores should be possible.

However, validate_tech_score.compute_technical_score() ALWAYS returns
scores >= 0 (structure 0-40 + signal 0-30 + resonance 0-20 + volume 0-10
+ volatility 0-5 = 0-105). The score_backtest.py simplified scorer CAN
go negative (it subtracts 3, 5, 8).

**Impact**: The resonance penalty block (line 105) checks tech_norm < 60.
If tech_score is, say, -20, then tech_norm = -20, and the penalty logic
applies. But if tech_score = 0, tech_norm = 0, same behavior. The
functioning is consistent but the clamping creates an asymmetry:
tech=40 → tech_norm=40, but the theoretical [-30,100] range for tech
is never actually produced by the main scoring pipeline.

### 2.2 MODERATE: Composite score range [-30,100] technically unreachable from standard pipeline
**File**: composite_scorer.py:102
**Code**:
```python
composite = tech_norm * w_tech + fund_score * w_fund + news_score * w_news
```
With tech∈[0,100], fund∈[0,100], news∈[0,100]:
  min composite = 0*0.4 + 0*0.3 + 0*0.3 = 0
  max composite = 100*0.4 + 100*0.3 + 100*0.3 = 100

The [-30,0) range is only reachable if tech_score goes negative (from
score_backtest.py's simplified scorer), which is not the standard path.

**Recommendation**: If negative scores are intended, document when they
should occur. Otherwise, update SCORE_MIN to 0.

### 2.3 LOW: Position logic doesn't perfectly match stated theory
**File**: composite_scorer.py:127-133
**Theory**: fund<40 → 轻仓15%, fund≥60 → 重仓50%
**Actual Code**:
```python
if not can_buy:
    position = POSITION_NONE
elif fund_score >= FUND_HEAVY_THRESHOLD:  # >= 60
    position = POSITION_HEAVY if grade == 'A' else POSITION_NORMAL
elif fund_score >= FUND_LIGHT_THRESHOLD:  # >= 40
    position = POSITION_NORMAL if grade >= 'B' else POSITION_LIGHT
else:
    position = POSITION_LIGHT  # fund < 40 → 15%
```
**Discrepancy**: For fund>=60, position is 50% ONLY if grade='A'. If
grade='B','C','D', position is 30% (NORMAL), not 50%. This is a
reasonable refinement but diverges from the simple stated rule.

### 2.4 grid_search.py: compute_composite_score does NOT implement resonance penalty
**File**: grid_search.py:210-245
**Problem**: grid_search.compute_composite_score() uses a simple weighted
average without the resonance penalty that composite_scorer.compute_3d_score()
applies. This means grid search results may not reflect actual live scoring
behavior when both tech and fund are weak.

### 2.5 grid_search.py: news_score always 50.0 — wasted weight
**File**: grid_search.py:228
```python
news_score = 50.0
```
All weight schemes assign 25-30% to news, but it's always 50 (neutral).
This means the grid search is partially exploring noise — the news weight
varies but the actual news input is fixed. The effective test is really
about rebalancing tech vs fund weights.

### 2.6 Two SEPARATE tech scoring systems (not a single source of truth)
**File**: validate_tech_score.py:54-288 vs score_backtest.py:184-240
**Problem**: There are TWO completely independent tech scoring implementations:

A) validate_tech_score.compute_technical_score():
   - 4 structured dimensions: structure(40) + signal(30) + resonance(20) + volume(10) + volatility(5)
   - 9-档中枢位置精细化
   - Returns [0, 105]

B) score_backtest.calc_tech_score() (fallback path, no recent buy point):
   - Ad-hoc additions: +15(up bi), +20(above ZS), +10(golden MACD), +25(recent buy)
   - Ad-hoc subtractions: -3(down bi), -5(three down), -8(below ZS), -8(sell unresolved), -8(divergence)
   - Can return [-30, 100]

When score_backtest finds a recent buy point, it delegates to (A).
Otherwise it uses (B). The two systems have very different distributions
and sensitivity profiles.

### 2.7 fund_backtest.py: calc_fund_score is a COPY of quick_fundamental.calculate_fundamental_score
**File**: fund_backtest.py:183-300 vs quick_fundamental.py:307-505
**Problem**: The scoring logic is copy-pasted. If one is updated, the other
must be manually synced. There's already a minor difference:
fund_backtest.py skips the "else: p_score += 0" for gp_margin (line 227
uses "elif" not "else"), making its score slightly different.


## ============================================================
## 3. STOCK CLASSIFICATION INCONSISTENCY
## ============================================================

### TWO INDEPENDENT CLASSIFICATION SYSTEMS:

### System A: validate_tech_score.py (imported from check_negative_news + quick_fundamental.classify_by_industry)
**File**: validate_tech_score.py:33-51 (DEFAULT_STOCKS) + quick_fundamental.py:282-304
Uses `classify_by_industry()` which is a lightweight function:
- "安防" → "成长" (because "安防" is in growth_industries list)
- Unknown industry → "其他"

### System B: score_backtest.py _STYLE_MAP (hardcoded)
**File**: score_backtest.py:42-46
```python
_STYLE_MAP = {
    "化工": "周期", "食品": "蓝筹", "消费": "蓝筹", "农牧": "周期",
    "新能源": "成长", "地产": "周期", "建材": "周期", "保险": "蓝筹",
    "金融": "成长", "银行": "蓝筹", "安防": "蓝筹",   # ← "安防" = "蓝筹"
}
```

### System C: quick_fundamental.classify_stock_type() (data-driven)
**File**: quick_fundamental.py:219-279
Uses industry lists + ROE/growth/market_cap combination.

### System D: fund_backtest.py STYLE_MAP
**File**: fund_backtest.py:38-42
```python
STYLE_MAP = {
    "化工": "周期", "食品": "蓝筹", "消费": "蓝筹", "农牧": "周期",
    "新能源": "成长", "地产": "周期", "建材": "周期", "保险": "蓝筹",
    "金融": "成长", "银行": "蓝筹", "安防": "蓝筹",   # ← same as System B
}
```

### System E: check_negative_news.py _INDUSTRY_MAP + _NAME_INDUSTRY_HINTS
**File**: check_negative_news.py:23-51
Separate industry inference logic for news monitoring.

### EXACT DISCREPANCIES:

| Industry | classify_by_industry (A) | _STYLE_MAP (B/D) | classify_stock_type (C) |
|----------|-------------------------|-------------------|------------------------|
| 安防     | 成长                    | 蓝筹              | 成长                   |
| 金融     | 蓝筹 (matched by "金融" in blue_chip list) | 成长 | 蓝筹 (from industry match) |
| 食品     | 蓝筹                    | 蓝筹              | 蓝筹 (consistent)      |
| 消费     | 蓝筹                    | 蓝筹              | 蓝筹 (consistent)      |
| 钢铁     | 周期                    | (not mapped)      | 周期                   |
| 有色     | 周期                    | (not mapped)      | 周期                   |

**Key Issue**: "安防" (海康威视):
- validate_tech_score.py uses classify_by_industry → "成长"
- score_backtest.py uses _STYLE_MAP → "蓝筹"
- quick_fundamental.classify_stock_type uses growth_industries → "成长"

The stock 海康威视 (002415) will be classified as "蓝筹" in backtest
reports but "成长" in live analysis. This affects:
- Score interpretation
- Report grouping
- Any strategy that treats blue-chip vs growth differently

**Recommendation**: Unify to classify_by_industry() from quick_fundamental.py.
Delete _STYLE_MAP from score_backtest.py and fund_backtest.py.


## ============================================================
## 4. DATA PIPELINE ISSUES
## ============================================================

### 4.1 Monkey-patched print() — fragile across 3 files
**Files**: quick_fundamental.py:8-14, score_backtest.py:22-28, fund_backtest.py:23-26
**Code** (quick_fundamental.py):
```python
_orig_print = builtins.print
builtins.print = lambda *a, **kw: _orig_print(*a, **({**kw, 'file': sys.stderr}
    if 'file' not in kw and a and (str(a[0]).startswith('[') or ...) else kw))
```
**Problem**: Three modules independently monkey-patch builtins.print.
If imported in the same process, only the last one takes effect, and
_orig_print chains can break. This is a ticking time bomb.

**Fix**: Use Python's `logging` module with a stderr handler, or use
`sys.stderr.write()` explicitly for Baostock messages.

### 4.2 Baostock session management — multiple login/logout without coordination
**Files**: quick_fundamental.py:44+194, grid_search.py:78-83, fund_backtest.py:107+163
**Problem**: Each module calls bs.login()/bs.logout() independently.
Baostock is stateful — multiple login() calls without matching logout()
can cause "you don't login" errors or session leaks.

grid_search.py:78-83 has an especially fragile pattern:
```python
try:
    rs_test = bs.query_stock_basic(code=bs_code)
    if rs_test.error_code != '0':
        bs.login()
except:
    bs.login()
```
This runs login() once per stock per quarter (with caching), but if
the session expires mid-loop, subsequent queries fail silently.

### 4.3 Bare except clauses — silently swallowing errors
**Locations**:
- backtest_engine.py:206,437,479,521,567 — all `except: pass`
- score_backtest.py:181 `except: pass` — hides analysis failures
- generate_analysis.py (multiple locations)
- fund_backtest.py:166 `except: pass`

These hide real errors. Should at minimum log the exception.

### 4.4 Field name mapping: Baostock vs internal
**File**: quick_fundamental.py:109
```python
"totalRevenue": safe_float(p.get("MBRevenue")),
```
Baostock's profit_data uses "MBRevenue" for revenue but the system
stores it as "totalRevenue". This mapping is correct but undocumented
and could break if Baostock changes field names.

### 4.5 get_quarter_for_date() — logic bug for April
**File**: grid_search.py:56
```python
elif m >= 5:     return (y, 2) if m > 4 or dt.day >= 30 else (y - 1, 4)
```
For m=4 (April), this checks `m > 4` (False) or `dt.day >= 30`.
- April 30 → returns (y, 2) ← WRONG: should be (y-1, 4) because
  Q1 report is just being filed, annual report (y-1, Q4) deadline is April 30
- April 29 → returns (y-1, 4) ← correct

The boundary should be: April 30 is the DEADLINE for annual reports,
not the start of Q2 availability. After May 1, Q1 (y, 1) becomes
available, and after Aug 31, semi-annual (y, 2) becomes available.

Corrected logic:
```python
if m >= 10:     return (y, 3)      # Q3 after 10/31
elif m >= 9:    return (y, 2)      # Semi-annual after 8/31
elif m >= 5:    return (y, 1)      # Q1 after 4/30
else:           return (y-1, 4)    # Annual report, available by 4/30
```

### 4.6 Duplicate Baostock code — fund_backtest.py vs quick_fundamental.py
**Files**: fund_backtest.py:92-168 vs quick_fundamental.py:43-206
The Baostock data fetching logic is essentially duplicated. If Baostock
API changes or a bug is found, it must be fixed in two places.


## ============================================================
## 5. CODE QUALITY
## ============================================================

### 5.1 Dead code / unused imports
- validate_tech_score.py:33 imports `classify_by_industry` from quick_fundamental
  — used for default stock type, but not for the main validation loop
  where stock_type is assigned from stock_type_map
- score_backtest.py:15 `from typing import List, Dict, Optional` —
  Optional is imported but never used
- backtest_engine.py:27 `from typing import ... Tuple` — Tuple is used
  but Optional is imported and unused in some paths
- grid_search.py:313 `NUM_STOCKS = len(stocks)` — shadows the module-level
  NUM_STOCKS = 30 (line 204). The module-level one is never used.

### 5.2 Hardcoded values that should be configurable
| Value | Location | Suggestion |
|-------|----------|------------|
| 0.0003 (commission) | backtest_engine.py:407 | Config constant |
| 0.08 (hard stop -8%) | backtest_engine.py:185 | Config constant |
| 120 days (buy point expiry) | trading_strategy.py:31 | Config constant |
| 0.97 (30min stop -3%) | trading_strategy.py:42 | Config constant |
| 66 days (buy point window) | score_backtest.py:164 | Config constant |
| 2000000 (initial capital) | backtest_engine.py:51 | Already parameterized, OK |
| xlsx_path | check_negative_news.py:20 | Config or env var |

### 5.3 score_backtest.py _STYLE_MAP duplicates classify_by_industry
Already covered in Section 3. Remove _STYLE_MAP, use classify_by_industry.

### 5.4 quick_fundamental.py: print redirect intercepts ALL imports
Line 8-14 monkey-patches builtins.print at MODULE LOAD TIME. This means
anyone importing quick_fundamental for any reason (even just to get
classify_by_industry) gets their print() hijacked. This is extremely
bad practice.

### 5.5 fund_backtest.py: calc_fund_score is copy-pasted from quick_fundamental
Should import and reuse quick_fundamental.calculate_fundamental_score().

### 5.6 Inconsistent string quoting
Mixed use of single and double quotes across the codebase. Minor but
reduces readability.


## ============================================================
## 6. OPTIMIZATION OPPORTUNITIES
## ============================================================

### 6.1 Performance: score_backtest.py re-creates ChanLunAnalyzer for every (stock, date)
**File**: score_backtest.py:279-318
For each test date × each stock, a new ChanLunAnalyzer is created and
runs full analysis on the truncated data. With 18 stocks × ~38 weeks,
that's ~684 full analyses. This is the main bottleneck.

**Optimization**: Pre-compute all analyses once per stock on full data,
then for each test date, filter buy/sell points by date. The ChanLun
structure (bi/zhongshu) is deterministic given the data up to that point,
but re-running from scratch each time is wasteful.

### 6.2 Performance: grid_search.py runs validate_tech_score for all stocks sequentially
**File**: grid_search.py:326-337
Each stock calls run_single_analysis() which loads data from Baostock.
This is I/O-bound and sequential. With 18 stocks, it takes minutes.

**Optimization**: Parallelize with ThreadPoolExecutor (I/O bound).

### 6.3 Performance: Baostock data is not cached across modules
Each module (quick_fundamental, grid_search, fund_backtest) independently
fetches the same data. A shared LRU cache or DataManager singleton would
reduce API calls.

### 6.4 Readability: backtest_engine.run_backtest() is 250+ lines
The main trading loop (lines 338-587) handles buying, selling, stop-loss,
and take-profit all in one massive for-loop. Should be refactored into
separate methods: _process_buy_signals(), _process_sell_signals(),
_check_take_profit().

### 6.5 Readability: generate_analysis.py is 1294 lines
The ChanLunAnalyzer class has all logic in one file. Could be split:
- chanlun_core.py (KLine, FenXing, Bi, ZhongShu dataclasses)
- chanlun_analyzer.py (ChanLunAnalyzer class)
- recursive_timing.py (RecursiveTimingSystem)

### 6.6 Maintainability: No type hints on most functions
Only composite_scorer.py and trading_strategy.py use type hints
consistently. The rest uses bare parameters.

### 6.7 Maintainability: No unit tests
There are `if __name__ == "__main__"` self-test blocks in
composite_scorer.py and validate_tech_score.py, but no formal test suite.
A pytest suite covering:
- composite_scorer edge cases
- classify_by_industry vs classify_stock_type consistency
- ChanLunAnalyzer known-pattern tests
would catch regressions.

### 6.8 Maintainability: stock_pool.py has 400+ stocks but MONITOR_LIST has 18
There's a mismatch between the "universe" (stock_pool.py, ~400 A500 stocks)
and the "active monitoring" list (check_negative_news.py, 18 stocks).
The backtest systems use the 18-stock list. The stock_pool.py is used
only by run_backtest.py's --pool flag. This split is intentional but
undocumented.


## ============================================================
## 7. FILE-BY-FILE NOTES
## ============================================================

### composite_scorer.py
- Purpose: 3D composite scoring (tech + fund + news)
- Key functions: compute_3d_score(), position_reason()
- Issues: tech_norm clamping (2.1), resonance penalty (2.1), grade position (2.3)
- Quality: Good — clean, well-documented, self-tested

### validate_tech_score.py
- Purpose: Tech score validation with forward returns
- Key functions: compute_technical_score(), get_forward_returns(), run_single_validation()
- Issues: Uses classify_by_industry (correct), but DEFAULT_STOCKS has inline industry
- Quality: Good — comprehensive 4-dimension scoring

### quick_fundamental.py
- Purpose: Baostock financial data fetching + scoring
- Key functions: get_fundamentals(), classify_stock_type(), classify_by_industry(), calculate_fundamental_score()
- Issues: Monkey-patched print (4.1), dual classification (3), Baostock session (4.2)
- Quality: Moderate — monkey-patching is a major smell

### score_backtest.py
- Purpose: Tech score backtesting with HTML visualization
- Key functions: calc_tech_score(), run_backtest(), generate_html()
- Issues: Dual scoring system (2.6), _STYLE_MAP (3), monkey-patched print (4.1)
- Quality: Moderate — the fallback scorer is ad-hoc

### grid_search.py
- Purpose: Parameter grid search for weights + anchoring
- Key functions: compute_composite_score(), get_fund_score(), main()
- Issues: No resonance penalty (2.4), fixed news=50 (2.5), get_quarter_for_date bug (4.5), shadowed NUM_STOCKS (5.1)
- Quality: Moderate — functional but has subtle bugs

### backtest_engine.py
- Purpose: Full backtest engine with funnel filtering strategy
- Key functions: BacktestEngine.run_backtest(), _check_stop_loss(), _find_zs_for_third_buy()
- Issues: Massive run_backtest() (6.4), hardcoded values (5.2), bare excepts (4.3)
- Quality: Good logic, needs refactoring for readability

### generate_analysis.py
- Purpose: Core ChanLun analysis engine (分型/笔/中枢/买卖点)
- Key functions: ChanLunAnalyzer.analyze(), _find_bis(), _find_zhongshus(), _find_buy_sell_points()
- Issues: Monolithic 1294-line file (6.5)
- Quality: Core engine — works, but hard to maintain at this size

### trading_strategy.py
- Purpose: Trading signal generation (buy/sell/hold)
- Key functions: TradingStrategy.generate_signal(), FullTradingSystem.execute_for_stock()
- Issues: Hardcoded 120-day expiry (5.2)
- Quality: Good — clean and focused

### check_negative_news.py
- Purpose: Negative news monitoring (cron entry point)
- Key functions: _load_monitor_list(), format_report(), main()
- Issues: Hardcoded xlsx_path (5.2)
- Quality: Good — proper fallback chain

### data_source_helper.py
- Purpose: Data source priority documentation + bridge protocol
- Key functions: save_fallback_data(), check_agent_fallback(), mark_python_sources_failed()
- Issues: None significant — this is mostly documentation
- Quality: Good — well-organized reference

### stock_pool.py
- Purpose: Unified stock pool (A500 subset)
- Key: TIER1_POOL (14 stocks), DEFAULT_POOL (~400 stocks)
- Issues: None — simple data file
- Quality: Good

### run_backtest.py
- Purpose: CLI batch backtest runner
- Issues: None significant
- Quality: Good — clean CLI wrapper

### fund_backtest.py
- Purpose: Fundamental score backtesting with HTML
- Issues: Duplicate calc_fund_score (5.5), duplicate STYLE_MAP (3), monkey-patched print (4.1)
- Quality: Moderate — needs deduplication


## ============================================================
## 8. SUMMARY OF FINDINGS
## ============================================================

### Critical (1):
1. Monkey-patched print() across 3 files — can break when modules co-imported

### High (4):
2. TWO independent tech scoring systems (validate vs score_backtest fallback)
3. FOUR independent stock classification systems with conflicting results
4. get_quarter_for_date() off-by-one logic bug for April boundary
5. Duplicate code: fund_backtest.py calc_fund_score = copy of quick_fundamental

### Medium (6):
6. Baostock session management uncoordinated across modules
7. Bare except clauses hiding real errors (12+ locations)
8. grid_search doesn't implement resonance penalty → results don't reflect live behavior
9. backtest_engine.run_backtest() is 250+ lines, needs decomposition
10. generate_analysis.py is 1294 lines, needs splitting
11. No formal test suite

### Low (5):
12. Composite score [-30,0) range unreachable from standard pipeline
13. Hardcoded values (commission, stop-loss %, expiry days)
14. Unused imports in several files
15. Module-level NUM_STOCKS shadowed in grid_search.main()
16. 400-stock pool vs 18-stock monitor list split undocumented
