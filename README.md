# VMaxxing：你的个人交易智能体

> 一条命令，让你的智能体具备完整交易研究能力。


---

## 项目介绍

**VMaxxing** 是一个开源的研究工作台，用于把金融问题转化为可运行的分析。它将自然语言提示连接到市场数据加载器、策略生成、回测引擎、报告、导出和持久研究记忆。

它面向**研究、模拟与回测**——并且在你选择时，可通过你自己授权的券商（如 Robinhood Agentic Trading）进行自主交易。它不托管任何资金，绝不超出你设定的限额交易，且你可随时一键停止。

- 🧠 **自然语言驱动**：用一句话描述你的交易问题，智能体自动编排数据、工具与技能完成研究。
- 🛡️ **安全优先**：模拟盘与实盘的区分是每家券商的结构性运行时守卫，实盘下单受 mandate 约束、kill switch 与完整审计账本保护。
- 🔌 **多入口**：交互式 CLI、Web UI、FastAPI、MCP 插件，研究 session 可跨入口延续。

---

## 项目功能

### 🔍 核心能力

| 任务 | 输出 |
|------|------|
| **提出交易问题** | 结合工具、数据、文档与可复用 session 上下文的市场研究。 |
| **回测策略想法** | 策略代码、指标、benchmark 上下文、验证 artifacts 与 run cards。 |
| **复盘自己的交易** | 券商日志解析、行为诊断、规则提取。 |
| **读取文档与图表** | 用可插拔 OCR 解析 PDF / DOCX / XLSX / PPTX / 图片，并用视觉模型语义化读取图表截图。 |
| **改进重复研究** | 持久记忆与可编辑 skills 把有用流程变成可复用工作流。 |
| **运行分析师团队** | 面向投资、量化、加密、宏观与风控工作流的多智能体研究评审。 |
| **把研究接入 IM 通道** | 通过 16 个内置消息适配器（Telegram、飞书、企微、Discord、微信/NapCat 等）管理同一套 session runtime。 |
| **交付可用成果** | 报告、TradingView Pine Script、TDX、MetaTrader 5、MCP tools，以及可延续的研究 sessions。 |
| **跑预置 alpha zoo 横评** | 462 个 alpha 因子，一行 CLI 在你选的 universe 上算 IC + IR + alive/reversed/dead 分类。 |
| **识别相关性状态** | `/correlation` 界面的边密度 + 迟滞时间线，显示市场何时融合为一个板块（描述性风险上下文，非交易信号）。 |

### 🗺️ 研究工作流

多数运行都会遵循同一条证据路径：路由请求、加载正确的市场上下文、执行工具、验证输出，并保持 artifacts 可检查。

| 层 | 发生什么 |
|----|----------|
| **Plan** | 选择相关金融 skills、tools、数据源，以及在有帮助时选择 swarm preset。 |
| **Ground** | 通过可用 loader 拉取 A 股、港股/美股、加密、期货、外汇、文档或网页上下文。 |
| **Execute** | 生成可测试的策略代码，运行工具，并使用匹配的回测引擎或分析工作流。 |
| **Validate** | 在适用时加入指标、benchmark comparison、Monte Carlo、Bootstrap、Walk-Forward、run cards 与 warnings。 |
| **Deliver** | 返回报告、artifacts、tool traces，以及面向 TradingView、TDX、MetaTrader 5、MCP clients 或后续 sessions 的导出。 |

### 📚 金融技能库（88 个 skills，9 个类别）

覆盖传统市场、加密与 DeFi，从数据源到量化研究的完整能力链路：

- **Data Source（10）**：`data-routing`、`tushare`、`yfinance`、`okx-market`、`akshare`、`mootdx`、`ccxt`、`eastmoney`、`sec-edgar`、`qveris`
- **Strategy（19）**：`strategy-generate`、`cross-market-strategy`、`technical-basic`、`candlestick`、`ichimoku`、`elliott-wave`、`smc`、`multi-factor`、`ml-strategy`
- **Analysis（22）**：`factor-research`、`correlation-regime`、`macro-analysis`、`global-macro`、`valuation-model`、`earnings-forecast`、`credit-analysis`、`dividend-analysis`
- **Asset Class（9）**：`options-strategy`、`options-advanced`、`convertible-bond`、`etf-analysis`、`asset-allocation`、`sector-rotation`
- **Crypto（7）**：`perp-funding-basis`、`liquidation-heatmap`、`stablecoin-flow`、`defi-yield`、`onchain-analysis`
- **Flow（8）**：`hk-connect-flow`、`us-etf-flow`、`edgar-sec-filings`、`financial-statement`、`adr-hshare`
- **Tool（10）**：`backtest-diagnose`、`report-generate`、`pine-script`、`doc-reader`、`web-reader`、`vnpy-export`、`trade-journal`
- **Research（2）**：`alpha-zoo`、`strategy-dev-manager`
- **Risk Analysis（1）**：`ashare-pre-st-filter`

### 🐝 多智能体交易团队（30 个 swarm presets）

开箱即用的智能体团队，预配置金融工作流：

| Preset | 工作流 |
|--------|--------|
| `investment_committee` | 多空辩论 → 风险审查 → PM 最终决策 |
| `global_equities_desk` | A 股 + 港/美股 + 加密研究员 → 全球策略师 |
| `crypto_trading_desk` | Funding/basis + liquidation + flow → 风险经理 |
| `earnings_research_desk` | 基本面 + 预期修正 + options → 财报策略师 |
| `macro_rates_fx_desk` | 利率 + 外汇 + 商品 → 宏观 PM |
| `quant_strategy_desk` | 筛选 + 因子研究 → 回测 → 风险审计 |
| `technical_analysis_panel` | 经典 TA + Ichimoku + harmonic + Elliott + SMC → 共识 |
| `risk_committee` | 回撤 + 尾部风险 + regime review → 审批 |
| `global_allocation_committee` | A 股 + 加密 + 港/美股 → 跨市场配置 |

运行 `vmx --swarm-presets` 查看全部 30 个预设。

### 📡 数据源与智能 Fallback

一次 `get_market_data` 调用，**22 个免费行情数据源**（另有可选付费市场 **QVeris**）。设 `source: "auto"`——loader 按符号自动选源，再沿按**被封 IP 风险**排序的同市场链向下走（永不封的公开源在前，限速 / 需 key 的在后）。零配置，无单点故障。

| Source | Markets | Auth | Role |
|--------|---------|------|------|
| `tencent` · `mootdx` | A 股 | none | never IP-banned（`mootdx` = 通达信 TCP） |
| `eastmoney` | A / 美 / 港 | none | OHLCV + 深度基本面与资金流工具（限速） |
| `baostock` · `akshare` | A（+ 美/港/期货/宏观/外汇） | none | 免费 fallback |
| `tushare` | A / 期货 / 基金 / 宏观 | token | 最丰富的 A 股数据 |
| `yahoo` · `sina` · `stooq` | 美(/港) | none | 直连行情/报价/期权，K 线至 1984 年 |
| `yfinance` | 美 / 港 | none | 封装 |
| `longbridge` | 美股 / 港股 | App Key + Secret + Access Token | 可选历史 OHLCV 源（需装可选 SDK） |
| `finnhub` · `alphavantage` · `tiingo` · `fmp` | 美 | key | 可选 provider |
| `qveris` | 全球多资产 | key · credits | **付费市场**——一把 key 通 63+ 家（仅显式选用） |
| `okx` · `ccxt` · `binance` | 加密 | none | OKX + 100+ 交易所 + Binance 历史 / USD-M 永续 |
| `futu` | 港 / A | OpenD | 可选本地 FutuOpenD |
| `mt5` | 外汇 / 贵金属 | MT5 终端 | MetaTrader 5（Exness 风格）行情 |
| `india_broker` | 印度（NSE/BSE） | 券商登录 | 只读 Shoonya / Dhan bars（fallback 链尾） |
| `local` | 任意 | none | 你自己的 CSV / Parquet / DuckDB（`local:` 前缀） |

除 OHLCV 外，**18 个只读数据工具**深入基本面与资金面——资金流、龙虎榜、北向、两融、大宗交易、股东户数、解禁、板块、研报、新闻、SEC 文件、财务报表、期权链、机构持仓、全市场筛选、代码搜索、宏观——全部经 MCP 暴露。

<details>
<summary><strong>🔧 进阶功能（回测引擎 · Alpha Zoo · 券商连接器）</strong></summary>

### 🧪 回测引擎（8 个引擎 + options portfolio，跨市场 composite）

| 引擎 | 市场 | 说明 |
|------|------|------|
| **ChinaA** | A 股 | T+1、涨跌停、pre-ST 筛选 |
| **GlobalEquity** | 美股 / 港股 | T+0 |
| **IndiaEquity** | 印度（NSE/BSE） | T+1、熔断带、config 驱动成本栈 |
| **Crypto** | 加密现货 / USD-M 永续 | 资金费结算、成交价/标记价分离 |
| **ChinaFutures** · **GlobalFutures** | 期货 | 保证金、合约乘数 |
| **Forex** | 外汇 / 贵金属 | 经 `mt5` loader |
| **Composite** | 跨市场 | 跨市场共享单一资金池 |
| **options_portfolio** | 期权 | 多腿、Greeks、payoff/scenario |

日内 bar：1m / 5m / 15m / 30m / 1H / 4H / 1D。15 项指标 + benchmark 对比，**5 个组合优化器**（equal-volatility / risk-parity / mean-variance / max-diversification / turnover-aware），以及 3 个验证工具（Monte Carlo / Bootstrap / Walk-Forward）。

### 🧬 Alpha Zoo（462 个预置 alpha，5 个家族）

- 📈 一行 CLI 完成 IC + IR + alive/reversed/dead 分类
- 🔬 AST 纯函数门禁 + lookahead 哨兵测试 + `pytest-socket` 网络阻断

| Zoo | 数量 | 来源 |
|-----|------|------|
| **qlib158** | 154 | Microsoft Qlib `Alpha158`（Apache-2.0） |
| **alpha101** | 101 | Kakushadze (2015), "101 Formulaic Alphas", arXiv:1601.00991 |
| **gtja191** | 191 | 国君证券 (2014)《191 个短周期交易型 alpha 因子》 |
| **academic** | 12 | Fama-French 5 因子 + Carhart 动量 + 多项学术稳定性因子 |
| **fundamental** | 4 | PIT 安全的 SEC company facts 因子 |

运行 `vmx alpha list` 浏览全部因子，`vmx alpha bench --zoo X --universe Y --period Z` 给一整个 zoo 打分。

### 🔌 券商连接器（12 家——读取 + 模拟盘，支持的券商可受约束实盘）

连接器优先（connector-first）的配置档。每个连接器都支持读取 + 模拟盘下单；实盘下单受用户定义的 mandate 约束（标的白名单、下单规模 / 敞口上限、每日交易次数上限、即时 kill switch），且从不托管资金——由券商执行。

| Broker | 市场 | 能力 |
|--------|------|------|
| **IBKR** | 全球 | 本地 TWS / Gateway，只读 |
| **Robinhood** | 美 | Agentic MCP（桌面 OAuth）——读取 + 受约束实盘 |
| **Tiger** | 美 / 港 / A | 读取 + 模拟盘 + 受约束实盘 |
| **Alpaca** | 美 | 读取 + 模拟盘 + 受约束实盘（+ TAP 密钥隔离模式） |
| **OKX** · **Binance** | 加密 | 读取 + 模拟盘 + 受约束实盘 |
| **Futu** | 港 / 美 / A | 读取 + 模拟盘 + 受约束实盘 |
| **MetaTrader 5** | 外汇 / CFD | 读取 + 模拟盘 + 受约束实盘（Exness 风格） |
| **Longbridge** · **Dhan** · **Shoonya** | 美/港 · 印度 | 仅读取 + 模拟盘——无运行时模拟/实盘判别标识，实盘被硬拒 |
| **Trading 212** | 英 / 欧 | 完全只读 |

</details>

---

## 如何配置启动

### 本地安装（开发环境）

前置条件：
- **Python 3.11+**
- 任意受支持 provider 的 **LLM API key**，或使用 **Ollama** 本地运行（无需 key）

```bash
python -m venv .venv
source .venv/bin/activate          # Linux / macOS
# .venv\Scripts\Activate.ps1       # Windows PowerShell
pip install -e .                    # 可编辑安装，便于二次开发
cp agent/.env.example agent/.env   # 编辑 —— 设置你的 LLM provider API key
vmx                       # 启动交互式 TUI
```

安装后你会获得以下命令：

| 命令 | 用途 |
|------|------|
| `vmx` | 交互式 CLI / TUI |
| `vmx-mcp` | 启动 MCP server（接入 Claude Desktop、OpenClaw、Cursor 等 MCP 客户端） |

首次运行一个研究任务：

```bash
vmx init
vmx run -p "Backtest a BTC-USDT 20/50 moving-average strategy for 2024 and summarize return and drawdown"
```

### 配置 LLM

将 `agent/.env.example` 复制为 `agent/.env`，取消注释你想使用的 provider block。每个 provider 需要 3–4 个变量：

| 变量 | 必需 | 说明 |
|------|:----:|------|
| `LANGCHAIN_PROVIDER` | 是 | Provider 名称（`openrouter`、`deepseek`、`groq`、`ollama` 等） |
| `<PROVIDER>_API_KEY` | 是* | API key（`OPENROUTER_API_KEY`、`DEEPSEEK_API_KEY` 等） |
| `<PROVIDER>_BASE_URL` | 是 | API endpoint URL |
| `LANGCHAIN_MODEL_NAME` | 是 | 模型名称（例如 `deepseek-v4-pro`） |
| `TUSHARE_TOKEN` | 否 | A 股数据的 Tushare Pro token（会 fallback 到 AKShare） |
| `TIMEOUT_SECONDS` | 否 | LLM 调用超时，默认 120s |
| `API_AUTH_KEY` | 网络部署推荐 | API 可被非本地客户端访问时要求的 Bearer token |

\* Ollama 不需要 API key。OpenAI Codex 使用 ChatGPT OAuth，不写入 `agent/.env`。

**支持的 LLM providers：** OpenRouter、Requesty、OpenAI、Anthropic（原生 Messages API）、DeepSeek、Gemini、Groq、DashScope/Qwen、Zhipu、Moonshot/Kimi、MiniMax、SiliconFlow（CN + Global）、Xiaomi MIMO、iFlytek 星火、Z.ai、NVIDIA NIM、Ollama（本地）。未设置 `*_BASE_URL` 时，每个 provider 会回退到其规范端点，因此只需一个 key 即可。

**免费数据（无需 key）：** A 股通过 AKShare，港/美股通过 yfinance，加密通过 OKX，100+ 加密交易所通过 CCXT。系统会为每个市场自动选择最佳可用数据源。

**推荐模型**

VMaxxing 是高度依赖工具的智能体，模型选择直接决定它是否真正使用工具：

| 档位 | 示例 | 使用场景 |
|------|------|----------|
| **Best** | `anthropic/claude-opus-4.7`、`openai/gpt-5.5-pro`、`google/gemini-3.5-flash` | 复杂 swarms、长研究 sessions、论文级分析 |
| **Sweet spot（默认）** | `deepseek-v4-pro`、`x-ai/grok-4.20`、`z-ai/glm-5.1`、`moonshotai/kimi-k2.6`、`qwen/qwen3-max-thinking` | 日常主力，约 1/10 成本下具备可靠工具调用 |
| **避免用于 agent** | `*-nano`、`*-flash-lite`、小型 / 蒸馏变体 | 工具调用不可靠，智能体易"凭记忆回答"而非运行回测 |

---

## 使用示例

### 策略与回测

```bash
# 美股均线交叉
vmx run -p "Backtest a 20/50-day moving average crossover on AAPL for the past year, show Sharpe ratio and max drawdown"

# 加密 RSI 均值回归
vmx run -p "Test RSI(14) mean-reversion on BTC-USDT: buy below 30, sell above 70, last 6 months"

# A 股多因子策略
vmx run -p "Backtest a momentum + value + quality multi-factor strategy on CSI 300 constituents over 2 years"

# 回测后导出到 TradingView / TDX / MetaTrader 5
vmx --pine <run_id>
```

一行命令横评预置 alpha zoo：

```bash
vmx alpha bench --zoo gtja191 --universe csi300 --period 2018-2025 --top 20
```

### 市场研究

```bash
# 个股深度研究
vmx run -p "Research NVDA: earnings trend, analyst consensus, option flow, and key risks for next quarter"

# 宏观分析
vmx run -p "Analyze the current Fed rate path, USD strength, and impact on EM equities and gold"

# 加密链上分析
vmx run -p "Deep dive BTC on-chain: whale flows, exchange balances, miner activity, and funding rates"
```

### 多智能体工作流（Swarm）

```bash
# 个股多空辩论
vmx --swarm-run investment_committee '{"topic": "Is TSLA a buy at current levels?"}'

# 从筛选到回测的量化策略
vmx --swarm-run quant_strategy_desk '{"universe": "S&P 500", "horizon": "3 months"}'

# 加密交易台：funding + liquidation + flow → 风险经理
vmx --swarm-run crypto_trading_desk '{"asset": "ETH-USDT", "timeframe": "1w"}'

# 全球宏观组合配置
vmx --swarm-run macro_rates_fx_desk '{"focus": "Fed pivot impact on EM bonds"}'
```

### 跨会话记忆

```bash
# 一次性保存你的偏好
vmx run -p "Remember: I prefer RSI-based strategies, max 10% drawdown, hold period 5–20 days"

# 智能体在后续 session 中自动回忆
vmx run -p "Build a crypto strategy that fits my risk profile"
```

### 文档上传与分析

```bash
# 分析券商导出或财报
vmx --upload trades_export.csv
vmx run -p "Profile my trading behavior and identify any biases"

vmx --upload NVDA_Q1_earnings.pdf
vmx run -p "Summarize the key risks and beats/misses from this earnings report"
```

### 常用 CLI

```bash
vmx               # 交互式 TUI
vmx run -p "..."  # 单次运行
vmx serve         # API / Web UI server
vmx alpha list    # 浏览 462 个预置 alpha（支持 show / bench / compare）
vmx --upload report.pdf
vmx --swarm-presets
```

TUI 内 slash 命令：`/help`、`/skills`、`/swarm`、`/list`、`/show <run_id>`、`/code <run_id>`、`/pine <run_id>`、`/trace <run_id>`、`/continue <run_id> <prompt>`、`/sessions`、`/settings`、`/quit`。

---

## 致谢

本项目基于以下开源项目构建，在此致谢：

- **Vibe-Trading**（[HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)）—— 本项目的上游研究工作台，提供了自然语言驱动交易研究的核心能力与架构基础。
- **Learn OpenHarness**（[joyehuang/Learn-Open-Harness](https://github.com/joyehuang/Learn-Open-Harness)）—— Claude Code / Agent Harness 教学项目，为本项目的工程化与测试 harness 设计提供了参考。

> 原 Vibe-Trading 的完整贡献者名单见其上游仓库；本项目为个人二开版本，未另行维护独立贡献者列表。

---

## 免责声明与许可

VMaxxing 是研究与交易软件。它不是投资建议，不托管任何资金，也不运营执行场所。仅通过你自己明确授权的券商通道（如 Robinhood Agentic Trading）进行交易，且只在你设定的限额内、你可随时停止。该券商交易能力为实验性，未经我们对接真实券商账户验证——风险自负。历史表现不代表未来结果。

[MIT License](LICENSE) — 欢迎通过 [CONTRIBUTING.md](CONTRIBUTING.md) 参与贡献。

⭐ 如果 **VMaxxing** 对你的研究有帮助，点个 Star 让更多人看到它。
