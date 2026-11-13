"""Eval report: charts (PNG) + a self-contained HTML page.

Palette follows a validated categorical order: slot 1 blue = Aegis,
slot 2 orange = baseline, slot 3 aqua = ablations; text stays in ink colors;
one axis per chart; direct labels on bars; legend always shown for >= 2 series.
"""
from __future__ import annotations

import base64
import html
import json
from pathlib import Path

INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e1", "#fcfcfb"
SYSTEM_COLORS = {"aegis": "#2a78d6", "baseline": "#eb6834"}
OTHER = "#1baf7a"


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": MUTED, "xtick.color": MUTED,
                         "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False,
                         "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "axes.grid": True,
                         "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True})
    return plt


def _bars(summary: dict, metric: str, title: str, path: Path, ci_key: str | None = None, pct: bool = True) -> None:
    plt = _plt()
    groups = summary["groups"]
    order = ["none", "mild", "harsh", "outage", "crash", "crash_mid"]
    profiles = sorted({g["profile"] for g in groups}, key=lambda p: order.index(p) if p in order else 9)
    # Headline chart compares the two systems only; ablations live in the table
    # (five same-colored bars per group were unreadable).
    systems = [s for s in ("baseline", "aegis") if any(g["system"] == s for g in groups)]
    fig, ax = plt.subplots(figsize=(7.2, 3.6), dpi=150)
    w = 0.8 / len(systems)
    for i, s in enumerate(systems):
        xs, ys, lo, hi = [], [], [], []
        for j, p in enumerate(profiles):
            g = next((g for g in groups if g["system"] == s and g["profile"] == p), None)
            v = None if g is None else g.get(metric)
            if v is None:
                continue
            xs.append(j + (i - (len(systems) - 1) / 2) * w)
            ys.append(v)
            if ci_key and g.get(ci_key):
                lo.append(v - g[ci_key][0])
                hi.append(g[ci_key][1] - v)
        color = SYSTEM_COLORS.get(s, OTHER)
        bars = ax.bar(xs, ys, width=w * 0.92, color=color, label=s, edgecolor=SURFACE, linewidth=1.5)
        if lo:
            ax.errorbar(xs, ys, yerr=[lo, hi], fmt="none", ecolor=MUTED, elinewidth=1, capsize=3)
        for b, v in zip(bars, ys):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.02, f"{v:.0%}" if pct else f"{v:.2f}",
                    ha="center", va="bottom", fontsize=8.5, color=INK)
    ax.set_xticks(range(len(profiles)), [f"faults: {p}" for p in profiles])
    if pct:
        ax.set_ylim(0, 1.12)
        ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.set_title(title, loc="left", fontsize=11, color=INK, pad=22)
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0.0, 1.0), ncols=len(systems), fontsize=8.5,
              borderaxespad=0.1)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _coverage(summary: dict, path: Path, profile: str = "none") -> bool:
    plt = _plt()
    fig, ax = plt.subplots(figsize=(5.2, 3.6), dpi=150)
    drew = False
    for g in summary["groups"]:
        if g["profile"] != profile or g["system"] not in SYSTEM_COLORS:
            continue
        pts = [(c["coverage"], c["accuracy"]) for c in g["coverage_accuracy"] if c["accuracy"] is not None]
        if len(pts) < 2:
            continue
        ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o", ms=4, lw=2, color=SYSTEM_COLORS[g["system"]],
                label=g["system"])
        drew = True
    if not drew:
        plt.close(fig)
        return False
    ax.set_xlabel("coverage (share of variants answered)")
    ax.set_ylabel("accuracy on answered")
    ax.set_title("Declining low-evidence calls: coverage vs accuracy", loc="left", fontsize=11, color=INK, pad=10)
    ax.legend(frameon=False, fontsize=8.5)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return True


def write_report(summary: dict, rows: list[dict], out_dir: str, mode: str, title: str, md: str) -> list[str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    (out / "report.md").write_text(md)
    charts = []
    _bars(summary, "success_rate", "Run success rate under injected faults (95% CI)", out / "success_rate.png",
          "success_ci")
    charts.append("success_rate.png")
    if any(g.get("citation_validity") is not None for g in summary["groups"]):
        _bars(summary, "citation_validity", "Cited evidence that is real and quoted verbatim", out / "citation_validity.png")
        charts.append("citation_validity.png")
    # Accuracy curves only mean something on real labels; skip for synthetic fixtures.
    if mode != "offline" and _coverage(summary, out / "coverage_accuracy.png"):
        charts.append("coverage_accuracy.png")
    imgs = "".join(
        f'<figure><img alt="{c}" src="data:image/png;base64,{base64.b64encode((out / c).read_bytes()).decode()}"></figure>'
        for c in charts)
    banner = ("<p class='warn'>OFFLINE run: synthetic fixtures and a deterministic stand-in model with injected "
              "hallucinations. These numbers measure the runtime (recovery, verification, grounding), not real "
              "classification accuracy.</p>") if mode == "offline" else ""
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font:14px/1.5 -apple-system,Segoe UI,Inter,sans-serif;max-width:1100px;margin:28px auto;padding:0 18px;color:{INK};background:{SURFACE}}}
h1{{font-size:21px}} .warn{{background:#fff6e5;border:1px solid #f0d9a8;padding:10px 12px;border-radius:8px}}
figure{{display:inline-block;margin:10px 10px 0 0}} img{{max-width:520px;border:1px solid {GRID};border-radius:8px}}
table{{border-collapse:collapse;font-size:12.5px;margin-top:14px}} td,th{{border-bottom:1px solid {GRID};padding:5px 8px;text-align:left}}
th{{color:{MUTED};font-weight:600}} code{{font-size:12px}}</style></head><body>
<h1>{html.escape(title)}</h1>{banner}{imgs}
<h2>Table</h2>{_table(summary)}
<h2>Paired significance</h2><ul>{''.join(f"<li>{p['profile']}: Aegis-only successes {p['only_b_ok']}, baseline-only {p['only_a_ok']}, exact McNemar p = {p['p_value']:.2g}</li>" for p in summary['paired'])}</ul>
<p style="color:{MUTED}">{len(rows)} runs. Raw rows: runs.jsonl. Every run's full trace is in eval.db (open with <code>aegis serve</code>).</p>
</body></html>"""
    (out / "report.html").write_text(page)
    return charts


def _table(summary: dict) -> str:
    cols = ["system", "profile", "n", "success_rate", "recovery_rate_under_faults", "citation_validity",
            "reports_with_ungrounded_numbers", "coverage", "auroc_plp_vs_blb", "mean_llm_calls", "mean_cost_usd"]
    if all(str(g.get("mean_cost_usd")) in ("0.0", "None") for g in summary["groups"]):
        cols = [c for c in cols if c not in ("coverage", "auroc_plp_vs_blb", "mean_cost_usd")]
    head = "".join(f"<th>{c}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(g.get(c)))}</td>" for c in cols) + "</tr>"
                   for g in summary["groups"])
    return f"<table><tr>{head}</tr>{body}</table>"
