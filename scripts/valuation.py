#!/usr/bin/env python3
"""投资决策整合层——深知可信投研的收敛结论板块

把"金融基本面（company_data）+ 政策影响（impact_data）"收敛为：
  DCF 内在价值（三情景） + 相对估值（历史分位/同业中位弹性降级）
  + 目标价区间（bands，模型假设）+ 决策矩阵（动作建议）

原则：
- 研究性估值整合，**非投资建议**；全部假设显式披露、强免责。
- 弹性降级：任一数据取不到时降级到下一档，**绝不编造、绝不卡住**。
- 只做 A 股；重资产扩张期 FCF 为负或缺失时 DCF 不硬算（available=False）。
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))

# ============================================================
# 模型常量（集中定义便于复核；均为研究性参数假设）
# ============================================================
WACC = 0.10            # 折现率基准
HI_YEARS = 10          # 两阶段显式预测年数
TERMINAL_GROWTH = 0.025  # 永续增速
SAFETY_BUFFER = 0.20   # 中性内在价值的安全垫
UPLIFT = 0.03          # 政策利好对乐观档增速的上调
DRAG = 0.03            # 政策利空对悲观档增速的下调
MIN_G = 0.02           # 增速下限（clamp）
MAX_G = 0.25           # 增速上限（clamp）
MIN_HIST_SAMPLES = 250  # 历史分位最少样本（交易日）

DISCLAIMER = (
    "本板块为基于公开披露数据与既定规则模型自动生成的估值区间与研究性结论，"
    "仅用于方法论演示与信息整合，不构成任何投资建议、证券推荐、收益承诺或买入/卖出指令。"
    "DCF 增速假设、折现率、政策方向修正均为参数化假设，实际结果可能显著偏离；"
    "历史 PE/PB 分位与同业比较存在口径与时效局限。"
    "证券投资决策请以持牌机构正式研究报告及您自身的独立判断为准，据此操作，风险自担。"
)


# ============================================================
# 工具
# ============================================================

def _f(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _annual_rows(cash_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """只保留年报期（-12-31 结尾）并按时间升序，用于算 FCF 基准与 CAGR。"""
    ann = [r for r in cash_rows if str(r.get("reportDate") or "").endswith("-12-31")]
    ann.sort(key=lambda x: str(x.get("reportDate", "")))
    return ann


def _cagr(values: List[float]) -> Optional[float]:
    """几何年均增速；要求首末值均为正。"""
    if len(values) < 2 or values[0] <= 0 or values[-1] <= 0:
        return None
    n = len(values) - 1
    return (values[-1] / values[0]) ** (1.0 / n) - 1.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _discount_intrinsic(base_fcf: float, growth: float, total_share: float) -> Optional[float]:
    """两阶段自由现金流折现 → 每股内在价值（元/股）。

    显式预测 HI_YEARS 年：FCF_t = base_fcf*(1+g)^t，逐年折现；
    终值 = FCF_10*(1+terminal)/(wacc-terminal)，折现回现值。
    """
    if total_share <= 0 or WACC <= TERMINAL_GROWTH:
        return None
    pv = 0.0
    fcf_t = base_fcf
    terminal_fcf = None
    for t in range(1, HI_YEARS + 1):
        fcf_t = base_fcf * (1 + growth) ** t
        pv += fcf_t / (1 + WACC) ** t
        if t == HI_YEARS:
            terminal_fcf = fcf_t
    terminal_pv = terminal_fcf * (1 + TERMINAL_GROWTH) / (WACC - TERMINAL_GROWTH)
    terminal_pv /= (1 + WACC) ** HI_YEARS
    equity = pv + terminal_pv
    return equity / total_share


def _score_label(score: int) -> str:
    return {1: "弱", 2: "中", 3: "强"}.get(score, "中")


# ============================================================
# 主入口
# ============================================================

def generate_valuation_data(stock_code: str,
                            company_data: Dict[str, Any],
                            impact_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """生成投资决策整合数据。

    Args:
        stock_code: 6 位 A 股代码
        company_data: get_company_research_data 返回（basicInfo/keyIndicators/industryRanks）
        impact_data: generate_impact_analysis 返回（可空）

    Returns:
        结构见模块注释 / 下游 format_decision_section。
    """
    from akshare_api import (
        get_cash_flow, get_valuation_history, get_peer_valuation,
    )

    basic = company_data.get("basicInfo") or {}
    kis = company_data.get("keyIndicators") or []
    industry = basic.get("resolvedIndustry") or basic.get("industryName")

    # ---- basic（现价/股本/市值/PE/PB，来自历史估值源或 basicInfo）----
    vh = get_valuation_history(stock_code)
    latest = (vh or {}).get("latest") or {}
    price = _f(latest.get("close")) or _f(basic.get("price"))
    total_share = _f(latest.get("totalShare")) or _f(basic.get("totalShare"))
    market_cap = _f(latest.get("marketCap")) or _f(basic.get("totalMarketCap"))
    pe_ttm = _f(latest.get("peTtm"))
    pb = _f(latest.get("pb"))
    basic_out = {
        "price": price,
        "totalShare": round(total_share / 1e8, 2) if total_share else None,  # 亿股
        "marketCap": round(market_cap / 1e8, 1) if market_cap else None,     # 亿元
        "peTtm": round(pe_ttm, 2) if pe_ttm else None,
        "pb": round(pb, 2) if pb else None,
        "sourceNote": (vh or {}).get("sourceNote", "公开披露数据（akshare）"),
        "reportDate": (latest or {}).get("date"),
    }

    # ---- 政策方向（用于 DCF 增速情景 + 决策矩阵 policy 维度）----
    bull = bear = neutral = 0
    if impact_data:
        s = impact_data.get("summary") or {}
        bull = int(s.get("bull_count", 0) or 0)
        bear = int(s.get("bear_count", 0) or 0)
        neutral = int(s.get("neutral_count", 0) or 0)
    policy_abs = impact_data is None

    # ============================================================
    # DCF（三情景）
    # ============================================================
    dcf = {"available": False, "assumptions": [], "policyAdj": {
        "applied": not policy_abs, "bullCount": bull, "bearCount": bear}}
    cf_rows = []
    try:
        cf_rows = get_cash_flow(stock_code, periods=8)
    except Exception as e:  # noqa: BLE001
        print(f"[valuation] 现金流量获取失败（DCF 降级）: {repr(e)[:100]}")
    annual = _annual_rows(cf_rows)
    fcf_series = [r for r in annual if r.get("fcf") is not None]
    fcf_positive = [r["fcf"] for r in fcf_series if r["fcf"] > 0]

    base_fcf = None
    base_source = ""
    if annual:
        # 基准取最近一个"FCF 为正"的年报期（重资产扩张期 FCF 为负则降级）
        for r in reversed(annual):
            if r.get("fcf") is not None and r["fcf"] > 0:
                base_fcf = r["fcf"]
                base_source = f"{r['reportDate']}年报 经营现金流-资本开支"
                break
    if base_fcf and total_share:
        # 中性增速：优先 FCF 历史 CAGR，其次净利历史 CAGR，再回落保守下限
        g = None
        note_cagr = ""
        if len(fcf_positive) >= 3:
            g = _cagr(fcf_positive)
            note_cagr = "基于近N个年报 FCF 几何增速"
        if g is None:
            np_series = []
            for r in annual:
                # 用 keyIndicators 里年报期净利做净利 CAGR（更稳，FCF 常为负）
                pass
            # 从 keyIndicators 取年报净利
            ann_ki = [k for k in kis if str(k.get("reportDate") or "").endswith("-12-31")]
            ann_ki.sort(key=lambda x: str(x.get("reportDate", "")))
            np_vals = [_f(k.get("netProfitAtsopc")) for k in ann_ki]
            np_vals = [v for v in np_vals if v is not None]
            if len(np_vals) >= 3:
                g = _cagr(np_vals)
                note_cagr = "基于近N个年报归母净利几何增速"
        if g is None:
            g = MIN_G
            note_cagr = "历史增速样本不足，回落保守下限 2%"
        g_neutral = _clamp(g, MIN_G, MAX_G)

        # 三情景：政策方向决定乐观/悲观档偏差
        g_opt = _clamp(g_neutral + UPLIFT, MIN_G, MAX_G)
        g_pess = _clamp(g_neutral - DRAG, MIN_G, MAX_G)
        applied_note = "政策方向中性/未介入，三档对称"
        if not policy_abs:
            if bull > bear:
                applied_note = f"政策净利好（利好 {bull} > 利空 {bear}），上调乐观档增速"
            elif bear > bull:
                applied_note = f"政策净利空（利空 {bear} > 利好 {bull}），下调悲观档增速"
            else:
                applied_note = "政策利好/利空均衡，三档对称"

        iv_n = _discount_intrinsic(base_fcf, g_neutral, total_share)
        iv_o = _discount_intrinsic(base_fcf, g_opt, total_share)
        iv_p = _discount_intrinsic(base_fcf, g_pess, total_share)

        def _ups(p):
            return round((p / price - 1) * 100, 1) if (p and price) else None

        if iv_n:
            dcf = {
                "available": True,
                "baseFcf": {
                    "value": round(base_fcf / 1e8, 2),
                    "source": base_source,
                },
                "cagr": {
                    "netProfitCagr": None, "fcfCagr": g, "years": len(annual),
                    "note": note_cagr,
                },
                "params": {
                    "wacc": WACC, "hiYears": HI_YEARS,
                    "terminalGrowth": TERMINAL_GROWTH, "safetyBuffer": SAFETY_BUFFER,
                },
                "scenarios": {
                    "neutral": {"label": "中性（基准）", "growth": g_neutral,
                                "intrinsicPs": round(iv_n, 2), "upsidePct": _ups(iv_n)},
                    "optimistic": {"label": "乐观", "growth": g_opt,
                                   "intrinsicPs": round(iv_o, 2) if iv_o else None,
                                   "upsidePct": _ups(iv_o)},
                    "pessimistic": {"label": "悲观", "growth": g_pess,
                                    "intrinsicPs": round(iv_p, 2) if iv_p else None,
                                    "upsidePct": _ups(iv_p)},
                },
                "policyAdj": {
                    "applied": not policy_abs, "bullCount": bull, "bearCount": bear,
                    "note": applied_note,
                },
                "assumptions": [
                    f"基准 FCF：{round(base_fcf/1e8,2)} 亿元（{base_source}）",
                    f"永续增速 {TERMINAL_GROWTH:.1%}，折现率 {WACC:.0%}，显式预测 {HI_YEARS} 年",
                    note_cagr,
                    applied_note,
                ],
            }

    # ============================================================
    # 相对估值（弹性降级三档）
    # ============================================================
    relative = {
        "mode": "directional", "degraded": True, "degradeReason": "",
        "current": {"peTtm": basic_out["peTtm"], "pb": basic_out["pb"]},
        "percentile": None, "peerMedian": None,
        "comment": "",
    }
    hist = (vh or {}).get("series") or []
    if vh and len(hist) >= MIN_HIST_SAMPLES and pe_ttm and pb:
        # 档1：历史分位
        pe_vals = [h["peTtm"] for h in hist if h.get("peTtm") is not None and h["peTtm"] > 0]
        pb_vals = [h["pb"] for h in hist if h.get("pb") is not None and h["pb"] > 0]

        def _pct(cur, vals):
            return round(sum(1 for v in vals if v <= cur) / len(vals), 2) if vals else None

        def _pl(p):
            if p is None:
                return None
            return "偏低" if p < 0.30 else ("偏高" if p > 0.70 else "中枢")
        pe_pct, pb_pct = _pct(pe_ttm, pe_vals), _pct(pb, pb_vals)
        relative = {
            "mode": "percentile", "degraded": False, "degradeReason": "",
            "current": {"peTtm": basic_out["peTtm"], "pb": basic_out["pb"]},
            "percentile": {
                "pe": {"current": basic_out["peTtm"], "pct": pe_pct,
                       "windowDays": len(pe_vals), "label": _pl(pe_pct)},
                "pb": {"current": basic_out["pb"], "pct": pb_pct,
                       "windowDays": len(pb_vals), "label": _pl(pb_pct)},
            },
            "peerMedian": None,
            "comment": "基于历史 PE/PB 分位（东方财富估值分析，近 N 个交易日）定位",
        }
    else:
        # 档2：同业/市场中位数
        peer = None
        try:
            peer = get_peer_valuation(stock_code, industry)
        except Exception as e:  # noqa: BLE001
            print(f"[valuation] 同业估值获取失败（方向性降级）: {repr(e)[:80]}")
        if peer and (peer.get("peerMedianPe") or peer.get("marketMedianPe")):
            relative = {
                "mode": "peer_median", "degraded": True,
                "degradeReason": "历史 PE/PB 分位样本不足或不可用",
                "current": {"peTtm": basic_out["peTtm"], "pb": basic_out["pb"]},
                "percentile": None,
                "peerMedian": peer,
                "comment": peer.get("note", "改以同业/市场中位数横向参照"),
            }
        else:
            relative = {
                "mode": "directional", "degraded": True,
                "degradeReason": "历史分位与同业/市场中位数本次均不可用",
                "current": {"peTtm": basic_out["peTtm"], "pb": basic_out["pb"]},
                "percentile": None, "peerMedian": None,
                "comment": "本次仅披露当前 PE/PB 绝对水平，估值分位/同业数据暂缺，结论为方向性、请谨慎",
            }

    # ============================================================
    # DCF 适用性提示（高估值成长股的保守 DCF 参考性有限，如实标注）
    # ============================================================
    if dcf.get("available"):
        up_n = ((dcf.get("scenarios") or {}).get("neutral") or {}).get("upsidePct")
        pe_p_pct = (((relative.get("percentile") or {}).get("pe")) or {}).get("pct")
        high_dev = up_n is not None and abs(up_n) > 60
        high_pct = pe_p_pct is not None and pe_p_pct > 0.90
        if high_dev or high_pct:
            why = []
            if high_dev:
                why.append(f"DCF 内在价值较现价偏离 {up_n:+.0f}%")
            if high_pct:
                why.append(f"PE 处于历史 {pe_p_pct*100:.0f}% 高分位")
            dcf["applicabilityNote"] = (
                "⚠ 适用性提示：该股当前定价主要由成长预期驱动（" + "；".join(why) +
                "），保守 DCF（增速回落下限、10% 折现）对此类标的参考性有限，"
                "内在价值区间大概率显著低估市场定价逻辑；请以相对估值分位与基本面质量为主要参照。"
            )

    # ============================================================
    # 目标价区间（bands，模型假设非买卖点位）
    # ============================================================
    bands = {"basedOn": "目标区间为模型假设，非买卖点位", "note": ""}
    iv_n = (dcf.get("scenarios", {}).get("neutral") or {}).get("intrinsicPs") if dcf.get("available") else None
    iv_o = (dcf.get("scenarios", {}).get("optimistic") or {}).get("intrinsicPs") if dcf.get("available") else None
    iv_p = (dcf.get("scenarios", {}).get("pessimistic") or {}).get("intrinsicPs") if dcf.get("available") else None
    if iv_n and price:
        center = iv_n
        buy_below = min(iv_p if iv_p else center, center * (1 - SAFETY_BUFFER))
        sell_above = max(iv_o if iv_o else center, center * (1 + SAFETY_BUFFER))
        bands = {
            "intrinsicCenter": round(center, 2),
            "buyBelow": round(buy_below, 2),
            "hold": [round(buy_below, 2), round(sell_above, 2)],
            "sellAbove": round(sell_above, 2),
            "basedOn": f"中性内在价值 {center:.2f} ± 安全垫 {SAFETY_BUFFER:.0%}；区间为模型假设非买卖点位",
            "note": "",
        }
    else:
        # DCF 不可用：基于现价与相对估值做方向性区间
        bands = {
            "intrinsicCenter": None, "buyBelow": None,
            "hold": None, "sellAbove": None,
            "basedOn": "DCF 本次不可用（缺 FCF/股本或 FCF 为负），不输出量化区间，仅作方向性提示",
            "note": "重资产扩张期 FCF 为负或数据缺失，估值请结合相对估值与基本面判断",
        }

    # ============================================================
    # 决策矩阵（matrix）
    # ============================================================
    latest_ki = kis[0] if kis else {}
    debt = _f(latest_ki.get("assetLiabRatio"))
    np_yoy = _f(latest_ki.get("netProfitAtsopcYoy"))
    rev_yoy = _f(latest_ki.get("revenueYoy"))
    fcf_positive_now = any((r or {}).get("fcf") is not None and r["fcf"] > 0 for r in cf_rows[:2]) if cf_rows else None

    # 维度打分（1/2/3）
    def _resolve_roe():
        """ROE 取最近年报期（累计口径的季/半年报未年化，直接用会系统性低估）。"""
        for k in kis:
            d = str(k.get("reportDate") or "")
            v = _f(k.get("wgtAvgRoe"))
            if d.endswith("-12-31") and v is not None:
                return v, f"{d} 年报"
        # 无年报期：最新期年化近似（Q1×4 / H1×2，同花顺 ROE 为累计加权口径）
        k0 = kis[0] if kis else {}
        v = _f(k0.get("wgtAvgRoe"))
        if v is None:
            return None, ""
        d = str(k0.get("reportDate") or "")
        if d.endswith("-03-31"):
            return v * 4, f"{d} 一季报×4 年化近似"
        if d.endswith("-06-30"):
            return v * 2, f"{d} 半年报×2 年化近似"
        return v, d

    def _moat_score():
        roe_v, roe_basis = _resolve_roe()
        if roe_v is None:
            return 2, ["ROE 数据缺失，中性"]
        if roe_v >= 15:
            return 3, [f"加权 ROE {roe_v:.1f}% ≥ 15%（{roe_basis}，盈利质量佳）"]
        if roe_v >= 8:
            return 2, [f"加权 ROE {roe_v:.1f}%（{roe_basis}，8-15% 一般）"]
        return 1, [f"加权 ROE {roe_v:.1f}% < 8%（{roe_basis}，盈利质量弱）"]

    def _fin_score():
        ev = []
        sc = 2
        if debt is not None:
            ev.append(f"资产负债率 {debt:.1f}%")
            sc = 3 if debt < 60 else (2 if debt < 75 else 1)
        if fcf_positive_now is True:
            ev.append("近两期存在正自由现金流")
            sc = max(sc, 2)
        elif fcf_positive_now is False:
            ev.append("近两期自由现金流为负")
            sc = min(sc, 2)
        return sc, ev

    def _growth_score():
        if rev_yoy is None and np_yoy is None:
            return 2, ["增速数据缺失，中性"]
        vals = [v for v in (rev_yoy, np_yoy) if v is not None]
        avg = sum(vals) / len(vals)
        ev = [f"营收/净利同比均值 {avg:+.1f}%"]
        if avg > 20:
            return 3, ev
        if avg >= 0:
            return 2, ev
        return 1, ev

    def _val_score():
        # 现价相对 bands/内在价值
        if iv_n and price:
            if price <= (bands.get("buyBelow") or 0):
                return 3, [f"现价 {price} 低于买入区（模型假设 {bands['buyBelow']}）"]
            if price >= (bands.get("sellAbove") or 1e18):
                return 1, [f"现价 {price} 高于卖出区（模型假设 {bands['sellAbove']}）"]
            return 2, [f"现价 {price} 处于持有区"]
        if pe_ttm and pb:
            return 2, [f"PE {pe_ttm:.1f} / PB {pb:.2f}（DCF 缺失，参考相对水平）"]
        return 2, ["估值分位/DCF 本次不可用，中性"]

    def _policy_score():
        if policy_abs:
            return 2, ["未开通深知检索，未纳入政策维度"]
        if bull > bear:
            return 3, [f"政策净利好（利好 {bull} / 利空 {bear}）"]
        if bear > bull:
            return 1, [f"政策净利空（利空 {bear} / 利好 {bull}）"]
        return 2, [f"政策方向均衡（利好 {bull} / 利空 {bear}）"]

    moat_s, moat_ev = _moat_score()
    fin_s, fin_ev = _fin_score()
    grow_s, grow_ev = _growth_score()
    val_s, val_ev = _val_score()
    pol_s, pol_ev = _policy_score()

    dims = [
        {"key": "moat", "label": "护城河（盈利质量）", "weight": 0.25, "score": moat_s,
         "tone": _score_label(moat_s), "evidence": moat_ev},
        {"key": "financial", "label": "财务健康", "weight": 0.20, "score": fin_s,
         "tone": _score_label(fin_s), "evidence": fin_ev},
        {"key": "growth", "label": "成长性", "weight": 0.15, "score": grow_s,
         "tone": _score_label(grow_s), "evidence": grow_ev},
        {"key": "valuation", "label": "估值吸引力", "weight": 0.30, "score": val_s,
         "tone": _score_label(val_s), "evidence": val_ev},
        {"key": "policy", "label": "政策方向", "weight": 0.10, "score": pol_s,
         "tone": _score_label(pol_s), "evidence": pol_ev},
    ]
    policy_weight_zero = policy_abs
    if policy_weight_zero:
        dims = [d for d in dims if d["key"] != "policy"]
        total_w = sum(d["weight"] for d in dims)
        dims = [{**d, "weight": d["weight"] / total_w} for d in dims]

    score = sum(d["weight"] * (d["score"] - 1) / 2 for d in dims)  # 0~1（1分=0，3分=1）
    if score >= 0.8:
        action, action_label = "buy_watch", "买入关注"
    elif score >= 0.6:
        action, action_label = "hold_watch", "持有观望"
    else:
        action, action_label = "avoid", "回避"

    action_note = ""
    if action == "buy_watch":
        action_note = "关注前提：待政策/财务跟踪指标验证、且现价回调进入买入区后再评估，非现在即买。"
    elif action == "hold_watch":
        action_note = "持有观望：关注财务拐点、政策时间表与估值分位变化，再决定增减。"
    else:
        action_note = "当前基本面/估值不具吸引力，规避或等待更好的安全边际。非机械买卖指令。"

    matrix = {
        "dimensions": dims,
        "score": round(score, 2),
        "action": action, "actionLabel": action_label, "actionNote": action_note,
        "policyWeightZero": policy_weight_zero,
        "rulesNote": "动作建议由既定规则模型生成，仅作研究参考，非投资建议；请结合持牌机构研究独立决策。",
    }

    # ============================================================
    return {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "method": "dcf_relative_dual",
        "basic": basic_out,
        "dcf": dcf,
        "relative": relative,
        "bands": bands,
        "matrix": matrix,
        "disclaimer": DISCLAIMER,
        "dataFlags": {
            "dcfDegraded": not dcf.get("available", False),
            "relativeDegraded": relative.get("degraded", False),
            "policyAbsent": policy_abs,
        },
    }


# ============================================================
# 自测
# ============================================================

def _mock_company(fcf_ok=True, with_policy=True) -> Dict[str, Any]:
    """构造内联 mock company_data 供自测（无需网络/Key）。"""
    return {
        "stockCode": "600519",
        "basicInfo": {
            "secName": "贵州茅台", "industryName": "白酒", "resolvedIndustry": "白酒",
            "totalShare": 12.56e8, "totalMarketCap": 1.8e12,
        },
        "keyIndicators": [
            {"reportDate": "2025-12-31", "totalRevenue": 1500e8, "netProfitAtsopc": 750e8,
             "netProfitAfterNrgalAtsolc": 748e8, "basicEps": 59.0,
             "wgtAvgRoe": 30.0, "assetLiabRatio": 20.0, "revenueYoy": 15.0, "netProfitAtsopcYoy": 16.0},
            {"reportDate": "2024-12-31", "totalRevenue": 1300e8, "netProfitAtsopc": 640e8,
             "netProfitAfterNrgalAtsolc": 638e8, "basicEps": 51.0,
             "wgtAvgRoe": 30.0, "assetLiabRatio": 19.0, "revenueYoy": 12.0, "netProfitAtsopcYoy": 13.0},
            {"reportDate": "2023-12-31", "totalRevenue": 1160e8, "netProfitAtsopc": 560e8,
             "netProfitAfterNrgalAtsolc": 558e8, "basicEps": 45.0,
             "wgtAvgRoe": 29.0, "assetLiabRatio": 18.0, "revenueYoy": 10.0, "netProfitAtsopcYoy": 11.0},
        ],
        "industryRanks": {},
        "dataSource": "mock",
    }


def _patch_offline(monkey: Dict[str, Any], code: str) -> Dict[str, Any]:
    """把 akshare_api 的数据函数替换为离线假数据，供 --mock 免网络自测。

    对 generate_valuation_data 内 'from akshare_api import ...' 生效：
    因该 import 发生在函数体内，此处替换 akshare_api 模块属性即可。
    """
    import akshare_api
    akshare_api.get_cash_flow = monkey["cash"]          # type: ignore[assignment]
    akshare_api.get_valuation_history = monkey["hist"]  # type: ignore[assignment]
    akshare_api.get_peer_valuation = monkey["peer"]     # type: ignore[assignment]
    return akshare_api


def _offline_hist(code: str, max_days: int = 1500):
    """构造离线历史估值（约 700 个交易日，PE 在 10~40 间波动）。"""
    import math
    series = []
    for i in range(700):
        base = 20 + 10 * math.sin(i / 60)
        series.append({"date": f"20{20+i//300:02d}-{(i % 12)+1:02d}-{(i % 28)+1:02d}",
                       "close": 100.0, "peTtm": max(5.0, base), "pb": 3.0})
    return {
        "series": series,
        "latest": {"date": "2026-09-02", "close": 110.0, "peTtm": 22.0, "pb": 3.2,
                   "marketCap": 1.0e12, "totalShare": 90.0e8},
        "historyDays": len(series),
        "sourceNote": "离线 mock",
    }


def _main() -> None:
    import argparse
    import json as _json
    p = argparse.ArgumentParser(description="估值整合自测")
    p.add_argument("--mock", action="store_true", help="内联 mock 离线跑通（无需网络/Key）")
    p.add_argument("--mock-scenarios", action="store_true",
                   help="mock 三种退化场景：FCF负 / 无历史 / 无政策")
    p.add_argument("--live", metavar="CODE", help="真调 akshare 跑某 A 股")
    p.add_argument("--force-degrade", action="store_true", help="真机强制降级历史估值（验证不伪造）")
    args = p.parse_args()

    if args.mock_scenarios:
        import akshare_api
        # 场景A：FCF 全为负 → dcf.available=False
        print("== 场景A：FCF 为负（DCF 降级）==")
        akshare_api.get_cash_flow = lambda *a, **k: [
            {"reportDate": "2025-12-31", "ocf": 1.0e8, "capex": 3.0e8, "fcf": -2.0e8},
            {"reportDate": "2024-12-31", "ocf": 1.0e8, "capex": 2.5e8, "fcf": -1.5e8},
            {"reportDate": "2023-12-31", "ocf": 0.9e8, "capex": 2.0e8, "fcf": -1.1e8},
        ]
        akshare_api.get_valuation_history = _offline_hist
        r = generate_valuation_data("600519", _mock_company(), None)
        print("  dcf.available:", r["dcf"]["available"], "| flags:", r["dataFlags"],
              "| matrix:", r["matrix"]["actionLabel"])

        # 场景B：历史估值不可用 → 走 peer_median/directional
        print("== 场景B：历史估值缺失（相对估值降级）==")
        akshare_api.get_cash_flow = lambda *a, **k: [
            {"reportDate": "2025-12-31", "ocf": 5.0e8, "capex": 1.0e8, "fcf": 4.0e8},
            {"reportDate": "2024-12-31", "ocf": 4.5e8, "capex": 1.0e8, "fcf": 3.5e8},
            {"reportDate": "2023-12-31", "ocf": 4.0e8, "capex": 1.0e8, "fcf": 3.0e8},
        ]
        akshare_api.get_valuation_history = lambda *a, **k: None
        akshare_api.get_peer_valuation = lambda *a, **k: {
            "industryName": "白酒", "peerMedianPe": 25.0, "peerMedianPb": 6.0,
            "marketMedianPe": 30.0, "marketMedianPb": 4.0,
            "currentPe": 22.0, "currentPb": 3.2, "sampleN": 10,
            "note": "历史 PE/PB 分位暂缺，改以同业/市场中位数横向参照（弹性降级）"}
        r = generate_valuation_data("600519", _mock_company(), None)
        print("  relative.mode:", r["relative"]["mode"], "| degraded:", r["relative"]["degraded"])
        return

    if args.force_degrade:
        import akshare_api
        akshare_api.get_valuation_history = lambda *a, **k: None  # type: ignore[assignment]

    if args.live:
        from akshare_api import get_company_research_data
        cd = get_company_research_data(args.live)
        res = generate_valuation_data(args.live, cd, None)
    else:
        # 离线 mock：注入假数据函数
        _patch_offline({
            "cash": lambda *a, **k: [
                {"reportDate": "2025-12-31", "ocf": 600.0e8, "capex": 20.0e8, "fcf": 580.0e8},
                {"reportDate": "2024-12-31", "ocf": 520.0e8, "capex": 18.0e8, "fcf": 502.0e8},
                {"reportDate": "2023-12-31", "ocf": 470.0e8, "capex": 15.0e8, "fcf": 455.0e8},
            ],
            "hist": _offline_hist,
            "peer": lambda *a, **k: None,
        }, "600519")
        res = generate_valuation_data("600519", _mock_company(), None)

    print(_json.dumps(res, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    _main()
