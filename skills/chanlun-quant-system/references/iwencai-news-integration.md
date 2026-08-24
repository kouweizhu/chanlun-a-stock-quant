# 同花顺问财新闻API集成 — 消息面评分升级

## 变更概要

| 项目 | 旧 | 新 |
|:----|:---|:---|
| **主数据源** | Sina (标题级) → Tavily → Metaso | 同花顺问财 OpenAPI |
| **Top30 评分=50占比** | 77% (23/30) | 7% (2/30) |
| **平均分** | ~53 | 67.4 |
| **>70分** | 0只 | 14只 |
| **请求耗时** | ~1-2s (Sina慢) | ~0.5s |
| **数据质量** | 仅标题 | 标题+200字摘要 |

## 降级链

```
L0: AKShare 公告预扫描 (免费，预筛选)
L1: 同花顺新闻搜索 (问财OpenAPI, news-search skill, 8条/股)
L1b: 同花顺公告搜索 (问财OpenAPI, announcement-search skill, 5条/股)
L2: Sina Finance (免费fallback)
L3: Tavily API (配额fallback)
L4: Metaso API (配额fallback)
Fallback: 写 .news_fallback_{code}.json + score=50
```

## 文件改动

- `pool_screener.py:scan_news()` — 在第1级位置插入同花顺API调用(约+80行)
- 同花顺失败时继续走 Sina → Tavily → Metaso 旧链路

## 验证

```bash
cd /home/zjj1990/work/chanlun_core
HOME=/home/zjj1990 IWENCAI_API_KEY="$IWENCAI_API_KEY" python3 -c "
from pool_screener import scan_news
for code, name in [('600276','恒瑞医药'), ('002027','分众传媒')]:
    score, detail = scan_news(code, name)
    print(f'{name}: score={score} | {detail}')
"
```

## 配置要求

- `IWENCAI_API_KEY` 环境变量 (设于 ~/.hermes/.env)
- 同花顺技能 `news-search` 和 `announcement-search` 已安装
  (安装于 `~/.openclaw/workspace/skills/news-search/` 等)
- 无需额外 pip 包 — 使用 Python 内置 `urllib.request`
