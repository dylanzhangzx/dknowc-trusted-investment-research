#!/usr/bin/env python3
"""深知可信搜索 API 封装模块

封装深知可信搜索接口，用于检索政策、标准、法规原文。

接口文档参考: 深知可信搜索skill/public/skillhub/dknowc-trusted-search/SKILL.md
"""

import urllib.request
import urllib.parse
import json
import re
from datetime import date
from typing import Dict, Any, List, Optional, Tuple

from config import get_dknowc_headers


# ============================================================
# 发布日期多源校验
#
# 背景：深知接口的"发布日期"字段不可靠——可信度"一般"的条目
# 常返回页面转载/更新时间而非发文日期（实测：2012 年文件返回
# 2025-02-25）。以下旁证按优先级交叉校验：
#   1. 正文"成文日期：YYYY年MM月DD日"（结构化字段，最权威）
#   2. 正文文号年份（如"工信部产业（2012）528号"）
#   3. 标题标准号年份（如"GB/T 25042-2024"）
#   4. URL 年份（如 miit.gov.cn/.../art/2012/...）
# 接口日期与旁证冲突、或为未来日期时，按旁证修正并透明标注。
# ============================================================

_RE_ISSUE_DATE = re.compile(r"成文日期[：:]\s*(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_RE_DOC_NO_YEAR = re.compile(r"(?:公告|产业|工信部|发改|财|税)[（(]?(20\d{2})[）)]|第\s*二十|\d{4}\s*年第\s*\d+\s*号|（(20\d{2})）\s*\d+\s*号")
_RE_GB_YEAR = re.compile(r"GB[/\\]?T?\s*\d+[-—–]\s*(20\d{2})")
_RE_URL_YEAR = re.compile(r"/(?:art/)?(20\d{2})(?:[-/年]|\b)")


def _parse_cn_date(text: str) -> Optional[Tuple[int, int, int]]:
    """解析 'YYYY年MM月DD日' 形式日期，返回 (y, m, d)"""
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text or "")
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        date(y, mo, d)
        return (y, mo, d)
    except ValueError:
        return None


def verify_date(raw_date: str, url: str, title: str, full_text: str) -> Tuple[str, str]:
    """多源校验并修正发布日期

    Returns:
        (修正后日期文本, 提示文本；无提示时为空串)
        - 确定性修正（成文日期/标准号等强旁证）静默完成，不产生提示
        - 仅"日期待核验"等需要用户注意的情况才返回提示
    """
    today = date.today()
    parsed = _parse_cn_date(raw_date or "")

    # ---- 旁证 1：正文"成文日期"（最高优先级） ----
    m = _RE_ISSUE_DATE.search(full_text or "")
    issue_date = (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
    if issue_date:
        try:
            date(*issue_date)
        except ValueError:
            issue_date = None

    # ---- 旁证 2：正文文号年份 ----
    doc_year = None
    for pat in (r"[（(](20\d{2})[）)]\s*\d+\s*号", r"公告\s*(20\d{2})\s*年"):
        mm = re.search(pat, full_text or "")
        if mm:
            doc_year = int(mm.group(1))
            break

    # ---- 旁证 3：标题标准号年份 ----
    gb_year = None
    mg = _RE_GB_YEAR.search(title or "")
    if mg:
        gb_year = int(mg.group(1))

    # ---- 旁证 4：URL 年份 ----
    url_year = None
    mu = _RE_URL_YEAR.search(url or "")
    if mu:
        url_year = int(mu.group(1))

    def fmt(d): return f"{d[0]}年{d[1]:02d}月{d[2]:02d}日"

    # ---- 规则 A：接口日期为未来日期 → 必错 ----
    if parsed and date(*parsed) > today:
        if issue_date:
            return fmt(issue_date), ""  # 成文日期为强旁证，静默修正
        cand = next((y for y in (doc_year, gb_year, url_year) if y), None)
        if cand:
            return f"{cand}年", ""  # 有年份旁证，静默修正
        return "日期待核验", f"原文日期无法核验（接口返回 {raw_date}），请点击原文确认"

    # ---- 规则 B：正文成文日期与接口日期冲突 → 成文日期最权威 ----
    if issue_date and parsed and date(*issue_date) != date(*parsed):
        return fmt(issue_date), ""  # 成文日期为强旁证，静默修正

    # ---- 规则 C：无成文日期时，用年份旁证投票 ----
    if parsed:
        api_year = parsed[0]
        side_years = [y for y in (doc_year, gb_year, url_year) if y]
        if side_years:
            # 多数旁证一致且与接口年份不同 → 修正为旁证年份
            from collections import Counter
            votes = Counter(side_years)
            top_year, top_n = votes.most_common(1)[0]
            if top_n >= 2 and top_year != api_year:
                return f"{top_year}年", ""  # 多旁证一致，静默修正
            if top_n == 1 and len(set(side_years)) == 1 and top_year != api_year:
                # 唯一旁证：标准号年份可信度高（GB 标准号年份即发布年），其余单证据不动完整日期
                if gb_year and gb_year == top_year:
                    return f"{gb_year}年", ""  # 标准号年份即发布年份，静默修正
        return raw_date, ""

    # ---- 接口日期本身无法解析 ----
    if issue_date:
        return fmt(issue_date), ""  # 成文日期为强旁证，静默补全
    return raw_date or "日期待核验", ""


def search_policy_standard(config: Dict[str, Any],
                           query: str,
                           service_area: Optional[str] = None,
                           eff_time: Optional[str] = None,
                           segment_count: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """检索政策/标准原文

    Args:
        config: 配置字典
        query: 检索查询（如"新能源汽车 支持政策 补贴 税收优惠"）
        service_area: 地域（默认从 config 读取）
        eff_time: 生效时间（默认从 config 读取）
        segment_count: 每篇材料段落数（默认从 config 读取）

    Returns:
        检索结果字典，包含检索文章列表，失败返回 None
    """
    base_url = config["dknowc"]["base_url"]
    search_params = config.get("search_params", {})

    # 构建请求体
    payload = {
        "query": query,
        "policy": search_params.get("policy", True),
        "item": search_params.get("item", True),
        "knowBase": search_params.get("knowBase", True),
        "return_full_content": False,
        "simplified": search_params.get("simplified", True),
        "segmentCount": segment_count or search_params.get("segmentCount", 3),
        "service_area": [service_area or search_params.get("service_area", "全国")],
        "eff_time": [eff_time or search_params.get("eff_time", "2026年")]
    }

    headers = get_dknowc_headers(config)

    try:
        req = urllib.request.Request(
            base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data.get("ret") == 0 and data.get("content"):
            return data["content"]

        print(f"[dknowc_search] 检索失败: ret={data.get('ret')}, msg={data.get('msg')}")
        return None
    except Exception as e:
        print(f"[dknowc_search] 检索异常: {e}")
        return None


def search_industry_policy(config: Dict[str, Any],
                           industry_name: str,
                           service_area: str = "全国",
                           eff_time: str = "2026年") -> Optional[Dict[str, Any]]:
    """检索行业政策（专用封装）

    Args:
        config: 配置字典
        industry_name: 行业名称（如"新能源汽车"、"光伏"）
        service_area: 地域
        eff_time: 生效时间

    Returns:
        检索结果字典
    """
    # 检索词降级策略：完整长词在小众行业易空结果（实测"玻璃纤维+补贴税收优惠"返回 0 篇），
    # 空结果时自动降级为更短、更泛的检索词重试
    query_candidates = [
        f"{industry_name} 支持政策 补贴 税收优惠 企业适用条件",
        f"{industry_name} 行业 政策",
        f"{industry_name} 产业政策",
    ]
    result = None
    for query in query_candidates:
        print(f"[dknowc_search] 检索行业政策: {query}")
        result = search_policy_standard(config, query, service_area, eff_time)
        data = (result or {}).get("data", {}) or {}
        if data.get("检索文章") or data.get("policyFiles"):
            return result
        print(f"[dknowc_search]   空结果，降级重试…")
    return result


def search_industry_standard(config: Dict[str, Any],
                             industry_name: str,
                             service_area: str = "全国",
                             eff_time: str = "2026年") -> Optional[Dict[str, Any]]:
    """检索行业标准（专用封装）

    Args:
        config: 配置字典
        industry_name: 行业名称
        service_area: 地域
        eff_time: 生效时间

    Returns:
        检索结果字典
    """
    query = f"{industry_name} 国家标准 行业规范 准入条件 技术规范"
    print(f"[dknowc_search] 检索行业标准: {query}")
    return search_policy_standard(config, query, service_area, eff_time)


def _match_article(pf: Dict[str, Any],
                   articles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """在"检索文章"通道中找与 policyFile 对应的同一篇文章

    匹配策略：URL 完全一致或标题去空格后互含，均收为候选；
    候选中取段落内容最全的版本——同一文件常有多个转载版本
    （实测同题公告存在 190 字节选版与 780 字政策库完整版），
    URL 精确匹配可能恰好命中节选版而丢失关键条款（如"减半征收"），
    因此不做短路，统一按内容量择优。
    """
    url = (pf.get("sourceUrl") or "").strip()
    title = re.sub(r"\s+", "", pf.get("title") or "")

    def content_len(art: Dict[str, Any]) -> int:
        return sum(len(p.get("内容") or "") for p in (art.get("段落") or []))

    candidates = []
    for art in articles:
        art_url = (art.get("源网址") or "").strip()
        art_title = re.sub(r"\s+", "", art.get("文章标题") or "")
        url_hit = bool(url and art_url == url)
        title_hit = bool(title and art_title and (title in art_title or art_title in title))
        if url_hit or title_hit:
            candidates.append(art)
    if candidates:
        return max(candidates, key=content_len)
    return None


# 摘录选段信号词：政策关键条款常含这些词，优先选该段做摘录
# （首段常是文号抬头/背景，真正影响投资的条款在正文中间）
_SIGNAL_WORDS = (
    "减半", "退坡", "免征", "减免", "补贴", "优惠", "不得超过", "不超过",
    "比例", "税率", "自20", "起实施", "施行", "万元", "%",
)


def _pick_excerpt(full_text: str, limit: int = 200) -> str:
    """从全文智能选取摘录段

    优先返回第一个含信号词（金额/比例/时点/税惠条款）的段落，
    都没有则返回全文开头。避免摘录落在文号抬头上。
    """
    if not full_text:
        return ""
    for para in full_text.split("\n"):
        para = para.strip()
        if len(para) < 15:
            continue  # 跳过文号行、空行
        if any(w in para for w in _SIGNAL_WORDS):
            text = para[:limit].replace("\n", " ")
            return text + ("..." if len(para) > limit else "")
    # 无信号段落：取开头
    text = full_text.strip()[:limit].replace("\n", " ")
    return text + ("..." if len(full_text.strip()) > limit else "")


def extract_policy_files(search_result: Dict[str, Any],
                         max_files: int = 5) -> List[Dict[str, str]]:
    """从接口 policyFiles（规范性文件清单）通道提取政策条目

    policyFiles 是接口服务端维护的结构化"规范性文件清单"（payload policy=true 触发），
    相比检索文章通道，它由服务端做了类型把关——天然只含政策/规范性文件，
    是"政策 vs 标准"区分的接口原生依据。字段结构与检索文章不同：
    title / sourceUrl / createDate / createDateReliability / writtenText

    writtenText 常为 null：此时在同结果集的"检索文章"里按 URL/标题找同一
    文件，合并其段落全文（供日期校验与影响分析摘录），找不到则摘录置空。

    Returns:
        政策条目列表（与 extract_policy_highlights 同构，含 dateNote 日期校验）
    """
    if not search_result or "data" not in search_result:
        return []

    policy_files = search_result["data"].get("policyFiles", []) or []
    articles = search_result["data"].get("检索文章", []) or []
    highlights = []

    for pf in policy_files[:max_files]:
        title = pf.get("title", "")
        url = pf.get("sourceUrl", "")
        raw_date = pf.get("createDate", "")
        full_text = pf.get("writtenText") or ""

        # 清单正文缺失或过短时（writtenText 常为 11-32 字的文号摘要而非
        # 真正文），从检索文章通道合并同一文件的段落
        art = None
        if len(full_text or "") < 100:
            art = _match_article(pf, articles)
            if art:
                paragraphs = art.get("段落") or []
                merged = "\n".join(p.get("内容", "") for p in paragraphs if p.get("内容"))
                if len(merged) > len(full_text or ""):
                    full_text = merged

        # createDate 可信度高的条目直接采用；一般/未标注时仍走多源校验
        reliability = pf.get("createDateReliability", "")
        if reliability == "高" and _parse_cn_date(raw_date):
            fixed_date, date_note = raw_date, ""
        else:
            fixed_date, date_note = verify_date(raw_date, url, title, full_text)

        excerpt = ""
        if full_text:
            excerpt = _pick_excerpt(full_text)
        elif art:
            # 无段落但文章通道有该文件：以文章日期做旁证提示
            excerpt = f"（正文见原文链接；同源文章日期 {art.get('发布日期', '未标注')}）"

        highlights.append({
            "title": title,
            "source": "规范性文件清单（深知接口）",
            "url": url,
            "date": fixed_date,
            "dateNote": date_note,
            "excerpt": excerpt or "（规范性文件清单通道，摘录见原文）",
            # 全文截断：供影响分析做信号检测（摘录仅200字，关键条款常在深处）
            "fullText": (full_text or "")[:2000],
        })

    return highlights


# 标准条目识别：标题含标准号或标准关键词（用于标准栏过滤串类条目）
_RE_STD_MARK = re.compile(
    r"GB[/\\]?T?\s*\d+|GB/\w+|HB\s*\d+|JB[/\\]?T?\s*\d+|团体标准|国家标准|行业标准|标准条件|技术标准|规范条件"
)
# 政策条目识别：文件类型词（用于标准栏排除明显政策文件）
_RE_POLICY_MARK = re.compile(
    r"关于(印发|发布|调整|延续|优化|公布)|管理办法|实施意见|行动方案|补贴|税收优惠|十四五|十五五"
)


def extract_policy_highlights(search_result: Dict[str, Any],
                              max_articles: int = 5,
                              only_standards: bool = False) -> List[Dict[str, str]]:
    """提取政策/标准检索的关键条目

    Args:
        search_result: search_policy_standard 返回的结果
        max_articles: 最多提取几篇文章
        only_standards: True 时仅保留标准类条目（标题含标准号/标准关键词），
            用于标准栏过滤——标准检索词也会混入政策文件（实测 32 篇里首条
            即"废止和修订…政策标准…的公告"），按标题识别归位

    Returns:
        条目列表，每项包含 title, source, date, dateNote, excerpt
    """
    if not search_result or "data" not in search_result:
        return []

    articles = search_result["data"].get("检索文章", [])
    highlights = []

    for article in articles:
        if len(highlights) >= max_articles:
            break

        title = article.get("文章标题", "")

        # 标准栏过滤：标题无标准号/标准关键词的条目跳过
        if only_standards and not _RE_STD_MARK.search(title or ""):
            continue

        source = article.get("数据源", "") or "未标注来源"
        url = article.get("源网址", "")

        # 全部段落全文：用于发布日期旁证校验（文号/成文日期常在正文）
        paragraphs = article.get("段落", [])
        full_text = "\n".join(p.get("内容", "") for p in paragraphs if p.get("内容"))

        # 多源日期校验：接口日期 vs 成文日期/文号/标准号/URL 年份
        raw_date = article.get("发布日期", "")
        fixed_date, date_note = verify_date(raw_date, url, title, full_text)

        # 智能选段摘录：优先含金额/比例/时点等信号词的段落
        excerpt = _pick_excerpt(full_text)

        highlights.append({
            "title": title,
            "source": source,
            "url": url,
            "date": fixed_date,
            "dateNote": date_note,
            "excerpt": excerpt,
            # 全文截断：供影响分析做信号检测（摘录仅200字，关键条款常在深处）
            "fullText": (full_text or "")[:2000],
        })

    return highlights


def get_full_research_data(config: Dict[str, Any],
                           industry_name: str,
                           service_area: str = "全国",
                           eff_time: str = "2026年") -> Dict[str, Any]:
    """获取完整的政策/标准检索数据（组合调用）

    Args:
        config: 配置字典
        industry_name: 行业名称
        service_area: 地域
        eff_time: 生效时间

    Returns:
        包含 policies, standards, policy_highlights, standard_highlights 的综合数据字典
    """
    result = {
        "industryName": industry_name,
        "serviceArea": service_area,
        "effTime": eff_time,
        "policies": None,
        "standards": None,
        "policyHighlights": [],
        "standardHighlights": []
    }

    # 1. 检索政策
    print(f"[dknowc_search] 检索 {industry_name} 行业政策")
    result["policies"] = search_industry_policy(config, industry_name, service_area, eff_time)
    if result["policies"]:
        # 政策栏优先使用接口原生 policyFiles（规范性文件清单）通道——
        # 服务端已做类型把关，天然只含政策/规范性文件；清单为空时回退检索文章
        result["policyHighlights"] = extract_policy_files(result["policies"])
        if not result["policyHighlights"]:
            result["policyHighlights"] = extract_policy_highlights(result["policies"])

    # 2. 检索标准
    print(f"[dknowc_search] 检索 {industry_name} 行业标准")
    result["standards"] = search_industry_standard(config, industry_name, service_area, eff_time)
    if result["standards"]:
        # 标准栏启用过滤：仅保留标题含标准号/标准关键词的条目
        result["standardHighlights"] = extract_policy_highlights(
            result["standards"], only_standards=True)

    return result


if __name__ == "__main__":
    # 测试
    from config import load_config, validate_config

    config = load_config()
    if not validate_config(config):
        exit(1)

    # 测试检索新能源汽车政策
    print("\n=== 测试政策检索 ===")
    data = get_full_research_data(config, "新能源汽车")

    print(f"\n行业: {data['industryName']}")
    print(f"地域: {data['serviceArea']}")
    print(f"时间: {data['effTime']}")

    print(f"\n政策检索命中: {len(data['policyHighlights'])} 篇")
    for i, p in enumerate(data["policyHighlights"], 1):
        print(f"  {i}. {p['title'][:50]}... | {p['source']} | {p['date']}")

    print(f"\n标准检索命中: {len(data['standardHighlights'])} 篇")
    for i, s in enumerate(data["standardHighlights"], 1):
        print(f"  {i}. {s['title'][:50]}... | {s['source']} | {s['date']}")
