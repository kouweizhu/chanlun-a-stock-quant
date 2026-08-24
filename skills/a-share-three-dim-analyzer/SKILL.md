---
name: a-share-three-dim-analyzer
description: A股三维辅助分析（筹码分布+资金面+市场情绪），基于FinGenius移植的纯数据模块，作为缠论信号的辅助过滤器。调用方式：python analyze.py <股票代码> <chip|money|sentiment|all>
category: trading
triggers:
  - "看一下.*资金面"
  - "看看.*筹码"
  - "查看.*游资"
  - "分析.*情绪"
  - "筹码分布"
  - "资金流向"
  - "市场情绪"
  - "龙虎榜"
---

# A股三维辅助分析 Skill

## 定位
本skill是**缠论量化系统的外围辅助工具**，基于FinGenius移植的三个纯数据模块，覆盖缠论未直接覆盖的维度：
- **筹码面**：筹码分布、主力成本、套牢区、买卖信号
- **资金面**：个股资金流向、龙虎榜、热门板块、主力净流入
- **情绪面**：市场涨跌比、行业广度、情绪评分（0-100）

**核心原则**：所有数据为纯计算，无LLM黑盒；仅作信号过滤器，不替代缠论决策。

## 触发条件
当用户说以下话术时自动加载：
- "看一下 688036 的资金面"
- "看看传音控股的筹码分布"
- "分析一下市场情绪"
- "查一下XX龙虎榜"
- "检查XX筹码集中度"

## 使用方式

### 命令行（在Hermes terminal中调用）
```bash
cd D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core

# 筹码分布
python analyze.py 688036 chip

# 资金面（游资/龙虎榜/资金流向）
python analyze.py 688036 money

# 市场情绪（默认参考上证指数）
python analyze.py 000001 sentiment

# 全部分析
python analyze.py 688036 all
```

### 在你的缠论Python代码中直接import
```python
from portable_chip_tool import PortableChipTool
from portable_hot_money_tool import PortableHotMoneyTool
from portable_sentiment_tool import PortableSentimentTool
```

## 工具文件清单（均在 D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core/）
| 文件 | 功能 | 数据源 |
|:-----|:-----|:-----|
| `analyze.py` | 统一入口脚本 | — |
| `portable_chip_tool.py` | 筹码分布（主力成本、套牢区、买卖信号） | AKShare stock_cyq_em |
| `portable_hot_money_tool.py` | 资金面（龙虎榜、资金流向、热门板块） | AKShare + efinance |
| `portable_sentiment_tool.py` | 市场情绪（涨跌比、广度、情绪评分） | AKShare stock_zh_a_spot_em |

## 输出格式示例

### 筹码分布
```
当前价: 57.75  |  股票: 传音控股
数据源: stock_cyq_em

💰 主力成本区: 56.59
🎯 控盘程度: 高度控盘
📉 套牢比例: 50.0%  |  抛压中等

✅ 买入信号:
   - 价格回踩主力成本线，支撑强劲
```

### 资金面
```
当前价: 57.75  |  股票: 传音控股

💸 5日主力净流入: 171061372.0
📊 龙虎榜净买入: 0.00 万
🔥 热门概念: 先进封装, 2026季报扭亏, PLC概念

✅ 信号:
   - 近5日主力净流入，资金面积极
```

### 市场情绪
```
📊 涨跌比: 1.17x  (涨2878 / 跌2461)
📈 涨停: 118 只  |  跌停: 30 只
🏭 行业广度: 1.3x  (↑280 / ↓216)

🎯 情绪评分: 49.2  (中性)
   涨跌比: 1.17x → 53.4分
   市场广度: 1.3x → 56.0分
   涨停数量: 118只 → 90分
```

## 与缠论系统的集成模式

### 三维辅助分析（本 skill，缠论外围）
\`\`\`
缠论信号（笔/段/中枢/买卖点）
       ↓
    三维验证（本skill）
       ↓
 筹码OK?  →  资金OK?  →  情绪OK?
   ↓          ↓          ↓
 全部配合 → 按计划介入
 任一预警 → 暂缓/减仓
\`\`\`

### 四维评分 Pipeline（A500 批量选股用）
A500 选股场景走独立的四维评分 Pipeline，详见 `a500-multi-factor-selection` skill:
\`\`\`
pool_scanner → alpha_factor_filter(alpha因子截面排名)
    → composite_scorer(tech/fund/alpha/news 四维)
    → rescore_news(报告生成)
\`\`\`
两者互补：本 skill 用于单只个股的深度辅助盘面分析（筹码/资金/情绪），  
四维 Pipeline 用于 A500 全池的批量综合评分（含 Alpha 因子排名和风控否决）。

## 注意事项
1. **成本**：纯本地计算，无LLM API调用费用
2. **龙虎榜**：AKShare当前版本龙虎榜接口有bug，已用营业部排行降级，部分场景可能无数据（详见 `references/akshare-quirks.md`）
3. **情绪扫描速度**：全量A股扫描约60秒（`stock_zh_a_spot_em`），可接受。仅SentimentTool涉及
4. **网络**：东方财富 push2 直连API比AKShare稳定。所有AKShare东方财富源接口可能因频次限制断开（`RemoteDisconnected`），详见 quirks 文档第7章
5. **严禁替代缠论**：本skill仅作信号过滤器，核心仓位决策仍严格按缠论规则执行
6. **价格字段陷阱**：`stock_individual_info_em` 的价格字段是 `最新` 不是 `最新价`（见 quirks 文档）
7. **市值单位**：原始值为元，`analyze.py` 已自动转换为亿
8. **第4层回退**：当所有AKShare层失败时，Agent可直接调用 `push2.eastmoney.com` HTTP API（见 quirks 文档第7章的Python代码模板，已验证可用）
9. **WSL 环境资金面回退（重要）**：在 WSL 下，AKShare 和 push2.eastmoney.com 的资金流向接口大概率全部 RemoteDisconnected（2026-06-10 多次验证）。此时**直接切 mx-data skill** 的 `get_data.py` 脚本查询资金流向，它走不同的内部 API 通道，WSL 下稳定可用。详见 `references/wsl-fund-flow-fallback.md`。查询模板：
   ```bash
   cd C:/Users/13120/.agents/skills/mx-data
   python scripts/get_data.py --query "XXX 股票代码 近5个交易日每日主力资金净流入 超大单 大单 中单 小单 DDX"
   ```
   然后用 openpyxl 解析 xlsx 输出结构化结果。本 skill 的 money 维度分析（龙虎榜、板块热度）仍可保留，但核心资金流向数据优先走 mx-data。

## 参考文档
- `references/akshare-quirks.md` — AKShare接口字段映射、龙虎榜降级链、三层回退模板、WSL2兼容矩阵
- `references/wsl-fund-flow-fallback.md` — WSL下资金流向数据回退方案：mx-data查询命令、xlsx解析模板、Sheet结构映射、多股查询防丢注意事项
