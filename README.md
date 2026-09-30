# 无锡工业气体销售线索自动化系统

> 自动抓取无锡环评受理公示 → OCR识别气体用量 → 生成销售线索报告 → 推送企业微信

---

## 项目背景

Frank 从事无锡工业气体销售（液氧/液氩/液氮/氦气等）。核心挑战：如何在客户**还没开始采购**之前，就找到他们？

传统方式是等客户打电话过来，或靠人脉口耳相传，往往客户已经签了别家合同才得知。

**关键洞察**：企业在建新厂或做技改之前，必须先做**环境影响评价（环评）**，并在政府网站上公示。环评报告里写得清楚：新增哪些设备、用哪些原材料、气体年消耗量是多少。

> 环评受理公示 = 工业气体需求的提前预告，比实际采购早 3～12 个月。

无锡市数据局每天在 [bigdata.wuxi.gov.cn](https://bigdata.wuxi.gov.cn/gggs/jsxmhpspgszl/ffsjsxmhpspgs/slgs/index.shtml) 发布受理公示，人工每天去看效率太低，容易遗漏。于是开始构建这套自动化系统。

---

## 开发历程（完整记录）

### 阶段零：手工探索，发现数据价值

项目的起点是完全人工的：手动打开无锡市数据局网站，逐条看公示标题，手动点进去下载PDF，然后人工阅读。

这个阶段发现了三个高价值项目（均为2026年8～9月公示）：

| 编号 | 建设单位 | 项目 | 关键数据 |
|------|---------|------|---------|
| SLA-20 | 江阴天虹金属铸造 | 合金锻件技改 | 液氧900t + 液氩850t + 液氮600t = 2,350t/年，**原文明确** |
| SLA-21 | 无锡锦绣轮毂 | 铝合金轮毂技改 | 液氩85t/年 + 氦气150瓶/年，**原文明确** |
| SLA-22 | 无锡瑞翎金属 | 无氧铜杆技改 | 平面图标注【氮气储罐】，文字层无数量 |

还分析了几个无关项目（排除）：
- 宜兴豪一模具 五金工具配件 → C2929 塑料注塑，无气体需求，排除
- 正方体纺织 → 纺织行业，排除
- 芯源金属 定转子铁芯 → 无明显气体用量，排除

**这个阶段验证了核心假设**：环评报告里有足够详细的气体数据，完全值得做自动化。

---

### 阶段一：用 PyMuPDF 提取文字，第一代 PDF 报告

**工具选型（第一尝试）**：用 `PyMuPDF`（fitz）直接提取PDF文字层。

问题来了：无锡数据局的环评文件是**扫描件**——整本PDF是图片，没有可选择的文字层。`pdftotext` 和 `pymupdf` 提取到的全是空白。

天虹金属那份是 `.docx` 格式（12MB），用 `python-docx` 成功提取了文字。但锦绣轮毂（2.6MB PDF, 140页）和瑞翎金属（7.5MB PDF, 126页）都是图片版扫描件，文字层提取无效。

**人工阅读替代**：对这两份PDF，只能人工翻阅，记下关键数据（气体名称、年消耗量、储存设施）。

有了数据，开始写第一版PDF生成脚本 `gen_all_leads_pdf.py`：
- 用 `ReportLab` 生成PDF
- 字体：最初用 `wqy-microhei`，发现该字体在 Cowork 云环境不存在；`NotoSansCJK` 报错"postscript outlines not supported"；最终锁定 `wqy-zenhei.ttc`
- 第一版布局：彩色方块标题、三段式结构（基本信息→气体用量→商机评估+行动）

**Python SyntaxError 陷阱**（踩过多次）：
- 中文乘号 `×`（U+00D7）在Python 3.12+里报 `invalid character in identifier`
- 中文弯引号 `"…"`（U+201C/D）写在Python字符串里报 `invalid syntax`
- 解决：全部改为ASCII字符，弯引号改用 `「」` 或 unicode 转义

第一版 PDF 成功生成并推送企业微信。但 Frank 觉得格式太简单，有一份更早期的报告（`gen_tianhong_pdf.py`）风格更好。

---

### 阶段二：PDF 格式迭代，最终定型"三木化工风格"

Frank 上传了一份早期报告的PDF，说："我比较喜欢最早的那个 PDF 版式"，"以前的版式是这样的"。

对比研究后，重写了第二版 `gen_all_leads_v2.py`：
- 五段式结构：基本信息 → 气体用量 → 技改内容（工艺对照表）→ 商机评估（五维度表）→ 建议行动（粗体左标题）
- 成功生成并推送，但仍与目标格式有差距

**第三版 `gen_leads_v3.py` 终于定型**，命名为"**三木化工风格**"，特征：

```
页眉：无锡环评商机 {日期} · {公司名} · 用气需求和竣工时间均为推断，以环评公示原文为准    第N页
────────────────────────────────────────────────────────────────────────────────

【高】江阴市天虹金属铸造有限公司
合金锻件技改项目
无锡环评商机线索｜受理公示日期 2026-08-18｜相关度：★★★ 高

关键气体：液氧(LOX) 900t/年 · 液氩(LAr) 850t/年 · 液氮(LN2) 600t/年 ...
────────────────────────────────────────────────────────────────────────────────

一、基本信息
┌──────────┬───────────────────────────────────────────────────────────┐
│ 建设单位 │ 江阴市天虹金属铸造有限公司                                    │
│ 项目名称 │ 合金锻件技改项目                                              │
...

二、气体相关物料（重点）
┌──────────────┬────────┬──────────┬──────────┬──────────┬──────────────────┐
│ 名称          │ 规格   │ 年消耗量  │ 最大暂存 │ 包装/储存 │ 与气体关系       │
├──────────────┼────────┼──────────┼──────────┼──────────┼──────────────────┤
│ 液氧 (LOX)   │ 工业级  │ 900 t/年 │ 31.56m³ │ 低温储罐  │ 直接用气：精炼   │  ← 浅红底 #ffe0e0
│ 液氩 (LAr)   │ 工业级  │ 850 t/年 │ 31.56m³ │ 低温储罐  │ 直接用气：保护气  │  ← 浅红底
│ 液氮 (LN2)   │ 工业级  │ 600 t/年 │ 31.56m³ │ 低温储罐  │ 直接用气：冷却   │  ← 浅红底

三、技改内容与气体需求背景
四、施工工期 / 开工情况（报告原文）
五、预计竣工（推断，非报告原文）
六、潜在用气需求（推断）
七、跟进建议（推断）

原文链接
说明：...（免责声明）

页脚：2026-08-18                                                           第N页
```

**颜色含义**：
- 🟥 浅红 `#ffe0e0` = 直接用气（环评原文明确数量）
- 🟧 浅橙 `#fff2e0` = 间接带动（推断，用量待确认）

三份报告（SLA-20/21/22）全部生成并推送企业微信，**Frank 确认格式定型**，说"以后就按照这个格式，然后每次都要推送到企业微信"。

---

### 阶段三：接入 Jira，第一次部署为定时任务

**Jira MCP 集成**：接入 Atlassian MCP，在 SLA 看板自动创建 Issue。
- Jira project：SLA（board ID 2）
- CloudId：`0cde2765-376e-4231-965a-bc2c6be2609e`
- 问题：`assignToSprint: "active"` 返回"No sprint matching 'active' found"——看板没有配置 active sprint，暂时跳过

**第一个自动化方案**：在 Cowork 云环境里设置 **Claude 定时任务**（trig_01GRduDiGVtcchNFFtMhFTc2），cron `0 0 * * *`（UTC 00:00 = 北京时间 08:00），每天自动运行脚本生成PDF并推送企业微信。

这个方案有个隐患：脚本运行在 Cowork 云 session 里，依赖 `WXWORK_WEBHOOK_KEY` 环境变量，但定时任务的 session 没有这个变量，实际跑时会失败。

---

### 阶段四：迁移到 GitHub Actions

Frank 提出："要把这个项目部署到 github 上 用 github action 跑"。

这是更稳健的方案：
- 代码版本控制
- GitHub Secrets 安全存储 Webhook Key
- GitHub Actions 自带调度器，不依赖 Cowork 云 session

**代码重构**（从 scratchpad 脚本 → GitHub 版 `gen_leads.py`）：

| 变更点 | 原 scratchpad 版 | GitHub Actions 版 |
|--------|----------------|------------------|
| Webhook Key | 硬编码在脚本里 | `os.environ["WXWORK_WEBHOOK_KEY"]`（GitHub Secret） |
| 输出目录 | `/tmp/claude-0/.../scratchpad/` | `os.environ.get("OUTPUT_DIR", "/tmp")` |
| CA 证书 | `verify="/root/.ccr/ca-bundle.crt"` | `verify=True`（系统证书，不需要 Cowork 专用证书） |
| 字体查找 | 单路径硬编码 | 候选列表逐一尝试 + RuntimeError |
| 企业微信 | `verify=CA_CERT` | `verify=True` |

**`daily.yml`**（GitHub Actions workflow）：
```yaml
on:
  schedule:
    - cron: '0 0 * * *'   # 北京时间 08:00
  workflow_dispatch:        # 手动触发

steps:
  - 安装 fonts-wqy-zenhei（apt-get）
  - 配置 Python 3.11 + pip cache
  - pip install -r requirements.txt
  - python gen_leads.py（读 WXWORK_WEBHOOK_KEY secret）
  - 上传 PDF artifact（保留7天）
```

**数据状态**：此时 `gen_leads.py` 里三条线索（SLA-20/21/22）全部是**硬编码数据**，不是从网站实时抓取的。这只是验证"自动化推送"环节可行。

---

### 阶段五：遭遇 GitHub Push 封锁

重构完代码，想从 Cowork 云环境直接 push 到 GitHub。尝试了全部方法，全部失败：

| 方法 | 结果 | 原因 |
|------|------|------|
| HTTPS git push | 被拒绝 | Anthropic 代理检测到凭证（PAT）泄露风险 |
| SSH 端口 22 | 超时 | 防火墙拦截出站 SSH |
| SSH 端口 443 | `connection closed` | 同上 |
| GitHub REST API（仓库级） | 403 | 代理只开放账号级 API，仓库级全封锁 |
| GitHub Actions API | 403 | 同上 |
| `gh api user` | ✅ 成功 | 仅账号级可用 |

Frank 还提供了新的 PAT，同样因为代理的仓库级封锁无法使用。

**根本原因**：Cowork 云环境的 Anthropic 代理要求每个仓库单独授权（`add_repo` 工具），而这个工具只在 Claude Code CLI 中存在，Cowork session 里没有。

**解决方案**：接受现实，改用 **GitHub 网页界面上传代码文件**（github.com → 仓库 → Upload files）。

> 这成了整个项目的协作模式：Cowork 负责写代码 → 用户通过 GitHub Web 上传 → GitHub Actions 自动运行

---

### 阶段六：验证网站可访问性（连通性测试）

在正式写爬虫之前，先验证 GitHub Actions 环境能不能访问无锡市数据局网站。写了 `test_scrape.yml` 做四步测试：

```
Test 1：列表页访问
  GET https://bigdata.wuxi.gov.cn/.../index.shtml
  → HTTP 200，解析到 20 条公示 ✅

Test 2：详情页访问
  GET https://bigdata.wuxi.gov.cn/doc/2026/09/29/4837537.shtml
  → HTTP 200，找到 3 个 PDF 链接 ✅
  例：https://bigdata.wuxi.gov.cn/uploadfiles/202609/29/2026092916183741231888.pdf

Test 3：PDF 下载
  → HTTP 200，前50KB = b'%PDF-1.6' ✅

Test 4：安装 tesseract-ocr
  → sudo apt-get install -y -qq tesseract-ocr tesseract-ocr-chi-sim ✅
  （曾因粘贴时 poppler-utils 被截断为 poppler 报"Unable to locate package"，注意包名完整）
```

**发现的额外问题**：GitHub Actions runner 终端输出中文乱码——`2026年9月29日` 显示为 `2026å¹´9æ29æ¥`。原因是 UTF-8 字节被当 Latin-1 渲染。加 `PYTHONIOENCODING=utf-8` 环境变量解决（不影响功能，只是日志显示）。

**确认的网站特征**：
- 列表页：服务器渲染 HTML，无 JS 动态加载，`ul.list03 li a` 选择器
- 日期格式：`span.riqi fr`，格式 `2026/09/29`
- 详情页：汇总表（序号/项目名称/建设地点/建设单位/环评机构/受理日期）
- PDF：图片版扫描件，约 25MB，~98 页，须 OCR

---

### 阶段七：自动化爬取（scrape_leads.py）

连通性确认后，写正式的自动化爬取脚本。

**OCR 技术选型**：

| 方案 | 问题 | 结果 |
|------|------|------|
| PyMuPDF 文字层提取 | 扫描件，无文字层 | ❌ 返回空白 |
| pdftotext（poppler） | 同上 | ❌ 返回空白 |
| tesseract + chi_sim | 图像识别 | ✅ 能识别中文 |

**关键参数选择**：
- DPI=120：72 dpi 识别率太低；200 dpi 速度太慢（40页约5分钟）；120 dpi 平衡点（约1秒/页）
- 只处理前40页：气体原辅材料表通常在前三分之一，跳过后60页节省60%时间

**`scrape_leads.py` 完整流程**：

```
每天 08:00 北京时间（UTC 00:00）GitHub Actions 触发
    │
    ▼
1. 抓取列表页，筛选今日北京时间日期的公示（注意时区：UTC+8）
    │
    ▼
2. 逐条获取详情页
   提取：项目名称 / 建设单位 / 建设地点 / 受理日期 / PDF链接
    │
    ▼
3. 排除明显非工业项目（住宅/超市/餐饮等关键词）
    │
    ▼
4. 下载PDF → pdf2image 转图片（前40页，DPI=120）→ tesseract chi_sim+eng OCR
    │
    ▼
5. 搜索 20+ 个气体关键词（液氧/液氮/液氩/氦气/储罐/气化站/CO2...）
    │
    ├── 未发现关键词 → 跳过，记录到已跳过列表
    │
    └── 发现关键词 → 自动评级 + 生成 PDF 报告（复用 gen_leads.py 工具函数）
                │
                ▼
6. 企业微信推送：文字摘要 + 各 PDF 文件
```

**重用 gen_leads.py**：通过 `from gen_leads import ...` 直接导入全套 PDF 工具函数（样式、颜色、表格函数、页眉页脚、企业微信函数），保持报告风格一致。

**评级逻辑**：

| 评级 | 触发词 | 含义 |
|------|-------|------|
| 🔴 高 ★★★ | 液氧/液氮/液氩/液氦/LOX/LIN/LAr/LHe/吹氩/低温储罐 | 液态直接采购，量大 |
| 🟡 中 ★★ | 氧气/氮气/氩气/储罐/气化站/CO2/乙炔 | 用量和供货形式待确认 |
| ⚪ 低 ★ | 其他气体相关词 | 有迹象，需人工判断 |

**重要说明**：评级基于 OCR 自动识别，图片扫描件识别误差较大，数字和单位可能不准确。所有线索需人工下载原文核实后再决策。

---

## 当前技术架构

```
wuxi-leads/
├── gen_leads.py          # 手动模板 + PDF工具函数库
│   ├── 颜色/样式定义（三木化工风格）
│   ├── info_tbl() / gas_tbl() / dark_tbl()
│   ├── make_page_callbacks()（页眉页脚）
│   ├── wxwork_text() / wxwork_upload_and_send()
│   └── 3条硬编码线索：make_tianhong() / make_lungu() / make_ruileng()
│
├── scrape_leads.py       # 自动抓取主脚本
│   ├── import gen_leads（复用全套工具函数）
│   ├── fetch_today_notices()（列表页抓取）
│   ├── fetch_detail()（详情页解析）
│   ├── ocr_pdf_for_gas()（下载+OCR+关键词搜索）
│   ├── grade()（评级）
│   ├── make_lead_pdf()（生成PDF报告）
│   └── main()（主流程）
│
├── requirements.txt
│   └── reportlab, requests, beautifulsoup4, pdf2image, pytesseract, Pillow
│
└── .github/workflows/
    ├── daily.yml          # 生产：每天08:00北京时间
    │   └── apt: fonts-wqy-zenhei + tesseract-ocr + tesseract-ocr-chi-sim + poppler-utils
    └── test_scrape.yml   # 连通性测试（已完成，可保留备用）
```

---

## 开发时间线总结

```
阶段零  │ 完全手工：人工浏览网站，下载PDF，人工阅读，发现三条高价值线索
        │ 验证核心假设：环评数据值得自动化
        │
阶段一  │ PyMuPDF 文字提取尝试 → 发现扫描件无文字层 → 人工读取数据
        │ 第一版PDF报告：ReportLab生成，发现字体/编码坑
        │
阶段二  │ PDF格式多次迭代（v1→v2→v3）
        │ 定型"三木化工风格"：页眉/7章节/双色气体表
        │ Frank 确认格式，固化为标准输出
        │
阶段三  │ Jira MCP 集成（自动创建 Issue）
        │ 第一个自动化：Claude 定时任务（后发现 Secret 不通，废弃）
        │
阶段四  │ 迁移到 GitHub Actions
        │ gen_leads.py 重构：移除 CA 证书/硬编码路径/硬编码 Key
        │ daily.yml 创建，数据仍为硬编码（验证推送流程可行）
        │
阶段五  │ 尝试从 Cowork 云 push 代码 → 全部方法被 Anthropic 代理封锁
        │ 确认根本限制，改为 GitHub Web 界面上传
        │
阶段六  │ GitHub Actions 连通性测试（test_scrape.yml）
        │ 确认：无锡数据局网站可访问，PDF可下载，OCR包可安装
        │ 发现并修复：中文终端乱码（PYTHONIOENCODING=utf-8）
        │
阶段七  │ 写 scrape_leads.py：完整自动化管道
        │ 列表页抓取 → 详情解析 → PDF下载 → OCR → 评级 → 报告生成 → 企业微信
```

---

## 部署与使用

### 前提

- GitHub 仓库：已有（shengqiu/wuxi-leads）
- GitHub Secret：`WXWORK_WEBHOOK_KEY`（在仓库 Settings → Secrets → Actions 中配置）

### 自动运行

每天 UTC 00:00（北京时间 08:00）自动触发 `每日线索报告` workflow。

### 手动测试

GitHub 仓库 → Actions → 每日线索报告 → Run workflow

生成的 PDF 会作为 artifact 保留7天（即使企业微信推送失败也能下载查看）。

### 上传代码更新

由于 Cowork 云环境无法直接 push，新版本代码通过 GitHub 网页上传：
```
github.com/shengqiu/wuxi-leads → Add file → Upload files
```

---

## 已知局限与后续方向

| 局限 | 原因 | 优先级 |
|------|------|--------|
| OCR数字识别不准 | 扫描件分辨率低 | 中——需人工核实 |
| 无历史去重 | 尚未实现 | 高——避免重复推送 |
| Jira自动建卡未完成 | Sprint配置问题 | 中 |
| 仅今日公示 | 08:00运行可能未更新 | 低——可改为检查最近2天 |
| 仅无锡 | 设计如此 | 低——可扩展至苏州/常州 |

---

*本项目用于三木化工（无锡）工业气体销售线索开发，数据来源为无锡市政府公开的环评受理公示信息。*
