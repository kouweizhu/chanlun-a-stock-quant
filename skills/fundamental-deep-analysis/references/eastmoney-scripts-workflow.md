# 东方财富妙想大模型深度分析工作流

> 当用户要求"用东方财富"分析股票时使用的替代流程。
> 记录于 2026-05-12 安井食品深度分析实战。

---

## 触发条件

用户在请求中包含以下任意表述：
- "用东方财富 深度分析 XXX"
- "东方财富 分析 XXX"
- "妙想大模型 看 XXX"

此类请求表明用户期望使用东方财富的 AI 分析能力，而非传统的 AKShare/Baostock 数据采集。

## 工作流总览

```
用户请求"用东方财富 深度分析 XXX"
         │
         ├──→ [并行] 脚本1：initiation-of-coverage-or-deep-dive
         │              └→ 深度研究报告（PDF + Word）
         │
         ├──→ [并行] 脚本2：stock-diagnosis
         │              └→ 多维度诊断报告（Markdown）
         │
         └──→ 整合输出到目标目录 + README.md 导航
```

## 详细步骤

### Step 0：确定股票代码

确认用户请求的股票代码（如安井食品 → 603345.SH）。
如果是 A+H 两地上市，在查询词中注明以便脚本自动识别。

### Step 1：并行启动两个脚本

两个脚本无依赖关系，同时下达执行：

```bash
# 脚本1 - 深度研究报告（后台运行，需等2-10分钟）
background_terminal(
  cwd = "C:/Users/13120/.agents/skills/initiation-of-coverage-or-deep-dive",
  cmd = "python scripts/generate_deep_research_report.py "
        "--query '深度分析{股票简称}({股票代码})，包括公司概况、业务分析、财务分析、估值分析和投资建议' "
        "--output-dir 'D:/常用文件/东方财富skill-分析/{股票简称}深度分析'"
)

# 脚本2 - 诊断报告（60秒内返回）
terminal(
  cwd = "C:/Users/13120/.agents/skills/stock-diagnosis",
  cmd = "python scripts/get_data.py "
        "--query '全面诊断{股票简称}({股票代码})，包括基本面、技术面、估值、市场情绪、风险等各个维度，并给出评分和建议'"
)
```

### Step 2：保存诊断报告

诊断脚本返回后，将内容保存到输出目录：

```bash
# 使用 --no-save 避免写入默认miaoxiang目录，然后手动存到目标目录
python scripts/get_data.py --query "..." --no-save | cat > "D:/常用文件/东方财富skill-分析/{股票简称}深度分析/安井食品_诊断报告_YYYY-MM-DD.md"
```

### Step 3：等待深度报告完成

通过 `process(action="wait", session_id=..., timeout=600)` 等待后台任务完成。
成功后会生成 `initiation_of_coverage_or_deep_dive_<hash>.pdf` 和 `.docx` 两个文件。

### Step 4：提取报告内容摘要

从 docx 文件中提取文本内容构建导航摘要：

```python
import zipfile, xml.etree.ElementTree as ET
with zipfile.ZipFile(docx_path) as z:
    xml_content = z.read('word/document.xml')
    root = ET.fromstring(xml_content)
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    texts = []
    for p in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'):
        para_text = ''
        for t in p.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'):
            if t.text:
                para_text += t.text
        if para_text.strip():
            texts.append(para_text.strip())
```

### Step 5：生成 README.md 导航文件

README.md 应包含：
- 文件清单及大小
- 核心数据速览（营收、利润、增速等关键指标）
- 正面看点 / 风险提示
- 机构评级汇总
- 免责声明

## 输出目录规范

```
D:/常用文件/东方财富skill-分析/           ← 总目录（固定）
  └─ {股票简称}深度分析/                        ← 子目录
       ├─ initiation_of_coverage_or_deep_dive_<hash>.pdf   ← 深度报告（完整）
       ├─ initiation_of_coverage_or_deep_dive_<hash>.docx  ← 深度报告（源文件）
       ├─ {股票简称}_诊断报告_YYYY-MM-DD.md                 ← 诊断报告
       └─ README.md                                         ← 导航摘要
```

## 已知问题 & 规避方案

| 问题 | 现象 | 规避 |
|------|------|------|
| 脚本路径依赖 | 必须在 skill 目录下执行（相对路径） | `cd C:/Users/13120/.agents/skills/<skill-name> && python scripts/...` |
| 深度报告超时 | 妙想大模型响应慢，个别请求超1200s | 超时后重试，或检查 query 是否过于复杂 |
| 网络不通 | WSL2 无法访问外网API | 东方财富 ai-saas.eastmoney.com 是国内线路，WSL2 可用 |
| 诊断脚本 - 默认miaoxiang目录 | 脚本默认保存到 `cwd/miaoxiang/stock_diagnosis/` | 用 `--no-save` 避免，或手动清理 |
| docx 提取中文乱码 | 某些 docx 段落标签嵌套复杂 | 用 `python-docx` 库替代手动 xml 解析 |
| 重复EM_API_KEY配置 | 多个skill脚本都内嵌了默认Key | 无需额外配置，脚本自带可用 |

## 脚本 API 速查

### generate_deep_research_report.py

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `--query` | ✅ | 自然语言查询，包含标的名称+分析指令 |
| `--output-dir` | ❌ | 输出目录（默认 `cwd/miaoxiang/initiation_of_coverage_or_deep_dive/`） |

返回值：JSON，含 `pdf_file_path`, `word_file_path`, `title`, `content`, `shareUrl` 等。

### get_data.py (stock-diagnosis)

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `--query` | ✅ | 自然语言诊断查询 |
| `--no-save` | ❌ | 不保存到本地文件（适合管道重定向） |

返回值：stdout 输出诊断 Markdown 文本。

## 实战验证记录

- **安井食品(603345.SH)** — 2026-05-12：深度报告生成约5分钟，诊断报告30秒返回。两者互补良好。
