# 笔延伸日期与买卖点同步问题（v3.5.5 已修复）

## 问题描述（v3.5.4 折中方案遗祸）

`_extend_last_bi()` 的 v3.5.4 设计（只延伸价格，不延伸日期）导致**时空错配**：

```
笔[56]: 末笔向下，原始端点 @2026-05-22 ¥32.57（底分型确认）
延伸后: end_price=¥31.61（来自05-28的最低点），但 end_date 保持 05-22

→ 一买检测: point_date=leave_bi.end_date=05-22, point_price=leave_bi.end_price=31.61
→ 报告显示: "一买 @2026-05-22 ¥31.61"
→ 但 05-22 的实际最低价是 ¥32.57，¥31.61 是 05-28 才到的
→ 用户看到"一买后价格创新低"的困惑
```

## v3.5.5 修复（两步完成，2026-05-29）

### 第一步：延伸日期+价格同步

**文件**：`generate_analysis.py:_extend_last_bi()`

```python
# v3.5.4（折中方案）：
# 只延伸价格，不延伸日期
last_bi.end_price = new_low

# v3.5.5（修复）：
# 笔延伸后同步更新日期和价格
last_bi.end_date = new_date
last_bi.end_price = new_low
```

同时修改了上涨笔的对称分支。

### 第二步：增设潜在一买机制

**文件**：`generate_analysis.py:_find_first_class_points()`

在背驰条件满足创建一买点时，检查是否存在向上一笔启动确认：

```python
# 检查一买是否被向上一笔确认
_has_up_after = any(
    b.direction == 'up' and b.start_date > point_date
    for b in self.bis
)
if _has_up_after:
    # 确认一买：趋势背驰 + 向上一笔验证
    _reason = f'一类买点：下跌趋势背驰（面积:{enter_area:.2f}>{leave_area:.2f}）'
    _confirmed = True
else:
    # 潜在一买：背驰条件满足但无向上一笔确认
    _reason = f'潜在一买：背驰条件满足，等待向上一笔确认（面积:{enter_area:.2f}>{leave_area:.2f}）'
    _confirmed = False
```

#### HTML 渲染区分

| 类型 | 散点图 | 信号列表标签 | 透明度 |
|:----:|:------:|:-----------:|:------:|
| 确认一买 | 黄色实心 pin 图标 | B1 | 100% |
| 潜在一买 | 半透明空心 circle 图标 | 潜B1 | 55% |

**修改文件**：`generate_analysis.py` 的 `generate_html()` 方法
- CSS：新增 `.signal-item.potential { opacity: 0.55; }`
- JS 散点图：`confirmedBuys` / `potentialBuys` 分两个 series 渲染
- JS 信号列表：`isPotential` 判断添加"潜"前缀 + 半透明类

#### BuySellPoint 类新增字段

```python
@dataclass
class BuySellPoint:
    type: str
    level: int
    date: str
    price: float
    reason: str
    confirmed: bool = True  # v3.5.5: 一买是否有向上一笔确认（False=潜在一买）
```

`calibrated_points` 序列化时自动携带 `confirmed` 字段到 HTML JSON。

### 中国太保验证结果

```
改前: 一买 @2026-05-22 ¥31.61  ← ¥31.61是5/28才到的价
改后: 潜在一买 @2026-05-28 ¥31.61  ✓ 日期价格一致，confirmed=False

背驰: 进入段面积 12.33 → 离开段面积 6.42（原4.62，多5根K线）
背驰比: 52.1%，仍显著背驰 ✓
HTML: 显示为空心圈+潜B1标签，信号列表"潜B1" 透明度55%
```

## 中枢 end_date 影响追踪（完整链）

执行顺序在 `analyze()` 中：

```
line 78: self.bis = self._find_bis(...)            → 笔创建
line 79: self._extend_last_bi(merged)               → ★ 笔延伸（改后同步延伸日期+价格）
line 80: self.zhongshus = self._find_zhongshus(bis) → 中枢计算在延伸之后
```

**关键：中枢计算使用的是延伸后的笔。** 中枢[9].end_date 从 05-22 → 05-28，但：

| 下游消费点 | 读什么 | 实际影响 |
|-----------|:------:|:--------:|
| `_identify_trends()` | 只读 ZG/ZD | **0 影响** |
| `_find_first_class_points()` line 548（反向中枢检查） | `z.start_date >= last_zs.end_date` | 中枢[9]是最后一个中枢，后面没新中枢。**无实际改变** |
| `_find_first_class_points()` line 561（离开段筛选） | `b.end_date >= last_zs.end_date` | 阈值 05-22→05-28，唯一候选笔[56]的 end_date=05-28，**结果集不变** |
| `_find_third_class_points()` line 827 | `b.end_date >= zs.end_date` | 笔[56]是下跌笔，不触发三买逻辑。**0 影响** |
| `_check_first_buy_structure()` | 只用 zs.start_date | **0 影响** |
| K线计数过滤 line 282 | `merged_count` 多5根 | **更宽松**，非破坏性 |

**结论：中枢 end_date 的同步变动不会产生任何逻辑破坏。**

## 潜在一买的缠论理论基础

缠论原文中一买的五步确认条件：

| 步骤 | 条件 | 系统支持 |
|:----:|------|:--------:|
| ① | 趋势结构完整（≥2中枢，ZG下移） | ✅ _identify_trends() |
| ② | 离开段MACD背驰（显著缩小） | ✅ _check_first_buy_structure() |
| ③ | 离开段端点出现有效底分型 | ⚠ 笔延伸后分型已被破坏 |
| ④ | 底分型后K线不跌破该分型低点 | ⚠ 待后续K线验证 |
| ⑤ | 后续确认出现向上一笔（最终确认） | ✅ **v3.5.5 新增** |

**v3.5.5 新增了第⑤步检查**：存在向上一笔 → 确认一买；不存在 → 潜在一买。

## 关联问题

1. **与 divergence_threshold 的关系**：独立问题。即使 threshold=0.7，中国太保的一买仍需要潜在一买机制。
2. **与二买分型回溯的关系**：二买有独立的 v3.5.3 分型回溯逻辑，不依赖此改动。
3. **与三买的关系**：三买不依赖末笔端点，不受影响。
4. **与 segment_analyzer 的关系**：段级别有独立的 `_extend_last_bi`，不联动。

## 改动汇总

| 改动 | 文件 | 位置 | 代码量 |
|:----:|:----:|:----:|:------:|
| 末笔延伸同步 end_date | generate_analysis.py | _extend_last_bi() L245, L255 | +2行 |
| 向上一笔确认检查 | generate_analysis.py | _find_first_class_points() | +8行 |
| BuySellPoint.confirmed 字段 | generate_analysis.py | BuySellPoint 类 | +1行 |
| 序列化确认状态 | generate_analysis.py | generate_html() calibrated_points | +1行 |
| HTML散点图区分布 | generate_analysis.py | JS 渲染 | +8行 |
| HTML信号列表区分布 | generate_analysis.py | JS 渲染 + CSS | +4行 |