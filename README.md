# 深知可信投研（上市公司研究+政策标准洞察）

将开源公开披露的金融数据与深知可信检索的政策、法规、标准结合，输出含政策影响分析的可溯源投研报告。

## 快速开始

### 1. 检查 Python 运行环境

主程序会在当前 Python 环境中自动检测 akshare：已安装则直接使用；未安装则通过同一个 Python 的 `-m pip` 安装一次，避免 WorkBuddy 在多个 Python/pip 环境间重复安装。

如需提前检查：

```bash
python3 scripts/runtime_check.py
```

返回 `ready: true` 后直接运行，不要再安装。只有 `ready: false` 时才执行一次：

```bash
python3 scripts/runtime_check.py --install
```

不要使用普通 `pip install`、`pip3` 或其他环境的 `pip list` 判断安装状态。

### 2. 开通深知权威检索（政策/标准板块需要）

金融数据开箱即用；检索政策/标准需要开通深知能力（手机号验证，Agent 代办）：

```bash
python3 scripts/initialize.py                 # 检查状态
node scripts/register_key.mjs send --phone <手机号>
node scripts/register_key.mjs register --phone <手机号> --vcode <验证码>
# Key 注入环境变量 DKNOWC_API_KEY 后重新 initialize.py 确认
```

### 3. 运行研究

```bash
python3 scripts/run_research.py "比亚迪" 比亚迪_报告.md
```

一次生成三件套（`official-docs/output/`）：`报告.md` + `可溯源.html` + `数据快照.data.json`。

## 报告结构（七板块）

1. 公司概况（公开披露）
2. 关键财务指标（同花顺 F10 主源，同比自动匹配上年同期）
3. 行业定位（板块规模位次，指标排名口径已标注）
4. 政策环境（深知规范性文件清单通道）
5. 标准与准入（标准号过滤）
6. **政策影响分析（投资视角）**：方向/传导链/影响变量/跟踪指标/投资含义，财务信号 × 政策时间表联动归因，每条带角标可溯源
7. 风险提示

## 数据源说明

| 数据 | 来源 | 说明 |
|------|------|------|
| 金融数据 | akshare（同花顺 F10 主源 + 东财/巨潮备源） | 公开披露，免 Key，MIT 开源 |
| 政策/标准 | 深知可信搜索 | policyFiles 清单通道 + 检索文章，日期多源校验 |

数据质量：财务数值与上市公司法定披露一致（营收/净利差异 ≤0.01%，ROE/负债率一致），来源可追溯。东财源限流时自动降级重试。

## 目录结构

```
dknowc-trusted-investment-research/
├── SKILL.md                     # Skill 说明（Agent 入口）
├── README.md
├── .gitignore
├── official-docs/
│   ├── search-results/          # 中间产物
│   └── output/                  # 最终交付（三件套）
├── reference/                   # 样例报告
└── scripts/
    ├── initialize.py            # 深知检索初始化检查（DKNOWC_API_KEY）
    ├── runtime_check.py         # 当前 Python 依赖检查/一次性安装
    ├── register_key.mjs         # MaaS 注册（SkillHub 渠道）
    ├── akshare_api.py           # 开源金融数据层（免 Key）
    ├── config.py                # 纯环境变量配置
    ├── dknowc_search.py         # 深知检索（日期校验/分类归位）
    ├── impact_analysis.py       # 政策影响分析
    ├── format_report.py         # Markdown 渲染
    ├── render_html.py           # 可溯源 HTML 渲染
    ├── run_research.py          # 主入口
    └── check_release.py         # 发布检查
```

## 发布检查

```bash
python3 scripts/check_release.py
```

拦截 API Key、真实配置、工作区产物、`__pycache__` 进入公开包。

## 常见问题

- **WorkBuddy 一直重复安装 akshare？** 运行 `python3 scripts/runtime_check.py`。若返回 `ready: true`，立即停止安装并直接运行主程序；不要再执行其他 `pip list`。若为 `false`，只运行一次带 `--install` 的同解释器安装。
- **港股？** 仅 A 股（6 位代码）。
- **开着代理（Clash/V2Ray/Surge）会不会影响数据？** 不会。数据层自动把东财、同花顺、巨潮加入直连名单（no_proxy）绕过代理，并对东财做多服务器域名降级，用户无需改代理配置。
- **东财接口偶发失败？** 内置重试、多域名降级与同花顺主源兜底，核心财务不受影响。
- **未开通深知检索？** 金融数据板块照常输出，政策板块提示开通，不编造。

## 版本历史

- v1.0.0（2026-08-27）：首个公开版。akshare 开源数据层 + 深知政策标准检索 + 政策影响分析，三件套交付。
