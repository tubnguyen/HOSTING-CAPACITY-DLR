"""Command-line entry point for a single scenario."""
from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

from . import dataio, dlr, network, plots, reference, report, simulate
from .config import from_record, parse_args, to_record

RATING_NAMES = {0: "static", 1: "ambient-adjusted", 2: "full-weather"}


def _write(cfg, result, make_plots: bool, ref=None):
    out_dir = Path(cfg.out_dir) / cfg.stem
    paths = report.write(cfg, result, out_dir, ref)
    if make_plots:
        plots.run_figures(cfg, result, out_dir / "figures", ref)
    return out_dir, paths


def simulate_static_reference(cfg, make_plots: bool = True, progress: bool = False):
    """Simulate a static-rating study, write it as a run of its own, return its result."""
    net, buses = network.build(cfg)
    result = simulate.run(cfg, net, buses, dataio.load_inputs(cfg),
                          progress=progress, label="static")
    _write(cfg, result, make_plots)
    return result


def _static_reference_worker(record: dict, make_plots: bool, progress: bool):
    """Process entry point. A Config does not pickle, so it travels as its record."""
    return simulate_static_reference(from_record(record), make_plots, progress)


def _start_reference(ref_cfg, make_plots: bool, progress: bool):
    """Start the static reference in a second process; (None, None) if there is none.

    Spawned, not forked, on every platform: a fresh interpreter inherits none
    of the parent's threads, which a fork copies unsafely, and behaves the
    same on Linux, macOS and Windows.
    """
    try:
        pool = multiprocessing.get_context("spawn").Pool(processes=1)
    except (OSError, NotImplementedError):
        return None, None
    pending = pool.apply_async(_static_reference_worker,
                               (to_record(ref_cfg), make_plots, progress))
    pool.close()
    return pool, pending


def _finish_reference(ref_cfg, pool, pending, make_plots: bool, progress: bool):
    """The finished static reference, or None if it could not be produced."""
    try:
        if pool is None:
            # No second process available: run it now, after the DLR study.
            result = simulate_static_reference(ref_cfg, make_plots, progress)
        else:
            if progress and not pending.ready():
                print("  static    still running; waiting for it to finish", flush=True)
            result = pending.get()
    except Exception as exc:                      # the DLR run is still worth writing
        print(f"  static    reference run failed ({type(exc).__name__}: {exc}); "
              f"writing the summary without a comparison", file=sys.stderr)
        return None
    finally:
        if pool is not None:
            pool.terminate()
            pool.join()
    return reference.Reference(ref_cfg, result, "simulated")


def run_scenario(cfg, make_plots: bool = True, progress: bool = True, compare: bool = False):
    """Build, simulate, report and plot one scenario. Returns its metrics and paths.

    With `compare`, a DLR scenario is reported against a static-rating run
    that differs in nothing else. A matching run already under cfg.out_dir is
    reused; otherwise one is simulated in a second process while this one
    runs, and written to its own folder, where the next DLR run with the same
    settings finds it. The command line compares unless told --no-compare.
    """
    net, buses = network.build(cfg)
    if progress:
        print(f"  network   {network.summary(cfg, net)}")
        check = dlr.calibration(cfg)
        print(f"  rating    mode {cfg.dlr_mode} ({RATING_NAMES[cfg.dlr_mode]}), "
              f"model against static rating {check['deviation_pct']:+.2f} %")

    # Inputs are loaded before the reference starts, so a dataset that does
    # not cover the window fails here and not halfway through two runs.
    inputs = dataio.load_inputs(cfg)
    if progress:
        print(f"  inputs    {len(inputs['index'])} steps, "
              f"{cfg.start_ts:%Y-%m-%d} to {cfg.end_ts:%Y-%m-%d}")

    ref = ref_cfg = pool = pending = None
    if compare and cfg.dlr_mode > 0:
        ref = reference.find(cfg)
        if ref is None:
            ref_cfg = reference.static_config(cfg)
            pool, pending = _start_reference(ref_cfg, make_plots, progress)
            if progress:
                how = "alongside this run" if pool is not None else "after this run"
                print(f"  static    simulating {ref_cfg.stem} {how}, for the comparison "
                      f"(--no-compare skips it)", flush=True)
        elif progress:
            print(f"  static    reusing {ref.cfg.stem}, same settings, data and model")

    try:
        result = simulate.run(cfg, net, buses, inputs, progress=progress,
                              label="dlr" if pool is not None else "")
    except BaseException:
        if pool is not None:
            pool.terminate()
            pool.join()
        raise
    if ref_cfg is not None:
        ref = _finish_reference(ref_cfg, pool, pending, make_plots, progress)

    out_dir, paths = _write(cfg, result, make_plots, ref)
    if progress:
        print()
        print(report.summary_text(cfg, result, ref))
        print(f"  output -> {out_dir}")
        if ref is not None and ref.origin == "simulated":
            print(f"  static -> {Path(ref.cfg.out_dir) / ref.cfg.stem}")
    return report.metrics(cfg, result), paths


def main(argv=None) -> int:
    """Entry point. Configuration and input problems are reported as messages.

    These are the two failures a user causes and can fix - a bad flag, a
    dataset that does not cover the window - so they exit with a line of text
    rather than a traceback. Anything else is a bug in the model and keeps its
    traceback, which is what a bug report needs.
    """
    try:
        cfg, options = parse_args(argv)
    except (ValueError, KeyError) as exc:
        print(f"corridor-sim: {exc}", file=sys.stderr)
        return 2
    print(f"corridor-sim  ·  {cfg.stem}")
    try:
        run_scenario(cfg, make_plots=options.plots, compare=options.compare)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"corridor-sim: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
