"""
Alpha Zoo 测试脚本 — 从 DBHub 拉数据跑因子验证
"""
from __future__ import annotations

import sys
from pathlib import Path

# 确保能找到同目录的模块
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
from dbhub_panel import load_panel, get_stock_codes
from zoo import FACTORS, compute_all, list_factors


def main():
    # 1. 获取 DBHub 中数据最多的 30 只股票
    print("=" * 60)
    print("Alpha Zoo — DBHub 集成验证")
    print("=" * 60)

    print("\n[1/4] 扫描 DBHub 可用股票...")
    try:
        codes = get_stock_codes(limit=30, min_days=200)
    except FileNotFoundError as e:
        print(f"  ❌ {e}")
        print("  请确认 DBHub 数据库路径是否正确")
        sys.exit(1)

    print(f"  找到 {len(codes)} 只股票: {', '.join(codes[:5])}...")

    # 2. 加载近 2 年数据
    print("\n[2/4] 加载面板数据 (2023-01-01 ~ 2024-12-31)...")
    try:
        panel = load_panel(codes, "2023-01-01", "2024-12-31")
    except ValueError as e:
        print(f"  ❌ {e}")
        sys.exit(1)

    print(f"  日期范围: {panel['close'].index[0].date()} ~ {panel['close'].index[-1].date()}")
    print(f"  天数: {len(panel['close'])}")
    print(f"  股票数: {panel['close'].shape[1]}")
    print(f"  面板形状: {panel['close'].shape}")

    for col in panel:
        print(f"    {col}: {panel[col].shape}")

    # 3. 跑所有因子
    print("\n[3/4] 计算所有因子...")
    results = compute_all(panel, skip_missing_cols=True)

    print(f"\n  成功计算 {len(results)} 个因子:\n")

    # 按因子分组打印
    gtja_results = {k: v for k, v in results.items() if k.startswith("gtja")}
    qlib_results = {k: v for k, v in results.items() if k.startswith("qlib")}

    if gtja_results:
        print("  ┌─ GTJA 幸存因子 ──────────────────────────────────┐")
        for fid, df in sorted(gtja_results.items()):
            meta = FACTORS[fid]["meta"]
            theme = ",".join(meta.get("theme", []))
            ic = meta.get("ic_mean", "?")
            ir = meta.get("ir", "?")
            valid = int(df.notna().sum().sum())
            total = int(df.size)
            pct = valid / total * 100 if total > 0 else 0

            print(f"  │ {fid:15s} │ IC={ic:<6} IR={ir:<6} │ 有效={valid}/{total} ({pct:.0f}%) │")
        print("  └──────────────────────────────────────────────────┘")

    if qlib_results:
        print("  ┌─ qlib158 K 线形态 ───────────────────────────────┐")
        for fid, df in sorted(qlib_results.items()):
            desc = FACTORS[fid]["meta"].get("desc", "")
            valid = int(df.notna().sum().sum())
            total = int(df.size)
            pct = valid / total * 100 if total > 0 else 0
            print(f"  │ {fid:15s} │ {desc:12s} │ 有效={valid}/{total} ({pct:.0f}%) │")
        print("  └──────────────────────────────────────────────────┘")

    # 列出跳过的因子（需要 amount）
    skipped = [fid for fid in list_factors() if fid not in results]
    if skipped:
        print(f"\n  跳过（需要 amount/vwap 列）: {', '.join(skipped)}")

    # 4. 展示具体数值样例
    print("\n[4/4] 因子值样例（最近 5 天 × 前 5 只股票）:")
    for fid, df in sorted(results.items())[:4]:  # 只展示前 4 个
        recent = df.tail(5).iloc[:, :5]
        print(f"\n  {fid}:")
        # 显示 3 位小数
        pd.set_option('display.float_format', '{:.4f}'.format)
        print(recent.to_string().replace('\n', '\n    '))


if __name__ == "__main__":
    main()