#!/usr/bin/env python3
"""开源金融数据层（akshare）——深知可信投研的公开渠道数据源

设计目标：
- 数据真实性：与上市公司法定披露一致的公开数据（同花顺 F10 / 东财 F10），
  营收/归母净利差异 ≤0.01%，加权 ROE / 资产负债率与法定披露一致。
- 稳定性：同花顺源为主源（实测 3/3 稳定、平均 0.6s），东财源为备源 +
  重试（东财限流敏感，间歇 ProxyError）。
- 免 Key、免版权：akshare 为 MIT 开源库，无需任何 API Key，可公开分发。

输出结构（basicInfo / industryRanks / keyIndicators 三段）供下游
impact_analysis / format_report / render_html 使用。

依赖：akshare。首次运行自动检测，并始终通过当前解释器安装，避免 pip/Python 串环境。
"""

import importlib
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

try:
    import akshare as ak
except ImportError:
    ak = None


RETRY_TIMES = 2          # 备源/重试次数
RETRY_INTERVAL = 3       # 重试间隔（秒）

# 国内财经数据域名：即使系统开了 Clash/V2Ray/Surge 等代理，也强制直连。
# 这些是大陆境内站点，直连最快最稳；若被转发到海外代理节点会 ProxyError/超时。
# no_proxy 中 "eastmoney.com" 可匹配其全部子域名（push2/datacenter/quote 等）。
_DIRECT_NO_PROXY = ("eastmoney.com", "10jqka.com.cn", "cninfo.com.cn")


def ensure_direct_finance_domains() -> None:
    """把国内财经域名追加进 no_proxy，强制 akshare 请求绕过系统代理直连。"""
    existing = os.environ.get("no_proxy") or os.environ.get("NO_PROXY") or ""
    parts = [p.strip() for p in existing.split(",") if p.strip()]
    for d in _DIRECT_NO_PROXY:
        if d not in parts:
            parts.append(d)
    merged = ",".join(parts)
    os.environ["no_proxy"] = merged
    os.environ["NO_PROXY"] = merged


# requests/urllib 每次请求都会重新读取 no_proxy，模块加载时设置一次即可
# 覆盖 akshare 内部所有对东财/同花顺/巨潮的访问。
ensure_direct_finance_domains()


def akshare_status() -> Dict[str, Any]:
    """返回当前解释器中的 akshare 状态，供 Agent 机器判断，禁止用其他 pip list 猜测。"""
    return {
        "ready": ak is not None,
        "python": sys.executable,
        "akshare_version": getattr(ak, "__version__", None) if ak is not None else None,
        "akshare_path": getattr(ak, "__file__", None) if ak is not None else None,
        "install_command": [sys.executable, "-m", "pip", "install", "-U", "akshare"],
    }


def ensure_akshare(auto_install: bool = False) -> None:
    """确保 akshare 可用；安装与运行始终绑定当前 Python 解释器。"""
    global ak
    if ak is not None:
        return

    install_command = [sys.executable, "-m", "pip", "install", "-U", "akshare"]
    if auto_install:
        print(f"[runtime] 当前 Python: {sys.executable}", file=sys.stderr)
        print(
            "[runtime] 未检测到 akshare，首次运行安装一次；完成后不再重复安装。",
            file=sys.stderr,
        )
        try:
            result = subprocess.run(
                install_command,
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
            importlib.invalidate_caches()
            ak = importlib.import_module("akshare")
            print(
                f"[runtime] akshare {getattr(ak, '__version__', 'unknown')} 安装成功",
                file=sys.stderr,
            )
            return
        except Exception as exc:  # noqa: BLE001
            detail = ""
            if isinstance(exc, subprocess.CalledProcessError):
                detail = (exc.stderr or exc.stdout or "").strip()[-2000:]
            elif isinstance(exc, subprocess.TimeoutExpired):
                detail = "安装超过 300 秒，已终止"
            raise SystemExit(
                "[akshare_api] akshare 自动安装失败。请不要改用其他 pip 重复安装；\n"
                f"请原样运行：{json.dumps(install_command, ensure_ascii=False)}\n"
                f"当前 Python：{sys.executable}\n"
                f"错误：{detail or exc}"
            ) from exc

    command_text = " ".join(f'"{part}"' if " " in part else part for part in install_command)
    raise SystemExit(
        "[akshare_api] 当前 Python 缺少 akshare。请只安装一次，并使用同一解释器运行：\n"
        f"  {command_text}\n"
        f"当前 Python：{sys.executable}\n"
        "不要使用普通 pip、pip3 或其他 Python 的 pip list 判断安装状态。"
    )


# ============================================================
# 通用：金额字符串解析（同花顺源返回 '1502.25亿' / '3000.48万'）
# ============================================================

def parse_amount(value: Any) -> Optional[float]:
    """'1502.25亿' -> 1502.25（亿元）；'3000.48万' -> 0.300048（亿元）"""
    if value is None:
        return None
    s = str(value).replace(",", "").strip()
    if s in ("False", "None", "", "--", "nan", "-"):
        return None
    try:
        if s.endswith("亿"):
            return float(s[:-1])
        if s.endswith("万"):
            return float(s[:-1]) / 10000
        return float(s)
    except ValueError:
        return None


def parse_ratio(value: Any) -> Optional[float]:
    """'271.46%' -> 271.46；数值直通"""
    if value is None:
        return None
    s = str(value).replace(",", "").replace("%", "").strip()
    if s in ("False", "None", "", "--", "nan", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _with_retry(fn, *args, **kwargs):
    """带重试的调用：失败时等待后重试，共 RETRY_TIMES+1 次"""
    last_err = None
    for attempt in range(RETRY_TIMES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 接口异常统一重试
            last_err = e
            if attempt < RETRY_TIMES:
                time.sleep(RETRY_INTERVAL)
    raise last_err  # type: ignore[misc]


# ============================================================
# 1. 股票代码查询（名称 -> 代码）
# ============================================================

def lookup_stock(keyword: str) -> Optional[Dict[str, Any]]:
    """按名称/拼音/代码模糊查询股票

    优先用东财 suggest 轻量接口（标准 urllib、网络环境宽容），
    失败时降级 akshare 交易所列表。

    Returns:
        {symbol, symbolName} 或 None
    """
    # 纯 6 位数字直接返回
    if re.fullmatch(r"\d{6}", keyword or ""):
        return {"symbol": keyword, "symbolName": keyword}

    # 源1：东财 suggest（轻量 GET，稳定）
    try:
        import json as _json
        import urllib.parse as _up
        import urllib.request as _ur
        url = ("https://searchapi.eastmoney.com/api/suggest/get?input="
               + _up.quote(keyword or "") + "&type=14&count=5"
               "&token=D43BF722C8E33BDC906FB84D85E326E8")
        req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _ur.urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        items = ((data.get("QuotationCodeTable") or {}).get("Data")) or []
        for it in items:
            # A 股筛选：Classify=AStock（东财统一标识）；SecurityType 各市场取值不一，
            # 再用 6 位纯数字代码兜底校验
            code = str(it.get("Code") or "")
            if str(it.get("Classify")) == "AStock" and re.fullmatch(r"\d{6}", code):
                return {"symbol": code, "symbolName": str(it.get("Name"))}
    except Exception as e:  # noqa: BLE001
        print(f"[akshare_api] suggest 接口失败（降级列表源）: {repr(e)[:60]}")

    ensure_akshare()
    frames = []
    # 多源降级：全市场列表 → 深交所 → 上交所（后者实测更稳）
    try:
        frames.append(_with_retry(ak.stock_info_a_code_name))
    except Exception as e:  # noqa: BLE001
        print(f"[akshare_api] 全市场列表失败（降级交易所源）: {repr(e)[:60]}")
    if not frames:
        try:
            sz = _with_retry(ak.stock_info_sz_name_code)
            sz = sz.rename(columns={"A股代码": "code", "A股简称": "name"})
            frames.append(sz[["code", "name"]])
            sh = _with_retry(ak.stock_info_sh_name_code)
            sh = sh.rename(columns={"证券代码": "code", "证券简称": "name"})
            frames.append(sh[["code", "name"]])
        except Exception as e:  # noqa: BLE001
            print(f"[akshare_api] 交易所列表获取失败: {repr(e)[:60]}")
            return None

    import pandas as pd
    if frames:
        df = pd.concat(frames, ignore_index=True)
        # 列名兼容：新版 code/name，旧版 代码/名称
        code_col = "code" if "code" in df.columns else "代码"
        name_col = "name" if "name" in df.columns else "名称"

        kw = (keyword or "").strip()
        exact = df[df[name_col] == kw]
        if not exact.empty:
            row = exact.iloc[0]
            return {"symbol": row[code_col], "symbolName": row[name_col]}
        contains = df[df[name_col].str.contains(kw, na=False)]
        if not contains.empty:
            row = contains.iloc[0]
            return {"symbol": row[code_col], "symbolName": row[name_col]}

    # 最终兜底：东财搜索接口（轻量、对网络环境宽容）
    try:
        se = _with_retry(ak.stock_zh_a_spot_em)
        # spot 全量较大，仅在列表源全部失败时使用
        kw = (keyword or "").strip()
        name_col = "名称"
        hit = se[se[name_col] == kw]
        if not hit.empty:
            row = hit.iloc[0]
            return {"symbol": str(row["代码"]), "symbolName": row["名称"]}
    except Exception as e:  # noqa: BLE001
        print(f"[akshare_api] 名称解析兜底失败: {repr(e)[:60]}")
    return None


# 行业字段缺失时的检索兜底映射：证券简称关键词 -> 行业检索词
# （东财/巨潮降级时保证深知检索仍用行业词而非公司名）
_INDUSTRY_FALLBACK_HINTS = (
    ("比亚迪", "新能源汽车"), ("宁德时代", "动力电池"), ("隆基绿能", "光伏"),
    ("阳光电源", "光伏"), ("中国巨石", "玻璃纤维"), ("长江电力", "电力"),
    ("贵州茅台", "白酒"), ("五粮液", "白酒"), ("伊利股份", "乳制品"),
    ("迈瑞医疗", "医疗器械"), ("恒瑞医药", "创新药"), ("中芯国际", "半导体"),
    ("北方华创", "半导体设备"), ("万华化学", "化工"), ("宝钢股份", "钢铁"),
)


def _industry_fallback(name: str) -> str:
    for key, ind in _INDUSTRY_FALLBACK_HINTS:
        if key in (name or ""):
            return ind
    return ""

def get_basic_info(stock_code: str, fallback_name: str = "") -> Optional[Dict[str, Any]]:
    """获取公司基本资料（多源降级：东财轻量个股信息 → 巨潮概况 → 证券列表兜底）

    注意：不用 akshare 的 stock_individual_info_em——它会把几百个字段拼成超长
    query string，被东财风控直接 reset（RemoteDisconnected，直连/代理均复现）。
    这里用东财 stock/get 的简短 fields 轻量请求，0.2s 返回且稳定。

    Returns:
        {secName, secCode, industryName, totalMarketCap, listedDate, ...}
    """
    ensure_akshare()
    info: Dict[str, Any] = {"secCode": stock_code}

    # 源1：东财轻量个股信息（简短 fields 规避超长URL风控；多域名降级规避限流）
    for host in ("push2delay.eastmoney.com", "push2.eastmoney.com"):
        try:
            import urllib.parse as _up
            import urllib.request as _ur
            market_code = 1 if str(stock_code).startswith("6") else 0
            # 仅取必要字段：f57代码 f58名称 f84总股本 f85流通股 f116总市值 f117流通市值 f127行业 f189上市时间
            fields = "f57,f58,f84,f85,f116,f117,f127,f189"
            url = (f"https://{host}/api/qt/stock/get?fltt=2&invt=2"
                   f"&secid={market_code}.{_up.quote(str(stock_code))}"
                   f"&fields={_up.quote(fields)}")
            req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _ur.urlopen(req, timeout=12) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            data = payload.get("data") or {}
            d = {
                "股票简称": data.get("f58"),
                "行业": data.get("f127"),
                "总市值": data.get("f116"),          # 元
                "流通市值": data.get("f117"),        # 元
                "上市时间": data.get("f189"),
            }
            info.update({
                "secName": d.get("股票简称"),
                "industryName": d.get("行业"),
                "totalMarketCap": d.get("总市值"),
                "circulatedMarketCap": d.get("流通市值"),
                "listedDate": d.get("上市时间"),
            })
            if info.get("secName"):
                break
        except Exception as e:  # noqa: BLE001
            print(f"[akshare_api] 东财 {host} 个股信息失败（换域名）: {repr(e)[:70]}")

    # 源2：巨潮公司概况（全称/简介/经营范围；较稳定）
    try:
        profile = _with_retry(ak.stock_profile_cninfo, symbol=stock_code)
        if profile is not None and not profile.empty:
            row = profile.iloc[0]
            info["orgName"] = row.get("公司名称") or info.get("orgName")
            intro = (row.get("公司简介") or "").strip()
            scope = (row.get("经营范围") or "").strip()
            if intro or scope:
                info["mainOprBus"] = (intro or scope)[:300]
    except Exception as e:  # noqa: BLE001
        print(f"[akshare_api] 公司概况获取失败（降级）: {repr(e)[:80]}")

    # 兜底：证券列表回填名称（保证 basicInfo 至少有 secName/secCode）
    if not info.get("secName") and fallback_name:
        info["secName"] = fallback_name

    if not info.get("secName"):
        return None
    return info


# ============================================================
# 3. 关键财务指标
# ============================================================

# 同花顺财务摘要列 -> 内部关键指标字段
_THS_FIELD_MAP = {
    "营业总收入": "totalRevenue",
    "净利润": "netProfitAtsopc",
    "扣非净利润": "netProfitAfterNrgalAtsolc",
    "基本每股收益": "basicEps",
}
_THS_YOY_MAP = {
    "营业总收入同比增长率": "revenueYoy",
    "净利润同比增长率": "netProfitAtsopcYoy",
}


def get_key_indicators(stock_code: str, periods: int = 5) -> List[Dict[str, Any]]:
    """获取关键财务指标（近 N 期，累计口径）

    主源：同花顺财务摘要（营收/净利/扣非/EPS + 同比）
    补充：东财财务指标（加权 ROE / 资产负债率）

    Returns:
        [{reportDate, totalRevenue(元), netProfitAtsopc(元), basicEps,
          wgtAvgRoe, assetLiabRatio, revenueYoy, netProfitAtsopcYoy}, ...]
        按报告期降序（最新在前）
    """
    ensure_akshare()
    rows: List[Dict[str, Any]] = []

    # 主源：同花顺财务摘要（按报告期 = 累计口径）
    try:
        df = _with_retry(ak.stock_financial_abstract_ths, symbol=stock_code,
                         indicator="按报告期")
        df = df.tail(periods + 6).iloc[::-1]  # 多取几期便于同期匹配，降序
        for _, r in df.iterrows():
            rows.append({
                "reportDate": str(r.get("报告期"))[:10],
                **{dst: parse_amount(r.get(src)) for src, dst in _THS_FIELD_MAP.items()},
                **{dst: parse_ratio(r.get(src)) for src, dst in _THS_YOY_MAP.items()},
            })
    except Exception as e:  # noqa: BLE001
        print(f"[akshare_api] 财务摘要获取失败: {e}")

    # 亿 -> 元（口径：内部字段单位为元）
    for row in rows:
        for k in ("totalRevenue", "netProfitAtsopc", "netProfitAfterNrgalAtsolc"):
            v = row.get(k)
            if v is not None:
                row[k] = v * 1e8

    # 补充：东财财务指标（加权 ROE / 资产负债率），按报告期对齐
    if rows:
        start_year = rows[-1]["reportDate"][:4] if rows else "2024"
        try:
            fi = _with_retry(ak.stock_financial_analysis_indicator,
                             symbol=stock_code, start_year=start_year)
            roe_col = next((c for c in fi.columns if "加权净资产收益率" in str(c)), None)
            zcfzl_col = next((c for c in fi.columns if "资产负债率" in str(c)), None)
            date_col = next((c for c in fi.columns if "日期" in str(c) or "报告" in str(c)), None)
            if date_col:
                idx = {}
                for _, r in fi.iterrows():
                    idx[str(r.get(date_col))[:10]] = (
                        parse_ratio(r.get(roe_col)) if roe_col else None,
                        parse_ratio(r.get(zcfzl_col)) if zcfzl_col else None,
                    )
                for row in rows:
                    hit = idx.get(row["reportDate"])
                    if hit:
                        row["wgtAvgRoe"] = hit[0]
                        row["assetLiabRatio"] = hit[1]
        except Exception as e:  # noqa: BLE001
            print(f"[akshare_api] 财务指标补充失败（降级为空）: {e}")

    return rows[:periods]


# ============================================================
# 4. 行业排名（简化版：同花顺行业成分 + 本公司定位）
# ============================================================

def get_industry_rank(stock_code: str, industry_name: Optional[str] = None,
                      metric: str = "jzcsyl") -> Optional[Dict[str, Any]]:
    """行业相对位置（简化实现）

    akshare 无现成"行业内指标排名"接口；本实现取同花顺行业板块成分，
    按流通市值给出公司在其行业中的规模位次作为参考定位。
    指标排名（ROE/PE 等）标记为"公开渠道暂不提供"。

    Returns:
        {industryName, industryRank, industryAvg, industryList, metric}
    """
    ensure_akshare()
    if not industry_name:
        return None
    try:
        df = _with_retry(ak.stock_board_industry_cons_ths, symbol=industry_name)
    except Exception as e:  # noqa: BLE001
        print(f"[akshare_api] 行业成分获取失败（板块降级处理）: {e}")
        return None

    industry_list = []
    target_rank = None
    # 板块成分一般按市值降序返回
    for i, (_, r) in enumerate(df.iterrows(), 1):
        code = str(r.get("代码") or r.get("股票代码") or "")
        name = str(r.get("名称") or r.get("股票名称") or "")
        value = parse_amount(r.get("总市值") or r.get("流通市值"))
        if code == stock_code:
            target_rank = i
        industry_list.append({"secName": name, "secCode": code, "rank": i, "value": value})

    if not industry_list:
        return None

    vals = [x["value"] for x in industry_list if x["value"]]
    industry_avg = sum(vals) / len(vals) if vals else None

    metric_label = {"jzcsyl": "ROE", "pe": "PE", "pb": "PB", "zsz": "总市值"}.get(metric, metric)
    return {
        "industryName": industry_name,
        "metric": metric,
        "rankBasis": f"板块成分规模位次（{metric_label} 指标排名公开渠道暂不提供）",
        "industryRank": f"{target_rank}/{len(industry_list)}" if target_rank else "未在成分列表中",
        "industryAvg": f"{industry_avg:.1f} 亿" if industry_avg else "N/A",
        "reportDate": "实时",
        "industryList": industry_list,
    }


# ============================================================
# 5. 组合入口
# ============================================================

def get_company_research_data(stock_code: str,
                              industry_name: Optional[str] = None,
                              fallback_name: str = "") -> Dict[str, Any]:
    """获取完整公司研究数据（组合调用，供 run_research 使用）

    Args:
        stock_code: 6 位代码
        industry_name: 行业名覆盖（basicInfo 行业缺失时的兜底由调用方传入）
        fallback_name: 证券简称兜底

    Returns:
        {stockCode, basicInfo, keyIndicators, industryRanks, dataSource}
    """
    print(f"[akshare_api] 获取公司数据: {stock_code}")
    basic = get_basic_info(stock_code, fallback_name=fallback_name)
    name_for_hint = (basic or {}).get("secName") or fallback_name
    industry = (
        industry_name
        or (basic or {}).get("industryName")
        or _industry_fallback(name_for_hint)
    )

    kis = get_key_indicators(stock_code)

    industry_ranks: Dict[str, Any] = {}
    if industry:
        rank = get_industry_rank(stock_code, industry)
        if rank:
            industry_ranks["zsz"] = rank

    # resolvedIndustry：含兜底映射后的行业名（供深知检索使用，避免用公司名检索）
    basic = dict(basic or {})
    basic["resolvedIndustry"] = industry

    return {
        "stockCode": stock_code,
        "basicInfo": basic,
        "keyIndicators": kis,
        "industryRanks": industry_ranks,
        "dataSource": "akshare（同花顺/东财公开披露数据）",
    }


if __name__ == "__main__":
    ensure_akshare()
    data = get_company_research_data("002594")
    b = data["basicInfo"] or {}
    print(f"公司: {b.get('secName')} | 行业: {b.get('industryName')} | 数据源: {data['dataSource']}")
    print(f"财务期数: {len(data['keyIndicators'])}")
    for k in data["keyIndicators"][:3]:
        print(f"  {k['reportDate']}: 营收 {parse_amount(k.get('totalRevenue'))} | "
              f"ROE {k.get('wgtAvgRoe')} | 负债率 {k.get('assetLiabRatio')}")
