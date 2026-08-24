# 微信推送集成审计 — 2026-05-02 ✅ 已修复

## 集成状态：COMPLETE

`position_monitor.py` 已集成 `weixin_pusher.py`，所有功能可用。

## weixin_pusher.py 架构

```
weixin_pusher.py
  ├─ WeixinPusher 类
  │   ├─ __init__() — 从 ~/.hermes/.env 读 Token/Account/ChatID
  │   ├─ send(msg, format_md=True) — 同步发送，自动分片+重试
  │   ├─ send_signal_alert(...) — 结构化交易信号（买/卖/止损/止盈）
  │   ├─ send_position_summary([{code,name,...}]) — 持仓汇总+盈亏
  │   ├─ send_data_health_alert([(name,detail)]) — 数据源故障
  │   └─ _format_for_weixin(msg) — Markdown→微信简化格式
  ├─ 快捷函数：wx_send(), wx_signal(), wx_positions(), wx_alert()
  └─ CLI 模式：python3 weixin_pusher.py [message]
```

**依赖**：`gateway.platforms.weixin.send_weixin_direct()`，需 `aiohttp + cryptography`。无需启动 gateway。

**配置读取顺序**：`hermes_cli.config.get_env_value()` → `os.getenv()` fallback。Token 字段：`WEIXIN_TOKEN`，账号：`WEIXIN_ACCOUNT_ID`，聊天ID：`WEIXIN_CHAT_ID` 或 `WEIXIN_HOME_CHANNEL`。

## position_monitor.py 集成缺口

| 功能 | 修复前 | 修复后(2026-05-02) |
|------|--------|---------------------|
| --push 推送到微信 | ❌ 仅 stdout print | ✅ 调用 `WeixinPusher().send(report)` |
| --alert-only 参数 | ❌ 不存在 | ✅ 仅 CRITICAL/HIGH 告警时推送 |
| 空持仓通知 | ❌ exit(1) | ✅ 推送 `[EMPTY] 当前无持仓` |
| 止损告警优先推送 | ❌ 不推送 | ✅ --alert-only 模式下优先推送 |

**推荐修复（position_monitor.py 第461-467行）**：
```python
if args.push:
    from weixin_pusher import WeixinPusher
    pusher = WeixinPusher()
    
    if args.alert_only:
        # 仅推送有 CRITICAL/HIGH 告警的
        critical = [r for r in results if any(a['level'] in ('CRITICAL','HIGH') for a in r.get('alerts',[]))]
        if critical:
            pusher.send_position_summary([
                {'code': r['code'], 'name': r['name'], 'price': r['current_price'],
                 'pnl': r.get('pnl_pct', 0), 'pnl_pct': r.get('pnl_pct', 0),
                 'stop_loss': 0}
                for r in critical
            ])
        else:
            print("[推送] 无紧急告警，跳过推送")
    else:
        pusher.send(report)
```

## config_loader.py 本次修复

### Bug 1: JS 风格布尔值（第55-57行）
```python
# 修复前（NameError）
"st_stock": true,    # JS 风格
"negative_equity": false,
"revenue_decline_3y": false,

# 修复后
"st_stock": True,    # Python 风格
"negative_equity": False,
"revenue_decline_3y": False,
```

### Bug 2: YAML 空列表 → None（第156-161行）
```python
# 修复前（TypeError: 'NoneType' object is not iterable）
BANNED_CODES = set(_cfg()["banned"].get("codes", []))

# 修复后（or [] 防 None）
BANNED_CODES = set(_cfg()["banned"].get("codes") or [])
```

**根因**：`config.yaml` 中 `codes:` 下全被 `#` 注释，YAML 解析为 `None`。`.get("codes", [])` 的默认值 `[]` 不生效（key 存在但值为 None）。
