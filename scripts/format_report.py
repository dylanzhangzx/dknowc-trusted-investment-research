#!/usr/bin/env python3
"""报告格式化模块

将公开披露公司数据（akshare）和深知政策检索结果组合成投研报告（Markdown 格式）。
"""

from typing import Dict, Any, List, Optional
from datetime import datetime


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


def format_impact_section(impact_data: Dict[str, Any]) -> List[str]:
    """渲染"六、政策影响分析"板块（Markdown）

    Args:
        impact_data: impact_analysis.generate_impact_analysis 的返回值

    Returns:
        Markdown 行列表
    """
    lines: List[str] = []
    summary = impact_data.get("summary", {})
    signals = impact_data.get("signals", [])

    lines.append("## 六、政策影响分析（投资视角）")
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


def generate_report(stock_code: str,
                    company_data: Dict[str, Any],
                    policy_data: Dict[str, Any],
                    impact_data: Optional[Dict[str, Any]] = None) -> str:
    """生成完整的增强版投研报告

    Args:
        stock_code: 股票代码
        company_data: akshare_api.get_company_research_data 返回的数据
        policy_data: dknowc_search.get_full_research_data 返回的数据
        impact_data: impact_analysis.generate_impact_analysis 返回的影响分析（可选）

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

    # 第六部分：政策影响分析（核心增强）
    if impact_data:
        lines.extend(format_impact_section(impact_data))

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
