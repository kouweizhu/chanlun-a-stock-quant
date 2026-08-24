---
name: stock-analysis
description: A股三维分析——全本地执行，无子Agent。技术面(缠论Baostock)+基本面(AKShare同花顺摘要主源,含扣非+双同比+周转天数+5年趋势;Baostock补估值分位;iwencai仅key可用时增强)+消息面(同花顺news_detail_report单票模式+check_negative_news)。v6.1：基本面数据源重构(2026-08,iwencai key失效后切零key主链)。
version: 6.1
author: Hermes Agent
created: 2026-04-27
updated: 2026-08-23
tags: [缠论, 三维分析, A股, 技术面, 基本面, 消息面]
---

# A股三维分析（v5.0 — SKILL.md 精简版）

> ⚠️ **跨系统依赖**：本技能与 `a500-screening-workflow` 共享 `generate_analysis.py`、`data_manager.py`、`composite_scorer.py`、`config_loader.py` 等核心脚本。修改共享代码前必须审计所有消费者。详见 `references/cross-system-dependency-map.md`。

**本流程不再使用 `delegate_task`。** 所有分析步骤由主Agent在本地执行。

## 极简速查 — 数据获取脚本

> 路径：`D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core/`（WSL2 原生文件系统）
> ⚠️ 一律使用绝对路径，`~` 在 background 模式下可能展开到 profile 目录。Python 代码中的 `os.path.expanduser("~")` 同样会解析到 Hermes profile 目录（如 `C:/Users/13120/.hermes/profiles/commander/`），并非用户 home。路径相关脚本应改用 `os.path.dirname(os.path.abspath(__file__))` 或硬编码绝对路径。**已修复案例**：`single_stock_analysis.py` L226 的 `_CHANLUN_CORE` → `_SCRIPT_DIR`。
> ⚠️ `quick_html.py` 输出路径仍为 `chanlun_core/reports_html/`（用于生成阶段），但**最终交付路径**已改为 `D:/常用文件/analysis_reports/{股票名}/`。生成后需复制HTML文件到目标文件夹，与Markdown报告放在一起。详见 Step 6 保存路径。

| 脚本/接口 | 功能 | 执行方式 |
|:----------|:-----|:---------|
| `quick_chanlun.py {代码}` | 缠论分析（中枢/买卖点/MACD） | 直接传代码 |
| `hithink_fundamental.py {代码}` | 基本面（v6.1 三级源：AKShare同花顺摘要主源[扣非+双同比+周转天数+5年趋势] → iwencai增强[仅key可用] → sina兜底[⚠️扣非/ROE/毛利率缺失]） | 直接传代码 |
| `news_detail_report.py --code {代码} --name {名称}` | 同花顺消息面评分（单票模式） | `--code/--name` |
| `check_negative_news.py --stocks {代码} --name {名称} --json` | 同花顺负面信号L1/L2/L3 | `--stocks/--name/--json` |
| `quick_html.py {代码}` | HTML可视化报告 | 直接传代码 |
| **新: `single_stock_analysis.py --code {代码} --name {名称}`** | **合并以上5个脚本到1次执行** | **仅限个股，ETF禁用 ⚠️** |
| *ETF标的*: `quick_chanlun.py {代码}` + 手动背驰验证（见ETF专项流程） | ETF分析总入口 | **ETF专用路径** |

## Step 1: 解析输入

- 股票名称转标准代码（"茅台"→"600519.SH"）
- 查 memory 历史记录（仅 DB 指针行），用 `stock_db.py trend {代码}` 获取完整趋势
- 查数据时效性：hithink_fundamental 自带最新报告期，无需手动修正

## Step 2: 获取数据（默认 single_stock_analysis.py --report）

这是**默认首选路径**，直接生成完整报告，Agent 只做最终决策审核，省 ~9K tokens/次。

```bash
cd D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core
python single_stock_analysis.py --code 600872 --name "中炬高新" --report
```

### 基本面数据源降级链（v6.1, 2026-08-23 重构）

`hithink_fundamental.get_fundamentals()` 内部自动三级调度，返回契约不变，Agent 无需干预：

| 级 | 数据源 | 条件 | 字段完整度 |
|:--:|--------|------|-----------|
| L1 | **AKShare 同花顺财务摘要**(`stock_financial_abstract_ths`) + Baostock(PE/PB/行业/名称) | 零 key，默认主源 | **最全**：扣非/扣非同比/营收净利同比/存货应收周转天数/流动速动比率/5年年报全字段 |
| L2 | 同花顺 query2data (`call_ithink`) | 仅 `IWENCAI_API_KEY` 已配置时作为增强 | 全 + 股息率 |
| L3 | 新浪三表 (`em_utils.sina_financial_report`) | L1+L2 双失败兜底 | ⚠️ **扣非/ROE/毛利率/流动比率恒为 None**——不可用于利润质量分析 |

判断当前走了哪级：看输出 `data_source` 字段——`akshare-ths-primary`(L1) / `hithink-finance-query`(L2) / `sina-backup`(L3, confidence=3)。报告引用基本面数据时注明来源级。

> ⚠️ **历史教训（2026-08 批次G审计）**：iwencai key 失效后旧链路每次都静默落到 sina 兜底，导致三维报告的"扣非/ROE"分析长期基于 None 空值。v6.1 起 AKShare 主源不再依赖任何 key。若报告中需要股息率（仅 L2 有），AKShare 单独接口 `ak.stock_individual_info_em` 或 Baostock 可补。

`--report` 参数效果：
1. 内部 5 线程并行获取缠论/基本面/消息面/负面/HTML 数据（13-15s）
2. 自动调 `generate_report.py` 用 Jinja2 模板渲染完整 markdown 报告
3. 同步保存到本地 + Windows 目录
4. Agent 只需读报告+做最终决策，无需手动算分/排版

> ⚠️ **缓存日期滞后陷阱 — DataManager缓存可能不含当日数据**：DataManager的Cache HIT消息显示缓存时效（如"5.2h old"），但**缓存中的实际K线数据可能只到前一个交易日**（Baostock盘后才更新）。2026-06-10中国人寿分析案例：缓存HIT(5.2h)但实际数据只到06-09——当天涨4.5%的K线不在缓存中。拿到Cache HIT后立即用`tail(1)`验证缓存中的最新日期：
> ```bash
> python -c "from data_manager import DataManager; import pandas as pd; d=DataManager(); k=d.get_klines('{代码}','daily'); print(k['date'].iloc[-1])"
> ```
> 如果最新日期不是当天（且当天已收盘≥16:00），强制用`rm -f data_cache/{代码}_daily.parquet`清除缓存后重新获取。
>
> ⚠️ **ETF代码前缀坑 — DataManager数据全部失败**：沪市ETF（如513330恒生互联网）的Baostock编码为`sh.513330`，但DataManager默认的代码前缀映射（`sz.`→深市/`sh.`→沪市）对ETF不生效，导致Baostock查询返回空结果，连锁导致efinance/AkShare也失败，写入`.source_failed_{code}_daily.flag`。遇到ETF数据全部失败时：
> ```bash
> # 确认ETF编码格式
> cd ~/work/chanlun_core && python -c "
> import baostock_utils, baostock as bs
> baostock_utils.ensure_login()
> for prefix in ['sh.', 'sz.']:
>     rs = bs.query_history_k_data_plus(prefix + '{代码}', 'date,open,high,low,close,volume',
>         start_date='2024-01-01', end_date='2026-06-01', frequency='d', adjustflag='2')
>     rows = [rs.get_row_data() for _ in iter(lambda: rs.next() if rs.next() else None, None)]
>     print(f'{prefix}: {len(rows)} rows')
> "
> ```
> 找到正确的`sh.{code}`格式后，用`quick_chanlun.py`和`quick_html.py`直接跑，不走`single_stock_analysis.py`（后者也走DataManager会同样失败）。详见 `references/baostock-etf-data-quirks.md`。
>
> ⚠️ **超时陷阱**：`single_stock_analysis.py --report` 在多买卖点/多中枢或**高笔数/高分型**的股票上可能超时（120s不够）。超时原因分三类：
> 1. **买卖点+中枢过多**：300059有47个买卖点+42个中枢（timeout=124s），JSON序列化+HTML渲染叠加耗时。
> 2. **30分钟数据获取全部失败**：珀莱雅(603605, 30点+18中枢)仍超时180s，根因是30min数据源全部耗尽（Baostock→efinance→AkShare Sina→AkShare EM均失败），留下 `.source_failed_*_30min.flag` 文件。**30min数据拉取失败是独立超时原因，与股票复杂度无关。**
> 3. **笔数/分型数过多**：海康威视(002415, 54笔+208分型, 仅9个买卖点→12中枢)超时180s，根因是Jinja2渲染 + Canvas绘制54条线段+208分型标记密集计算。**笔数>40即独立风险，与买卖点/中枢数量无关。** 详见 `references/single-stock-timeout-edge-case.md` 案例C。
>
> 4. **generate_report.py 渲染失败但JSON完整**：万科A(000002)案例中 `single_stock_analysis.py` exit 0、JSON 完整、但 .md 未生成。**根因修正(v5.3.4审计A2)**：并非 Jinja2 渲染压力——是编排器把 `/dev/stdin` 或尚未写盘的路径传给子进程，Windows 下必失败且失败被静默吞掉。已修复（先写盘再传参+失败计入errors非零退出）。历史案例中"服务器连接失败"字样是 stderr 噪音误导。若仍遇渲染失败：v5.3.4-C4 已加降级渲染兜底（md 必产出，含[⚠️降级渲染]标记+核心摘要+错误堆栈），Agent 可直接从 JSON 提取数据手动补全。
>
> **超高笔数（500+）的额外特征**：
> - `quick_chanlun.py {代码}` 本身也会超时（默认30s不够），无法通过它的输出来读取 bi_count
> - `quick_html.py` 重跑同样超时，但 pipeline 首次执行时已经生成的 HTML 文件仍然有效
> - 替代查看笔数的方法：直接读取 parquet 的行数或用 `python -c "import pandas as pd; d=pd.read_parquet('data_cache/{代码}_daily.parquet'); print(f'rows={len(d)}')"` 确认数据量
> - 笔数 > 200 时即进入极端区域，所有脚本耗时应翻倍预期
>
> **遇到超时后的高效恢复流程**：
> ① 检查是否有 `.source_failed_*_30min.flag` — 若有，说明30min数据源耗尽，直接跳过30min部分
> ② 检查 `reports_html/` 下HTML文件和 `data_cache/` 下 parquet 缓存 — 部分脚本可能已成功执行，无需重跑
> ③ 缺失的数据用 Step 2b 回退方案单独补跑（利用已有缓存，通常远快于原始执行）
>
**实测13-15s仅适用于中等复杂度+30min缓存有效的股票**。遇到超时不要无脑重试 `single_stock_analysis`，按以下流程恢复已有产出：
① 检查 `reports_html/{代码}_chanlun.html` — 若已存在，quick_html 已成功，无需重跑
② 检查 `.source_failed_{代码}_30min.flag` — 若有，说明30min所有数据源耗尽，跳过30min部分
③ 检查 `data_cache/{代码}_daily.parquet` — 若已更新，日线数据已成功
④ **快速判断超时类型**：先尝试 `quick_chanlun.py {代码}`（~5-10s）。若成功，`bi_count`若>40或`fenxing_count`>150，属于类型3/4（笔数过多），后续报告手动生成而非重跑 single_stock_analysis。**⚠️ 若 quick_chanlun.py 本身也超时**（30s不够，常见于500+笔的股票），改用替代判断：
   ```bash
   cd D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core
   python -c "
   import pandas as pd
   d = pd.read_parquet('data_cache/{代码}_daily.parquet')
   rows = len(d)
   print(f'日线数据: {rows}行')
   # 粗略估算笔数：约每20-30行1笔
   est_bi = rows / 25
   print(f'估算笔数: ~{est_bi:.0f}')
   if est_bi > 40:
       print('→ 判定为类型3/4（笔数过多），手动构建报告')
   "
   ```
   同时检查 `reports_html/{代码}_chanlun.html` 是否已存在（pipeline早期执行，往往已生成）。若有，HTML无需重跑。
⑤ 对缺失的数据用 Step 2b 回退方案单独补跑
>
> ⚠️ **quick_html.py 日期类型陷阱**：Baostock 的 date 字段可能为 `datetime.date` 对象而非字符串，导致 `strptime`、`[-5:]` 切片、JSON 序列化等操作报错。如果 `quick_html.py` 报 `'datetime.date' object is not subscriptable` 或 `Object of type date is not JSON serializable`，说明 `generate_analysis.py` 或 `segment_analyzer.py` 中的日期处理需要更新。详见 `references/baostock-date-type-migration.md`。

`--output result.json` 保存合并 JSON（供 `generate_report.py --input` 后续消费）。

### Step 2b: 回退方案（5 独立脚本，推荐并行后台执行）

当 single_stock_analysis.py 超时时，**先检查已有产出**再补跑缺失部分：

```bash
# ① 检查30min失败标志（30min数据源全部耗尽）
ls -la .source_failed_{代码}_30min.flag 2>/dev/null && echo "30min全失败"

# ② 检查HTML是否已生成（pipeline早期执行，往往已成功）
ls -la reports_html/{代码}_chanlun.html 2>/dev/null && echo "HTML已存在"

# ③ 检查parquet数据缓存
ls -la data_cache/{代码}_daily.parquet 2>/dev/null && echo "日线缓存OK"
```

**推荐方式：并行后台执行（总耗时 30-60s）**

同时启动 5 个独立脚本为 background 进程，利用 Hermes 的 `terminal(background=true)` 让它们并行运行。所有脚本同时完成通知，无需逐一等候：

```bash
# 同时启动（3-5个并行 background session）
python quick_chanlun.py {代码}           # 缠论（5-10s）
python hithink_fundamental.py {代码}     # 基本面（5-15s）
python news_detail_report.py --code {代码} --name {名称}   # 消息面（5-15s）
python check_negative_news.py --stocks {代码} --name {名称} --json  # 负面（5-15s）
python quick_html.py {代码}              # HTML报告（5-10s）
```

- 利用已有 parquet/news 缓存，通常 30-60s 全部完成
- 每个脚本应配 `notify_on_complete=true` 自动通知
- 逐脚本 poll/wait 确认结果，缺失的单独补跑

**备选方式：串行前台执行（总耗时 2-5 min）**

如果并行后台有问题（终端限制、进程数限制），改用串行逐一确认：

```bash
cd D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core
python quick_chanlun.py {代码}
python hithink_fundamental.py {代码}
python news_detail_report.py --code {代码} --name {名称}
python check_negative_news.py --stocks {代码} --name {名称} --json
python quick_html.py {代码}
```

> ⚠️ **万华化学实测（2026-07-17）**: `single_stock_analysis.py --report` 卡在数据采集完成→报告生成之间（3分钟无进程输出）。终止后按并行后台模式启动 5 个独立脚本，利用已有缓存，全部在 60s 内完成（缠论10s、基本面30s、消息面45s、负面60s、HTML 30s）。恢复效率显著优于串行重跑。

### Step 2e: ETF 分析专项流程

> 当用户输入的标的为 **ETF**（代码以5开头沪市ETF、159开头深市ETF、港股ETF等）时，**禁止**走 single_stock_analysis.py --report 路径。DataManager 的代码前缀映射对 ETF 不生效，会导致全部数据获取失败（Baostock→efinance→AkShare 链式失败）。

#### 第一步：ETF 代码前缀确认

```bash
cd D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core
python -c "
import baostock_utils, baostock as bs
baostock_utils.ensure_login()
code = '{代码}'
for prefix in ['sh.', 'sz.']:
    rs = bs.query_history_k_data_plus(prefix + code, 'date,open,high,low,close,volume',
        start_date='2024-01-01', end_date='2026-06-15', frequency='d', adjustflag='2')
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    print(f'{prefix}: {len(rows)} rows')
"
# sh.有数据 = 沪市ETF，sz.有数据 = 深市ETF
```

已知规律：沪市ETF（513xxx/518xxx等）使用 `sh.` 前缀，深市ETF（159xxx等）使用 `sz.` 前缀。

#### 第二步：数据获取（ETF 专用路径）

```bash
cd D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core

# ① 缠论分析（quick_chanlun 内部对 ETF 做了代码前缀自动识别）
python quick_chanlun.py {代码}

# ② 消息面（与个股相同）
python news_detail_report.py --code {代码} --name "{名称}"
python check_negative_news.py --stocks {代码} --name "{名称}" --json

# ③ HTML可视化
python quick_html.py {代码}
```

**不执行**（ETF无对应数据）：
- `hithink_fundamental.py` — ETF无财报/ROE/营收数据
- `single_stock_analysis.py --report` — DataManager 前缀映射会失败
- `stock_db.py` — SQLite 记录仅用于个股

> ⚠️ **数据长度陷阱（2026-06-15）**: Baostock 对部分 ETF 只有近半年数据（513330仅~105行/6个月），但日线缠论至少需要**2年数据**才能形成可靠的背驰判断。若 quick_chanlun 输出笔数 < 15 或中枢数 < 5，说明数据过短。数据不足时 get_klines 返回的尾行验证显示最新日期已接近当日，但缺少历史数据会导致买卖点/背驰判断完全错误。此时向用户说明情况，请求导出完整CSV，用「parquet缓存注入法」（references/baostock-etf-data-quirks.md）写入缓存后重跑。**数据不足时的"无背驰"结论很可能是假阴性。**

#### 第三步：手动 MACD 背驰验证（ETF 一买判断核心）

ETF 的 quick_chanlun 输出通常**无自动买点检测**（因为 ETF 的 K线形态与个股不同，买卖点算法默认不输出），一买判断必须手动计算 MACD 背驰：

```python
# 从 parquet 缓存读取日线数据
import pandas as pd, numpy as np
d = pd.read_parquet(f'data_cache/{代码}_daily.parquet')
d['date'] = pd.to_datetime(d['date'])

# 计算MACD
close = d['close'].astype(float).values
ema_fast = pd.Series(close).ewm(span=12).mean()
ema_slow = pd.Series(close).ewm(span=26).mean()
dif = (ema_fast - ema_slow).values
dea = dif.copy()
import pandas as pd_ser
dea_series = pd.Series(dif).ewm(span=9).mean()
dea = dea_series.values
macd = 2 * (dif - dea)

# 找最近两个同向向下笔（从 quick_chanlun 的 last_5_bis 中提取）
# 比较它们的 MACD 柱绝对值面积
bi_prev_mask = (d['date'] >= '{前一下跌笔起始}') & (d['date'] <= '{前一下跌笔结束}')
bi_latest_mask = (d['date'] >= '{最新下跌笔起始}') & (d['date'] <= '{最新下跌笔结束}')
area_prev = abs(macd[bi_prev_mask]).sum()
area_latest = abs(macd[bi_latest_mask]).sum()
ratio = area_latest / area_prev * 100

# 判断标准
if ratio < 40:     # 极强底背驰
elif ratio < 70:   # 底背驰确认
elif ratio < 100:  # 面积缩小但不足
else:              # 无背驰，下跌强化
```

**ETF 一买判断标准**（与个股一致）：

| 条件 | 一买成立 | 潜在一买（关注） | 未形成 |
|:----|:--------|:--------------|:------|
| MACD面积比 | <70% | 70-100% | ≥100% |
| MACD金叉 | 已出现 | 未出现 | 死叉 |
| 底分型 | 已确认 | 未确认 | 无 |
| 价格位置 | 中枢下沿下方 | 中枢下沿附近 | 大幅脱离 |

#### 第四步：ETF 维度权重调整

ETF 无基本面数据，三维框架压缩为二维：

```
ETF 综合评分 = 技术面 × 60% + 消息面 × 40%
```

规则说明：
- 基本面权重（30%）全部分配给技术面（+20%）和消息面（+10%）
- 不触发置信度锚定规则（基本面不存在）
- 锚定规则调整：技术分 < 30 → 技术面降至 40%，消息面升至 60%

#### 第五步：ETF 概率化分类

ETF 无 ROE/营收/股息率数据，概率分类依赖行业属性：

| 类型 | ETF所属行业对应 | 加分 |
|:----|:--------------|:----:|
| **成长** | 科技/医药/新能源/互联网行业ETF | +10% |
| **蓝筹** | 消费/金融/公用事业ETF | +10% |
| **周期** | 化工/地产/农牧/钢铁ETF | +10% |

兜底规则不变（任一类<10%按10%计算，归一化至100%）。

行业归属判断方法：
- 从 ETF 名称提取（如"恒生互联网"→科技/成长）
- 从同花顺新闻中提取描述（如"AI含量最高"→成长）
- 回退：沪市大盘ETF → 蓝筹 50%/成长 50%

#### 第六步：ETF 报告输出

ETF 报告省略个股专有内容：
- ❌ 5年财务趋势表（无数据）
- ❌ 最新季报点评（无数据）
- ❌ 基本面评分明细（无数据）
- ✅ 保留技术面数据（中枢/笔/买卖点/MACD/背驰分析）
- ✅ 保留消息面评分+来源透明度
- ✅ 保留概率化分类
- ✅ 保留否决检查
- ✅ 保留可执行观察清单
- ✅ ETF特有指标：规模(亿元)、份额(亿份)、近1年规模变化、日均成交额

ETF特有指标从同花顺新闻中提取（如 `同花顺新闻` 中的份额/规模信息）：

```bash
# 从 news_detail_report 输出中手动提取
python -c "
import json
with open('{输出路径}') as f:
    data = json.load(f)
# 搜索份额/规模相关行
for line in data.get('detail','').split('\n'):
    if '份额' in line or '规模' in line:
        print(line.strip())
"
```

> 完整 ETF 分析案例见 `references/etf-analysis-case-513330.md`。
> ETF 估值工作流见 `references/etf-valuation-workflow.md`（含多源数据搜索技巧和估值评价框架）。

### 提取关键数据

从 JSON 输出提取：
- **当前价**（前复权收盘价）
- **最新中枢** ZG（上沿）、ZD（下沿）
- **最近买卖点**：最近3个，含类型/级别/日期/价格/置信度
- **最近5笔**：方向/起止日期/起止价格
- **MACD状态**：dif/dea/金叉死叉/趋势
- **最新季报**：从顶层 profitability/growth/health 提取，data_date 标识报告期
- **5年财务趋势**：multi_year_data 中的年度数据

### 买卖点信号有效性校验（v5.3 新增）

> ⚠️ **关键陷阱**：`quick_chanlun.py` 的自动买卖点检测算法存在**理论误标**风险。信号在使用前必须经过缠论有效性校验，否则评分和否决决策可能建立在错误的信号基础上。

#### 一卖有效性校验

**缠论铁律：一卖（第一类卖点）必须是趋势背驰的真正终点。如果一卖后价格创出新高，则原一卖已被破坏。**

校验方法：
```bash
python -c "
import pandas as pd
d = pd.read_parquet('data_cache/{代码}_daily.parquet')
yi_mai_price = {一卖价格}  # 从 quick_chanlun 输出中提取
mask = d['date'] > '{一卖日期}'
peak_after = d[mask]['high'].max()
if peak_after > yi_mai_price * 1.005:  # 允许微小误差（前复权）
    print('一卖被破坏（后续最高 {:.2f} > {:.2f}）'.format(peak_after, yi_mai_price))
else:
    print('一卖有效')
"
```

| 结果 | 处理 |
|:----|:-----|
| 一卖后被新高 | **一卖无效**，否决机制中不使用此信号，顶部背驰惩罚不以该卖点为依据 |
| 一卖后无新高 | **一卖有效**，正常用于否决和评分 |

**实案（2026-06-12 中国中免）：** 系统标记一卖@74.06(2025-09-18)，但后续价格最高到99.81(2026-01-20)。一卖被破坏，不应作为否决依据。真正的顶部在99.81（系统未识别）。

#### 二卖有效性校验

二卖的定义是"一卖后第一次反弹不创新高"。校验二卖时需要确认：
1. **参照的一卖是否有效**（一卖未被后续新高破坏）
2. **反弹高点是否确实低于参照的一卖价格**

如果一卖已被后续新高破坏，则以此一卖为参照的二卖在理论上也有问题。此时需要：
- 寻找真正的顶部（一卖应在价格最高点附近）
- 以真正顶部为基准重新判断二卖是否成立
- 在报告中标注「系统误标一卖@XX，实际顶部在@XX，二卖概念正确」

#### 一买有效性校验

对称地，一买（第一类买点）如果被后续更低价格跌破，则原一买被破坏：
```bash
yi_mai_price = {一买价格}
low_after = d[mask]['low'].min()
if low_after < yi_mai_price * 0.995:
    print('一买被破坏（后续最低 {:.2f} < {:.2f}）'.format(low_after, yi_mai_price))
```

详见 `references/buy-sell-signal-validation.md`。

## Step 2c: 技术面手工评分速查

当 `single_stock_analysis.py --report` 超时需要手工计算技术面评分时，使用 `references/tech-scoring-quick-reference.md`。该文件包含：
- 完整加分/减分项速查表
- 盘整背驰MACD面积计算代码模板
- 买点时效性分档规则
- 快速计算Python模板（可直接复制运行）
- 运达股份实例（-21分）

### HTML报告再生判定

`quick_html.py` 使用**缓存的日线数据**渲染，耗时约5-10s。判定逻辑：

| 场景 | 是否重跑 | 原因 |
|:-----|:--------:|:-----|
| 日线parquet已更新（本次分析获取了新数据） | ✅ 重跑 | 数据变了，图需要更新 |
| HTML存在且日线parquet未变 | ❌ 复用 | 数据未变，图无需更新 |
| HTML存在但日期>7天 | ✅ 重跑 | 可能有新K线 |
| 首次分析该股票 | ✅ 必跑 | 无历史HTML |

判断命令：
```bash
# 比较HTML和parquet的修改时间
ls -la reports_html/{代码}_chanlun.html data_cache/{代码}_daily.parquet
# 如果parquet比HTML新，重跑quick_html.py
```

## ✅ 数据获取检查清单（Step 1→2 → Step 3 前置条件）

在执行 Step 3 时效性校验之前，逐项确认以下条件：

| # | 检查项 | 通过标准 | 失败处理 |
|:-:|:-------|:---------|:---------|
| 1 | 股票代码解析 | `代码.市场` 格式正确（如 600519.SH） | 检查 memory 或重新输入 |
| 2 | 历史趋势查询 | `stock_db.py trend` 返回非空结果 | 跳过历史对比，标注「首次分析」 |
| 3 | 数据获取完成 | `single_stock_analysis.py` / 5独立脚本均返回 exit 0 | 按 Step 2b 恢复流程处理 |
| 4 | 30min 数据状态 | 无 `.source_failed_*_30min.flag` | 跳过30min分析，报告中标注 |
| 5 | HTML 报告生成 | `reports_html/{代码}_chanlun.html` 存在 | 单独补跑 `quick_html.py` |
| 6 | 关键数据完整性 | 当前价、最新中枢、最近3个买卖点、MACD状态、最新季报、5年趋势 6项均已提取 | 缺失项单独补脚本 |
| 7 | 实时行情可用性 | 盘中时段获取到 Tavily/雪球实时价 | 标注「数据滞后」，使用收盘价 |

> 全部 7 项检查通过后，方可进入 Step 3 时效性校验。任一检查项失败需先处理再继续。

## Step 3: 数据时效性校验（v5.2）

> ⚠️ **重要**：Baostock 仅提供**已收盘的盘后数据**，不含当天盘中行情。如果在交易时段（工作日 9:30-15:00）分析，`quick_chanlun.py` 返回的 `current_price` 是最新日线的收盘价，可能已滞后数小时甚至一天。忽略此校验会导致报告中「当前价」与实时行情脱节。

### 校验流程

```
① 确认 Baostock 最新数据日期（从 quick_chanlun 输出 macd_status.date 查看）
② 判断是否在交易时段（周一到周五 9:30-15:00 为盘中）
③ 跨源交叉校验：用 Tavily 搜索雪球获取实时价
   tavily_search("珀莱雅 603605 股价")
   返回格式: "珀莱雅. 66.90. +5.09 +8.23%. SH603605, 06-01 13:31:51"
```

### 决策树

| Baostock最新日 | 实时价对比 | 判断 |
|:--------------|:----------|:-----|
| 昨天（盘前/盘中） | 与收盘价接近 | 数据有效，直接使用 |
| 昨天（盘中拉升） | 大幅高于收盘价 | **数据滞后**，需标注「盘中价已突破」 |
| 前天（非交易日） | 与最近收盘价接近 | 非交易日，数据正常 |
| 上周五（周一盘中） | 大幅高于上周五 | **跨周末滞后**，需标注并调整评分 |

### 滞后处理三原则

1. **报告中标注**：在技术面章节注明「当前价基于[日期]收盘数据，截至[实时日期]盘中已变动至[实时价]」
2. **评分预估**：实时价明显改变技术面结构时（如从中枢内→突破上沿），在报告中给出修正评分估算，用「~」表示估算值
3. **不重跑**：不要仅因盘中数据滞后就重跑全部分析；等收盘后 Baostock 更新再确认

### 盘中突破时的评分调整

```
示例：原技术分32（中枢中轴上方+15, MACD金叉+10, ...）
实时价67已突破ZG=62.55 → 追加突破上沿+20 → 修正技术分~52
但此修正需次日K线确认，正式评分仍以收盘价为准
```

> 详见 `references/data-freshness-verification.md`（含完整命令和交易时段判断逻辑）。

### 技术面评分 (-30 到 100)

#### 加分基础项

| 评分条件 | 分值 | 说明 |
|:--------|:----:|:-----|
| 最新一笔为向上笔 | +15 | 趋势向上 |
| 最新一笔为向下笔（不破前低） | +10 | 回调良性 |
| 中枢中轴上方（价格>中轴） | +15 | 偏强盘整 |
| 中枢中轴下方（ZD<价格<中轴） | +5 | 偏弱盘整 |
| 突破中枢上沿 | +20 | 强势突破 |
| MACD 金叉 | +10 | 动能转正 |
| MACD 趋势向上 | +10 | 动量支持 |
| MACD 死叉 | -5 | 动能偏空（不对称惩罚） |
| 近1月（≤30天）内有买点信号 | +25 | 最强买入 |
| 近3月（31-90天）内有买点信号 | +15 | 中期买入 |
| 近半年（91-180天）内有买点信号 | +8 | 远期买入 |
| >180天的买点信号 | +0 | 信号过期，不参与评分 |
| 置信度≥4 | +5 | 可靠加成 |

> ⚠️ **买点时效性（v5.0 修正）**：2026-05-14 之前所有买点统一 +25，导致青岛啤酒(600600) 2025-03-21 的三买在 2026-05-14 仍被计 25 分，技术分虚高至 100。修正后按日期距离分档计分，过期信号不参与评分。

盘整背驰检测：比较最近两个同向向下笔的MACD柱**绝对值面积**。面积比<70%（最新段仅为前一段的70%以下）→盘背确认（卖点扣分减半至-4，额外+10分）。⚠️ 两段均为负值时不可直接用代数和相除（会得到错误比值），必须用 `.abs().sum()` 取绝对值后再比较。盘背不设时效限制（结构信号不过期）。详见 `references/baostock-etf-data-quirks.md`。手动验证背驰（数据管理器+手动区间）见 `references/manual-divergence-check.md`。

#### 减分/惩罚项

| 评分条件 | 分值 | 说明 |
|:--------|:----:|:-----|
| 最新向下笔（破前低） | -3 | 恶化 |
| 跌破中枢下沿 | -8 | 三卖风险 |
| 连续3笔向下 | -5 | 持续下跌 |
| 无中枢结构 | -5 | 结构混乱 |
| 顶部背驰显现且未修复 | -8 | 背离未消化 |
| **二卖确认且价格在二卖下方运行** | **-5** | **二卖后延续下跌** |
| 无任何买点信号（含过期） | -5 | 缺乏买入依据 |
| 共振惩罚（日线卖点+30分钟卖点） | 额外-5~-10 | 多级别同步空头 |

> ⚠️ **二卖扣分条件**：适用于二卖出现（最近7-30天内）且当前价格已跌破二卖价格。如果二卖刚出现但价格仍在二卖之上，不属于此条件。二卖的有效性必须先通过"买卖点信号有效性校验"确认。

**卖点消耗规则**：卖点后出现级别更高的反向买点（或盘背确认），该卖点被消耗，扣分取消或减半。

### ⚠️ pool_scanner.py 中 pattern 与 buy_type 的区分（v3.5.6）

`pool_scanner.py` 对每个买点输出两个相关字段：
- **`pattern`** — 汇总MD表的"模式"列使用的字段，来自 `best_pattern`（如 `"一买(近期,8天前)"`）
- **`buy_type`** — 个股报告"买点类型"行的字段，来自 `buy_type_str`（如 `"一买"`）

类一买（潜在一买，`confirmed=False`）修改时必须改**两处**：
1. `buy_type_str`（L518-521）— 修改 `buy_type` 字段
2. `best_pattern`（L403）— 修改 `pattern` 字段

**只改 buy_type 不改 pattern 会导致汇总MD表"模式"列仍显示"一买"**。详见 `a500-multi-factor-selection` 的"类一买评分区分"节。

### 30分钟补充分析

当用户要求看30分钟K线时，用 `DataManager.get_klines()` 获取数据后手动计算MACD，检查：
1. 30分钟金叉/死叉
2. 底分型/顶分型（最近3根K线）
3. 近期支撑/阻力

**不参与评分公式**，仅用于报告描述。代码模板见 `references/full-reference.md`。

### 段级别信号分析

当用户问到SB1/SB2/段中枢时，运行 `test_segment_zhongshu.py {代码} 1200`。代码模板见 `references/full-reference.md`。

### 基本面评分

hithink_fundamental.py v2.0 直接输出四维度评分（各25分）+ 趋势修正因子 + 扣非净利润：

```json
"fundamental_score": {"profitability_score": 16, "growth_score": 12,
  "health_score": 20, "valuation_score": 23,
  "total_score": 71, "trend_correction": -4, "adjusted_total": 67}
```

修正后总分 `adjusted_total` 用于加权计算。明细含5年趋势数据 + 扣非利润质量。

> ⚠️ **数据格式陷阱（v5.3.4/B2 已统一）**：`multi_year_data` 与 `profitability` 顶层的比率字段现**全部为小数口径**（0.153 = 15.3%；2026-08-23 契约统一修复，此前 multi_year 曾是百分数）。展示时一律 ×100 或使用 `_fp/_qfp` 类自动检测 helper。唯一例外：`growth.revenue_yoy_pct/profit_yoy_pct` 展示键为百分点。详见 `references/hithink-decimal-format-pitfall.md`。
> 另：基本面评分函数已统一为 `quick_fundamental.calculate_fundamental_score`（健康20/估值10/键名 marginal_improvement），hithink 内旧版四维各25分实现已弃用改名 `_legacy_hithink`。
>
> ⚠️ **multi_year_data 查询陷阱**：同花顺 API 单次查询超过约20个字段会返回 status_code=-3001。必须分批查询（5次×9字段），且键名必须用精确格式 `营业收入[20221231]` 而非简写 `2022营收`。五年范围 [2021,2025]。详见 `references/multi-year-query-format-pitfall.md`。

### 消息面评分

通过 `news_detail_report.py --code --name` 获取 JSON 评分（0-100），`check_negative_news.py --json` 检查L3否决信号。

数据源（与 A500 选股共享 news_scanner.py）：全量采集（东财新闻/涨停池/雪球/同花顺/新浪/CCTV/Tavily）+ 多源加权融合评分 + LLM语义分析（需配置LLM_API_ENDPOINT/LLM_API_KEY）。

#### 评分公式（v6.0 更新）

**关键词通道（权重40%）：**

| net 范围 | 公式 | 说明 |
|:--------:|:----:|:-----|
| ≥ 4 | min(81, 50+net×6) | 大幅利好 |
| 2 ~ 3 | min(76, 50+net×5) | 明显利好 |
| 1 | min(66, 50+net×5) | 轻微利好 |
| -1 ~ -2 | max(35, 50+net×5) | 轻微利空 |
| -3 ~ -4 | max(25, 50+net×4) | 明显利空 |
| < -4 | max(15, 50+net×3) | 严重利空 |

**LLM语义通道（权重60%，可选）：**

配置环境变量 `LLM_API_ENDPOINT` + `LLM_API_KEY` 后自动启用。

`最终得分 = 0.4 × 关键词分 + 0.6 × LLM分`

未配置LLM时降级到纯关键词评分。

**数据源权重（v6.0调整）：**

| 源 | 权重 | 说明 |
|:-----|:----:|:-----|
| 东财新闻 | 1.2 | 个股新闻 |
| 同花顺公告 | 1.2 | 公告信息 |
| 同花顺新闻 | 1.0 | 新闻资讯 |
| CCTV财经 | **0.5** | 宏观/个股混合 |
| 新浪财经 | 0.8 | 财经新闻 |
| 涨停池 | 0.8 | 全市场情绪 |
| 雪球热搜 | 0.6 | 全市场情绪 |
| Tavily | 0.7 | 通用搜索 |

#### 消息明细表生成规则

在 Step 6 报告中，消息面章节必须包含 **来源数据透明度说明** 和 **消息明细表**：

1. **来源透明度**：对每个数据源，列出收到的条数，并注明多少条与个股直接相关、多少条为行业/宏观噪声（示例：`东财新闻20条 → 2条提及该股，18条为行业流动数据`）
2. **消息明细表**：只列出与个股直接相关的消息（行业/宏观噪声不列入），按时间倒序排列，表格格式为：
   ```
   | 日期 | 来源 | 倾向 | 一句话摘要 |
   |:----|:----|:----:|:----------|
   | YYYY-MM-DD | 源名 | 🟢/⚪/🔴 | 15字以内核心事实 |
   ```
3. **倾向标记**：🟢正面 ⚪中性 🔴负面（与 news_scanner 的 sentiment 字段一致）
4. **一句话摘要**：15字以内的事实陈述，不含评价性措辞
5. **被过滤的行业/宏观消息**：在表格之后用一行说明，格式为 `其余 N 条为行业/宏观新闻，与个股无关`

> ⚠️ **来源透明度铁律**：不得因某个来源的消息全部为行业噪声就跳过对该来源的说明。每条来源都必须出现在报告中，即使结果是「0条个股相关」。否则用户会质疑数据完整性。
>
> **如何判定个股相关**（`generate_report.py` 中 `extract_news_details()` 实现）：
> - `同花顺新闻` / `同花顺公告` / `Tavily` → 默认个股相关
> - `东财新闻` / `CCTV财经` / `涨停池` / `雪球热搜` → 需行内出现股票名称或代码才算个股相关
> - 报告中的消息明细表仅展示个股相关条目，行业/宏观消息在表格下方的文字说明中统一标注
> - `extract_news_details()` 参数：`stock_name` 和 `stock_code` 用于过滤。模板渲染时通过 `news.per_source_table`（来源汇总表）、`news.relevant_lines`（个股相关明细）、`news.noise_note`（说明行）传入模板。

## Step 4: 概率化分类

> ⚠️ **ETF特殊处理**（详见 Step 2e）：ETF 无 ROE/营收/股息率/市值数据，概率分类只能依赖行业归属。兜底时若无行业归属判断依据，默认成长 40%/蓝筹 40%/周期 20%。置信度锚定不触发。

| 类型 | 加分条件 | 每项加分 |
|:----|:---------|:--------:|
| **蓝筹** | 消费/金融/公用 +10%；ROE标准差<3% +15%；营收增速<10% +5%；市值>1000亿+10%；股息率>3%+15%；机构持仓>10%+10% | 累加 |
| **成长** | 科技/医药/新能源+10%；营收增速>15%+15%；ROE波动>5%+10%；市值<100亿+15%；股息率<0.5%+10% | 累加 |
| **周期** | 化工/地产/农牧/钢铁/有色+10%；营收波动大+10%；ROE交替+10% | 累加 |

兜底：任一类<10%按10%计算。归一化至100%。

> ⚠️ **股息率数据缺失处理**：`hithink_fundamental.dividend_yield` 可能为 `null`（如海康威视）。当该字段为空时，按以下顺序兜底：① 从 `check_negative_news` 或 `news_detail_report` 的搜索结果中提取股息率（如 TradingView/AASTOCKS 数据）；② 用 Tavily 搜索 "海康威视 股息率 002415" 获取；③ 仍无数据时，用同花顺问财 (iwencai) 查询。若所有兜底均失败，股息率加分项暂不计分（不加不减），在报告中标注「股息率数据缺失」。

**置信度锚定**：缠论置信度≥4且基本面置信度<4时，技术面权重+5%（对应减少基本面权重）。

## Step 5: 综合研判

### 默认权重
```python
综合评分 = 技术面×40% + 基本面×30% + 消息面×30%   # 个股
ETF 综合评分 = 技术面×60% + 消息面×40%              # ETF（无基本面）
```
锚定规则：个股技术分<30 → 技术面降至30%，基本面升至40%。ETF技术分<30 → 技术面降至40%，消息面升至60%。

### 否决机制
- 日线顶背驰+一卖 → 降级2档
  > ⚠️ **一卖有效性必须先校验**：使用此否决条件前，必须执行"买卖点信号有效性校验"确认一卖未被后续新高破坏。如果一卖已被破坏，否决条件不触发。
- ROE连降3年+负债率>70% → 降级2档（银行/保险行业酌情降1档）
- 证监会立案调查/财务造假 → 直接回避
- 消息面L3级负面 → 直接回避

### 决策矩阵（新买入决策用）

| 综合评分 | 蓝筹 | 成长 | 周期 | 建议仓位 | 止损 |
|:--------:|:----:|:----:|:----:|:--------:|:----:|
| ≥80 | 强力推荐 | 强力推荐 | 强力推荐 | 30%-50% | -5% |
| 70-80 | 推荐 | 推荐 | 推荐 | 20%-30% | 结构/硬-8% |
| 60-70 | 关注 | 关注 | 关注 | 10%-20% | -8% |
| 50-60 | 观望 | 观望 | 观望 | 0% | — |
| <50 | 回避 | 回避 | 回避 | 0% | — |

### ⚠️ 深套持仓分析（现有持仓/已深套场景，v5.8 新增）

当用户问"已持有XX深套中，建议持有/加仓/割肉"时，上述决策矩阵不直接适用。深套持仓者有特殊的心理和成本结构，需要**三叉戟决策框架**：

#### 三叉戟决策框架

| 决策方向 | 适用条件 | 依据 |
|:--------|:---------|:-----|
| **加仓** | ①综合评分≥60 ②技术面出现买点信号 ③基本面未触发否决 ④有明确的底部结构(底分型+MACD金叉+放量) | 四项全满足才考虑 |
| **持有观望** | ①未触发L3否决 ②政策面出现行业拐点 ③跌幅已超80% ④当前位置无明显加速下跌风险 | 默认选择，满足2条即可 |
| **割肉/减仓** | ①触发否决(ROE连降+高负债/L3负面/财务造假) ②持续加速下跌 ③有明确更好的替代标的 ④仓位过重影响心态 | 任一项触发即考虑 |

**深套分析的推理链修正**：
1. 先跑否决检查 — 只有触发否决才建议割肉
2. 行业周期定位 — 深套个股的复苏往往依赖行业整体回暖（政策底→市场底→业绩底的传导链条）
3. 政策面优先级提升 — 深套场景下政策面权重应提升至与基本面同等水平
4. 比较同行业标的 — 持仓内哪个更强（MACD先金叉/跌幅更小/基本面更优），优先保留强者
5. 输出建议必须包含触发条件（"若出现XX信号则转为加仓/若XX则转为割肉"），不能只给单一结论

**深套场景报告结构**（与标准报告不同）：
- ❌ 不强调"推荐仓位百分比"（已无调整空间）
- ✅ **政策面分析优先** — 行业政策拐点判断
- ✅ **否决检查重点** — 是否会退市/爆雷
- ✅ **持仓内比较** — 多只持仓时谁更强
- ✅ **触发条件清单** — 什么情况下该做何操作
- ✅ **信号验证时间线** — 预计多久能出现翻转信号

### 多股同行业分析（v5.8 新增）

当用户同时询问多只同行业股票（如两只地产股、两只白酒股）时：

1. **行业集中度警告**：同行业多只持仓放大行业风险，报告中必须标注「行业集中度风险」
2. **相对强弱对比**：在推理链中加入对比表，排序各标的的强弱
3. **综合建议优先行业配置**：如果全行业评分均<50，建议减仓而非等待个股反转
4. **比较维度**：
   - 技术面：MACD状态（谁先金叉/死叉）、中枢位置（谁离支撑更近）
   - 基本面：ROE趋势、负债率、营收下滑幅度
   - 消息面：政策受益程度排序

### 推理链必须包含
1. 权重选择依据（含置信度锚定是否触发）
2. 否决检查逐条确认
3. 评分矛盾说明（各维度方向不一致时解释原因）
4. 历史对比（有stock_db记录时输出评分变化趋势）
5. **如果是深套场景**：标注场景类型（新买入/深套持仓/多股同行业），附加三叉戟决策判断
6. **如果是多股同行业**：标注行业集中度警告和相对强弱排序

## Step 6: 输出报告

包含：评分总览（加权计算明细）+ 技术面完整数据 + 基本面评分明细 + 5年财务趋势表 + **最新季报列 + 季报点评** + 消息面评分 + 概率分类 + 否决检查 + 推理链 + 可执行观察清单 + 仓位建议

**5年财务趋势+最新季报表格式**：
```
| 指标 | 2021 | 2022 | 2023 | 2024 | 2025 | Q1 2026 | 趋势 |
|:-----|:----|:----|:----|:----|:----|:--------|:----|
| 营收(亿) | ... | ... | ... | ... | ... | ... | ↑/↓/→ |
```
年度数据来自 multi_year_data，季度数据来自顶层字段。

**必须含最新季报点评**（7指标评估表 + 核心判断一句话），模板详见 `references/full-reference.md`。

**可执行观察清单**：具体触发条件（如"若放量突破XX，上调至XX"）。

### 保存路径
- **Markdown报告**和**HTML报告**统一保存到同一文件夹：`D:/常用文件/analysis_reports/{股票名}/`
- 文件命名规范：
  - Markdown: `{股票名}_{代码}_{YYYY-MM-DD}.md`
  - HTML: `{股票名}_{代码}_{YYYY-MM-DD}_chanlun.html`
- 生成后需复制HTML文件到目标文件夹：`cp reports_html/{代码}_chanlun.html "D:/常用文件/analysis_reports/{股票名}/{股票名}_{代码}_{YYYY-MM-DD}_chanlun.html"`

> **ETF报告**：ETF的期末报告省略5年财务趋势表、最新季报点评、基本面评分明细。保留ETF特有指标（规模、份额、日均成交额）。详见 Step 2e「ETF 分析专项流程」。

### 结构化 JSON
```
{stock_code, stock_name, report_date, industry, comprehensive_score,
 stock_type_probs, reasoning_chain, veto_check, conditional_triggers,
 fund_weight_adjustments, trend_analysis, multi_year_data}
```

## 记忆管理

Hermes memory 只保留一行 DB 指针，完整历史走 SQLite：
```bash
python stock_db.py trend {代码}     # 查询历史趋势
python stock_db.py write '{JSON}'   # 写入新记录
```

> ⚠️ **stock_db.py write JSON键名必须与SQLite列名一致**（v5.4 B-07 修订）：
> 旧版本文档曾教唆错误键名 `score`/`stock_type`/`veto`/`note`——这些键会被静默写入 NULL 空壳行。
> 现在：缺 `stock_code` 直接抛 ValueError；幽灵键自动映射到正确列并 stderr 告警（过渡期兼容）。
> 正确格式：
> ```json
> {"stock_code":"300059","stock_name":"东方财富","composite_score":59.4,"tech_score":12,
>  "fund_score":93,"news_score":62,"stock_type_probs":"蓝筹52.9%","decision":"观望",
>  "veto_triggered":0,"core_conflict":"简要说明"}
> ```
> 键名必须是 `stock_code`/`stock_name`/`report_date`/`tech_score`/`fund_score`/`news_score`/`composite_score`/`decision`/`position_suggestion`/`stock_type_probs`/`veto_triggered`(整数)/`core_conflict`/`observation_points`/`report_path`。

## 前置依赖

### 运行环境
- Python 3.8+（推荐 3.10+）
- Windows 环境（原生 Python 3.12）
- `chanlun_core` 工作目录：`D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core/`

### Python 依赖
| 包名 | 用途 | 安装方式 |
|:-----|:-----|:---------|
| `baostock` | A股日线/分钟线数据 | `pip install baostock` |
| `pandas` | 数据处理 | `pip install pandas` |
| `numpy` | 数值计算 (MACD) | `pip install numpy` |
| `requests` | HTTP 请求（同花顺 API） | 内置 |
| `jinja2` | HTML 报告模板渲染 | `pip install jinja2` |
| `tavily-python` | 实时行情搜索 | `pip install tavily-python` |

### 外部数据源
- **Baostock**：盘后日线/30分钟K线（免费，需联网）
- **同花顺 hithink API**：基本面/消息面数据（内网接口）
- **Tavily**：盘中实时行情搜索（需 API Key）
- **Metaso**：消息面回退搜索源

### 引用文件
- `generate_analysis.py`、`data_manager.py`、`composite_scorer.py`、`config_loader.py` — 跨系统共享脚本，修改前须审计所有消费者
- `references/` 目录下所有文档 — 含数据格式陷阱、边界案例、历史修复记录

## 附录: 脚本路径

```bash
cd D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core
python single_stock_analysis.py --code 600872 --name "中炬高新"   # 推荐
python quick_chanlun.py 600872                                    # 单缠论
python hithink_fundamental.py 600872                             # 单基本面
python stock_db.py trend 600872                                   # 查历史
```

> 详细代码模板、30分钟分析、段级别分析、数据时效性校验、EastMoney回退方案、历史bug记录、路径清理指南、超时边缘案例、Baostock日期类型变更修复、Baostock列顺序陷阱、Baostock ETF数据前缀、MACD背驰绝对值计算、MACD背驰绝对值计算 → 见 `references/baostock-etf-data-quirks.md`；中枢扩展算法bug（P0-4）→ 见 `chanlun-quant-system`技能 `references/zhongshu-extension-bug-2026-06-01.md`（跨技能引用）。详细代码模板、30分钟分析、段级别分析、数据时效性校验、EastMoney回退方案、历史bug记录、路径清理指南、超时边缘案例、Baostock日期类型变更修复、Baostock列顺序陷阱、Baostock ETF数据前缀 → 见 `references/full-reference.md`、`references/data-freshness-verification.md`、`references/single-stock-timeout-edge-case.md`、`references/baostock-date-type-migration.md`、`references/baostock-data-column-mapping.md`、`references/baostock-etf-data-quirks.md` 及 `references/multi-year-query-format-pitfall.md`

## CHANGELOG

| 版本 | 日期 | 变更内容 |
|:----|:----|:---------|
| v6.1 | 2026-08-23 | **基本面数据源重构（iwencai key 失效应对）**：`hithink_fundamental.get_fundamentals()` 改三级调度——L1 AKShare 同花顺摘要主源（零key，字段最全：扣非/双同比/周转天数）+Baostock估值 → L2 iwencai增强（仅key配置时）→ L3 sina兜底（⚠️扣非/ROE/毛利率恒None）。返回契约不变，下游零改动。起因：key失效后旧链静默落sina，三维报告扣非分析长期空值。源自批次G审计（A500 score_report 对齐项目同期发现）。 |
| v6.0 | 2026-07-29 | 消息面评分公式更新：利好上限从75提升至81（+6），加分系数从+4/+5提升至+5/+6。启用LLM语义分析通道（0.4关键词+0.6LLM，需配置LLM_API_ENDPOINT/LLM_API_KEY）。CCTV权重从1.0降至0.5。 |
| v6.2 | 2026-07-29 | 三买 base_score 从 3 提升至 4（缠论中三买"回踩不进中枢"本身就是确认信号，无需额外30分钟确认）。三买 confidence 现在为 4~5，稳定触发 +5 加分。 |
| v5.9 | 2026-07-17 | Step 2b 回退方案改为**并行后台执行**（推荐方式），实测万华化学恢复耗时 60s vs 串行 2-5min。新增串行备选方式说明。 | 新增深套持仓分析（三叉戟决策框架：加仓/持有观望/割肉）及多股同行业对比分析；新增参考文件 `references/deep-hold-decision-framework.md`（含金地+万科实操案例） | `D:/常用文件/analysis_reports/{股票名}/`，文件命名规范更新为 `{股票名}_{代码}_{YYYY-MM-DD}_chanlun.html` |
| v5.0 | 2026-04-27 | 初始 SKILL.md 精简版，合并 5 脚本到 single_stock_analysis.py |
| v5.1 | 2026-05-14 | 买点时效性分档修正（过期信号不计分）；ETF 数据前缀坑文档化；超时恢复流程 |
| v5.6 | 2026-06-17 | 超时陷阱新增第4类：generate_report.py 渲染失败但JSON完整（万科A 530笔案例）。超高笔数(500+)场景下 quick_chanlun.py 本身也超时，新增parquet行数估算法替代判断。新增参考文档 `references/report-layer-failure-vanke-2026-06-17.md`。
| v5.4 | 2026-06-12 | 消息面：`extract_news_details(stock_name, stock_code)` 新增个股相关性自动过滤；模板新增来源汇总表（含个股相关列 + 说明行）；新增路径警告：Python `os.path.expanduser("~")` 在 Hermes 下解析到 profile 目录；修复 `single_stock_analysis.py` 用 `_SCRIPT_DIR` 替代 `_CHANLUN_CORE`。源自海康威视分析用户反馈（东财/CCTV噪声过滤+路径错误）。 |
| v5.3 | 2026-06-12 | 新增买卖点信号有效性校验（Step 2c）。新增一卖被新高破坏的校验方法和二卖参照偏移处理。新增二卖确认后追加减分-5。否决机制增加一卖有效性前置检查。新增参考文档 `buy-sell-signal-validation.md`（含中国中免误标案例）。 |
| v5.2 | 2026-06-02 | 新增数据时效性校验 Step 3 + 超时后恢复流程 v2 + 盘中突破评分修正准则 |
| v5.2-p1 | 2026-06-02 | Frontmatter 补全（author/created/updated/tags）；Step 7→6 编号修正；新增形式化检查清单（Step 1→2→3 前置条件）；新增前置依赖声明；新增 CHANGELOG 节 |
