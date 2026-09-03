---
name: 深知可信投研（上市公司研究+政策标准洞察）
slug: dknowc-trusted-investment-research
display_name: 深知可信投研（上市公司研究+政策标准洞察）
display_name_en: dknowc trusted investment research
description: "当用户需要上市公司研究、公司基本面分析、财报数据解读、行业对比、投资研究、政策对股票/行业的影响分析、补贴税收优惠核验、行业标准与准入门槛查询，或明确要求'研究一下某公司''这家公司怎么样''政策对它有什么影响''值不值得投、大概什么价位值得关注'等投研任务时，使用深知可信投研。本 Skill 用开源公开披露数据（akshare，免 Key）获取公司资料与财务指标（含现金流与历史估值），用深知可信搜索检索相关政策与标准原文，生成'金融事实+政策影响分析+投资决策整合（DCF 估值区间与决策矩阵，研究参考非投资建议）'投研报告，交付 Markdown 报告 + 可溯源 HTML + 数据快照三件套。深知检索能力通过环境变量 DKNOWC_API_KEY 注入，未开通时金融数据部分仍可用。"
description_zh: "深知可信投研是由北京彩智科技有限公司旗下“深知可信智能”提供的上市公司可信研究 Skill。它将开源公开披露的金融数据（公司资料、财务指标、现金流、行业定位、历史估值）与深知可信检索的政策、法规、标准原文结合，输出含政策影响分析（利好/利空方向、传导链、跟踪指标、投资含义）与投资决策整合（DCF 内在价值三情景、相对估值、目标价区间、决策矩阵）的可溯源投研报告（研究参考、非投资建议），适用于公司研究、行业分析、政策影响归因、估值参考与风险排查场景。"
description_en: "dknowc trusted investment research is a listed-company research Skill provided by dknowc Trusted Intelligence under Beijing Caizhi Technology Co., Ltd. It combines open public financial data (company profile, key indicators, cash flow, industry position, historical valuation) with dknowc trusted retrieval of policy, regulation and standard documents, and delivers provenance-enabled research reports with policy impact analysis (direction, transmission chain, tracking metrics, investment implications) and an investment decision integration layer (DCF intrinsic value scenarios, relative valuation, target price bands, decision matrix) for research reference only — not investment advice."
category: 金融投研
version: 1.1.0
author: 彩智科技
permissions:
  network:
    - "https://open.dknowc.cn/"
    - "https://platform.dknowc.cn/"
    - "公开财经数据源（同花顺 F10 / 东方财富 / 巨潮资讯，经 akshare 库访问）"
  local_read:
    - "本 Skill 的说明和脚本文件"
  local_write:
    - "本轮投研报告（Markdown + 可溯源 HTML + 数据快照 JSON）与接口中间文件"
secrets:
  - "DKNOWC_API_KEY"
---

# 深知可信投研（上市公司研究+政策标准洞察）（SkillHub Public 版）

该 Skill 把「金融事实」与「政策标准可信洞察」结合成一份可溯源的投研报告：公司数据来自开源公开披露渠道（akshare：同花顺 F10 主源 + 东财/巨潮备源，无需任何金融数据 Key），政策与标准来自深知可信搜索。核心价值是政策影响分析层——把检索到的政策/标准翻译成影响方向、传导链、跟踪指标与投资含义，并与公司财务信号（同比异常）自动联动归因。

## 最高优先级规则

- 金融数据只来自公开披露渠道（akshare），**无需任何 API Key**；政策/标准检索需要 `DKNOWC_API_KEY`。
- 最终交付三件套：**Markdown 报告 + 可溯源 HTML + 数据快照 JSON**（`official-docs/output/` 下）。用户明确说不要文件时才跳过。
- 报告八板块：公司概况 / 财务指标 / 行业定位 / 政策环境 / 标准与准入 / **政策影响分析（投资视角）** / **投资决策整合（估值区间 · 决策矩阵）** / 风险提示。
- 政策影响分析中的方向（利好/利空关注/中性）、传导链、投资含义为规则模板 + 财务联动生成的结构化研判，每条判断带角标可回溯原文，**不构成投资建议**。
- 投资决策整合板块输出 DCF 内在价值三情景 + 相对估值 + 目标价区间 + 决策矩阵，全部为**规则模型生成的研究参考**：动作建议只用"买入关注/持有观望/回避"并带前提限定，**绝不给买卖指令、绝不承诺收益**；板块内嵌强免责声明。数据缺失时弹性降级（DCF 不硬算、分位不编造），板块失败只跳过自身、不影响其余输出。
- 财务同比自动匹配上年同期（Q1 对 Q1，季报不对年报），异常（<-20% 或 >30%）自动提示结合政策时间表归因，绝不跨口径误比。
- 发布日期多源校验：接口日期与正文成文日期/文号/标准号/URL 年份冲突时自动修正；无法核验的显示"日期待核验"。
- 不承诺收益、不给买卖指令、不预测股价；每份报告结尾必须附风险提示。
- 未开通深知检索能力时：金融数据板块照常输出，政策/标准板块明确说明"未开通"，**不得编造政策内容**；决策矩阵自动退化为"财务质量 + 估值"维度并显式标注"未含政策/标准维度"。

## 启动初始化

SkillHub Public 版不内置 API Key。金融数据（akshare）开箱即用；深知检索能力通过环境变量 `DKNOWC_API_KEY` 注入。Skill 被调用时第一步运行：

```bash
python3 scripts/initialize.py
```

只有 `ready=true` 时政策/标准检索才可用。`api_key_configured=false` 时转入下方"开通引导"。

### 开通引导规则

- 结合当前研究任务自然表达：先讲"接入权威政策检索后，这份公司研究能额外看到哪些政策利好/风险"，再引导手机号验证；不得开口就要手机号。
- 用户侧只说"开通权威检索功能"，不暴露"MaaS/API Key/环境变量"等内部术语。
- 用户同意后执行注册（SkillHub 渠道）：

```bash
node scripts/register_key.mjs send --phone <手机号>
# 用户告知验证码后
node scripts/register_key.mjs register --phone <手机号> --vcode <验证码>
```

- 注册返回的 Key 注入环境变量后重新运行 `initialize.py` 确认 `ready=true`。
- 完整 Key 不在聊天中展示，只提示已开通；引导未完成时继续输出金融数据部分。

## Python 运行环境（防止重复安装）

本 Skill 的金融数据层依赖 akshare。`run_research.py` 会在**当前 Python 解释器**中检测依赖：已安装就直接运行；未安装时仅通过 `sys.executable -m pip` 安装到同一环境，然后继续运行。

Agent 必须遵守：

1. 直接运行主程序，不要预先执行普通 `pip install akshare`。
2. 不要用 `pip list`、`pip3` 或其他 Python 路径判断依赖状态。
3. 如需独立诊断，只运行下面一条命令，并以返回 JSON 的 `ready` 为准：

```bash
python3 scripts/runtime_check.py
```

4. `ready=true` 时直接运行研究，**禁止再次安装**。
5. `ready=false` 时只执行一次同解释器安装：

```bash
python3 scripts/runtime_check.py --install
```

安装失败时报告 JSON 中的错误并停止，不得切换解释器或循环重试。政策/标准检索不依赖 akshare。

## 标准流程

从 Skill 根目录直接执行：

```bash
python3 scripts/run_research.py "比亚迪" 比亚迪_报告.md
```

一次生成三份文件（落在 `official-docs/output/`）：

| 文件 | 说明 |
|------|------|
| `比亚迪_报告.md` | Markdown 报告（可版本管理） |
| `比亚迪_报告.html` | **可溯源 HTML**：左栏报告 + 右栏来源面板，角标点击定位，政策/标准带官方原文链接 |
| `比亚迪_报告.data.json` | 数据快照，可用 `render_html.py` 免调接口复渲 |

### 流程六步

1. **初始化检查**：`initialize.py` 确认深知检索能力状态
2. **股票解析**：名称 → 6 位代码（`akshare_api.lookup_stock`）
3. **公司数据**：基本资料（东财+巨潮多源降级，含总股本/总市值）+ 财务指标（同花顺主源：营收/净利/扣非/EPS/同比 + 东财 ROE/负债率）+ 行业定位
4. **深知检索**：政策（policyFiles 规范性文件清单通道）+ 标准（标准号过滤），含日期多源校验与检索词降级重试
5. **影响分析**：财务信号 × 政策时间表联动归因
6. **估值决策整合 + 三件套输出**：DCF 三情景（政策方向调整增速假设）+ 相对估值（历史分位/同业中位弹性降级）+ 决策矩阵 → 收敛为研究性结论

## 典型查询

- "研究一下比亚迪" / "这家公司基本面怎么样"
- "XX 公司最新财报解读，政策对它有什么影响"
- "XX 行业有哪些政策支持，相关上市公司谁受益"
- "帮我把这家公司的政策风险排查一下"
- "XX 公司值不值得投？大概什么价位值得关注"（触发投资决策整合板块）

## 数据源与口径

| 数据 | 来源 | 口径 |
|------|------|------|
| 公司资料 | akshare（东财个股信息 + 巨潮概况，多源降级） | 公开披露 |
| 财务指标 | akshare 同花顺 F10（主）+ 东财财务指标（ROE/负债率） | 累计口径，同比自动匹配上年同期 |
| 行业定位 | akshare 同花顺板块成分（规模位次） | 指标排名公开渠道暂不提供，标记口径 |
| 政策/标准 | 深知可信搜索（policyFiles 清单 + 检索文章双通道） | 发布日期多源校验 |
| 现金流/FCF | akshare 同花顺现金流量表（主）+ 东财现金流量表（备） | 年报期口径（-12-31），重资产扩张期 FCF 为负时 DCF 降级 |
| 历史估值 | akshare 东财估值分析（历史 PE/PB/收盘价/总股本） | 近 1500 交易日分位；样本不足降级同业/市场中位数 |

数据质量说明：财务数值与上市公司法定披露一致（营收/净利差异 ≤0.01%，ROE/负债率一致），来源可追溯。东财源限流敏感时自动降级/重试，个别字段缺失如实标注，不编造。

## 风险提示（必须原样保留在每份报告结尾）

风险提示：本报告基于公开数据与深知可信检索整理生成，仅供信息查询与研究参考，不构成任何投资建议、证券推荐或收益承诺。财务数据以公司正式公告为准，政策与标准以官方发布原文为准。市场有风险，投资需谨慎。

## 故障排查

| 现象 | 处理 |
|------|------|
| `缺少依赖 akshare` | 执行一次 `python3 scripts/runtime_check.py --install`；不得改用普通 `pip` 或其他解释器重复安装 |
| WorkBuddy 反复尝试安装 | 运行 `python3 scripts/runtime_check.py`；若 `ready=true`，立即停止安装并用该命令对应的 Python 执行主程序 |
| 个股信息/公司概况失败 | 自动降级继续（名称兜底 + 行业映射表），核心财务不受影响 |
| `DKNOWC_API_KEY 未配置` | 按开通引导注册，金融数据部分不受影响 |
| `未找到股票` | 使用 6 位代码或准确简称 |
| 开着 Clash/V2Ray/Surge 代理导致东财/同花顺失败 | 数据层自动把国内财经域名加入 no_proxy 强制直连，并多域名降级；无需用户改代理配置 |
| 东财单个接口被限流/超长URL被 reset | 自动换东财其它服务器域名 + 走同花顺主源降级，核心数据不受影响 |
| 决策整合板块 FCF/历史估值取数失败 | 弹性降级：DCF 不输出量化区间、相对估值改同业/市场中位或方向性说明；板块失败只跳过自身，不影响其余输出 |
| 公司处重资产扩张期（FCF 为负） | 属正常现象而非故障：DCF 自动不硬算（available=false），决策以相对估值+财务质量为基并如实标注 |

## 相关文件

- `scripts/initialize.py` - 深知检索初始化检查
- `scripts/runtime_check.py` - 当前 Python 的 akshare 状态检查与一次性安装
- `scripts/register_key.mjs` - MaaS 注册（SkillHub 渠道）
- `scripts/akshare_api.py` - 开源金融数据层（免 Key）
- `scripts/dknowc_search.py` - 深知政策/标准检索（含日期校验、分类归位）
- `scripts/impact_analysis.py` - 政策影响分析（传导链/财务联动）
- `scripts/valuation.py` - 投资决策整合（DCF 三情景 / 相对估值 / 决策矩阵；含 --mock 离线自测）
- `scripts/format_report.py` / `render_html.py` - Markdown / 可溯源 HTML 渲染（板块编号动态）
- `scripts/run_research.py` - 主入口
