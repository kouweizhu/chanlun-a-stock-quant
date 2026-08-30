# Phase 3 HTML 生成统一（v3.5.6, 2026-05-29）

## 改动

`pool_screener.py` 的 `generate_html_report()` 改为优先调用 `quick_html.generate_html(code, name)`：

```python
def generate_html_report(s: dict, output_dir: str):
    # 优先用 quick_html（含潜在一买标签）
    try:
        from quick_html import generate_html as qh_generate
        qh_result = qh_generate(code, name)
        if qh_result and not qh_result.get('error'):
            src = qh_result.get('html_path', '')
            if src and os.path.exists(src):
                shutil.copy2(src, dst)
                return dst
    except Exception:
        pass
    # fallback: 用内存 analyzer
    ...
```

## 效果

- 每只股票只输出一个 `{code}_chanlun.html`（不再生成 `_chanlun_analysis.html`）
- 含 v3.5.5 潜在一买标签（空心圈+潜B1?）
- 使用 quick_html 的 RecursiveTimingSystem + SegmentChanLunAnalyzer（更详细的缠论分析）
- 全流程不需要再手动补 HTML

## 影响

- 旧 `_chanlun_analysis.html` 文件不会自动删除，需手动清理
- quick_html 方式每次生成需 6-21s/只，30只约 3-5min（可接受）
