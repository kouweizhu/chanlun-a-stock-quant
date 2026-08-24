---
name: institutional-underweight-screening
description: >
  机构低配选股——基于公募基金行业配置数据，识别被机构低配的行业，在低配行业中用结构化数据筛选基本面优质个股，
  最终精选3-5只低估值+高增长标的。数据链路：iwencai快照→mx-finance-search研报→同花顺query2data结构化筛选+财务验证。
  触发词：低配行业、机构低配选股、基金低配、行业拥挤度选股
category: trading
tags: [low-allocation, undervalued, value-screening, fund-allocation]
version: 2.0.0
---

## 📦 前置依赖

| 类别 | 要求 |
|------|------|
| Python | >= 3.8 |
| API Key 环境变量 | `HITHINK_OPENAPI_KEY`（同花顺 query2data 鉴权） |
| MCP 服务 | `mx-finance-search`（研报搜索）、`iwencai-skillhub`（快照）、同花顺 `query2data` 系列 |
| 依赖 skill | `mx-finance-search`、`iwencai-skillhub`、`hithink-market-query`、`hithink-finance-query` |

> ⚠️ 使用 query2data 前确保已执行 `source ~/.bashrc` 或手动导出 `HITHINK_OPENAPI_KEY`。

# 机构低配选股 v2

## 核心逻辑

机构持仓历史低位 → 估值保护充分 → 基本面边际改善 → 催化剂来临 → 均值回归机会

## 工具链

| 步骤 | 工具 | 用途 | 数据类型 |
|------|------|------|---------|
| Step 1 | iwencai-skillhub快照 + mx-finance-search | 机构行业配置 | 快照+研报文字 |
| Step 2 | mx-finance-search | 行业基本面+催化剂 | 研报摘要 |
| Step 3 | mx-finance-search + 同花顺query2data(行业涨跌幅) | 行业深度判断 | 研报+结构化 |
| Step 4 | 同花顺query2data | 个股筛选 | 结构化API |
| Step 5 | 同花顺query2data | 财务验证 | 结构化API |

## ⚠️ 关键约束

1. **基金行业配置没有结构化API**——同花顺和东方财富都不提供。Step 1必须依赖研报数据
2. **query2data每批不超过5只股票+3个指标**，否则静默返回空
3. **申万一级行业查询经常返回0条**——用"行业涨跌幅排名"替代
4. **券商财务数据需逐只查询**——3只+9个指标的组合会返回0条
5. **字段名动态变化**——日期后缀随交易日变化，用关键词匹配而非精确字段名

---

## Step 1：机构行业配置排名

### 目标
识别哪些行业被高配（拥挤）/ 低配（冷门），输出低配TOP5+高配TOP5

### 数据来源（按优先级）

1. **iwencai-skillhub参考文件**：`~/.hermes/skills/trading/iwencai-skillhub/references/industry-allocation-patterns.md`
   - 包含"2026Q1 机构配置快照"——超配/低配比例、历史分位
   - ⚠️ 会过时，每次分析时先检查数据时效

2. **mx-finance-search搜索最新研报**（补充更新数据）：
   ```bash
   python3 {mx-finance-search-baseDir}/scripts/get_data.py \
     --query "2026年一季度 公募基金 行业配置 持仓比例 低配 高配 申万行业"
   ```

3. **同花顺query2data行业涨跌幅排名**（补充当日行业表现）：
   ```
   query: "行业涨跌幅排名"
   skill_id: hithink-market-query
   ```

### 输出格式

```
| 分类 | 行业 | 配置系数 | 持仓占比 | 历史分位 | 信号 |
|------|------|---------|---------|---------|------|
| 低配TOP5 | 非银金融 | 0.27x | 0.38% | 最低 | ... |
| 高配TOP5 | 通信 | 3.34x | 13.1% | 100% | ... |
```

### 数据溯源要求
- 每个数字必须标注来源（iwencai快照 / 研报标题 / query2data API）
- 配置系数、持仓占比来自研报文字提取，不是结构化API

### ✅ Step 1 验证
- 确认输出表格中**低配TOP5和高配TOP5均非空**
- 确认每个数字标注来源（快照/研报/API）
- 如果研报搜索返回空 → 回退仅用 iwencai 快照数据

---

## Step 2：低配行业基本面+催化剂

### 目标
逐个低配行业搜索基本面现状和催化剂，输出"体检单"

### 操作

对Step 1的低配TOP5行业，逐个用mx-finance-search搜索：

```bash
python3 {mx-finance-search-baseDir}/scripts/get_data.py --query "{行业} 2026年二季度 基本面 催化剂 投资建议"
```

### 评级框架（主观判断，非API输出）

| 评级 | 含义 | 判断标准 |
|------|------|---------|
| 🔥重点 | 有明确反转逻辑 | 配置历史最低 + 基本面边际改善 + 催化剂明确 |
| 🔵可关注 | 有关注价值但需等待 | 配置低位 + 催化剂不够明确 |
| ⚪观望 | 暂不介入 | 基本面仍在恶化 或 催化剂遥远 |
| ❌排除 | 不适合 | 行业逻辑被破坏 |

### 输出格式

```
| 行业 | 配置系数 | 催化剂 | 基本面 | 评级 |
|------|---------|--------|--------|------|
| 券商 | 0.27x | 资本市场改革+业绩高增 | 利润+14% | 🔥重点 |
```

### ✅ Step 2 验证
- 确认低配TOP5行业中**每个行业都有评级输出**（🔥重点/🔵可关注/⚪观望/❌排除）
- 确认🔥重点行业**至少有一个**，否则扩大搜索范围重新查询
- 确认每个行业判断有**研报依据**（非凭空判断）

---

## Step 3：行业深度研究

### 目标
对🔥重点行业判断"短期修复 vs 中期趋势"，确认是否值得配置

### 操作

用mx-finance-search搜索最新研报投资建议：

```bash
python3 {mx-finance-search-baseDir}/scripts/get_data.py --query "{行业} 2026年二季度 研报 投资建议 估值"
```

### 判断标准

| 时间维度 | 特征 | 配置策略 |
|---------|------|---------|
| 短期修复 | 事件驱动、超跌反弹、催化剂一次性 | 快进快出，仓位轻 |
| 中期趋势 | 政策红利、业绩拐点、行业周期反转 | 左侧布局，仓位重 |

### 合格标准
- "中期趋势"→ 进入Step 4
- "短期修复"→ 需精选个股，降低仓位
- 其他 → 排除

### ✅ Step 3 验证
- 确认🔥重点行业**已判断时间维度**（短期修复 / 中期趋势）
- 如果研报搜索返回空 → 重新搜索，换关键词（如加"行业展望"）
- 只有"中期趋势"才进入Step 4；"短期修复"需降低仓位提示

---

## Step 4：同花顺query2data个股筛选

### 目标
在合格行业中用结构化数据筛选基本面优质个股

### query模板

**券商（低配行业首选）**：
```
query: "券商股 归母净利润同比增长率 市盈率 市净率"
skill_id: hithink-finance-query
→ 返回code_count=36，按PE<15x + 净利增速>10%筛选
```

**计算机**：
```
query: "计算机 归母净利润同比增长率 市盈率 市净率"
skill_id: hithink-finance-query
→ 返回code_count=352，按PE<60x + 净利增速>15%筛选
```

**游戏（传媒子行业）**：
```
query: "游戏 市盈率 归母净利润同比增长率 股息率"
skill_id: hithink-finance-query
→ 返回code_count=24，按PE<40x筛选
```

**医药**：
```
query: "医药 创新药 归母净利润同比增长率 市盈率 市净率"
skill_id: hithink-finance-query
→ 按PE>0且<80x + 净利增速排序
```

### 筛选条件（各行业不同）

| 行业 | PE上限 | 净利增速下限 | 其他条件 |
|------|--------|-------------|---------|
| 券商 | <15x | >10% | PB<2x优先 |
| 计算机 | <60x | >15% | 毛利率>20%优先 |
| 游戏 | <40x | >0% | 股息率>2%优先 |
| 医药 | <80x | >0% | 创新药管线优先 |

### API调用注意事项

```python
# 正确：用urllib（避免curl引号嵌套）
import json, urllib.request, secrets
headers = {
    'Content-Type': 'application/json',
    'Authorization': f'Bearer {api_key}',
    'X-Claw-Call-Type': 'normal',
    'X-Claw-Skill-Id': 'hithink-finance-query',
    'X-Claw-Skill-Version': '1.0.0',
    'X-Claw-Plugin-Id': 'none',
    'X-Claw-Plugin-Version': 'none',
    'X-Claw-Trace-Id': secrets.token_hex(32),
}
payload = json.dumps({
    'query': '券商股 归母净利润同比增长率 市盈率',
    'page': '1', 'limit': '50',
    'is_cache': '1', 'expand_index': 'true'
}).encode()

# 字段名动态匹配（不要硬编码）
pe = d.get('最新市盈率ttm', d.get('市盈率(pe,ttm)[20260529]', None))
profit_chg = d.get('归母净利润同比增长率', None)
```

### 输出
每个行业筛选出3-5只，合计10-15只进入Step 5

### ✅ Step 4 验证
- 确认 query2data 返回的 `code_count` > 0
- 如果返回空 → 检查字段名是否匹配当前交易日（日期后缀），换关键词重试
- 如果所有行业都返回0只 → 回退到 Step 3 的研报推荐个股（研报中提及的标的）
- 确认筛选后合计至少5只候选，否则放宽筛选条件

---

## Step 5：同花顺query2data财务验证

### 目标
对Step 4候选池逐只查询完整财务数据，精选3-5只

### query模板

**逐只查询**（避免多只+多指标返回空）：
```
query: "{股票名} 营业收入 归母净利润 毛利率 ROE 资产负债率 经营活动产生的现金流量净额 市盈率 市净率 股息率 归母净利润同比增长率"
skill_id: hithink-finance-query
```

⚠️ **券商必须逐只查**——3只+9个指标的组合会返回0条。其他行业可以2-3只一批。

### 验证指标清单

| 维度 | 指标 | 健康标准 | 一票否决 |
|------|------|---------|---------|
| 盈利质量 | 毛利率 | >20%（券商除外） | <5% |
| 盈利质量 | ROE | >5% | <0% |
| 成长性 | 净利增速 | >10% | <-20% |
| 成长性 | 营收增速 | >0% | <-15% |
| 安全性 | 负债率 | <80%（券商除外） | >90% |
| 安全性 | 经营现金流 | >0 | 连续为负 |
| 估值 | PE | <行业中位数 | >100x |
| 股东回报 | 股息率 | >1%优先 | 0% |

### 精选标准
- 满足6/8项以上 → A级候选
- 满足4-5项 → B级观察
- 一票否决触发 → 排除

### 最终输出

```
| 排名 | 股票 | 行业 | PE | 股息率 | 核心优势 | 验证结果 |
|------|------|------|-----|--------|---------|---------|
| 1 | 华泰证券 | 券商 | 9.4x | 2.9% | PE最低+PB破净 | 8/8 ✅ |
```

### ✅ Step 5 验证
- 确认每只候选股票**至少返回了主要财务字段**（PE、净利增速、营收）
- 如果某只股票返回空 → 逐只重查，减少指标数量（最多3个指标/次）
- 确认最终精选出3-5只且有**明确的验证评分**
- 如果最终池不足3只 → 从Step 4 B级候选中补选

---

## 🟡 边界条件

| 条件 | 处理策略 |
|------|---------|
| 非交易日 / 节假日 | Step 4-5 的 query2data 可能返回空。用最近交易日数据，或切换到研报文字推荐 |
| 网络超时 / API 限流 | 每次调用间隔 >= 1s，最多重试 2 次。连续失败则跳过该步骤，用已有数据继续 |
| Step 1 研报搜索返回空 | 回退仅用 iwencai 快照行业配置数据，跳过研报补充 |
| Step 2-3 研报搜索返回空 | 换关键词重试（+行业展望/+投资建议），重复 2 次仍空则标记为"数据不足" |
| Step 4 所有行业返回 0 只 | 回退到 Step 3 研报中提及的个股作为候选池 |
| Step 5 某只股票字段为空 | 逐只重查，减少指标到 3 个；连续 3 只全空则暂停，提示检查 API KEY 有效性 |
| 字段日期后缀不匹配 | 用 `.get()` + 默认值 / 关键词匹配（如 `最新市盈率ttm` 回退给 `市盈率`） |

## 完整执行速查

```bash
# Step 1: 读iwencai快照
cat ~/.hermes/skills/trading/iwencai-skillhub/references/industry-allocation-patterns.md

# Step 1补充: 搜最新研报
python3 ~/.hermes/skills/trading/mx-finance-search/scripts/get_data.py \
  --query "2026年 公募基金 行业配置 超配低配"

# Step 2: 逐行业搜催化剂（以券商为例）
python3 ~/.hermes/skills/trading/mx-finance-search/scripts/get_data.py \
  --query "券商 2026年二季度 基本面 催化剂 投资建议"

# Step 3: 搜研报判断时间维度
python3 ~/.hermes/skills/trading/mx-finance-search/scripts/get_data.py \
  --query "券商 2026年二季度 研报 投资建议 估值修复"

# Step 4: 同花顺query2data筛选（需source ~/.bashrc加载API KEY）
# 用Python urllib调用，见上方API调用注意事项

# Step 5: 同花顺query2data逐只财务验证
# 逐只查询，见上方query模板
```

## 历史执行记录

- **2026-05-29 v2首次执行**：精选5只（华泰证券、吉比特、三七互娱、广发证券、海康威视）
  - 相比v1新增：吉比特（PE 11.8x + 股息率5.9%，v1从研报推荐中漏掉）
  - 相比v1移除：恒瑞医药（PE 41x）、中科曙光（PE 58x）——被结构化筛选淘汰

---

## 📖 CHANGELOG

| 版本 | 日期 | 变更 |
|------|------|------|
| v2.0.0 | 2026-06-02 | 新增 frontmatter category/tags/version；新增 📦 前置依赖 区块（Python 3.8+、API Key 变量名、MCP 服务）；每个 Step 后新增 ✅ 验证检查（确认返回非空+回退策略）；新增 🟡 边界条件 区块（非交易日/网络重试/Step 4 0只回退/字段适配）；新增本 📖 CHANGELOG |
| v2 | 2026-05-29 | 首次正式版本。基于机构低配逻辑全流程，精选5只标的。相比v1增加了query2data结构化筛选替代纯研报推荐 |
