# 外部工具交互陷阱速查

> 2026-05-02 会话中踩过的坑，快速参考。

## Pandas 读取 Excel 股票代码

**问题**: 如 `002415` 被 pandas 读为整数 2415，丢失前导零。

**修复**:
```python
df = pd.read_excel(path, header=None, dtype={0: str})
```

**表头智能检测**:
```python
first_val = str(df.iloc[0, 0]).strip()
has_header = not (first_val.isdigit() and 4 <= len(first_val) <= 6)
```

## WSL 与 Windows 路径映射

Windows `D:\常用文件` 在 WSL 下为 `/mnt/d/常用文件`。
**WSL 可直接读写 Windows 文件**，无需复制。用户端更新即刻生效。

回测脚本中路径写法:
```python
HOLDINGS_DIR = "/mnt/d/常用文件/持仓监控"
REPORT_DIR = "/mnt/d/常用文件/股票池推荐股"
```

## Metaso API 格式

请求体使用 `"q"` 而非 `"query"`:
```python
payload = {"q": "搜索词", "scope": "webpage", "size": 5}
```

响应中结果字段是 `"webpages"` / `"snippet"`，不是 `"results"` / `"content"`（与 Tavily 不同）。

| 源 | 搜索字段 | 结果字段 | 标题 | 摘要 |
|:--|:--|:--|:--|:--|
| Tavily | `"query"` | `"results"` | `"title"` | `"content"` |
| Metaso | `"q"` | `"webpages"` | `"title"` | `"snippet"` |

## Baostock 股票代码前缀

| 代码特征 | 前缀 |
|---------|------|
| 6xxxxx / 9xxxxx | `sh.` |
| 0xxxxx / 3xxxxx | `sz.` |

⚠️ `quick_chanlun.py` 内部的 `DataManager` 自动添加前缀，所以调用时**必须传裸代码**（如 `688036`），传 `sh.688036` 会变成 `sh.sh.688036`。

## Cron 提示词要点

- **必须使用绝对路径**: `terminal(background=true)` 下 `$HOME` 被 profile 改写，`~/work` 会失败
- **使用 `workdir=` 参数做双重保险**
- **所有路径写 `/home/zjj1990/work/chanlun_core/`**，不用 `~`

## Baostock 30 分钟历史数据

2020 年以前的 30 分钟线 Baostock 基本不可用（`sz.000001 股票数据不存在`）。
回测引擎已内置 M30 降级模式，但**实战环境（2020+）不适用此降级**。

## YAML 空列表 → Python None

`config.yaml` 中注释掉的列表项（如 `codes:` 下全被 `#` 注释）会被 `yaml.safe_load()` 解析为 `None`，而非空列表 `[]`。

```python
# config.yaml
banned:
  codes:
    # - "000002"    ← 全注释
# → yaml.safe_load() 结果: {"codes": None}

# 错误写法（默认值 [] 不生效，因为 key 存在但值为 None）
BANNED_CODES = set(cfg.get("codes", []))  # TypeError!

# 正确写法
BANNED_CODES = set(cfg.get("codes") or [])  # or [] 防 None
```

**规则**：对 YAML 中可能为空的列表/字典字段，一律用 `x or []` / `x or {}`，不要用 `x, []`（后者只防 key 不存在）。

## WSL DNS 解析 GitHub 到 127.0.0.1

WSL 环境中 `api.github.com` / `github.com` 可能被解析到 `127.0.0.1`（VPN/代理/DNS 配置导致），`curl` 和 `git push` 均失败。

**排查**:
```bash
python3 -c "import socket; print(socket.gethostbyname('github.com'))"
# 如果输出 127.0.0.1 → DNS 有问题
```

**解决方案**: 在能访问 GitHub 的机器上推送（Windows GitHub Desktop、另一台服务器）。WSL 内无法修复 DNS 时不要浪费时间折腾。

## JS 风格布尔值混入 Python

从 JSON/YAML 粘贴代码时，`true`/`false`（JS/JSON 风格）不会被 Python 解析，直接报 `NameError: name 'true' is not defined`。手动检查 `_DEFAULTS` 字典等硬编码数据结构中的布尔值。
