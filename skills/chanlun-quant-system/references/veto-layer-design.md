# Veto 否决层 — 设计文档

> 新增于 2026-05-24
> 文件：`chanlun_core/composite_scorer.py` + `chanlun_core/alpha_factor_filter.py`

---

## 为什么需要否决层

消息面在 4D 评分中仅占 0.10 权重，发生立案调查这类重大利空时：
- 即使 news_score=10，对 composite 的影响也只有 0.10 × (50-10) = 4 分
- 不足以阻止开仓

否决层解决了这个结构性矛盾：**日常消息用权重调节，重大利空用硬否决。**

---

## 架构

```
Pool Scanner
    ↓
alpha_factor_filter.py
    ├─ alpha_score (4 GTJA因子截面排名)
    └─ check_candidate_risks()
         ├─ 人工黑名单检查
         ├─ ST/*ST 名称检查
         └─ (可选) AKShare risk_filter 深度检查
    ↓
composite_scorer.compute_3d_score()
    ├─ apply_veto()  ← 此处执行两级否决
    │   ├─ Veto → grade=D, position=0, composite=0
    │   └─ Severe → 扣20分, max position=LIGHT
    └─ 4D 加权评分 (未触发否决时)
```

---

## 两级否决定义

### Veto（一票否决）

| 条件 | 来源 | 优先级 |
|------|------|:------:|
| 人工黑名单命中 | config.yaml manual_blacklist | 最高 |
| ST/*ST 名称 | 候选股名称字段 | 最高 |
| risk_filter 返回立案/造假等 | risk_filter.py + AKShare | 高 |
| 新闻详情匹配 veto_keywords | news_detail 文本关键词扫描 | 中 |

效果：`grade='D', position=0, composite=0`

### Severe（严重降级）

| 条件 | 来源 |
|------|------|
| risk_filter 返回非致命检查项 | risk_filter.py |
| 新闻详情匹配 severe_keywords | news_detail 文本关键词扫描 |

效果：composite 扣 20 分，`position` 上限为 LIGHT

---

## 关键词分类

配置在 `config.yaml -> scoring` 段，加载到 `config_loader.py`：

### veto_keywords（一票否决，16个）

```yaml
- 立案调查, 被立案, 证监会立案, 证监会调查
- 财务造假, 虚增收入, 虚增利润, 虚假记载
- '*ST', 退市风险警示
- 非标审计, 保留意见, 无法表示意见
- 涉嫌, 违法违规, 操纵市场
```

### severe_keywords（严重降级，12个）

```yaml
- 行政处罚, 公开谴责, 纪律处分
- 减持计划, 减持公告, 大股东减持
- 业绩预告变脸, 业绩修正, 由盈转亏
- 监管措施, 监管关注, 问询函
```

---

## 接口设计

### apply_veto() — 纯函数，无副作用

```python
def apply_veto(
    code: str = "",
    name: str = "",
    news_detail: str = "",
    risk_reasons: Optional[List[str]] = None,
    manual_blacklist: Optional[dict] = None,
) -> Optional[Score3D]:
```

返回 `Score3D`（否决）或 `None`（放行）。

### compute_3d_score() — 可选参数

```python
def compute_3d_score(
    tech_score, fund_score, alpha_score=50, news_score=50,
    code="", name="", news_detail="", risk_reasons=None, manual_blacklist=None,
) -> Score3D:
```

当传入 `code`/`name`/`news_detail`/`risk_reasons` 时自动执行否决检查。

### check_candidate_risks() — 批量检查

```python
def check_candidate_risks(
    candidates: list[dict],
    manual_blacklist: Optional[dict] = None,
) -> list[dict]:
```

给每个 candidate 附加 `veto_reasons` 和 `severe_reasons` 列表。

---

## 测试验证结果

```
正常（无否决）    → composite=79.8  grade=A  position=50%
立案调查(Veto)    → composite=0.0   grade=D  position=0%   ⛔
ST股(Veto)       → composite=0.0   grade=D  position=0%   ⛔
减持计划(降级)    → composite=59.8  grade=C  position=15%  ⚠️（原79.8-20）
risk_filter否决  → composite=0.0   grade=D  position=0%   ⛔
```

---

## 维护要点

- 关键词增加/删除：只改 `config.yaml`，不改代码
- 如需增加新的否决条件：在 `apply_veto()` 中加入新的检查分支
- 注意 `NEWS score` 和 `veto` 的关系：score 仍然反映日常消息情绪，veto 只拦截极端事件
- 关键词太多了会误伤，建议 veto_keywords 控制在 20 个以内
