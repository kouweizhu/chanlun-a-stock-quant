# 同花顺API消息面脚本输出JSON Schema

两个消息面脚本（`news_detail_report.py` 和 `check_negative_news.py`）的单票模式均输出结构化 JSON，供主Agent解析消费。

---

## news_detail_report.py — JSON Schema

```bash
python news_detail_report.py --code 600872 --name 中炬高新
```

### 顶层字段

| 字段 | 类型 | 说明 |
|:-----|:-----|:------|
| `code` | string | 股票代码 |
| `name` | string | 股票名称 |
| `source` | string | 数据来源: "同花顺news"/"同花顺announcement"/"Tavily"/"Metaso"/"skip" |
| `score` | int | **消息面评分 0-100**（主Agent直接使用的值） |
| `reason` | string | 评分计算推理链 |
| `neg_count` | int | 负面关键词命中次数 |
| `pos_count` | int | 正面关键词命中次数 |
| `total_articles` | int | 总文章数 |
| `relevant_articles` | int | 与公司名相关的文章数 |
| `error` | string | 异常信息（空字符串=正常） |
| `articles` | array | 逐条文章详情 |

### articles[] 子字段

| 字段 | 类型 | 说明 |
|:-----|:-----|:------|
| `title` | string | 文章标题 |
| `content` | string | 摘要（前300字） |
| `url` | string | 原文链接 |
| `neg_hits` | string[] | 命中的负面关键词 |
| `pos_hits` | string[] | 命中的正面关键词 |
| `relevant` | bool | 是否提及公司名 |

### 评分计算逻辑

```
基准 = 50
net = pos_count - neg_count
net ≥ 4:  score = min(75, 50+net×5)   # 大幅利好
net ≥ 2:  score = min(70, 50+net×4)   # 明显利好
net ≥ 1:  score = min(60, 50+net×4)   # 轻微利好
net ≥ -2: score = max(35, 50+net×5)   # 轻微利空
net ≥ -4: score = max(25, 50+net×4)   # 明显利空
else:     score = max(15, 50+net×3)   # 严重利空
无结果:    score = 50                   # 中性
```

---

## check_negative_news.py — JSON Schema

```bash
python check_negative_news.py --stocks 600872 --name 中炬高新 --json
```

### 顶层字段

| 字段 | 类型 | 说明 |
|:-----|:-----|:------|
| `total_negative` | int | 负面消息总数 |
| `l3_count` | int | L3致命级（>0 → 直接回避） |
| `l2_count` | int | L2重大级（>0 → 建议处理） |
| `l1_count` | int | L1普通级 |
| `results` | array | 逐条详情 |

### results[] 子字段

| 字段 | 类型 | 说明 |
|:-----|:-----|:------|
| `symbol` | string | 股票代码 |
| `name` | string | 股票名称 |
| `level` | string | "L3"/"L2"/"L1" |
| `title` | string | 新闻标题 |
| `summary` | string | 摘要 |
| `date` | string | YYYY-MM-DD |
| `neg_hits` | string[] | 命中的负面关键词 |

### 退出码

| 退出码 | 含义 | 操作 |
|:------:|:-----|:-----|
| 0 | 无负面 | 正常 |
| 1 | L2信号 | 建议处理 |
| 2 | L3信号 | **直接回避** |

---

## 解析示例

```python
import json, subprocess

# news_detail_report
r = subprocess.run(["python", "news_detail_report.py", "--code", "600872", "--name", "中炬高新"],
    capture_output=True, text=True, cwd="D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core")
d = json.loads(r.stdout)
news_score = d["score"]

# check_negative_news
r = subprocess.run(["python", "check_negative_news.py", "--stocks", "600872", "--name", "中炬高新", "--json"],
    capture_output=True, text=True, cwd="D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core")
d = json.loads(r.stdout)
veto = d["l3_count"] > 0 or r.returncode == 2
has_l2 = d["l2_count"] > 0
```

## 常见陷阱

1. **退出码 vs JSON** — `--json` 模式同时输出 JSON 到 stdout 和设置退出码。必须两者都检查：`r.returncode==2 → L3`，同时 `d["l3_count"]>0`
2. **source="skip"** — 表示 IWENCAI_API_KEY 未设置，同花顺API不可用，需回退 Tavily
3. **空列表 ≠ 异常** — `total_articles=0, error=""` 是正常情况（搜索无结果），score=50 中性
4. **--hours 建议** — 个股分析用 `--hours 168`（7天），默认24小时可能不够