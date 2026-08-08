#!/usr/bin/env python3
"""Static measurement of the progressive-disclosure mechanism (experiment E2).

Redesigned comparison (per design review 2026-08-06)
----------------------------------------------------
The previous version compared "summary-only" against "inline ALL skill bodies"
(VIBE_EAGER_SKILLS=all). That control is a strawman: no real system inlines 87
full SKILL.md docs, and at ~215% of the context window it cannot even execute.

The honest, runnable comparison is at the *summary-selection* level:

  - Baseline  (full disclosure): every turn injects ALL 87 skill summaries.
                                  This is the agent's current default behaviour.
  - Treatment (progressive)    : every turn injects only the task-relevant
                                  subset of summaries; full bodies and the rest
                                  are pulled on demand via the load_skill tool.

Both arms fit comfortably in the window, so this is a fair head-to-head. The
per-iteration token delta measures the real saving of *selective* disclosure.

A secondary, clearly-labelled feasibility note reports that inlining full
BODIES does not fit the window at all (~215%), which makes on-demand body
loading a *requirement*, not an optimization. That is supporting context, not
the headline.

The relevant-subset retriever mirrors src/memory/persistent.py:find_relevant
(keyword set-overlap: meta_hits*2.0 + body_hits*1.0) so the modelled
progressive arm uses the same scoring the agent would at runtime.

The progressive arm is a *proposed* selective-injection mode (it requires a
small context.py change to actually inject only relevant summaries at runtime).
This script measures both arms statically; it makes no runtime changes.

Usage:
    .venv/bin/python agent/scripts/measure_disclosure.py
    .venv/bin/python agent/scripts/measure_disclosure.py --context-window 65536
    .venv/bin/python agent/scripts/measure_disclosure.py --top-k 12
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_DIR))

# --------------------------------------------------------------------------
# Tokenizer -- mirrors src/memory/persistent.py EXACTLY so the modelled
# progressive arm uses the agent's real recall scoring.
# --------------------------------------------------------------------------
_NON_LATIN_SCRIPT_RANGES = (
    "一-鿿"   # CJK Unified Ideographs   (U+4E00-U+9FFF)
    "㐀-䶿"   # CJK Extension A          (U+3400-U+4DBF)
    "฀-๿"   # Thai                     (U+0E00-U+0E7F)
    "ؠ-ي"   # Arabic letters           (U+0620-U+064A)
    "א-ת"   # Hebrew letters           (U+05D0-U+05EA)
    "Ѐ-ӿ"   # Cyrillic                 (U+0400-U+04FF)
)
_TOKEN_RE = re.compile(rf"[a-zA-Z0-9]{{3,}}|[{_NON_LATIN_SCRIPT_RANGES}]")


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


# --------------------------------------------------------------------------
# Skills loading: prefer the real SkillsLoader (also picks up user skills at
# ~/.vmx/skills/user), fall back to a self-contained SKILL.md parser so the
# script runs even without the full package / pydantic installed.
# --------------------------------------------------------------------------
_CATEGORY_ORDER = [
    "data-source", "strategy", "analysis", "asset-class",
    "crypto", "flow", "tool", "other",
]


def _load_skills() -> list[dict]:
    try:
        from src.agent.skills import SkillsLoader

        loader = SkillsLoader()
        return [
            {"name": s.name, "category": s.category,
             "description": s.description, "body": s.body}
            for s in loader.skills
        ]
    except Exception:
        return _load_skills_fallback()


def _load_skills_fallback() -> list[dict]:
    skills_dir = AGENT_DIR / "src" / "skills"
    out: list[dict] = []
    if not skills_dir.exists():
        return out
    for f in sorted(skills_dir.rglob("SKILL.md")):
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"^---\s*\n(.*?)\n---", text, re.S)
        if not m:
            continue
        fm = m.group(1)

        def _field(key: str) -> str:
            mm = re.search(rf"^{key}:\s*(.+)$", fm, re.M)
            return mm.group(1).strip() if mm else ""

        out.append({
            "name": _field("name"),
            "category": _field("category") or "other",
            "description": _field("description"),
            "body": text[m.end():],
        })
    return out


def _format_summaries(skills: list[dict]) -> str:
    """Render skill summaries exactly like SkillsLoader.get_descriptions()."""
    groups: dict[str, list[dict]] = {}
    for s in skills:
        groups.setdefault(s["category"], []).append(s)
    ordered = [c for c in _CATEGORY_ORDER if c in groups]
    ordered += [c for c in sorted(groups) if c not in _CATEGORY_ORDER]
    lines: list[str] = []
    for cat in ordered:
        lines.append(f"\n### {cat}")
        for s in groups[cat]:
            lines.append(f"  - {s['name']}: {s['description']}")
    return "\n".join(lines)


def _relevant_subset(skills: list[dict], query: str, top_k: int = 0) -> list[dict]:
    """Pick task-relevant skills by keyword overlap (mirrors find_relevant)."""
    q = _tokenize(query)
    if not q:
        return []
    scored: list[tuple[float, dict]] = []
    for s in skills:
        meta = _tokenize(f"{s['name']} {s['description']}")
        body = _tokenize(s["body"])
        score = len(q & meta) * 2.0 + len(q & body) * 1.0
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    picked = scored[:top_k] if top_k > 0 else scored
    return [s for _, s in picked]


# --------------------------------------------------------------------------
# Representative benchmark queries -- mirror the bench_agent.py suites so the
# numbers are meaningful for the resume bullets.
# --------------------------------------------------------------------------
_QUERIES: list[tuple[str, str]] = [
    ("multi-source equity", "对比贵州茅台和五粮液的估值、基本面与投资价值"),
    ("long-horizon sector", "半导体行业长期投资前景与产业链研究"),
    ("crypto",             "比特币和以太坊的链上数据、持仓与情绪分析"),
    ("strategy/backtest",  "回测一个双均线交叉策略并评估夏普比率与最大回撤"),
    ("macro/asset-class",  "美联储加息对港股和新兴市场资产的影响"),
    ("flow/data-source",   "获取北向资金每日流向与行业分布数据"),
]


def _encoder():
    """Return a token counter. Prefer tiktoken; degrade to a char heuristic."""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return (lambda s: len(enc.encode(s))), "tiktoken/cl100k_base"
    except Exception:
        return (lambda s: len(s) // 3), "heuristic chars/3 (tiktoken unavailable)"


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure progressive disclosure statically")
    ap.add_argument("--context-window", type=int, default=131_072,
                    help="Model context window in tokens (default 128k)")
    ap.add_argument("--top-k", type=int, default=0,
                    help="Cap the progressive arm at K skills (0 = all relevant, score>0)")
    ap.add_argument("--eager-n", type=int, default=10,
                    help="Secondary note: also report inlining the first N full bodies")
    args = ap.parse_args()

    count, tokenizer = _encoder()
    skills = _load_skills()
    n_skills = len(skills)
    win = args.context_window

    full_block = _format_summaries(skills)
    t_full = count(full_block)

    # ----- PRIMARY: full-summary vs progressive (relevant subset) -----
    per_query: list[tuple[str, int, int]] = []
    rel_tokens: list[int] = []
    rel_counts: list[int] = []
    for label, q in _QUERIES:
        sub = _relevant_subset(skills, q, args.top_k)
        t = count(_format_summaries(sub))
        per_query.append((label, len(sub), t))
        rel_tokens.append(t)
        rel_counts.append(len(sub))

    t_rel_avg = sum(rel_tokens) / len(rel_tokens)
    t_rel_min = min(rel_tokens)
    t_rel_max = max(rel_tokens)
    k_avg = sum(rel_counts) / len(rel_counts)

    print(f"tokenizer      : {tokenizer}")
    print(f"skills on disk : {n_skills}")
    print(f"context window : {win:,} tok\n")

    print("  === PRIMARY: summary-selection (full vs progressive) ===")
    print(f"  {'arm':<26}{'skills':>8}{'tokens':>11}{'vs window':>12}")
    print("  " + "-" * 57)
    print(f"  {'baseline: all summaries':<26}{n_skills:>8}{t_full:>11,}{t_full / win:>11.1%}")
    print(f"  {'progressive: relevant':<26}{k_avg:>7.1f}{t_rel_avg:>11,.0f}"
          f"{t_rel_avg / win:>11.1%}")
    print(f"  {'  (min / max query)':<26}{'':>8}{t_rel_min:>11,}{'':>1}{t_rel_max:>10,}")

    print("\n  per-query progressive subset size:")
    for label, k, t in per_query:
        print(f"    {label:<20}{k:>3} skills   {t:>6,} tok")

    print("\n  ---- HEADLINE ----")
    saving = (t_full - t_rel_avg) / t_full
    print(f"  skill-summary block / iteration: {t_full:,} -> {t_rel_avg:,.0f} tok "
          f"({(t_rel_avg - t_full) / t_full:+.1%})")
    print(f"  progressive keeps only ~{k_avg:.1f} of {n_skills} summaries on average")
    for iters in (10, 20):
        print(f"  at {iters} iterations: {(t_full - t_rel_avg) * iters:,} tok of "
              f"summary avoided (~{(saving):.0%} of the summary block each turn)")

    # ----- SECONDARY: feasibility of inlining full BODIES (not the headline) -----
    bodies_total = sum(count(s["body"]) for s in skills)
    bodies_n = sum(count(s["body"]) for s in skills[: args.eager_n])
    print("\n  === SECONDARY (feasibility note, not the head-to-head) ===")
    print(f"  inlining all 87 full BODIES alone : {bodies_total:,} tok "
          f"({bodies_total / win:.0%} of window) before any summary/tool/history")
    print(f"  inlining first {args.eager_n} bodies    : {bodies_n:,} tok "
          f"({bodies_n / win:.0%} of window)")
    print("  -> full-body inlining exceeds the window (~215% in the prior full-")
    print("     prompt measurement of 281,777 tok); on-demand body loading is")
    print("     therefore mandatory, not an optimization.")

    print("\n  (The progressive arm is the PROPOSED selective-injection mode; this")
    print("   script measures it statically. Runtime injection of only relevant")
    print("   summaries still requires the small context.py change.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
