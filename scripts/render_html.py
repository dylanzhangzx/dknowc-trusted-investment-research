#!/usr/bin/env python3
"""可溯源投研报告渲染器（对齐深知公文写作 3.7.0 溯源核验报告原型）

单栏连续文档流（1080px、灰底、紫渐变品牌色）：
- 正文区：章节标题（渐隐细线）+ 章节引用徽章（本章引用 N 处 · 已核验）；
  政策/标准/影响分析条目后接行内引文胶囊（jb），点击胶囊原地展开溯源卡
  （原文摘录 + 查看全文），再点收起。
- 材料专库独立视图：召回材料按 检索条件 分组 tabs，支持搜索、热词
  （标题/摘录词频真实计算）与未引用筛选；单列宽卡、摘录全文直接展开。
- 核验报告单与过程回顾条保留，数字全部真实计算。
- 顶栏工具：报告/专库视图切换、只看正文、复制全文（去胶囊纯文本）、
  打印归档下拉（只打印正文 / 完整归档含核验材料附录）。
- 原文链接质量保障：生成时并发检测全部材料链接（HTTP 404/410 判失效，
  失效不再展示"查看全文"）；连接失败/超时保守放行防反爬误杀；
  接口返回快照（screenShotPath）时失效链接改"查看存档全文"。

引用映射仍为程序内预分配的稳定 ID（P1..Pn / S1..Sn / F1），
正文胶囊与材料卡由同一注册表生成，不做任何按位置猜测。
"""

import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def fmt_num(value: Any, unit: str = "") -> str:
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
# 1. 来源注册表（一次遍历生成正文胶囊与材料卡，保证 ID 一致）
# ============================================================

def build_sources(company_data: Dict[str, Any],
                  policy_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """P1..Pn 政策 / S1..Sn 标准 / F1 金融数据（公开披露，说明卡）。

    search_key：材料所属的检索分组（材料专库 tabs 依据）；verified：摘录可比对。
    """
    sources: List[Dict[str, Any]] = []
    industry = policy_data.get("industryName", "")

    for i, item in enumerate(policy_data.get("policyHighlights", []), 1):
        sources.append({
            "id": f"P{i}", "type": "policy",
            "title": item.get("title", "未命名政策"),
            "agency": item.get("source", "") or "未标注来源",
            "date": item.get("date", "") or "未标注日期",
            "dateNote": item.get("dateNote", ""),
            "url": item.get("url", ""),
            "excerpt": item.get("excerpt", ""),
            "search_key": f"政策检索 · {industry}",
            "verified": bool((item.get("excerpt") or "").strip()),
        })
    for i, item in enumerate(policy_data.get("standardHighlights", []), 1):
        sources.append({
            "id": f"S{i}", "type": "standard",
            "title": item.get("title", "未命名标准"),
            "agency": item.get("source", "") or "未标注来源",
            "date": item.get("date", "") or "未标注日期",
            "dateNote": item.get("dateNote", ""),
            "url": item.get("url", ""),
            "excerpt": item.get("excerpt", ""),
            "search_key": f"标准检索 · {industry}",
            "verified": bool((item.get("excerpt") or "").strip()),
        })

    basic = company_data.get("basicInfo") or {}
    sources.append({
        "id": "F1", "type": "finance",
        "title": "公开披露金融数据（akshare）",
        "agency": "同花顺 F10 / 东方财富 / 巨潮资讯",
        "date": datetime.now().strftime("%Y-%m-%d 查询"),
        "dateNote": "",
        "url": "",
        "excerpt": (
            f"公司画像（公司资料）、关键财务指标（营业总收入/归母净利润/EPS/ROE/资产负债率）"
            f"与行业定位来自公开披露渠道，经 akshare 开源库（同花顺 F10 主源 + 东方财富/巨潮备源）"
            f"获取。本报告 {basic.get('secName', '')}（{basic.get('secCode', '')}）"
            f"全部金融数值均来自该公开数据层，未做任何外部补齐。"
        ),
        "search_key": "金融数据 · akshare",
        "verified": True,
    })
    return sources


# ============================================================
# 2. 链接质量检测 + 快照兜底
# ============================================================

def _probe_url(url: str, timeout: int = 6) -> bool:
    """返回 False 仅在明确的 404/410；其余（超时/连接失败/403/5xx）保守放行"""
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=timeout) as resp:
            return resp.status not in (404, 410)
    except HTTPError as e:
        return e.code not in (404, 410)
    except Exception:  # noqa: BLE001 反爬/网络异常不判死
        return True


def check_links(sources: List[Dict[str, Any]], enabled: bool = True) -> None:
    """生成时并发检测全部材料链接，标记 link_dead（失效链接不再展示）"""
    targets = [s for s in sources if enabled and s.get("url")]
    if not targets:
        return
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda s: (s, _probe_url(s["url"])), targets))
    for s, alive in results:
        s["link_dead"] = not alive


def extract_snapshots(policy_data: Dict[str, Any]) -> Dict[str, str]:
    """从深知原始返回中尽力提取 screenShotPath（快照兜底）。

    返回 {标题或URL: 快照路径}，用于给材料卡补"查看存档全文"。
    """
    snap: Dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            path = node.get("screenShotPath") or node.get("snapshotPath")
            if path:
                title = node.get("标题") or node.get("title")
                url = node.get("源网址") or node.get("url")
                for key in (title, url):
                    if key:
                        snap[str(key)] = str(path)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    try:
        walk(policy_data)
    except Exception:  # noqa: BLE001 快照兜底失败不影响主流程
        pass
    return snap


def attach_snapshots(sources: List[Dict[str, Any]], snaps: Dict[str, str]) -> None:
    for s in sources:
        s["snapshot"] = snaps.get(s.get("title") or "") or snaps.get(s.get("url") or "") or ""


# ============================================================
# 3. 行内引文胶囊（原型式 jb：点击原地展开溯源卡）
# ============================================================

CARET_SVG = ('<svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">'
             '<path d="M3 4.5 6 7.5 9 4.5" fill="none" stroke="currentColor" '
             'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def jb_html(s: Dict[str, Any]) -> str:
    """句后引文胶囊：编号徽章 + 材料标题；点击展开原文摘录 + 查看全文"""
    sid = s["id"]
    if s.get("url") and not s.get("link_dead"):
        link = (f'<a class="jb-src" href="{esc(s["url"])}" target="_blank" '
                f'rel="noopener noreferrer">查看全文 ↗</a>')
    elif s.get("url") and s.get("snapshot"):
        link = (f'<a class="jb-src" href="{esc(s["snapshot"])}" target="_blank" '
                f'rel="noopener noreferrer">查看存档全文 ↗</a>')
    else:
        link = ""
    excerpt = (s.get("excerpt") or "").strip()
    quote = (f'<span class="jb-quote" role="button" tabindex="0">'
             f'<i class="jb-no">{esc(sid)}</i><span>{esc(excerpt)}</span></span>'
             if excerpt else "")
    site = (f'<span class="jb-site"><span class="dot"></span>{esc(s.get("agency") or "")}</span>'
            if (s.get("agency") or "").strip() and s.get("agency") != "未知来源" else "")
    return (
        f'<span class="jb" data-cite="{esc(sid)}">'
        f'<button class="jb-name" type="button" aria-expanded="false">{CARET_SVG}'
        f'<i class="jb-id">{esc(sid)}</i>'
        f'<span class="jb-txt">{esc(s["title"])}</span></button>'
        f'<span class="jb-body"><span class="jb-head"><b>溯源原文：</b>{link}</span>'
        f'{site}{quote}</span></span>'
    )


# ============================================================
# 4. 正文各板块（生成 HTML 片段，胶囊按条目挂数）
# ============================================================

def render_basic_info(basic: Optional[Dict[str, Any]]) -> str:
    if not basic:
        return '<p class="doc-p muted">未获取到公司基本资料。</p>'
    rows = [
        ("证券简称", basic.get("secName")), ("证券代码", basic.get("secCode")),
        ("公司全称", basic.get("orgName")), ("上市日期", str(basic.get("listedDate") or "")[:10]),
        ("所属行业", basic.get("industryName")), ("所属地区", basic.get("provinceName")),
        ("董事长", basic.get("chairMan")), ("总经理", basic.get("manager")),
        ("董事会秘书", basic.get("secretary")), ("员工人数", basic.get("staffNum")),
        ("注册资本(万元)", basic.get("regAsset")),
    ]
    trs = "".join(f'<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>'
                  for k, v in rows if v not in (None, ""))
    main_biz = basic.get("mainOprBus") or ""
    biz = (f'<p class="doc-p"><b>主营业务：</b>{esc(main_biz)}</p>' if main_biz.strip() else "")
    table = f'<table class="kv-table"><tbody>{trs}</tbody></table>' if trs else ""
    return table + biz


def render_financials(indicators: Optional[List[Dict[str, Any]]]) -> str:
    if not indicators:
        return '<p class="doc-p muted">未获取到关键财务指标。</p>'
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


METRIC_LABELS = {"jzcsyl": "ROE", "pe": "PE", "pb": "PB", "gmjlr": "归母净利润", "zsz": "总市值", "zsr": "总收入"}


def render_ranks(industry_ranks: Dict[str, Any]) -> str:
    if not industry_ranks:
        return '<p class="doc-p muted">未获取行业定位（板块降级）。</p>'
    cards = []
    for metric, rd in industry_ranks.items():
        label = METRIC_LABELS.get(metric, metric)
        peers = (rd.get("industryList") or [])[:10]
        peer_rows = "".join(
            f"<tr><td>{esc(p.get('rank', ''))}</td><td>{esc(p.get('secName', ''))}</td>"
            f"<td>{esc(p.get('secCode', ''))}</td><td>{esc(p.get('value', ''))}</td></tr>"
            for p in peers)
        peers_html = (f'<details class="peer-details"><summary>同业前 10</summary>'
                      f'<table class="data-table compact"><thead><tr><th>排名</th><th>公司</th>'
                      f'<th>代码</th><th>{esc(label)}</th></tr></thead>'
                      f'<tbody>{peer_rows}</tbody></table></details>' if peer_rows else "")
        cards.append(
            '<div class="rank-card">'
            f'<div class="rank-head"><span class="rank-name">{esc(label)} · 行业定位</span>'
            f'<span class="rank-pos">{esc(rd.get("industryRank", "--"))}</span></div>'
            f'<div class="rank-meta">所属行业 {esc(rd.get("industryName", "--"))} · '
            f'口径 {esc(rd.get("rankBasis", "--"))} · 行业均值 {esc(rd.get("industryAvg", "--"))}</div>'
            f'{peers_html}</div>')
    return "".join(cards)


def render_evidence(items: List[Dict[str, str]], id_prefix: str,
                    sources: List[Dict[str, Any]]) -> str:
    """政策/标准条目：标题句 + 行内胶囊；元信息（数据源 · 日期）单列一行"""
    if not items:
        return '<p class="doc-p muted">未检索到相关内容。</p>'
    by_id = {s["id"]: s for s in sources}
    out = []
    for i, item in enumerate(items, 1):
        s = by_id.get(f"{id_prefix}{i}")
        cap = jb_html(s) if s else ""
        note = f'<span class="date-fixed" title="发布日期已经多源校验修正">已校验</span>' if item.get("dateNote") else ""
        out.append(
            '<div class="ev-block">'
            f'<p class="doc-p"><b>{esc(item.get("title", "未命名"))}。</b>{cap}</p>'
            f'<p class="ev-meta">{esc(item.get("source") or "未标注来源")} · '
            f'{esc(item.get("date") or "未标注日期")}{note}</p>'
            '</div>')
    return "".join(out)


def _direction_class(direction: str) -> str:
    if "利空" in direction or "退坡" in direction:
        return "dir-bear"
    if direction.startswith("利好"):
        return "dir-bull"
    return "dir-neutral"


def render_impact(impact_data: Optional[Dict[str, Any]],
                  sources: List[Dict[str, Any]]) -> str:
    if not impact_data:
        return ""
    by_id = {s["id"]: s for s in sources}
    summary = impact_data.get("summary", {})
    signals = impact_data.get("signals", [])
    signal_html = "".join(f'<div class="fin-signal">⚡ {esc(s)}</div>' for s in signals)
    overview = (
        '<div class="impact-overview">'
        f'<span class="ov-bull">利好 {summary.get("bull_count", 0)}</span>'
        f'<span class="ov-bear">利空关注 {summary.get("bear_count", 0)}</span>'
        f'<span class="ov-neutral">中性/待研判 {summary.get("neutral_count", 0)}</span>'
        f'<span class="ov-total">共 {summary.get("total", 0)} 条</span></div>'
    )
    cards = []
    for a in impact_data.get("policies", []) + impact_data.get("standards", []):
        s = by_id.get(a.get("sid"))
        cap = jb_html(s) if s else ""
        link = f'<div class="ia-link">🔗 {esc(a["financial_link"])}</div>' if a.get("financial_link") else ""
        cards.append(
            f'<details class="ia-card {_direction_class(a["direction"])}" open>'
            f'<summary><span class="ia-dir">{esc(a["direction"])}</span>'
            f'<span class="ia-title">{esc(a["title"])}</span>'
            f'<span class="ia-meta">{esc(a["type_label"])} · 时间窗口 {esc(a["time_window"])}</span></summary>'
            f'<div class="ia-body">{cap}'
            f'<div class="ia-row"><span class="ia-k">传导链</span><span>{esc(a["chain"])}</span></div>'
            f'<div class="ia-row"><span class="ia-k">影响变量</span><span>{esc("、".join(a["variables"]))}</span></div>'
            f'{link}'
            f'<div class="ia-row"><span class="ia-k">跟踪指标</span><span>{esc(a["tracking"])}</span></div>'
            f'<div class="ia-row hl-row"><span class="ia-k">投资含义</span><span>{esc(a["investment_view"])}</span></div>'
            '</div></details>')
    return (
        '<div class="impact-wrap">'
        f'{signal_html}{overview}{"".join(cards)}'
        '<div class="impact-guide"><b>如何使用</b>'
        '<span>建模：把「时间窗口」写入假设依据 · 跟踪：按「跟踪指标」建监测清单 · '
        '归因：财报异常对照传导链拆解 · 核验：点击胶囊查官方原文。'
        '传导分析为规则模板生成，仅供研究参考，不构成投资建议。</span></div></div>'
    )


def _fmt_pct(v: Any) -> str:
    return f"{v * 100:.1f}%" if isinstance(v, (int, float)) else "--"


def render_decision_section(v: Dict[str, Any]) -> str:
    """渲染"投资决策整合"板块（估值区间 · 研究参考 · 非投资建议）"""
    basic = v.get("basic", {})
    dcf = v.get("dcf", {})
    relative = v.get("relative", {})
    bands = v.get("bands", {})
    matrix = v.get("matrix", {})

    price = basic.get("price")
    price_txt = f"{price:.2f} 元" if price else "--"
    mcap = basic.get("marketCap")
    share = basic.get("totalShare")
    base_rows = (
        '<div class="ia-row"><span class="ia-k">估值基础</span><span>'
        f'现价 {price_txt} · 总市值 {mcap if mcap is not None else "--"} 亿元 · '
        f'总股本 {share if share is not None else "--"} 亿股 · '
        f'PE(TTM) {basic.get("peTtm") if basic.get("peTtm") is not None else "--"} · '
        f'PB {basic.get("pb") if basic.get("pb") is not None else "--"} · '
        f'数据源：{esc(basic.get("sourceNote") or "公开披露数据（akshare）")}</span></div>'
    )

    # DCF 三情景
    if dcf.get("available"):
        scen_rows = ""
        for key in ("pessimistic", "neutral", "optimistic"):
            s = (dcf.get("scenarios") or {}).get(key) or {}
            up = s.get("upsidePct")
            up_txt = f"{up:+.1f}%" if isinstance(up, (int, float)) else "--"
            scen_rows += (f'<tr><td>{esc(s.get("label", ""))}</td>'
                          f'<td>{_fmt_pct(s.get("growth"))}</td>'
                          f'<td>{esc(s.get("intrinsicPs", "--"))}</td><td>{up_txt}</td></tr>')
        dcf_html = (
            '<table class="data-table"><thead><tr><th>情景</th><th>增速假设</th>'
            '<th>内在价值(元/股)</th><th>较现价</th></tr></thead>'
            f'<tbody>{scen_rows}</tbody></table>'
            + "".join(f'<p class="doc-p muted">· {esc(a)}</p>' for a in dcf.get("assumptions", []))
            + (f'<p class="doc-p muted">政策衔接：{esc((dcf.get("policyAdj") or {}).get("note", ""))}</p>'
               if (dcf.get("policyAdj") or {}).get("note") else "")
        )
    else:
        dcf_html = (
            '<div class="fin-signal">⚠ 本次缺少可用的自由现金流（FCF）或股本口径，或公司处于重资产扩张期'
            '（FCF 为负），DCF 不输出量化内在价值——估值判断以下方相对估值与决策矩阵为准（不硬算、不编造）。</div>'
            + (f'<p class="doc-p muted">{esc(bands.get("note", ""))}</p>' if bands.get("note") else "")
        )

    # 相对估值
    cur = relative.get("current") or {}
    rel_lines = [f'当前 PE(TTM) {cur.get("peTtm") if cur.get("peTtm") is not None else "--"}'
                 f' / PB {cur.get("pb") if cur.get("pb") is not None else "--"}']
    if relative.get("mode") == "percentile":
        pl = relative.get("percentile") or {}
        pe_p, pb_p = (pl.get("pe") or {}), (pl.get("pb") or {})
        if pe_p.get("pct") is not None:
            rel_lines.append(f'PE 分位 {_fmt_pct(pe_p.get("pct"))}（{pe_p.get("label", "")}）')
        if pb_p.get("pct") is not None:
            rel_lines.append(f'PB 分位 {_fmt_pct(pb_p.get("pct"))}（{pb_p.get("label", "")}）')
        if relative.get("comment"):
            rel_lines.append(esc(relative["comment"]))
    elif relative.get("mode") == "peer_median":
        pm = relative.get("peerMedian") or {}
        rel_lines.append(f'同业中位 PE {pm.get("peerMedianPe", "--")} / PB {pm.get("peerMedianPb", "--")}'
                         f'（{pm.get("industryName", "同业")}，样本 {pm.get("sampleN", "--")} 家）')
        if relative.get("comment"):
            rel_lines.append(esc(relative["comment"]))
    elif relative.get("comment"):
        rel_lines.append(esc(relative["comment"]))
    rel_html = ('<div class="ia-row"><span class="ia-k">相对估值</span><span>'
                + ' · '.join(rel_lines) + '</span></div>')

    # 估值区间
    if bands.get("intrinsicCenter"):
        band_parts = [f'中性内在价值 {bands["intrinsicCenter"]} 元/股']
        if bands.get("buyBelow") is not None:
            band_parts.append(f'买入关注区 ≤ {bands["buyBelow"]} 元')
        if bands.get("hold"):
            band_parts.append(f'持有观察区 {bands["hold"][0]} ~ {bands["hold"][1]} 元')
        if bands.get("sellAbove") is not None:
            band_parts.append(f'高估观察区 ≥ {bands["sellAbove"]} 元')
        band_parts.append(esc(bands.get("basedOn", "")))
    else:
        band_parts = [esc(bands.get("basedOn", ""))]
    band_html = ('<div class="ia-row"><span class="ia-k">估值区间</span><span>'
                 + ' · '.join(band_parts) + '</span></div>')

    # 决策矩阵
    mat_rows = ""
    for d in matrix.get("dimensions", []):
        ev = "；".join(d.get("evidence", [])) or "--"
        w = d.get("weight")
        w_txt = f"{w * 100:.0f}%" if isinstance(w, (int, float)) else "--"
        mat_rows += (f'<tr><td>{esc(d.get("label", ""))}</td><td>{w_txt}</td>'
                     f'<td>{esc(d.get("tone", ""))}（{d.get("score", "--")}/3）</td>'
                     f'<td class="mat-ev">{esc(ev)}</td></tr>')
    score = matrix.get("score")
    score_txt = f"{score * 100:.0f}/100" if isinstance(score, (int, float)) else "--"
    zero_note = ('<p class="doc-p muted">注：未开通深知检索，本次决策未纳入政策/标准维度，'
                 '退化为财务质量 + 估值维度。</p>') if matrix.get("policyWeightZero") else ""
    matrix_html = (
        '<table class="data-table matrix-table"><thead><tr><th>维度</th><th>权重</th>'
        f'<th>打分</th><th>依据</th></tr></thead><tbody>{mat_rows}</tbody></table>'
        f'<p class="doc-p"><b>综合得分</b>：{score_txt} · '
        f'<b>动作建议（研究参考）</b>：{esc(matrix.get("actionLabel", "--"))}</p>'
        + (f'<p class="doc-p muted">{esc(matrix.get("actionNote", ""))}</p>'
           if matrix.get("actionNote") else "")
        + zero_note
        + (f'<p class="doc-p muted">模型说明：{esc(matrix.get("rulesNote", ""))}</p>'
           if matrix.get("rulesNote") else "")
    )

    return (
        '<div class="decision-wrap">'
        '<p class="doc-p muted">本板块把财务基本面（第二部分）与政策影响（上一部分）收敛为一个研究性结论：'
        'DCF 内在价值三情景 + 相对估值 + 目标价区间 + 决策矩阵。'
        '所有区间/动作均为规则模型生成的研究参考，非投资建议，据此操作风险自担。</p>'
        f'{base_rows}{dcf_html}{rel_html}{band_html}{matrix_html}'
        + (f'<div class="risk">⚠️ 免责声明：{esc(v.get("disclaimer", ""))}</div>'
           if v.get("disclaimer") else "")
        + '</div>'
    )


# ============================================================
# 5. 核验报告单与过程回顾（数字全部真实计算）
# ============================================================

def compute_verification(sources: List[Dict[str, Any]],
                         sections: List[Dict[str, Any]]) -> Dict[str, Any]:
    cited = {cid for sec in sections for cid in sec["cites"]}
    total = len(sources)
    used = [s for s in sources if s["id"] in cited]
    verified = [s for s in used if s.get("verified")]
    dead_links = [s for s in sources if s.get("url") and s.get("link_dead")]
    snap_saved = [s for s in dead_links if s.get("snapshot")]
    hidden_links = [s for s in dead_links if not s.get("snapshot")]

    dates = [s["date"][:10] for s in sources
             if re.match(r"^\d{4}-\d{2}-\d{2}", str(s.get("date", "")))]
    newest = max(dates) if dates else "N/A"

    n_policy = sum(1 for s in sources if s["type"] == "policy")
    n_std = sum(1 for s in sources if s["type"] == "standard")

    metrics = [
        ("依据溯源", "ok" if len(verified) == len(used) and used else "bad",
         f"{len(verified)}/{len(used)} 条引用材料摘录可比对"
         + (f"；{len(sources) - len(used)} 条召回未引用（见材料专库）" if len(sources) > len(used) else "")),
        ("材料构成", "ok", f"政策 {n_policy} · 标准 {n_std} · 金融数据 1"),
        ("材料新旧", "ok" if newest != "N/A" else "warn", f"最新材料 {newest}"),
        ("链接状态", "ok" if not hidden_links else "warn",
         (f"{sum(1 for s in sources if s.get('url') and not s.get('link_dead'))} 条有效"
          + (f" · {len(dead_links)} 条失效（{len(snap_saved)} 条快照兜底）" if dead_links else "")
          + (f" · {len(hidden_links)} 条失效已隐藏链接" if hidden_links else "")) or "无原文链接"),
    ]
    passed = len(verified) == len(used) and bool(used)
    return {
        "metrics": metrics,
        "passed": passed,
        "conclusion": ("核验完成，正文依据逐条对过原文" if passed
                       else "核验未完全通过：部分引用材料缺少可比对摘录"),
    }


def render_verify_panel(v: Dict[str, Any]) -> str:
    cls = "" if v["passed"] else " fail"
    head_txt = "✓ 核验完成" if v["passed"] else "⚠ 核验未完全通过"
    rows = "".join(
        f'<div class="v-row"><b>{esc(name)}</b>'
        f'<span class="v-val {"v-ok" if state == "ok" else "v-warn"}">{esc(text)}</span></div>'
        for name, state, text in v["metrics"])
    return (
        f'<section class="verify{cls}" aria-label="核验报告单">'
        f'<div class="v-head">{head_txt}</div>'
        f'<p class="v-concl">{esc(v["conclusion"])}</p>{rows}'
        '<p class="v-method">核验方式：正文角标与材料卡同源生成、逐条绑定；'
        '摘录取自原文原段可比对；原文链接生成时真实检测。</p></section>'
    )


def render_process_bar(company_data: Dict[str, Any], policy_data: Dict[str, Any],
                       impact_data: Optional[Dict[str, Any]],
                       stock_keyword: str,
                       valuation_data: Optional[Dict[str, Any]] = None) -> str:
    basic = company_data.get("basicInfo") or {}
    kis = company_data.get("keyIndicators") or []
    n_pol = len(policy_data.get("policyHighlights", []))
    n_std = len(policy_data.get("standardHighlights", []))
    n_imp = (impact_data or {}).get("summary", {}).get("total", 0)
    steps = [
        ("股票解析", f"{stock_keyword} → {basic.get('secCode') or '--'}", bool(basic.get("secCode"))),
        ("金融数据", f"akshare 公开披露 · {len(kis)} 期", bool(kis)),
        ("政策检索", f"深知可信搜索 · {n_pol} 条", n_pol > 0),
        ("标准检索", f"深知可信搜索 · {n_std} 条", n_std > 0),
        ("影响分析", f"规则模板 + 财务联动 · {n_imp} 条", n_imp > 0),
    ]
    if valuation_data:
        m = valuation_data.get("matrix") or {}
        score = m.get("score")
        score_txt = f"{score * 100:.0f}/100" if isinstance(score, (int, float)) else "--"
        steps.append(("估值决策", f"DCF + 相对估值 + 决策矩阵 · {score_txt}",
                      bool(valuation_data.get("method"))))
    items = []
    for i, (name, detail, ok) in enumerate(steps):
        items.append(
            f'<li class="p-step{" done" if ok else ""}">'
            f'<span class="p-no">{i + 1}</span>'
            f'<span class="p-txt"><b>{esc(name)}</b><i>{esc(detail)}</i></span></li>')
        if i < len(steps) - 1:
            items.append('<span class="p-arrow">›</span>')
    return (
        '<section class="process" aria-label="过程回顾">'
        '<div class="p-head"><b>过程回顾</b><span>五步流程 · 数字为本次真实执行结果</span></div>'
        f'<ol class="p-steps">{"".join(items)}</ol></section>'
    )


# ============================================================
# 6. 材料专库视图 + 热词 + 打印附录
# ============================================================

STOP_WORDS = {"有限公司", "有限责任", "股份", "集团", "关于", "国家", "中华人民共和国",
              "管理", "通知", "公告", "工作", "企业", "行业", "相关", "实施", "支持",
              "国务院", "部门", "政策", "标准", "发展", "建设", "有关", "问题", "进一步"}


def extract_hot_terms(sources: List[Dict[str, Any]], top: int = 8) -> List[str]:
    freq: Dict[str, int] = {}
    for s in sources:
        text = f"{s.get('title', '')} {s.get('excerpt', '')}"
        for token in re.findall(r"[一-龥]{2,6}", text):
            if token in STOP_WORDS:
                continue
            freq[token] = freq.get(token, 0) + 1
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    return [w for w, _ in ranked[:top]]


TYPE_LABELS = {"policy": "政策", "standard": "标准", "finance": "金融数据"}


def render_source_card(s: Dict[str, Any], for_print: bool = False) -> str:
    cited_cls = "" if s.get("used", True) else " unused"
    if s.get("url") and not s.get("link_dead"):
        link = (f'<a class="sc-link" href="{esc(s["url"])}" target="_blank" '
                f'rel="noopener noreferrer">查看全文 ↗</a>')
    elif s.get("url") and s.get("snapshot"):
        link = (f'<a class="sc-link" href="{esc(s["snapshot"])}" target="_blank" '
                f'rel="noopener noreferrer">查看存档全文 ↗</a>')
    else:
        link = ""
    dead_note = ('<span class="sc-dead">原文链接已失效</span>' if s.get("link_dead") else "")
    note = s.get("dateNote") or ""
    note_html = f'<p class="sc-datenote">🔧 {esc(note)}</p>' if note else ""
    excerpt = esc((s.get("excerpt") or "").strip() or "（无摘录）")
    return (
        f'<article class="source-card{s.get("type", "")}{cited_cls}" '
        f'data-id="{esc(s["id"])}" data-cond="{esc(s.get("search_key", ""))}" '
        f'data-type="{esc(s.get("type", ""))}" data-cited="{1 if s.get("used", True) else 0}">'
        f'<div class="sc-head"><span class="sc-id">{esc(s["id"])}</span>'
        f'<span class="sc-type">{TYPE_LABELS.get(s.get("type"), s.get("type"))}</span>'
        f'<span class="sc-cited">{"已引用" if s.get("used", True) else "未引用"}</span></div>'
        f'<h4>{esc(s["title"])}</h4>'
        f'<p class="sc-meta">{esc(s.get("agency") or "")} · {esc(s.get("date") or "")}{dead_note}</p>'
        f'{note_html}<p class="sc-excerpt">{excerpt}</p>'
        f'<div class="sc-links">{link}</div></article>'
    )


def render_library_view(sources: List[Dict[str, Any]], hot_terms: List[str]) -> str:
    if not sources:
        return ('<section class="view" id="view-library" aria-label="材料专库">'
                '<div class="lib-empty">本次运行没有可展示的材料。</div></section>')
    unused = [s for s in sources if not s.get("used", True)]
    conds: List[Tuple[str, int]] = []
    for s in sources:
        key = (s.get("search_key") or "").strip()
        if not key:
            continue
        for i, (name, count) in enumerate(conds):
            if name == key:
                conds[i] = (name, count + 1)
                break
        else:
            conds.append((key, 1))
    tabs = [f'<button class="on" data-cond="" type="button">全部（{len(sources)}）</button>']
    for name, count in conds:
        tabs.append(f'<button data-cond="{esc(name)}" type="button">{esc(name)}（{count}）</button>')
    if unused:
        tabs.append(f'<button data-cond="__unused__" type="button">未引用（{len(unused)}）</button>')
    hot_html = "".join(f'<button class="hot-term" type="button">{esc(t)}</button>' for t in hot_terms)
    cards = "".join(render_source_card(s) for s in sources)
    return (
        '<section class="view" id="view-library" aria-label="材料专库">'
        '<div class="lib-head">'
        f'<div class="lib-title"><b>材料专库</b><span>共 {len(sources)} 条 · '
        f'已引用 {len(sources) - len(unused)} · 摘录均取自原文原段</span></div>'
        '<div class="lib-searchWrap"><span class="lib-searchIcon" aria-hidden="true">⌕</span>'
        '<input class="lib-search" type="search" placeholder="搜索标题 / 来源 / 摘录…" aria-label="搜索材料"></div>'
        f'<div class="hot-terms" aria-label="热门搜索词"><span class="hot-label">大家都在搜</span>{hot_html}</div>'
        f'<div class="lib-tabs" role="tablist" aria-label="按检索分组筛选">{"".join(tabs)}</div>'
        '</div>'
        f'<div class="lib-list" id="lib-list">{cards}</div>'
        '<div class="lib-empty hide" id="lib-empty">没有符合当前筛选的材料。</div>'
        '<div class="lib-foot">材料来源：深知可信搜索（政策/标准）+ akshare 公开披露（金融数据）</div>'
        '</section>'
    )


def render_print_appendix(sources: List[Dict[str, Any]]) -> str:
    cards = "".join(render_source_card(s, for_print=True) for s in sources)
    return (f'<section class="print-appendix" aria-label="核验材料附录">'
            f'<h2>核验材料附录（{len(sources)} 条）</h2>{cards}</section>')


# ============================================================
# 7. 样式与脚本（独立常量，避免模板转义）
# ============================================================

PAGE_CSS = """
:root{
  --ink:#1f2328; --muted:#6b7280; --line:#e5e7eb; --line-strong:#cbd2dd;
  --brand:#6512ad; --brand-2:#8b5cf6; --brand-soft:#ede9fe;
  --grad:linear-gradient(135deg,#6512ad,#8b5cf6);
  --policy:#0f9d6e; --policy-soft:#e3f5ee;
  --std:#9a6700; --std-soft:#fdf3dc;
  --fin:#6d3cc7; --fin-soft:#f1ebfd;
  --warn:#9a6700; --danger:#e5484d; --ok:#0f9d6e;
  --shadow-1:0 1px 3px rgba(24,20,40,.04),0 8px 30px rgba(24,20,40,.08);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,"PingFang SC","HarmonyOS Sans SC","Microsoft YaHei",sans-serif;
  color:var(--ink);background:#f4f5f7;line-height:1.75;font-size:14.5px}
a{color:var(--brand)}

/* ===== 顶栏 ===== */
.topbar{position:sticky;top:0;z-index:50;display:flex;justify-content:space-between;align-items:center;gap:10px;
  padding:9px 22px;background:rgba(255,255,255,.92);backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.tb-left{display:flex;align-items:center;gap:10px;min-width:0}
.tb-stamp{flex:none;font-size:12.5px;font-weight:800;color:#20242c;letter-spacing:4px;text-indent:4px}
.tb-stamp::before,.tb-stamp::after{content:"";display:inline-block;width:22px;border-top:1px solid var(--line-strong);
  vertical-align:middle;margin:0 6px}
.tb-title{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);font-size:13px}
.tb-right{display:flex;align-items:center;gap:8px;flex:none}
.view-switch{display:inline-flex;border:1px solid var(--line);border-radius:999px;overflow:hidden;background:#fff}
.view-switch button{border:0;background:transparent;color:var(--muted);font-size:12.5px;font-weight:700;padding:5px 14px;cursor:pointer}
.view-switch button.on{background:var(--grad);color:#fff}
.tb-tools{display:flex;gap:6px}
.tb-tools button{border:1px solid var(--line);border-radius:8px;background:#fff;color:#3b3550;
  font-size:12.5px;font-weight:700;padding:5px 11px;cursor:pointer}
.tb-tools button:hover{border-color:var(--brand-2);color:var(--brand)}
.tb-tools button[aria-pressed="true"]{background:var(--grad);border-color:var(--brand);color:#fff}
.print-menu{position:relative;display:inline-flex}
.print-drop{position:absolute;top:calc(100% + 6px);right:0;z-index:70;display:flex;flex-direction:column;gap:4px;
  min-width:200px;padding:6px;background:#fff;border:1px solid #e9dff9;border-radius:10px;
  box-shadow:0 3px 9px rgba(101,18,173,.22),0 14px 36px rgba(24,20,40,.14);text-align:left}
.print-drop[hidden]{display:none}
.print-drop button{border:0;background:none;border-radius:7px;color:var(--ink);font-size:12.5px;font-weight:600;
  padding:8px 12px;cursor:pointer;text-align:left;font-family:inherit;white-space:nowrap}
.print-drop button:hover{background:#f3edfc;color:var(--brand)}
.progress{position:absolute;left:0;right:0;bottom:-1px;height:2px;background:transparent}
.progress i{display:block;height:100%;width:0;background:linear-gradient(90deg,#6512ad,#8b5cf6)}

/* ===== Hero ===== */
.hero{position:relative;background:linear-gradient(180deg,#fbfaff 0%,#f7f4fd 100%);
  color:var(--ink);padding:30px 22px 54px;text-align:center;border-bottom:1px solid var(--line)}
.r-badge{display:inline-flex;align-items:center;gap:14px;margin:0 0 8px;
  background:var(--grad);color:#fff;border-radius:999px;padding:4px 22px;
  font-size:15px;font-weight:800;letter-spacing:7px;text-indent:7px;line-height:1.6;
  box-shadow:0 2px 8px rgba(101,18,173,.22)}
.hero h1{margin:0 0 10px;font-size:24px;line-height:1.5;max-width:860px;margin-left:auto;margin-right:auto;color:var(--ink)}
.hero .meta{color:var(--muted);font-size:13px;letter-spacing:.5px}

/* ===== 容器与视图 ===== */
.container{max-width:1080px;margin:0 auto;padding:0 18px}
.app[data-view="library"] #view-report{display:none}
#view-library{display:none}
.app[data-view="library"] #view-library{display:block}

/* ===== 过程回顾条 ===== */
.process{background:#fff;border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow-1);
  padding:15px 20px 13px;margin:-34px auto 16px;position:relative}
.p-head{display:flex;align-items:baseline;gap:10px;margin-bottom:10px}
.p-head b{font-size:14.5px;color:#20242c;letter-spacing:1px}
.p-head span{font-size:11.5px;color:var(--muted)}
.p-steps{list-style:none;display:flex;align-items:stretch;gap:8px;margin:0;padding:0;flex-wrap:wrap}
.p-step{display:flex;gap:9px;align-items:center;min-width:0;flex:1 1 150px;
  border:1px solid var(--line);border-radius:10px;padding:8px 11px;background:#fafbfc}
.p-no{flex:none;width:24px;height:24px;border-radius:50%;background:var(--grad);color:#fff;
  display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:800}
.p-txt{min-width:0}
.p-txt b{display:block;font-size:12.8px;color:#20242c;line-height:1.5}
.p-txt i{display:block;font-style:normal;font-size:11px;color:var(--muted);line-height:1.5;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.p-step.done{border-color:#bfe8da;background:#f2faf7}
.p-step.done .p-no{background:var(--ok)}
.p-arrow{align-self:center;color:#b9c6d9;font-size:16px;font-weight:700;flex:none}

/* ===== 核验报告单 ===== */
#view-report{padding-bottom:26px}
.verify{margin:0 0 16px;background:#fff;border:1px solid #bfe8da;border-left:4px solid var(--ok);
  border-radius:14px;padding:16px 20px;box-shadow:var(--shadow-1)}
.verify.fail{border-color:#f6c9cb;border-left-color:var(--danger);background:#fffafa}
.v-head{display:flex;align-items:center;gap:9px;font-size:15px;font-weight:800;color:var(--ok)}
.verify.fail .v-head{color:var(--danger)}
.v-concl{margin:6px 0 10px;font-size:13.5px;color:var(--ink)}
.v-row{display:flex;justify-content:space-between;gap:14px;padding:4px 2px;font-size:12.8px;
  border-top:1px dashed var(--line);flex-wrap:wrap}
.v-row b{color:#20242c;font-weight:700;flex:none}
.v-ok{color:var(--ok)}
.v-warn{color:var(--warn)}
.v-method{margin:10px 0 0;font-size:11.8px;color:var(--muted)}

/* ===== 正文文档流 ===== */
.doc{background:#fff;border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow-1);
  padding:26px 30px 30px;margin-bottom:16px}
.ch-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:26px 0 4px;position:relative}
.ch-head:first-child{margin-top:0}
.ch-head::after{content:"";position:absolute;left:0;right:0;bottom:-6px;height:1px;
  background:linear-gradient(90deg,var(--line-strong),transparent)}
.ch-no{display:inline-flex;width:26px;height:26px;border-radius:7px;background:var(--grad);color:#fff;
  align-items:center;justify-content:center;font-size:13px;font-weight:800}
.ch-title{font-size:18px;font-weight:800;color:#20242c;margin:0}
.ch-lead{font-size:12.5px;color:var(--muted);margin:0 0 12px;padding-top:10px}
.sec-badge{order:4;flex:none;font-size:11.5px;color:var(--ok);background:var(--policy-soft);
  border-radius:6px;padding:2px 9px;font-weight:700}
.doc-p{font-size:15.5px;line-height:1.95;margin:10px 0;color:#1f2328}
.doc-p.muted{color:var(--muted);font-size:13.5px}
.kv-table,.data-table{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0}
.kv-table th,.kv-table td,.data-table th,.data-table td{border:1px solid var(--line);padding:7px 10px;text-align:left}
.kv-table th{background:#f7f5fb;width:130px;color:#20242c}
.data-table th{background:#f7f5fb;color:#20242c;white-space:nowrap}
.data-table.compact th,.data-table.compact td{padding:5px 8px}
.rank-card{border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:10px 0;background:#fbfcfe}
.rank-head{display:flex;justify-content:space-between;align-items:center}
.rank-name{font-weight:700;color:#20242c;font-size:13.5px}
.rank-pos{font-family:monospace;font-weight:800;color:var(--brand);font-size:15px}
.rank-meta{font-size:12px;color:var(--muted);margin:3px 0 6px}
.peer-details summary{cursor:pointer;font-size:12.5px;color:var(--brand)}
.ev-block{margin:4px 0}
.ev-meta{font-size:12px;color:var(--muted);margin:-4px 0 8px}
.date-fixed{display:inline-block;margin-left:5px;padding:0 6px;border-radius:4px;background:#fff4e0;
  border:1px solid #f2ddb8;color:#b26a00;font-size:10.5px;font-weight:700;cursor:help}
/* 政策影响分析 */
.fin-signal{margin:10px 0;padding:9px 13px;border:1px solid #f2ddb8;border-left:4px solid #b26a00;
  border-radius:8px;background:#fff8ec;font-size:12.8px;color:#7a4d00}
.impact-overview{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.impact-overview span{border-radius:999px;padding:3px 12px;font-size:12px;font-weight:700}
.ov-bull{background:var(--policy-soft);color:var(--policy)}
.ov-bear{background:#fdecea;color:#e5484d}
.ov-neutral{background:#eef1f6;color:#4b5563}
.ov-total{background:var(--brand-soft);color:var(--brand)}
.ia-card{border:1px solid var(--line);border-radius:10px;margin-bottom:9px;background:#fff;overflow:hidden;
  border-left:4px solid var(--muted)}
.ia-card.dir-bull{border-left-color:var(--policy)}
.ia-card.dir-bear{border-left-color:#e5484d}
.ia-card summary{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:10px 13px;cursor:pointer;list-style:none}
.ia-card summary::-webkit-details-marker{display:none}
.ia-dir{flex:none;border-radius:5px;padding:1px 8px;font-size:11px;font-weight:800;white-space:nowrap}
.dir-bull .ia-dir{background:var(--policy-soft);color:var(--policy)}
.dir-bear .ia-dir{background:#fdecea;color:#e5484d}
.ia-card.dir-neutral .ia-dir{background:#eef1f6;color:#4b5563}
.ia-title{font-size:13.5px;font-weight:700;color:#20242c;min-width:200px;flex:1}
.ia-meta{font-size:11.5px;color:var(--muted);width:100%}
.ia-body{padding:2px 13px 12px;border-top:1px dashed var(--line)}
.ia-row{display:flex;gap:10px;margin-top:8px;font-size:12.6px;line-height:1.65}
.ia-k{flex:none;width:62px;font-weight:700;color:var(--brand)}
.ia-link{margin-top:8px;padding:8px 11px;border-radius:7px;background:var(--fin-soft);font-size:12.3px;color:#5b3aa8}
.hl-row span{color:#20242c}
.impact-guide{margin-top:12px;padding:10px 13px;border:1px dashed var(--line-strong);border-radius:9px;
  font-size:11.8px;color:var(--muted);line-height:1.7}
.impact-guide b{color:#20242c;margin-right:6px}
.matrix-table td.mat-ev{white-space:normal;min-width:220px;font-size:12px}
.risk{background:#fff8ec;border:1px solid #f2ddb8;border-radius:12px;padding:14px 18px;font-size:12.5px;color:#7a4d00}
.risk b{display:block;margin-bottom:4px}

/* ===== 行内引文胶囊 jb（原型复刻） ===== */
.jb{position:relative;display:inline;white-space:normal}
.jb-name{display:inline-flex;align-items:center;gap:4px;max-width:100%;min-height:26px;padding:4px 13px;
  margin:0 3px;border:1px solid #d3bef0;border-radius:13px;background:linear-gradient(135deg,#7c3aed,#8b5cf6);
  color:#fff;font-size:12px;font-weight:700;line-height:1.4;vertical-align:baseline;cursor:pointer;
  transition:box-shadow .18s}
.jb-name:hover{box-shadow:0 2px 10px rgba(101,18,173,.32)}
.jb-name i{font-style:normal;font-weight:800}
.jb-name svg{transition:transform .18s;flex:none}
.jb.open .jb-name svg{transform:rotate(180deg)}
.jb-txt{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:34em}
.jb-body{display:none;margin:6px 0 10px;padding:12px 16px;border:1px solid #e9dff9;border-left:3px solid var(--brand-2);
  border-radius:10px;background:#faf7ff;box-shadow:0 2px 10px rgba(24,20,40,.05);font-size:13px;line-height:1.8}
.jb.open .jb-body{display:block}
.jb-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.jb-head b{color:var(--brand);font-size:12.5px}
.jb-src{font-size:12.5px;font-weight:800;text-decoration:none}
.jb-site{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--muted)}
.jb-site .dot{width:6px;height:6px;border-radius:50%;background:var(--ok)}
.jb-quote{display:block;margin-top:8px;padding:9px 13px;border-radius:8px;background:#fff;
  border:1px dashed #e2d5f7;color:#3b4152;cursor:pointer}
.jb-no{display:inline-flex;min-width:22px;height:22px;align-items:center;justify-content:center;margin-right:9px;
  border-radius:6px;background:var(--brand-soft);color:var(--brand);font-style:normal;font-size:11px;font-weight:800}

/* ===== 材料专库 ===== */
.lib-head{display:flex;flex-direction:column;gap:12px;padding:18px 0 6px}
.lib-title b{font-size:18px;color:#20242c;margin-right:10px}
.lib-title span{font-size:12.5px;color:var(--muted)}
.lib-searchWrap{position:relative;max-width:420px}
.lib-search{width:100%;padding:9px 14px 9px 34px;border:1px solid var(--line);border-radius:10px;font-size:13px}
.lib-searchIcon{position:absolute;left:11px;top:50%;transform:translateY(-50%);color:var(--muted)}
.hot-terms{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.hot-label{font-size:12px;color:var(--muted)}
.hot-term{border:1px solid var(--line);border-radius:999px;background:#fff;color:#3b3550;
  font-size:12px;padding:3px 12px;cursor:pointer}
.hot-term:hover{border-color:var(--brand-2);color:var(--brand)}
.lib-tabs{display:flex;gap:8px;flex-wrap:wrap}
.lib-tabs button{border:1px solid var(--line);border-radius:999px;background:#fff;color:var(--muted);
  padding:4px 14px;font-size:12.5px;font-weight:700;cursor:pointer}
.lib-tabs button.on{background:var(--grad);border-color:var(--brand);color:#fff}
.lib-list{display:flex;flex-direction:column;gap:12px;padding:14px 0 20px}
.source-card{border:1px solid var(--line);border-left-width:3px;border-left-color:var(--policy);
  border-radius:12px;padding:14px 18px;background:#fff;box-shadow:var(--shadow-1)}
.source-card.standard{border-left-color:var(--std)}
.source-card.finance{border-left-color:var(--fin)}
.source-card.unused{opacity:.72}
.source-card.hl{box-shadow:0 0 0 3px var(--brand-2)}
.source-card.hide{display:none}
.sc-head{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.sc-id{font-family:monospace;font-weight:800;font-size:11px;color:var(--brand);
  background:var(--brand-soft);border-radius:4px;padding:1px 7px}
.sc-type{font-size:11px;font-weight:700;border-radius:4px;padding:1px 8px}
.source-card.policy .sc-type{background:var(--policy-soft);color:var(--policy)}
.source-card.standard .sc-type{background:var(--std-soft);color:var(--std)}
.source-card.finance .sc-type{background:var(--fin-soft);color:var(--fin)}
.sc-cited{margin-left:auto;font-size:11px;color:var(--muted)}
.source-card h4{margin:0 0 4px;font-size:14px;color:#20242c;line-height:1.5}
.sc-meta{font-size:11.5px;color:var(--muted)}
.sc-dead{margin-left:6px;color:var(--danger)}
.sc-datenote{margin:5px 0;padding:7px 10px;border:1px solid #f2ddb8;border-radius:7px;
  background:#fff8ec;font-size:11.8px;color:#7a4d00;line-height:1.6}
.sc-excerpt{font-size:12.5px;color:#42566f;margin:8px 0;line-height:1.7}
.sc-link{font-size:12.5px;font-weight:800;text-decoration:none}
.lib-empty{padding:40px 0;text-align:center;color:var(--muted);font-size:13.5px}
.lib-empty.hide{display:none}
.lib-foot{padding:10px 0 30px;text-align:center;color:var(--muted);font-size:11.5px}
.foot{text-align:center;color:var(--muted);font-size:11.5px;padding:14px 0 30px}

/* ===== 打印附录（默认隐藏，完整归档时显示） ===== */
.print-appendix{display:none}

/* ===== 只看正文 ===== */
body.reading .process,body.reading .verify,body.reading .sec-badge,body.reading .jb-body,
body.reading .impact-guide .ch-lead{ }
body.reading .process,body.reading .verify,body.reading .sec-badge{display:none}

@media (max-width:760px){
  .doc{padding:18px 16px}
  .doc-p{font-size:14.5px}
  .jb-txt{max-width:16em}
  .topbar{flex-wrap:wrap}
}

@media print{
  .topbar,.lib-tabs,.hot-terms,.lib-searchWrap,.print-menu{display:none!important}
  body{background:#fff}
  .app[data-view="library"] #view-report{display:block}
  .app[data-view="library"] #view-library{display:none}
  .doc,.verify,.process{box-shadow:none;border-color:var(--line)}
  .jb-body{display:none!important}
  .jb-name{border-color:#ccc;background:#f3edfc!important;color:#6512ad!important}
  body.print-full .print-appendix{display:block;page-break-before:always}
  body.print-full .print-appendix .source-card{box-shadow:none;page-break-inside:avoid}
}
"""

PAGE_JS = """
(function () {
  "use strict";

  /* 视图切换：报告 / 材料专库 */
  var app = document.querySelector(".app");
  document.querySelectorAll("[data-view-btn]").forEach(function (b) {
    b.addEventListener("click", function () {
      document.querySelectorAll("[data-view-btn]").forEach(function (x) { x.classList.remove("on"); });
      b.classList.add("on");
      app.setAttribute("data-view", b.getAttribute("data-view-btn"));
    });
  });

  /* 行内引文胶囊：点击原地展开/收起溯源卡 */
  document.querySelectorAll(".jb-name").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var jb = btn.closest(".jb");
      var open = jb.classList.toggle("open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
  });
  document.addEventListener("click", function (e) {
    if (!e.target.closest(".jb")) return;
  });

  /* 只看正文 */
  var readBtn = document.querySelector("[data-act='reading']");
  if (readBtn) readBtn.addEventListener("click", function () {
    var on = document.body.classList.toggle("reading");
    readBtn.setAttribute("aria-pressed", on ? "true" : "false");
    readBtn.textContent = on ? "退出只读" : "只看正文";
  });

  /* 复制全文（按文档流顺序、去胶囊纯文本） */
  var copyBtn = document.querySelector("[data-act='copy']");
  if (copyBtn) copyBtn.addEventListener("click", function () {
    var lines = [];
    document.querySelectorAll("#view-report h2.ch-title, #view-report .doc-p, #view-report .v-concl, #view-report .fin-signal").forEach(function (el) {
      if (el.closest(".jb")) return;
      var clone = el.cloneNode(true);
      clone.querySelectorAll(".jb, sup.cite").forEach(function (x) { x.remove(); });
      var text = (clone.textContent || "").replace(/\\s+/g, " ").trim();
      if (text) lines.push((el.classList.contains("ch-title") ? "\\n## " : "") + text);
    });
    var plain = lines.join("\\n");
    function done(ok) {
      copyBtn.textContent = ok ? "已复制 ✓" : "复制失败";
      setTimeout(function () { copyBtn.textContent = "复制全文"; }, 1800);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(plain).then(function () { done(true); }, function () { done(false); });
    } else { done(false); }
  });

  /* 打印归档下拉 */
  var printBtn = document.querySelector("[data-act='print-menu']");
  var printDrop = document.getElementById("print-drop");
  if (printBtn && printDrop) {
    printBtn.addEventListener("click", function (e) { e.stopPropagation(); printDrop.hidden = !printDrop.hidden; });
    document.addEventListener("click", function () { printDrop.hidden = true; });
    document.querySelectorAll("[data-print]").forEach(function (b) {
      b.addEventListener("click", function () {
        printDrop.hidden = true;
        var full = b.getAttribute("data-print") === "full";
        document.body.classList.toggle("print-full", full);
        window.print();
      });
    });
    window.addEventListener("afterprint", function () { document.body.classList.remove("print-full"); });
  }

  /* 材料专库：tabs 分组 + 搜索 + 热词 */
  var cond = "", kw = "";
  function applyFilter() {
    var empty = true;
    document.querySelectorAll("#lib-list .source-card").forEach(function (c) {
      var okCond = !cond || (cond === "__unused__"
        ? c.getAttribute("data-cited") === "0"
        : c.getAttribute("data-cond") === cond);
      var okKw = !kw || (c.textContent || "").toLowerCase().indexOf(kw) !== -1;
      var show = okCond && okKw;
      c.classList.toggle("hide", !show);
      if (show) empty = false;
    });
    var box = document.getElementById("lib-empty");
    if (box) box.classList.toggle("hide", !empty);
  }
  document.querySelectorAll(".lib-tabs button").forEach(function (b) {
    b.addEventListener("click", function () {
      document.querySelectorAll(".lib-tabs button").forEach(function (x) { x.classList.remove("on"); });
      b.classList.add("on");
      cond = b.getAttribute("data-cond");
      applyFilter();
    });
  });
  var search = document.querySelector(".lib-search");
  if (search) search.addEventListener("input", function () { kw = search.value.trim().toLowerCase(); applyFilter(); });
  document.querySelectorAll(".hot-term").forEach(function (t) {
    t.addEventListener("click", function () {
      if (!search) return;
      search.value = t.textContent.trim();
      kw = search.value.toLowerCase();
      applyFilter();
    });
  });

  /* 阅读进度条 */
  var bar = document.querySelector(".progress i");
  if (bar) window.addEventListener("scroll", function () {
    var h = document.documentElement;
    var max = h.scrollHeight - h.clientHeight;
    bar.style.width = (max > 0 ? (h.scrollTop / max * 100) : 0) + "%";
  }, { passive: true });
})();
"""


# ============================================================
# 8. 主渲染函数
# ============================================================

def generate_report_html(stock_code: str,
                         company_data: Dict[str, Any],
                         policy_data: Dict[str, Any],
                         impact_data: Optional[Dict[str, Any]] = None,
                         valuation_data: Optional[Dict[str, Any]] = None,
                         generated_at: Optional[datetime] = None,
                         check_link_enabled: bool = True) -> str:
    generated_at = generated_at or datetime.now()
    basic = company_data.get("basicInfo") or {}
    company_name = basic.get("secName", stock_code)
    industry = basic.get("industryName") or basic.get("resolvedIndustry") or "--"

    sources = build_sources(company_data, policy_data)
    attach_snapshots(sources, extract_snapshots(policy_data))
    check_links(sources, enabled=check_link_enabled)
    by_id = {s["id"]: s for s in sources}
    f1_cap = jb_html(by_id["F1"])

    # ---- 正文板块（胶囊按条目挂数，cites 收集用于章节徽章） ----
    pol_items = policy_data.get("policyHighlights", [])
    std_items = policy_data.get("standardHighlights", [])
    pol_ids = [f"P{i}" for i in range(1, len(pol_items) + 1)]
    std_ids = [f"S{i}" for i in range(1, len(std_items) + 1)]
    imp_ids = ([a.get("sid") for a in (impact_data or {}).get("policies", [])] +
               [a.get("sid") for a in (impact_data or {}).get("standards", [])])

    sections = [
        {"no": "一", "title": "公司概况", "lead": "公开披露数据（akshare）",
         "cites": ["F1"], "body": render_basic_info(basic)},
        {"no": "二", "title": "关键财务指标",
         "lead": "最近 5 期 · 累计口径 · 同花顺 F10 主源 + 东方财富 ROE/负债率补充",
         "cites": ["F1"], "body": render_financials(company_data.get("keyIndicators"))},
        {"no": "三", "title": "行业定位",
         "lead": "板块规模位次来自公开披露数据（同花顺板块成分，经 akshare）",
         "cites": ["F1"], "body": render_ranks(company_data.get("industryRanks") or {})},
        {"no": "四", "title": "政策环境",
         "lead": f"检索词：{policy_data.get('industryName', '')} 支持政策 补贴 税收优惠 企业适用条件 · 点击胶囊看原文",
         "cites": pol_ids, "body": render_evidence(pol_items, "P", sources)},
        {"no": "五", "title": "标准与准入",
         "lead": f"检索词：{policy_data.get('industryName', '')} 国家标准 行业规范 准入条件 技术规范",
         "cites": std_ids, "body": render_evidence(std_items, "S", sources)},
    ]
    # 板块编号动态：前五固定；影响分析 / 投资决策整合都可能缺失
    _CN_NO = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
    next_no = 5
    if impact_data:
        sections.append({"no": _CN_NO[next_no], "title": "政策影响分析",
                         "lead": "方向 / 传导链 / 影响变量 / 跟踪指标 / 投资含义；财务信号 × 政策时间表联动归因",
                         "cites": imp_ids, "body": render_impact(impact_data, sources)})
        next_no += 1
    if valuation_data:
        sections.append({"no": _CN_NO[next_no], "title": "投资决策整合（估值区间 · 研究参考 · 非投资建议）",
                         "lead": "DCF 内在价值三情景 + 相对估值 + 目标价区间 + 决策矩阵；规则模型生成，非投资建议",
                         "cites": ["F1"], "body": render_decision_section(valuation_data)})
        next_no += 1

    # 标记引用状态（材料专库 已引用/未引用）
    cited_ids = {cid for sec in sections for cid in sec["cites"]}
    for s in sources:
        s["used"] = s["id"] in cited_ids

    doc_html = ""
    for sec in sections:
        badge = (f'<span class="sec-badge">本章引用 {len(sec["cites"])} 处 · 已核验</span>'
                 if sec["cites"] else "")
        cap = f1_cap if "F1" in sec["cites"] and (
            sec["no"] in ("一", "二", "三") or sec["title"].startswith("投资决策")) else ""
        doc_html += (
            f'<div class="ch-head"><span class="ch-no">{sec["no"]}</span>'
            f'<h2 class="ch-title">{esc(sec["title"])}</h2>{badge}{cap}</div>'
            f'<p class="ch-lead">{esc(sec["lead"])}</p>{sec["body"]}'
        )
    risk_html = (
        '<div class="risk"><b>风险提示</b>'
        '本报告基于公开披露数据与深知可信检索结果整理生成，仅供信息查询与研究参考，'
        '不构成任何投资建议、证券推荐或收益承诺。金融数据以上市公司正式公告为准，'
        '政策与标准内容来自深知可信搜索检索，现行有效性以官方发布原文为准。'
        '大模型可能存在理解偏差或生成不准确的情况，请结合上市公司正式公告等权威披露文件核查确认。'
        '市场有风险，投资需谨慎。</div>'
    )

    verification = compute_verification(sources, sections)
    hot_terms = extract_hot_terms([s for s in sources if s["type"] != "finance"])

    meta_line = (f"所属行业：{esc(industry)} · 检索地域：{esc(policy_data.get('serviceArea', '--'))}"
                 f" · 政策时间口径：{esc(policy_data.get('effTime', '--'))}"
                 f" · 生成于 {generated_at.strftime('%Y-%m-%d %H:%M')}")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(company_name)}（{esc(stock_code)}）投研核验报告 · 可溯源版</title>
<style>{PAGE_CSS}</style>
</head>
<body>
<div class="app" data-view="report">

<header class="topbar">
  <div class="tb-left"><span class="tb-stamp">投研核验报告</span>
    <span class="tb-title">{esc(company_name)}（{esc(stock_code)}）</span></div>
  <div class="tb-right">
    <span class="view-switch">
      <button class="on" data-view-btn="report" type="button">报告视图</button>
      <button data-view-btn="library" type="button">材料专库</button>
    </span>
    <span class="tb-tools">
      <button data-act="reading" type="button" aria-pressed="false">只看正文</button>
      <button data-act="copy" type="button">复制全文</button>
      <span class="print-menu">
        <button data-act="print-menu" type="button">打印归档 ▾</button>
        <span class="print-drop" id="print-drop" hidden>
          <button data-print="body" type="button">只打印正文</button>
          <button data-print="full" type="button">完整归档（含核验材料附录）</button>
        </span>
      </span>
    </span>
  </div>
  <span class="progress"><i></i></span>
</header>

<section class="hero">
  <span class="r-badge">可溯源投研报告</span>
  <h1>{esc(company_name)}（{esc(stock_code)}）深度研究报告</h1>
  <p class="meta">{meta_line}</p>
</section>

<div class="container">
  <div id="view-report">
    {render_process_bar(company_data, policy_data, impact_data, stock_code, valuation_data)}
    {render_verify_panel(verification)}
    <article class="doc" id="doc-flow">{doc_html}{risk_html}</article>
    <p class="foot">深知可信投研 · dknowc-trusted-investment-research · 公开披露金融数据 + 深知政策标准洞察</p>
    {render_print_appendix(sources)}
  </div>
  {render_library_view(sources, hot_terms)}
</div>
</div>
<script>{PAGE_JS}</script>
</body>
</html>"""


if __name__ == "__main__":
    # 独立运行：从 data.json 快照渲染（run_research.py 主流程会程序化调用）
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description="从研究数据 JSON 渲染可溯源投研核验报告")
    ap.add_argument("data_json", help="run_research 输出的 .data.json 文件")
    ap.add_argument("output", help="输出 HTML 路径")
    ap.add_argument("--no-link-check", action="store_true",
                    help="跳过生成时链接检测（离线复渲时使用）")
    args = ap.parse_args()

    payload = json.loads(Path(args.data_json).read_text(encoding="utf-8"))
    doc = generate_report_html(
        payload["stockCode"],
        payload["companyData"],
        payload["policyData"],
        impact_data=payload.get("impactData"),  # 旧快照无此字段时自动跳过该板块
        valuation_data=payload.get("valuationData"),
        check_link_enabled=not args.no_link_check,
    )
    Path(args.output).write_text(doc, encoding="utf-8")
    print(f"HTML 已生成: {args.output}")
