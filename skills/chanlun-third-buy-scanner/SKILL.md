---
name: chanlun-third-buy-scanner
description: 缠论三买候选股轻量扫描器 — 从股票池中快速筛选"突破中枢后回踩确认/进行中"的标的，定位类似002415(海康威视)的三买机会。
category: trading
tags: [缠论, 三买, 扫描器, 中枢, 量化选股, A500, 买点检测]
version: 1.0.0
author: Hermes Trading Team
created: 2026-05-01
updated: 2026-06-02
---

# 缠论候选股轻量扫描器（三买 + 全买点类型）

## 前置依赖

使用本 skill 中的脚本模板或生产脚本前，确保以下依赖已就绪：

| 依赖项 | 最低版本 | 说明 |
|--------|---------|------|
| Python | 3.8+ | 运行环境 |
| baostock | 最新 | 日线/30分钟行情数据源 |
| pandas | 1.3+ | 数据处理 |
| numpy | 1.20+ | 数值计算 |
| openpyxl | 3.0+ | Excel 导出（可选，仅导出时需安装） |
| chanlun_core | — | 缠论分析引擎库，路径 `~/work/chanlun_core/` |

**chanlun_core 路径确认**：生产脚本 `pool_scanner.py` 和 `pool_screener.py` 均位于 `~/work/chanlun_core/`，需要在该目录下执行，或确保 `sys.path` 中包含该路径。

**安装命令**（首次使用）：
```bash
pip install baostock pandas numpy openpyxl
```

## 生产级实现

本 skill 描述的是概念框架和脚本模板。**生产级实现已落地为两个独立脚本：**

| 脚本 | 功能 | 路径 |
|------|------|------|
| `pool_scanner.py` | Phase 1：全买点类型扫描（一二三类买点 + 潜在买点结构），0-5 评分，输出 JSON 缓存 | `~/work/chanlun_core/pool_scanner.py` |
| `pool_screener.py` | Phase 1→2→3 全流程：扫描 → 三维评分 → HTML+MD+Excel 报告 | `~/work/chanlun_core/pool_screener.py` |

**v3.6 更新（2026-05-30）**：修复类一买（盘整底背驰）信号质量问题。盘整背驰评分从 5/4/3 降为 4/3/2；新增"中枢下移"检测，趋势下跌中的类一买进一步降级为 2/1/0。详见 `references/leiyimai-signal-quality-fix.md`。

**使用方法**（在 hermes commander 中）：
```bash
cd ~/work/chanlun_core

# 仅扫描（3-5分钟）
python3 pool_scanner.py

# 全流程（8-15分钟）
python3 pool_screener.py

# 从缓存继续
python3 pool_screener.py --from-cache

# 测试前N只
python3 pool_screener.py --test 10
```

**流水线架构**：
```
A500 股票池 (510只, A500持仓.xls)
    ↓
Phase 1: pool_scanner.py — ChanLun 日线扫描
    - 一买(底背驰): 价格≤ZD + MACD底背驰 → score 5
    - 二买(二次确认): 一买后回踩未破前低 → score 5
    - 三买(突破回踩): 价格>ZG + 回踩守住 → score 5
    - 潜在一买: 中枢下沿+MACD金叉 → score 3
    - 中枢附近: 价格在ZD附近+MACD改善 → score 2
    - 突破未回踩: 价格>ZG无回踩 → score 2
    阈值: score ≥ 2 → 进入 Phase 2（约50-80只）
    ↓ ✅ [检查点 1] Phase 1 扫描完成，验证 candidate 数量合理（预期50-80只）
Phase 2: 三维深度评估
    - 技术: validate_tech_score.compute_technical_score()
    - 基本面: quick_fundamental.calculate_fundamental_score()
    - 消息面: scan_news() — DuckDuckGo 搜索负面新闻
    - 综合: composite_scorer.compute_3d_score()
    - 容错: Baostock 失败 → 基本面降级为 50；技术评分失败 → 用扫描分估算
    ↓ ✅ [检查点 2] Phase 2 评分完成，检查 composite 分布是否正常（A/B/C/D 四档均有数据）
Phase 3: 报告生成
    每只推荐股:
    - HTML: generate_analysis.HTMLVisualizer → 交互式缠论 K 线图
    - MD: 三维评分详情（含仓位建议）
    汇总:
    - MD 总表 + Excel 总表（带等级着色）
    输出: /mnt/d/常用文件/股票池推荐股/{股票名}_{代码}/
    ✅ [检查点 3] 输出目录存在且含完整报告（同时检查磁盘空间）
```

**依赖的共享模块**：`baostock_utils.py`（print 重定向 + Baostock session 管理 + 重试），所有模块统一 import，不再各自 monkey-patch。

---

## 使用场景

从大量自选股（100~500+只）中快速筛选出缠论买点机会。本 skill 最初专注于三买（"**突破中枢→回踩不破中枢上沿**"），后扩展为全买点类型扫描。生产级代码见 `pool_scanner.py`。

## 扫描逻辑

对每只股票运行日线缠论分析，按以下条件判断：

```
条件1: 至少有一个完整中枢 (zhongshu)
条件2: 有一笔向上笔突破了最近中枢的ZG（中枢上沿），且涨幅 > 1%
条件3: 突破后出现了向下笔回踩（或仍在回踩中）
条件4: 回踩的最低点 > ZG（三买确认）
```

### 买点分类与评分体系（v4.0）

**标准缠论买点（0-5分，受时间窗口+价格涨幅+回调幅度多重惩罚）：**

| 分数范围 | 分类 | 含义 |
|:---:|:---|:---|
| 0-5 | 一买(确认) | 下跌趋势背驰+向上笔确认。30天内=5分，超30天=2分；涨超20%扣2，涨超10%扣1 |
| 0-5 | 二买 | 一买后回调不破前低。30天内=5分，31-60天=4分。v4.0新增：回调距一买低点<2%扣1.5，<5%扣0.5 |
| 0-5 | 三买 | 突破ZG回踩不破ZG。30天内=5分，超30天=2分。普通惩罚同上 |
| 1-4 | 潜一买(等待确认) | 趋势背驰满足，等向上笔确认。从对应一买分-1（确认升级），最低1分 |
| 2-4 | 类一买(盘整底背驰) | 盘整中连续下跌笔MACD面积衰减<40%。极强(≤20%)=4，强(≤30%)=3，温和(≤40%)=2 |
| 0-2 | 类一买(中枢下移) | 趋势下跌中的盘整底背驰，可靠性低。进一步降级为2/1/0 |
| 1-3 | 反转后三买 | 趋势反转后新中枢的三买。无背驰支撑，基础3分-价格惩罚（v3.6从5降至3） |

**结构位置评分（不进候选池）：**

| 分数 | 分类 | 含义 |
|:---:|:---|:---|
| 2 | 中枢下沿机会 | 价格在ZD附近+MACD改善，非买点信号 |
| 2 | 中枢上沿附近 | 价格在ZG附近，观察三买 |
| 3-4 | 三买形成中(未成笔) | 突破后回踩进行中。深回踩≥5%=4分，浅回踩3-5%=3分。v4.0上限从5降至4（已确认三买才给5） |
| 1 | 突破未回踩 | 突破ZG但尚无回踩笔 |
| 0 | 上涨趋势回调 | 上涨趋势中的一买/类一买信号，无效 |
| 0 | 下跌趋势中 | 无信号 |

**关键理论约束（v3.6新增）：**
- 类一买/潜一买**只在下跌趋势或盘整中触发**，上涨趋势中的回调不是一买/类一买
- 判断标准：最近5个中枢ZD上涨超过10% → 上涨趋势 → 过滤
- 反转后三买不需要一买背驰确认，只有结构支撑，可靠性低于标准三买，评分降至3

### 类一买趋势过滤（v3.6新增，2026-05-30）

**核心原则**：一买/类一买只在下跌趋势或盘整中有效，上涨趋势中的回调不是类一买。

**判断标准**：
```python
# 最近5个中枢ZD上涨超过10% → 上涨趋势 → 不触发类一买
lookback = min(5, len(zhongshus))
trend_zs = zhongshus[-lookback:]
trend_zd = [float(z.zd) for z in trend_zs]
if trend_zd[-1] > trend_zd[0] * 1.10:
    return 0, "", None  # 上涨趋势中的回调，不是类一买
```

**典型案例**：
- 艾力斯(688578)：ZD 37→55→75→92→103→89（涨3倍后回调14%）→ 上涨趋势，类一买无效
- 同仁堂(600085)：ZD 47→41→35→38→36→33→31→28（持续下移）→ 下跌趋势，类一买有效

### 买点命名规范（v3.6修正）

| 代码中的名称 | 正确名称 | 评分 | 说明 |
|-------------|---------|------|------|
| 潜在一买(中枢下沿+MACD改善) | **中枢下沿机会(下沿+MACD改善)** | 2 | 结构位置，非买点信号 |
| 潜在二买(中枢下沿+向上笔) | **中枢下沿机会(下沿+向上笔)** | 2 | 结构位置，非买点信号 |
| 类一买(上涨趋势,无效) | 类一买(上涨趋势,无效) | 0 | 已过滤 |

**关键区分**：
- `confirmed=False` 的一买（generate_analysis.py）= 背驰已出现但无向上笔确认 → 类一买
- "中枢下沿机会"（pool_scanner.py）= 价格在中枢下沿附近+MACD改善 → **不是买点信号**
| 1 | 突破未回踩 | 突破ZG但尚无回踩笔 |
| 2 | 回踩进中枢 | 回踩跌入中枢内部(ZD~ZG) |
| 0 | 跌破中枢 | 回踩跌破ZD |
| 0 | 类一买(上涨趋势,无效) | 上涨趋势中的回调，不是类一买 |

**重要**：类一买（盘整底背驰）只在下跌趋势或盘整中触发。上涨趋势中的回调不应被识别为类一买。判断标准：最近5个中枢ZD上涨超过10% → 上涨趋势 → 过滤。

## 脚本模板

```python
#!/usr/bin/env python3
"""light_scanner.py — 轻量扫描器：寻找"突破中枢后回踩"的三买候选股"""

import sys, os, time
sys.path.insert(0, '/path/to/缠论/dir')
os.chdir('/path/to/缠论/dir')

from data_manager import DataManager
from generate_analysis import ChanLunAnalyzer
from stock_pool import DEFAULT_POOL    # 或自定义池

dm = DataManager()

def scan_stock(code, name):
    """扫描单只，判断是否为三买候选"""
    try:
        df = dm.get_klines(code, 'daily', '2024-01-01', '2026-04-24')
        if df.empty or len(df) < 100:
            return None  # ✅ [检查点 A] 数据加载失败或不足100根K线 → 跳过
        
        klines = dm.to_json_list(df)
        analyzer = ChanLunAnalyzer('daily', min_bi_klines=5)
        analyzer.analyze(klines)
        
        if len(analyzer.zhongshus) == 0 or len(analyzer.bis) < 5:
            return None  # ✅ [检查点 B] 无中枢或笔数不足5 → 非缠论可分析标的
        
        latest_zs = analyzer.zhongshus[-1]
        current_price = float(df.iloc[-1]['close'])
        current_date = str(df.iloc[-1]['date'])
        
        # 找最后一笔向上突破中枢的笔
        up_bis_after_zs = [b for b in analyzer.bis 
                          if b.direction == 'up' 
                          and b.start_date >= latest_zs.start_date
                          and b.end_price > latest_zs.zg * 1.01]
        
        if not up_bis_after_zs:
            return None  # 无有效突破
        
        last_up = up_bis_after_zs[-1]
        
        # 检查最近是否有向下笔
        recent_down = [b for b in analyzer.bis 
                      if b.direction == 'down' 
                      and b.start_date >= last_up.start_date]
        
        # 判断结构
        if not recent_down:
            pattern_type = "突破未回踩"
            score = 1 if current_price > latest_zs.zg else 0
        else:
            last_down = recent_down[-1]
            down_low = min(last_down.start_price, last_down.end_price)
            
            if down_low > latest_zs.zg:
                pattern_type = "三买已确认"
                score = 5
                # 检查最新笔方向
                latest_bi = sorted(analyzer.bis, key=lambda b: b.end_date)[-1]
                if latest_bi.direction == 'down':
                    pattern_type += "(回踩进行中)"
                else:
                    pattern_type += "(反弹中)"
            elif down_low > latest_zs.zd:
                pattern_type = "回踩进中枢"
                score = 2
            else:
                pattern_type = "跌破中枢"
                score = 0
        
        # 安全垫（现价距ZG的%)
        safety_margin = (current_price - latest_zs.zg) / latest_zs.zg * 100
        # ✅ [检查点 C] 安全垫 < 5% 但 score=5 才标注高风险，须人工复核
        
        # 最近买点信息
        buy_points = [p for p in analyzer.buy_sell_points if p.type == 'buy']
        latest_buy = buy_points[-1] if buy_points else None
        
        return {
            'code': code, 'name': name,
            'price': current_price, 'date': current_date,
            'pattern': pattern_type, 'score': score,
            'zg': round(latest_zs.zg, 2), 'zd': round(latest_zs.zd, 2),
            'up_high': round(last_up.end_price, 2),
            'safety_margin': round(safety_margin, 2),
            'latest_buy_date': latest_buy.date[:10] if latest_buy else '',
            'latest_buy_price': round(latest_buy.price, 2) if latest_buy else 0,
            'total_bis': len(analyzer.bis),
            'total_zs': len(analyzer.zhongshus),
        }
    except Exception as e:
        return None

|# === 主流程 ===
|results = [r for i, (code, name) in enumerate(DEFAULT_POOL) 
|           if (r := scan_stock(code, name))]
|# ✅ [检查点 D] 扫描完成：结果数=len(results)/总股票数=len(DEFAULT_POOL)，有效比例应>10%

# 按分数排序
def sort_key(r):
    type_prio = {'三买已确认(反弹中)': 1, '三买已确认(回踩进行中)': 2,
                 '突破未回踩': 3, '回踩进中枢': 4, '跌破中枢': 5}
    return (type_prio.get(r['pattern'], 9), -abs(r['safety_margin']))

results.sort(key=sort_key)

# 输出三买候选
candidates = [r for r in results if r['score'] >= 3]
watch = [r for r in results if r['score'] == 1]
```

## 输出格式规范

按分数排序后，分成两个区域输出：

### 🎯 三买候选区 (score >= 3)

列：代码、名称、模式、现价、中枢[ZG~ZD]、突破高点、安全垫、最近买点

```python
print(f"  {r['code']:<8} {r['name']:<10} {r['pattern']:<20} "
      f"¥{r['price']:<6.2f} [{r['zg']:.2f}~{r['zd']:.2f}] "
      f"¥{r['up_high']:<6.2f} {r['safety_margin']:+.2f}% "
      f"{r['latest_buy_date']}@{r['latest_buy_price']:.2f}")
```

### 👀 观察池 (score = 1，突破未回踩)

列：代码、名称、现价、中枢上沿、突破高、距ZG%、最近买点

## Excel 输出模块（openpyxl）

扫描结果可导出为带格式的 Excel 文件，含两个 Sheet：

### 三买候选 Sheet（19只候选）

| 字段 | 说明 |
|:---|:---|
| 代码/名称/现价/模式 | 基础信息 |
| 安全垫% | 条件格式：>10%绿底（安全但等待期长），<5%无着色（接近买入） |
| ZG/ZD/突破高点 | 结构关键位 |
| 最近买点日期/价 | 距买点天数 >999 显示 N/A |
| 笔数/中枢数/B3信号数/S3信号数 | 全量统计 |

### 观察池 Sheet（前50只）

突破未回踩类型，额外增加「距ZG%」列。

### 添加方式

在扫描主流程末尾追加以下代码：

```python
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

excel_path = '/path/to/output.xlsx'
wb = openpyxl.Workbook()

# === Sheet 1: 三买候选 ===
ws = wb.active
ws.title = "三买候选"

headers = ['代码', '名称', '现价', '模式', '安全垫%', '中枢上沿(ZG)', '中枢下沿(ZD)', 
           '突破高点', '最近买点日期', '最近买点价', '距买点天数', '笔数', '中枢数',
           '过去B3信号', '过去S3信号']
header_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
header_font = Font(bold=True, color='FFFFFF', size=11)

for col, h in enumerate(headers, 1):
    cell = ws.cell(row=1, column=col, value=h)
    cell.fill = header_fill
    cell.font = header_font
    cell.alignment = Alignment(horizontal='center', vertical='center')

for row_idx, r in enumerate(candidates, 2):
    ws.cell(row=row_idx, column=1, value=r['code'])
    ws.cell(row=row_idx, column=2, value=r['name'])
    ws.cell(row=row_idx, column=3, value=r['price']).number_format = '#,##0.00'
    ws.cell(row=row_idx, column=4, value=r['pattern'])
    sm = ws.cell(row=row_idx, column=5, value=r['safety_margin'])
    sm.number_format = '+0.00%'
    if r['safety_margin'] > 10:
        sm.fill = PatternFill(start_color='C6EFCE', end_color='C6EFCE', fill_type='solid')
    ws.cell(row=row_idx, column=6, value=r['zg']).number_format = '#,##0.00'
    ws.cell(row=row_idx, column=7, value=r['zd']).number_format = '#,##0.00'
    ws.cell(row=row_idx, column=8, value=r['up_high']).number_format = '#,##0.00'
    ws.cell(row=row_idx, column=9, value=r['latest_buy_date'])
    ws.cell(row=row_idx, column=10, value=r['latest_buy_price'] if r['latest_buy_price'] else '').number_format = '#,##0.00'
    ws.cell(row=row_idx, column=11, value=r['days_since_last_buy'] if r.get('days_since_last_buy', 999) < 999 else 'N/A')
    ws.cell(row=row_idx, column=12, value=r['total_bis'])
    ws.cell(row=row_idx, column=13, value=r['total_zs'])
    ws.cell(row=row_idx, column=14, value=r.get('total_buy3', 0))
    ws.cell(row=row_idx, column=15, value=r.get('total_sell3', 0))

# 列宽
col_widths = [8, 10, 8, 22, 10, 12, 12, 10, 14, 12, 12, 6, 6, 10, 10]
for i, w in enumerate(col_widths, 1):
    ws.column_dimensions[get_column_letter(i)].width = w

# === Sheet 2: 观察池(前50) ===
ws2 = wb.create_sheet("观察池(前50)")
headers2 = headers[:8] + ['距ZG%', '最近买点日期', '最近买点价']
for col, h in enumerate(headers2, 1):
    cell = ws2.cell(row=1, column=col, value=h)
    cell.fill = header_fill
    cell.font = header_font
    cell.alignment = Alignment(horizontal='center')

for row_idx, r in enumerate(watch[:50], 2):
    ws2.cell(row=row_idx, column=1, value=r['code'])
    ws2.cell(row=row_idx, column=2, value=r['name'])
    ws2.cell(row=row_idx, column=3, value=r['price']).number_format = '#,##0.00'
    ws2.cell(row=row_idx, column=4, value=r['pattern'])
    gap = (r['price'] - r['zg']) / r['zg'] * 100
    ws2.cell(row=row_idx, column=5, value=round(gap, 2)).number_format = '+0.00%'
    ws2.cell(row=row_idx, column=6, value=r['zg']).number_format = '#,##0.00'
    ws2.cell(row=row_idx, column=7, value=r['zd']).number_format = '#,##0.00'
    ws2.cell(row=row_idx, column=8, value=r['up_high']).number_format = '#,##0.00'
    ws2.cell(row=row_idx, column=9, value=round(gap, 2)).number_format = '+0.00%'
    ws2.cell(row=row_idx, column=10, value=r['latest_buy_date'])
    ws2.cell(row=row_idx, column=11, value=r['latest_buy_price'] if r['latest_buy_price'] else '').number_format = '#,##0.00'

wb.save(excel_path)
```

## JSON 中间缓存（推荐）

在扫描主流程末尾加 JSON 保存，这样 Excel 生成失败时数据不丢，也方便后续调试：

```python
import json
json_path = '/path/to/scan_results.json'
with open(json_path, 'w') as f:
    json.dump({'candidates': candidates, 'all_valid': results, 'watch': watch}, 
              f, ensure_ascii=False, indent=2)
# ✅ [检查点 E] JSON 缓存写入验证：文件存在 + json.load 可正常解析
```

## 末笔延伸(_extend_last_bi) 对候选数的影响

在 `generate_analysis.py` 中实现的 `_extend_last_bi` 会将末笔的终点延伸到后续K线突破的最极端位置（下跌笔见更低低点、上涨笔见更高高点）。这会影响三买扫描结果：

- **上涨笔延伸** → 末笔向上突破更高，更容易满足 `end_price > ZG * 1.01` 条件 → **增加候选数**
- **下跌笔延伸** → 回踩低点更低，可能跌破ZG → **减少候选数**
- **实测效果**（510只）：无 `_extend_last_bi` 时19只候选，有 `_extend_last_bi` 时31只候选（+63%）

如果使用时发现候选数明显变化，先检查 `_extend_last_bi` 是否开启。可临时打补丁做对照实验：

```python
import generate_analysis as ga
_original = ga.ChanLunAnalyzer._extend_last_bi
ga.ChanLunAnalyzer._extend_last_bi = lambda self, mk: None  # 禁用
# ... 扫描 ...
ga.ChanLunAnalyzer._extend_last_bi = _original  # 还原
```

## 实战验证要点

### 反转后买点 vs 标准三买：代码路径差异

标准三买在 `scan_stock()` 主流程中直接检测（score 5），而"反转后三买"走的是
`_detect_post_reversal_buy()` 函数的 3c 分支，**仅在标准买点被窗口/价格惩罚筛掉后才触发**，
且阈值更严格（REV_SCORE_THRESHOLD=4 vs SCORE_THRESHOLD=3）。

详见 `references/reversal-buy-detection-logic.md`。

### 技术满分 + 基本面拉胯 → 谨慎对待

scanner 的 Phase 1 纯看缠论结构，tech_score 可以打 100。但 Phase 2 的 fund_score
如果低于 50（如 ROE<5%、亏损行业），composite 会被拖到 B 级。

**实战建议**：对 fund_score < 40 的标的，即使 tech_score 满分，也应降级或剔除。
当前系统不自动做这个过滤——需要人工判断或在 pool_screener 后处理中加条件。

### 回踩幅度的安全垫计算

"三买形成中"标注时，安全垫 = (现价 - ZG) / ZG。回踩越深，安全垫越薄：
- 5% 回踩 → 安全垫约 5-7%（还能接受）
- 6%+ 回踩 → 安全垫可能 < 5%（风险较高，继续回踩可能跌入中枢失效）

**⚠️ 2026-05-31 审计发现（P1-4）**：三买形成中的评分上限偏松——深回踩 5% 给 score=5（与标准三买已确认相同分数）。但此时"回踩未成笔"，可能继续深跌。建议将三买形成中的满分上限降为 4 分，标准三买(已确认)保持 5 分。

**⚠️ 2026-05-31 审计发现（P0-1）**：三买的 Phase 2 技术评分存在结构评分反转——`validate_tech_score.py` 对三买在中枢下沿时仍给"安全边际高(+15)"的评价，但实际上三买跌回中枢下沿意味着突破失败、结构已坏。该问题已有 zs_break_penalty 做部分补偿但不完整。详见 `chanlun-quant-system` skill 的 `references/audit-session-2026-05-31.md`。

## 类一买信号泛滥的风险与处理

**背景**：类一买（盘整底背驰）在以下场景可能大量触发，但股价并不反转：
- **箱体震荡**：连续下跌笔MACD面积逐次缩小，但股价在区间内反复
- **趋势下跌中的中枢下移**：中枢ZD逐次降低，本质是趋势延续而非盘整

**识别方法**：如果一次扫描中类一买候选占比超过50%，应警惕以下两种情况：

| 情形 | 中枢ZD趋势 | 类一买可靠性 | 处理建议 |
|------|-----------|------------|---------|
| 真盘整 | ZD基本持平 | 中等（可参考） | 结合基本面+资金面过滤 |
| 假盘整（趋势下跌） | ZD逐次降低 | 低（应降级或剔除） | 中枢下移的类一买直接剔除 |

**代码层面的防护**（建议在pool_screener后处理中加）：
```python
# 检查最近3个中枢的ZD是否在下移
if '类一买' in buy_type:
    zs_list = analyzer.zhongshus[-3:] if len(analyzer.zhongshus) >= 3 else analyzer.zhongshus
    if len(zs_list) >= 2:
        zd_trend = [float(z.zd) for z in zs_list]
        if zd_trend[-1] < zd_trend[0] * 0.97:  # 中枢下移>3% = 趋势下跌
            score = max(1, score - 2)
            pattern = f"类一买(中枢下移,降级)"
```

**2026-05-29审计相关bug**：
- P0：一买进入段取最后中枢而非第一个中枢 → 大量盘整背驰被误判为趋势一买（已修复）
- P1：类一买代理confirmed判断用`!=`而非`not in` → 类一买获得与标准一买相同高分（已修复）
- 修复后类一买信号应明显减少，但仍需关注中枢趋势

**实战经验**：用户报告A500选股中大量出现类一买信号，担心类似同仁堂2025年9月后的连续下跌反复触发买点。经代码分析确认担心合理——在中枢下移的趋势下跌中，类一买条件全部可以满足但股价不反转。建议对类一买候选增加"中枢不下移"的前置过滤。

## 类一买信号的"趋势下跌 vs 正常回调"过滤（v3.6，2026-05-30）

### 问题

类一买（盘整底背驰）信号在趋势下跌股票中会反复触发（如同仁堂2025年9月后：中枢ZD 47→41→35→38→36→33→31→28，每次下跌笔MACD面积都缩小，但股价从未反转）。如果不加过滤，投资者会越抄越亏。

### 修复方案（已落地 pool_scanner.py）

在 `_detect_panbei_divergence()` 和 `scan_stock()` 两处增加"中枢趋势检查"：

```python
# 判断标准（两个条件同时满足才认定为趋势下跌）：
# ① 最近3个中枢ZD连续下移（每个都比前一个低）
# ② 整体趋势下降（最近5个中枢ZD下降超过5%）
consecutive_down = all(zd_values[i] > zd_values[i+1] for i in range(len(zd_values)-1))
lookback = min(5, len(zhongshus))
trend_zd = [float(z.zd) for z in zhongshus[-lookback:]]
overall_down = trend_zd[-1] < trend_zd[0] * 0.95
if consecutive_down and overall_down:
    zs_downgrade = True  # 降级
```

### 演进过程（重要教训）

- **第一版**：只看最后3个中枢ZD `zd_values[-1] < zd_values[0]` → 误伤艾力斯（ZD 37→55→75→92→103→89，涨了3倍后回调14%被误判为趋势下跌）
- **第二版**：加"连续下移"+"整体趋势下降"两个条件 → 精准区分

### 区分标准

| 场景 | 中枢ZD模式 | 判定 | score |
|------|-----------|------|-------|
| 同仁堂（趋势下跌）| 47→41→35→38→36→33→31→28 | 中枢下移，降级 | 1-2 |
| 艾力斯（正常回调）| 37→55→75→92→103→89 | 整体上涨，不降级 | 3-4 |
| 泸州老窖（趋势下跌）| 147→161→119→127→108→117→107→131→119→103 | 中枢下移，降级 | 1-2 |

### 评分体系（v3.6调整）

| 信号类型 | 旧评分 | 新评分 | 理由 |
|----------|-------|--------|------|
| 趋势一买（趋势背驰）| 5 | 5 | 最可靠 |
| 类一买（盘整，无中枢下移）| 5/4/3 | 4/3/2 | 可靠性低于趋势背驰 |
| 类一买（中枢下移）| 5/4/3 | 2/1/0 | 趋势下跌，大幅降级 |

## 已知注意事项

1. **数据源**：默认用 Baostock 前复权日线，数据起始至少 2 年（推荐 2024-01-01 起）
2. **[P0] 中枢扩展算法bug（2026-06-01修复）**：`_find_zhongshus()` 原扩展条件过于宽松——一笔"穿透中枢"的离开笔(high≥ZG且low≤ZD)被错误地纳入原中枢扩展，导致漏掉下方新形成的中枢。此bug直接影响所有买点判定：
   - **一买**：参考中枢错误（可能用上层中枢而非最后一个中枢），导致背驰段计算错误
   - **三买**：突破的参考中枢位置错误
   - **中枢下移检测**：趋势下跌中的中枢下移被误判为中枢扩展，低估下跌烈度
   
   生产代码 `pool_scanner.py` 和 `generate_analysis.py` 已修复（穿透笔检查：`next_high >= zg and next_low <= zd → break`）。
   详见 `chanlun-quant-system` skill 的 `references/zhongshu-extension-bug-2026-06-01.md`。
   
3. **扫描速度与性能**：
   - 使用 `ChanLunAnalyzer` 直接扫描（仅日线）：510只约3-5分钟 ✅
   - ⚠️ 切勿使用 `RecursiveTimingSystem.run_full_analysis()` 做全量扫描
   - **JSON优先、Excel后生原则（防超时）**：先保存 JSON 缓存，再从缓存生成 Excel，避免扫描中途超时丢失全部数据
4. **WSL 文件持久性**：WSL 重启后 `/mnt/d/` 挂载点稳定，但建议每次运行前确认输出目录可写
5. **安全垫解读**：安全垫 > 10% 意味着等待期可能较长（突破后价格远离中枢，回调到位需更多时间）；安全垫 < 5% 则风险较高（股价接近中枢上沿，下探即跌破确认）

## 买点分类参考

完整的买点分类体系（标准缠论3类 + 扩展6类 + 无效3类）详见 `references/buy-point-classification.md`。

**关键区分**：
- **潜一买 vs 类一买**：潜一买有下跌趋势+背驰（confirmed=False），类一买只有盘整+力度衰减
- **反转后三买 vs 二买**：二买必须先有一买确认，反转后三买只需趋势反转确认
- **中枢下沿机会**：原名"潜在一买"，已改名避免与缠论一买混淆，score=2不进候选池
- **上涨趋势过滤**：最近5个中枢ZD上涨超过10% → 类一买/潜一买不触发（score=0）

## 演进：已扩展为 A500 全买点筛选系统

本 skill 的 `scan_stock()` 模板已被 `pool_scanner.py` 扩展，支持：
- 全买点类型（一买/二买/三买 + 潜在买点）
- 三买形成中（突破后回踩未成笔）检测
- 价格距离惩罚
- 分批执行 + Baostock 限流对策

详见 `chanlun-a500-screener` skill。本 skill 保留作为三买专项扫描的参考实现。

## 参考文件

- `references/a500-pipeline-notes.md` — A500 选股流水线实现笔记
- `references/reversal-buy-detection-logic.md` — 反转后买点检测代码逻辑详解
- `references/quasi-first-buy-signal-flooding-analysis.md` — 类一买信号泛滥问题分析（箱体震荡/趋势下跌中的误触发风险、区分方法、建议修改）
- `references/dbhub-sql-screening-patterns.md` — DBHub SQL + Python 混合筛选模式

## DBHub + Python 混合筛选工作流

当需要通过 dbhub 查询 **日线底分型 + 放量 > 5日均量×1.5**，再验证 **30分钟三买** 时，数据跨两张覆盖范围不同的表（日线515只 vs 30分钟48只），需要分层策略：

```
Step 1: 查日线表 → 识别底分型 + 放量候选（SQL）
Step 2: 检查30分钟股票覆盖 → 确认候选都在30分钟表里
Step 3: 无 30分钟数据的候选 → 标为"仅日线可用"
Step 4: 有 30分钟数据的候选 → Python直连SQLite做三买分析
```

### 核心发现

- **30分钟表仅包含48只大盘股**，大部分日线底分型候选不在其中。此时需要在25只以内的交集池中搜索，或接受"仅日线信号"作为次优方案。
- **最新日期不一致**：日线数据可能更新到最近交易日（如2026-05-18），但30分钟数据只到2-3天前（如2026-05-15），导致最新交易日的底分型候选无法验证30分钟结构。
- 六只日线最新数据到2026-05-18的股票（002415,600298,600872,600887,601155,688036）值得优先关注，它们最接近当前行情。

### 常见陷阱

1. **DBHub 子查询+LIMIT陷阱**：WHERE volume > (SELECT AVG(volume) FROM (SELECT ... LIMIT 5)) 在DBHub中返回空结果。必须用 CTE + GROUP BY 预计算。
2. **30分钟三买严格版本罕见**：在48只有限样本中，严格版三买（回踩不碰ZG）几乎不可见。实用策略是接受"类三买"（回踩稍进中枢但反弹确认），判定为偏二买结构。
3. **日期对齐**：日线和30分钟的 last bar 可能差1-2个交易日，取30分钟最近120根K线时注意去除未闭合K线。

详见 `references/dbhub-sql-screening-patterns.md`。

---

## 变更日志 (CHANGELOG)

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| 1.0.0 | 2026-06-02 | 初始正式版。补全 frontmatter（category/tags/version/author/created/updated）、新增前置依赖节、关键步骤加检查点（流水线3处 + 代码模板5处）、修复已知注意事项编号错乱、补充缺失项描述、新增 CHANGELOG 节 |
| v3.6 | 2026-05-30 | 修复类一买信号质量问题：盘整背驰评分从 5/4/3 降为 4/3/2；新增中枢下移检测，趋势下跌中的类一买进一步降级为 2/1/0 |
| v3.5 | 2026-05-20 | 扩展为 A500 全买点筛选系统，新增 pool_scanner.py / pool_screener.py 生产脚本 |
| v2.0 | — | 从纯三买扩展为全买点类型扫描 |
| v1.0 | — | 初始版本：三买专项扫描
