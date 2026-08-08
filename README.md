# VMaxxing

> 面向新手投资者的 Vibe-trading Agent Harness

技术栈：Python · OpenAI-compatible · Pydantic · Rich CLI · asyncio / Threading · ReAct · tushare · ddgs · Multi-agent

---

## 研究工作流

| 阶段 | 发生什么 |
|------|----------|
| **Plan** | 按需加载相关 skill、工具与数据源，必要时选一个 swarm preset |
| **Ground** | 通过 loader 拉取 A 股 / 国内市场的行情、新闻、财报等上下文 |
| **Execute** | 生成可测试的策略代码，运行工具，并用 A 股回测引擎做验证 |
| **Validate** | 加入指标、基准对比、Monte Carlo 等，输出带风险披露的 markdown 结论 |
| **Deliver** | 返回报告、回测产物（metrics / equity / trades）与可继续的研究 session |

---

## 核心能力

### 1. ReAct 主循环与工具调度（`src/agent/loop.py`）

- **自主拆解任务**：循环调用行情 / 回测 / 分析工具，最终收敛为结构化研究结论，最多 50 轮（可配）。
- **工具读写分级执行**：连续的只读工具走线程池并发（≤ 8 worker），写工具串行；每个工具带心跳与超时熔断，只读工具超时被丢弃、写工具只告警不中断；任何阶段都可中途取消（取消信号在迭代边界、流式 chunk、工具批次之间被轮询）。
- **流式输出**：直连 OpenAI 兼容的 `/v1/chat/completions` 端点，SSE 流式；并抽取各家的思考流（`reasoning_content`，覆盖 DeepSeek / OpenRouter 等别名）。

### 2. 上下文与记忆管理（`src/agent/loop.py` · `src/memory/persistent.py`）

| 层 | 机制 | 成本 |
|----|------|------|
| L1 microcompact | 内存压力下裁剪旧工具结果，仅保留最近若干条 | 零 |
| L2 context_collapse | 折叠长文本的中间段，保留头尾 | 零（纯字符串） |
| L3 auto_compact | 超 token 阈值时做结构化摘要，保留约 20K token 的近期尾部 | 一次 LLM 调用 |
| L4 compact 工具 | agent 主动调用 `compact` 触发压缩 | 一次 LLM 调用 |
| L5 迭代式更新 | 第 N 次压缩只更新上次摘要，而非从头重写，信息不衰减 | — |

- **跨会话记忆**：以 Markdown 文件持久化在 `~/.vmx/memory/`，启动时加载为**冻结快照**注入 system prompt 以命中 prompt cache；每次提问时按**关键词重叠**做 top-K 召回，把相关记忆塞进 user message。
- 阈值可通过 `TOKEN_THRESHOLD` 调整。

### 3. 工具 / Skill 渐进式披露（`src/agent/context.py` · `src/agent/skills.py`）

- system prompt 里每个 skill 只放**一行摘要**；完整 skill 文档由 agent 在真正需要时通过 `load_skill` 拉取，避免每轮都背着全部技能文档。

### 4. Agent Swarm 编排（`src/swarm/runtime.py`）

- **YAML preset 定义可复用 DAG**；运行时基于 Kahn 拓扑分层，**层内并发、层间串行**（层内用 `ThreadPoolExecutor`）。
- `depends_on` 做依赖门控（上游失败则下游标记为 blocked，不空跑）；`input_from` 把上游摘要路由给下游 agent。
- 内置preset覆盖舆情、基本面、量化、多空辩论等场景。

### 5. 常驻运行（可选，`cli/watch.py`）

- `watch` 守护进程 + asyncio 定时调度，可驱动周期性研究任务自动触发。
- 含**休眠封顶**（解决定时漂移）、**心跳存活检测**（感知进程是否卡死）、崩溃重启后的**运行-持久化状态对齐校验**，保证 agent 跨重启持续运行。

### 6. 可观测性

- 每次运行在 `runs/<run_id>/` 落盘：`trace.jsonl`（事件流）、`llm_usage.json`（token 用量）、完整 transcript，以及 `artifacts/`（metrics / equity / trades / validation 等）。
- 交互循环里 `/debug` 可实时看 token 与延迟。

---

## 数据源（A 股 / 国内）

一次 `get_market_data` 调用，loader 按市场自动选源并沿免 key 链 fallback（`source: "auto"`）：

| 源 | 市场 | 鉴权 |
|----|------|------|
| `tushare` | A 股 / 期货 / 基金 / 宏观 | token |
| `akshare` | A 股（及港/美/期货/宏观） | 免 key |
| `baostock` | A 股 | 免 key（TCP 协议，避开 eastmoney 系 CDN 封禁） |
| `tencent` | A 股 | 免 key |
| `mootdx` | A 股 | 免 key（通达信 TCP） |

---

## 回测引擎（A 股）

A 股引擎考虑 **T+1、涨跌停、pre-ST 过滤**等规则。策略以 `SignalEngine` 形式编写，回测产出 `metrics.csv` / `equity.csv` / `trades.csv`，并自动做交易归因、基准回归、Monte Carlo 等验证（数据缺失或调用失败时只跳过并标注）。

---

## 实盘 / 券商接入

目前以 A 股 / 港股的**读取与模拟盘**为主。研究闭环本身不依赖任何券商，所有研究、回测、记忆都可以在纯本地完成。

---

## 多智能体示例

| Preset | 工作流 |
|--------|--------|
| `investment_committee` | 多空辩论 → 风险审查 → PM 最终决策 |
| `fundamental_research_team` | 基本面研究小组（财务 / 预期 / 行业分工） |

交互循环里用 `/swarm <preset>` 启动，例如：

```
/swarm investment_committee 宁德时代现在估值贵不贵？多空两边各给我理由
```

---

## 快速开始

前置条件：Python 3.11+，以及任意一个 OpenAI 兼容端点的 API key。

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -e .                    # 可编辑安装，便于二次开发
cp agent/.env.example agent/.env   # 编辑 —— 填入 LLM 配置（见下）
vmx                                 # 进入交互式研究循环
```

### 配置 LLM（OpenAI 兼容）

只需一个 OpenAI 兼容端点。编辑 `agent/.env`：

| 变量 | 说明 |
|------|------|
| `LANGCHAIN_PROVIDER` | provider 别名，映射到对应的 key / base_url 环境变量 |
| `LANGCHAIN_MODEL_NAME` | 模型名 |
| `<PROVIDER>_API_KEY` | 该 provider 的 API key |
| `<PROVIDER>_BASE_URL` | OpenAI 兼容的 `/v1/chat/completions` 端点 |
| `TIMEOUT_SECONDS` | 可选，LLM 调用超时，默认 120s |

原理：provider 目录把别名解析为 key / base_url，再统一转发给一个直连的 OpenAI 兼容客户端（无 `langchain-openai` 依赖），所以任意兼容端点都能用，不需要为每个厂商写适配。

---

## 使用示例（中文）

```bash
# 进入交互式研究循环（主入口），之后直接用自然语言对话
vmx

# 非交互单次运行
vmx run -p "研究一下宁德时代近一年的基本面和机构预期，给一份中文结论"
vmx run -p "回测一个沪深300成分股上的动量与价值双因子策略，看两年夏普和最大回撤"

# 让 agent 记住你的偏好，后续 session 自动召回
vmx run -p "我记得我偏好 RSI 类策略、最大回撤不超过 10%，帮我生成一个符合我风险偏好的方案"
```

交互循环内的常用 slash 命令：

```
/swarm investment_committee 宁德时代现在值得买吗          # 启动多智能体投研
/memory                                                     # 查看 / 管理跨会话记忆
/debug                                                      # 实时看 token 与延迟
/history                                                    # 浏览并继续之前的 session
```

```bash
vmx list                 # 列出近期运行
vmx show <run_id>        # 查看某次运行的产物与 trace
vmx init                 # 重新跑配置向导
vmx-mcp                  # 启动 MCP server（可选，接 Claude Desktop 等 MCP 客户端）
```

TUI 内全部 slash 命令：`/help` `/model` `/memory` `/history` `/goal` `/search` `/swarm` `/skill` `/show` `/clear` `/pine` `/journal` `/export` `/debug` `/quit`

---

## 致谢

- **Vibe-Trading**（[HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)）—— 上游研究工作台，提供了自然语言驱动投研的核心能力与架构基础。
- **Learn OpenHarness**（[joyehuang/Learn-Open-Harness](https://github.com/joyehuang/Learn-Open-Harness)）—— agent harness 工程化与测试设计的参考。

---

## 免责声明

VMaxxing 是研究与回测软件，不是投资建议，不托管任何资金，也不运营执行场所。历史表现不代表未来结果；若接入券商做模拟 / 实盘，风险自负。
