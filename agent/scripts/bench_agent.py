"""Resume-experiment benchmark harness for VMaxxing agent loop.

Runs a FIXED prompt suite through the agent (non-interactive) and the swarm
runner, toggling one mechanism at a time via env flags, and reports the
end-to-end latency + prompt-token delta between the optimized (default) and
the degraded (flag=1) configuration. This is the data backbone for the
resume's quantitative claims.

Each experiment maps to one resume bullet:
  E1  VIBE_SERIAL_TOOLS  -> 只读工具并发降低端到端延迟
  E2  VIBE_EAGER_SKILLS  -> 渐进式披露降低 prompt token 开支
  E3  VIBE_NO_COMPRESS   -> 4层压缩降低累计上下文 + 提升任务成功率
  E4  VIBE_SWARM_SERIAL  -> Swarm 层内并发降低端到端延迟

Usage:
    # from repo root, with provider credentials in the environment.
    # IMPORTANT: use the project venv's interpreter -- the harness spawns
    # subprocesses with `sys.executable`, so a bare `python3` without the
    # project deps installed will fail every run.
    .venv/bin/python agent/scripts/bench_agent.py
    # scope to one experiment (prefix match, e.g. E3 -> E3_compression):
    .venv/bin/python agent/scripts/bench_agent.py --only E3
    # override repetitions (default is per-experiment, see EXPERIMENTS):
    .venv/bin/python agent/scripts/bench_agent.py --only E1 --repeat 5
    # resume an interrupted sweep (skips already-recorded runs):
    .venv/bin/python agent/scripts/bench_agent.py --resume
    # print the summary from existing results without running anything:
    .venv/bin/python agent/scripts/bench_agent.py --report-only

Why repetitions matter:
  LLM wall-clock varies ±10-20% run to run (provider queueing, network, data
  source latency). The concurrency/swarm speedups being measured are of a
  similar magnitude, so a single sample is indistinguishable from noise. Every
  metric here is therefore reported as a MEDIAN over `repeat` runs, with the
  min/max range printed so the spread is visible and defensible.

Reproducibility notes:
  * Pin the model + temperature in the env below (PIN_ENV). Default behavior
    uses whatever provider is configured; for comparable numbers, fix it.
  * Market-data sources (tushare/yfinance/...) are date-sensitive. If your
    data loaders accept an `as_of` date via env or prompt, pin it in PIN_ENV
    so every run fetches the same snapshot. Otherwise note the run date in the
    report.
  * Every individual run is appended to bench_results/<experiment>.jsonl the
    moment it finishes, so an interrupted sweep never loses completed work and
    the raw records stay auditable.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]  # .../agent
RUNS_DIR = AGENT_DIR / "runs"
SWARM_RUNS_DIR = AGENT_DIR / ".swarm" / "runs"
RESULTS_DIR = Path(__file__).resolve().parent / "bench_results"

# ---- Pin these so every A/B run is comparable --------------------------------
# Credentials are NOT set here: the child process resolves them itself via
# src/providers/llm.py::_ensure_dotenv(), which loads the first existing file in
# ~/.vmx/.env -> agent/.env -> $CWD/.env. We spawn with cwd=AGENT_DIR, so
# agent/.env is picked up automatically. Because that loader uses
# `override=False`, anything set here wins over the .env value -- which is
# exactly the point: the experiment must not drift with day-to-day .env edits.
#
# TIMEOUT_SECONDS / MAX_RETRIES are pinned deliberately:
#   - the everyday default (120s) is too tight for E3's degraded arm, where
#     disabling compression inflates the context and slows generation. A
#     transport timeout there would masquerade as "task failed to converge",
#     corrupting the very success-rate number E3 exists to measure.
#   - retries silently add whole timeout windows to wall-clock, which is the
#     metric E1/E4 report. Pinning keeps both arms structurally identical.
PIN_ENV: dict[str, str] = {
    "LANGCHAIN_MODEL_NAME": "deepseek-v4-pro",
    "LANGCHAIN_TEMPERATURE": "0",
    "TIMEOUT_SECONDS": "600",
    "MAX_RETRIES": "2",
}

# Per-run subprocess timeout. Long-horizon tasks with compression DISABLED can
# crawl, so this is deliberately generous; a timeout is recorded as a failure
# rather than aborting the sweep.
RUN_TIMEOUT_S = 2400

# ---- Fixed prompt suite -------------------------------------------------------
# `kind="agent"`  -> equivalent to `vmx -p "<prompt>"`
# `kind="swarm"`  -> equivalent to `vmx --swarm-run <preset> '{}'`
# The harness invokes `python -m cli` instead of the `vmx` console script so it
# keeps working whether or not the package is currently pip-installed.
#
# These prompts ARE the unit of measurement -- keep them STABLE across runs.
# Groups:
#   multi-source-*  : tool-parallelism heavy (several independent read-only
#                     fetches per turn) -> E1 latency, E2 token
#   long-horizon-*  : deliberately long, multi-stage research that blows past
#                     TOKEN_THRESHOLD without compression -> E3
#   swarm-*         : multi-agent DAG presets -> E4 latency
SUITE = [
    # -- multi-source (parallel read-only fetches) -----------------------------
    {
        "name": "multi-source-baijiu",
        "kind": "agent",
        "prompt": (
            "对比分析贵州茅台(600519)与五粮液(000858)过去一年：拉取两者周线行情、"
            "最新财报关键指标、近期券商研报观点，并各给出技术面与基本面结论。"
        ),
    },
    {
        "name": "multi-source-ev",
        "kind": "agent",
        "prompt": (
            "对比分析宁德时代(300750)与比亚迪(002594)过去一年：拉取两者周线行情、"
            "最新财报关键指标、近期新闻与券商观点，并各给出技术面与基本面结论。"
        ),
    },
    {
        "name": "multi-source-bank",
        "kind": "agent",
        "prompt": (
            "横向对比招商银行(600036)、兴业银行(601166)、宁波银行(002142)过去一年："
            "分别拉取周线行情、最新财报关键指标（净息差、不良率、ROE）与近期新闻，"
            "给出三者的估值与资产质量对比结论。"
        ),
    },
    # -- long-horizon (context-window stress, for E3) --------------------------
    {
        "name": "long-horizon-semi",
        "kind": "agent",
        "prompt": (
            "做一次完整的行业研究：先扫描半导体板块全部成分股的近一年行情与估值分位，"
            "再对营收增速top5的个股逐一拉取财报与新闻，汇总产业链上下游关系，"
            "最后给出一份带风险提示的研究报告并附上一组可回测的选股条件。"
        ),
    },
    {
        "name": "long-horizon-newenergy",
        "kind": "agent",
        "prompt": (
            "做一次完整的行业研究：先扫描新能源车板块全部成分股的近一年行情与估值分位，"
            "再对营收增速top5的个股逐一拉取财报与新闻，梳理电池-整车-充电桩产业链上下游关系，"
            "最后给出一份带风险提示的研究报告并附上一组可回测的选股条件。"
        ),
    },
    {
        "name": "long-horizon-pharma",
        "kind": "agent",
        "prompt": (
            "做一次完整的行业研究：先扫描医药生物板块全部成分股的近一年行情与估值分位，"
            "再对研发投入占比top5的个股逐一拉取财报与新闻，梳理创新药-CXO-医疗器械子赛道格局，"
            "最后给出一份带风险提示的研究报告并附上一组可回测的选股条件。"
        ),
    },
    {
        "name": "long-horizon-solar",
        "kind": "agent",
        "prompt": (
            "做一次完整的行业研究：先扫描光伏板块全部成分股的近一年行情与估值分位，"
            "再对毛利率top5的个股逐一拉取财报与新闻，梳理硅料-硅片-电池片-组件产业链上下游，"
            "最后给出一份带风险提示的研究报告并附上一组可回测的选股条件。"
        ),
    },
    {
        "name": "long-horizon-defense",
        "kind": "agent",
        "prompt": (
            "做一次完整的行业研究：先扫描国防军工板块全部成分股的近一年行情与估值分位，"
            "再对订单增速较快的top5个股逐一拉取财报与新闻，梳理主机厂-分系统-原材料产业链，"
            "最后给出一份带风险提示的研究报告并附上一组可回测的选股条件。"
        ),
    },
    {
        "name": "long-horizon-consumer-elec",
        "kind": "agent",
        "prompt": (
            "做一次完整的行业研究：先扫描消费电子板块全部成分股的近一年行情与估值分位，"
            "再对营收增速top5的个股逐一拉取财报与新闻，梳理零部件-代工-品牌产业链上下游关系，"
            "最后给出一份带风险提示的研究报告并附上一组可回测的选股条件。"
        ),
    },
    {
        "name": "long-horizon-broker",
        "kind": "agent",
        "prompt": (
            "做一次完整的行业研究：先扫描券商板块全部成分股的近一年行情与估值分位，"
            "再对ROE top5的个股逐一拉取财报与新闻，对比经纪-自营-资管三块业务结构差异，"
            "最后给出一份带风险提示的研究报告并附上一组可回测的选股条件。"
        ),
    },
    {
        "name": "long-horizon-property",
        "kind": "agent",
        "prompt": (
            "做一次完整的行业研究：先扫描房地产板块全部成分股的近一年行情与估值分位，"
            "再对现金流较健康的top5个股逐一拉取财报与新闻，梳理开发-物业-建材产业链风险传导，"
            "最后给出一份带风险提示的研究报告并附上一组可回测的选股条件。"
        ),
    },
    {
        "name": "long-horizon-food",
        "kind": "agent",
        "prompt": (
            "做一次完整的行业研究：先扫描食品饮料板块全部成分股的近一年行情与估值分位，"
            "再对净利率top5的个股逐一拉取财报与新闻，梳理白酒-乳制品-调味品子赛道竞争格局，"
            "最后给出一份带风险提示的研究报告并附上一组可回测的选股条件。"
        ),
    },
    {
        "name": "long-horizon-machinery",
        "kind": "agent",
        "prompt": (
            "做一次完整的行业研究：先扫描机械设备板块全部成分股的近一年行情与估值分位，"
            "再对营收增速top5的个股逐一拉取财报与新闻，梳理工程机械-通用设备-自动化子赛道，"
            "最后给出一份带风险提示的研究报告并附上一组可回测的选股条件。"
        ),
    },
    # -- swarm presets (multi-agent DAG, for E4) -------------------------------
    {"name": "swarm-fundamental", "kind": "swarm", "preset": "fundamental_research_team"},
    {"name": "swarm-sentiment", "kind": "swarm", "preset": "sentiment_intelligence_team"},
    {"name": "swarm-quant", "kind": "swarm", "preset": "quant_strategy_desk"},
]

LONG_HORIZON = [t["name"] for t in SUITE if t["name"].startswith("long-horizon-")]
MULTI_SOURCE = [t["name"] for t in SUITE if t["name"].startswith("multi-source-")]
SWARM_TASKS = [t["name"] for t in SUITE if t["kind"] == "swarm"]

EXPERIMENTS = {
    # flag    : which env toggle isolates the mechanism
    # tasks   : suite task names this experiment runs on
    # metric  : what should improve in the OPTIMIZED (flag unset) config
    # repeat  : default repetitions per (task, config); median is reported
    "E1_concurrency": {
        "flag": "VIBE_SERIAL_TOOLS",
        "tasks": MULTI_SOURCE,
        "metric": "latency",
        "repeat": 3,  # latency is noisy -> need a median
    },
    "E2_progressive_disclosure": {
        "flag": "VIBE_EAGER_SKILLS",
        # All 87 skills inlined = ~264k tokens of system prompt, which blows past
        # the model context window on iteration 1 -- the degraded arm would fail
        # to run at all rather than produce a comparable number. Inline the first
        # 10 skills (~30k tok) so the arm stays runnable and the delta is real.
        # The "you cannot inline all of them" finding is measured statically by
        # scripts/measure_disclosure.py instead.
        "flag_value": "10",
        "tasks": MULTI_SOURCE,
        "metric": "tokens",
        "repeat": 2,  # prompt-token delta is structural -> low variance
    },
    "E3_compression": {
        "flag": "VIBE_NO_COMPRESS",
        "tasks": LONG_HORIZON,  # 10 tasks -> success rate has a real denominator
        "metric": "tokens+completion",
        "repeat": 2,
    },
    "E4_swarm": {
        "flag": "VIBE_SWARM_SERIAL",
        "tasks": SWARM_TASKS,
        "metric": "latency",
        "repeat": 3,
    },
}


# ---- result persistence -------------------------------------------------------


def _results_path(experiment: str) -> Path:
    return RESULTS_DIR / f"{experiment}.jsonl"


def _load_results(experiment: str) -> list[dict]:
    p = _results_path(experiment)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue  # tolerate a partially-written trailing line
    # Backfill tool timing for records written before it was collected. The raw
    # run directories are still on disk, so no re-run is needed.
    for r in out:
        if "tool_wall_s" not in r and r.get("run_dir"):
            rd = Path(r["run_dir"])
            if rd.exists():
                r.update(_read_tool_timing(rd))
    return out


def _append_result(experiment: str, record: dict) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(_results_path(experiment), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


# ---- run-artifact readers -----------------------------------------------------


def _newest_run(before: float, base: Path, expect_prompt: str | None = None) -> Path | None:
    """Locate the run directory produced by the subprocess we just launched.

    mtime alone can mis-attribute if anything else writes to runs/, so when the
    task is a plain agent prompt we additionally match req.json's prompt.
    """
    if not base.exists():
        return None
    cands = [p for p in base.iterdir() if p.is_dir() and p.stat().st_mtime > before]
    if not cands:
        return None
    if expect_prompt:
        matched = []
        for p in cands:
            rq = p / "req.json"
            if not rq.exists():
                continue
            try:
                if json.loads(rq.read_text()).get("prompt") == expect_prompt:
                    matched.append(p)
            except Exception:
                continue
        if matched:
            cands = matched
    return max(cands, key=lambda p: p.stat().st_mtime)


def _read_usage(run_dir: Path | None) -> dict:
    """Best-effort extraction of token totals from a run directory.

    Also derives a *per-iteration* prompt size. Cumulative `input_tokens` scales
    with how many iterations the model happened to take, and E1 showed that
    trajectory length drifts ~40% between otherwise identical runs -- so the
    cumulative figure cannot isolate a prompt-construction mechanism. The
    per-iteration median is invariant to trajectory length and is the correct
    metric for E2.
    """
    if not run_dir:
        return {}
    # agent: runs/<ts>/llm_usage.json  (per_iteration + totals)
    for jf in run_dir.rglob("llm_usage.json"):
        try:
            d = json.loads(jf.read_text())
            out = dict(d.get("totals", {}))
            per = [i.get("input_tokens", 0) for i in d.get("per_iteration", [])]
            if per:
                out["iters_billed"] = len(per)
                out["in_tok_per_iter"] = _median(per)
                # First call carries the system prompt with the least history
                # attached, so it is the cleanest read on prompt construction.
                out["in_tok_first_iter"] = per[0]
            return out
        except Exception:
            pass
    # swarm: serialized run.json with token accounting
    for jf in run_dir.rglob("run.json"):
        try:
            d = json.loads(jf.read_text())
            if "totals" in d:
                return d["totals"]
            if "input_tokens" in d:
                return {"input_tokens": d["input_tokens"], "output_tokens": d.get("output_tokens", 0)}
        except Exception:
            pass
    return {}


def _read_tool_timing(run_dir: Path | None) -> dict:
    """Per-run tool-execution timing, reconstructed from trace.jsonl.

    Why this exists
    ---------------
    Cross-run wall-clock is NOT a usable signal for the concurrency experiment.
    An LLM agent takes a different trajectory on every run (different iteration
    count, different tool calls), so the between-run variance swamps the effect
    being measured -- the degraded arm can easily finish *faster* simply because
    it happened to converge in fewer iterations.

    The fix is a within-run counterfactual. Every `tool_result` event carries
    `elapsed_ms`, and its `ts` is the completion time, so each call's start is
    `ts - elapsed`. For every batch (tool results sharing an iteration):

        tool_wall   = max(end) - min(start)   <- what actually happened
        tool_serial = sum(elapsed)            <- what serial execution would cost

    Both come from the SAME run, i.e. the same trajectory, so the difference is
    attributable to concurrency alone with zero trajectory noise.

    `tool_wall / tool_serial` also doubles as a flag-integrity check: the
    degraded arm must show saved_s ~= 0, since serial execution cannot overlap.
    """
    empty = {
        "iterations": 0, "tool_calls": 0,
        "tool_wall_s": 0.0, "tool_serial_s": 0.0, "tool_saved_s": 0.0,
        "parallel_batches": 0,
    }
    if not run_dir:
        return empty
    trace = run_dir / "trace.jsonl"
    if not trace.exists():
        return empty  # swarm runs and older layouts simply have no trace

    batches: dict[int, list[tuple[float, float]]] = {}
    iterations = 0
    try:
        for line in trace.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            it = int(e.get("iter", 0) or 0)
            iterations = max(iterations, it)
            if e.get("type") != "tool_result":
                continue
            end = float(e.get("ts", 0) or 0)
            elapsed = float(e.get("elapsed_ms", 0) or 0) / 1000.0
            batches.setdefault(it, []).append((end - elapsed, end))
    except Exception:
        return empty

    tool_wall = tool_serial = 0.0
    calls = par_batches = 0
    for spans in batches.values():
        calls += len(spans)
        tool_serial += sum(e - s for s, e in spans)
        tool_wall += max(e for _, e in spans) - min(s for s, _ in spans)
        if len(spans) > 1:
            par_batches += 1
    return {
        "iterations": iterations,
        "tool_calls": calls,
        "tool_wall_s": round(tool_wall, 2),
        "tool_serial_s": round(tool_serial, 2),
        "tool_saved_s": round(tool_serial - tool_wall, 2),
        "parallel_batches": par_batches,
    }


def _read_swarm_timing(run_dir: Path | None) -> dict:
    """Per-run swarm layer-parallelism timing, read from swarm_timing.jsonl.

    Mirrors _read_tool_timing but at the DAG-layer granularity: each line is one
    layer's parallel wall (max task end - min task start) vs serial wall (sum of
    per-task durations). This is the within-run counterfactual for E4 -- swarm
    runs have no trace.jsonl, so the tool-level reader returns empty for them.

    Returns the SAME field names as _read_tool_timing (tool_wall_s / tool_serial_s
    / tool_saved_s) so the existing E4 aggregation and report path applies unchanged.
    """
    empty = {"tool_wall_s": 0.0, "tool_serial_s": 0.0, "tool_saved_s": 0.0}
    if not run_dir:
        return empty
    path = run_dir / "swarm_timing.jsonl"
    if not path.exists():
        return empty
    try:
        wall = serial = saved = 0.0
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            wall += float(d.get("wall_s", 0.0))
            serial += float(d.get("serial_s", 0.0))
            saved += float(d.get("saved_s", 0.0))
        return {
            "tool_wall_s": round(wall, 2),
            "tool_serial_s": round(serial, 2),
            "tool_saved_s": round(saved, 2),
        }
    except Exception:
        return empty


def _read_status(run_dir: Path | None, returncode: int) -> str:
    """Terminal status of a run.

    Source of truth is state.json, written by core/state.py mark_success /
    mark_failure for EVERY agent run:
        {"status": "success"} | {"status": "failed", "reason": ...}
    (run_card.json is NOT usable here -- it is only emitted by backtest-style
    runs, so most runs simply have none.)
    Swarm runs have no state.json, so they fall back to the exit code.
    """
    if returncode != 0:
        return "failed"
    if not run_dir:
        return "no-run"
    sf = run_dir / "state.json"
    if sf.exists():
        try:
            return str(json.loads(sf.read_text()).get("status", "unknown"))
        except Exception:
            return "unreadable"
    return "success"


def _cmd(task: dict) -> list[str]:
    # `-m cli` (not `-m cli.main`): cli/__init__.py already imports cli.main,
    # so `-m cli.main` triggers a double-import RuntimeWarning that pollutes
    # captured output. cli/__main__.py is the clean entrypoint.
    if task["kind"] == "swarm":
        return [sys.executable, "-m", "cli", "--swarm-run", task["preset"], "{}"]
    return [sys.executable, "-m", "cli", "-p", task["prompt"]]


# Runs inside the child, under the exact env a benchmark run gets, and reports
# which .env actually won. Credentials are never echoed -- only the key length,
# which is enough to tell "configured" from "missing" without leaking anything.
_PROBE = """
import os
from src.providers import llm as L
loaded = next((p for p in L._ENV_CANDIDATES if p.exists()), None)
L._ensure_dotenv()
cfg = L.get_env_config()
key = os.getenv("OPENAI_API_KEY") or ""
print("dotenv_file=" + (str(loaded) if loaded else "NONE"))
print("provider=" + str(cfg.llm.langchain_provider))
print("model=" + str(cfg.llm.langchain_model_name or "(unset)"))
print("base_url=" + (os.getenv("OPENAI_BASE_URL") or "(provider default)"))
print("api_key=" + ("set(len=%d)" % len(key) if key else "MISSING"))
print("timeout_s=" + str(cfg.llm.timeout_seconds))
print("max_retries=" + str(cfg.llm.max_retries))
"""


def _preflight() -> None:
    """Fail fast on a wrong interpreter or unresolvable credentials.

    An 88-run sweep takes hours; discovering a missing API key on run #1 (or
    worse, silently benchmarking a different model than intended) is expensive.
    """
    proc = subprocess.run(
        [sys.executable, "-c", "import cli"],
        cwd=str(AGENT_DIR),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(
            f"[preflight] `import cli` failed under {sys.executable}\n"
            f"{proc.stderr.strip()[-400:]}\n\n"
            "Run this harness with the project venv interpreter, e.g.\n"
            "  .venv/bin/python agent/scripts/bench_agent.py\n"
        )
        raise SystemExit(2)

    # Probe with the same env the real runs get, so PIN_ENV overrides are visible.
    env = dict(os.environ)
    env.update(PIN_ENV)
    probe = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=str(AGENT_DIR),
        env=env,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        sys.stderr.write(
            "[preflight] could not resolve provider config:\n"
            f"{probe.stderr.strip()[-600:]}\n"
        )
        raise SystemExit(2)

    fields = dict(
        line.split("=", 1)
        for line in probe.stdout.strip().splitlines()
        if "=" in line
    )
    print("  [preflight] resolved provider config (child process)")
    for k in ("dotenv_file", "provider", "model", "base_url",
              "api_key", "timeout_s", "max_retries"):
        print(f"    {k:<13} {fields.get(k, '?')}")
    pinned = ", ".join(f"{k}={v}" for k, v in PIN_ENV.items())
    print(f"    {'pinned':<13} {pinned}")

    if fields.get("api_key") == "MISSING":
        sys.stderr.write(
            "\n[preflight] OPENAI_API_KEY is not set for the child process.\n"
            "Add it to agent/.env (or ~/.vmx/.env, which takes precedence).\n"
        )
        raise SystemExit(2)
    if fields.get("dotenv_file") == "NONE":
        sys.stderr.write("\n[preflight] no .env file found on any candidate path.\n")
        raise SystemExit(2)
    print()


def _run_once(task: dict, flag: str, flag_on: bool, flag_value: str = "1") -> dict:
    env = dict(os.environ)
    env.update(PIN_ENV)
    if flag_on:
        env[flag] = flag_value
    else:
        env.pop(flag, None)

    base = RUNS_DIR if task["kind"] == "agent" else SWARM_RUNS_DIR
    before = time.time()
    timed_out = False
    try:
        proc = subprocess.run(
            _cmd(task),
            env=env,
            cwd=str(AGENT_DIR),
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_S,
        )
        returncode = proc.returncode
    except subprocess.TimeoutExpired:
        # A timeout is a legitimate outcome (esp. E3 degraded) -- record it as a
        # failure instead of killing the whole sweep.
        timed_out = True
        returncode = -1
    wall = time.time() - before
    rd = _newest_run(before, base, task.get("prompt"))
    usage = _read_usage(rd)
    return {
        "wall_s": round(wall, 2),
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "in_tok_per_iter": usage.get("in_tok_per_iter", 0),
        "in_tok_first_iter": usage.get("in_tok_first_iter", 0),
        "iters_billed": usage.get("iters_billed", 0),
        "status": "timeout" if timed_out else _read_status(rd, returncode),
        "returncode": returncode,
        "run_dir": str(rd) if rd else None,
        **(_read_swarm_timing(rd) if task["kind"] == "swarm" else _read_tool_timing(rd)),
    }


# ---- aggregation --------------------------------------------------------------


def _median(vals: list[float]) -> float:
    return round(statistics.median(vals), 2) if vals else 0.0


def _pct(base: float, variant: float) -> str:
    """Percent improvement of `base` (optimized) relative to `variant` (degraded)."""
    if variant == 0:
        return "n/a"
    drop = (variant - base) / variant * 100.0
    return f"-{drop:.1f}%" if drop >= 0 else f"+{abs(drop):.1f}%"


_EMPTY_AGG = {
    "n": 0, "wall_median": 0.0, "wall_min": 0.0, "wall_max": 0.0,
    "tok_median": 0.0, "success": 0, "success_rate": 0.0,
    "tok_per_iter_median": 0.0, "tok_first_iter_median": 0.0,
    "iter_median": 0.0, "calls_median": 0.0,
    "tool_wall_sum": 0.0, "tool_serial_sum": 0.0, "tool_saved_sum": 0.0,
    "wall_sum": 0.0,
}


def _agg(records: list[dict], config: str, task: str | None = None) -> dict:
    rs = [r for r in records if r["config"] == config and (task is None or r["task"] == task)]
    if not rs:
        return dict(_EMPTY_AGG)  # full shape, so callers can index unconditionally
    walls = [r["wall_s"] for r in rs]
    # Token totals are only meaningful for runs that actually reported usage.
    toks = [r["input_tokens"] for r in rs if r["input_tokens"]]
    ok = sum(1 for r in rs if r["status"] == "success")
    iters = [r["iterations"] for r in rs if r.get("iterations")]
    calls = [r["tool_calls"] for r in rs if r.get("tool_calls")]
    return {
        "n": len(rs),
        "wall_median": _median(walls),
        "wall_min": round(min(walls), 2),
        "wall_max": round(max(walls), 2),
        "tok_median": _median(toks),
        # Trajectory-invariant prompt size -- the correct metric for E2.
        "tok_per_iter_median": _median([r["in_tok_per_iter"] for r in rs
                                        if r.get("in_tok_per_iter")]),
        "tok_first_iter_median": _median([r["in_tok_first_iter"] for r in rs
                                          if r.get("in_tok_first_iter")]),
        "success": ok,
        "success_rate": round(ok / len(rs) * 100.0, 1),
        # Trajectory shape -- exposes whether the two arms are even comparable.
        "iter_median": _median(iters),
        "calls_median": _median(calls),
        # Summed (not median) because the counterfactual is a pooled ratio.
        "tool_wall_sum": round(sum(r.get("tool_wall_s", 0.0) for r in rs), 2),
        "tool_serial_sum": round(sum(r.get("tool_serial_s", 0.0) for r in rs), 2),
        "tool_saved_sum": round(sum(r.get("tool_saved_s", 0.0) for r in rs), 2),
        "wall_sum": round(sum(walls), 2),
    }


def _print_counterfactual(opt: dict, deg: dict) -> None:
    """Within-run concurrency effect -- the number that is actually defensible.

    Cross-run wall-clock compares two different trajectories and is therefore
    dominated by noise; this compares each run against itself.
    """
    if not opt["tool_serial_sum"]:
        print("  (no trace timing available -- counterfactual skipped)")
        return

    # Sanity gate: serial execution cannot overlap, so the degraded arm must
    # show ~zero savings. A non-trivial value means the toggle did not apply.
    if deg["n"] and deg["tool_serial_sum"]:
        deg_ratio = deg["tool_saved_sum"] / deg["tool_serial_sum"] * 100.0
        verdict = "OK" if deg_ratio < 5.0 else "!! FLAG MAY NOT BE APPLIED"
        print(f"  flag-check: degraded overlap = {deg_ratio:.1f}% of tool time  [{verdict}]")

    saved = opt["tool_saved_sum"]
    ser, par = opt["tool_serial_sum"], opt["tool_wall_sum"]
    wall = opt["wall_sum"]
    llm_share = (wall - par) / wall * 100.0 if wall else 0.0
    print(f"  parallelizable phase (within-run): {ser}s -> {par}s  ({_pct(par, ser)})   <-- USE THIS")
    print(f"  projected end-to-end   : {round(wall + saved, 2)}s -> {wall}s  "
          f"({_pct(wall, wall + saved)})")
    print(f"  non-parallelizable (LLM) time = {llm_share:.1f}% of wall  "
          f"(Amdahl ceiling on end-to-end gain)")

    # Trajectory comparability -- if these diverge, cross-run wall-clock is junk.
    if deg["n"] and opt["iter_median"] and deg["iter_median"]:
        print(f"  trajectory: iters {opt['iter_median']} vs {deg['iter_median']}, "
              f"tool calls {opt['calls_median']} vs {deg['calls_median']} (opt vs deg)")
        drift = abs(opt["iter_median"] - deg["iter_median"]) / max(opt["iter_median"], 1) * 100
        if drift > 20:
            print(f"              -> {drift:.0f}% iteration drift; cross-run latency NOT comparable")


def _print_summary(name: str, cfg: dict, records: list[dict]) -> None:
    metric = cfg["metric"]
    print(f"\n{'=' * 78}")
    print(f"  {name}   (toggle {cfg['flag']}, metric={metric})")
    print(f"{'=' * 78}")

    if not records:
        print("  (no results yet)")
        return

    # per-task breakdown
    tasks = [t for t in cfg["tasks"] if any(r["task"] == t for r in records)]
    print(f"\n  {'task':<30} {'config':<10} {'n':>3} {'wall_med':>9} {'range':>19} "
          f"{'in_tok_med':>12} {'ok':>7}")
    print("  " + "-" * 92)
    for t in tasks:
        for config in ("optimized", "degraded"):
            a = _agg(records, config, t)
            if not a["n"]:
                continue
            rng = f"{a['wall_min']}-{a['wall_max']}"
            ok = f"{a['success']}/{a['n']}"
            print(
                f"  {t:<30} {config:<10} {a['n']:>3} {a['wall_median']:>9} {rng:>19} "
                f"{int(a['tok_median']):>12} {ok:>7}"
            )

    # pooled headline numbers -- these are what goes on the resume
    opt = _agg(records, "optimized")
    deg = _agg(records, "degraded")
    print("\n  ---- POOLED (all tasks) ----")
    print(f"  optimized: n={opt['n']}  wall_median={opt['wall_median']}s  "
          f"in_tok_median={int(opt['tok_median'])}  "
          f"success={opt['success']}/{opt['n']} ({opt['success_rate']}%)")
    print(f"  degraded : n={deg['n']}  wall_median={deg['wall_median']}s  "
          f"in_tok_median={int(deg['tok_median'])}  "
          f"success={deg['success']}/{deg['n']} ({deg['success_rate']}%)")

    if not opt["n"] or not deg["n"]:
        print("  (need both configs to compute a delta)")
        return

    print("\n  ---- HEADLINE ----")
    if "latency" in metric:
        print(f"  latency : {deg['wall_median']}s -> {opt['wall_median']}s  "
              f"({_pct(opt['wall_median'], deg['wall_median'])})  [cross-run, noisy]")
        _print_counterfactual(opt, deg)
    if "tokens" in metric:
        # Cumulative tokens scale with trajectory length, which drifts run to
        # run; the per-iteration figure isolates prompt construction itself.
        print(f"  in_token cumulative : {int(deg['tok_median'])} -> {int(opt['tok_median'])}  "
              f"({_pct(opt['tok_median'], deg['tok_median'])})  [scales with iters, noisy]")
        if opt["tok_per_iter_median"] and deg["tok_per_iter_median"]:
            print(f"  in_token PER ITER   : {int(deg['tok_per_iter_median'])} -> "
                  f"{int(opt['tok_per_iter_median'])}  "
                  f"({_pct(opt['tok_per_iter_median'], deg['tok_per_iter_median'])})   <-- USE THIS")
            print(f"  first-call prompt   : {int(deg['tok_first_iter_median'])} -> "
                  f"{int(opt['tok_first_iter_median'])}  "
                  f"({_pct(opt['tok_first_iter_median'], deg['tok_first_iter_median'])})"
                  "  [least history attached]")
            print("  static ceiling      : run scripts/measure_disclosure.py for the "
                  "zero-noise all-87-skills figure")
    if "completion" in metric:
        print(f"  success : {deg['success_rate']}% -> {opt['success_rate']}%  "
              f"(degraded {deg['success']}/{deg['n']}, optimized {opt['success']}/{opt['n']})")


# ---- driver -------------------------------------------------------------------


def _run_experiment(name: str, cfg: dict, repeat: int, resume: bool) -> None:
    flag = cfg["flag"]
    flag_value = cfg.get("flag_value", "1")
    done = _load_results(name) if resume else []
    if not resume and _results_path(name).exists():
        # Fresh sweep: archive the old file rather than silently mixing runs
        # from different code versions into one dataset.
        old = _results_path(name)
        old.rename(old.with_suffix(f".jsonl.bak.{int(time.time())}"))

    def already(task: str, config: str, i: int) -> bool:
        return any(r["task"] == task and r["config"] == config and r["rep"] == i for r in done)

    total = len(cfg["tasks"]) * 2 * repeat
    n = 0
    print(f"\n### {name}: {len(cfg['tasks'])} tasks x 2 configs x {repeat} reps = {total} runs")

    for task_name in cfg["tasks"]:
        task = next(t for t in SUITE if t["name"] == task_name)
        for i in range(repeat):
            for config, flag_on in (("optimized", False), ("degraded", True)):
                n += 1
                if already(task_name, config, i):
                    print(f"  [{n}/{total}] {task_name} {config} rep{i} -- skipped (resume)")
                    continue
                print(f"  [{n}/{total}] {task_name} {config} rep{i} ... ", end="", flush=True)
                r = _run_once(task, flag, flag_on=flag_on, flag_value=flag_value)
                record = {
                    "experiment": name,
                    "task": task_name,
                    "config": config,
                    "rep": i,
                    "flag": flag,
                    "flag_on": flag_on,
                    "flag_value": flag_value if flag_on else None,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    **r,
                }
                _append_result(name, record)
                done.append(record)
                print(f"{r['wall_s']}s  in_tok={r['input_tokens']}  {r['status']}")

    _print_summary(name, cfg, _load_results(name))


def main() -> int:
    ap = argparse.ArgumentParser(description="VMaxxing resume-experiment harness")
    ap.add_argument(
        "--only",
        help="Run a single experiment by id or prefix, e.g. E3 or E3_compression",
    )
    ap.add_argument(
        "--repeat",
        type=int,
        default=None,
        help="Repetitions per (task, config). Overrides the per-experiment default. "
             "Metrics are reported as the median across repetitions.",
    )
    ap.add_argument(
        "--resume",
        action="store_true",
        help="Skip (task, config, rep) combinations already present in bench_results/.",
    )
    ap.add_argument(
        "--report-only",
        action="store_true",
        help="Re-print summaries from existing bench_results/ without running anything.",
    )
    args = ap.parse_args()

    if args.only:
        selected = {k: v for k, v in EXPERIMENTS.items() if k.startswith(args.only)}
        if not selected:
            sys.stderr.write(
                f"[error] no experiment matches {args.only!r}. "
                f"Available: {', '.join(EXPERIMENTS)}\n"
            )
            return 2
    else:
        selected = EXPERIMENTS

    if args.report_only:
        for name, cfg in selected.items():
            _print_summary(name, cfg, _load_results(name))
        return 0

    _preflight()
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    grand = sum(
        len(cfg["tasks"]) * 2 * (args.repeat or cfg["repeat"]) for cfg in selected.values()
    )
    print(f"Planned: {grand} agent runs across {len(selected)} experiment(s).")
    print(f"Results stream to {RESULTS_DIR}/<experiment>.jsonl (safe to Ctrl-C, "
          f"resume with --resume).")

    for name, cfg in selected.items():
        _run_experiment(name, cfg, args.repeat or cfg["repeat"], args.resume)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
