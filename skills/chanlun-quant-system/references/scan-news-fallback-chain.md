# scan_news() 多源降级链：数据源发现与集成

> 日期: 2026-05-02 | 涉及: `pool_screener.py::scan_news()`

## 降级链 (v3.5+)

```
scan_news(code, name)
  ├─ 第0级: AKShare公告 (免费, akshare_scanner)
  ├─ 第1级: 新浪财经个股新闻 (免费, urllib直连) ← 2026-05-02新增
  ├─ 第2级: Tavily (API额度)
  ├─ 第3级: Metaso (API额度)
  └─ 写 .news_fallback 标记
```

## 新浪财经源发现过程

尝试过的方案（按时间顺序）：
1. ❌ 东方财富 `push2.eastmoney.com/api/qt/stock/news/get` → 404
2. ❌ 东方财富 `search-api.eastmoney.com` → 403
3. ❌ 东方财富 `so.eastmoney.com/news/s` → 12KB HTML但无新闻标题
4. ❌ 东方财富 `guba.eastmoney.com` → 股吧论坛，非新闻
5. ✅ 新浪财经 `vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock` → **成功**

## 新浪财经源详情

- URL 模板: `https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/{prefix}{code}.phtml`
- prefix: `sz` (0/2/3开头) or `sh` (6/9开头)
- 编码: gbk
- 页面大小: ~55KB
- 新闻条数: ~40条近期新闻

**提取逻辑**：
```python
# Step 1: 定位 datelist div
dl = re.search(r'<div class="datelist"[^>]*>(.*?)</div>', html, re.DOTALL)
raw = dl.group(1)

# Step 2: 分离提取日期和链接（比合并正则更鲁棒）
dates = re.findall(r'(\d{4}-\d{2}-\d{2})', raw)
links = re.findall(r"<a[^>]*href=['\"]([^'\"]*)['\"][^>]*>([^<]+)</a>", raw)

# Step 3: 按顺序配对（日期和链接在datelist中按出现顺序一一对应）
for i in range(min(len(dates), len(links), 10)):
    href, title = links[i]
    news_items.append(f"{dates[i]} {title.strip()}")
```

## Tavily 消耗分析

- 每轮 A500 选股: 最多 30 次 Tavily (NEWS_TOP_N=30)
- 每月 3 轮: ≈90 次
- Tavily 免费额度: 1000 次/月
- 占比: **9%** — 不高，但加新浪后降至接近 0

## 坑点

1. **不要用合并正则匹配日期+链接** — sina页面中日期和链接之间有 `&nbsp;`、空格、`<a>` 标签的各种组合，分开提取更可靠
2. **必须限在 datelist div 内提取** — 全页面搜索会匹配到导航链接和页脚链接，产生大量噪音
3. **gbk 编码** — 不是 utf-8，`decode('gbk', errors='ignore')`
4. **不需要 cookie/session** — 直接 urllib 请求即可，无反爬
