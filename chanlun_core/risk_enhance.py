#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
risk_enhance.py —— 风控否决层增强（P1-2，自 a-stock-data V3.6.0 移植）

两个新否决/降级信号源（与现有 L1/L2/L3 负面消息互补）：
  1. 解禁预警:  未来90天限售解禁 → 解禁股数/占总股本比超过阈值 → severe/veto
  2. 重点监控池: 交易所风险警示/重点监控名单（含生效时间窗）→ veto

用法：
  from risk_enhance import check_regulatory_risks
  risks = check_regulatory_risks(code)   # -> {"level": "veto"/"severe"/"none", "reasons": [...]}

阈值（可调，默认对齐 a-stock-data 提示的「解禁占比大 = 抛压」逻辑）：
  LOCKUP_RATIO_SEVERE = 0.05  # 未来90天解禁占总股本 ≥5% → severe
  LOCKUP_RATIO_VETO    = 0.20  # 未来90天解禁占总股本 ≥20% → veto
"""
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import em_utils  # noqa: E402

# 解禁阈值（占总股本比）
LOCKUP_RATIO_SEVERE = 0.05   # ≥5% → 严重降级
LOCKUP_RATIO_VETO = 0.20     # ≥20% → 一票否决
LOCKUP_FORWARD_DAYS = 90     # 预警窗口（天）

# 重点监控池内股票 → 直接 veto
MONITOR_VETO = True


def check_lockup_risk(code: str, forward_days: int = LOCKUP_FORWARD_DAYS) -> dict:
    """未来 forward_days 天解禁预警。返回 {"level", "reasons"}"""
    try:
        d = em_utils.lockup_expiry(code, forward_days=forward_days)
    except Exception as e:
        return {"level": "none", "reasons": [], "detail": f"解禁查询失败({str(e)[:40]})"}
    upcoming = d.get("upcoming", [])
    if not upcoming:
        return {"level": "none", "reasons": [], "detail": f"未来{forward_days}天无解禁"}

    # 汇总：解禁股数(万股) × 占总股本比
    total_ratio = 0.0
    lines = []
    for u in upcoming:
        ratio = u.get("ratio") or 0
        total_ratio += ratio
        lines.append(f"{u['date']} {u.get('type','')} {u.get('shares',0):.0f}万股 "
                     f"占比{ratio*100:.1f}%")
    level = "none"
    reasons = []
    if total_ratio >= LOCKUP_RATIO_VETO:
        level = "veto"
        reasons.append(f"未来{forward_days}天解禁占总股本 {total_ratio*100:.1f}% ≥{LOCKUP_RATIO_VETO*100:.0f}%")
    elif total_ratio >= LOCKUP_RATIO_SEVERE:
        level = "severe"
        reasons.append(f"未来{forward_days}天解禁占总股本 {total_ratio*100:.1f}% ≥{LOCKUP_RATIO_SEVERE*100:.0f}%")
    detail = f"未来{forward_days}天解禁 {len(upcoming)} 批, 合计占比 {total_ratio*100:.1f}%: " + " | ".join(lines[:3])
    return {"level": level, "reasons": reasons, "detail": detail}


# 模块级缓存：重点监控池是全局静态 JSON，全池逐股检查时只请求一次
_MONITOR_CACHE = {"ts": 0.0, "pool": None}
_MONITOR_TTL = 3600  # 缓存 1 小时


def check_monitor_risk(code: str) -> dict:
    """交易所重点监控池检查（带模块级缓存，全池逐股时只请求一次）。"""
    import time as _time
    now = _time.time()
    if _MONITOR_CACHE["pool"] is None or now - _MONITOR_CACHE["ts"] > _MONITOR_TTL:
        try:
            pool = em_utils.em_stock_monitor(only_active=True)
            _MONITOR_CACHE["pool"] = pool
            _MONITOR_CACHE["ts"] = now
        except Exception as e:
            return {"level": "none", "reasons": [], "detail": f"重点监控池查询失败({str(e)[:40]})"}
    pool = _MONITOR_CACHE["pool"]
    hit = [s for s in pool if s.get("code") == code]
    if not hit:
        return {"level": "none", "reasons": [], "detail": "不在重点监控池"}
    s = hit[0]
    if MONITOR_VETO:
        return {
            "level": "veto",
            "reasons": [f"重点监控名单({s['name']}) 监控期 {s['start']}~{s['end']}"],
            "detail": f"在重点监控池: {s['name']} ({s['market']}) 监控期 {s['start']}~{s['end']}",
        }
    return {"level": "severe", "reasons": [f"重点监控名单({s['name']})"], "detail": "在重点监控池"}


def check_regulatory_risks(code: str) -> dict:
    """风控增强总入口：解禁预警 + 重点监控池。
    返回 {"level": "veto"/"severe"/"none", "reasons": [...], "details": [...]}"""
    reasons, details = [], []

    m = check_monitor_risk(code)
    if m["level"] != "none":
        reasons.extend(m["reasons"])
        details.append(m["detail"])

    lk = check_lockup_risk(code)
    if lk["level"] != "none":
        reasons.extend(lk["reasons"])
        details.append(lk["detail"])

    # 级别合并：任一 veto → veto；否则任一 severe → severe
    levels = [m["level"], lk["level"]]
    if "veto" in levels:
        level = "veto"
    elif "severe" in levels:
        level = "severe"
    else:
        level = "none"
    return {"level": level, "reasons": reasons, "details": details}


if __name__ == "__main__":
    import json
    code = sys.argv[1] if len(sys.argv) > 1 else "002475"
    r = check_regulatory_risks(code)
    print(json.dumps(r, ensure_ascii=False, indent=2))
