#!/usr/bin/env python3
"""深知可信投研主入口：公司研究 + 政策标准可信洞察 一键生成

流程：
1. 初始化检查（DKNOWC_API_KEY；金融数据走 akshare 开源库免 Key）
2. 股票代码解析（名称 -> 6 位代码）
3. 公司数据（akshare：基本资料 + 财务指标 + 行业定位）
4. 深知可信搜索（政策 / 标准检索，需 DKNOWC_API_KEY）
5. 政策影响分析 + 双格式输出（Markdown + 可溯源 HTML + data.json）

用法：
    python run_research.py "比亚迪"
    python run_research.py "比亚迪" official-docs/output/比亚迪_报告.md

工作区约定（与深知可信搜索一致）：
    official-docs/search-results/  中间产物
    official-docs/output/          最终交付物
"""

import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

SKILL_ROOT = Path(__file__).resolve().parent.parent
SEARCH_RESULTS_DIR = SKILL_ROOT / "official-docs" / "search-results"
OUTPUT_DIR = SKILL_ROOT / "official-docs" / "output"


def init_check() -> bool:
    """运行 initialize.py，检查深知 Key 状态"""
    r = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "initialize.py")],
        capture_output=True, text=True,
    )
    try:
        status = json.loads(r.stdout)
    except json.JSONDecodeError:
        print("[init] 初始化检查执行失败")
        return False
    if status.get("ready"):
        return True
    print("[init] 尚未开通深知权威检索能力（缺少 DKNOWC_API_KEY）。")
    print("       请先通过 scripts/register_key.mjs 注册获取 Key 并注入环境变量，")
    print("       或在 Skill 引导下完成手机号验证开通。金融数据部分不受影响。")
    return False


def resolve_output_path(value: Optional[str]) -> Path:
    """输出路径定位到 official-docs/output/（与深知可信搜索约定一致）"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not value:
        return OUTPUT_DIR / f"投研报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    raw = Path(value)
    resolved = raw if raw.is_absolute() else (OUTPUT_DIR / raw.name).resolve()
    if resolved.suffix.lower() != ".md":
        resolved = resolved.with_suffix(".md")
    try:
        resolved.relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        resolved = OUTPUT_DIR / resolved.name
    return resolved


def run(stock_keyword: str, output_file: Optional[str] = None) -> str:
    print(f"{'=' * 60}")
    print(f"深知可信投研: {stock_keyword}")
    print(f"{'=' * 60}")

    # 1. 初始化检查
    print("\n[1/5] 初始化检查...")
    key_ready = init_check()
    key_status = "已开通" if key_ready else "未开通（政策/标准板块将跳过）"
    print(f"  {'✓' if key_ready else '⚠'} 深知检索能力: {key_status}")

    # 2. 股票代码
    print(f"\n[2/5] 解析股票: {stock_keyword}")
    from akshare_api import lookup_stock, get_company_research_data, ensure_akshare
    print(f"  Python 解释器: {sys.executable}")
    ensure_akshare(auto_install=True)
    import re as _re
    if _re.fullmatch(r"\d{6}", stock_keyword):
        stock_code, company_name = stock_keyword, stock_keyword
        print(f"  ✓ 使用代码: {stock_code}")
    else:
        hit = lookup_stock(stock_keyword)
        if not hit:
            raise SystemExit(f"未找到股票: {stock_keyword}，请使用 6 位代码或准确简称")
        stock_code, company_name = hit["symbol"], hit["symbolName"]
        print(f"  ✓ {company_name} ({stock_code})")

    # 3. 公司数据（akshare，免 Key）
    print(f"\n[3/5] 公司数据（akshare 开源公开披露）...")
    company_data = get_company_research_data(stock_code, fallback_name=company_name)
    basic = company_data.get("basicInfo") or {}
    kis = company_data.get("keyIndicators") or []
    print(f"  ✓ {basic.get('secName') or company_name} | 行业: {basic.get('industryName') or 'N/A'}")
    print(f"  ✓ 财务指标: {len(kis)} 期 | 行业定位: {'有' if company_data.get('industryRanks') else '无（板块降级）'}")

    # 4. 深知政策/标准检索
    print(f"\n[4/5] 深知可信搜索（政策/标准）...")
    policy_data = {"policyHighlights": [], "standardHighlights": []}
    if key_ready:
        from config import load_config
        from dknowc_search import get_full_research_data
        config = load_config()
        industry = basic.get("resolvedIndustry") or basic.get("industryName") or stock_keyword
        policy_data = get_full_research_data(config, industry)
        print(f"  ✓ 政策 {len(policy_data.get('policyHighlights', []))} 条 | "
              f"标准 {len(policy_data.get('standardHighlights', []))} 条")
    else:
        print("  ⚠ 跳过（未开通深知检索能力）")

    # 5. 影响分析 + 三件套输出
    print(f"\n[5/5] 政策影响分析 + 报告生成...")
    impact_data = None
    if policy_data.get("policyHighlights") or policy_data.get("standardHighlights"):
        from impact_analysis import generate_impact_analysis
        impact_data = generate_impact_analysis(company_data, policy_data)
        s = impact_data["summary"]
        print(f"  ✓ 影响分析 {s['total']} 条（利好 {s['bull_count']} / 利空 {s['bear_count']} / 中性 {s['neutral_count']}）")

    from format_report import generate_report
    from render_html import generate_report_html
    report = generate_report(stock_code, company_data, policy_data, impact_data)
    report_html = generate_report_html(stock_code, company_data, policy_data, impact_data)

    output_path = resolve_output_path(output_file)
    output_path.write_text(report, encoding="utf-8")
    html_path = output_path.with_suffix(".html")
    html_path.write_text(report_html, encoding="utf-8")
    data_path = output_path.with_suffix(".data.json")
    data_path.write_text(json.dumps({
        "stockCode": stock_code,
        "companyData": company_data,
        "policyData": policy_data,
        "impactData": impact_data,
        "generatedAt": datetime.now().isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'=' * 60}")
    print(f"Markdown 报告: {output_path}")
    print(f"可溯源 HTML:   {html_path}")
    print(f"原始数据 JSON: {data_path}")
    print(f"{'=' * 60}")

    return report


def main():
    if len(sys.argv) < 2:
        print("用法: python run_research.py <股票代码或名称> [输出文件名]")
        print("示例: python run_research.py 比亚迪")
        sys.exit(1)
    try:
        run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
