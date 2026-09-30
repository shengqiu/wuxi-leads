#!/usr/bin/env python3
"""
scrape_leads.py — 自动抓取无锡环评公示，OCR识别气体用量，生成线索报告推送企业微信
每天 GitHub Actions 08:00 北京时间运行
"""

import os, sys, re, datetime, logging

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
WXWORK_KEY = os.environ["WXWORK_WEBHOOK_KEY"]
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/tmp")

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


def fetch_today_notices():
    """抓取列表页，返回今日公示 [(title, date_str, detail_url), ...]"""
    log.info(f"搜索今日公示（{TODAY_STR}）...")
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

        today_items = [(t, d, u) for t, d, u in page_items if d == TODAY_STR]
        results.extend(today_items)
        log.info(f"  第{page}页：{len(page_items)}条，今日{len(today_items)}条")

        # 若本页无今日条目，或当前页最早日期已早于今日，停止翻页
        if page_items and not today_items:
            break
        if not page_items:
            break

    log.info(f"今日共 {len(results)} 条公示")
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
# OCR
# ══════════════════════════════════════════════════════════════════════════════

def ocr_pdf_for_gas(pdf_url, max_pages=40, dpi=120):
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
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 评级
# ══════════════════════════════════════════════════════════════════════════════

def grade(ocr_result):
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

def make_lead_pdf(info, ocr_result, lead_id, pdf_url):
    """生成单条线索PDF报告（复用 gen_leads.py 工具函数）"""
    company      = info.get("company")      or "（未知建设单位）"
    project_name = info.get("project_name") or info.get("title", "（未知项目）")
    location     = info.get("location")     or "—"
    env_agency   = info.get("env_agency")   or "—"
    accept_date  = info.get("accept_date")  or TODAY_LBL
    detail_url   = info.get("detail_url",  "")

    g_label, g_stars = grade(ocr_result)

    safe = re.sub(r"[^\w一-鿿]", "_", company[:15])
    out  = os.path.join(OUTPUT_DIR, f"{lead_id}_{safe}.pdf")

    on_first, on_later = make_page_callbacks(company, TODAY_LBL)
    doc = SimpleDocTemplate(
        out, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2.2*cm, bottomMargin=1.8*cm,
    )
    s = []

    # ── 标题区 ────────────────────────────────────────────────────────────────
    s.append(Paragraph(f"【{g_label}】" + company, company_s))
    s.append(Paragraph(project_name, proj_s))
    s.append(Paragraph(
        f"无锡环评商机线索｜受理公示日期 {accept_date}｜"
        f"相关度：{g_stars} {g_label}（自动OCR识别，需人工核实）",
        meta_s,
    ))

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
# 主流程
# ══════════════════════════════════════════════════════════════════════════════

def main():
    log.info("=" * 60)
    log.info(f"无锡环评工业气体线索自动抓取 · {TODAY_LBL}")
    log.info("=" * 60)

    # ── Step 1：获取今日公示 ───────────────────────────────────────────────
    notices = fetch_today_notices()
    if not notices:
        msg = f"📋 无锡环评 · {TODAY_LBL}\n今日无新受理公示。"
        wxwork_text(msg)
        log.info("今日无公示，已通知。")
        return

    log.info(f"\n今日 {len(notices)} 条公示，开始逐条处理...\n")

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

        # ── Step 3：排除明确非工业项目 ───────────────────────────────────
        skip_hit = next((kw for kw in SKIP_KEYWORDS if kw in combined), None)
        if skip_hit:
            log.info(f"  跳过（排除词「{skip_hit}」）")
            skipped.append((title, f"排除关键词「{skip_hit}」"))
            continue

        # ── Step 4：下载+OCR PDF ──────────────────────────────────────────
        if not info["pdf_urls"]:
            log.warning("  未找到PDF链接，跳过")
            skipped.append((title, "无PDF链接"))
            continue

        # 优先选含"报告"字样的PDF，否则用第一个
        pdf_url = next(
            (u for u in info["pdf_urls"] if "报告" in u or "report" in u.lower()),
            info["pdf_urls"][0],
        )

        ocr = ocr_pdf_for_gas(pdf_url, max_pages=40, dpi=120)

        if not ocr["found"]:
            skipped.append((title, "OCR未发现气体关键词"))
            continue

        # ── Step 5：生成PDF报告 ───────────────────────────────────────────
        lead_id = f"AUTO-{TODAY_BJ.strftime('%Y%m%d')}-{counter:02d}"
        counter += 1
        try:
            pdf_path = make_lead_pdf(info, ocr, lead_id, pdf_url)
            g_label, g_stars = grade(ocr)
            leads.append((company, project_name, g_label, g_stars, pdf_path))
        except Exception as e:
            log.error(f"  PDF生成失败: {e}", exc_info=True)
            skipped.append((title, f"PDF生成失败"))

    # ── Step 6：企业微信推送 ───────────────────────────────────────────────
    log.info("\n=== 企业微信推送 ===")

    ICONS = {"高": "🔴", "中": "🟡", "低": "⚪", "未知": "⚫"}

    if not leads:
        msg = (
            f"📋 无锡环评 · {TODAY_LBL}\n"
            f"今日 {len(notices)} 条公示，未发现工业气体相关项目。\n"
            f"（已处理 {len(notices) - len(skipped)} 条 / 跳过 {len(skipped)} 条）"
        )
        wxwork_text(msg)
        log.info("无有效线索，已推送通知。")
        return

    # 文字摘要
    lines = []
    for comp, proj, gl, gs, _ in leads:
        lines.append(f"{ICONS.get(gl,'⚫')} {comp[:15]} · {proj[:20]}\n   {gs} {gl}（OCR自动识别）")

    summary = (
        f"📋 无锡环评气体线索 · {TODAY_LBL}\n\n"
        f"今日 {len(notices)} 条公示，发现 {len(leads)} 条潜在线索：\n\n"
        + "\n\n".join(lines)
        + f"\n\n⚠️ 评级基于OCR自动识别，需人工核实原文\n"
        f"详细报告见下方文件。"
    )
    r = wxwork_text(summary)
    log.info(f"文字摘要推送：{r}")

    # 发送各PDF
    for comp, proj, gl, gs, pdf_path in leads:
        label = f"{comp[:10]} · {proj[:15]}"
        ok = wxwork_upload_and_send(pdf_path, label)
        log.info(f"  {'✅' if ok else '❌'} {label}")

    log.info(f"\n=== 完成 ===  线索：{len(leads)}  跳过：{len(skipped)}")


if __name__ == "__main__":
    main()
