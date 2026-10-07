"""Figures for a single run and for the scenario matrix.

Every chart uses one measurement axis, a fixed categorical colour order, and
carries a legend plus direct labels wherever a reader would otherwise have to
match a colour by eye.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from . import constants as C  # noqa: E402
from . import report  # noqa: E402
from .report import corridor_series  # noqa: E402

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#8a8984"
GRID = "#e5e4e0"
SEQUENTIAL = "viridis"

RATING_LABELS = {0: "Static rating", 1: "Ambient-adjusted", 2: "Full-weather DLR"}


def _style():
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE, "font.size": 9.5,
        "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "axes.titlesize": 11,
        "axes.titleweight": "bold", "axes.titlecolor": INK,
        "text.color": INK, "xtick.color": INK_2, "ytick.color": INK_2,
        "grid.color": GRID, "grid.linewidth": 0.8,
        "legend.frameon": False, "figure.dpi": 130,
    })


def _clean(ax, ylabel="", title="", xlabel=""):
    ax.set_title(title, loc="left", pad=10)
    ax.set_ylabel(ylabel)
    ax.set_xlabel(xlabel)
    ax.grid(True, axis="y", alpha=0.7)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def _legend_below(ax, ncols=3, pad=0.16, handles=None):
    """Place the legend under the axes, where it cannot cover data or a limit label."""
    extra = {} if handles is None else {"handles": handles}
    ax.legend(loc="upper left", bbox_to_anchor=(0.0, -pad), ncols=ncols,
              handlelength=1.6, columnspacing=1.6, borderaxespad=0.0, **extra)


def _reference(ax, value, label, inside=False, x=0.01, below=False):
    """Muted dashed limit line, labelled clear of the data.

    The label sits in the right margin by default; `inside` puts it above the
    line at the left, for axes that already have something in that margin.
    `x` staggers labels horizontally and `below` drops a label under its line,
    for limits close enough together that their text would otherwise collide.
    """
    ax.axhline(value, color=MUTED, lw=1.2, ls=(0, (4, 3)), zorder=1)
    if inside:
        ax.annotate(label, xy=(x, value), xycoords=("axes fraction", "data"),
                    ha="left", va="top" if below else "bottom", fontsize=8.5, color=INK_2,
                    xytext=(0, -4 if below else 4), textcoords="offset points")
    else:
        ax.annotate(label, xy=(1.0, value), xycoords=("axes fraction", "data"),
                    ha="left", va="center", fontsize=8.5, color=INK_2,
                    xytext=(6, 0), textcoords="offset points", annotation_clip=False)


def rating_timeseries(cfg, result: pd.DataFrame, path: Path):
    """How the operative rating moves with weather, against the current carried."""
    _style()
    ok = result[result["converged"]]
    fig, ax = plt.subplots(figsize=(10, 4.0))
    rating = corridor_series(ok, "rating_{zone}_a", "min").rolling(8, min_periods=1).mean()
    current = corridor_series(ok, "i_{zone}_a", "max").rolling(8, min_periods=1).mean()

    # The heat balance alone, drawn behind the operative rating. The gap
    # between the two is uplift the weather offered and the scheme may not
    # use, which is a different quantity from the headroom above the current
    # and reads as the same thing if only one line is plotted.
    ncols, top = 2, max(float(rating.max()), float(current.max()), cfg.static_rating_a)
    if cfg.dlr_mode > 0 and "rating_Z1_weather_a" in ok.columns:
        weather = corridor_series(ok, "rating_{zone}_weather_a", "min").rolling(
            8, min_periods=1).mean()
        ax.plot(weather.index, weather, color=MUTED, lw=1.2, ls=(0, (5, 2)),
                label="Heat-balance ampacity (weather)", zorder=1)
        ax.fill_between(weather.index, rating, weather, where=weather >= rating,
                        color=MUTED, alpha=0.10, lw=0, zorder=0)
        ncols, top = 3, max(top, float(weather.max()))

    ax.plot(rating.index, rating, color=SERIES[0], lw=1.6, label="Operative rating")
    ax.plot(current.index, current, color=SERIES[1], lw=1.6, label="Corridor current")
    ax.fill_between(rating.index, current, rating, where=rating >= current,
                    color=SERIES[0], alpha=0.08, lw=0)
    _reference(ax, cfg.static_rating_a, f"Static rating {cfg.static_rating_a:.0f} A")
    _clean(ax, "Amperes", f"Corridor rating and loading  ·  {RATING_LABELS[cfg.dlr_mode]}")
    ax.set_ylim(0, top * 1.12)
    _legend_below(ax, ncols=ncols, pad=0.34)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def loading_duration(runs: dict, path: Path):
    """Duration curve of corridor loading under each rating method."""
    _style()
    fig, ax = plt.subplots(figsize=(8.6, 4.2))
    for i, (label, result) in enumerate(runs.items()):
        ok = result[result["converged"]]
        loading = corridor_series(ok, "loading_{zone}_pct", "max").sort_values(
            ascending=False).to_numpy()
        pct = np.linspace(0, 100, len(loading))
        ax.plot(pct, loading, color=SERIES[i % len(SERIES)], lw=1.8, label=label)
    _reference(ax, 100.0, "Thermal limit")
    _clean(ax, "Corridor loading (% of operative rating)",
           "Loading duration curve by rating method", "Share of time exceeded (%)")
    ax.set_xlim(0, 100)
    ax.set_ylim(0, None)
    ax.legend(loc="lower left")
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def curtailment_matrix(table: pd.DataFrame, path: Path):
    """Curtailed share of available energy across the scenario matrix."""
    _style()
    order = ["static", "dlr1", "dlr2", "twin"]
    labels = {"static": "Static rating", "dlr1": "Ambient-adjusted",
              "dlr2": "Full-weather DLR", "twin": "Twin conductor"}
    table = table.copy()
    table["group"] = np.where(table["conductor"] == "twin", "twin",
                              "dlr" + table["dlr_mode"].astype(str))
    table["group"] = table["group"].replace({"dlr0": "static"})
    table = table[table["n_der"] > 0]

    fig, ax = plt.subplots(figsize=(9, 4.4))
    der_counts = sorted(table["n_der"].unique())
    width = 0.8 / len(order)
    x = np.arange(len(der_counts))
    for i, group in enumerate(order):
        subset = table[(table["group"] == group) & (table["storage"] == 0)]
        by_count = subset.groupby("n_der")["curtailed_pct"].mean()
        values = [float(by_count.get(n, 0.0)) for n in der_counts]
        offset = (i - (len(order) - 1) / 2) * width
        bars = ax.bar(x + offset, values, width * 0.92, label=labels[group],
                      color=SERIES[i], linewidth=0)
        # Every bar is labelled: a zero-curtailment series draws no bar at all,
        # and without a number it reads as missing rather than as zero.
        for bar, value in zip(bars, values, strict=True):
            ax.annotate(f"{value:.1f}", (bar.get_x() + bar.get_width() / 2, value),
                        ha="center", va="bottom", fontsize=7.5, color=INK_2,
                        xytext=(0, 2), textcoords="offset points")
    ax.set_xticks(x, [f"{n} plant" if n == 1 else f"{n} plants" for n in der_counts])
    _clean(ax, "Curtailed energy (% of available)",
           "Curtailment against generation connected", "")
    ax.set_ylim(0, max(1.0, ax.get_ylim()[1]) * 1.08)
    ax.legend(loc="upper left", ncols=2)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def rating_vs_weather(cfg, result: pd.DataFrame, path: Path):
    """What drives the conductor rating, and where the scheme stops using it.

    Plots the heat balance rather than the operative rating. Once a ceiling
    binds, the operative rating is a horizontal line and a scatter of it against
    wind speed says nothing at all - the physics is still there, it is just no
    longer what sets the limit. Drawing the ceilings over the physics shows both
    the relationship and how much of it is reachable.
    """
    _style()
    column = ("rating_Z1_weather_a" if "rating_Z1_weather_a" in result.columns
              else "rating_Z1_a")
    ok = result[result["converged"]].dropna(subset=["wind_Z1_ms", column])
    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    scatter = ax.scatter(ok["wind_Z1_ms"], ok[column], c=ok["t_air_c"],
                         cmap=SEQUENTIAL, s=9, alpha=0.55, linewidths=0)
    # Staggered: the cap and the equipment rating sit close together by design,
    # and stacked labels at the same x would overlap.
    _reference(ax, cfg.static_rating_a, f"Static {cfg.static_rating_a:.0f} A", inside=True)
    if cfg.rating_cap_a is not None:
        _reference(ax, cfg.rating_cap_a, f"Cap {cfg.rating_cap_a:.0f} A", inside=True,
                   below=True)
    if cfg.equipment_rating_a is not None:
        _reference(ax, cfg.equipment_rating_a,
                   f"Series equipment {cfg.equipment_rating_a:.0f} A",
                   inside=True, x=0.30)
    bar = fig.colorbar(scatter, ax=ax, pad=0.02)
    bar.set_label("Air temperature (°C)", color=INK_2)
    bar.outline.set_visible(False)
    _clean(ax, "Heat-balance ampacity (A)",
           "What the weather allows, against what the scheme may use",
           "Wind speed at conductor height (m/s)")
    ax.set_ylim(0, None)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def storage_operation(cfg, result: pd.DataFrame, path: Path):
    """Export at the interface and the state of charge behind it."""
    _style()
    ok = result[result["converged"]]
    fig, axes = plt.subplots(2, 1, figsize=(10, 5.4), sharex=True,
                             gridspec_kw={"height_ratios": [1.25, 1]})

    axes[0].plot(ok.index, ok["pcc_p_mw"], color=SERIES[0], lw=1.3, label="Export at interface")
    axes[0].plot(ok.index, ok["storage_p_grid_mw"], color=SERIES[1], lw=1.3,
                 label="Storage power (+ discharge)")
    _reference(axes[0], cfg.export_cap_mw, f"Export cap {cfg.export_cap_mw:.0f} MW")
    _clean(axes[0], "MW", "Interface export and storage dispatch")
    axes[0].set_ylim(top=cfg.export_cap_mw * 1.42)
    axes[0].legend(loc="upper left", ncols=2, framealpha=0.0)

    axes[1].plot(ok.index, ok["storage_soc_pct"], color=SERIES[2], lw=1.6,
                 label="State of charge")
    _reference(axes[1], 100 * C.BESS_SOC_RESERVE_FLOOR, "Reserve floor")
    _clean(axes[1], "State of charge (%)", "")
    axes[1].set_ylim(0, 118)
    _legend_below(axes[1], ncols=2, pad=0.30)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def voltage_profile(result: pd.DataFrame, path: Path):
    """Voltage along the corridor, from the head to the grid interface."""
    _style()
    order = ["SUB_A", "TAP_PV", "TAP_W", "SUB_C", "TAP_B", "PCC"]
    ok = result[result["converged"]]
    cols = [f"v_{b}_pu" for b in order]
    fig, ax = plt.subplots(figsize=(8, 4.0))
    x = np.arange(len(order))
    low = [ok[c].quantile(0.01) for c in cols]
    high = [ok[c].quantile(0.99) for c in cols]
    median = [ok[c].median() for c in cols]

    ax.fill_between(x, low, high, color=SERIES[0], alpha=0.16, lw=0,
                    label="1st to 99th percentile")
    ax.plot(x, median, color=SERIES[0], lw=2.0, marker="o", ms=5, label="Median")
    _reference(ax, C.V_MAX_PU, "Upper band")
    _reference(ax, C.V_MIN_PU, "Lower band")
    ax.set_xticks(x, order)
    _clean(ax, "Voltage (pu)", "Voltage along the corridor")
    _legend_below(ax, ncols=2)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def _period_name(result: pd.DataFrame) -> str:
    days = len(result) * C.DT_H / 24.0
    return "year" if days >= 365 else f"{days:g} days"


def ampacity_by_month(cfg, result: pd.DataFrame, path: Path):
    """Operative ampacity by month, against the single and twin static ratings.

    The heat balance is drawn too whenever a ceiling held the rating below it:
    a capped rating is a flat line, and without the weather beside it the
    figure cannot say whether the weather or the scheme set that line.
    """
    _style()
    ok = result[result["converged"]]
    stats = report.monthly_ampacity(result)
    n = len(stats)
    x = np.arange(n)
    # Thin range bars: about 22 px whatever the number of months.
    span = max(n, 6)
    pad = (span - n) / 2.0
    width = 0.024 * span

    fig, ax = plt.subplots(figsize=(8.6, 4.2))
    span_bars = ax.bar(x, stats["rating_max"] - stats["rating_min"],
                       bottom=stats["rating_min"], width=width, color=SERIES[0], alpha=0.25,
                       linewidth=0, label="DLR operative, monthly min to max")
    (mean_line,) = ax.plot(x, stats["rating_mean"], color=SERIES[0], lw=1.8, marker="o", ms=7,
                           mec=SURFACE, mew=1.5, label="DLR operative, monthly mean", zorder=3)
    handles = [mean_line, span_bars]
    top = max(float(stats["rating_max"].max()), report.TWIN_STATIC_A)

    binding = report.governing_binding(ok)
    if np.isin(binding, ["cap", "equipment"]).any():
        (weather_line,) = ax.plot(x, stats["weather_mean"], color=MUTED, lw=1.4,
                                  ls=(0, (5, 2)), marker="o", ms=6, mfc=SURFACE, mec=MUTED,
                                  mew=1.4, label="Heat-balance ampacity, monthly mean",
                                  zorder=2)
        handles.append(weather_line)
        top = max(top, float(stats["weather_mean"].max()))

    _reference(ax, report.SINGLE_STATIC_A,
               f"{report.SINGLE['label']} static {report.SINGLE_STATIC_A:.0f} A")
    _reference(ax, report.TWIN_STATIC_A,
               f"{report.TWIN['label']} static {report.TWIN_STATIC_A:.0f} A")
    one_year = len({p.year for p in stats.index}) == 1
    ax.set_xticks(x, [p.strftime("%b" if one_year else "%b\n%Y") for p in stats.index])
    ax.set_xlim(-0.5 - pad, n - 0.5 + pad)
    ax.set_ylim(0, top * 1.12)
    _clean(ax, "Ampacity (A)", f"Operative ampacity by month  ·  "
           f"{RATING_LABELS[cfg.dlr_mode]} on {cfg.conductor_label}")
    _legend_below(ax, ncols=3, pad=0.12, handles=handles)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def current_duration(cfg, result: pd.DataFrame, path: Path):
    """Corridor current and operative rating against the share of time exceeded.

    Marks how long the current stayed above a single conductor's static
    rating: the time a statically rated single conductor would have been
    overloaded by the same flow.
    """
    _style()
    ok = result[result["converged"]]
    current = np.sort(corridor_series(ok, "i_{zone}_a", "max").to_numpy())[::-1]
    rating = np.sort(corridor_series(ok, "rating_{zone}_a", "min").to_numpy())[::-1]
    pct = np.linspace(0.0, 100.0, len(current))
    static = report.SINGLE_STATIC_A
    name = report.SINGLE["label"]

    fig, ax = plt.subplots(figsize=(8.6, 4.2))
    ax.fill_between(pct, static, current, where=current > static, interpolate=True,
                    color=SERIES[1], alpha=0.12, lw=0, label=f"Above {name} static rating")
    ax.plot(pct, rating, color=SERIES[0], lw=1.8, label="Operative rating")
    ax.plot(pct, current, color=SERIES[1], lw=1.8, label="Corridor current")
    _reference(ax, static, f"{name} static {static:.0f} A")
    if cfg.conductor != "single":
        _reference(ax, cfg.static_rating_a,
                   f"{cfg.conductor_label} static {cfg.static_rating_a:.0f} A")

    share = 100.0 * float(np.mean(current > static))
    hours = float(np.sum(current > static)) * C.DT_H
    if share > 0.0:
        ax.plot([share, share], [0.0, static], color=INK_2, lw=1.0, ls=(0, (2, 2)), zorder=1)
        ax.annotate(f"{share:.1f} % of the time above {static:.0f} A\n({hours:.0f} h)",
                    xy=(share, static), xytext=(8, 10), textcoords="offset points",
                    ha="left", va="bottom", fontsize=9, color=INK)
    else:
        ax.annotate(f"Never above {static:.0f} A", xy=(0.5, static),
                    xycoords=("axes fraction", "data"), xytext=(0, 6),
                    textcoords="offset points", ha="center", va="bottom",
                    fontsize=9, color=INK)
    top = max(float(rating.max()), float(current.max()), cfg.static_rating_a, static)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, top * 1.12)
    _clean(ax, "Amperes", f"Corridor current duration  ·  {RATING_LABELS[cfg.dlr_mode]}",
           f"Time exceeded (% of {_period_name(result)})")
    _legend_below(ax, ncols=3, pad=0.20)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def _quantity(value: float) -> str:
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.1f}"


def technical_cost(cfg, result: pd.DataFrame, reference, path: Path):
    """Curtailed energy, losses and reactive exchange: static rating against DLR.

    One panel per quantity, because they carry different units and one shared
    axis would hide the smaller ones.
    """
    _style()
    m = report.metrics(cfg, result)
    r = report.metrics(reference.cfg, reference.result)
    panels = (("Curtailed energy", "MWh", "curtailed_mwh"),
              ("Network losses", "MWh", "losses_mwh"),
              (f"Reactive outside ±{cfg.q_window_mvar:g} MVAr window", "MVArh",
               "reactive_outside_window_mvarh"))
    fig, axes = plt.subplots(1, len(panels), figsize=(10, 3.9))
    for ax, (title, unit, key) in zip(axes, panels, strict=True):
        values = [float(r[key]), float(m[key])]
        bars = ax.bar([0, 1], values, width=0.22, color=[MUTED, SERIES[0]], linewidth=0)
        for bar, value in zip(bars, values, strict=True):
            ax.annotate(_quantity(value), (bar.get_x() + bar.get_width() / 2, value),
                        ha="center", va="bottom", fontsize=9, color=INK,
                        xytext=(0, 3), textcoords="offset points")
        if values[0] > 0:
            change = 100.0 * (values[1] - values[0]) / values[0]
            ax.annotate(f"{change:+.0f} %", xy=(0.5, 0.98), xycoords="axes fraction",
                        ha="center", va="top", fontsize=9.5, color=INK_2)
        ax.set_xticks([0, 1], ["Static", "DLR"])
        ax.set_xlim(-0.7, 1.7)
        ax.set_ylim(0, max(max(values), 1e-9) * 1.28)
        _clean(ax, unit, title)
        ax.title.set_fontsize(10)
    total_s, total_d = r["technical_cost_keur"], m["technical_cost_keur"]
    total = f"total {total_s:,.0f} → {total_d:,.0f} k€"
    if total_s > 0:
        total += f" ({100.0 * (total_d - total_s) / total_s:+.0f} %)"
    fig.suptitle(f"Technical cost  ·  static rating vs {RATING_LABELS[cfg.dlr_mode]}  ·  "
                 f"{total}", x=0.01, ha="left", fontsize=11, fontweight="bold", color=INK)
    fig.text(0.01, -0.02, f"Costs at {cfg.energy_price_eur_mwh:g} €/MWh for curtailment and "
             f"losses, {cfg.reactive_price_eur_mvarh:g} €/MVArh for reactive energy outside "
             f"the window.", ha="left", va="top", fontsize=8.5, color=INK_2)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def run_figures(cfg, result: pd.DataFrame, out_dir: Path, reference=None) -> list:
    """Figures that describe one run; the cost figure needs its static reference."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []

    def target(name: str) -> Path:
        made.append(out_dir / f"{cfg.stem}_{name}.png")
        return made[-1]

    rating_timeseries(cfg, result, target("rating"))
    if cfg.dlr_mode > 0:
        ampacity_by_month(cfg, result, target("ampacity"))
    current_duration(cfg, result, target("duration"))
    if reference is not None:
        technical_cost(cfg, result, reference, target("cost"))
    else:
        # A cost figure left from an earlier run would show a comparison this
        # run did not make.
        (out_dir / f"{cfg.stem}_cost.png").unlink(missing_ok=True)
    voltage_profile(result, target("voltage"))
    if cfg.dlr_mode == 2:
        rating_vs_weather(cfg, result, target("rating_drivers"))
    if cfg.storage_enabled:
        storage_operation(cfg, result, target("storage"))
    return made
