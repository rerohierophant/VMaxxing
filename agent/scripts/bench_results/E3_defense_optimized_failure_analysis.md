# E3 — long-horizon-defense / optimized (rep0) 失败分析

- 运行记录：`bench_results/E3_compression.jsonl` 第 9 行
- run_dir：`agent/runs/20260806_131137_79_c45f12`
- 原始日志行：`[9/20] long-horizon-defense optimized rep0 ... 4061.19s  in_tok=947826  failed`

## 1. 失败根因：provider 流式连接被掐断（传输层抖动，非压缩逻辑）

`state.json`：
```json
{
  "status": "failed",
  "reason": "provider_stream_error provider=deepseek model=deepseek-v4-pro: RemoteProtocolError: peer closed connection without sending complete message body (incomplete chunked read)"
}
```

`trace.jsonl` 最后事件：
```json
{"type": "end", "iter": 22, "status": "error", "reason": "provider_stream_error ... (incomplete chunked read)", "iterations": 22}
```

关键事实：
- agent 已经跑完 **21 轮 LLM 调用**，且在第 21 轮成功调用了 `generate_backtest_config`（最终交付物：可回测选股条件已生成，返回 `status: ok`）。
- **第 22 轮**模型正在流式输出最终综合报告时，DeepSeek 把连接掐断了（`incomplete chunked read`）。
- 即：**死在终点线前一步，且是 provider 传输层抖动，不是 4 层压缩把任务搞挂。**

## 2. 它在 E3 整体结果里的位置（10 行汇总）

| task | config | wall_s | in_tok | status |
|---|---|---|---|---|
| semi | optimized | 660.96 | 1061850 | success |
| semi | degraded | 674.02 | 1401628 | success |
| newenergy | optimized | 393.80 | 823385 | success |
| newenergy | degraded | 337.43 | 1356240 | success |
| pharma | optimized | 421.92 | 875464 | success |
| pharma | degraded | 449.58 | 2137316 | success |
| solar | optimized | 353.05 | 844257 | success |
| solar | degraded | 460.76 | 1006279 | success |
| **defense** | **optimized** | **4061.19** | **947826** | **failed** |
| defense | degraded | 1442.24 | 1287073 | success |

- **上下文压缩效果（claim 的一半）：稳。** 5/5 任务压缩后累计 input token 全部下降 16%–59%（defense −26%）。这部分成立。
- **成功率（claim 的另一半）：被 1 次抖动污染。** optimized 4/5=80%（唯一失败就是这条），degraded 5/5=100%。但**唯一失败是 provider 流错误个例**，其余 4 个 optimized 全成功 → 不能据此说“压缩降低成功率”。

## 3. 4061s 是误导性数字，别当延迟证据

- 其余 4 个 optimized 运行全部在 **350–660s** 内完成。defense optimized 的 4061s 是 agent 跑满 22 轮 + 流错误重试致死，不是“压缩更慢”。
- 4061s 还 **超过 harness 自身的 `RUN_TIMEOUT_S=2400`**（超时应记 `status=timeout, rc=-1`，而非 `failed, rc=1`）。说明这次运行要么来自更早一次更大超时的 launch，要么 CLI 对流错误的内层重试把进程拖过了父进程超时才以 rc=1 退出。→ 不要把 4061 vs degraded 1442 当成“压缩慢 3 倍”的比较，它不是干净的延迟信号。

## 4. 次要噪声：行情数据源也在抖

trace 内可见东方财富(eastmoney)调用失败：
- iter1 `get_sector_info`：`ProxyError / RemoteDisconnected`（连接代理失败）
- iter15 `get_research_reports`（600893/600760/688568）：`400 Client Error`

agent 都优雅处理（工具层 `status=ok` 但 payload 内含 `ok:false`，已重试/跳过），没导致崩溃——但 long-horizon 任务上这类噪声会叠加，进一步放大单次运行的方差。

## 5. 结论与建议

1. **这次 `failed` 不应被解读为“压缩降低成功率”**——它是 1 次 DeepSeek 流式掉线的偶发事件，且失败时最终交付物其实已经生成。
2. **E3 当前用 `--repeat 1` 不具统计意义。** harness docstring 自己写“single sample is indistinguishable from noise”，默认 `repeat=2`；而成功率这种指标建议 ≥5 reps 才有可信分母。当前的 80% vs 100% 一个抖动就翻面。
3. **复跑 / 加固建议：**
   - 只重跑这条：删掉 `E3_compression.jsonl` 中 `long-horizon-defense optimized rep0` 那一行，再 `--resume` 就只重跑它（约 ~10–20min，取决于 provider）。
   - 更稳：把 `EXPERIMENTS["E3_compression"]["repeat"]` 从 2 提到 5，重新跑（会归档旧 jsonl），成功率才有意义。
   - 根治：给 `RemoteProtocolError`/`provider_stream_error` 加重试或兜底，避免一次掉线就判整个任务失败——benchmark 对 provider 抖动会更鲁棒。
4. **顺手核对**：`RUN_TIMEOUT_S=2400` 与实测 wall=4061(rc=1) 矛盾，建议确认是否早先 launch 用了更大超时，或 CLI 内层重试绕过了父进程超时。E3 指标是 tokens+completion（非延迟），不影响 headline，但影响任何未来的延迟旁读。
