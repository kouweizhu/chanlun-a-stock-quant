# 系统架构优化路线图（2026-05-14 审计发现）

> 状态：P0-P4 **全部完成**（2026-05-14 当天实施完毕）

## 概述

2026-05-14 对个股三维分析 + A500 选股系统做全链路 token 效率与通信效率审计。以下为审计发现和优化建议，按收益/成本排序。**这些是改进方向，非当前必须执行的步骤。** 实现前需验证各优化不影响现有功能正确性。

---

## P0: SKILL.md 瘦身（压缩 Prompt 最大收益）

### 当前问题

`stock-analysis` SKILL.md 约 50KB / ~12,500 tokens，每次分析全量注入 system prompt。其中：

| 内容分类 | 占比 | 实际每次分析是否用到 |
|:---------|:---:|:-------------------:|
| 核心流程 + 命令表 | ~5% | ✅ 是 |
| 评分规则表 | ~8% | ✅ 是 |
| 30分钟分析完整代码模板（40+行 × 3个） | ~15% | ❌ 仅在用户问\"看30分钟\"时 |
| 段级别信号分析（完整代码+3个表格） | ~12% | ❌ 仅在用户问段级别时 |
| 历史变更记录（v2.0→v4.2.3） | ~10% | ❌ 仅开发/排查需要 |
| 回退方案/故障排查 | ~8% | ❌ 仅异常时 |
| 路径清理/SQLite 用法/APPENDIX | ~10% | ❌ 仅维护需要 |
| 其他（注释/空白/格式） | ~32% | — |

**核心问题**: **~87% 的内容在 87% 的分析中完全没用**，却每次都占用 10,000+ tokens。

### 改造方向

**分层拆分**: SKILL.md 只保留高频使用的内容（~5-8KB），低频/边缘情况内容按需加载。

```markdown
SKILL.md（精简版, ~5KB）
├── 核心流程图（1图）
├── 脚本速查表（1表，即现有「极简速查」表）
├── Step 2.1 并行命令（5行命令，现有）
├── Step 2.4 评分规则表（2-3表，必用）
├── Step 4 概率分类 + Step 5 加权计算
├── 否决检查表
├── 输出报告格式要求（简短）
└── → 低频内容引用 references/ 目录

references/30min-analysis-pattern.md          ← 原 Step 2.4b 完整代码
references/segment-level-sb1-detection.md     ← 原 Step 2.4c 段分析
references/eastmoney-api-usage.md             ← 原 Step 2.9 回退方案
references/quarterly-report-commentary-template.md
references/news-api-output-schema.md
references/fundamental-trend-analysis.md
references/yi-mai-signal-handling.md
references/architecture-optimization-roadmap.md ← 本文件
```

### 工作流程变化

**改造后**，Agent 的调用路径:

```
1. 加载精简 SKILL.md（~5KB → ~1,200 tokens）
2. 按速查表跑 5 并行命令
3. 如果用户问"看看30分钟" → skill_view(references/30min-analysis-pattern.md)
4. 如果同花顺 API 异常 → skill_view(references/eastmoney-api-usage.md)
5. 其他情况不加载任何 additional references
```

### 预期收益

| 项目 | 优化前 | 优化后 |
|:-----|:------:|:------:|
| SKILL.md 进 context | ~12,000 tokens | ~1,200 tokens |
| 额外 references 加载 | 0（已在 SKILL.md 内嵌） | ~1,000-3,000 tokens（按需） |
| **单次分析省 tokens** | — | **~9,000-10,000 tokens** |

---

## P1: 合并 5 子进程为统一分析脚本

### 当前问题

每次个股分析启动 **5 个独立 Python 子进程**:

```
quick_chanlun.py     ──→ 冷启动 ~0.4s + 模块导入 ~0.3s + Baostock 登录 ~0.2s
hithink_fundamental.py ─→ 冷启动 ~0.3s + 模块导入 ~0.2s
news_detail_report.py  ─→ 冷启动 ~0.3s + urllib/json ~0.15s
check_negative_news.py ─→ 冷启动 ~0.3s + urllib/json ~0.15s
quick_html.py          ─→ 冷启动 ~0.4s + 模块导入 ~0.3s
                                          ───────────────────
                          纯浪费: ~2.0-2.8s
```

**每个进程**独立冷启动 Python 解释器、重复导入 pandas/numpy/baostock、独立 Baostock 登录。

### 改造方向

写一个 `single_stock_analysis.py`，内部依次调用各模块，一次性输出完整 JSON:

```python
# single_stock_analysis.py --code 600872 --name "中炬高新"
# --json-only: 只输出合并 JSON 到 stdout
# --report:    额外生成 markdown 报告（用 Jinja2 模板）

def main():
    code = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else ""
    
    result = {}
    result['chanlun'] = run_chanlun(code)
    result['fundamental'] = run_fundamental(code)
    result['news'] = run_news_detail(code, name)
    result['negative'] = run_check_negative(code, name)
    result['html_path'] = run_html_report(code)
    result['composite'] = compute_scores(result)
    
    print(json.dumps(result))  # 仅一次 stdout
```

### 预期收益

| 项目 | 优化前 | 优化后 |
|:-----|:------:|:------:|
| Python 冷启动 | 5 次 (~1.5s) | **1 次 (~0.3s)** |
| Baostock 登录 | 5 次 | **1 次** |
| pandas/numpy 导入 | 各 3-5 次 | **各 1 次** |
| Agent manage 并行等待 | 5 process.wait | **1 个** |

**额外收益**：`generate_report.py` 已通过 `import stock_db` 直接写入 SQLite，替换了 `terminal("python stock_db.py write '...'")` 子进程模式，再省 1 次子进程 + 1 次 JSON 序列化。

---

## P2: 报告生成 Python 化（Jinja2 模板）

### 当前问题

Agent 在 Step 7 中手动做以下工作：
1. 从 JSON 读取各维度数据（~4,000-6,000 tokens 进 context）
2. 按规则计算综合评分（虽然 composite_scorer.py 已算好，Agent 仍做一遍）
3. 用 markdown 逐格拼接表格
4. 生成 7 指标季报评估表

**这些都是模板化工作，LLM 来做既慢又贵。**

### 改造方向

在 `single_stock_analysis.py` 中集成 Jinja2 报告生成:

```bash
python single_stock_analysis.py --code 600872 --name "中炬高新" --report
```

- `--report` 参数输出完整 markdown 报告文件
- 模板: `templates/stock_report.md.j2`
- 模板循环生成财务趋势表、季报表、评分明细表
- 模板中只保留 **{$ veto_check $}** 标记给 Agent 做最终否决检查
- 自动写 stock_db + 保存到 D:\

### 预期收益

| Agent 工作项 | 当前 tokens | 优化后 tokens |
|:-------------|:----------:|:-------------:|
| JSON 解读 | ~5,000 | ~200（仅检查开关） |
| 评分计算推理 | ~1,500 | 0（Python 预计算） |
| 报告排版（表格+格式） | ~2,000 | 0（Jinja2 模板） |
| stock_db 子进程 | ~500 | 0（模块内联调用） |
| **合计** | **~9,000** | **~200** |

Agent 角色从「分析师+编辑」变为「审核员+决策者」，回归 skill 定义的「只做分发、汇总、决策」。

---

## P3: A500 Phase 2 线程池化

### 当前问题

`pool_screener.py` Phase 2 遍历 ~168 只候选股，全部单进程顺序执行:

```
for candidate in candidates:
    # API 调用: AKShare (~1s) → Baostock (~0.3s) → 研报 (~0.5s) → 同花顺新闻 (~2s)
    # 全部 I/O 等待串行，~4s/只
    # ~168 × ~4s = ~11min
```

### 改造方向

用 `ThreadPoolExecutor` 替换 API 调用的串行等待:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def process_candidate(c):
    code, name = c['code'], c['name']
    # ... 现有的三维评分逻辑 ...
    return result

with ThreadPoolExecutor(max_workers=8) as pool:
    futures = {pool.submit(process_candidate, c): c for c in candidates}
    for f in as_completed(futures):
        batch_output.append(f.result())
```

### 注意事项

- **Baostock session**: Baostock 的 connection 不是线程安全的。每个线程需要独立 `bs.login()`/`bs.logout()`，或使用 `threading.Lock` 保护共享连接。推荐每个线程独立登录（登录开销很小，~0.1s）。
- **结果顺序**: `as_completed` 不保证顺序。Phase 3 报告生成需要按综合分排序，所以顺序不影响最终输出。
- **API 限流**: 同花顺 API 可能有 QPS 限制。8 线程可能触发限流，需要观察。如果触发，降到 4 线程或加 `time.sleep(random.uniform(0.1, 0.5))`。

### 预期收益

| 指标 | 串行 | 8 线程 |
|:-----|:----:|:------:|
| 单只耗时 | ~3-4s | ~3-4s（相同，仅 I/O 重叠） |
| ~168 只总耗时 | ~8-11min | **~1.5-2min** |
| API 调用总数 | 相同 | **相同**（无额外开销） |

---

## P4: 减少 JSON 流入 Context

### 当前问题

5 个脚本的输出 JSON 共 ~16-32KB 全部流入 Agent context。Agent 逐字段读取后做评分计算。

### 改造方向（与 P1/P2 联动）

当 P1（合并脚本）和 P2（Jinja2 模板）实现后，Agent 不再需要读取原始 JSON:

```python
# 当前: Agent 读 JSON → 手动处理
json_data = terminal("python quick_chanlun.py {code}").output
# ... Agent 做 ~1,500 tokens 的推理来解读 ...

# 优化后: Python 预处理完毕，Agent 只需要读摘要
terminal("python single_stock_analysis.py --code {code} --name {name}")
# 本地输出报告文件，Agent 只读文件路径+一句话结论
```

Agent 的 Input 从 JSON 原始数据变为 Python 已经算好的「决策简报」:
- 综合评分 + 各维度分（已算好加权）
- 否决检查结果（Python 预检查，Agent 复审）
- 核心矛盾（Python 提取矛盾字段，Agent 做综合判断）

---

## P5: 内存+SQLite 写入去冗余

### 当前问题

每次分析后 **3 个独立的持久化操作**:
1. SQLite 写入（stock_db.py write）
2. Hermes memory 写入（指针，80 字）
3. Markdown 报告保存到 D:\

1 和 2 记录的信息几乎完全重叠。Hermes memory 空间极其有限（2200 chars）。

### 改造方向

SQLite 已经是完整的分析历史数据库。memory 只需要留一条指针:

```
{股票}({代码}) 最近分析: {日期} 综合{分} | stock_db trend {代码}
```

**不再**在 memory 中存评分变化趋势——SQLite 的趋势输出 (`stock_db.py trend`) 比 memory 更完整。

### 历史清理

SQLite 中不需要清理旧记录（无空间限制）。如果某只股票记录过多（>50 条），可用 `stock_db.py clean {代码} 20` 保留最近 20 条。

---

## 优先级路线图

```
第一优先（文本重组，0 代码改动，收益最大）
  └── ✅ 拆分 SKILL.md 为精简版+references
       → 每次分析省 ~10,000 tokens

第二优先（~80 行胶水脚本，不改现有模块）
  └── ✅ single_stock_analysis.py 合并 5 子进程
       → 子进程从 5 次变 1 次，省 ~2s + 1 次 Baostock 登录

第三优先（Jinja2 模板，替换 LLM 排版）
  └── ✅ generate_report.py 用模板生成 markdown
       → Agent 从报告编辑中解放，省 ~8,000 tokens/次

第四优先（Python concurrent.futures，标准库）
  └── ✅ pool_screener.py ThreadPoolExecutor
       → A500 Phase 2 从 ~8-11min 缩到 ~1.5-2min
```

**所有优化原则**: 不删功能、不改现有模块核心逻辑、不降低分析质量。只改变编排层（Agent ↔ 脚本 之间的通信方式）和表示层（SKILL.md 的组织方式）。
