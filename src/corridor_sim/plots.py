"""Figures for a single run and for the scenario matrix.

The single-run figures follow the network: the currents in the corridor
against every limit, how often each limit is exceeded, the power flow from
the plants to the grid interface, the current along the corridor, the
voltages, and what it all cost. Every chart uses one measurement axis per
panel, a fixed colour per quantity, and a legend plus direct labels wherever
a reader would otherwise have to match a colour by eye.

Colours keep one meaning across figures: orange is what the plants push or
offer, aqua what actually flows, blue the operative rating or the interface,
grey the heat balance and every fixed reference.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from . import constants as C  # noqa: E402
from . import report  # noqa: E402
from .report import corridor_series  # noqa: E402
from .simulate import realised_generation  # noqa: E402

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#8a8984"
GRID = "#e5e4e0"
SEQUENTIAL = "viridis"

BLUE, ORANGE, AQUA = SERIES[0], SERIES[1], SERIES[2]
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
    if inside and label:
        ax.annotate(label, xy=(x, value), xycoords=("axes fraction", "data"),
                    ha="left", va="top" if below else "bottom", fontsize=8.5, color=INK_2,
                    xytext=(0, -4 if below else 4), textcoords="offset points",
                    bbox={"boxstyle": "round,pad=0.15", "fc": SURFACE, "ec": "none",
                          "alpha": 0.85})
    else:
        ax.annotate(label, xy=(1.0, value), xycoords=("axes fraction", "data"),
                    ha="left", va="center", fontsize=8.5, color=INK_2,
                    xytext=(6, 0), textcoords="offset points", annotation_clip=False)


def _dates(ax):
    locator = mdates.AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))


def _period_name(result: pd.DataFrame) -> str:
    days = len(result) * C.DT_H / 24.0
    return "year" if days >= 365 else f"{days:g} days"


def _current_references(cfg, ax, label=True):
    """The single-conductor static rating and the twin-bundle reinforcement."""
    twin = f"{report.TWIN['label']} static {report.TWIN_STATIC_A:.0f} A"
    if (cfg.dlr_mode > 0 and cfg.rating_cap_a is not None
            and abs(cfg.rating_cap_a - report.TWIN_STATIC_A) < 1.0):
        twin += " = DLR cap"
    _reference(ax, report.SINGLE_STATIC_A,
               f"{report.SINGLE['label']} static {report.SINGLE_STATIC_A:.0f} A" if label else "")
    _reference(ax, report.TWIN_STATIC_A, twin if label else "")


# ── Single run ───────────────────────────────────────────────────────────────
def corridor_current(cfg, result: pd.DataFrame, path: Path):
    """Every corridor current at 15-minute resolution, and its mean by period.

    Top: the current all plants push before curtailment, what the corridor
    carries after it, the operative rating and the heat-balance ampacity,
    against the single-conductor static rating and the twin-bundle
    reinforcement. Bottom: the same as monthly means, or one mean over a
    window shorter than four weeks.
    """
    _style()
    ok = result[result["converged"]]
    t = ok.index
    pushed = report.pushed_current(ok)
    carried = corridor_series(ok, "i_{zone}_a", "max")
    rating = corridor_series(ok, "rating_{zone}_a", "min")
    weather = (corridor_series(ok, "rating_{zone}_weather_a", "min")
               if cfg.dlr_mode > 0 else None)

    fig, (ax, axm) = plt.subplots(2, 1, figsize=(10, 7.4),
                                  gridspec_kw={"height_ratios": [1.7, 1.0], "hspace": 0.45})
    # Drawn back to front: the heat balance only shows where it rises above the
    # operative rating, and the carried current over the current pushed.
    handles = []
    if cfg.dlr_mode > 0:
        weather_line = ax.plot(t, weather, color=MUTED, lw=0.9, ls=(0, (4, 2)),
                               label="DLR ampacity, heat balance (weather)")[0]
        rating_line = ax.plot(t, rating, color=BLUE, lw=1.3, label="Operative DLR rating")[0]
    if pushed is not None:
        handles.append(ax.plot(t, pushed, color=ORANGE, lw=1.0,
                               label="Pushed by all plants (before curtailment)")[0])
    handles.append(ax.plot(t, carried, color=AQUA, lw=1.0, label="Carried (after curtailment)")[0])
    if cfg.dlr_mode > 0:
        handles += [rating_line, weather_line]
    if pushed is not None:
        ax.fill_between(t, carried, pushed, where=pushed > carried + 0.5, interpolate=True,
                        color=ORANGE, alpha=0.18, lw=0, zorder=0)
        handles.append(Patch(color=ORANGE, alpha=0.18, lw=0, label="Curtailed"))
    _current_references(cfg, ax)
    tops = [s.max() for s in (pushed, carried, rating, weather) if s is not None]
    top = max(max(tops), report.TWIN_STATIC_A)
    ax.set_ylim(0, top * 1.1)
    _dates(ax)
    _clean(ax, "Amperes", f"Corridor current  ·  {RATING_LABELS[cfg.dlr_mode]} on "
           f"{cfg.conductor_label}  ·  15-minute values")

    means = report.period_means(result)
    groups = [("pushed_mean", ORANGE), ("carried_mean", AQUA)]
    if cfg.dlr_mode > 0:
        groups += [("rating_mean", BLUE), ("weather_mean", MUTED)]
    groups = [(key, colour) for key, colour in groups if means[key].notna().any()]
    n, k = len(means), len(groups)
    span = max(n, 4)
    pad = (span - n) / 2.0
    width = min(0.8 / k, 0.025 * span)
    x = np.arange(n)
    for j, (key, colour) in enumerate(groups):
        offset = (j - (k - 1) / 2.0) * width * 1.7
        bars = axm.bar(x + offset, means[key], width, color=colour, linewidth=0)
        if n <= 4:
            for bar, value in zip(bars, means[key], strict=True):
                if np.isfinite(value):
                    axm.annotate(f"{value:.0f}", (bar.get_x() + bar.get_width() / 2, value),
                                 ha="center", va="bottom", fontsize=8, color=INK_2,
                                 xytext=(0, 2), textcoords="offset points")
    _current_references(cfg, axm)
    axm.set_xticks(x, list(means.index))
    axm.set_xlim(-0.5 - pad, n - 0.5 + pad)
    axm.set_ylim(0, max(float(np.nanmax(means[[g for g, _ in groups]].to_numpy())),
                        report.TWIN_STATIC_A) * 1.15)
    _clean(axm, "Amperes", "Mean over the period" if report.is_short_window(result)
           else "Monthly mean")
    _legend_below(axm, ncols=3, pad=0.20, handles=handles)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def current_duration(cfg, result: pd.DataFrame, path: Path):
    """Each corridor current against the share of time it is reached or exceeded.

    Marks how long the current the plants push stays above the single
    conductor's static rating (the time a statically rated single conductor
    would be overloaded), above the operative rating, and above the twin
    bundle's static rating.
    """
    _style()
    ok = result[result["converged"]]
    pushed = report.pushed_current(ok)
    carried = corridor_series(ok, "i_{zone}_a", "max")
    rating = corridor_series(ok, "rating_{zone}_a", "min")

    def curve(series):
        values = np.sort(series.dropna().to_numpy())[::-1]
        return np.linspace(0.0, 100.0, len(values)), values

    fig, ax = plt.subplots(figsize=(10, 4.6))
    if cfg.dlr_mode > 0:
        ax.plot(*curve(corridor_series(ok, "rating_{zone}_weather_a", "min")), color=MUTED,
                lw=1.2, ls=(0, (4, 2)), label="DLR ampacity, heat balance (weather)")
        ax.plot(*curve(rating), color=BLUE, lw=1.6, label="Operative DLR rating")
    if pushed is not None:
        ax.plot(*curve(pushed), color=ORANGE, lw=1.8, label="Pushed by all plants")
    ax.plot(*curve(carried), color=AQUA, lw=1.8, label="Carried after curtailment")
    _current_references(cfg, ax)

    if pushed is not None:
        lines = []
        for name, above in (
                (f"above {report.SINGLE_STATIC_A:.0f} A ({report.SINGLE['label']} static)",
                 pushed > report.SINGLE_STATIC_A),
                ("above the operative rating", pushed > rating),
                (f"above {report.TWIN_STATIC_A:.0f} A ({report.TWIN['label']} static)",
                 pushed > report.TWIN_STATIC_A)):
            share = 100.0 * float(above.mean())
            lines.append(f"{name}: {share:.1f} % ({float(above.sum()) * C.DT_H:.0f} h)")
        for limit in (report.SINGLE_STATIC_A, report.TWIN_STATIC_A):
            share = 100.0 * float((pushed > limit).mean())
            if share > 0.0:
                ax.plot([share, share], [0.0, limit], color=INK_2, lw=0.9, ls=(0, (2, 2)),
                        zorder=1)
        ax.text(0.985, 0.96, "Current pushed by all plants\n" + "\n".join(lines),
                transform=ax.transAxes, ha="right", va="top", fontsize=8.8, color=INK,
                linespacing=1.5, bbox={"boxstyle": "round,pad=0.5", "fc": SURFACE, "ec": GRID})
    ax.set_xlim(0, 100)
    ax.set_ylim(0, max(ax.get_ylim()[1], report.TWIN_STATIC_A * 1.1) * 1.08)
    _clean(ax, "Amperes", f"Corridor current duration  ·  {RATING_LABELS[cfg.dlr_mode]}",
           f"Time exceeded (% of {_period_name(result)})")
    _legend_below(ax, ncols=4, pad=0.18)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def power_flow(cfg, result: pd.DataFrame, path: Path):
    """Active power from the plants to the grid, and reactive power at the interface."""
    _style()
    ok = result[result["converged"]]
    t = ok.index
    available = ok["available_total_mw"]
    delivered = realised_generation(ok)
    export = ok["pcc_p_mw"]
    q = ok["pcc_q_mvar"]
    window = cfg.q_window_mvar

    fig, (ax, axq) = plt.subplots(2, 1, figsize=(10, 6.6), sharex=True,
                                  gridspec_kw={"height_ratios": [1.6, 1.0]})
    ax.fill_between(t, delivered, available, where=available > delivered + 0.05,
                    interpolate=True, color=ORANGE, alpha=0.18, lw=0, zorder=0)
    handles = [ax.plot(t, available, color=ORANGE, lw=1.0, label="Available from all plants")[0],
               ax.plot(t, delivered, color=AQUA, lw=1.0, label="Delivered by the plants")[0],
               ax.plot(t, export, color=BLUE, lw=1.0, label="Net export at PCC")[0],
               Patch(color=ORANGE, alpha=0.18, lw=0, label="Curtailed")]
    ax.axhline(0.0, color=GRID, lw=1.0, zorder=0)
    _reference(ax, cfg.export_cap_mw, f"Export cap {cfg.export_cap_mw:.0f} MW")
    ax.set_ylim(min(0.0, float(export.min())) * 1.15,
                max(cfg.export_cap_mw, float(available.max())) * 1.12)
    _clean(ax, "MW", "Power flow  ·  from the plants to the grid interface")

    axq.axhspan(-window, window, color=MUTED, alpha=0.10, lw=0)
    handles.append(axq.plot(t, q, color=INK_2, lw=1.0,
                            label="Reactive power at PCC (+ export)")[0])
    handles.append(Patch(color=MUTED, alpha=0.10, lw=0, label=f"Window ±{window:g} MVAr"))
    _reference(axq, window, f"+{window:g} MVAr")
    _reference(axq, -window, f"−{window:g} MVAr")
    axq.axhline(0.0, color=GRID, lw=1.0, zorder=0)
    lim = max(window, float(q.abs().max())) * 1.15
    axq.set_ylim(-lim, lim)
    _clean(axq, "MVAr", "Reactive power at the grid interface")
    _dates(axq)
    _legend_below(axq, ncols=3, pad=0.22, handles=handles)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def corridor_segments(cfg, result: pd.DataFrame, path: Path):
    """Mean and maximum current on each corridor segment, head to grid interface.

    The power the plants inject joins the corridor at three taps, so the flow
    builds up along it; the segment that carries everything is the bottleneck.
    """
    _style()
    ok = result[result["converged"]]
    lines = [line for line in report.SEGMENTS if f"i_{line}_a" in ok.columns]
    mean_i = [float(ok[f"i_{line}_a"].mean()) for line in lines]
    max_i = [float(ok[f"i_{line}_a"].max()) for line in lines]
    labels = [f"{report.SEGMENTS[line][0].replace(' to ', ' → ')}\n"
              f"{report.SEGMENTS[line][1]:g} km, mean {ok[f'p_{line}_mw'].mean():.0f} MW"
              for line in lines]

    fig, ax = plt.subplots(figsize=(10, 4.6))
    x = np.arange(len(lines))
    width = 0.22
    for offset, values, colour, name in ((-0.13, mean_i, AQUA, "Mean current"),
                                         (0.13, max_i, BLUE, "Maximum current")):
        bars = ax.bar(x + offset, values, width, color=colour, linewidth=0, label=name)
        for bar, value in zip(bars, values, strict=True):
            ax.annotate(f"{value:.0f}", (bar.get_x() + bar.get_width() / 2, value),
                        ha="center", va="bottom", fontsize=8, color=INK_2,
                        xytext=(0, 2), textcoords="offset points")
    _current_references(cfg, ax)
    ax.set_xticks(x, labels)
    ax.set_ylim(0, max(max(max_i), report.TWIN_STATIC_A) * 1.15)
    _clean(ax, "Amperes", "Current along the corridor, after curtailment")
    _legend_below(ax, ncols=2, pad=0.24)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def voltage_timeseries(result: pd.DataFrame, path: Path):
    """Voltage at every corridor bus, head to grid interface, at 15-minute resolution."""
    _style()
    ok = result[result["converged"]]
    buses = [b for b in report.CORRIDOR_BUSES if f"v_{b}_pu" in ok.columns]
    fig, ax = plt.subplots(figsize=(10, 4.4))
    for colour, bus in zip(SERIES, buses, strict=False):
        ax.plot(ok.index, ok[f"v_{bus}_pu"], color=colour, lw=0.9, label=bus)
    _reference(ax, C.V_MAX_PU, f"Upper band {C.V_MAX_PU:g} pu")
    _reference(ax, C.V_MIN_PU, f"Lower band {C.V_MIN_PU:g} pu")
    values = ok[[f"v_{b}_pu" for b in buses]].to_numpy()
    ax.set_ylim(min(C.V_MIN_PU - 0.01, float(np.nanmin(values)) - 0.005),
                max(C.V_MAX_PU + 0.01, float(np.nanmax(values)) + 0.005))
    _dates(ax)
    _clean(ax, "Voltage (pu)", "Voltage along the corridor  ·  15-minute values")
    _legend_below(ax, ncols=6, pad=0.16)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def technical_cost(cfg, result: pd.DataFrame, path: Path, reference=None):
    """Cost of curtailment, losses and reactive exchange outside the window.

    One bar per item in k€, labelled with the energy behind it. With a static
    reference each item gets a static and a DLR bar.
    """
    _style()
    m = report.metrics(cfg, result)
    r = report.metrics(reference.cfg, reference.result) if reference is not None else None
    items = (("Curtailed energy", "cost_curtailed_keur", "curtailed_mwh", "MWh"),
             ("Network losses", "cost_losses_keur", "losses_mwh", "MWh"),
             (f"Reactive outside ±{cfg.q_window_mvar:g} MVAr", "cost_reactive_keur",
              "reactive_outside_window_mvarh", "MVArh"))
    fig, ax = plt.subplots(figsize=(9.4, 3.8 if r is None else 4.4))
    y = np.arange(len(items))[::-1].astype(float)
    top = max(m[key] for _, key, _, _ in items)

    def label(bar, metrics_, key, qty, unit):
        value = metrics_[key]
        ax.annotate(f"{value:,.1f} k€  ·  {metrics_[qty]:,.0f} {unit}",
                    (value, bar.get_y() + bar.get_height() / 2), ha="left", va="center",
                    fontsize=8.8, color=INK, xytext=(5, 0), textcoords="offset points")

    if r is None:
        bars = ax.barh(y, [m[key] for _, key, _, _ in items], 0.36, color=BLUE, linewidth=0)
        for bar, (_, key, qty, unit) in zip(bars, items, strict=True):
            label(bar, m, key, qty, unit)
        title = (f"Technical cost  ·  {m['technical_cost_keur']:,.0f} k€ over "
                 f"{_period_name(result)}")
    else:
        top = max(top, max(r[key] for _, key, _, _ in items))
        for offset, metrics_, colour, name in ((0.2, r, MUTED, "Static rating"),
                                                (-0.2, m, BLUE, RATING_LABELS[cfg.dlr_mode])):
            bars = ax.barh(y + offset, [metrics_[key] for _, key, _, _ in items], 0.34,
                           color=colour, linewidth=0, label=name)
            for bar, (_, key, qty, unit) in zip(bars, items, strict=True):
                label(bar, metrics_, key, qty, unit)
        total_s, total_d = r["technical_cost_keur"], m["technical_cost_keur"]
        change = f" ({100.0 * (total_d - total_s) / total_s:+.0f} %)" if total_s > 0 else ""
        title = (f"Technical cost  ·  static rating vs {RATING_LABELS[cfg.dlr_mode]}  ·  "
                 f"{total_s:,.0f} → {total_d:,.0f} k€{change}")
        _legend_below(ax, ncols=2, pad=0.24)
    ax.set_yticks(y, [name for name, _, _, _ in items])
    ax.set_xlim(0, max(top, 1e-9) * 1.55)
    _clean(ax, "", title, "k€")
    ax.set_title(title, loc="left", pad=24)
    ax.grid(True, axis="x", alpha=0.7)
    ax.grid(False, axis="y")
    ax.annotate(f"At {cfg.energy_price_eur_mwh:g} €/MWh for curtailment and losses, "
                f"{cfg.reactive_price_eur_mvarh:g} €/MVArh for reactive energy outside the "
                f"window.", xy=(0.0, 1.0), xycoords="axes fraction", xytext=(0, 6),
                textcoords="offset points", ha="left", va="bottom", fontsize=8.5, color=INK_2)
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
    _reference(ax, cfg.static_rating_a, f"Static {cfg.static_rating_a:.0f} A", inside=True)
    cap, equipment = cfg.rating_cap_a, cfg.equipment_rating_a
    if cap is not None and equipment is not None and abs(equipment - cap) < 0.1 * cap:
        # Close enough that two labels would collide: one label for both lines.
        _reference(ax, cap, "")
        _reference(ax, equipment, f"Cap {cap:.0f} A, series equipment {equipment:.0f} A",
                   inside=True)
    else:
        if cap is not None:
            _reference(ax, cap, f"Cap {cap:.0f} A", inside=True, below=True)
        if equipment is not None:
            _reference(ax, equipment, f"Series equipment {equipment:.0f} A", inside=True,
                       x=0.30)
    bar = fig.colorbar(scatter, ax=ax, pad=0.02)
    bar.set_label("Air temperature (°C)", color=INK_2)
    bar.outline.set_visible(False)
    _clean(ax, "Heat-balance ampacity (A)",
           "What the weather allows, against what the scheme may use",
           f"Cooling wind at the conductor (m/s, {100 * cfg.dlr_wind_factor:.0f} % "
           f"of the open-terrain wind)")
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

    axes[0].plot(ok.index, ok["pcc_p_mw"], color=BLUE, lw=1.3, label="Export at interface")
    axes[0].plot(ok.index, ok["storage_p_grid_mw"], color=ORANGE, lw=1.3,
                 label="Storage power (+ discharge)")
    _reference(axes[0], cfg.export_cap_mw, f"Export cap {cfg.export_cap_mw:.0f} MW")
    _clean(axes[0], "MW", "Interface export and storage dispatch")
    axes[0].set_ylim(top=cfg.export_cap_mw * 1.42)
    axes[0].legend(loc="upper left", ncols=2, framealpha=0.0)

    axes[1].plot(ok.index, ok["storage_soc_pct"], color=AQUA, lw=1.6,
                 label="State of charge")
    _reference(axes[1], 100 * C.BESS_SOC_RESERVE_FLOOR, "Reserve floor")
    _clean(axes[1], "State of charge (%)", "")
    axes[1].set_ylim(0, 118)
    _dates(axes[1])
    _legend_below(axes[1], ncols=2, pad=0.30)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


# Figures earlier versions drew and this one does not: removed when a run of
# the same name is drawn again, so a folder never mixes two versions.
_RETIRED = ("rating", "ampacity")


def run_figures(cfg, result: pd.DataFrame, out_dir: Path, reference=None) -> list:
    """Figures that describe one run; the cost figure compares only with a reference."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []

    def target(name: str) -> Path:
        made.append(out_dir / f"{cfg.stem}_{name}.png")
        return made[-1]

    corridor_current(cfg, result, target("current"))
    current_duration(cfg, result, target("duration"))
    power_flow(cfg, result, target("power"))
    corridor_segments(cfg, result, target("segments"))
    voltage_timeseries(result, target("voltage"))
    technical_cost(cfg, result, target("cost"), reference)
    if cfg.dlr_mode == 2:
        rating_vs_weather(cfg, result, target("rating_drivers"))
    if cfg.storage_enabled:
        storage_operation(cfg, result, target("storage"))
    for name in _RETIRED:
        (out_dir / f"{cfg.stem}_{name}.png").unlink(missing_ok=True)
    return made


# ── Scenario matrix ──────────────────────────────────────────────────────────
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
