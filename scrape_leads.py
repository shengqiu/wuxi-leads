#!/usr/bin/env python3
"""
scrape_leads.py — 自动抓取无锡环评公示，OCR识别气体用量，生成线索报告推送企业微信
每天 GitHub Actions 08:00 北京时间运行

用法：
  python scrape_leads.py                        # 今天（北京时间）
  python scrape_leads.py --date 2026-09-28      # 指定某天
  python scrape_leads.py --days 3               # 最近3天（含今天）
  python scrape_leads.py --date 2026-09-28 --days 3   # 从09-28往前3天
  python scrape_leads.py --no-push              # 只生成PDF，不推送企业微信
"""

import os, sys, re, json, base64, datetime, logging, argparse
from io import BytesIO

import requests
from bs4 import BeautifulSoup

# ── 日志 ─────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── 环境变量（先设好，gen_leads 导入时也会读取同一个 key）────────────────────
WXWORK_KEY     = os.environ.get("WXWORK_WEBHOOK_KEY", "")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
OUTPUT_DIR     = os.environ.get("OUTPUT_DIR", "/tmp")

if not WXWORK_KEY:
    log.warning("WXWORK_WEBHOOK_KEY 未设置，企业微信推送将失败（本地测试请加 --no-push）")
if not ANTHROPIC_KEY:
    log.warning("ANTHROPIC_API_KEY 未设置，将跳过Claude深度分析（只做关键词匹配）")

# ── 导入 gen_leads.py 中的 PDF 工具 ──────────────────────────────────────────
# gen_leads 在加载时注册字体、读取 WXWORK_KEY，均为正常操作
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from gen_leads import (                                     # noqa: E402
    info_tbl, gas_tbl, dark_tbl, make_page_callbacks, hr,
    wxwork_text, wxwork_upload_and_send,
    company_s, proj_s, meta_s, key_s, h1_s, body_s, note_s, foot_s, link_s,
)

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

# ── 北京时间 ──────────────────────────────────────────────────────────────────
TZ_BJ     = datetime.timezone(datetime.timedelta(hours=8))
NOW_BJ    = datetime.datetime.now(TZ_BJ)
TODAY_BJ  = NOW_BJ.date()
TODAY_STR = TODAY_BJ.strftime("%Y/%m/%d")   # 公示页格式：2026/09/30
TODAY_LBL = TODAY_BJ.strftime("%Y-%m-%d")   # 文件名格式

log.info(f"北京时间今日：{TODAY_LBL}")

# ── 目标网站 ──────────────────────────────────────────────────────────────────
BASE_URL = "https://bigdata.wuxi.gov.cn"
LIST_URL = BASE_URL + "/gggs/jsxmhpspgszl/ffsjsxmhpspgs/slgs/index.shtml"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# ── 过滤关键词 ────────────────────────────────────────────────────────────────
# 明确跳过（住宅/零售/纯市政）
SKIP_KEYWORDS = [
    "住宅", "商品房", "安置房", "保障房",
    "超市", "便利店", "餐饮", "大排档",
    "停车场", "公厕",
]

# 气体关键词（OCR 后搜索，包含设施词以降低漏报）
GAS_KEYWORDS = [
    # 液态
    "液氧", "液氮", "液氩", "液氦", "液氢",
    "LOX", "LIN", "LN2", "LAr", "LHe",
    "低温液体", "低温储罐", "液化气体",
    # 气态
    "氧气", "氮气", "氩气", "氦气", "氢气",
    "二氧化碳", "CO2", "乙炔",
    "高纯氮", "高纯氩", "高纯氧",
    # 设施
    "气化站", "气化器", "储罐", "气瓶组",
    "吹氩", "保护气氛", "充氮",
    # 英文简写
    "O2", "N2", "Ar", "He", "H2",
]

# 高价值直接用气关键词（评为"高"）
HIGH_KW = {"液氧", "液氮", "液氩", "液氦", "液氢",
           "LOX", "LIN", "LN2", "LAr", "LHe", "吹氩", "低温储罐"}
# 中价值气体（评为"中"）
MID_KW  = {"氧气", "氮气", "氩气", "氦气", "氢气",
           "二氧化碳", "CO2", "乙炔", "气化站", "储罐"}


# ══════════════════════════════════════════════════════════════════════════════
# 网络抓取
# ══════════════════════════════════════════════════════════════════════════════

def _get(url, timeout=30, stream=False):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, stream=stream)
        r.raise_for_status()
        return r
    except Exception as e:
        log.error(f"HTTP GET 失败 {url}: {e}")
        return None


def fetch_today_notices(target_date_str: str):
    """抓取列表页，返回指定日期公示 [(title, date_str, detail_url), ...]"""
    log.info(f"搜索公示（{target_date_str}）...")
    results = []

    for page in range(1, 6):   # 最多检查5页
        url = LIST_URL if page == 1 else LIST_URL.replace("index.shtml", f"index_{page}.shtml")
        r = _get(url)
        if not r:
            break
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")

        page_items = []
        for li in soup.select("ul.list03 li"):
            a = li.find("a")
            if not a:
                continue
            title = a.get_text(strip=True)
            href  = a.get("href", "")
            if not href:
                continue
            detail_url = href if href.startswith("http") else BASE_URL + ("" if href.startswith("/") else "/") + href

            date_span = li.find("span", class_="riqi")
            date_str  = date_span.get_text(strip=True) if date_span else ""
            page_items.append((title, date_str, detail_url))

        day_items = [(t, d, u) for t, d, u in page_items if d == target_date_str]
        results.extend(day_items)
        log.info(f"  第{page}页：{len(page_items)}条，目标日期{len(day_items)}条")

        # 若本页无目标日期条目，停止翻页
        if page_items and not day_items:
            break
        if not page_items:
            break

    log.info(f"共 {len(results)} 条公示")
    return results


def fetch_detail(detail_url):
    """
    解析详情页，返回 dict:
      project_name, location, company, env_agency, accept_date, pdf_urls, detail_url
    """
    r = _get(detail_url)
    if not r:
        return None
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")

    info = {
        "project_name": "", "location": "", "company": "",
        "env_agency": "", "accept_date": "",
        "pdf_urls": [], "detail_url": detail_url,
    }

    # 解析汇总表格（列名：序号/项目名称/建设地点/建设单位/环评机构/受理日期）
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        headers = [td.get_text(strip=True) for td in rows[0].find_all(["th", "td"])]
        if "项目名称" not in headers and "建设单位" not in headers:
            continue
        # 可能有多行数据，取第一条
        for data_row in rows[1:]:
            cells = data_row.find_all(["td", "th"])
            if not cells:
                continue
            col = {h: i for i, h in enumerate(headers)}
            def cell(key):
                idx = col.get(key)
                return cells[idx].get_text(strip=True) if idx is not None and idx < len(cells) else ""
            info["project_name"] = cell("项目名称")
            info["location"]     = cell("建设地点")
            info["company"]      = cell("建设单位")
            info["env_agency"]   = cell("环评机构")
            info["accept_date"]  = cell("受理日期")
            if info["company"]:
                break
        if info["company"]:
            break

    # 提取PDF链接（优先 uploadfiles 路径）
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" in href.lower() or "uploadfiles" in href:
            pdf_url = href if href.startswith("http") else BASE_URL + ("" if href.startswith("/") else "/") + href
            if pdf_url not in seen:
                seen.add(pdf_url)
                info["pdf_urls"].append(pdf_url)

    return info


# ══════════════════════════════════════════════════════════════════════════════
# 平面图视觉分析（Claude Vision）
# ══════════════════════════════════════════════════════════════════════════════

# 图纸页识别关键词（页面OCR中含这些词 → 大概率是图纸）
_DIAGRAM_KW = [
    "平面图", "布置图", "总平面", "设备布置", "工艺流程图",
    "管道图", "图例", "方位", "图号", "比例", "方向",
    "N↑", "North", "图纸", "设备图", "车间平面",
]

_VISION_PROMPT = """\
以上是「{company}」{project_name}环评报告中的 {n} 张图纸页面。

请仔细识别每张图中所有气体相关设备和设施，重点寻找：
  • 低温液体储罐（液氧/液氮/液氩/液氦储罐，标注 LOX/LIN/LAr/LHe/低温储罐 或容积如 10m³/5000L）
  • 气化器 / 汽化器 / Vaporizer
  • 气体管道（供氮管道/供氧管道/保护气体管线，标注 G-N₂/G-O₂/DN50 等）
  • 气瓶组 / 汇流排 / 气瓶间
  • 制氮机 / 制氧机 / 空分设备 / 氮气发生器
  • 压缩空气站 / 储气罐 / 空压机
  • 乙炔站 / CO₂供应间
  • 任何含"气"字或气体相关的设施标注

只返回 JSON，不要其他文字：
{{
  "has_gas_equipment": true或false,
  "equipment": [
    {{
      "page": 第几页编号（整数）,
      "name": "设备名称（如液氮储罐）",
      "spec": "规格（如10m³/DN50，没有则空字符串）",
      "qty": "数量（如2台，没有则空字符串）",
      "location": "在图中位置（如厂区北侧，没有则空字符串）",
      "estimated_annual_gas": "估算年用气量（能推断则填，如约30-50t/年，否则空字符串）",
      "confidence": "高/中/低"
    }}
  ],
  "summary": "一句话总结发现（如：第3页设备布置图中发现10m³液氮储罐和气化器各1台；若无发现则写'未发现气体相关设备'）",
  "diagram_pages": [有图纸内容的页码列表（整数）]
}}"""


def _analyze_floor_plans_with_vision(images, page_texts, company, project_name):
    """
    对PDF图片页面做Claude视觉分析，识别平面图/设备布置图中的气体设备。
    返回 {"found": bool, "equipment": [...], "pages_analyzed": [int,...], "summary": str}
    """
    empty = {"found": False, "equipment": [], "pages_analyzed": [], "summary": ""}

    if not ANTHROPIC_KEY:
        return {**empty, "summary": "未设置ANTHROPIC_API_KEY，跳过视觉分析"}
    try:
        import anthropic
    except ImportError:
        return {**empty, "summary": "未安装anthropic包"}

    # ── 识别候选图纸页 ────────────────────────────────────────────────────────
    candidates = []
    for i, (img, txt) in enumerate(zip(images, page_texts)):
        nonempty = [l for l in txt.split("\n") if l.strip()]
        is_diagram = (
            len(nonempty) < 20                         # 文字极少 → 很可能是图纸
            or any(kw in txt for kw in _DIAGRAM_KW)   # OCR含图纸关键词
        )
        if is_diagram:
            candidates.append((i + 1, img))

    # 没找到明确图纸 → 取文字最少的一半页面（图纸往往文字稀疏）
    if not candidates and images:
        scored = sorted(
            enumerate(page_texts),
            key=lambda x: len([l for l in x[1].split("\n") if l.strip()])
        )
        half = max(1, len(images) // 2)
        candidates = [(idx + 1, images[idx]) for idx, _ in scored[:half]]

    page_nums  = [p for p, _ in candidates]
    log.info(f"  📐 视觉分析图纸页（共{len(candidates)}张）：{page_nums}")

    # ── 构建多图 content ──────────────────────────────────────────────────────
    content = []
    for page_num, img in candidates:
        # 缩放到最大宽1200（保证可读且省token）
        w, h = img.size
        if w > 1200:
            img = img.resize((1200, int(h * 1200 / w)))

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=72)
        b64 = base64.standard_b64encode(buf.getvalue()).decode()

        content.append({"type": "text",  "text": f"【第{page_num}页】"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": b64,
        }})

    content.append({"type": "text", "text": _VISION_PROMPT.format(
        company=company, project_name=project_name, n=len(candidates),
    )})

    # ── 调用Claude Vision ─────────────────────────────────────────────────────
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        resp   = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1400,
            messages=[{"role": "user", "content": content}],
        )
        raw = resp.content[0].text.strip()
        m   = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            data  = json.loads(m.group())
            found = data.get("has_gas_equipment", False)
            equip = data.get("equipment", [])
            summ  = data.get("summary", "")
            log.info(f"  📐 平面图分析：{'✅ 发现' if found else '❌ 未发现'}气体设备"
                     + (f"（{len(equip)}项）" if equip else ""))
            return {
                "found":          found,
                "equipment":      equip,
                "pages_analyzed": page_nums,
                "diagram_pages":  data.get("diagram_pages", []),
                "summary":        summ,
            }
        log.warning(f"  平面图分析返回非JSON: {raw[:120]}")
    except json.JSONDecodeError as e:
        log.error(f"  平面图分析JSON解析失败: {e}")
    except Exception as e:
        log.error(f"  平面图分析API失败: {e}")

    return empty


# ══════════════════════════════════════════════════════════════════════════════
# OCR
# ══════════════════════════════════════════════════════════════════════════════

def ocr_pdf_for_gas(pdf_url, max_pages=40, dpi=120, company="", project_name=""):
    """
    下载PDF → OCR前N页 → 搜索气体关键词
    返回 {"found": bool, "keywords": [(kw,page,line),...], "snippets": [str,...], "raw_text": str}
    """
    import pdf2image
    import pytesseract

    result = {"found": False, "keywords": [], "snippets": [], "raw_text": ""}

    # 下载
    log.info(f"  下载 {pdf_url}")
    r = _get(pdf_url, timeout=120, stream=True)
    if not r:
        return result

    pdf_bytes = b""
    for chunk in r.iter_content(chunk_size=65536):
        pdf_bytes += chunk
        if len(pdf_bytes) > 100 * 1024 * 1024:   # >100MB 跳过
            log.warning("  PDF超过100MB，跳过")
            return result
    log.info(f"  大小: {len(pdf_bytes)/1024/1024:.1f}MB")

    # 转图片
    try:
        log.info(f"  pdf2image 转换前{max_pages}页 (DPI={dpi})...")
        images = pdf2image.convert_from_bytes(
            pdf_bytes, dpi=dpi,
            first_page=1, last_page=max_pages,
            fmt="jpeg",
        )
        log.info(f"  转换了 {len(images)} 页")
    except Exception as e:
        log.error(f"  pdf2image 失败: {e}")
        return result

    # OCR
    page_texts = []
    for i, img in enumerate(images):
        try:
            txt = pytesseract.image_to_string(img, lang="chi_sim+eng")
            page_texts.append(txt)
        except Exception as e:
            log.warning(f"  第{i+1}页OCR失败: {e}")
            page_texts.append("")

    full_text = "\n".join(f"=== 第{i+1}页 ===\n{t}" for i, t in enumerate(page_texts))
    result["raw_text"] = full_text

    # 搜索关键词
    for kw in GAS_KEYWORDS:
        if kw not in full_text:
            continue
        for p_idx, p_txt in enumerate(page_texts):
            for line in p_txt.split("\n"):
                if kw in line and line.strip():
                    result["keywords"].append((kw, p_idx + 1, line.strip()))
                    if len(result["snippets"]) < 10:
                        snip = f"[第{p_idx+1}页] {line.strip()}"
                        if snip not in result["snippets"]:
                            result["snippets"].append(snip)

    result["found"] = bool(result["keywords"])
    if result["found"]:
        unique = list(dict.fromkeys(k for k, _, _ in result["keywords"]))
        log.info(f"  ✅ 气体关键词: {unique}")
    else:
        log.info("  ❌ 未发现气体关键词")

    # ── 平面图视觉分析（复用已转换的图片，不再重新下载）────────────────────
    fp = _analyze_floor_plans_with_vision(images, page_texts, company, project_name)
    result["floor_plan"] = fp

    # 视觉分析发现设备 → 更新 found 状态（确保不遗漏）
    if fp.get("found"):
        result["found"] = True
        log.info(f"  📐 视觉发现气体设备：{fp.get('summary','')}")

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Claude 深度分析
# ══════════════════════════════════════════════════════════════════════════════

def extract_key_sections(ocr_text: str, max_chars: int = 7000) -> str:
    """从OCR全文中提取最相关章节（原辅料/设备/工艺），减少token消耗"""
    section_triggers = [
        "原辅料", "原料", "辅料", "原材料", "主要原材料",
        "主要设备", "生产设备", "设备清单", "设备一览", "设备名称",
        "主要产品", "生产工艺", "工艺流程", "产品方案", "生产规模",
        "年产", "项目概况", "项目内容", "建设内容",
    ]
    lines   = ocr_text.split("\n")
    buckets = []          # list of (start_line_idx, lines[])
    in_sec  = False
    buf     = []

    for i, ln in enumerate(lines):
        hit = any(t in ln for t in section_triggers)
        if hit:
            if buf:
                buckets.append(buf)
            buf    = [ln]
            in_sec = True
        elif in_sec:
            buf.append(ln)
            if len(buf) > 60:   # 每章节最多60行
                buckets.append(buf)
                buf    = []
                in_sec = False
    if buf:
        buckets.append(buf)

    extracted = "\n".join("\n".join(b) for b in buckets)
    if not extracted.strip():
        extracted = ocr_text           # 回退：全文

    return extracted[:max_chars]


# Claude 分析 prompt（三步法）
_CLAUDE_PROMPT = """\
你是一位资深工业气体销售专家，正在分析一份无锡新建工业项目的环评报告OCR文本。
目标：判断该项目是否需要采购工业气体（液氧/液氮/液氩/液氦/氢气/CO₂/乙炔等），并估算用量。

项目信息
  建设单位：{company}
  项目名称：{project_name}
  建设地点：{location}

以下是从环评PDF中OCR识别的关键段落（图片扫描，识别可能有误差）：
---
{key_text}
---

请按三个维度分析，只返回JSON，不要任何其他文字：

{{
  "raw_materials": {{
    "found": true/false,
    "gases": [
      {{"name": "液氮", "qty": "50", "unit": "t/年", "purpose": "冷却保护气氛"}}
    ],
    "source": "原辅料表中找到的原文关键行（没找到则空字符串）",
    "note": ""
  }},
  "equipment": {{
    "found": true/false,
    "items": [
      {{"name": "液氮储罐", "spec": "10m³", "qty": "1台", "estimated_gas": "约30-50t/年"}}
    ],
    "source": "设备清单中气体相关设备原文（没找到则空字符串）",
    "note": "根据储罐容积/气化器型号估算日用量或年用量的推理"
  }},
  "process": {{
    "products": "主要产品（简短）",
    "process_name": "核心工艺名称",
    "capacity": "年产能（如有）",
    "needs_gas": true/false,
    "gas_types": ["液氮","氩气"],
    "estimated_use": "根据产能估算年用量（如：液氮约20-50t/年）",
    "reasoning": "该工艺需要气体的原因：例如热处理炉保护气氛需要氮气/氩气…",
    "source": "原文支持句子"
  }},
  "verdict": {{
    "grade": "高/中/低/无",
    "confidence": "高/中/低",
    "primary_basis": "raw_materials/equipment/process/none",
    "key_finding": "最关键的一句发现",
    "estimated_annual_qty": "综合估算年用气总量",
    "sales_action": "建议的第一步销售动作"
  }}
}}

评级标准（verdict.grade）：
  高 → 原辅料表明确列出气体用量，或设备有液氧/液氮/液氩储罐，基本确定需要采购
  中 → 工艺分析判断很可能需要气体，或有气化器/气体管道等间接证据
  低 → 工艺上偶尔用到气体，但量少或证据不足（如维修用）
  无 → 该项目明确不需要工业气体（住宅/餐饮/纯商业/市政）
"""


def analyze_with_claude(ocr_text: str, company: str,
                         project_name: str, location: str,
                         floor_plan: dict | None = None) -> dict | None:
    """调用Claude API对环评OCR文本进行三步深度分析，返回结构化评估（失败返回None）"""
    if not ANTHROPIC_KEY:
        return None
    try:
        import anthropic
    except ImportError:
        log.warning("  anthropic包未安装（pip install anthropic），跳过Claude分析")
        return None

    key_text = extract_key_sections(ocr_text)

    # 把平面图视觉分析结论拼入prompt，让Claude综合判断
    fp_context = ""
    if floor_plan and floor_plan.get("found"):
        equip_lines = "\n".join(
            f"  • {e.get('name','')} {e.get('spec','')} ×{e.get('qty','')} "
            f"[第{e.get('page','?')}页] {e.get('estimated_annual_gas','')}"
            for e in floor_plan.get("equipment", [])
        )
        fp_context = (
            f"\n\n【⚠️ 平面图视觉分析已发现以下气体设备，请在原辅料/设备维度中参考】\n"
            f"{equip_lines}\n"
            f"（{floor_plan.get('summary','')}）"
        )

    prompt = _CLAUDE_PROMPT.format(
        company=company, project_name=project_name,
        location=location, key_text=key_text + fp_context,
    )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        resp   = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        m   = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            result = json.loads(m.group())
            grade_val = result.get("verdict", {}).get("grade", "?")
            log.info(f"  🤖 Claude评级: {grade_val} | {result.get('verdict',{}).get('key_finding','')}")
            return result
        log.warning(f"  Claude返回非JSON: {raw[:120]}")
    except json.JSONDecodeError as e:
        log.error(f"  Claude JSON解析失败: {e}")
    except Exception as e:
        log.error(f"  Claude API失败: {e}")
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 评级
# ══════════════════════════════════════════════════════════════════════════════

def grade(ocr_result, claude_analysis=None):
    """综合 Claude分析 + OCR关键词匹配 确定最终评级"""
    # Claude分析优先（置信度高或中时直接用）
    if claude_analysis:
        v  = claude_analysis.get("verdict", {})
        cg = v.get("grade", "")
        cc = v.get("confidence", "")
        if cg == "高":
            return "高", "★★★"
        if cg == "中":
            return "中", "★★"
        if cg == "低" and cc in ("高", "中"):
            return "低", "★"
        # Claude说"无"且置信度高 → 跳过
        if cg == "无" and cc == "高":
            return "无", ""

    # 回退：OCR关键词匹配
    found_kws = {k for k, _, _ in ocr_result["keywords"]}
    if found_kws & HIGH_KW:
        return "高", "★★★"
    if found_kws & MID_KW:
        return "中", "★★"
    if ocr_result["found"]:
        return "低", "★"
    return "未知", "?"


# ══════════════════════════════════════════════════════════════════════════════
# PDF 报告生成
# ══════════════════════════════════════════════════════════════════════════════

def make_lead_pdf(info, ocr_result, lead_id, pdf_url, run_date_lbl: str,
                  claude_analysis=None):
    """生成单条线索PDF报告（复用 gen_leads.py 工具函数）"""
    company      = info.get("company")      or "（未知建设单位）"
    project_name = info.get("project_name") or info.get("title", "（未知项目）")
    location     = info.get("location")     or "—"
    env_agency   = info.get("env_agency")   or "—"
    accept_date  = info.get("accept_date")  or run_date_lbl
    detail_url   = info.get("detail_url",  "")

    g_label, g_stars = grade(ocr_result, claude_analysis)

    safe = re.sub(r"[^\w一-鿿]", "_", company[:15])
    out  = os.path.join(OUTPUT_DIR, f"{lead_id}_{safe}.pdf")

    on_first, on_later = make_page_callbacks(company, run_date_lbl)
    doc = SimpleDocTemplate(
        out, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2.2*cm, bottomMargin=1.8*cm,
    )
    s = []

    # ── 标题区 ────────────────────────────────────────────────────────────────
    s.append(Paragraph(f"【{g_label}】" + company, company_s))
    s.append(Paragraph(project_name, proj_s))

    analysis_src = "Claude三步分析" if claude_analysis else "关键词匹配"
    s.append(Paragraph(
        f"无锡环评商机线索｜受理公示日期 {accept_date}｜"
        f"相关度：{g_stars} {g_label}（{analysis_src}，需人工核实）",
        meta_s,
    ))

    if claude_analysis:
        v        = claude_analysis.get("verdict", {})
        kf       = v.get("key_finding", "")
        est      = v.get("estimated_annual_qty", "")
        action   = v.get("sales_action", "")
        summary_parts = []
        if kf:    summary_parts.append(f"发现：{kf}")
        if est:   summary_parts.append(f"估算用量：{est}")
        if action:summary_parts.append(f"建议动作：{action}")
        if summary_parts:
            s.append(Paragraph("🤖 " + "　".join(summary_parts), key_s))
    else:
        unique_kws = list(dict.fromkeys(k for k, _, _ in ocr_result["keywords"]))
        kw_str = "、".join(unique_kws[:8]) if unique_kws else "（见OCR摘录）"
        s.append(Paragraph(
            f"识别到气体关键词：{kw_str}。"
            "以下摘录来自环评PDF前40页OCR，图片版PDF识别有误差，数量/单位需对照原文核实。",
            key_s,
        ))
    s.append(hr())
    s.append(Spacer(1, 0.3 * cm))

    # ── 一、基本信息 ──────────────────────────────────────────────────────────
    s.append(Paragraph("一、基本信息", h1_s))
    s.append(info_tbl([
        ["建设单位", company],
        ["项目名称", project_name],
        ["建设地点", location],
        ["环评机构",  env_agency],
        ["受理日期",  accept_date],
        ["数据来源",  "无锡市数据局 · 受理公示（自动抓取）"],
        ["评级说明",  f"{g_label} {g_stars} — " + (
            "发现液态气体关键词，直接采购可能性高" if g_label == "高" else
            "发现气态气体/设施关键词，用量待核实"  if g_label == "中" else
            "发现气体相关词汇，具体情况待人工确认"
        )],
    ]))
    s.append(Spacer(1, 0.4 * cm))

    # ── 二、OCR 气体关键词摘录 ────────────────────────────────────────────────
    s.append(Paragraph("二、气体关键词 OCR 摘录（需人工核实原文）", h1_s))
    s.append(Paragraph(
        "Tesseract OCR 自动识别，图片版扫描件误差较大，数字/单位可能不准确。"
        "请下载原环评PDF核实数量、储存设施和供应形式。",
        note_s,
    ))
    s.append(Spacer(1, 0.15 * cm))

    if ocr_result["snippets"]:
        rows = [[f"#{i+1}", snip[:85]] for i, snip in enumerate(ocr_result["snippets"][:10])]
        s.append(dark_tbl(["#", "OCR 识别文字（含气体关键词）"], rows, [1*cm, 15*cm]))
    else:
        s.append(Paragraph("（OCR未提取到含气体关键词的文字行）", body_s))
    s.append(Spacer(1, 0.4 * cm))

    # ── 三、关键词频次 ────────────────────────────────────────────────────────
    s.append(Paragraph("三、识别关键词频次汇总", h1_s))
    kw_count = {}
    for kw, pg, _ in ocr_result["keywords"]:
        kw_count[kw] = kw_count.get(kw, 0) + 1

    if kw_count:
        kw_rows = sorted(kw_count.items(), key=lambda x: -x[1])[:15]
        s.append(dark_tbl(
            ["关键词", "出现次数（含OCR误识）", "类型"],
            [[kw, str(cnt),
              "高价值" if kw in HIGH_KW else "中价值" if kw in MID_KW else "参考"]
             for kw, cnt in kw_rows],
            [3.5*cm, 4*cm, 9*cm],
        ))
    else:
        s.append(Paragraph("（未识别到气体关键词）", body_s))
    s.append(Spacer(1, 0.4 * cm))

    # ── 三A、平面图视觉分析 ───────────────────────────────────────────────────
    fp = ocr_result.get("floor_plan", {})
    s.append(Paragraph("三A、平面图设备视觉分析（Claude Vision）", h1_s))
    pages_str = "、".join(f"第{p}页" for p in fp.get("pages_analyzed", []))
    s.append(Paragraph(
        f"已分析图纸页：{pages_str or '—'}。"
        "Claude直接读取平面图/设备布置图识别气体设备，比OCR文字更准确。",
        note_s,
    ))
    s.append(Spacer(1, 0.15 * cm))

    if fp.get("found") and fp.get("equipment"):
        rows = [
            [
                str(e.get("page", "?")),
                e.get("name", ""),
                e.get("spec", "—"),
                e.get("qty", "—"),
                e.get("location", "—"),
                e.get("estimated_annual_gas", "—"),
            ]
            for e in fp["equipment"]
        ]
        s.append(dark_tbl(
            ["页", "设备名称", "规格", "数量", "位置", "估算年用气"],
            rows,
            [1*cm, 4*cm, 2.5*cm, 1.5*cm, 3*cm, 4.5*cm],
        ))
        if fp.get("summary"):
            s.append(Paragraph(f"💡 {fp['summary']}", key_s))
    else:
        s.append(Paragraph(
            fp.get("summary") or "平面图/布置图中未识别到明显气体设备标注。",
            body_s,
        ))
    s.append(Spacer(1, 0.4 * cm))

    # ── 三B、Claude 三步深度分析 ──────────────────────────────────────────────
    if claude_analysis:
        s.append(Paragraph("三B、Claude 三步深度分析", h1_s))
        s.append(Paragraph(
            "以下分析由 Claude AI 基于环评OCR文本自动生成，供参考，关键数据需核实原文。",
            note_s,
        ))
        s.append(Spacer(1, 0.15 * cm))

        # 原辅料
        rm = claude_analysis.get("raw_materials", {})
        s.append(Paragraph("① 原辅料/原料清单分析", key_s))
        if rm.get("found") and rm.get("gases"):
            rows = [[g.get("name",""), f"{g.get('qty','')} {g.get('unit','')}".strip(),
                     g.get("purpose","")] for g in rm["gases"]]
            s.append(dark_tbl(["气体名称", "用量", "用途"], rows, [3*cm, 4*cm, 9.5*cm]))
        else:
            s.append(Paragraph("原辅料表中未发现气体直接列项。", body_s))
        if rm.get("source"):
            s.append(Paragraph(f"原文依据：{rm['source'][:120]}", note_s))
        if rm.get("note"):
            s.append(Paragraph(rm["note"][:120], note_s))
        s.append(Spacer(1, 0.25 * cm))

        # 设备
        eq = claude_analysis.get("equipment", {})
        s.append(Paragraph("② 主要设备分析", key_s))
        if eq.get("found") and eq.get("items"):
            rows = [[i.get("name",""), i.get("spec",""), i.get("qty",""),
                     i.get("estimated_gas","")] for i in eq["items"]]
            s.append(dark_tbl(["设备名称", "规格", "数量", "估算用气"],
                               rows, [4*cm, 3*cm, 2*cm, 7.5*cm]))
        else:
            s.append(Paragraph("设备清单中未发现气体储罐/气化器等直接设施。", body_s))
        if eq.get("source"):
            s.append(Paragraph(f"原文依据：{eq['source'][:120]}", note_s))
        if eq.get("note"):
            s.append(Paragraph(eq["note"][:150], note_s))
        s.append(Spacer(1, 0.25 * cm))

        # 工艺
        pr = claude_analysis.get("process", {})
        s.append(Paragraph("③ 生产工艺分析", key_s))
        proc_rows = []
        if pr.get("products"):
            proc_rows.append(["主要产品", pr["products"][:80]])
        if pr.get("process_name"):
            proc_rows.append(["核心工艺", pr["process_name"][:80]])
        if pr.get("capacity"):
            proc_rows.append(["生产规模", pr["capacity"][:80]])
        if pr.get("gas_types"):
            proc_rows.append(["所需气体", "、".join(pr["gas_types"])])
        if pr.get("estimated_use"):
            proc_rows.append(["估算用量", pr["estimated_use"][:80]])
        if proc_rows:
            s.append(info_tbl(proc_rows))
        if pr.get("reasoning"):
            s.append(Paragraph(f"分析依据：{pr['reasoning'][:200]}", body_s))
        s.append(Spacer(1, 0.25 * cm))

        # 综合评估
        v = claude_analysis.get("verdict", {})
        s.append(Paragraph("④ Claude 综合评估", key_s))
        verdict_rows = []
        if v.get("grade"):
            verdict_rows.append(["评级",
                f"{v['grade']}（置信度：{v.get('confidence','—')}，依据：{v.get('primary_basis','—')}）"])
        if v.get("key_finding"):
            verdict_rows.append(["关键发现", v["key_finding"][:100]])
        if v.get("estimated_annual_qty"):
            verdict_rows.append(["估算年用量", v["estimated_annual_qty"][:80]])
        if v.get("sales_action"):
            verdict_rows.append(["建议动作", v["sales_action"][:100]])
        if verdict_rows:
            s.append(info_tbl(verdict_rows))
        s.append(Spacer(1, 0.4 * cm))

    # ── 四、跟进建议 ──────────────────────────────────────────────────────────
    s.append(Paragraph("四、跟进建议（自动生成）", h1_s))
    s.append(Paragraph(
        "1）优先下载原环评PDF，核实：气体名称、年用量（t/年或m³/年）、储存设施（储罐型号）、"
        "是否已有供应商。\n"
        "2）确认建设单位联系方式：官网/工商查询→拨打采购部/设备部电话。\n"
        "3）了解项目进度：是否已批复、预计开工/竣工时间、气化站是否已招标。\n"
        "4）若用气量 >50t/年 且无现有长期合同，立即提交整体供气方案（储罐+气化器+运维）。",
        body_s,
    ))
    s.append(Spacer(1, 0.4 * cm))

    # ── 五、原文链接 ──────────────────────────────────────────────────────────
    s.append(Paragraph("五、原文链接", h1_s))
    s.append(Paragraph(
        f"公示详情页：{detail_url}\n"
        f"环评PDF：{pdf_url}\n"
        f"公示列表：{LIST_URL}",
        link_s,
    ))
    s.append(Spacer(1, 0.3 * cm))
    s.append(hr())
    s.append(Spacer(1, 0.1 * cm))
    s.append(Paragraph(
        "说明：本报告由自动脚本生成，OCR识别有误差。气体用量、建设工期等关键数据请以环评PDF原文为准，"
        "人工核实后方可作为销售决策依据。",
        foot_s,
    ))

    doc.build(s, onFirstPage=on_first, onLaterPages=on_later)
    log.info(f"  生成: {out}")
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 单日流程
# ══════════════════════════════════════════════════════════════════════════════

def run_for_date(target_date: datetime.date, no_push: bool = False):
    """针对指定日期运行完整抓取→OCR→生成→推送流程"""
    target_date_str = target_date.strftime("%Y/%m/%d")   # 公示页格式
    run_date_lbl    = target_date.strftime("%Y-%m-%d")   # 文件名/报告格式

    log.info("=" * 60)
    log.info(f"无锡环评工业气体线索自动抓取 · {run_date_lbl}")
    log.info("=" * 60)

    # ── Step 1：获取指定日期公示 ──────────────────────────────────────────
    notices = fetch_today_notices(target_date_str)
    if not notices:
        msg = f"📋 无锡环评 · {run_date_lbl}\n当日无新受理公示。"
        if not no_push:
            wxwork_text(msg)
        log.info("无公示，已通知。")
        return

    log.info(f"\n{run_date_lbl} 共 {len(notices)} 条公示，开始逐条处理...\n")

    leads   = []   # [(company, project_name, grade_label, grade_stars, pdf_path), ...]
    skipped = []   # [(title, reason), ...]
    counter = 1

    for title, date_str, detail_url in notices:
        log.info(f"▶ {title[:60]}")

        # ── Step 2：获取详情页 ─────────────────────────────────────────────
        info = fetch_detail(detail_url)
        if not info:
            skipped.append((title, "详情页获取失败"))
            continue
        info["title"] = title

        company      = info.get("company") or title
        project_name = info.get("project_name") or title
        combined     = company + project_name + title

        # ── Step 3：排除明确非工业项目 ────────────────────────────────────
        skip_hit = next((kw for kw in SKIP_KEYWORDS if kw in combined), None)
        if skip_hit:
            log.info(f"  跳过（排除词「{skip_hit}」）")
            skipped.append((title, f"排除关键词「{skip_hit}」"))
            continue

        # ── Step 4：下载+OCR PDF ───────────────────────────────────────────
        if not info["pdf_urls"]:
            log.warning("  未找到PDF链接，跳过")
            skipped.append((title, "无PDF链接"))
            continue

        # 优先选含"报告"字样的PDF，否则用第一个
        pdf_url = next(
            (u for u in info["pdf_urls"] if "报告" in u or "report" in u.lower()),
            info["pdf_urls"][0],
        )

        ocr = ocr_pdf_for_gas(
            pdf_url, max_pages=40, dpi=120,
            company=company, project_name=project_name,
        )

        # ── Step 4B：Claude深度分析（整合平面图结论+OCR文字）───────────────
        claude = analyze_with_claude(
            ocr["raw_text"], company, project_name, location,
            floor_plan=ocr.get("floor_plan"),
        )

        # 判断是否为有效线索：OCR发现关键词 OR Claude评为高/中
        claude_grade = (claude or {}).get("verdict", {}).get("grade", "")
        ocr_found    = ocr["found"]
        is_relevant  = ocr_found or claude_grade in ("高", "中")

        # Claude明确判断为"无"且置信度高 → 跳过（即使OCR有词也尊重Claude）
        if claude_grade == "无" and (claude or {}).get("verdict", {}).get("confidence") == "高":
            log.info("  🤖 Claude: 该项目不需要工业气体，跳过")
            skipped.append((title, "Claude判定无气体需求"))
            continue

        if not is_relevant:
            skipped.append((title, "OCR未发现关键词且Claude无相关评级"))
            continue

        # ── Step 5：生成PDF报告 ────────────────────────────────────────────
        lead_id = f"AUTO-{target_date.strftime('%Y%m%d')}-{counter:02d}"
        counter += 1
        try:
            pdf_path = make_lead_pdf(info, ocr, lead_id, pdf_url, run_date_lbl, claude)
            g_label, g_stars = grade(ocr, claude)
            leads.append((company, project_name, g_label, g_stars, pdf_path))
        except Exception as e:
            log.error(f"  PDF生成失败: {e}", exc_info=True)
            skipped.append((title, "PDF生成失败"))

    # ── Step 6：企业微信推送 ───────────────────────────────────────────────
    log.info("\n=== 企业微信推送 ===")

    ICONS = {"高": "🔴", "中": "🟡", "低": "⚪", "未知": "⚫"}

    if not leads:
        msg = (
            f"📋 无锡环评 · {run_date_lbl}\n"
            f"共 {len(notices)} 条公示，未发现工业气体相关项目。\n"
            f"（已处理 {len(notices) - len(skipped)} 条 / 跳过 {len(skipped)} 条）"
        )
        if not no_push:
            wxwork_text(msg)
        else:
            log.info(f"[--no-push] {msg}")
        log.info("无有效线索。")
        return

    # 文字摘要
    lines = []
    for comp, proj, gl, gs, _ in leads:
        lines.append(f"{ICONS.get(gl,'⚫')} {comp[:15]} · {proj[:20]}\n   {gs} {gl}（OCR自动识别）")

    summary = (
        f"📋 无锡环评气体线索 · {run_date_lbl}\n\n"
        f"共 {len(notices)} 条公示，发现 {len(leads)} 条潜在线索：\n\n"
        + "\n\n".join(lines)
        + "\n\n⚠️ 评级基于OCR自动识别，需人工核实原文\n"
        "详细报告见下方文件。"
    )

    if not no_push:
        r = wxwork_text(summary)
        log.info(f"文字摘要推送：{r}")
    else:
        log.info(f"[--no-push] 文字摘要：\n{summary}")

    # 发送各PDF
    for comp, proj, gl, gs, pdf_path in leads:
        label = f"{comp[:10]} · {proj[:15]}"
        if not no_push:
            ok = wxwork_upload_and_send(pdf_path, label)
            log.info(f"  {'✅' if ok else '❌'} {label}")
        else:
            log.info(f"  [--no-push] 已生成: {pdf_path}")

    log.info(f"\n=== 完成 ===  线索：{len(leads)}  跳过：{len(skipped)}")


# ══════════════════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="无锡环评工业气体线索自动抓取",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
用法举例：
  python scrape_leads.py                         # 今天（北京时间）
  python scrape_leads.py --date 2026-09-28       # 指定某天
  python scrape_leads.py --days 3                # 最近3天（含今天）
  python scrape_leads.py --date 2026-09-28 --days 3    # 从09-28往前3天
  python scrape_leads.py --no-push               # 只生成PDF，不推送企业微信
        """,
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="指定结束日期（默认：北京时间今日）",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        metavar="N",
        help="往前查几天，包含结束日期（默认：1，即只查 --date 那天）",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="只生成PDF，不推送企业微信（本地测试用）",
    )
    args = parser.parse_args()

    # 确定结束日期
    if args.date:
        try:
            end_date = datetime.date.fromisoformat(args.date)
        except ValueError:
            log.error(f"日期格式错误（须 YYYY-MM-DD）：{args.date}")
            sys.exit(1)
    else:
        end_date = TODAY_BJ

    # 构建日期列表（从早到晚排序）
    dates = sorted([
        end_date - datetime.timedelta(days=i)
        for i in range(args.days)
    ])

    if len(dates) > 1:
        log.info(f"将处理 {len(dates)} 天：{dates[0]} → {dates[-1]}")
    if args.no_push:
        log.info("--no-push 模式：只生成PDF，不推送企业微信")

    for target_date in dates:
        run_for_date(target_date, no_push=args.no_push)


if __name__ == "__main__":
    main()
