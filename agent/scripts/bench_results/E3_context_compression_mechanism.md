# E3 上下文压缩机制：对照组（degraded）到底怎么管上下文，为什么不管也能成功

核心开关：`src/agent/loop.py:78` `_no_compress()`
```python
def _no_compress() -> bool:
    """Benchmark toggle: disable the 4-layer context compression pipeline."""
    return os.environ.get("VIBE_NO_COMPRESS") == "1"
```
- **optimized 臂（E3 的主角 / 默认）**：`VIBE_NO_COMPRESS` 未设 → `_no_compress()` 为 False → **4 层压缩全开**。
- **degraded 臂（对照组 / 基线）**：`VIBE_NO_COMPRESS=1` → `_no_compress()` 为 True → **所有压缩层被跳过**。

---

## 1. optimized 臂的 4 层压缩（全部 `if not _no_compress()`）

触发阈值来自 `TOKEN_THRESHOLD`（默认 **40000**，`env_schema.py:294`）。
局部分级：`MICROCOMPACT = 0.5×`（20000）、`COLLAPSE = 0.7×`（28000）、`AUTO = 1.0×`（40000）。

| 层 | 函数 / 位置 | 触发 | 做什么 | 代价 |
|---|---|---|---|---|
| L1 microcompact | `_microcompact` `loop.py:254` | tokens > 20000 | 把**旧的** tool_result 内容改成 `[cleared]`，保留最近 `KEEP_RECENT` 条 | 0（纯字符串） |
| L2 context_collapse | `_context_collapse` `loop.py:269` | tokens > 28000 | 把旧消息里长文本的中间折叠，只留 head+tail：`...[N chars collapsed]...` | 0 |
| L3 auto_compact | `_auto_compact` `loop.py:1486` | tokens > 40000 | **调用 LLM 生成结构化摘要**（goal/progress/decisions/files），保留约 20K token 的最近尾巴，重建 `system + summary + tail` | 1 次 LLM 调用 |
| L4 compact 工具 | `loop.py:984,1113` | 模型主动调用 `compact` | 触发 L3，可带 `focus_topic` 优先压缩某主题 | 1 次 LLM 调用 |
| L5 迭代更新 | `loop.py:1555` | 第 N 次压缩 | 在上一版 summary 上**增量更新**而非从头生成，避免信息衰减 | 含在 L3 内 |

辅助：`_fix_tool_pairs`（`loop.py:292`）在压缩后修复被清掉的 tool_call/tool_result 孤儿对，保证 API 合法。

---

## 2. degraded 对照组（VIBE_NO_COMPRESS=1）的“上下文管理”= 几乎没有

`loop.py:679 / 684 / 690 / 984` 四处压缩入口全部是 `if not _no_compress():`，关闭后为 **True → 跳过**。
即对照组**不 prune、不 collapse、不 summarize**，每轮把 `messages` 全量原样发给模型。

它仅有的三道“天花板”：
1. **`TOOL_RESULT_LIMIT = 10_000`**（`loop.py:54,1468`）：每条工具返回值写进 context 前**先截断到 1 万字符**。这是“单条结果截断”，不是“轨迹压缩”。
2. **`max_iterations`**（`loop.py:707`）：到上限后强制模型收尾（"Stop calling tools and provide your final answer"）。
3. **模型原生上下文窗口**（deepseek-v4-pro 的硬上限）：唯一真正约束整段历史长度的东西。

---

## 3. 不管上下文，任务为什么还能成功？

因为 **完整（未压缩）的对话历史始终没超过模型的上下文窗口**。

实测（E3 数据）：
- consumer-elec **degraded** 峰值每轮 input = **82,292** token（14 轮跑完，累计 1,099,601）。
- defense **degraded** 峰值每轮 ≈ 60,404 token（17 轮，累计 1,287,073）。

这两个数字都**高于压缩阈值 40000**（所以“若无压缩会触发压缩”），但**远低于 deepseek-v4-pro 的窗口**（128K+ 量级），因此 API 没拒、模型每轮都拿到了**完整历史**，信息零丢失，自然能跑完。

结论：**压缩不是任务“能否成功”的前提，而是“省成本 + 防止超长轨迹溢出窗口”的优化。**
- 对照组靠“暴力全量喂历史”成功——而且因为没有信息被摘要掉，某些任务反而收敛更快（defense 17 轮 vs optimized 22 轮；consumer-elec 14 轮 vs optimized 43 轮）。
- 真正会反噬的场景：当轨迹长到原始 transcript **超过模型窗口**时，对照组才会失败（API 报错 / 上下文溢出）。long-horizon 任务被设计成“without compression 会 blow past TOKEN_THRESHOLD”，但本批实测都还卡在窗口内，所以对照组全成功。

---

## 4. 这套机制如何解释前面两组现象

- **degraded 每轮 token 更大**：它每轮都背着完整历史（82,292 vs optimized 53,597），正是关闭压缩的直接结果。
- **optimized 轮数反而更多（consumer-elec 43 vs 14）**：压缩把每轮成本压低后，agent 拿到了“上下文余量”，于是**把省下的预算又花在更多探索上**（43 轮 / 52 次工具 / 27 次 bash 本地计算 / 报告长 3 倍）。压缩买来的 headroom 被 agent 重投进了研究深度。
- **defense optimized 失败 ≠ 压缩的锅**：它死在 `provider_stream_error`（DeepSeek 掉流），对照组（压缩关）同任务成功，说明“不管上下文”本身没问题，失败是 provider 抖动。

---

## 5. 对 E3 实验口径的启示

- E3 的 token 指标要读 **per-iteration**（53,597 vs 82,292 = −35%，压缩确实赢），而不是 cumulative（optimized 累计反而更大，因为轮数多）。`bench_agent.py:_print_summary` 本来就标注 cumulative “scales with iters, noisy”，per-iter 才是正确读数。
- 对照组“无管理却成功”说明：单看 success_rate，压缩 ON/OFF 都可能 100%；只有在**超长轨迹溢出窗口**的任务上，压缩才会在成功率上体现差距。当前 10 个 long-horizon 任务都还不够长，所以 repeat=1 下 success_rate 主要由 provider 抖动决定（见 defense 那条），需要用 ≥5 reps 才有意义。
