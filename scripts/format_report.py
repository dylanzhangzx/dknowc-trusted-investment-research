#!/usr/bin/env python3
"""报告格式化模块

将公开披露公司数据（akshare）和深知政策检索结果组合成投研报告（Markdown 格式）。
"""

from typing import Dict, Any, List, Optional
from datetime import datetime


# 报告板块中文序号（一~八；政策影响/决策板块可能缺失，故序号须运行时算）
_CN_NO = ["", "一", "二", "三", "四", "五", "六", "七", "八"]


def format_number(value: Any, unit: str = "") -> str:
    """格式化数字显示

    Args:
        value: 数值（可能是数字或字符串）
        unit: 单位（如"亿元"、"%"）

    Returns:
        格式化后的字符串
    """
    if value is None or value == "":
        return "N/A"

    try:
        num = float(value)
        if unit == "亿元":
            return f"{num / 1e8:.2f} 亿元"
        elif unit == "%":
            return f"{num:.2f}%"
        else:
            return f"{num:.2f}"
    except (ValueError, TypeError):
        return str(value)


def format_basic_info(basic_info: Optional[Dict[str, Any]]) -> str:
    """格式化公司基本资料

    Args:
        basic_info: akshare_api.get_basic_info 返回的数据

    Returns:
        Markdown 格式的文本
    """
    if not basic_info:
        return "*未获取到公司基本资料*\n"

    lines = []
    # 只渲染有值的字段，公开数据拿不到的字段不占位显示
    row_fields = [
        ("证券简称", basic_info.get('secName')),
        ("证券代码", basic_info.get('secCode')),
        ("公司全称", basic_info.get('orgName')),
        ("上市日期", basic_info.get('listedDate')),
        ("所属行业", basic_info.get('industryName')),
        ("所属地区", basic_info.get('provinceName')),
        ("主营业务", basic_info.get('mainOprBus')),
    ]
    for label, val in row_fields:
        if val not in (None, ""):
            lines.append(f"- **{label}**: {val}")

    # 联系方式
    if any(basic_info.get(k) for k in ['telephone', 'email', 'orgWebsite']):
        lines.append("- **联系方式**:")
        if basic_info.get('telephone'):
            lines.append(f"  - 电话: {basic_info['telephone']}")
        if basic_info.get('email'):
            lines.append(f"  - 邮箱: {basic_info['email']}")
        if basic_info.get('orgWebsite'):
            lines.append(f"  - 官网: {basic_info['orgWebsite']}")

    # 管理层
    if any(basic_info.get(k) for k in ['chairMan', 'manager', 'secretary']):
        lines.append("- **管理层**:")
        if basic_info.get('chairMan'):
            lines.append(f"  - 董事长: {basic_info['chairMan']}")
        if basic_info.get('manager'):
            lines.append(f"  - 总经理: {basic_info['manager']}")
        if basic_info.get('secretary'):
            lines.append(f"  - 董秘: {basic_info['secretary']}")

    return "\n".join(lines)


def format_key_indicators(indicators: Optional[List[Dict[str, Any]]],
                          periods: int = 5) -> str:
    """格式化关键财务指标

    Args:
        indicators: akshare_api.get_key_indicators 返回的列表
        periods: 显示几期数据

    Returns:
        Markdown 格式的文本
    """
    if not indicators or len(indicators) == 0:
        return "*未获取到关键财务指标*\n"

    lines = []
    lines.append("| 报告期 | 营业总收入 | 归母净利润 | 基本每股收益 | 加权平均 ROE | 资产负债率 |")
    lines.append("|--------|-----------|-----------|-------------|-------------|-----------|")

    for item in indicators[:periods]:
        report_date = item.get('reportDate', 'N/A')
        revenue = format_number(item.get('totalRevenue'), '亿元')
        profit = format_number(item.get('netProfitAtsopc'), '亿元')
        eps = format_number(item.get('basicEps'))
        roe = format_number(item.get('wgtAvgRoe'), '%')
        debt_ratio = format_number(item.get('assetLiabRatio'), '%')

        lines.append(f"| {report_date} | {revenue} | {profit} | {eps} | {roe} | {debt_ratio} |")

    return "\n".join(lines)


def format_industry_rank(rank_data: Dict[str, Any], metric_name: str) -> str:
    """格式化行业排名

    Args:
        rank_data: akshare_api.get_industry_rank 返回的数据
        metric_name: 指标名称（如"ROE"、"PE"）

    Returns:
        Markdown 格式的文本
    """
    if not rank_data:
        return f"*未获取到 {metric_name} 行业排名*\n"

    lines = []
    industry_name = rank_data.get('industryName', 'N/A')
    report_date = rank_data.get('reportDate', 'N/A')
    company_rank = rank_data.get('industryRank', 'N/A')
    industry_avg = rank_data.get('industryAvg', 'N/A')

    lines.append(f"- **所属行业**: {industry_name}")
    lines.append(f"- **报告期**: {report_date}")
    lines.append(f"- **公司排名**: {company_rank}")
    lines.append(f"- **行业均值**: {industry_avg}")

    # 同业前 10
    industry_list = rank_data.get('industryList', [])
    if industry_list and len(industry_list) > 0:
        lines.append("- **同业前 10**:")
        for i, company in enumerate(industry_list[:10], 1):
            sec_name = company.get('secName', 'N/A')
            sec_code = company.get('secCode', 'N/A')
            rank = company.get('rank', 'N/A')
            value = company.get('value', 'N/A')
            if isinstance(value, (int, float)):
                value = f"{value:.1f} 亿"
            lines.append(f"  {i}. {sec_name} ({sec_code}): 排名 {rank}, {metric_name} = {value}")

    return "\n".join(lines)


def format_policy_highlights(highlights: List[Dict[str, str]],
                             category: str = "政策") -> str:
    """格式化政策/标准检索结果

    Args:
        highlights: dknowc_search.extract_policy_highlights 返回的列表
        category: 类别（"政策"或"标准"）

    Returns:
        Markdown 格式的文本
    """
    if not highlights or len(highlights) == 0:
        return f"*未检索到相关{category}*\n"

    lines = []
    for i, item in enumerate(highlights, 1):
        title = item.get('title', 'N/A')
        source = item.get('source', '') or '未标注来源'
        date = item.get('date', '') or '未标注日期'
        date_note = item.get('dateNote', '')
        excerpt = item.get('excerpt', '')

        lines.append(f"### {i}. {title}\n")
        lines.append(f"- **来源**: {source}")
        lines.append(f"- **日期**: {date}")
        if date_note:
            lines.append(f"- **日期校验**: {date_note}")
        if excerpt:
            lines.append(f"- **摘要**: {excerpt}")
        lines.append("")

    return "\n".join(lines)


def format_impact_section(impact_data: Dict[str, Any], no: str) -> List[str]:
    """渲染"政策影响分析"板块（Markdown）

    Args:
        impact_data: impact_analysis.generate_impact_analysis 的返回值
        no: 板块中文序号（如"六"）

    Returns:
        Markdown 行列表
    """
    lines: List[str] = []
    summary = impact_data.get("summary", {})
    signals = impact_data.get("signals", [])

    lines.append(f"## {no}、政策影响分析（投资视角）")
    lines.append("")
    lines.append("> 本板块把第四、五部分检索到的政策/标准翻译为投资研究可用的判断：")
    lines.append("> 每条含影响方向、传导链、影响变量、跟踪指标与投资含义，角标可回溯原文。")
    lines.append("")

    # 财务信号
    if signals:
        lines.append("### 财务信号")
        lines.append("")
        for s in signals:
            lines.append(f"- {s}")
        lines.append("")

    # 总览表
    lines.append("### 政策影响总览")
    lines.append("")
    lines.append(f"- **利好**: {summary.get('bull_count', 0)} 条（{', '.join(summary.get('bull_sids', [])) or '无'}）")
    lines.append(f"- **利空关注**: {summary.get('bear_count', 0)} 条（{', '.join(summary.get('bear_sids', [])) or '无'}）")
    lines.append(f"- **中性/待研判**: {summary.get('neutral_count', 0)} 条")
    lines.append("")

    # 逐条分析
    all_items = impact_data.get("policies", []) + impact_data.get("standards", [])
    for a in all_items:
        lines.append(f"#### [{a['sid']}] {a['title']}")
        lines.append("")
        lines.append(f"- **类型**: {a['type_label']} | **方向**: {a['direction']} | **时间窗口**: {a['time_window']}")
        lines.append(f"- **传导链**: {a['chain']}")
        lines.append(f"- **影响变量**: {'、'.join(a['variables'])}")
        if a.get("financial_link"):
            lines.append(f"- **财务联动**: {a['financial_link']}")
        lines.append(f"- **跟踪指标**: {a['tracking']}")
        lines.append(f"- **投资含义**: {a['investment_view']}")
        lines.append("")

    # 阅读指引
    lines.append("**如何使用本板块**：")
    lines.append("")
    lines.append("1. **建模时**——把「时间窗口」内的政策变量写入营收/成本假设的依据栏；")
    lines.append("2. **跟踪时**——按「跟踪指标」建立监测清单，政策生效日往往是财务拐点的先行信号；")
    lines.append("3. **归因时**——财报异常先对照本板块传导链做拆解（如需求前置、退坡、合规成本）；")
    lines.append("4. **核验时**——所有判断点击/查阅角标对应原文，确认现行有效性与具体条款。")
    lines.append("")
    lines.append("**边界说明**：传导分析为规则模板 + 财务数据联动生成的结构化研判，")
    lines.append("仅供研究参考；具体条款以角标对应官方原文为准，不构成投资建议。")
    lines.append("")

    return lines


def _fmt_pct(x) -> str:
    try:
        return f"{float(x) * 100:.0f}%"
    except (TypeError, ValueError):
        return "--"


def _fmt_pct_pp(x) -> str:
    """分位值（0~1）-> 百分比"""
    try:
        return f"{float(x) * 100:.0f}%"
    except (TypeError, ValueError):
        return "--"


def format_decision_section(valuation_data: Dict[str, Any], no: str) -> List[str]:
    """渲染"投资决策整合"板块（Markdown）

    Args:
        valuation_data: valuation.generate_valuation_data 的返回值
        no: 板块中文序号（如"七"）

    Returns:
        Markdown 行列表
    """
    lines: List[str] = []
    basic = valuation_data.get("basic", {})
    dcf = valuation_data.get("dcf", {})
    relative = valuation_data.get("relative", {})
    bands = valuation_data.get("bands", {})
    matrix = valuation_data.get("matrix", {})
    disclaim = valuation_data.get("disclaimer", "")

    lines.append(f"## {no}、投资决策整合（估值区间 · 研究参考 · 非投资建议）")
    lines.append("")
    lines.append("> 本板块把财务基本面（第二部分）与政策影响（上一部分）收敛为一个研究性结论：")
    lines.append("> DCF 内在价值三情景 + 相对估值 + 目标价区间 + 决策矩阵。")
    lines.append("> **所有区间/动作均为规则模型生成的研究参考，非投资建议，据此操作风险自担。**")
    lines.append("")

    # 估值基础
    price = basic.get("price")
    lines.append("### 估值基础")
    lines.append("")
    src_note = basic.get("sourceNote") or "公开披露数据（akshare）"
    price_txt = f"{price:.2f} 元" if price else "--"
    mcap_txt = f"{basic.get('marketCap')} 亿元" if basic.get("marketCap") is not None else "--"
    share_txt = f"{basic.get('totalShare')} 亿股" if basic.get("totalShare") is not None else "--"
    pe_txt = f"{basic.get('peTtm')}" if basic.get("peTtm") is not None else "--"
    pb_txt = f"{basic.get('pb')}" if basic.get("pb") is not None else "--"
    lines.append(f"- **现价**: {price_txt} | **总市值**: {mcap_txt} | **总股本**: {share_txt}")
    lines.append(f"- **PE(TTM)**: {pe_txt} | **PB**: {pb_txt} | **数据源**: {src_note}")
    lines.append("")

    # DCF
    lines.append("### DCF 内在价值（三情景）")
    lines.append("")
    if dcf.get("available"):
        scen = dcf.get("scenarios", {})
        lines.append("| 情景 | 增速假设 | 内在价值(元/股) | 较现价 |")
        lines.append("|------|---------|----------------|--------|")
        for key in ("pessimistic", "neutral", "optimistic"):
            s = scen.get(key) or {}
            up = s.get("upsidePct")
            up_txt = f"{up:+.1f}%" if up is not None else "--"
            lines.append(f"| {s.get('label','')} | {_fmt_pct(s.get('growth'))} | "
                         f"{s.get('intrinsicPs','--')} | {up_txt} |")
        lines.append("")
        lines.append("**假设与依据**：")
        lines.append("")
        for a in dcf.get("assumptions", []):
            lines.append(f"- {a}")
        lines.append("")
        if dcf.get("applicabilityNote"):
            lines.append(f"> {dcf['applicabilityNote']}")
            lines.append("")
        pa = dcf.get("policyAdj", {})
        lines.append(f"**政策衔接**：{pa.get('note','')} "
                     f"（利好 {pa.get('bullCount',0)} / 利空 {pa.get('bearCount',0)}）")
        lines.append("")
    else:
        lines.append("> 本次缺少可用的自由现金流（FCF）或股本口径，或公司处于重资产扩张期（FCF 为负），")
        lines.append("> **DCF 不输出量化内在价值**，估值判断以下方相对估值与决策矩阵为准（不硬算、不编造）。")
        lines.append("")

    # 相对估值
    lines.append("### 相对估值")
    lines.append("")
    cur = relative.get("current", {})
    pe_c, pb_c = cur.get("peTtm"), cur.get("pb")
    lines.append(f"- **当前**：PE(TTM) {pe_c if pe_c is not None else '--'} / PB {pb_c if pb_c is not None else '--'}")
    mode = relative.get("mode")
    if mode == "percentile":
        pl = relative.get("percentile") or {}
        pe_p, pb_p = (pl.get("pe") or {}), (pl.get("pb") or {})
        pe_txt = (f"{pe_p.get('current')}，{_fmt_pct_pp(pe_p.get('pct'))} 历史分位（{pe_p.get('label','')}）"
                  if pe_p.get("pct") is not None else "--")
        pb_txt = (f"{pb_p.get('current')}，{_fmt_pct_pp(pb_p.get('pct'))} 历史分位（{pb_p.get('label','')}）"
                  if pb_p.get("pct") is not None else "--")
        lines.append(f"- **PE 分位**：{pe_txt} | **PB 分位**：{pb_txt}")
        lines.append(f"- 口径：{relative.get('comment','')}（近 {pe_p.get('windowDays','--')} 交易日）")
    elif mode == "peer_median":
        pm = relative.get("peerMedian") or {}
        lines.append(f"- **同业中位**：PE {pm.get('peerMedianPe') if pm.get('peerMedianPe') is not None else '--'} "
                     f"/ PB {pm.get('peerMedianPb') if pm.get('peerMedianPb') is not None else '--'}"
                     f"（{pm.get('industryName','同业')}，样本 {pm.get('sampleN','--')} 家）")
        lines.append(f"- **市场中位**：PE {pm.get('marketMedianPe') if pm.get('marketMedianPe') is not None else '--'} "
                     f"/ PB {pm.get('marketMedianPb') if pm.get('marketMedianPb') is not None else '--'}")
        lines.append(f"- 说明：{pm.get('note','')}")
    else:
        lines.append(f"- 说明：{relative.get('comment','')}")
    lines.append("")

    # 目标价区间
    lines.append("### 估值区间与安全边际")
    lines.append("")
    if bands.get("intrinsicCenter"):
        lines.append(f"- **中性内在价值**: {bands['intrinsicCenter']} 元/股")
        if bands.get("buyBelow") is not None:
            lines.append(f"- **买入关注区（模型假设）**: ≤ {bands['buyBelow']} 元")
        if bands.get("sellAbove") is not None:
            lines.append(f"- **卖出/高估观察区（模型假设）**: ≥ {bands['sellAbove']} 元")
        if bands.get("hold"):
            lines.append(f"- **持有观察区（模型假设）**: {bands['hold'][0]} ~ {bands['hold'][1]} 元")
        lines.append(f"- 口径：{bands.get('basedOn','')}")
    else:
        lines.append(f"- {bands.get('basedOn','')}")
        if bands.get("note"):
            lines.append(f"- 提示：{bands.get('note')}")
    lines.append("")

    # 决策矩阵
    lines.append("### 决策矩阵")
    lines.append("")
    lines.append("| 维度 | 权重 | 打分 | 依据 |")
    lines.append("|------|------|------|------|")
    for d in matrix.get("dimensions", []):
        ev = "；".join(d.get("evidence", [])) or "--"
        w = d.get("weight")
        w_txt = f"{w*100:.0f}%" if w is not None else "--"
        lines.append(f"| {d.get('label','')} | {w_txt} | "
                     f"{d.get('tone','')}（{d.get('score','--')}/3） | {ev} |")
    lines.append("")
    mat_score = matrix.get("score")
    score_txt = f"{mat_score*100:.0f}/100" if mat_score is not None else "--"
    lines.append(f"**综合得分**: {score_txt}")
    lines.append("")
    if matrix.get("policyWeightZero"):
        lines.append("> 注：未开通深知检索，本次决策**未纳入政策/标准维度**，退化为财务质量 + 估值维度。")
        lines.append("")
    lines.append(f"**动作建议（研究参考）**: **{matrix.get('actionLabel','--')}**")
    lines.append("")
    if matrix.get("actionNote"):
        lines.append(f"- {matrix.get('actionNote')}")
        lines.append("")
    lines.append(f"- 模型说明：{matrix.get('rulesNote','')}")
    lines.append("")

    # 免责
    lines.append("---")
    lines.append("")
    lines.append(f"> ⚠️ **免责声明**：{disclaim}")
    lines.append("")

    return lines


def generate_report(stock_code: str,
                    company_data: Dict[str, Any],
                    policy_data: Dict[str, Any],
                    impact_data: Optional[Dict[str, Any]] = None,
                    valuation_data: Optional[Dict[str, Any]] = None) -> str:
    """生成完整的增强版投研报告

    Args:
        stock_code: 股票代码
        company_data: akshare_api.get_company_research_data 返回的数据
        policy_data: dknowc_search.get_full_research_data 返回的数据
        impact_data: impact_analysis.generate_impact_analysis 返回的影响分析（可选）
        valuation_data: valuation.generate_valuation_data 返回的估值/决策整合（可选）

    Returns:
        完整的 Markdown 报告
    """
    lines = []

    # 标题
    basic_info = company_data.get('basicInfo', {})
    company_name = basic_info.get('secName', stock_code) if basic_info else stock_code
    industry_name = basic_info.get('industryName', 'N/A') if basic_info else 'N/A'

    lines.append(f"# {company_name}（{stock_code}）深度研究报告")
    lines.append("")
    lines.append(f"**行业**: {industry_name} | **报告日期**: {datetime.now().strftime('%Y-%m-%d')}")
    lines.append("")

    # 第一部分：公司概况
    lines.append("## 一、公司概况")
    lines.append("")
    lines.append(format_basic_info(company_data.get('basicInfo')))
    lines.append("")

    # 第二部分：财务数据
    lines.append("## 二、关键财务指标")
    lines.append("")
    lines.append(format_key_indicators(company_data.get('keyIndicators')))
    lines.append("")

    # 第三部分：行业排名
    lines.append("## 三、行业对比")
    lines.append("")
    industry_ranks = company_data.get('industryRanks', {})
    if industry_ranks:
        for metric, rank_data in industry_ranks.items():
            metric_name = {
                'jzcsyl': 'ROE',
                'pe': 'PE',
                'pb': 'PB',
                'gmjlr': '归母净利润'
            }.get(metric, metric)
            lines.append(f"### {metric_name} 行业排名")
            lines.append("")
            lines.append(format_industry_rank(rank_data, metric_name))
            lines.append("")
    else:
        lines.append("*未获取行业排名（板块成分接口降级），可稍后重试或参考同业个股对比*")
        lines.append("")

    # 第四部分：政策环境（增强）
    lines.append("## 四、政策环境（增强分析）")
    lines.append("")
    policy_highlights = policy_data.get('policyHighlights', [])
    lines.append(f"共检索到 **{len(policy_highlights)}** 条相关政策：")
    lines.append("")
    lines.append(format_policy_highlights(policy_highlights, "政策"))

    # 第五部分：标准与准入（增强）
    lines.append("## 五、标准与准入（增强分析）")
    lines.append("")
    standard_highlights = policy_data.get('standardHighlights', [])
    lines.append(f"共检索到 **{len(standard_highlights)}** 条相关标准/规范：")
    lines.append("")
    lines.append(format_policy_highlights(standard_highlights, "标准"))

    # 政策影响分析 / 投资决策整合（编号动态：前五部分固定，二者都可能缺失）
    next_no = 6
    if impact_data:
        lines.extend(format_impact_section(impact_data, _CN_NO[next_no]))
        next_no += 1
    if valuation_data:
        lines.extend(format_decision_section(valuation_data, _CN_NO[next_no]))
        next_no += 1

    # 风险提示
    lines.append("---")
    lines.append("")
    lines.append("**风险提示**: 本报告基于公开数据整理，不构成投资建议。政策信息来自深知可信搜索，")
    lines.append("现行有效性以官方发布为准。市场有风险，投资需谨慎。")
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    # 测试（需要先有数据）
    print("[format_report] 模块测试需要先获取数据")
    print("请使用 run_research.py 运行完整流程")
