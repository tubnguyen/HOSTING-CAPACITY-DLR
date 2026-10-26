"""Figures for a single run and for the scenario matrix.

The single-run figures follow the network: the current from all plants
against every limit, how far above each limit it reaches and for how long,
the power flow from the plants to the grid interface, the current along the
corridor and the voltages. Every chart uses one measurement axis per panel,
a fixed colour per quantity, and a legend plus direct labels wherever a
reader would otherwise have to match a colour by eye.

The current figures are built around thermal loading. They show the current
every connected plant would produce at full output, the rating the weather
allows, and the two fixed conductor ratings, with a second scale reading the
same amperes as a percentage of the as-built conductor's static rating. How
far and how long the generation exceeds a rating is what a reader needs:
the excess is what curtailment has to remove, so the exceedance gives the
curtailment without a separate curve for it.

Colours keep one meaning across figures: orange is what the plants offer,
aqua what actually flows, blue the operative rating or the interface, grey
the heat balance and every fixed reference.

The topology figure is the exception, a diagram rather than a chart: it
draws the network the run was built with, one colour per kind of asset.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
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


def _clean(ax, ylabel="", title="", xlabel="", pad=10):
    ax.set_title(title, loc="left", pad=pad)
    ax.set_ylabel(ylabel)
    ax.set_xlabel(xlabel)
    ax.grid(True, axis="y", alpha=0.7)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def _legend_below(ax, ncols=3, pad=0.16, handles=None):
    """Place the legend under the axes, where it cannot cover data or a limit label.

    Key lines are drawn full strength whatever the series does on the axes: a
    trace thinned to stay readable over months becomes a hairline in a legend
    and stops naming its colour.
    """
    extra = {} if handles is None else {"handles": handles}
    legend = ax.legend(loc="upper left", bbox_to_anchor=(0.0, -pad), ncols=ncols,
                       handlelength=1.6, columnspacing=1.6, borderaxespad=0.0, **extra)
    for line in legend.get_lines():
        line.set_linewidth(max(line.get_linewidth(), 1.6))
        line.set_alpha(1.0)
    return legend


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
    if days >= 365:
        return "year"
    return f"{days:g} day" + ("" if days == 1 else "s")


def _current_references(cfg, ax, label=True, inside=False, x=0.01):
    """The single-conductor static rating and the twin-bundle reinforcement.

    `inside` keeps the labels within the axes, for a panel whose right margin
    carries the thermal-loading scale.
    """
    twin = f"{report.TWIN['label']} static {report.TWIN_STATIC_A:.0f} A"
    if (cfg.dlr_mode > 0 and cfg.rating_cap_a is not None
            and abs(cfg.rating_cap_a - report.TWIN_STATIC_A) < 1.0):
        twin += " = DLR cap"
    single = f"{report.SINGLE['label']} static {report.SINGLE_STATIC_A:.0f} A"
    _reference(ax, report.SINGLE_STATIC_A, single if label else "", inside=inside, x=x)
    _reference(ax, report.TWIN_STATIC_A, twin if label else "", inside=inside, x=x)


def _loading_axis(ax):
    """Second scale on the right reading amperes as thermal loading.

    100 % is the as-built single conductor's static rating, so the same curve
    answers both questions a rating study asks: how many amperes, and how far
    past what the conductor is rated to carry.
    """
    static = report.SINGLE_STATIC_A
    axis = ax.secondary_yaxis("right", functions=(lambda a: 100.0 * a / static,
                                                  lambda pct: pct * static / 100.0))
    axis.set_ylabel(f"Thermal loading (% of {static:.0f} A)", color=INK_2)
    axis.spines["right"].set_visible(False)
    return axis


def _exceedance(series, limit) -> tuple[float, float]:
    """Share of the window and hours for which `series` is above `limit`."""
    above = series > limit
    return 100.0 * float(above.mean()), float(above.sum()) * C.DT_H


# ── Single run ───────────────────────────────────────────────────────────────
def corridor_current(cfg, result: pd.DataFrame, path: Path):
    """The corridor current from all plants against every rating it meets.

    Top: the current every connected plant would produce at full output, the
    operative rating and the heat-balance ampacity behind it, against the
    as-built conductor's static rating and the twin-bundle reinforcement. The
    shading reads bottom up - everything the as-built conductor could not
    have carried, and inside it the part the dynamic rating could not carry
    either, which is what curtailment removes. Bottom: the monthly mean of
    the same current, with the range each month covered.
    """
    _style()
    ok = result[result["converged"]]
    t = ok.index
    plant, name, prefix = report.plant_current(ok), "Current from all plants", "plant"
    if plant is None:                       # a result saved before it was recorded
        plant, name, prefix = (report.carried_current(ok),
                               "Corridor current (after curtailment)", "carried")
    rating = corridor_series(ok, "rating_{zone}_a", "min")
    weather = (corridor_series(ok, "rating_{zone}_weather_a", "min")
               if cfg.dlr_mode > 0 else None)
    static = report.SINGLE_STATIC_A

    fig, (ax, axm) = plt.subplots(2, 1, figsize=(10, 8.0),
                                  gridspec_kw={"height_ratios": [1.6, 1.1], "hspace": 0.62})
    # Over months, 15-minute traces pile into a solid block. Thinner lines and
    # a faded heat balance keep the three apart; over days they stay crisp.
    lw, faint = (0.55, 0.45) if not report.is_short_window(result) else (1.0, 1.0)

    # The shaded bands, in one colour family. At the static rating the two
    # coincide, so only one is drawn and it is named for what it is there.
    handles = []
    ax.fill_between(t, static, plant, where=plant > static, interpolate=True,
                    color=ORANGE, alpha=0.13, lw=0, zorder=0)
    if cfg.dlr_mode > 0:
        ax.fill_between(t, rating, plant, where=plant > rating + 0.5, interpolate=True,
                        color=ORANGE, alpha=0.38, lw=0, zorder=0)
        weather_line = ax.plot(t, weather, color=MUTED, lw=0.9 * lw, ls=(0, (4, 2)),
                               alpha=faint, label="DLR ampacity, heat balance (weather)")[0]
    handles.append(ax.plot(t, plant, color=ORANGE, lw=lw, label=name)[0])
    if cfg.dlr_mode > 0:
        handles.append(ax.plot(t, rating, color=BLUE, lw=1.3 * lw,
                               label="Operative DLR rating")[0])
        handles.append(weather_line)
        handles.append(Patch(color=ORANGE, alpha=0.13, lw=0,
                             label=f"Above {static:.0f} A, carried by DLR"))
        handles.append(Patch(color=ORANGE, alpha=0.38, lw=0,
                             label="Above the operative rating, curtailed"))
    else:
        handles.append(Patch(color=ORANGE, alpha=0.13, lw=0,
                             label=f"Above {static:.0f} A, curtailed"))
    _current_references(cfg, ax, inside=True)
    tops = [s.max() for s in (plant, rating, weather) if s is not None]
    ax.set_ylim(0, max(max(tops), report.TWIN_STATIC_A) * 1.12)
    _loading_axis(ax)
    _dates(ax)
    _clean(ax, "Amperes", f"{name}  ·  {RATING_LABELS[cfg.dlr_mode]} on "
           f"{cfg.conductor_label}  ·  15-minute values", pad=26)
    pct, hours = _exceedance(plant, static)
    note = f"Above {static:.0f} A for {pct:.1f} % of the window ({hours:,.0f} h)"
    if cfg.dlr_mode > 0:
        over_pct, over_h = _exceedance(plant, rating)
        note += f", above the operative rating for {over_pct:.1f} % ({over_h:,.0f} h)"
    ax.annotate(note + ".", xy=(0.0, 1.0), xycoords="axes fraction", xytext=(0, 5),
                textcoords="offset points", ha="left", va="bottom", fontsize=8.5, color=INK_2)

    # Monthly means as a line, with the band each month covered. A bar per
    # month gives the mean alone; the band says whether the month sat near it.
    means = report.period_means(result)
    low, high = means[f"{prefix}_min"], means[f"{prefix}_max"]
    n = len(means)
    x = np.arange(n)
    if n >= 2:
        axm.fill_between(x, low, high, color=ORANGE, alpha=0.16, lw=0, zorder=0)
    else:
        axm.vlines(x, low, high, color=ORANGE, alpha=0.3, lw=14)
    band = "Range over the period" if report.is_short_window(result) else "Range in the month"
    mhandles = [axm.plot(x, means[f"{prefix}_mean"], color=ORANGE, lw=1.8, marker="o", ms=4,
                         label=f"Mean {name[0].lower() + name[1:]}")[0],
                Patch(color=ORANGE, alpha=0.16, lw=0, label=band)]
    if cfg.dlr_mode > 0 and means["rating_mean"].notna().any():
        mhandles.insert(1, axm.plot(x, means["rating_mean"], color=BLUE, lw=1.6, marker="o",
                                    ms=4, label="Mean operative DLR rating")[0])
    _current_references(cfg, axm, inside=True)
    axm.set_xticks(x, list(means.index))
    axm.set_xlim(-0.4, max(n - 0.6, 0.4))
    axm.set_ylim(0, max(float(np.nanmax(high.to_numpy())), report.TWIN_STATIC_A) * 1.14)
    _loading_axis(axm)
    _clean(axm, "Amperes", "Mean over the period" if report.is_short_window(result)
           else "Monthly mean, with the range each month covered")
    _legend_below(ax, ncols=3, pad=0.20, handles=handles)
    _legend_below(axm, ncols=3, pad=0.22, handles=mhandles)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def current_duration(cfg, result: pd.DataFrame, path: Path):
    """How long the current from all plants stays above each rating.

    Four lines and nothing else: the current the generation produces, the
    operative dynamic rating, and the two fixed conductor ratings. Every
    crossing is read off the axes - the lower axis in per cent of the window,
    the upper in hours of it - and the box gives the same three exceedances
    as numbers.
    """
    _style()
    ok = result[result["converged"]]
    plant, name = report.plant_current(ok), "Current from all plants"
    if plant is None:
        plant, name = report.carried_current(ok), "Corridor current (after curtailment)"
    rating = corridor_series(ok, "rating_{zone}_a", "min")
    hours = len(ok) * C.DT_H
    static = report.SINGLE_STATIC_A

    def curve(series):
        values = np.sort(series.dropna().to_numpy())[::-1]
        return np.linspace(0.0, 100.0, len(values)), values

    fig, ax = plt.subplots(figsize=(10, 5.0))
    ax.plot(*curve(plant), color=ORANGE, lw=1.9, label=name)
    if cfg.dlr_mode > 0:
        ax.plot(*curve(rating), color=BLUE, lw=1.6, label="Operative DLR rating")
    _current_references(cfg, ax, inside=True, x=0.30)

    lines = []
    for limit_name, limit in ((f"{report.SINGLE['label']} static {static:.0f} A", static),
                              ("the operative rating", rating),
                              (f"{report.TWIN['label']} static {report.TWIN_STATIC_A:.0f} A",
                               report.TWIN_STATIC_A)):
        if cfg.dlr_mode == 0 and limit_name == "the operative rating":
            continue                        # the static rating, already on the line above
        share, above_h = _exceedance(plant, limit)
        lines.append(f"above {limit_name}: {share:.1f} %  ·  {above_h:,.0f} h")
    # Where the generation crosses a fixed rating, drop a guide to the axis so
    # the share can be read off it as well as out of the box.
    for limit in (static, report.TWIN_STATIC_A):
        share = _exceedance(plant, limit)[0]
        if share > 0.0:
            ax.plot([share, share], [0.0, limit], color=INK_2, lw=0.9, ls=(0, (2, 2)), zorder=1)
    ax.text(0.985, 0.96, name + "\n" + "\n".join(lines),
            transform=ax.transAxes, ha="right", va="top", fontsize=8.8, color=INK,
            linespacing=1.5, bbox={"boxstyle": "round,pad=0.5", "fc": SURFACE, "ec": GRID})

    ax.set_xlim(0, 100)
    ax.set_ylim(0, max(ax.get_ylim()[1], report.TWIN_STATIC_A * 1.1) * 1.08)
    _loading_axis(ax)
    hours_axis = ax.secondary_xaxis("top", functions=(lambda pct: pct / 100.0 * hours,
                                                      lambda h: 100.0 * h / max(hours, 1e-9)))
    hours_axis.set_xlabel("Time exceeded (hours)", color=INK_2)
    hours_axis.spines["top"].set_visible(False)
    _clean(ax, "Amperes", f"Corridor current duration  ·  {RATING_LABELS[cfg.dlr_mode]}",
           f"Time exceeded (% of {_period_name(result)})", pad=32)
    _legend_below(ax, ncols=2, pad=0.20)
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
    # A midpoint connection splits a segment in two; past five segments the
    # labels take a line per end and the figure widens, so none overlap.
    crowded = len(lines) > 5
    arrow, comma = (" →\n", "\n") if crowded else (" → ", ", ")
    labels = [f"{report.SEGMENTS[line][0].replace(' to ', arrow)}\n"
              f"{report.SEGMENTS[line][1]:g} km{comma}mean {ok[f'p_{line}_mw'].mean():.0f} MW"
              for line in lines]

    fig, ax = plt.subplots(figsize=(max(10.0, 1.65 * len(lines)) if crowded else 10, 4.6))
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
    _legend_below(ax, ncols=2, pad=0.34 if crowded else 0.24)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def voltage_timeseries(result: pd.DataFrame, path: Path):
    """Voltage at every corridor bus, head to grid interface, at 15-minute resolution."""
    _style()
    ok = result[result["converged"]]
    buses = [b for b in report.CORRIDOR_BUSES if f"v_{b}_pu" in ok.columns]
    fig, ax = plt.subplots(figsize=(10, 4.4))
    # A midpoint connection adds buses past the six colours; those repeat a
    # colour dashed, so every bus keeps a line of its own.
    for i, bus in enumerate(buses):
        ax.plot(ok.index, ok[f"v_{bus}_pu"], color=SERIES[i % len(SERIES)], lw=0.9,
                ls="-" if i < len(SERIES) else (0, (4, 2)), label=bus)
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


# ── Network topology ─────────────────────────────────────────────────────────
TOPOLOGY_KINDS = {               # colour, symbol, legend label
    "wind": (BLUE, "W", "wind farm"),
    "solar": (SERIES[3], "S", "solar plant"),
    "battery": (AQUA, "B", "battery"),
    "load": (SERIES[4], "L", "added load"),
}
_TOPOLOGY_BUSES = ("SUB_A", "TAP_PV", "MID_A_C", "TAP_W", "SUB_C", "MID_C_PCC", "TAP_B", "PCC")
_BUS_GAP = 1.3                   # diagram units between neighbouring busbars


def topology_elements(cfg) -> list:
    """What the run connects: plants switched on, the battery if on, added loads.

    Each is a dict of name, kind, rating, the corridor bus it joins, whether
    it was added to the shipped network, and the text that labels it.
    """
    items = []
    for unit in cfg.units:
        if not cfg.unit_enabled(unit):
            continue
        mw = cfg.unit_rating_mw[unit]
        items.append(dict(name=unit, kind=cfg.unit_kind[unit], mw=mw, bus=cfg.unit_bus[unit],
                          added=unit not in C.DER_JOIN_BUS, text=f"{mw:g} MW"))
    if cfg.storage_enabled:
        items.append(dict(name=C.BESS_NAME, kind="battery", mw=cfg.storage_p_mw,
                          bus=cfg.storage_bus, added=cfg.storage_bus != C.STORAGE_DEFAULT_POINT,
                          text=f"{cfg.storage_p_mw:g} MW / {cfg.storage_e_mwh:g} MWh"))
    for load in cfg.added_loads:
        items.append(dict(name=load.name, kind="load", mw=load.mw, bus=load.at, added=True,
                          text=f"{load.mw:g} MW"))
    return items


def spread(wanted, gap: float) -> list:
    """Positions as close to `wanted` as possible, never closer than `gap`.

    Items that would crowd are gathered into runs spaced `gap` apart and
    centred on where they wanted to be; order is kept, ties in input order.
    """
    order = sorted(range(len(wanted)), key=lambda i: wanted[i])
    runs = []                                   # [members, first position]
    for i in order:
        runs.append([[i], wanted[i]])
        while len(runs) > 1 and runs[-2][1] + gap * len(runs[-2][0]) > runs[-1][1] + 1e-9:
            members = runs[-2][0] + runs[-1][0]
            centre = sum(wanted[m] for m in members) / len(members)
            runs[-2:] = [[members, centre - gap * (len(members) - 1) / 2]]
    placed = [0.0] * len(wanted)
    for members, first in runs:
        for k, m in enumerate(members):
            placed[m] = first + k * gap
    return placed


def network_topology(cfg, path: Path):
    """Single-line diagram of the network this run was built with.

    The 110 kV busbars evenly spaced along the corridor from SUB_A to the
    PCC, each with its distance from the head; generation above, the battery
    and added loads below, each labelled with its rating; added connections
    dashed; the two rating zones bracketed underneath. Plants switched off
    are left out. Even spacing rather than distance keeps TAP_B and the PCC,
    1 km apart, from landing on each other.
    """
    _style()
    buses = [b for b in _TOPOLOGY_BUSES if b not in C.MIDPOINT_BUSES or b in cfg.midpoints_used]
    x = {b: _BUS_GAP * i for i, b in enumerate(buses)}
    items = topology_elements(cfg)
    for side in (1, -1):
        members = [e for e in items if (e["kind"] in ("wind", "solar")) == (side > 0)]
        widest = max((len(e["text"]) for e in members), default=0)
        gap = max(0.66, 0.068 * widest + 0.15)
        for e, ex in zip(members, spread([x[e["bus"]] for e in members], gap), strict=True):
            e["side"], e["x"] = side, ex
    lo = min([0.0] + [e["x"] for e in items])
    hi = max([x["PCC"]] + [e["x"] for e in items])

    fig, ax = plt.subplots(figsize=(max(12.0, 0.95 * (hi - lo) + 3.5), 5.6))
    ax.plot([0, x["PCC"]], [0, 0], color=INK, lw=4, solid_capstyle="butt", zorder=2)
    for b, bx in x.items():
        ax.plot([bx, bx], [-0.2, 0.2], color=INK, lw=6, solid_capstyle="butt", zorder=3)
        ax.text(bx - 0.08, 0.12, b, ha="right", va="bottom", fontsize=8.5, fontweight="bold")
        ax.text(bx - 0.08, -0.12, f"{C.CORRIDOR_POSITION_KM[b]:g} km", ha="right", va="top",
                fontsize=8, color=INK_2)
    ax.annotate("", xy=(x["PCC"] + 1.1, 0), xytext=(x["PCC"] + 0.05, 0),
                arrowprops={"arrowstyle": "-|>", "color": INK, "lw": 2})
    ax.text(x["PCC"] + 1.2, 0, "grid", va="center", fontsize=9, fontweight="bold")

    z1 = C.CORRIDOR_POSITION_KM["SUB_C"]
    z2 = C.CORRIDOR_LENGTH_KM - z1
    for start, end, zone, km in ((0, x["SUB_C"], "Z1", z1), (x["SUB_C"], x["PCC"], "Z2", z2)):
        ax.plot([start, start, end, end], [-2.43, -2.55, -2.55, -2.43], color=MUTED, lw=1.1)
        ax.text((start + end) / 2, -2.65, f"zone {zone}  ·  {km:g} km", ha="center",
                va="top", fontsize=8.5, color=INK_2)

    for e in items:
        colour, symbol, _ = TOPOLOGY_KINDS[e["kind"]]
        bx, ex, side = x[e["bus"]], e["x"], e["side"]
        # Alternate the height of the horizontal run by bus, so the runs of
        # neighbouring busbars cannot lie on top of each other.
        knee = side * (0.6 if buses.index(e["bus"]) % 2 else 0.75)
        ax.plot([bx, bx, ex, ex], [side * 0.2, knee, knee, side * 1.13], color=INK_2, lw=1.2,
                ls=(0, (3, 2)) if e["added"] else "-", zorder=1)
        ax.scatter([ex], [side * 1.35], s=560, color=colour, zorder=4, edgecolor=SURFACE,
                   lw=1.5)
        ax.text(ex, side * 1.35, symbol, ha="center", va="center", color="white", fontsize=9,
                fontweight="bold", zorder=5)
        ax.text(ex, side * 1.65, f"{e['name']}\n{e['text']}", ha="center",
                va="bottom" if side > 0 else "top", fontsize=8.3, linespacing=1.3)
    if not items:
        ax.text(x["PCC"] / 2, 1.2, "no plants, battery or added loads connected",
                ha="center", fontsize=9, color=INK_2)

    shown = [k for k in TOPOLOGY_KINDS if any(e["kind"] == k for e in items)]
    handles = [Line2D([], [], marker="o", ls="", ms=11, color=TOPOLOGY_KINDS[k][0],
                      label=TOPOLOGY_KINDS[k][2]) for k in shown]
    if any(e["added"] for e in items):
        handles.append(Line2D([], [], color=INK_2, ls=(0, (3, 2)), label="added connection"))
    if handles:
        ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(0, 0.02),
                  ncols=len(handles), frameon=False)

    rating = {0: "static rating", 1: "ambient-adjusted DLR", 2: "full-weather DLR"}[cfg.dlr_mode]
    generation = sum(e["mw"] for e in items if e["kind"] in ("wind", "solar"))
    title = (f"Network  ·  {cfg.conductor_label} corridor, {C.CORRIDOR_LENGTH_KM:g} km, static "
             f"{cfg.static_rating_a:.0f} A, {rating}  ·  {generation:g} MW generation")
    if cfg.added_loads:
        title += f", {cfg.added_load_mw:g} MW added load"
    ax.set_title(title, loc="left", fontsize=11, fontweight="bold")
    ax.set_xlim(lo - 1.0, hi + 1.8)
    ax.set_ylim(-3.1, 2.7)
    ax.axis("off")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


# Figures earlier versions drew and this one does not: removed when a run of
# the same name is drawn again, so a folder never mixes two versions. The
# technical cost is still in the summary tables and the metrics; it is the
# figure that is gone.
_RETIRED = ("rating", "ampacity", "cost")


def run_figures(cfg, result: pd.DataFrame, out_dir: Path) -> list:
    """Every figure that describes one run.

    A run is drawn on its own. Nothing here sets two runs side by side: a
    comparison against the static reference is a table, in the summary CSV.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []

    def target(name: str) -> Path:
        made.append(out_dir / f"{cfg.stem}_{name}.png")
        return made[-1]

    network_topology(cfg, target("topology"))
    corridor_current(cfg, result, target("current"))
    current_duration(cfg, result, target("duration"))
    power_flow(cfg, result, target("power"))
    corridor_segments(cfg, result, target("segments"))
    voltage_timeseries(result, target("voltage"))
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
