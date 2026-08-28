#!/usr/bin/env python3
"""可溯源 HTML 报告渲染器

将公开披露公司数据（akshare）与深知政策/标准检索结果渲染为单文件可溯源 HTML：
- 左栏：投研报告正文（公司概况 / 财务 / 行业对比 / 政策 / 标准）
- 右栏：固定来源面板，点击正文角标 [P1]/[S1] 定位并高亮来源卡
- 政策/标准来源卡带原文链接；金融数据标注"公开披露数据"来源

引用映射采用程序内预分配的稳定 ID（P1..Pn / S1..Sn / F1），
正文角标与来源卡由同一数据结构生成，不做任何按位置猜测。

参考: 深知可信搜索 skill 的 render_trace_html.py 双栏溯源模式
"""

import html
import json
from datetime import datetime
from typing import Any, Dict, List, Optional


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def fmt_num(value: Any, unit: str = "") -> str:
    """数字格式化：亿元换算 / 百分比；空值返回 '--'"""
    if value is None or value == "":
        return "--"
    try:
        num = float(value)
        if unit == "亿":
            return f"{num / 1e8:,.2f} 亿"
        if unit == "%":
            return f"{num:.2f}%"
        return f"{num:,.2f}"
    except (ValueError, TypeError):
        return str(value)


# ============================================================
# 来源注册表：一次遍历生成正文引用与来源卡，保证 ID 一致
# ============================================================

def build_sources(company_data: Dict[str, Any],
                  policy_data: Dict[str, Any]) -> List[Dict[str, str]]:
    """构建稳定来源注册表

    P1..Pn  政策来源（深知可信搜索）
    S1..Sn  标准来源（深知可信搜索）
    F1      金融数据来源（公开披露数据，说明卡）
    """
    sources: List[Dict[str, str]] = []

    for i, item in enumerate(policy_data.get("policyHighlights", []), 1):
        sources.append({
            "id": f"P{i}",
            "type": "policy",
            "title": item.get("title", "未命名政策"),
            "agency": item.get("source", "未标注来源"),
            "date": item.get("date", "未标注日期"),
            "dateNote": item.get("dateNote", ""),
            "url": item.get("url", ""),
            "excerpt": item.get("excerpt", ""),
        })

    for i, item in enumerate(policy_data.get("standardHighlights", []), 1):
        sources.append({
            "id": f"S{i}",
            "type": "standard",
            "title": item.get("title", "未命名标准"),
            "agency": item.get("source", "未标注来源"),
            "date": item.get("date", "未标注日期"),
            "dateNote": item.get("dateNote", ""),
            "url": item.get("url", ""),
            "excerpt": item.get("excerpt", ""),
        })

    basic = company_data.get("basicInfo") or {}
    data_source = company_data.get("dataSource") or "公开披露数据"
    sources.append({
        "id": "F1",
        "type": "finance",
        "title": "公开披露金融数据（akshare）",
        "agency": "同花顺 F10 / 东方财富 / 巨潮资讯（经 akshare 开源库）",
        "date": datetime.now().strftime("%Y-%m-%d 查询"),
        "url": "",
        "excerpt": (
            f"公司画像（公司资料）、关键财务指标（营业总收入/归母净利润/EPS/ROE/资产负债率）"
            f"与行业定位来自公开披露渠道，经 akshare 开源库（同花顺 F10 主源 + 东方财富/巨潮备源）"
            f"获取。本报告 {basic.get('secName', '')}（{basic.get('secCode', '')}）"
            f"全部金融数值均来自该公开数据层，未做任何外部补齐。"
        ),
    })

    return sources


def cite(sid: str, sources: List[Dict[str, str]]) -> str:
    """生成正文角标按钮；ID 不在注册表中标红提示（不猜测）"""
    known = any(s["id"] == sid for s in sources)
    cls = "cite" if known else "cite unresolved"
    label = sid if known else f"{sid}未绑定"
    return f'<button class="{cls}" data-cite="{esc(sid)}" type="button" title="查看来源 {esc(sid)}">[{esc(label)}]</button>'


# ============================================================
# 正文各板块渲染
# ============================================================

def render_basic_info(basic: Optional[Dict[str, Any]]) -> str:
    if not basic:
        return '<p class="empty">未获取到公司基本资料</p>'
    rows = [
        ("证券简称", basic.get("secName")), ("证券代码", basic.get("secCode")),
        ("公司全称", basic.get("orgName")), ("上市日期", str(basic.get("listedDate") or "")[:10]),
        ("所属行业", basic.get("industryName")), ("所属地区", basic.get("provinceName")),
        ("董事长", basic.get("chairMan")), ("总经理", basic.get("manager")),
        ("董事会秘书", basic.get("secretary")), ("员工人数", basic.get("staffNum")),
        ("注册资本(万元)", basic.get("regAsset")),
    ]
    # 只渲染有值的字段，公开数据拿不到的字段不占位显示
    trs = "".join(
        f'<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>'
        for k, v in rows if v not in (None, "")
    )
    if not trs:
        return '<p class="empty">未获取到公司基本资料</p>'
    main_biz = basic.get("mainOprBus") or ""
    biz_html = f'<div class="biz"><span class="label">主营业务</span><p>{esc(main_biz)}</p></div>' if main_buz_ok(main_biz) else ""
    return f'<table class="kv-table"><tbody>{trs}</tbody></table>{biz_html}'


def main_buz_ok(v: str) -> bool:
    return bool(v and str(v).strip())


def render_financials(indicators: Optional[List[Dict[str, Any]]]) -> str:
    if not indicators:
        return '<p class="empty">未获取到关键财务指标</p>'
    header = ("<tr><th>报告期</th><th>营业总收入</th><th>归母净利润</th>"
              "<th>基本EPS</th><th>加权ROE</th><th>资产负债率</th></tr>")
    rows = []
    for it in indicators[:5]:
        rows.append(
            f"<tr><td>{esc(it.get('reportDate', '--'))}</td>"
            f"<td>{fmt_num(it.get('totalRevenue'), '亿')}</td>"
            f"<td>{fmt_num(it.get('netProfitAtsopc'), '亿')}</td>"
            f"<td>{fmt_num(it.get('basicEps'))}</td>"
            f"<td>{fmt_num(it.get('wgtAvgRoe'), '%')}</td>"
            f"<td>{fmt_num(it.get('assetLiabRatio'), '%')}</td></tr>"
        )
    return f'<table class="data-table"><thead>{header}</thead><tbody>{"".join(rows)}</tbody></table>'


METRIC_LABELS = {"jzcsyl": "ROE", "pe": "PE", "pb": "PB", "gmjlr": "归母净利润", "zsr": "总收入"}


def render_rank(metric: str, rank_data: Optional[Dict[str, Any]]) -> str:
    if not rank_data:
        return f'<p class="empty">未获取到该指标行业排名</p>'
    label = METRIC_LABELS.get(metric, metric)
    peers = (rank_data.get("industryList") or [])[:10]
    peer_rows = "".join(
        f"<tr><td>{esc(p.get('rank', ''))}</td><td>{esc(p.get('secName', ''))}</td>"
        f"<td>{esc(p.get('secCode', ''))}</td><td>{esc(p.get('value', ''))}</td></tr>"
        for p in peers
    )
    peers_html = (
        '<table class="data-table compact"><thead><tr><th>排名</th><th>公司</th>'
        f'<th>代码</th><th>{esc(label)}</th></tr></thead><tbody>{peer_rows}</tbody></table>'
        if peer_rows else '<p class="empty">无同业数据</p>'
    )
    return (
        '<div class="rank-card">'
        f'<div class="rank-head"><span class="rank-name">{esc(label)} 行业排名</span>'
        f'<span class="rank-pos">{esc(rank_data.get("industryRank", "--"))}</span></div>'
        f'<div class="rank-meta">所属行业 {esc(rank_data.get("industryName", "--"))} · '
        f'数据日期 {esc(rank_data.get("reportDate", "--"))} · 行业均值 {esc(rank_data.get("industryAvg", "--"))}</div>'
        f'<details class="peer-details"><summary>同业前 10</summary>{peers_html}</details>'
        '</div>'
    )


def render_evidence_items(items: List[Dict[str, str]], id_prefix: str,
                          sources: List[Dict[str, str]]) -> str:
    """政策/标准条目：标题 + 元信息 + 角标（摘录在右侧来源卡）"""
    if not items:
        return '<p class="empty">未检索到相关内容</p>'
    cards = []
    for i, item in enumerate(items, 1):
        sid = f"{id_prefix}{i}"
        fixed = ' <span class="date-fixed" title="发布日期已经多源校验修正">🔧已校验</span>' if item.get("dateNote") else ""
        cards.append(
            '<div class="ev-item">'
            f'<div class="ev-title">{esc(item.get("title", "未命名"))}{cite(sid, sources)}</div>'
            f'<div class="ev-meta">{esc(item.get("source", "未标注来源"))} · {esc(item.get("date", "未标注日期"))}{fixed}</div>'
            '</div>'
        )
    return "".join(cards)


# ============================================================
# 政策影响分析板块（HTML）
# ============================================================

def _direction_class(direction: str) -> str:
    if "利空" in direction or "退坡" in direction:
        return "dir-bear"
    if direction.startswith("利好"):
        return "dir-bull"
    return "dir-neutral"


def render_impact_section(impact_data: Dict[str, Any],
                          sources: List[Dict[str, str]]) -> str:
    """渲染"六、政策影响分析"板块（HTML）"""
    if not impact_data:
        return ""
    summary = impact_data.get("summary", {})
    signals = impact_data.get("signals", [])

    # 财务信号横幅
    signal_html = "".join(
        f'<div class="fin-signal">⚡ {esc(s)}</div>' for s in signals
    ) or ""

    # 总览条
    bull, bear, neutral = summary.get("bull_count", 0), summary.get("bear_count", 0), summary.get("neutral_count", 0)
    overview = (
        '<div class="impact-overview">'
        f'<span class="ov-bull">利好 {bull}</span>'
        f'<span class="ov-bear">利空关注 {bear}</span>'
        f'<span class="ov-neutral">中性/待研判 {neutral}</span>'
        f'<span class="ov-total">共 {summary.get("total", 0)} 条</span>'
        '</div>'
    )

    # 逐条分析卡
    items_html = []
    for a in impact_data.get("policies", []) + impact_data.get("standards", []):
        link_html = ""
        if a.get("financial_link"):
            link_html = f'<div class="ia-link">🔗 {esc(a["financial_link"])}</div>'
        items_html.append(
            f'<details class="ia-card {_direction_class(a["direction"])}" open>'
            f'<summary><span class="ia-dir">{esc(a["direction"])}</span>'
            f'<span class="ia-title">{esc(a["title"])}{cite(a["sid"], sources)}</span>'
            f'<span class="ia-meta">{esc(a["type_label"])} · 时间窗口 {esc(a["time_window"])}</span></summary>'
            f'<div class="ia-body">'
            f'<div class="ia-row"><span class="ia-k">传导链</span><span>{esc(a["chain"])}</span></div>'
            f'<div class="ia-row"><span class="ia-k">影响变量</span><span>{esc("、".join(a["variables"]))}</span></div>'
            f'{link_html}'
            f'<div class="ia-row"><span class="ia-k">跟踪指标</span><span>{esc(a["tracking"])}</span></div>'
            f'<div class="ia-row hl-row"><span class="ia-k">投资含义</span><span>{esc(a["investment_view"])}</span></div>'
            '</div></details>'
        )
    items = "".join(items_html) or '<p class="empty">无影响分析数据</p>'

    return (
        '<section class="section impact">'
        '<h2><span class="no impact-no">六</span>政策影响分析 <span class="src-badge">投资视角 · 判断可溯源</span></h2>'
        '<p class="lead">把第四、五部分的检索结果翻译为投资判断：方向 / 传导链 / 影响变量 / 跟踪指标 / 投资含义。点击角标回溯原文。</p>'
        f'{signal_html}{overview}{items}'
        '<div class="impact-guide"><b>如何使用</b>'
        '<span>建模：把「时间窗口」写入假设依据 · 跟踪：按「跟踪指标」建监测清单 · '
        '归因：财报异常对照传导链拆解 · 核验：点击角标查官方原文。'
        '传导分析为规则模板生成，仅供研究参考，不构成投资建议。</span></div>'
        '</section>'
    )


# ============================================================
# 右栏来源面板
# ============================================================

TYPE_LABELS = {"policy": "政策", "standard": "标准", "finance": "金融数据"}


def render_source_cards(sources: List[Dict[str, str]]) -> str:
    cards = []
    for s in sources:
        url_html = (
            f'<a href="{esc(s["url"])}" target="_blank" rel="noopener">查看原文 ↗</a>'
            if s.get("url") else '<span class="no-url">无原文链接</span>'
        )
        excerpt = esc(s.get("excerpt", "") or "（无摘录）")
        note = s.get("dateNote") or ""
        note_html = f'<div class="sc-datenote">🔧 {esc(note)}</div>' if note else ""
        cards.append(
            f'<article class="source-card" id="src-{esc(s["id"])}" data-type="{esc(s["type"])}">'
            f'<div class="sc-head"><span class="sc-id">{esc(s["id"])}</span>'
            f'<span class="sc-type type-{esc(s["type"])}">{TYPE_LABELS.get(s["type"], s["type"])}</span></div>'
            f'<h4>{esc(s["title"])}</h4>'
            f'<div class="sc-meta">{esc(s["agency"])} · {esc(s["date"])}</div>'
            f'{note_html}'
            f'<p class="sc-excerpt">{excerpt}</p>'
            f'<div class="sc-links">{url_html}</div>'
            '</article>'
        )
    return "".join(cards)


# ============================================================
# 主渲染函数
# ============================================================

def generate_report_html(stock_code: str,
                         company_data: Dict[str, Any],
                         policy_data: Dict[str, Any],
                         impact_data: Optional[Dict[str, Any]] = None,
                         generated_at: Optional[datetime] = None) -> str:
    generated_at = generated_at or datetime.now()
    basic = company_data.get("basicInfo") or {}
    company_name = basic.get("secName", stock_code)
    industry = basic.get("industryName", "--")
    sources = build_sources(company_data, policy_data)

    finance_badge = (
        f'<span class="src-badge" data-cite-target="F1">数据来源：公开披露数据（akshare） {cite("F1", sources)}</span>'
    )

    rank_sections = "".join(
        render_rank(m, rd)
        for m, rd in (company_data.get("industryRanks") or {}).items()
    ) or '<p class="empty">未获取行业排名</p>'

    pol_count = len(policy_data.get("policyHighlights", []))
    std_count = len(policy_data.get("standardHighlights", []))
    impact_html = render_impact_section(impact_data, sources) if impact_data else ""

    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(company_name)}（{esc(stock_code)}）深度研究报告 · 可溯源版</title>
<style>
:root{{
  --navy:#0b1f3a; --ink:#172236; --muted:#667085; --line:#dce4ef;
  --brand:#1559c7; --brand-soft:#e8f0ff;
  --policy:#0c9b78; --policy-soft:#e2f6ef;
  --std:#b26a00; --std-soft:#fff4e0;
  --fin:#6d3cc7; --fin-soft:#f1ebfd;
  --warn:#b93939;
}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  color:var(--ink);background:#f4f7fb;line-height:1.7;font-size:14.5px}}
a{{color:var(--brand)}}
.topbar{{position:sticky;top:0;z-index:10;display:flex;justify-content:space-between;align-items:center;
  padding:10px 22px;background:var(--navy);color:#fff;font-size:13px}}
.topbar .tags span{{display:inline-block;margin-left:6px;padding:2px 10px;border:1px solid rgba(255,255,255,.3);
  border-radius:999px;font-size:11.5px}}
.layout{{display:grid;grid-template-columns:minmax(0,760px) minmax(340px,420px);gap:26px;
  max-width:1240px;margin:0 auto;padding:26px 18px 60px}}
/* 左栏 报告 */
.report{{min-width:0}}
.r-head{{padding:22px 26px;background:#fff;border:1px solid var(--line);border-radius:12px;margin-bottom:16px}}
.r-head h1{{margin:0 0 6px;font-size:22px;color:var(--navy)}}
.r-head .sub{{color:var(--muted);font-size:13px}}
.section{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:20px 24px;margin-bottom:16px}}
.section h2{{margin:0 0 4px;font-size:16.5px;color:var(--navy);display:flex;align-items:center;gap:8px}}
.section h2 .no{{display:inline-flex;width:24px;height:24px;border-radius:6px;background:var(--brand);
  color:#fff;align-items:center;justify-content:center;font-size:13px}}
.section .lead{{margin:0 0 12px;font-size:12.5px;color:var(--muted)}}
.src-badge{{display:inline-flex;align-items:center;gap:2px;font-size:11.5px;color:var(--fin);
  background:var(--fin-soft);border-radius:6px;padding:2px 8px}}
.kv-table{{width:100%;border-collapse:collapse;font-size:13px}}
.kv-table th,.kv-table td{{border:1px solid var(--line);padding:7px 10px;text-align:left}}
.kv-table th{{background:#f2f5fb;width:130px;color:var(--navy)}}
.biz{{margin-top:10px;padding:10px 12px;background:#f8faff;border-radius:8px}}
.biz .label{{font-size:11.5px;color:var(--brand);font-weight:700}}
.biz p{{margin:4px 0 0;font-size:13px}}
.data-table{{width:100%;border-collapse:collapse;font-size:12.5px}}
.data-table th,.data-table td{{border:1px solid var(--line);padding:7px 9px;text-align:left;white-space:nowrap}}
.data-table th{{background:#f2f5fb;color:var(--navy)}}
.data-table.compact th,.data-table.compact td{{padding:5px 8px}}
.rank-card{{border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:10px;background:#fbfcfe}}
.rank-head{{display:flex;justify-content:space-between;align-items:center}}
.rank-name{{font-weight:700;color:var(--navy);font-size:13.5px}}
.rank-pos{{font-family:monospace;font-weight:800;color:var(--brand);font-size:15px}}
.rank-meta{{font-size:12px;color:var(--muted);margin:3px 0 6px}}
.peer-details summary{{cursor:pointer;font-size:12.5px;color:var(--brand)}}
.ev-item{{border:1px solid var(--line);border-left:3px solid var(--policy);border-radius:8px;
  padding:10px 12px;margin-bottom:8px;background:#fff}}
.std .ev-item{{border-left-color:var(--std)}}
.ev-title{{font-size:13.5px;font-weight:600;color:var(--navy)}}
.ev-meta{{font-size:12px;color:var(--muted);margin-top:2px}}
.empty{{color:var(--muted);font-size:13px;margin:6px 0}}
/* 发布日期校验标记 */
.date-fixed{{display:inline-block;margin-left:5px;padding:0 6px;border-radius:4px;background:#fff4e0;
  border:1px solid #f2ddb8;color:#b26a00;font-size:10.5px;font-weight:700;cursor:help}}
.sc-datenote{{margin:5px 0;padding:7px 10px;border:1px solid #f2ddb8;border-radius:7px;
  background:#fff8ec;font-size:11.8px;color:#7a4d00;line-height:1.6}}
/* 政策影响分析板块 */
.fin-signal{{margin:0 0 10px;padding:9px 13px;border:1px solid #f2ddb8;border-left:4px solid #b26a00;
  border-radius:8px;background:#fff8ec;font-size:12.8px;color:#7a4d00}}
.impact-overview{{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 12px}}
.impact-overview span{{border-radius:999px;padding:3px 12px;font-size:12px;font-weight:700}}
.ov-bull{{background:var(--policy-soft);color:var(--policy)}}
.ov-bear{{background:#fdecea;color:var(--warn)}}
.ov-neutral{{background:#eef1f6;color:#4b5563}}
.ov-total{{background:var(--brand-soft);color:var(--brand)}}
.ia-card{{border:1px solid var(--line);border-radius:10px;margin-bottom:9px;background:#fff;overflow:hidden;
  border-left:4px solid var(--muted)}}
.ia-card.dir-bull{{border-left-color:var(--policy)}}
.ia-card.dir-bear{{border-left-color:var(--warn)}}
.ia-card summary{{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:10px 13px;cursor:pointer;list-style:none}}
.ia-card summary::-webkit-details-marker{{display:none}}
.ia-dir{{flex:none;border-radius:5px;padding:1px 8px;font-size:11px;font-weight:800;white-space:nowrap}}
.dir-bull .ia-dir{{background:var(--policy-soft);color:var(--policy)}}
.dir-bear .ia-dir{{background:#fdecea;color:var(--warn)}}
.dir-neutral .ia-dir{{background:#eef1f6;color:#4b5563}}
.ia-title{{font-size:13.5px;font-weight:700;color:var(--navy);min-width:200px;flex:1}}
.ia-meta{{font-size:11.5px;color:var(--muted);width:100%}}
.ia-body{{padding:2px 13px 12px;border-top:1px dashed var(--line)}}
.ia-row{{display:flex;gap:10px;margin-top:8px;font-size:12.6px;line-height:1.65}}
.ia-k{{flex:none;width:62px;font-weight:700;color:var(--brand)}}
.ia-link{{margin-top:8px;padding:8px 11px;border-radius:7px;background:var(--fin-soft);
  font-size:12.3px;color:#5b3aa8}}
.hl-row span{{color:var(--navy)}}
.impact-guide{{margin-top:12px;padding:10px 13px;border:1px dashed var(--line-strong);border-radius:9px;
  font-size:11.8px;color:var(--muted);line-height:1.7}}
.impact-guide b{{color:var(--navy);margin-right:6px}}
.impact-no{{background:var(--policy)!important}}
.risk{{background:#fff8ec;border:1px solid #f2ddb8;border-radius:12px;padding:14px 18px;
  font-size:12.5px;color:#7a4d00}}
.risk b{{display:block;margin-bottom:4px}}
.cite{{margin-left:4px;padding:0 6px;border:0;border-radius:4px;background:var(--brand-soft);
  color:var(--brand);font-weight:800;font-size:10.5px;cursor:pointer;vertical-align:super;font-family:monospace}}
.cite:hover,.cite:focus-visible{{background:var(--brand);color:#fff;outline:none}}
.cite.unresolved{{background:#fdecea;color:var(--warn)}}
/* 右栏 来源面板 */
.sources{{position:sticky;top:56px;align-self:start;max-height:calc(100vh - 76px);overflow:auto;
  background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px 18px}}
.sources h3{{margin:0 0 4px;font-size:15px;color:var(--navy)}}
.sources .count{{font-size:12px;color:var(--muted);margin-bottom:10px}}
.filters{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}}
.filters button{{border:1px solid var(--line);border-radius:999px;background:#fff;color:var(--muted);
  padding:3px 11px;font-size:12px;cursor:pointer}}
.filters button.on{{background:var(--brand);border-color:var(--brand);color:#fff;font-weight:700}}
.src-search{{width:100%;padding:7px 10px;border:1px solid var(--line);border-radius:7px;
  font-size:12.5px;margin-bottom:12px}}
.source-card{{border:1px solid var(--line);border-radius:10px;padding:11px 13px;margin-bottom:10px;
  border-left-width:3px;border-left-color:var(--policy);transition:box-shadow .25s}}
.source-card[data-type="standard"]{{border-left-color:var(--std)}}
.source-card[data-type="finance"]{{border-left-color:var(--fin)}}
.source-card.hl{{box-shadow:0 0 0 3px var(--brand)}}
.source-card.hide{{display:none}}
.sc-head{{display:flex;align-items:center;gap:8px;margin-bottom:4px}}
.sc-id{{font-family:monospace;font-weight:800;font-size:11px;color:var(--brand);
  background:var(--brand-soft);border-radius:4px;padding:1px 7px}}
.sc-type{{font-size:11px;font-weight:700;border-radius:4px;padding:1px 8px}}
.type-policy{{background:var(--policy-soft);color:var(--policy)}}
.type-standard{{background:var(--std-soft);color:var(--std)}}
.type-finance{{background:var(--fin-soft);color:var(--fin)}}
.source-card h4{{margin:0 0 4px;font-size:13px;color:var(--navy);line-height:1.5}}
.sc-meta{{font-size:11.5px;color:var(--muted)}}
.sc-excerpt{{font-size:12px;color:#42566f;margin:6px 0;line-height:1.65;
  display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}}
.source-card.open .sc-excerpt{{display:block}}
.sc-links a{{font-size:12px;font-weight:700;text-decoration:none}}
.no-url{{font-size:11.5px;color:var(--muted)}}
.foot{{text-align:center;color:var(--muted);font-size:11.5px;padding:14px 0 4px}}
@media (max-width:1020px){{
  .layout{{grid-template-columns:1fr}}
  .sources{{position:static;max-height:none}}
}}
@media print{{
  .topbar,.filters,.src-search{{display:none!important}}
  .layout{{grid-template-columns:1fr;max-width:none;padding:0}}
  .sources{{position:static;max-height:none;border:0}}
  .sc-excerpt{{display:block}}
  body{{background:#fff}}
}}
</style>
</head>
<body>

<header class="topbar">
  <div>{esc(company_name)}（{esc(stock_code)}）深度研究报告 · 可溯源版</div>
  <div class="tags"><span>生成于 {generated_at.strftime('%Y-%m-%d %H:%M')}</span><span>金融数据：公开披露数据（akshare）</span><span>政策标准：深知可信搜索</span></div>
</header>

<div class="layout">
  <main class="report">
    <div class="r-head">
      <h1>{esc(company_name)}（{esc(stock_code)}）深度研究报告</h1>
      <div class="sub">所属行业：{esc(industry)} · 检索地域：{esc(policy_data.get('serviceArea', '--'))} · 政策时间口径：{esc(policy_data.get('effTime', '--'))}</div>
      <div class="sub">点击正文角标 [P1]/[S1]/[F1] 可在右侧来源面板定位对应材料原文。</div>
    </div>

    <section class="section">
      <h2><span class="no">一</span>公司概况 {finance_badge}</h2>
      <p class="lead">公司画像来自公开披露数据（同花顺 F10 / 东方财富 / 巨潮资讯，经 akshare）</p>
      {render_basic_info(basic)}
    </section>

    <section class="section">
      <h2><span class="no">二</span>关键财务指标 {finance_badge}</h2>
      <p class="lead">最近 5 期 · 累计口径 · 同花顺 F10 主源 + 东方财富 ROE/负债率补充</p>
      {render_financials(company_data.get('keyIndicators'))}
    </section>

    <section class="section">
      <h2><span class="no">三</span>行业对比 {finance_badge}</h2>
      <p class="lead">板块规模位次来自公开披露数据（同花顺板块成分，经 akshare）；指标排名口径见来源卡</p>
      {rank_sections}
    </section>

    <section class="section">
      <h2><span class="no">四</span>政策环境 <span class="src-badge">深知可信搜索 · 命中 {pol_count} 条</span></h2>
      <p class="lead">检索词：{esc(policy_data.get('industryName', ''))} 支持政策 补贴 税收优惠 企业适用条件 · 摘录与原文链接见右侧来源卡</p>
      {render_evidence_items(policy_data.get('policyHighlights', []), 'P', sources)}
    </section>

    <section class="section std">
      <h2><span class="no">五</span>标准与准入 <span class="src-badge">深知可信搜索 · 命中 {std_count} 条</span></h2>
      <p class="lead">检索词：{esc(policy_data.get('industryName', ''))} 国家标准 行业规范 准入条件 技术规范</p>
      {render_evidence_items(policy_data.get('standardHighlights', []), 'S', sources)}
    </section>

    {impact_html}

    <div class="risk"><b>风险提示</b>
      本报告基于公开披露数据与深知可信检索结果整理生成，仅供信息查询与研究参考，不构成任何投资建议、证券推荐或收益承诺。
      金融数据以上市公司正式公告为准，政策与标准内容来自深知可信搜索检索，现行有效性以官方发布原文为准。
      大模型可能存在理解偏差或生成不准确的情况，请结合上市公司正式公告等权威披露文件核查确认。市场有风险，投资需谨慎。
    </div>

    <div class="foot">深知可信投研 · dknowc-trusted-investment-research · 公开披露金融数据 + 深知政策标准洞察</div>
  </main>

  <aside class="sources" aria-label="来源面板">
    <h3>来源材料（{len(sources)}）</h3>
    <div class="count">P=政策 · S=标准 · F=金融数据 · 点击正文角标定位</div>
    <div class="filters" role="group" aria-label="来源筛选">
      <button class="on" data-f="all" type="button">全部</button>
      <button data-f="policy" type="button">政策</button>
      <button data-f="standard" type="button">标准</button>
      <button data-f="finance" type="button">金融数据</button>
    </div>
    <input class="src-search" type="search" placeholder="搜索标题 / 机构 / 摘录…" aria-label="搜索来源">
    <div class="src-list">{render_source_cards(sources)}</div>
  </aside>
</div>

<script>
(function () {{
  "use strict";
  var cards = Array.prototype.slice.call(document.querySelectorAll(".source-card"));
  var byId = {{}};
  var dup = [];
  cards.forEach(function (c) {{
    var id = c.id.replace(/^src-/, "");
    if (byId[id]) {{ dup.push(id); }}
    byId[id] = c;
  }});
  if (dup.length && console.warn) {{ console.warn("[sources] duplicate ids:", dup); }}

  /* 角标 → 来源卡：精确 ID 匹配，缺失即提示，绝不按位置猜测 */
  document.querySelectorAll("[data-cite]").forEach(function (btn) {{
    btn.addEventListener("click", function () {{
      var card = byId[btn.getAttribute("data-cite")];
      if (!card) {{
        btn.classList.add("unresolved");
        if (console.error) {{ console.error("[cite] unresolved:", btn.getAttribute("data-cite")); }}
        return;
      }}
      card.classList.remove("hide");
      card.classList.remove("open");
      card.classList.add("hl", "open");
      card.scrollIntoView({{ block: "center", behavior: "smooth" }});
      setTimeout(function () {{ card.classList.remove("hl"); }}, 2200);
    }});
  }});

  /* 筛选 + 搜索 */
  var filter = "all";
  var kw = "";
  function apply() {{
    cards.forEach(function (c) {{
      var okType = filter === "all" || c.getAttribute("data-type") === filter;
      var okKw = !kw || (c.textContent || "").toLowerCase().indexOf(kw) !== -1;
      c.classList.toggle("hide", !(okType && okKw));
    }});
  }}
  document.querySelectorAll(".filters button").forEach(function (b) {{
    b.addEventListener("click", function () {{
      document.querySelectorAll(".filters button").forEach(function (x) {{ x.classList.remove("on"); }});
      b.classList.add("on");
      filter = b.getAttribute("data-f");
      apply();
    }});
  }});
  var search = document.querySelector(".src-search");
  if (search) {{
    search.addEventListener("input", function () {{ kw = search.value.trim().toLowerCase(); apply(); }});
  }}

  if (console.info) {{
    console.info("[report] sources:", cards.length, "unique:", Object.keys(byId).length, "dup:", dup.length);
  }}
}})();
</script>
</body>
</html>"""
    return html_doc


if __name__ == "__main__":
    # 独立运行：从 JSON 文件渲染（run_research.py 主流程会程序化调用）
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description="从研究数据 JSON 渲染可溯源 HTML 报告")
    ap.add_argument("data_json", help="run_research --save-data 输出的 JSON 文件")
    ap.add_argument("output", help="输出 HTML 路径")
    args = ap.parse_args()

    payload = json.loads(Path(args.data_json).read_text(encoding="utf-8"))
    doc = generate_report_html(
        payload["stockCode"],
        payload["companyData"],
        payload["policyData"],
        impact_data=payload.get("impactData"),  # 旧快照无此字段时自动跳过该板块
    )
    Path(args.output).write_text(doc, encoding="utf-8")
    print(f"HTML 已生成: {args.output}")
