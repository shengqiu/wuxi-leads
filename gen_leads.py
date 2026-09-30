#!/usr/bin/env python3
"""生成线索PDF（三木化工风格：数据型报告）并发送企业微信
GitHub Actions 版本 —— 使用环境变量，适配 Ubuntu runner
"""

import os
import requests
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, HRFlowable)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── 环境变量 ────────────────────────────────────────────────────────────────
WXWORK_KEY = os.environ["WXWORK_WEBHOOK_KEY"]   # GitHub Secret
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/tmp")

# ── 字体 ────────────────────────────────────────────────────────────────────
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]
for fp in FONT_CANDIDATES:
    if os.path.exists(fp):
        pdfmetrics.registerFont(TTFont("CJK", fp))
        print(f"Font: {fp}")
        break
else:
    raise RuntimeError(
        f"No CJK font found. Tried: {FONT_CANDIDATES}\n"
        "Run: sudo apt-get install -y fonts-wqy-zenhei"
    )

CJK  = "CJK"
W, H = A4   # 595 x 842 pt

# ── 颜色 ────────────────────────────────────────────────────────────────────
C_HDR      = colors.HexColor("#1a3a5c")
C_GRAY     = colors.HexColor("#cccccc")
C_LGRAY    = colors.HexColor("#f2f2f2")
C_RED_BG   = colors.HexColor("#ffe0e0")   # 直接用气
C_ORANGE_BG= colors.HexColor("#fff2e0")   # 间接带动
C_WHITE    = colors.white

def S(name, **kw):
    return ParagraphStyle(name, fontName=CJK, **kw)

company_s = S("co",   fontSize=16, leading=22, spaceBefore=4, spaceAfter=2,
              textColor=C_HDR)
proj_s    = S("prj",  fontSize=10, leading=15, spaceAfter=6,
              textColor=colors.HexColor("#333333"))
meta_s    = S("meta", fontSize=9,  leading=14, spaceAfter=4,
              textColor=colors.HexColor("#555555"))
key_s     = S("key",  fontSize=9,  leading=14, spaceAfter=10,
              textColor=colors.HexColor("#333333"))
h1_s      = S("h1",  fontSize=11, leading=16, spaceBefore=10, spaceAfter=3,
              textColor=C_HDR)
body_s    = S("body", fontSize=9,  leading=15, spaceAfter=4)
note_s    = S("note", fontSize=8,  leading=13, spaceAfter=2,
              textColor=colors.HexColor("#888888"))
foot_s    = S("foot", fontSize=7.5, leading=12,
              textColor=colors.HexColor("#999999"))
link_s    = S("link", fontSize=8,  leading=13,
              textColor=colors.HexColor("#1155cc"))

def hr(): return HRFlowable(width="100%", thickness=0.5, color=C_GRAY)

# ── 基本信息表 ───────────────────────────────────────────────────────────────
def info_tbl(rows):
    t = Table(rows, colWidths=[3*cm, 13.5*cm])
    t.setStyle(TableStyle([
        ("FONTNAME",   (0,0), (-1,-1), CJK),
        ("FONTSIZE",   (0,0), (-1,-1), 9),
        ("FONTSIZE",   (0,0), (0,-1),  8),
        ("TEXTCOLOR",  (0,0), (0,-1),  colors.HexColor("#555555")),
        ("BACKGROUND", (0,0), (0,-1),  C_LGRAY),
        ("GRID",       (0,0), (-1,-1), 0.5, C_GRAY),
        ("PADDING",    (0,0), (-1,-1), 5),
        ("VALIGN",     (0,0), (-1,-1), "TOP"),
    ]))
    return t

# ── 气体表（带颜色标注） ──────────────────────────────────────────────────────
def gas_tbl(header, rows):
    """rows = [(name, spec, qty, stock, pkg, relation, is_direct)]
    is_direct: True=浅红（直接用气）, False=浅橙（间接带动）, None=white"""
    data = [header] + [[r[i] for i in range(6)] for r in rows]
    t = Table(data, colWidths=[3*cm, 2.5*cm, 2.5*cm, 2.5*cm, 2.5*cm, 3.5*cm])
    cmds = [
        ("FONTNAME",   (0,0), (-1,-1), CJK),
        ("FONTSIZE",   (0,0), (-1,-1), 8.5),
        ("BACKGROUND", (0,0), (-1,0),  C_HDR),
        ("TEXTCOLOR",  (0,0), (-1,0),  C_WHITE),
        ("GRID",       (0,0), (-1,-1), 0.5, C_GRAY),
        ("PADDING",    (0,0), (-1,-1), 5),
        ("VALIGN",     (0,0), (-1,-1), "TOP"),
    ]
    for i, r in enumerate(rows):
        is_direct = r[6] if len(r) > 6 else None
        if is_direct is True:
            cmds.append(("BACKGROUND", (0, i+1), (-1, i+1), C_RED_BG))
        elif is_direct is False:
            cmds.append(("BACKGROUND", (0, i+1), (-1, i+1), C_ORANGE_BG))
    t.setStyle(TableStyle(cmds))
    return t

# ── 普通深色表头表 ────────────────────────────────────────────────────────────
def dark_tbl(header, rows, col_widths):
    data = [header] + rows
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("FONTNAME",       (0,0), (-1,-1), CJK),
        ("FONTSIZE",       (0,0), (-1,-1), 9),
        ("BACKGROUND",     (0,0), (-1,0),  C_HDR),
        ("TEXTCOLOR",      (0,0), (-1,0),  C_WHITE),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [C_LGRAY, C_WHITE]),
        ("GRID",           (0,0), (-1,-1), 0.5, C_GRAY),
        ("PADDING",        (0,0), (-1,-1), 5),
        ("VALIGN",         (0,0), (-1,-1), "TOP"),
    ]))
    return t

# ── 页眉/页脚回调 ─────────────────────────────────────────────────────────────
def make_page_callbacks(company, date_str):
    def draw(c, doc):
        c.saveState()
        c.setFont(CJK, 7.5)
        c.setFillColor(colors.HexColor("#888888"))
        hdr = f"无锡环评商机 {date_str} · {company} · 用气需求和竣工时间均为推断，以环评公示原文为准"
        c.drawString(doc.leftMargin, H - 1.2*cm, hdr)
        c.drawRightString(W - doc.rightMargin, H - 1.2*cm, f"第 {doc.page} 页")
        c.setStrokeColor(colors.HexColor("#cccccc"))
        c.setLineWidth(0.3)
        c.line(doc.leftMargin, H - 1.4*cm, W - doc.rightMargin, H - 1.4*cm)
        c.setFont(CJK, 7.5)
        c.drawString(doc.leftMargin, 1.0*cm, date_str)
        c.drawRightString(W - doc.rightMargin, 1.0*cm, f"第 {doc.page} 页")
        c.line(doc.leftMargin, 1.3*cm, W - doc.rightMargin, 1.3*cm)
        c.restoreState()
    return draw, draw


# ════════════════════════════════════════════════════════════════════════════
# PDF 1: SLA-20 天虹金属
# ════════════════════════════════════════════════════════════════════════════
def make_tianhong():
    out = f"{OUTPUT_DIR}/SLA-20_天虹金属_合金锻件技改.pdf"
    company = "江阴市天虹金属铸造有限公司"
    date_str = "2026-08-18"
    on_first, on_later = make_page_callbacks(company, date_str)
    doc = SimpleDocTemplate(out, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2.2*cm, bottomMargin=1.8*cm)
    s = []
    s.append(Paragraph("【高】" + company, company_s))
    s.append(Paragraph("合金锻件技改项目", proj_s))
    s.append(Paragraph(
        "无锡环评商机线索｜受理公示日期 2026-08-18｜江阴市·周庄镇（市本级受理，环评报告表）｜相关度：★★★ 高",
        meta_s))
    s.append(Paragraph(
        "关键气体：液氧（LOX）900 t/年·液氩（LAr）850 t/年·液氮（LN2）600 t/年，合计 2,350 t/年；"
        "6座低温储罐自建供气站（液氧/液氩/液氮各×2，每个 31.56 m³）。工频感应电炉精炼需大量保护气体，"
        "技改换炉期是切入窗口。",
        key_s))
    s.append(hr())
    s.append(Spacer(1, 0.3*cm))

    s.append(Paragraph("一、基本信息", h1_s))
    s.append(info_tbl([
        ["建设单位", company],
        ["项目名称", "合金锻件技改项目"],
        ["建设地点", "江苏省无锡市江阴市周庄镇天虹路7号"],
        ["建设性质", "技术改造"],
        ["行业类别", "C3393 锻件及粉末冶金制品制造"],
        ["总投资",   "8,800 万元（环保投资 22.5 万元）"],
        ["现有产能", "合金锻件 3 万吨/年（已达产）"],
        ["新增设备", "工频感应电炉（淘汰中频炉）；南厂区气化站（液氧/液氩/液氮储罐各×2）"],
        ["劳动定员", "技改后 110 人"],
        ["受理机关", "无锡市数据局 / 无锡市生态环境局"],
        ["公示期",   "2026-08-18 ~ 2026-09-01（10个工作日）"],
    ]))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("二、气体相关物料（重点）", h1_s))
    s.append(Paragraph(
        "环评报告表工程分析「供气系统」章节（第4章4.1.4节）明确列出三种工业气体年消耗量及储存设施，"
        "均为外购液态气体，属直接采购对象。",
        body_s))
    hdr = ["名称", "规格/组分", "年消耗量", "最大暂存量", "包装/储存", "与气体的关系"]
    rows = [
        ["液氧 (LOX)",  "工业级", "900 t/年",  "31.56 m³×2",
         "低温液氧储罐\n各31.56m³", "直接用气：工频感应电炉\n精炼/冶炼，供压~1.4MPa", True],
        ["液氩 (LAr)",  "工业级", "850 t/年",  "31.56 m³×2",
         "低温氩气储罐\n各31.56m³", "直接用气：特种合金精炼\n保护气氛（主要消耗项）", True],
        ["液氮 (LN2)",  "工业级", "600 t/年",  "31.56 m³×2",
         "低温氮气储罐\n各31.56m³", "直接用气：保护气氛\n工艺冷却", True],
        ["合计", "—", "2,350 t/年", "共6座储罐", "南厂区集中气化站", "熔炼车间管道供气", True],
    ]
    s.append(gas_tbl(hdr, rows))
    s.append(Spacer(1, 0.15*cm))
    s.append(Paragraph(
        "浅红底 = 直接用气（环评原文明确数据）。数据来源：报告表第4章4.1.4供气系统，为技改后全厂年消耗量。",
        note_s))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("三、技改内容与气体需求背景（报告原文及分析）", h1_s))
    s.append(Paragraph(
        "本次技改核心：淘汰现有中频炉，新增工频感应电炉（更大容量、更高效率），同步扩大熔炼产能。"
        "工频炉精炼对保护气氛（液氩）和氧气（氧化精炼）需求量显著高于中频炉。新建南厂区气化站，"
        "设液氧、液氩、液氮低温储罐各2只（单罐31.56m³），配套气化增压装置向车间管道配送。",
        body_s))
    s.append(dark_tbl(
        ["工序", "主要设备", "气体用途（分析）"],
        [["熔化/精炼", "工频感应电炉（新增）\n中频炉（淘汰）", "液氧（冶炼精炼）+ 液氩（保护气氛）"],
         ["锻造",     "液压锻造机",   "液氮（局部冷却及保护，部分工艺）"],
         ["供气站",   "低温储罐×6 + 气化器", "液氧/液氩/液氮→气化增压→管道配送至熔炼车间"]],
        [3.5*cm, 5*cm, 8*cm]))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("四、施工工期 / 开工情况（报告原文）", h1_s))
    s.append(Paragraph(
        "报告表本次技改性质为技术改造，未明确披露详细施工计划节点。"
        "受理公示日期 2026-08-18，公示期结束后进入审批流程（一般1～2个月）。"
        "预计批复时间约 2026年10月，技改建设期约 3～6 个月，"
        "全部气体储罐和气化站需在换炉前完成安装调试。",
        body_s))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("五、预计竣工（推断，非报告原文）", h1_s))
    s.append(Paragraph(
        "批复预计 2026-10 ~ 2026-11；技改建设期3～6个月，预计 2027年上半年竣工投产。"
        "供气合同应在竣工验收前至少3个月确定（储罐安装/调试需提前）。"
        "实际以建设单位公布或竣工验收公示为准。",
        body_s))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("六、潜在用气需求（推断）", h1_s))
    s.append(Paragraph(
        "1）液氧900t/年 + 液氩850t/年 + 液氮600t/年，合计 2,350t/年，为环评报告原文明确数据，"
        "可信度高。按工业气体市场均价估算，年合同额 50 万元以上。\n"
        "2）工频炉换代后液氩需求可能进一步增加（精炼工艺升级）；关注实际投产后用量是否超出环评预测。\n"
        "3）瓶装/杜瓦气体：化验室标准气、小量特种气（推断）。",
        body_s))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("七、跟进建议（推断）", h1_s))
    s.append(Paragraph(
        "找谁：江阴市天虹金属铸造 设备/动力部或采购部负责人。"
        "切入点：技改换炉→气化站同步新建，整体方案（储罐+气化器+管道+运维）差异化竞争；"
        "重点推液氧/液氩长期供货协议，同时附带液氮配送报价。"
        "建议近期（1～2周）电话了解技改进度和现有供应商，中期（1～3月）建设期提交完整供气方案。"
        "注意该项目仍在环评公示期，批复前企业可能不确定建设时间节点，宜先建立联系。",
        body_s))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("原文链接", h1_s))
    s.append(Paragraph(
        "公示列表页：https://bigdata.wuxi.gov.cn/gggs/jsxmhpspgszl/ffsjsxmhpspgs/slgs/index.shtml\n"
        "（具体公示详情页及环评文件链接请在公示列表中按受理日期2026-08-18检索）",
        link_s))
    s.append(Spacer(1, 0.3*cm))
    s.append(hr())
    s.append(Spacer(1, 0.1*cm))
    s.append(Paragraph(
        "说明：基本信息和气体物料数据照录自环评报告原文；工期/竣工/用气需求/跟进建议均为分析推断，"
        "不作为合同依据。实际以建设单位确认或政府批复为准。",
        foot_s))

    doc.build(s, onFirstPage=on_first, onLaterPages=on_later)
    print(f"OK: {out}")
    return out


# ════════════════════════════════════════════════════════════════════════════
# PDF 2: SLA-21 锦绣轮毂
# ════════════════════════════════════════════════════════════════════════════
def make_lungu():
    out = f"{OUTPUT_DIR}/SLA-21_锦绣轮毂_铝合金轮毂技改.pdf"
    company = "无锡锦绣轮毂有限公司"
    date_str = "2026-09-10"
    on_first, on_later = make_page_callbacks(company, date_str)
    doc = SimpleDocTemplate(out, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2.2*cm, bottomMargin=1.8*cm)
    s = []
    s.append(Paragraph("【高】" + company, company_s))
    s.append(Paragraph("铝合金轮毂生产技术改造项目", proj_s))
    s.append(Paragraph(
        "无锡环评商机线索｜受理公示日期 2026-09-10｜惠山区·玉祁街道（市本级受理，环评报告表）｜相关度：★★★ 高",
        meta_s))
    s.append(Paragraph(
        "关键气体：液氩（LAr）85 t/年（4t储罐，吹氩除气）；氦气（He）150 瓶/年·40L/瓶（气密检测）。"
        "技改新增固定式除气机 JX-750GX，液氩为铝液精炼核心气体；氦气用量技改后增幅88%（80→150瓶/年）。",
        key_s))
    s.append(hr())
    s.append(Spacer(1, 0.3*cm))

    s.append(Paragraph("一、基本信息", h1_s))
    s.append(info_tbl([
        ["建设单位", company],
        ["项目名称", "铝合金轮毂生产技术改造项目"],
        ["建设地点", "无锡市惠山区玉祁街道锦祁路19号"],
        ["建设性质", "技术改造"],
        ["行业类别", "C3670 汽车零部件及配件制造"],
        ["总投资",   "4,800 万元（环保投资 150 万元）"],
        ["用地面积", "9,420 m²"],
        ["主要技改设备",
         "固定式除气机 JX-750GX（新增）；铝液静止保温炉 10 t（新增）；"
         "氦气气密机 Qyh-5012Z（保留）；热处理炉（保留，配淬火水池）"],
        ["工期",     "3 个月"],
        ["受理机关", "无锡市数据局 / 无锡市生态环境局"],
        ["公示期",   "2026-09-10 起（10个工作日）"],
    ]))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("二、气体相关物料（重点）", h1_s))
    s.append(Paragraph(
        "原辅材料表（表2-5）明确列出液氩和氦气的年用量及储存方式，均为外购，属直接采购对象。"
        "两种气体均在环评原文中有明确数字依据。",
        body_s))
    hdr = ["名称", "规格/组分", "年消耗量", "最大暂存量", "包装/储存", "与气体的关系"]
    rows = [
        ["液氩 (LAr)", "工业级 99.9%", "85 t/年\n（技改前后不变）",
         "4 t 储罐", "现有液氩储罐\n4 t（保留）",
         "直接用气：固定式除气机\nJX-750GX，吹氩去氢去夹杂", True],
        ["氦气 (He)", "高纯 99.999%",
         "150 瓶/年\n(40L/瓶·约 6m³/瓶)\n≈900 m³/年",
         "70 瓶（新增）", "气瓶（现有存瓶\n+新增70瓶）",
         "直接用气：氦气气密机\nQyh-5012Z，轮毂气密检漏\n技改前80瓶→技改后150瓶", True],
    ]
    s.append(gas_tbl(hdr, rows))
    s.append(Spacer(1, 0.15*cm))
    s.append(Paragraph(
        "浅红底 = 直接用气（原辅材料表原始数据）。液氩储量85t/年为环评原文确认值，非估算。",
        note_s))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("三、生产工艺与气体使用工序（报告原文及分析）", h1_s))
    s.append(Paragraph(
        "铝合金轮毂生产流程：铝锭熔化（710°C）→ 除气（吹氩）→ 低压铸造 → 固化 → "
        "热处理（T6：固溶+时效）→ 机加工 → 气密检测（氦气）→ X光探伤 → 涂装。",
        body_s))
    s.append(dark_tbl(
        ["工序", "气体", "设备（报告原文）", "备注"],
        [["铝液除气",  "液氩\n（吹入铝液）",
          "固定式除气机 JX-750GX\n铝液静止保温炉 10t",
          "技改新增；去除氢气和氧化夹杂\n提升铸件致密度"],
         ["气密检测", "氦气\n（检漏）",
          "氦气气密机 Qyh-5012Z",
          "保留设备；轮毂铸件逐件检测\n合格率要求极高"],
         ["热处理",   "无直接气体用量\n（天然气加热）",
          "热处理炉+淬火水池",
          "T6热处理：固溶炉500°C\n+淬火+时效炉"]],
        [2.5*cm, 2.5*cm, 5.5*cm, 6*cm]))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("四、施工工期 / 开工情况（报告原文）", h1_s))
    s.append(Paragraph(
        "报告表：建设工期 3 个月。受理公示日期 2026-09-10，公示期 10 个工作日（约至 2026-09-24）。"
        "批复预计 2026年10～11月；技改施工3个月，预计 2027年初投产。",
        body_s))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("五、预计竣工（推断，非报告原文）", h1_s))
    s.append(Paragraph(
        "批复 2026-10 ~ 2026-11 → 施工 3 个月 → 预计 2027年初至一季度竣工。"
        "液氩4t储罐在用，技改期间储罐不变，供货切换窗口在新除气机安装调试阶段（约施工中后期）。"
        "实际以建设单位公布或竣工验收公示为准。",
        body_s))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("六、潜在用气需求（推断）", h1_s))
    s.append(Paragraph(
        "1）液氩 85t/年：现有4t储罐配套，建议定期槽车配送（液氩真空绝热罐车）；"
        "新增除气机处理量提升后实际用量可能超过85t，关注实际投产后数据。\n"
        "2）氦气 150瓶/年（40L/瓶）：气瓶供货，需保障供应稳定性（轮毂出货节奏紧，"
        "氦气缺货会直接影响产线）；技改后新增70瓶库存，建议签年度供货协议。\n"
        "3）技改产能提升后两种气体用量可能同步增长，宜在首次合同中约定用量上调条款。",
        body_s))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("七、跟进建议（推断）", h1_s))
    s.append(Paragraph(
        "找谁：无锡锦绣轮毂 采购部/动力设备部负责人。"
        "切入点：①液氩：配合新增除气机JX-750GX的安装调试，提供液氩槽车+气化器配套报价，"
        "强调4t储罐配送优化方案（减少换气频次/提升安全性）；"
        "②氦气：年度供货协议，保障150瓶/年的稳定供应（汽车零部件行业对交货期要求严格）。"
        "近期（1～2周）电话确认技改进度，中期随设备安装提交整体方案。",
        body_s))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("原文链接", h1_s))
    s.append(Paragraph(
        "公示列表页：https://bigdata.wuxi.gov.cn/gggs/jsxmhpspgszl/ffsjsxmhpspgs/slgs/index.shtml\n"
        "（按受理日期2026-09-10、惠山区检索铝合金轮毂技改项目公示页及环评文件）",
        link_s))
    s.append(Spacer(1, 0.3*cm))
    s.append(hr())
    s.append(Spacer(1, 0.1*cm))
    s.append(Paragraph(
        "说明：基本信息和气体物料数据照录自环评原辅材料表原文；工期/竣工/用气需求/跟进建议均为分析推断，"
        "不作为合同依据。实际以建设单位确认或政府批复为准。",
        foot_s))

    doc.build(s, onFirstPage=on_first, onLaterPages=on_later)
    print(f"OK: {out}")
    return out


# ════════════════════════════════════════════════════════════════════════════
# PDF 3: SLA-22 瑞翎金属
# ════════════════════════════════════════════════════════════════════════════
def make_ruileng():
    out = f"{OUTPUT_DIR}/SLA-22_瑞翎金属_无氧铜杆技改.pdf"
    company = "无锡瑞翎金属制品有限公司"
    date_str = "2026-09-08"
    on_first, on_later = make_page_callbacks(company, date_str)
    doc = SimpleDocTemplate(out, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2.2*cm, bottomMargin=1.8*cm)
    s = []
    s.append(Paragraph("【中】" + company, company_s))
    s.append(Paragraph("上引法无氧铜杆连铸机组和绕组线技改项目", proj_s))
    s.append(Paragraph(
        "无锡环评商机线索｜受理公示日期 2026-09-08｜锡山区·东港镇（市本级受理，环评报告表）｜相关度：★★ 中（待确认）",
        meta_s))
    s.append(Paragraph(
        "关键气体：厂区平面布置图标注【氮气储罐】；工艺设备表列电退火炉x2台。"
        "原辅材料文字层未见氮气年用量（工艺流程图为图片格式，文字无法提取）。"
        "上引法无氧铜杆工艺需要氮气保护铜液，退火炉也通常使用氮气保护气氛，用量待电话确认。",
        key_s))
    s.append(hr())
    s.append(Spacer(1, 0.3*cm))

    s.append(Paragraph("一、基本信息", h1_s))
    s.append(info_tbl([
        ["建设单位", company],
        ["项目名称", "上引法无氧铜杆连铸机组和绕组线技改项目"],
        ["建设地点", "无锡市锡山区东港镇工业集中区A区1001号"],
        ["建设性质", "扩建"],
        ["行业类别", "C3392 有色金属铸造 / C3251 铜压延加工 / C3831 电线电缆制造"],
        ["总投资",   "1,500 万元"],
        ["新增产能", "无氧铜杆 +12,000 t/年（全厂达 18,000 t/年）；绕组线 +3,000 t/年"],
        ["主要新增设备", "上引法连铸机组（台数待核实）；电退火炉 x 2台（新增）"],
        ["工期",     "3 个月"],
        ["受理机关", "无锡市数据局 / 无锡市生态环境局"],
        ["公示期",   "2026-09-08 起（10个工作日）"],
    ]))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("二、气体相关物料（重点，用量待确认）", h1_s))
    s.append(Paragraph(
        "原辅材料表文字层未直接列出氮气。以下线索来自平面图、设备表和工艺特征推断，"
        "浅橙底标注为间接带动（推断），需电话确认实际用量。",
        body_s))
    hdr = ["线索来源", "具体内容", "年消耗量", "最大暂存量", "包装/储存", "可能用途（推断）"]
    rows = [
        ["厂区平面布置图", "标注【氮气储罐】", "未披露\n（待确认）",
         "未披露", "固定储罐\n（型号未知）",
         "间接：上引法铸造区保护\n气氛，防铜液氧化", False],
        ["工艺设备表", "电退火炉 x 2台\n（技改新增）", "未披露\n（待确认）",
         "未披露", "管道供气\n（推断）",
         "间接：铜丝退火保护气氛\n防止氧化变色", False],
        ["工艺特征", "上引法无氧铜杆\n(oxygen-free copper)", "—",
         "—", "—",
         "间接：\"无氧\"铜要求严格控制\n氧含量，需惰性气体全程保护", False],
    ]
    s.append(gas_tbl(hdr, rows))
    s.append(Spacer(1, 0.15*cm))
    s.append(Paragraph(
        "浅橙底 = 间接带动用气（推断）。上引法无氧铜杆行业通常氮气用量在数十至数百吨/年不等，"
        "建议电话确认后重新评级。",
        note_s))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("三、上引法工艺与氮气使用背景（分析）", h1_s))
    s.append(Paragraph(
        "上引法（Up-Cast）连续铸造：铜液在密封坩埚中向上引出，铸杆全程与空气隔绝，"
        "保护气体（通常为氮气）从坩埚底部或侧面持续通入防止铜液氧化。产能 18,000t/年属中大型规模，"
        "典型氮气耗量约 50～200 t/年（参考行业数据，具体取决于设备台数和气体利用率）。",
        body_s))
    s.append(dark_tbl(
        ["工序", "气体需求（推断）", "说明"],
        [["上引法铸造",  "氮气（保护气氛）", "铜液密封环境保护；持续通气，用量随产能正比增长"],
         ["电退火炉×2", "氮气（保护气氛）", "铜丝退火防氧化；退火炉一般使用纯氮或氮氢混合气"],
         ["绕组线制造",  "待确认",          "可能涉及退火保护，需现场或电话确认"]],
        [3*cm, 4*cm, 9.5*cm]))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("四、施工工期 / 开工情况（报告原文）", h1_s))
    s.append(Paragraph(
        "报告表：建设工期 3 个月。受理公示日期 2026-09-08。"
        "批复预计 2026年10～11月；施工3个月，预计 2026年底～2027年初投产。",
        body_s))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("五、预计竣工（推断，非报告原文）", h1_s))
    s.append(Paragraph(
        "批复 2026-10 ~ 2026-11 → 施工 3 个月 → 预计 2027年一季度竣工。"
        "扩建项目意味着气体需求将新增，新设备安装调试期（批复后约1～2个月）是切入最佳时机。"
        "实际以建设单位公布或竣工验收公示为准。",
        body_s))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("六、潜在用气需求（推断）", h1_s))
    s.append(Paragraph(
        "1）氮气（主要用气）：上引法铸造保护气 + 退火炉保护气，若产能18,000t/年满产，"
        "氮气需求估算 50～200 t/年（需电话确认后评级：>50t/年升为高价值★★★）。\n"
        "2）氮气供应形式：大用量建议液氮储罐+气化器（经济性好）；小用量建议气态氮气瓶组。\n"
        "3）标准气/特种气：化验检测（铜杆氧含量分析）可能需要标准气（瓶装，推断）。",
        body_s))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("七、跟进建议（推断）", h1_s))
    s.append(Paragraph(
        "找谁：无锡瑞翎金属 设备/动力部或采购部负责人。"
        "优先动作：电话咨询氮气年用量、现有供应商、气化站或瓶组配置，明确后重新评级。"
        "切入点：扩建新增退火炉x2台，设备安装前提出氮气供应方案（含储罐配套），"
        "强调无氧铜杆对氮气纯度和供应稳定性的高要求（通常要求纯度≥99.99%）。"
        "若氮气用量确认>50t/年，立即升为高价值线索并提交完整供货方案。",
        body_s))
    s.append(Spacer(1, 0.4*cm))

    s.append(Paragraph("原文链接", h1_s))
    s.append(Paragraph(
        "公示列表页：https://bigdata.wuxi.gov.cn/gggs/jsxmhpspgszl/ffsjsxmhpspgs/slgs/index.shtml\n"
        "（按受理日期2026-09-08、锡山区检索瑞翎金属技改项目公示页及环评文件）",
        link_s))
    s.append(Spacer(1, 0.3*cm))
    s.append(hr())
    s.append(Spacer(1, 0.1*cm))
    s.append(Paragraph(
        "说明：本文件中\"报告原文\"内容（基本信息、平面图线索、设备表）照录自环评公示稿；"
        "用气需求、竣工时间、跟进建议均为分析推断，不作为合同依据。实际以建设单位确认或政府批复为准。",
        foot_s))

    doc.build(s, onFirstPage=on_first, onLaterPages=on_later)
    print(f"OK: {out}")
    return out


# ════════════════════════════════════════════════════════════════════════════
# 企业微信推送
# ════════════════════════════════════════════════════════════════════════════
def wxwork_text(text):
    r = requests.post(
        f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={WXWORK_KEY}",
        json={"msgtype": "text", "text": {"content": text}},
        timeout=30)
    return r.json()

def wxwork_upload_and_send(filepath, label):
    fname = os.path.basename(filepath)
    with open(filepath, "rb") as f:
        resp = requests.post(
            f"https://qyapi.weixin.qq.com/cgi-bin/webhook/upload_media?key={WXWORK_KEY}&type=file",
            files={"media": (fname, f, "application/pdf")},
            timeout=60)
    data = resp.json()
    if data.get("errcode", -1) != 0:
        print(f"Upload FAIL [{label}]: {data}")
        return False
    print(f"Uploaded [{label}]: {data['media_id']}")
    r2 = requests.post(
        f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={WXWORK_KEY}",
        json={"msgtype": "file", "file": {"media_id": data["media_id"]}},
        timeout=30).json()
    print(f"Send [{label}]: {r2}")
    return r2.get("errcode") == 0


if __name__ == "__main__":
    import datetime
    today = datetime.date.today().strftime("%Y-%m-%d")
    print(f"=== 生成PDF（三木化工风格）· {today} ===")
    pdfs = [
        (make_tianhong(), "SLA-20 天虹金属"),
        (make_lungu(),    "SLA-21 锦绣轮毂"),
        (make_ruileng(),  "SLA-22 瑞翎金属"),
    ]
    print("\n=== 发送企业微信 ===")
    r = wxwork_text(
        f"📋 无锡工业气体销售线索报告 · {today}\n\n"
        "本批次3条线索（三木化工风格数据型报告）：\n"
        "🔴 SLA-20 江阴天虹金属铸造 合金锻件技改\n"
        "   液氧900t + 液氩850t + 液氮600t = 2,350t/年（报告原文）\n\n"
        "🔴 SLA-21 惠山 锦绣轮毂 铝合金轮毂技改\n"
        "   液氩85t/年 + 氦气150瓶/年（报告原文）\n\n"
        "🟡 SLA-22 锡山 瑞翎金属 无氧铜杆+绕组线\n"
        "   氮气储罐标注（用量待电话确认→确认后重新评级）\n\n"
        "Jira看板：https://shengqiupurdue.atlassian.net/jira/software/projects/SLA/boards/2"
    )
    print(f"Text: {r}")
    for path, label in pdfs:
        ok = wxwork_upload_and_send(path, label)
        print(f"  {'OK' if ok else 'FAIL'} {label}")
