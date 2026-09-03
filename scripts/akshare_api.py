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
                "总股本": data.get("f84"),           # 股
                "上市时间": data.get("f189"),
            }
            info.update({
                "secName": d.get("股票简称"),
                "industryName": d.get("行业"),
                "totalMarketCap": d.get("总市值"),
                "circulatedMarketCap": d.get("流通市值"),
                "totalShare": d.get("总股本"),
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

# ============================================================
# 4.5 行业板块成分（东财轻量自实现）
#     akshare ≥1.18.9 已移除 stock_board_industry_cons_ths；
#     其 EM 版 stock_board_industry_cons_em 走 29.push2 大分页端点，
#     实测易被东财风控断连（RemoteDisconnected）。
#     这里复用 get_basic_info 验证过的 push2delay 轻量模式自行分页拉取。
# ============================================================

_EM_BOARD_CACHE: Optional[List[Dict[str, str]]] = None  # [{name, code}...] 模块级缓存


def _em_clist(fs: str, fields: str, max_pages: int = 6) -> List[Dict[str, Any]]:
    """东财 clist 轻量分页拉取（push2delay 优先，100 条/页）。"""
    import urllib.parse as _up
    import urllib.request as _ur

    out: List[Dict[str, Any]] = []
    for host in ("push2delay.eastmoney.com", "push2.eastmoney.com"):
        for pn in range(1, max_pages + 1):
            url = (f"https://{host}/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1"
                   f"&fltt=2&invt=2&fid=f20&fs={_up.quote(fs)}&fields={_up.quote(fields)}")
            try:
                req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with _ur.urlopen(req, timeout=12) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                data = payload.get("data") or {}
                diff = data.get("diff") or []
                if not diff:
                    return out
                out.extend(diff)
                if len(out) >= int(data.get("total") or 0):
                    return out
            except Exception as e:  # noqa: BLE001
                print(f"[akshare_api] 东财 clist {host} 第{pn}页失败（换源/降级）: {repr(e)[:70]}")
                break
        if out:
            return out
    return out


def _em_board_map() -> List[Dict[str, str]]:
    """东财行业板块列表（name -> BK code），模块级缓存一次拉全。"""
    global _EM_BOARD_CACHE
    if _EM_BOARD_CACHE is not None:
        return _EM_BOARD_CACHE
    rows = _em_clist("m:90+t:2", "f12,f14", max_pages=7)
    _EM_BOARD_CACHE = [{"name": str(x.get("f14")), "code": str(x.get("f12"))} for x in rows]
    return _EM_BOARD_CACHE


def get_industry_constituents(industry_name: str) -> List[Dict[str, Any]]:
    """按行业名取东财板块成分（按总市值降序）。

    Returns:
        [{secCode, secName, value(总市值,元)}, ...]；板块未命中返回 []。
    """
    if not industry_name:
        return []
    name = str(industry_name).strip()
    board = None
    for b in _em_board_map():
        if b["name"] == name:
            board = b
            break
    if board is None:  # 双向包含模糊匹配（basicInfo 行业名与东财板块名可能略有出入）
        for b in _em_board_map():
            if name in b["name"] or b["name"] in name:
                board = b
                break
    if board is None:
        print(f"[akshare_api] 未匹配到东财行业板块: {name}")
        return []
    rows = _em_clist(f"b:{board['code']}", "f12,f14,f20", max_pages=5)
    cons = [{"secCode": str(x.get("f12")), "secName": str(x.get("f14")),
             "value": float(x.get("f20")) if isinstance(x.get("f20"), (int, float)) else None}
            for x in rows]
    return sorted(cons, key=lambda c: c["value"] or 0, reverse=True)


def get_industry_rank(stock_code: str, industry_name: Optional[str] = None,
                      metric: str = "jzcsyl") -> Optional[Dict[str, Any]]:
    """行业相对位置（简化实现）

    取东财行业板块成分（轻量自实现，见 get_industry_constituents），
    按总市值给出公司在行业中的规模位次作为参考定位。
    指标排名（ROE/PE 等）标记为"公开渠道暂不提供"。

    Returns:
        {industryName, industryRank, industryAvg, industryList, metric}
    """
    if not industry_name:
        return None
    try:
        industry_list_raw = get_industry_constituents(industry_name)
    except Exception as e:  # noqa: BLE001
        print(f"[akshare_api] 行业成分获取失败（板块降级处理）: {e}")
        return None

    industry_list = []
    target_rank = None
    for i, c in enumerate(industry_list_raw, 1):
        if c["secCode"] == str(stock_code):
            target_rank = i
        # 内部口径：value 统一亿元（对齐旧版 parse_amount 口径）
        v_yi = (c["value"] / 1e8) if c["value"] else None
        industry_list.append({"secName": c["secName"], "secCode": c["secCode"],
                              "rank": i, "value": v_yi})

    if not industry_list:
        return None

    vals = [x["value"] for x in industry_list if x["value"]]
    industry_avg = (sum(vals) / len(vals)) if vals else None  # 亿元

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


# ============================================================
# 6. 估值整合所需补充数据（FCF / 历史估值 / 同业对比）
#    —— 仅供"投资决策整合"板块使用；全部走多源降级、绝不编造
# ============================================================

# 现金流量表：列名关键词（子串匹配；同花顺宽表每行=报告期）
_CF_DATE_KEYS = ("报告期", "报告日期", "REPORT_DATE", "REPORTDATE")
_CF_CAPEX_PAY = "支付的现金"


def _to_amount_yuan(value: Any) -> Optional[float]:
    """金额统一转元：同花顺可能返回 'x亿'/'x万'，东财返回数值（元）。"""
    if value is None:
        return None
    s = str(value).replace(",", "").strip()
    if s in ("False", "None", "", "--", "nan", "-", "NoneType"):
        return None
    try:
        # 东财数值直通：若已是纯数字（元）则视为元；字符串带"亿/万"则换算
        f = float(s)
        return f
    except ValueError:
        # 含中文单位
        try:
            if s.endswith("亿"):
                return float(s[:-1]) * 1e8
            if s.endswith("万"):
                return float(s[:-1]) * 1e4
            return float(s)
        except ValueError:
            return None


def _pick_col(columns, keys, require_all=None):
    """按子串关键词在 DataFrame 列中找目标列名（首个命中）。"""
    for col in columns:
        c = str(col)
        hit = any(k in c for k in keys)
        if hit and require_all:
            hit = hit and all(rk in c for rk in require_all)
        if hit:
            return col
    return None


def _pick_ths_ocf_col(columns):
    """同花顺宽表中选干净的 OCF 列：须含'经营活动产生的现金流量净额'，
    排除带'*'（核心指标区）、排除'间接法-'，返回第一个匹配（明细区净额）。"""
    for col in columns:
        c = str(col)
        if "经营活动产生的现金流量净额" in c and "*" not in c and "间接法" not in c:
            return col
    return None


def get_cash_flow(stock_code: str, periods: int = 8) -> List[Dict[str, Any]]:
    """现金流量表（单位：元）——经营现金流 OCF 与资本开支 CAPEX，用于算 FCF。

    两个数据源均为"宽表"（每行=一个报告期，每列=一个科目）：
      主源：同花顺现金流量表（stock_financial_cash_ths，按报告期）值如 '373.35亿'
      备源：东财现金流量表（stock_cash_flow_sheet_by_report_em，需市场前缀）值 float 元，
            列名为代码（NETCASH_OPERATE / CONSTRUCT_LONG_ASSET）

    Returns:
        [{reportDate(YYYY-MM-DD), ocf(元), capex(元), fcf(元)}, ...] 按报告期降序；
        任一分量缺失时该期 fcf=None（不猜测，交下游弹性降级）。失败返回 []。
    """
    ensure_akshare()
    rows: List[Dict[str, Any]] = []

    # --- 主源：同花顺（宽表）---
    try:
        df = _with_retry(ak.stock_financial_cash_ths, symbol=stock_code, indicator="按报告期")
        if df is not None and not df.empty:
            cols = list(df.columns)
            date_col = _pick_col(cols, _CF_DATE_KEYS)
            ocf_col = _pick_ths_ocf_col(cols)
            capex_col = _pick_col(cols, ("购建固定资产",), require_all=_CF_CAPEX_PAY)
            if date_col and ocf_col:
                for _, r in df.iterrows():
                    ocf = _to_amount_yuan(r.get(ocf_col))
                    capex = _to_amount_yuan(r.get(capex_col)) if capex_col else None
                    fcf = (ocf - capex) if (ocf is not None and capex is not None) else None
                    rows.append({
                        "reportDate": str(r.get(date_col))[:10],
                        "ocf": ocf, "capex": capex, "fcf": fcf,
                    })
                rows = _drop_bad_dates(rows)[:periods]
                if rows:
                    return rows
    except Exception as e:  # noqa: BLE001
        print(f"[akshare_api] 同花顺现金流量表失败（换东财源）: {repr(e)[:80]}")

    # --- 备源：东财（宽表，列名为英文代码）---
    prefix = "SH" if str(stock_code).startswith("6") else "SZ"
    try:
        df = _with_retry(ak.stock_cash_flow_sheet_by_report_em, symbol=f"{prefix}{stock_code}")
        if df is not None and not df.empty:
            cols = list(df.columns)
            date_col = _pick_col(cols, ("REPORT_DATE", "报告日期"))
            ocf_col = _pick_col(cols, ("NETCASH_OPERATE",))
            capex_col = _pick_col(cols, ("CONSTRUCT_LONG_ASSET",))
            if date_col and ocf_col:
                for _, r in df.iterrows():
                    ocf = _to_amount_yuan(r.get(ocf_col))
                    capex = _to_amount_yuan(r.get(capex_col)) if capex_col else None
                    fcf = (ocf - capex) if (ocf is not None and capex is not None) else None
                    rows.append({
                        "reportDate": str(r.get(date_col))[:10],
                        "ocf": ocf, "capex": capex, "fcf": fcf,
                    })
                rows = _drop_bad_dates(rows)[:periods]
                if rows:
                    return rows
    except Exception as e:  # noqa: BLE001
        print(f"[akshare_api] 东财现金流量表失败（降级为空）: {repr(e)[:80]}")

    return []


def _drop_bad_dates(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """过滤无法解析为 YYYY-MM-DD 的行，并按日期降序。"""
    import datetime as _dt
    parsed = []
    for r in rows:
        d = r.get("reportDate") or ""
        try:
            if len(d) >= 10:
                _dt.date.fromisoformat(d[:10])
                parsed.append(r)
        except ValueError:
            continue
    parsed.sort(key=lambda x: str(x.get("reportDate", "")), reverse=True)
    return parsed


def get_valuation_history(stock_code: str, max_days: int = 1500) -> Optional[Dict[str, Any]]:
    """历史估值（东财估值分析，ak.stock_value_em）——PE/PB 历史分位 + 现价 + 总股本。

    Returns:
        {series: [{date, peTtm, pb, close}...按日期升序, 截断最近 max_days],
         latest: {date, close, peTtm, pb, marketCap(元), totalShare(股)},
         historyDays: int, sourceNote: str}
        空/异常/样本过短 -> None（触发下游降级）。
    """
    ensure_akshare()
    try:
        df = _with_retry(ak.stock_value_em, symbol=stock_code)
        if df is None or df.empty:
            return None
        cols = list(df.columns)
        date_col = _pick_col(cols, ("数据日期", "日期"))
        close_col = _pick_col(cols, ("当日收盘价", "收盘价"))
        pe_col = _pick_col(cols, ("PE(TTM)", "市盈率(TTM)", "市盈率-动态"))
        pb_col = _pick_col(cols, ("市净率",))
        mc_col = _pick_col(cols, ("总市值",))
        ts_col = _pick_col(cols, ("总股本",))
        if not (date_col and close_col):
            return None

        series = []
        for _, r in df.iterrows():
            d = str(r.get(date_col))[:10]
            try:
                close = float(r.get(close_col))
            except (TypeError, ValueError):
                continue
            def _num(x):
                try:
                    if x is None: return None
                    return float(x)
                except (TypeError, ValueError):
                    return None
            pe = _num(r.get(pe_col)) if pe_col else None
            pb = _num(r.get(pb_col)) if pb_col else None
            series.append({"date": d, "close": close, "peTtm": pe, "pb": pb})
        if not series:
            return None
        series.sort(key=lambda x: x["date"])
        series = series[-max_days:]

        latest_row = df.iloc[-1]
        def _num2(x):
            try:
                if x is None: return None
                return float(x)
            except (TypeError, ValueError):
                return None
        market_cap = _num2(latest_row.get(mc_col)) if mc_col else None
        total_share = _num2(latest_row.get(ts_col)) if ts_col else None

        # 单位自检：股本 × 收盘价 ≈ 总市值（只打日志，不阻断）
        latest = series[-1]
        if market_cap and total_share and latest["close"]:
            prod = total_share * latest["close"]
            ratio = prod / market_cap if market_cap else 1.0
            if not (0.5 <= ratio <= 2.0):
                print(f"[akshare_api] 历史估值单位自检偏差（股本×价/市值={ratio:.2f}）")

        return {
            "series": series,
            "latest": {
                "date": latest["date"], "close": latest["close"],
                "peTtm": latest["peTtm"], "pb": latest["pb"],
                "marketCap": market_cap, "totalShare": total_share,
            },
            "historyDays": len(series),
            "sourceNote": "东方财富 数据中心-估值分析（akshare）",
        }
    except Exception as e:  # noqa: BLE001
        print(f"[akshare_api] 历史估值获取失败（降级相对估值）: {repr(e)[:100]}")
        return None


def get_peer_valuation(stock_code: str,
                       industry_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """同业/全市场 PE/PB 中位数（相对估值降级档，仅历史分位不可用时调用）。

    Returns:
        {industryName, peerMedianPe, peerMedianPb, marketMedianPe, marketMedianPb,
         currentPe, currentPb, sampleN, note}
        任一步失败 -> None。
    """
    ensure_akshare()
    try:
        spot = _with_retry(ak.stock_zh_a_spot_em)
        if spot is None or spot.empty:
            return None
        code_col = next((c for c in spot.columns if c in ("代码", "代码")), spot.columns[0])
        pe_col = _pick_col(spot.columns, ("市盈率-动态", "市盈率"))
        pb_col = _pick_col(spot.columns, ("市净率",))
        if not (pe_col and pb_col):
            return None

        # 当前股
        row = spot[spot[code_col].astype(str) == str(stock_code)]
        current_pe = current_pb = None
        if not row.empty:
            r = row.iloc[0]
            try:
                current_pe = float(r.get(pe_col))
            except (TypeError, ValueError):
                pass
            try:
                current_pb = float(r.get(pb_col))
            except (TypeError, ValueError):
                pass

        def _median(df, col):
            s = df[col].dropna()
            s = s[s.apply(lambda x: isinstance(x, (int, float)))]
            s = s[(s > 0) & (s < 200)]
            return float(s.median()) if len(s) else None

        market_median_pe = _median(spot, pe_col)
        market_median_pb = _median(spot, pb_col)

        # 同业：东财行业板块成分（轻量自实现，尽力取，失败则退化为全市场中位数）
        peer_pe = peer_pb = None
        sample_n = 0
        if industry_name:
            try:
                cons = get_industry_constituents(industry_name)
                if cons:
                    codes = {c["secCode"] for c in cons}
                    peers = spot[spot[code_col].astype(str).str.zfill(6).isin(codes)]
                    sample_n = len(peers)
                    if sample_n > 1:
                        peer_pe = _median(peers, pe_col)
                        peer_pb = _median(peers, pb_col)
            except Exception as e:  # noqa: BLE001
                print(f"[akshare_api] 同业估值获取失败（退化为市场）: {repr(e)[:80]}")

        return {
            "industryName": industry_name,
            "peerMedianPe": peer_pe, "peerMedianPb": peer_pb,
            "marketMedianPe": market_median_pe, "marketMedianPb": market_median_pb,
            "currentPe": current_pe, "currentPb": current_pb,
            "sampleN": sample_n,
            "note": "历史 PE/PB 分位暂缺，改以同业/市场中位数横向参照（弹性降级）",
        }
    except Exception as e:  # noqa: BLE001
        print(f"[akshare_api] 同业估值获取失败（降级为空）: {repr(e)[:100]}")
        return None


if __name__ == "__main__":
    ensure_akshare()
    data = get_company_research_data("002594")
    b = data["basicInfo"] or {}
    print(f"公司: {b.get('secName')} | 行业: {b.get('industryName')} | 数据源: {data['dataSource']}")
    print(f"总股本(股): {b.get('totalShare')} | 总市值(元): {b.get('totalMarketCap')}")
    print(f"财务期数: {len(data['keyIndicators'])}")
    for k in data["keyIndicators"][:3]:
        print(f"  {k['reportDate']}: 营收 {parse_amount(k.get('totalRevenue'))} | "
              f"ROE {k.get('wgtAvgRoe')} | 负债率 {k.get('assetLiabRatio')}")
    print("\n-- 估值数据自测 --")
    cf = get_cash_flow("002594", periods=5)
    print(f"现金流量表: {len(cf)} 期" + (f"，最新 {cf[0]['reportDate']} FCF(元)={cf[0]['fcf']}" if cf else ""))
    vh = get_valuation_history("002594")
    if vh:
        l = vh["latest"]
        print(f"历史估值: {vh['historyDays']} 日 | 最新 {l['date']} 收盘 {l['close']} PE {l['peTtm']} PB {l['pb']} | 总股本(股) {l['totalShare']}")
    else:
        print("历史估值: 不可用（降级路径）")
