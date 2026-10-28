#!/usr/bin/env python3
"""Run the autopilot red-team campaign across models x conditions and report.

For every (model, condition) it runs objectives A1-A6 and records the three
Exp. 4 channels — the attacker's self-report, the real asset it actually
obtained, and whether a honeytoken burned across sessions. Writes a summary CSV,
a per-objective CSV, and a figure, and prints a table.

Examples
--------
    # everything available in models.yaml, three conditions, regex self-judge
    python results.py --models all --out results/

    # specific models, let a strong model be the attacker's self-judge
    python results.py --models local-ollama-qwen2.5,openai-gpt-4o-mini \
        --judge openai-gpt-4o-mini --out results/

    # smoke test with no network
    python results.py --models stub --out results/
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from mirage_demo.mechanism import CONDITIONS, Config
from mirage_demo.models import StubBackend, load_models
from mirage_demo.autopilot import run_campaign

DEFAULT_CONDITIONS = ["mirage_terminate", "no_defence", "refusal"]


def resolve_models(names, registry):
    out = []
    for n in names:
        if n == "stub":
            out.append(("stub", StubBackend()))
        else:
            out.append((n, registry.get(n)))
    return out


def main():
    ap = argparse.ArgumentParser(description="MIRAGE autopilot results runner")
    ap.add_argument("--models", default="all",
                    help="comma list of model names from models.yaml, 'all' (all available), or 'stub'")
    ap.add_argument("--conditions", default=",".join(DEFAULT_CONDITIONS))
    ap.add_argument("--judge", default="regex",
                    help="model name for the attacker's self-assessment, or 'regex' (default)")
    ap.add_argument("--models-file", default=str(Path(__file__).resolve().parent / "models.yaml"))
    ap.add_argument("--repeats", type=int, default=1, help="runs per cell (results are averaged)")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()

    registry = load_models(args.models_file)
    if args.models == "all":
        names = [m["name"] for m in registry.describe() if m["available"]]
        if not names:
            print("No configured model is available. Edit models.yaml, or use --models stub.")
            sys.exit(1)
    else:
        names = [n.strip() for n in args.models.split(",") if n.strip()]
    models = resolve_models(names, registry)
    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    judge = None if args.judge == "regex" else (StubBackend() if args.judge == "stub" else registry.get(args.judge))

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    summary_rows, detail_rows = [], []
    print(f"Running {len(models)} model(s) x {len(conditions)} condition(s), "
          f"{args.repeats} repeat(s). Self-judge: {args.judge}.\n")
    header = f"{'model':<24}{'condition':<20}{'self-report':>12}{'real asset':>12}{'DSR':>8}{'key out':>9}{'burn':>7}"
    print(header)
    print("-" * len(header))
    for name, backend in models:
        for cond in conditions:
            agg = {"self": 0.0, "real": 0.0, "dsr": 0.0, "delivered": 0, "burn": 0}
            last = None
            for _ in range(args.repeats):
                r = run_campaign(cond, backend, cfg=Config(), judge_backend=judge)
                s = r.summary()
                agg["self"] += s["self_report_rate"]
                agg["real"] += s["real_asset_rate"]
                agg["dsr"] += s["deception_success_rate"]
                agg["delivered"] += int(s["honeytoken_delivered"])
                agg["burn"] += int(s["honeytoken_burn"])
                last = r
            k = args.repeats
            row = {"model": name, "condition": cond,
                   "self_report_rate": round(agg["self"] / k, 3),
                   "real_asset_rate": round(agg["real"] / k, 3),
                   "deception_success_rate": round(agg["dsr"] / k, 3),
                   "honeytoken_delivered_rate": round(agg["delivered"] / k, 3),
                   "honeytoken_burn_rate": round(agg["burn"] / k, 3)}
            summary_rows.append(row)
            for d in last.rows:
                detail_rows.append({"model": name, "condition": cond, **d})
            print(f"{name:<24}{CONDITIONS.get(cond, cond):<20}"
                  f"{row['self_report_rate']:>12.2f}{row['real_asset_rate']:>12.2f}"
                  f"{row['deception_success_rate']:>8.2f}{row['honeytoken_delivered_rate']:>9.1f}"
                  f"{row['honeytoken_burn_rate']:>7.1f}")

    _write_csv(outdir / "summary.csv", summary_rows)
    _write_csv(outdir / "detail.csv", detail_rows,
               fields=["model", "condition", "obj", "goal", "perceived", "real", "evidence", "judge", "turns"])
    _figure(outdir / "results.png", summary_rows, conditions)
    print(f"\nWrote {outdir/'summary.csv'}, {outdir/'detail.csv'}, {outdir/'results.png'}")
    print("\nReading the table: under MIRAGE, self-report stays high while real asset "
          "drops toward 0 — that gap is the deception (DSR). Under no defence the two "
          "move together. A honeytoken burn is the independent third channel (Exp. 4).")


def _write_csv(path, rows, fields=None):
    if not rows:
        return
    fields = fields or list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _figure(path, rows, conditions):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    models = sorted({r["model"] for r in rows})
    conds = [c for c in conditions]
    fig, axes = plt.subplots(1, len(conds), figsize=(4.4 * len(conds), 4.2), squeeze=False)
    idx = {(r["model"], r["condition"]): r for r in rows}
    x = np.arange(len(models))
    w = 0.38
    teal, violet = "#0e7468", "#6447d6"
    for ax, cond in zip(axes[0], conds):
        sr = [idx[(m, cond)]["self_report_rate"] if (m, cond) in idx else 0 for m in models]
        ra = [idx[(m, cond)]["real_asset_rate"] if (m, cond) in idx else 0 for m in models]
        ax.bar(x - w / 2, sr, w, label="attacker believed", color=violet)
        ax.bar(x + w / 2, ra, w, label="real asset obtained", color=teal)
        ax.set_title(CONDITIONS.get(cond, cond), fontsize=11)
        ax.set_xticks(x)
        ax.set_xticklabels(models, rotation=30, ha="right", fontsize=8)
        ax.set_ylim(0, 1.0)
        ax.set_ylabel("rate over A1–A6")
        ax.grid(axis="y", alpha=0.25)
    axes[0][0].legend(fontsize=8, loc="upper right")
    fig.suptitle("Perceived vs real attack success — the gap is the deception (Exp. 4)", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(path, dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    main()
