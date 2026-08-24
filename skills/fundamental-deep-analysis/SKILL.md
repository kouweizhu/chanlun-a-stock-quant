---
name: fundamental-deep-analysis
description: A股个股深度基本面分析——四层框架（行业→公司→财务→估值），基于价值投资方法论，生成结构化深度研究报告。手动触发，不自动联动选股系统。
category: trading
tags: [a-stock, fundamental-analysis, value-investing, deep-research]
version: 1.0.0
---

# A股个股深度基本面分析

对单只A股进行系统化的深度基本面研究，输出结构化分析报告。

## 📦 前置依赖

在执行本技能之前，请确保以下环境就绪：

### Python 环境
- **Python 3.8+** 可用（确认：`python --version`）
- 安装数据采集依赖：
  ```bash
  pip install akshare baostock
  ```

### API Keys（可选，用于增强搜索）
- **Tavily API Key**（免费额度）：可配置于环境变量 `TAVILY_API_KEY`，用于定性信息的高质量搜索
- **Bing Search API Key**：可选，用于中文搜索降级备用
- **东方财富妙想大模型**：无需额外配置，脚本内嵌默认 Key

> 以上依赖缺失时，技能仍可降级运行：跳过定性搜索，仅基于免费的 AKShare + Baostock 数据完成结构化分析。

## 触发方式

用户在对话中说以下任意一句即触发：
- "深度分析XX的基本面"
- "基本面深度分析XX"
- "分析一下XX的基本面情况"
- "帮我看一下XX这家公司"
- 或类似的表达

## 执行流程

### 阶段 0：数据收集

**数据源优先级更新（2026-05-10）**：

经过同花顺与东方财富双平台对比分析，新增数据源选择策略：

```
首选：免费结构化数据源
    ├→ 同花顺(iwencai)：精准搜索、研报、公告、财务数据验证
    ├→ 东方财富(mx-skills)：行业分析、投资决策、组合管理建议  
    └→ AKShare/Baostock：股价、历史PE/PB、基本财务数据

次选：自然语言搜索平台（定性信息）
    ├→ Tavily：综合信息、负面消息、管理层评估
    ├→ DuckDuckGo：补充搜索
    └→ Metaso：备用搜索
```

**关键发现**：
1. **同花顺强项**：数据精确度、搜索能力、内容广度
2. **东方财富强项**：分析深度、投资决策支持、综合框架
3. **本地免费数据源**：AKShare/Baostock，稳定可用，适合基础数据

**最新推荐流程**：
- 财务数据获取 → 首选同花顺，次选东方财富
- 行业竞争分析 → 首选东方财富行业报告，次选Web搜索
- 投资决策支持 → 东方财富mx-financial-assistant综合分析入口
- 数据交叉验证 → 双平台对比，增强置信度

#### 步骤 0.1：基本面数据（Python 脚本，免费）

**⚠️ 不要使用 `python -c "..."` 内联模式！引号嵌套会导致语法错误。Python 语句中的引号与 shell 引号冲突时极难调试。**

正确做法：将 Python 脚本写入 `/tmp/` 临时文件，然后 `terminal(python /tmp/script.py)` 运行。

**⚠️ 务必获取最新季度数据！** 数据采集脚本中的报告期列表**必须包含当前最新已披露的季度**（如当前为2026年5月，则必须包含 `2026-03-31`）。不要假设最新是年报。如果无法确定最新报告期，先跑一次 `ak.stock_financial_abstract_ths` 取全部数据然后检查 `df['报告期'].max()` 确认最新可用季度。

AKShare + Baostock 分两个独立脚本并行执行：

**脚本1：AKShare 同花顺财务指标**（写入 `/tmp/fund_fin_{code}.py`）
```python
import akshare as ak, json
df = ak.stock_financial_abstract_ths(symbol='股票代码', indicator='按报告期')
# 提取近3年关键报告期：例如 2023-12-31, 2024-12-31, 2025-03-31, 2025-06-30, 2025-09-30, 2025-12-31
# 关键字段：净资产收益率、销售毛利率、销售净利率、
#   营业总收入同比增长率、净利润同比增长率、扣非净利润同比增长率、
#   资产负债率、流动比率、速动比率、每股经营现金流、基本每股收益、
#   每股净资产、存货周转天数、应收账款周转天数
```

**⚠️ Baostock `get_data()` 返回 DataFrame（2026-06-12 实测）**：`query_stock_basic()` 和 `query_history_k_data_plus()` 的 `get_data()` 返回的是**单行 pandas DataFrame**，不是 numpy structured array。访问方式：`d['列名'].iloc[0]`（注意 `.iloc[0]` 不能省略，否则返回 Series 而非标量）。列名通过 `d.columns` 查看。详见 `references/data-collection-pitfalls.md` 坑8b。

**脚本2：Baostock 基本信息+估值趋势**（写入 `/tmp/fund_bs_{code}.py`）
```python
import baostock as bs, json
bs.login()
# query_stock_basic(code='sh.{code}') → while r.next(): rows.append(...)
# query_history_k_data_plus('sh.{code}', 'date,close,peTTM,pbMRQ',
#   start_date='2019-09-30', end_date='2026-05-03', frequency='d', adjustflag='2')
#   → while r.next(): krows.append(...)
bs.logout()
# 计算 PE min/p25/median/p75/max/latest、PB min/p25/median/p75/max/latest
```

需获取的核心数据：
- 公司基本信息：Baostock `query_stock_basic()` → 全称、上市日期、板块
- 盈利能力：AKShare `stock_financial_abstract_ths` → ROE、毛利率、净利率
- 财务健康：AKShare 同花顺 → 资产负债率、流动比率、速动比率
- 现金流：AKShare 同花顺 → 每股经营现金流
- 成长能力：AKShare 同花顺 → 营收同比、净利润同比、扣非净利润同比
- 估值：Baostock `query_history_k_data_plus()` → PE、PB、PS 及历史分位
- 实时行情：AKShare `stock_zh_a_spot_em()` 或新浪财经 fallback

详见项目已有模块：`D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core/akshare_fundamental.py`（AKShare同花顺解析）、`quick_fundamental.py`（Baostock fallback）。

**⚠️ 数据采集常见坑点**：参见 `references/data-collection-pitfalls.md`

**⚠️ AKShare 排序陷阱（2026-05-30 实测确认）**：`stock_financial_abstract_ths` 返回的 DataFrame 默认按报告期**升序**排列（从最早的年报开始），并非最新数据在前。必须显式排序：
```python
df = df.sort_values('报告期', ascending=False)
recent = df.head(10).to_dict('records')
```
不排序会拿到2007年的历史数据，浪费一整轮API调用。

#### 步骤 0.2：定性信息（Web 搜索）

并行搜索以下主题（使用 Tavily / DuckDuckGo / Bing中文，均为免费额度内）：

```
┌─ 护城河分析: "{公司名} 竞争优势 护城河 品牌壁垒"
├─ 管理层评估: "{公司名} 管理层 减持 分红 诚信记录"
├─ 行业格局:   "{公司名} 行业竞争 市场份额 排名"
├─ 负面消息:   "{公司名} 处罚 诉讼 造假 退市风险"
└─ 研报观点:   "{公司名} 券商研报 评级 目标价"
```

**⚠️ 搜索失败处理**：若 Tavily 超限、DuckDuckGo 被墙、Bing中文研报覆盖不足，**继续执行**分析流程。在报告"置信度"章节明确标注"定性信息未获取，估值判断基于历史数据和行业对标"。详见 `references/data-collection-pitfalls.md` 坑9。

### 备选执行路径：东方财富妙想大模型（当用户要求"用东方财富"时）

当用户明确说"用东方财富"分析某只股票时，**跳过上述AKShare/Baostock数据采集流程**，改为使用东方财富妙想大模型脚本。这些脚本直接调用东方财富的 AI-SaaS 服务（ai-saas.eastmoney.com），无需安装额外数据包，WSL2 国内线路可正常访问。

#### 执行脚本

**脚本1：深度研究报告（initiation-of-coverage-or-deep-dive skill）**
```bash
cd C:/Users/13120/.agents/skills/initiation-of-coverage-or-deep-dive && \
python scripts/generate_deep_research_report.py \
  --query "深度分析{股票简称}({股票代码})，包括公司概况、业务分析、财务分析、估值分析和投资建议" \
  --output-dir "D:/常用文件/东方财富skill-分析/{股票简称}深度分析"
```
- **输出**：PDF + Word（7章完整投研报告：公司概况→行业分析→核心竞争力→财务分析→盈利预测与估值→风险提示→投资逻辑）
- **超时**：1200秒（约2-10分钟取决于妙想大模型响应），建议后台运行
- **脚本自带默认EM_API_KEY**，无需额外配置凭据

#### 输出目录规范
- 总目录：`D:/常用文件/东方财富skill-分析/`
- 子目录：`{股票简称}深度分析/`
- 必须生成 `README.md` 导航文件，列出所有产出并附快速导航摘要

#### 已知坑点
- `generate_deep_research_report.py` 无需额外配 EM_API_KEY（脚本内嵌默认Key `em_x15IY48x7JPdfGp75uBsnG5RNzeSGK8i` 可用）
- 深度报告生成需2-10分钟，务必后台运行（`background=true`）避免阻塞后续操作
- 脚本中使用的WSL绝对路径（`D:/...`）在Windows中对应 `D:\常用文件\东方财富skill-分析\`
- 详见 `references/eastmoney-scripts-workflow.md`

**✅ 检查点：数据完整性校验**
在进入阶段1之前，必须执行以下校验：

```python
if df is None or df.empty:
    print("❌ 数据收集失败：df 为空，降级到仅文本分析")
    downgrade_reason = "无可用财务数据"
elif latest_report_date is None:
    print("❌ 无法确认最新报告期，标记降级")
    downgrade_reason = "无法获取最新报告期"
else:
    from datetime import datetime, timedelta
    months_6_ago = datetime.now() - timedelta(days=180)
    report_date = datetime.strptime(str(latest_report_date), "%Y-%m-%d")
    if report_date < months_6_ago:
        print(f"⚠️ 最新财报({latest_report_date})距今超过6个月，数据可能过时，标记降级")
        downgrade_reason = f"最新财报日期{latest_report_date}距今超过6个月"
    else:
        print(f"✅ 数据有效，最新报告期: {latest_report_date}")
```

若校验失败（降级），在报告首页标注「⚠️ 数据降级」标记，并在置信度章节说明原因。

### 阶段 1：行业研究

回答三个核心问题：

**① 行业空间**
- 这个行业的市场规模有多大？
- 是增量市场还是存量市场？
- 未来3-5年增速预期？

**② 竞争格局**
- 行业集中度如何？CR3/CR5？
- 是"老大吃肉"还是"大家喝汤"？
- 头部公司份额在扩大还是缩小？
- 主要竞争手段是什么（价格战/品牌/技术）？

**③ 行业壁垒**
- 进入需要什么资质/牌照？
- 技术门槛有多高？
- 品牌积累需要多长时间？
- 政策风险有多大？（A股特别重要）

### 阶段 2：公司研究

回答三个核心问题：

**① 商业模式**（参考 `references/analysis-framework.md` 商业模式部分）
- 卖什么产品/服务？
- 怎么卖？（一次性/持续性/订阅）
- 有没有定价权？能不能提价？
- 先收钱还是先干活？（预付款 vs 应收款）
- 扩张需要大量资本投入吗？

**② 护城河分析**（参考 `references/moat-checklist.md`）
- 品牌护城河：消费者愿意为品牌付溢价吗？
- 转换成本：客户换供应商代价大吗？
- 网络效应：用户越多越值钱吗？
- 成本优势：能做到比竞争对手更便宜吗？
- 高效规模：市场容量有限，新进入者无利可图吗？
- **护城河强度评分**：强/中等/弱/无

**③ 管理层**
- 诚信记录：有没有财务造假、大股东侵占等黑历史？
- 能力：过去的战略决策是否正确？
- 激励机制：管理层利益和股东一致吗？（持股、分红、回购）
- 大股东有无频繁减持？

### 阶段 3：财务研究

**① 盈利能力（ROE 杜邦拆解）**
- ROE = 净利润率 × 资产周转率 × 权益乘数
- ROE 连续5年是否 > 15%？
- ROE 的来源是什么？高利润率型 / 高周转型 / 高杠杆型？
- 毛利率趋势？（上升/稳定/下降）→ 护城河在增强还是削弱？

**② 增长质量**

**②-0 前置检验：非经常性损益分解（关键！）**

> ⚠️ **重要**：在分析任何增长指标之前，必须先检验净利润的真实来源。**净利润大幅波动不一定等于经营恶化**。

**步骤1：提取三列关键数据**
从 AKShare 输出的 DataFrame 中取最新两个报告期的以下三列：
| 列名 | 含义 |
|------|------|
| `净利润同比增长率` | 报告期归母净利润同比 |
| `扣非净利润同比增长率` | 剔除一次性损益后的核心利润同比 |
| `营业总收入同比增长率` | 营收同比增长 |

**步骤2：计算"净利润-扣非净利润"剪刀差**
```python
gap = abs(净利润同比增速) - abs(扣非净利润同比增速)
if gap > 15:  # 剪刀差 > 15%
    ← 说明有重大非经常性损益干扰
    进入步骤3
else:
    ← 净利润和扣非在合理误差范围内，跳过步骤3
```

**步骤3：定位非经常性损益来源**
搜索以下关键词辅助定位：
- 搜索：`"{公司名} 计提 减值"` → 确认大额减值项目
- 搜索：`"{公司名} 出售 股权 投资收益"` → 资产处置收益
- 搜索：`"{公司名} 政府补贴"` → 非经常性补贴
- 回到 AKShare 数据查看是否有 `非经常性损益` 列或公告搜索

**步骤4：计算核心经营净利润**
```python
核心经营净利润 = 扣非净利润
if 非经常性项目是非现金减值:
    还原净利润 = 扣非净利润 + 减值金额  # 非现金项目不影响企业真实盈利能力
    备注: "净利润下降由XX亿减值导致，加回后核心经营利润实际约XX亿（增/减X%）"
elif 非经常性项目是资产出售收益:
    还原净利润 = 扣非净利润  # 出售收益是一次性的，不应计入核心利润
    备注: "净利润增长由XX亿资产出售收益驱动，扣非后核心增速仅X%"
```

**典型案例：** 分众传媒2025年报净利润-42.85%，扣非净利润-41.74%，剪刀差≈1%。但两个都是负数→净利暴跌是由一次性减值驱动的。搜索发现"计提数禾科技减值21.53亿"为非现金减值→还原后核心经营净利润约51亿（+8%），属于隐形的正面信号。详见 `references/nonrecurring-profit-decomposition.md`

- 收入和利润增速是否匹配？（在分解非经常性损益后判断）
- 增长是内生还是并购？（看商誉/总资产比）
- 净利润和经营现金流是否匹配？（含金量 > 80%？）

**③ 财务健康**
- 资产负债率是否过高？（制造业 > 60% 警惕）
- 有息负债率是否可控？（< 30% 为佳）
- 货币资金能否覆盖短期负债？
- 应收账款增速是否快于收入增速？
- **应收账款周转天数趋势（核心先行指标）**：从 AKShare 数据中提取连续3年的应收账款周转天数（`应收账款周转天数`字段），若持续恶化（如50天→80天→103天），即使应收/收入比尚可，也可能预示回款压力。对服务型/媒体型公司尤其重要——这经常是基本面恶化的最早信号，领先利润恶化1-2个季度。

**④ 股东回报**
- 派息率是否 > 20%？
- 分红是否持续稳定？
- 是否有股票回购？

### 阶段 4：估值研究

**① 当前估值水位**
- PE/PB 处于历史什么分位？(对比5-10年区间)
- 和同行相比是贵还是便宜？
- **机构一致预期交叉验证**：搜索 `"{公司名} 券商研报 评级"` 获取近6个月内机构预测的2026E净利润范围。将一致预期净利润代入PE计算得到**前瞻PE**。若前瞻PE比TTM PE低30%+，说明市场预期盈利将大幅修复（反之则是预期恶化）。在报告中明确标注"TTM PE vs 前瞻PE"的差异及原因。

**② PEG 估值**
- PEG = PE / 年利润增长率
- PEG < 1：低估 / PEG = 1：合理 / PEG > 1：高估

**③ 行业对标**
- 对照 `references/industry-benchmarks.md` 中的行业合理估值区间
- 消费龙头 → PE为主；金融 → PB为主；科技 → PEG为主

**④ 安全边际**
- 估算合理价值区间（简化DCF或PE区间法）
- 当前价格距离合理价值有多少折扣？
- 8折以上：可考虑 / 7折：较好 / 5折以下：非常理想

### 阶段 5：风险清单

使用 `references/fraud-detection-guide.md` 的检查清单：
- [ ] 利润含金量（经营现金流/净利润 > 80%？）
- [ ] 应收账款增速 > 收入增速？
- [ ] 存货异常增长？
- [ ] 商誉/总资产 > 30%？
- [ ] 大股东频繁减持？
- [ ] 关联交易占比过高？
- [ ] 审计意见非标？
- [ ] 行业处于周期顶点？（周期股低PE反而是危险信号）
- [ ] 护城河在削弱？（毛利率连续3年下滑）
- [ ] **收购商誉前瞻评估**（新增）：检查公司是否有正在进行的重大收购（搜索：`"{公司名} 收购 公告 商誉"`）。收购尚未完成时报表商誉/总资产看似安全，但并购完成后可能新增20%+的商誉，构成未来的减值隐患。在报告中单独列出"潜在商誉风险"。

## 输出格式

分析完成后生成 Markdown 报告，保存到：
`D:/常用文件/基本面深度分析/{股票代码}_{股票简称}_深度分析_{YYYY-MM-DD}.md`

> **路径说明**：用户指定的基本面深度分析报告专属目录为 `D:\常用文件\基本面深度分析\`。如分析包含缠论/技术面结论，额外保存一份到股票投资工作区。
> 报告保存后须记录评分快照+推理链摘要到长期记忆（AGENTS.md 维护）。

### 报告模板

使用 `templates/deep-analysis-report.md` 模板，结构如下：

```markdown
# {股票简称}({股票代码}) 基本面深度分析

> 分析日期：YYYY-MM-DD
> 数据来源：AKShare + Baostock（免费）+ Web公开信息搜索
> 最新财报数据截止：{latest_report_date}（⚠️ 若距分析日超过6个月，数据可能过时）

## 一、公司概览
- 全称 / 上市日期 / 所属板块 / 总股本 / 流通股本
- 主营业务简述

## 二、行业分析
### 行业空间
### 竞争格局
### 行业壁垒
### 政策风险
### 行业评分：X/10

## 三、公司分析
### 商业模式
### 护城河评估
### 管理层评价
### 公司评分：X/10

## 四、财务分析
### 盈利能力（ROE杜邦拆解）
### 增长质量
### 财务健康
### 股东回报
### 财务评分：X/10

## 五、估值分析
### 当前估值水位
### 历史PE/PB分位
### 行业对标
### 安全边际估算
### 估值评分：X/10

## 六、风险清单
| 风险项 | 状态 | 说明 |
|--------|:----:|------|
| 利润含金量不足 | ✅/⚠️/❌ | ... |
| ... | ... | ... |

## 七、综合结论
- 综合评级：★★★★★ (5星制)
- 核心优势：
- 主要风险：
- 合理估值区间：
- 当前操作建议：买入/观望/回避
- 置信度：高/中/低（说明不确定性来源）
```

## 数据工具映射

**核心原则：免费源优先。**

| 分析需求 | 主数据源（免费） |
|---------|---------|
| 公司基本信息 | Baostock `query_stock_basic()` |
| 总股本/流通股本 | Baostock `query_stock_industry()` |
| 盈利能力(ROE/毛利率等) | AKShare `stock_financial_abstract_ths` |
| 财务健康(负债率/流动比) | AKShare 同花顺 25项指标 |
| 成长能力(营收/利润增速) | AKShare 同花顺 |
| 估值(PE/PB/PS) | Baostock K线 / AKShare `stock_zh_a_spot_em` |
| 现金流 | AKShare 同花顺(每股经营现金流) |
| 实时行情 | AKShare `stock_zh_a_spot_em` / 新浪财经 |
| 护城河/竞争分析 | Tavily → DuckDuckGo → Bing中文 → 同花顺问财 |
| 管理层/负面消息 | Tavily → DuckDuckGo → Bing中文 → 同花顺问财 |
| 行业对比/研报 | Tavily → DuckDuckGo → Bing中文 → 同花顺问财 |

### 搜索工具降级链（重要）

定性信息搜索存在工具失效风险，必须准备降级路径：

```
首选：Tavily（质量最高，结构化输出）
    ↓ 超限报错 {"error":"This request exceeds your plan's set usage limit"}
备选1：DuckDuckGo（免费，无额度限制）
    ↓ 被墙/返回 "No results...bot detection"
备选2：Bing中文搜索（mcp_bing_cn_bing_search）
    ↓ 研报覆盖不足（常返回官网/百科）；**城市名股票几乎完全失效**（见下文坑点）
备选3：同花顺问财（iwencai）—— 需本地OpenClaw服务
    ↓ 服务未启动
备选4：基于结构化数据独立完成分析，在报告中标注"定性信息未获取"
```

**执行策略**：
- 并行尝试多个搜索工具，任一成功即停止
- 若全部搜索失败，**继续执行**分析流程，不阻塞
- 在报告"置信度"章节明确标注数据来源限制

**中文搜索特殊陷阱（2026-05-30实测）**：
- Bing中文搜索"唐山港 竞争优势"→ 返回唐山市旅游景点信息，完全无关
- **原因**：Bing中文对"A股公司名"的搜索匹配优先返回城市/地名结果，而非上市公司
- **规避**：使用"601000 唐山港股份"或"唐山港601000 年报"等包含代码的精确查询
- **备选**：直接抓取东方财富/新浪财经的公司页面（crawl4ai），而非依赖搜索引擎

## 重要原则

1. **双面呈现**：任何时候都给正面和反面逻辑
2. **区分事实与观点**：数据是事实，护城河判断是观点
3. **标注置信度**：数据可靠 → 高置信度；推断 → 中/低置信度
4. **不要编造**：拿不到的数据明确标注"未获取"，不要猜测
5. **A股特殊考虑**：政策风险、散户结构、分红真实性必须评估
6. **搜索失败不阻塞**：定性信息（研报、护城河、管理层）搜索失败时，基于结构化数据（财务+估值）独立完成分析，在报告中明确标注置信度降级原因

## 跨技能参考

### 最新季报点评格式

本技能使用 AKShare 数据管线（非 hithink_fundamental.py），但报告输出中的**最新季报点评章节格式**可复用 `stock-analysis` 技能的 `references/quarterly-report-commentary-template.md`（含7指标评估表、🟢/🟡/🔴判定规则、点评要点清单）。AKShare 数据中 `stock_financial_abstract_ths` 按报告期返回，取最新报告期即可获得季度数据。

## 与选股系统的关系

当前版本：**完全独立**。分析结果保存为本地 Markdown 报告，不自动联动 `pool_screener.py` 或 `composite_scorer.py`。

后续可扩展：将深度分析的关键结论（护城河评分、商业模式评分）作为参数传入选股系统的综合评分，实现量化+定性混合打分。

## 参考文档

- `references/analysis-framework.md` — 四层分析框架详细方法论
- `references/industry-benchmarks.md` — 各行业合理估值区间速查
- `references/moat-checklist.md` — 护城河类型评估清单
- `references/fraud-detection-guide.md` — 财务造假与陷阱识别
- `references/data-collection-pitfalls.md` — **数据采集常见坑点**（python -c引号陷阱、Baostock API、AKShare格式）
- `references/real-estate-analysis-patterns.md` — **房企专项分析框架**（国资分级、债务多层拆解、现金流质量、减值评估、土储质量、三阶段估值修复模型、执行检查清单）
- `references/gap-analysis-vs-current-system.md` — 现有系统 vs 完整框架落差分析（用于后续联动设计）
- `templates/deep-analysis-report.md` — 输出报告模板

---

## 📖 CHANGELOG

### v1.0.1（2026-06-12）

- **fix**: 坑8b 补充 Baostock `query_stock_basic` 实际列名（`code_name` 而非 `name`，仅 6 列无 industry/area）
- **fix**: 新增坑8c：Baostock `query_history_k_data_plus` 有时只返回 1 条数据，推荐用 AKShare `stock_zh_a_hist()` 替代
- **feat**: 新增 references/data-oriental-yuhong-2026-06-12.md — 东方雨虹完整数据采集记录（财务指标、行情、减持、机构预期、大宗交易），供后续分析复用

### v1.0.0（2026-06-02）

- **feat**: 首次正式版本标记
- **feat**: frontmatter 补充 tags（a-stock, fundamental-analysis, value-investing, deep-research）和 version 字段
- **feat**: 新增「📦 前置依赖」区块，明确 Python 3.8+、`pip install akshare baostock`、API Keys 要求
- **feat**: 在阶段 0 末尾新增数据完整性检查点（df 非空 + 最新报告期距今 < 6 个月校验，失败则标记降级）
- **docs**: 新增 CHANGELOG 区块，规范版本管理
