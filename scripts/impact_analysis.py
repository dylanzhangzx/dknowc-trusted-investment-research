#!/usr/bin/env python3
"""政策影响分析模块

把"检索到的政策/标准清单"翻译为"投资研究可用的判断"：
- 每条政策/标准输出：影响方向、时间窗口、传导链、影响变量、
  财务映射、跟踪指标、投资含义
- 每条判断绑定来源角标（P1..Pn / S1..Sn），可回溯到深知检索原文
- 提供汇总视图：利好/利空/中性统计 + 关注时间轴

分析由"规则映射 + 模板生成"完成：
- 规则层：基于关键词识别政策类型（补贴/税收/准入/技术标准/基础设施），
  映射到标准传导链
- 模板层：结合公司实际财务数据（增速拐点等）填充投资含义
- Agent 层（可选）：在 WorkBuddy 等平台运行时，AI 可基于本模块输出
  的结构化材料进一步细化判断，但必须保留角标
"""

from typing import Any, Dict, List, Optional
from datetime import datetime


# ============================================================
# 规则层：政策类型识别与传导链映射
# ============================================================

POLICY_RULES = [
    {
        "key": "tax_reduction",
        "keywords": ["购置税", "减免", "免征", "税收优惠", "减半", "退税", "加计扣除"],
        "type_label": "财税优惠",
        "direction_hint": "利好（注意退坡条款）",
        "chain": "税收/补贴条款 → 终端购买成本 → 需求量 → 公司收入与利润",
        "variables": ["终端需求", "产品价格弹性", "单价/单件税惠金额"],
    },
    {
        "key": "subsidy",
        "keywords": ["补贴", "补助", "奖励", "专项资金", "财政支持"],
        "type_label": "财政补贴",
        "direction_hint": "利好（关注申报窗口与名单）",
        "chain": "补贴目录/名单 → 企业申报资格 → 其他收益/现金流 → 利润",
        "variables": ["申报资格", "补贴额度", "到账节奏"],
    },
    {
        "key": "tech_threshold",
        "keywords": ["技术要求", "技术指标", "能耗", "续航", "能量密度", "准入", "目录管理", "规范条件", "生产准入"],
        "type_label": "技术/准入门槛",
        "direction_hint": "利好龙头，加速尾部出清",
        "chain": "技术门槛提高 → 合规成本上升 → 中小产能退出 → 行业集中度提升",
        "variables": ["行业集中度", "合规 Capex", "产品认证周期"],
    },
    {
        "key": "infrastructure",
        "keywords": ["基础设施", "充电", "加氢", "换电", "网络建设", "体系"],
        "type_label": "基础设施建设",
        "direction_hint": "长期利好（渗透率逻辑）",
        "chain": "配套设施完善 → 使用痛点缓解 → 渗透率天花板抬升 → 行业总需求",
        "variables": ["渗透率", "设施覆盖率", "使用成本"],
    },
    {
        "key": "safety_env_standard",
        "keywords": ["安全", "环保", "排放", "碳足迹", "回收", "综合利用", "强制性国家标准", "GB"],
        "type_label": "安全/环保/回收标准",
        "direction_hint": "中性偏成本（合规要求）",
        "chain": "强制标准实施 → 合规改造/认证投入 → 成本上升 / 无资质者出清",
        "variables": ["合规成本", "改造周期", "市场份额再分配"],
    },
    {
        "key": "industrial_plan",
        "keywords": ["发展规划", "行动计划", "指导意见", "产业政策", "高质量发展", "战略"],
        "type_label": "产业规划",
        "direction_hint": "长期利好（方向性）",
        "chain": "顶层规划 → 配套政策预期 → 行业景气度与估值预期",
        "variables": ["后续配套政策", "地方落地节奏"],
    },
]


def classify_policy(title: str, excerpt: str = "") -> Dict[str, Any]:
    """根据标题与摘录关键词识别政策类型，返回规则映射"""
    text = f"{title} {excerpt}"
    for rule in POLICY_RULES:
        hits = [kw for kw in rule["keywords"] if kw in text]
        if hits:
            return {**rule, "matched": hits}
    # 兜底：未识别类型
    return {
        "key": "other",
        "type_label": "综合政策",
        "direction_hint": "需人工研判",
        "chain": "政策发布 → 待识别传导路径",
        "variables": ["政策细则"],
        "matched": [],
    }


# ============================================================
# 时间窗口提取
# ============================================================

def extract_time_window(text: str) -> str:
    """从文本中粗提取生效/时间窗口信息"""
    import re
    # 常见模式：2026年起 / 2026-01-01 / 2024年1月1日 / 到2030年 / 2026-2027
    patterns = [
        (r"20\d{2}\s*年\s*起", None),
        (r"20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}日?", None),
        (r"20\d{2}\s*[-—至]\s*20\d{2}", None),
        (r"到\s*20\d{2}\s*年", None),
        (r"20\d{2}\s*年\s*\d{1,2}\s*月", None),
        (r"20\d{2}\s*年", None),
    ]
    found = []
    for pat, _ in patterns:
        m = re.search(pat, text)
        if m:
            found.append(m.group(0))
    if not found:
        return "未标注（见原文）"
    return " / ".join(dict.fromkeys(found))  # 去重保序


# ============================================================
# 财务联动：从公司数据提取关键信号
# ============================================================

def financial_signals(company_data: Dict[str, Any]) -> List[str]:
    """从关键财务指标提取可用于政策联动的信号（同比口径）

    同比 = 最新报告期 vs 上年同期报告期（相同月-日）。
    keyIndicators 为累计口径列表，不能直接拿相邻两期当同比
    （如 2026Q1 vs 2025 年报是"季 vs 全年"，口径不可比）。
    """
    signals = []
    kis = company_data.get("keyIndicators") or []
    if not kis:
        return signals

    latest = kis[0]
    latest_date = str(latest.get("reportDate") or "")

    # 找上年同期：2026-03-31 → 2025-03-31
    base = None
    try:
        from datetime import date
        y, m, d = (int(x) for x in latest_date.split("-"))
        target = date(y - 1, m, d).isoformat()
        for item in kis[1:]:
            if str(item.get("reportDate") or "") == target:
                base = item
                break
    except (ValueError, TypeError):
        base = None

    if base is None:
        # 无上年同期数据：不计算，避免跨口径误导
        signals.append(
            f"【财务信号】最新报告期 {latest_date or '未知'}；未找到上年同期数据，"
            f"本期不生成同比信号（避免季/年累计口径误比）"
        )
        return signals

    try:
        rev_now = float(latest.get("totalRevenue") or 0)
        rev_prev = float(base.get("totalRevenue") or 0)
        np_now = float(latest.get("netProfitAtsopc") or 0)
        np_prev = float(base.get("netProfitAtsopc") or 0)
        if rev_prev <= 0 or np_prev <= 0:
            return signals
        rev_yoy = (rev_now / rev_prev - 1) * 100
        np_yoy = (np_now / np_prev - 1) * 100
        period = latest_date
        if np_yoy < -20:
            signals.append(
                f"【财务信号】{period} 归母净利润同比 {np_yoy:+.1f}%（营收同比 {rev_yoy:+.1f}%），"
                f"利润显著承压——建议结合政策时间表（如税惠退坡、需求前置）拆解归因"
            )
        elif np_yoy > 30:
            signals.append(
                f"【财务信号】{period} 归母净利润同比 {np_yoy:+.1f}%（营收同比 {rev_yoy:+.1f}%），"
                f"利润高增——可核查是否存在政策红利（补贴/税惠/格局改善）贡献"
            )
        else:
            signals.append(
                f"【财务信号】{period} 营收同比 {rev_yoy:+.1f}%，归母净利润同比 {np_yoy:+.1f}%"
            )
    except (ValueError, ZeroDivisionError):
        pass
    return signals


# ============================================================
# 单条分析生成
# ============================================================

def analyze_one(item: Dict[str, str], sid: str,
                category: str = "policy") -> Dict[str, Any]:
    """对单条政策/标准生成结构化影响分析

    Args:
        item: policyHighlights/standardHighlights 中的条目
        sid: 来源角标 ID（P1/S1...）
        category: policy / standard
    """
    title = item.get("title", "")
    excerpt = item.get("excerpt", "")
    # 信号检测用全文（截断）：摘录仅 200 字，退坡/减免幅度等关键条款
    # 常在正文深处（如购置税公告第三条"2026-2027年减半征收"）
    full_text = (item.get("fullText") or "")[:1500]
    signal_text = f"{title} {excerpt} {full_text}"
    rule = classify_policy(title, signal_text)

    # 时间窗口：优先用多源校验后的发布日期；正文关键时点仅在与发布
    # 年份一致时展示（接口错误日期常残留在正文中，不一致时舍弃以免误导）
    date_str = item.get("date", "")
    tw = extract_time_window(f"{title} {excerpt}")
    if date_str and date_str not in ("日期待核验", "未标注日期"):
        import re as _re
        pub_year_m = _re.search(r"(20\d{2})", date_str)
        tw_year_m = _re.search(r"(20\d{2})", tw or "")
        tw_ok = (
            tw and tw != "未标注（见原文）"
            and (not pub_year_m or not tw_year_m or tw_year_m.group(1) == pub_year_m.group(1))
        )
        time_window = f"发布 {date_str}" + (f"；关键时点 {tw}" if tw_ok else "")
    else:
        time_window = tw

    # 影响方向细化
    direction = rule["direction_hint"]
    if any(kw in signal_text for kw in ["退坡", "减半", "取消", "废止", "移出", "收紧"]):
        direction = "利空关注（退坡/收紧信号）"

    return {
        "sid": sid,
        "category": category,
        "title": title,
        "type_label": rule["type_label"],
        "direction": direction,
        "time_window": time_window,
        "chain": rule["chain"],
        "variables": rule["variables"],
        "matched": rule.get("matched", []),
        "source": item.get("source", "未标注来源"),
        "date": item.get("date", "未标注日期"),
        "dateNote": item.get("dateNote", ""),
        "url": item.get("url", ""),
        "excerpt": excerpt,
    }


TRACKING_METRICS = {
    "tax_reduction": "月度终端销量/渗透率、公司产销快报、免税目录车型进出",
    "subsidy": "补贴目录/名单公告、公司其他收益科目、申报窗口通知",
    "tech_threshold": "准入目录更新、行业集中度（CR5）、竞品合规公告",
    "infrastructure": "设施建设进度数据、渗透率月度值",
    "safety_env_standard": "标准实施日历、公司合规投入公告、回收量数据",
    "industrial_plan": "部委后续配套文件、地方实施方案",
    "other": "政策原文后续修订",
}

INVESTMENT_TEMPLATES = {
    "tax_reduction": "税惠幅度变化直接影响终端购买成本。若处于退坡通道，需评估需求前置效应与退坡后的真实需求中枢，并下修/上修相应报告期营收假设。",
    "subsidy": "补贴影响利润表'其他收益'与现金流。关注公司是否在目录/名单内、补贴退坡节奏与申报窗口，避免把一次性补贴常态化计入盈利预测。",
    "tech_threshold": "技术门槛提升通常是龙头的'朋友'：合规成本相对可控，而尾部产能出清改善格局。跟踪行业集中度与竞品动态验证逻辑。",
    "infrastructure": "配套设施完善抬升渗透率天花板，属于行业 beta 逻辑。对个股传导较慢，适合作为行业长期空间假设的支撑证据。",
    "safety_env_standard": "强制标准带来确定性合规投入（成本项），同时淘汰无资质参与者（格局项）。需评估公司在标准实施前的准备进度。",
    "industrial_plan": "顶层规划本身不直接产生盈利，价值在于预判后续配套政策的密度与力度，可作为估值预期管理的背景板。",
    "other": "建议阅读原文研判传导路径后再纳入模型。",
}


def enrich_with_investment_view(analysis: Dict[str, Any],
                                signals: List[str]) -> Dict[str, Any]:
    """为单条分析补充跟踪指标与投资含义"""
    key = analysis.get("matched") and None  # placeholder, 使用 type 推断
    # 通过 type_label 反查 key
    for rule in POLICY_RULES:
        if rule["type_label"] == analysis["type_label"]:
            key = rule["key"]
            break
    key = key or "other"
    analysis["tracking"] = TRACKING_METRICS.get(key, TRACKING_METRICS["other"])
    analysis["investment_view"] = INVESTMENT_TEMPLATES.get(key, INVESTMENT_TEMPLATES["other"])

    # 财务信号联动：把最新一期财务信号附到最相关的条目（财税类优先）
    if signals and not enrich_with_investment_view.linked:
        analysis["financial_link"] = signals[0]
        enrich_with_investment_view.linked = True
    else:
        analysis["financial_link"] = ""
    return analysis


enrich_with_investment_view.linked = False


# ============================================================
# 汇总视图
# ============================================================

def summarize(analyses: List[Dict[str, Any]]) -> Dict[str, Any]:
    """生成政策影响总览（分类互斥：利空优先，其次利好，其余中性）"""
    bear = [a for a in analyses if "利空" in a["direction"]]
    bull = [a for a in analyses if "利好" in a["direction"] and a not in bear]
    neutral = [a for a in analyses if a not in bull and a not in bear]

    # 时间轴：按提取的时间窗口排序
    timeline = [
        {"title": a["title"], "window": a["time_window"], "direction": a["direction"], "sid": a["sid"]}
        for a in analyses if a["time_window"] != "未标注（见原文）"
    ]

    return {
        "total": len(analyses),
        "bull_count": len(bull),
        "bear_count": len(bear),
        "neutral_count": len(neutral),
        "bull_sids": [a["sid"] for a in bull],
        "bear_sids": [a["sid"] for a in bear],
        "timeline": timeline,
    }


# ============================================================
# 主入口
# ============================================================

def generate_impact_analysis(company_data: Dict[str, Any],
                             policy_data: Dict[str, Any]) -> Dict[str, Any]:
    """生成完整政策影响分析

    Returns:
        {
          "signals": [...财务信号...],
          "policies": [...带投资视角的政策分析...],
          "standards": [...带投资视角的标准分析...],
          "summary": {...总览...}
        }
    """
    signals = financial_signals(company_data)

    policies = []
    for i, item in enumerate(policy_data.get("policyHighlights", []), 1):
        a = analyze_one(item, f"P{i}", "policy")
        policies.append(enrich_with_investment_view(a, signals))

    standards = []
    for i, item in enumerate(policy_data.get("standardHighlights", []), 1):
        a = analyze_one(item, f"S{i}", "standard")
        standards.append(enrich_with_investment_view(a, signals))

    summary = summarize(policies + standards)

    return {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "signals": signals,
        "policies": policies,
        "standards": standards,
        "summary": summary,
    }


if __name__ == "__main__":
    # 自测：构造模拟数据
    mock_policy = {
        "policyHighlights": [
            {"title": "关于延续和优化新能源汽车车辆购置税减免政策的公告",
             "source": "中国政府网", "date": "2023-06-21", "url": "https://www.gov.cn/...",
             "excerpt": "2024-2025年免征购置税，2026-2027年减半征收，每辆减税额不超过1.5万元。"},
            {"title": "关于进一步构建高质量充电基础设施体系的指导意见",
             "source": "国务院办公厅", "date": "2023-06-19", "url": "",
             "excerpt": "到2030年基本建成高质量充电基础设施体系。"},
        ],
        "standardHighlights": [
            {"title": "GB/T 47136-2026 纯电动汽车动力蓄电池健康与安全状态评估规范",
             "source": "国家标准信息平台", "date": "2026-01-28", "url": "",
             "excerpt": "标准状态:现行"},
        ],
    }
    mock_company = {"keyIndicators": []}

    result = generate_impact_analysis(mock_company, mock_policy)
    print("=== 自测输出 ===")
    for p in result["policies"]:
        print(f"\n[{p['sid']}] {p['title'][:40]}")
        print(f"  类型: {p['type_label']} | 方向: {p['direction']} | 时间: {p['time_window']}")
        print(f"  传导: {p['chain']}")
        print(f"  投资含义: {p['investment_view'][:60]}")
    print(f"\n总览: {result['summary']['bull_count']} 利好 / {result['summary']['bear_count']} 利空关注 / {result['summary']['neutral_count']} 中性")
