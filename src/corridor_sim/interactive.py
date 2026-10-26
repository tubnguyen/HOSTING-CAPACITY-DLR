"""Set up a run by answering questions instead of typing options.

`corridor-sim` with no options in a terminal, or with `-i`, lands here. Each
question lists its options and offers a default in brackets: Enter takes the
default, and an answer that matches no option is asked again, with the
options it can be. Options given on the command line alongside `-i` become
the defaults offered.

The answers build the same Config the options would, through the same
validation, and the equivalent command is printed before anything runs, so a
run set up here can be repeated or edited without the questions.
"""
from __future__ import annotations

import re
import shlex
import sys

import pandas as pd

from . import constants as C
from .config import (
    DER_NAMES,
    Config,
    RunOptions,
    from_record,
    to_argv,
    to_record,
)

MAX_ADDED = 10                      # plants or loads added in one session
DEFAULT_ADDED_MW = 50.0
DEFAULT_POINT = "SUB_C"
RATING_OPTIONS = [
    ("0", "static", "the fixed static rating, weather ignored"),
    ("1", "ambient-adjusted", "measured air temperature and sun, low fixed wind"),
    ("2", "full weather", "measured air temperature, sun and wind"),
]
RATING_ALIASES = {"static": "0", "ambient": "1", "ambient-adjusted": "1", "full": "2",
                  "full-weather": "2", "full weather": "2", "weather": "2"}
KIND_TEXT = {"wind": "wind farm, output from the weather like WF_1 to WF_3",
             "solar": "solar plant, the PV_1 output scaled to its rating"}
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


class Cancelled(Exception):
    """The questions were ended (end of input) before the run was set up."""


def is_terminal() -> bool:
    """Whether a person is at the keyboard, so asking questions makes sense."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


class Prompter:
    """Asks one question at a time and only returns an answer that fits it.

    `ask` and `say` default to input and print, looked up when called; tests
    pass their own.
    """

    def __init__(self, ask=None, say=None):
        self._ask = ask or (lambda prompt: input(prompt))
        self.say = say or print

    def read(self, prompt: str) -> str:
        try:
            return self._ask(prompt).strip()
        except EOFError:
            raise Cancelled from None

    def choose(self, prompt: str, options, default: str, aliases=None,
               numbered: bool = False) -> str:
        """One of `options`, a list of (key, description), typed by key.

        `numbered` also lists a number per option and accepts it.
        """
        width = max(len(key) for key, _ in options)
        lookup = {key.lower(): key for key, _ in options}
        lookup.update({alias.lower(): key for alias, key in (aliases or {}).items()})
        for i, (key, text) in enumerate(options, start=1):
            number = f"{i}  " if numbered else ""
            self.say(f"       {number}{key:<{width}}  {text}")
            if numbered:
                lookup[str(i)] = key
        keys = ", ".join(key for key, _ in options)
        while True:
            answer = self.read(f"     {prompt} [{default}]: ")
            if not answer:
                return default
            if answer.lower() in lookup:
                return lookup[answer.lower()]
            self.say(f"     '{answer}' is not an option. Type one of: {keys}"
                     + (f", or its number 1 to {len(options)}" if numbered else ""))

    def yes_no(self, prompt: str, default: bool) -> bool:
        shown = "yes" if default else "no"
        while True:
            answer = self.read(f"     {prompt} (yes or no) [{shown}]: ").lower()
            if not answer:
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            self.say(f"     '{answer}' is not an option. Type yes or no")

    def number(self, prompt: str, default: float, low: float, high: float,
               integer: bool = False, unit: str = "") -> float:
        """A number from `low` to `high`; a unit typed after it is ignored."""
        bounds = f"{low:g} to {high:g}"
        while True:
            answer = self.read(f"     {prompt} ({bounds}) [{default:g}]: ")
            if not answer:
                return default
            text = answer.lower().removesuffix(unit.lower()).strip() if unit else answer
            try:
                value = int(text) if integer else float(text)
            except ValueError:
                kind = "a whole number" if integer else "a number"
                self.say(f"     '{answer}' is not {kind}. Type {kind} from {bounds}")
                continue
            if low <= value <= high:
                return value
            self.say(f"     {value:g} is outside {bounds}")

    def date(self, prompt: str, default: str, first: pd.Timestamp | None,
             last: pd.Timestamp | None) -> str:
        """A day as YYYY-MM-DD, from `first` to `last` when they are known."""
        bounds = (f" ({first:%Y-%m-%d} to {last:%Y-%m-%d})"
                  if first is not None and last is not None else "")
        while True:
            answer = self.read(f"     {prompt}{bounds} [{default}]: ") or default
            try:
                day = pd.Timestamp(answer, tz="UTC").normalize()
            except (ValueError, TypeError):
                self.say(f"     '{answer}' is not a date. Type it as YYYY-MM-DD")
                continue
            if first is not None and last is not None and not first <= day <= last:
                self.say(f"     {day:%Y-%m-%d} is outside the data{bounds}")
                continue
            return f"{day:%Y-%m-%d}"


def _coverage(cfg: Config):
    """The window the input data covers, or (None, None) if it cannot be read."""
    from .dataio import coverage
    try:
        return coverage(cfg.data_dir)
    except (OSError, ValueError, KeyError, IndexError):
        return None, None


def _point_options(points=C.CONNECTION_POINTS) -> list:
    return [(point, f"{C.CONNECTION_POINT_NAMES[point]}, "
                    f"{C.CORRIDOR_POSITION_KM[point]:g} km from SUB_A")
            for point in points]


def _section(p: Prompter, number: int, total: int, title: str) -> None:
    p.say("")
    p.say(f"  [{number}/{total}] {title}")


def _built_in_plants(p: Prompter, base: Config) -> list:
    for name in DER_NAMES:
        kind = C.DER_KIND[name]
        p.say(f"       {name}  {kind:<5}  {C.DER_RATING_MW[name]:>4.0f} MW  "
              f"joins at {C.DER_JOIN_BUS[name]}")
    chosen = [n for n in DER_NAMES if base.der_enabled.get(n, False)]
    default = ("all" if chosen == list(DER_NAMES) else
               "none" if not chosen else ",".join(chosen))
    while True:
        answer = p.read(f"     Connect which: all, none, or names with commas [{default}]: ")
        text = (answer or default).replace(" ", ",")
        names = [s.strip().upper() for s in text.split(",") if s.strip()]
        if names == ["ALL"]:
            return list(DER_NAMES)
        if names == ["NONE"]:
            return []
        unknown = [n for n in names if n not in DER_NAMES]
        if not unknown and names:
            return [n for n in DER_NAMES if n in names]
        shown = ", ".join(f"'{n}'" for n in unknown) or f"'{answer}'"
        p.say(f"     {shown} is not an option. "
              f"Type all, none, or some of {', '.join(DER_NAMES)}")


def _added(p: Prompter, what: str, existing: tuple, ask_one) -> list:
    count = int(p.number(f"How many {what} to add", len(existing), 0, MAX_ADDED, integer=True))
    return [ask_one(i, count, existing[i] if i < len(existing) else None)
            for i in range(count)]


def _one_plant(p: Prompter):
    def ask(i: int, count: int, before) -> dict:
        p.say(f"     Plant {i + 1} of {count}")
        kind = p.choose("Kind", [(k, KIND_TEXT[k]) for k in C.ADDED_DER_KINDS],
                        before.kind if before else "wind")
        mw = p.number("Rating in MW", before.mw if before else DEFAULT_ADDED_MW,
                      0.1, C.ADDED_MAX_MW, unit="mw")
        p.say("     Where it connects:")
        at = p.choose("Connection point", _point_options(),
                      before.at if before else DEFAULT_POINT, numbered=True)
        return {"kind": kind, "mw": mw, "at": at}
    return ask


def _one_load(p: Prompter):
    def ask(i: int, count: int, before) -> dict:
        p.say(f"     Load {i + 1} of {count}")
        mw = p.number("Demand in MW", before.mw if before else DEFAULT_ADDED_MW,
                      0.1, C.ADDED_MAX_MW, unit="mw")
        p.say("     Where it connects:")
        at = p.choose("Connection point", _point_options(),
                      before.at if before else DEFAULT_POINT, numbered=True)
        return {"mw": mw, "at": at, "pf": before.pf if before else 1.0}
    return ask


def _window(p: Prompter, base: Config) -> tuple:
    first, end = _coverage(base)
    last_day = (end - pd.Timedelta(days=1)).normalize() if end is not None else None
    if first is not None:
        p.say(f"     The input data covers {first:%Y-%m-%d} to {last_day:%Y-%m-%d}.")
    start_default = base.start
    if first is not None and not first <= pd.Timestamp(base.start, tz="UTC") <= last_day:
        start_default = f"{first:%Y-%m-%d}"
    start = p.date("First day", start_default, first, last_day)
    most = (int((end - pd.Timestamp(start, tz="UTC")) / pd.Timedelta(days=1))
            if end is not None else 3660)
    default = base.days if base.days is not None else max(
        1, int((base.end_ts - base.start_ts) / pd.Timedelta(days=1)))
    days = int(p.number("Days to simulate", min(default, most), 1, most, integer=True,
                        unit="days"))
    return start, days


def _run_name(p: Prompter, automatic: str, label: str) -> str:
    shown = label or f"automatic: {automatic}"
    while True:
        answer = p.read(f"     Name for the run's folder [{shown}]: ")
        if not answer:
            return label
        if answer.lower() in ("auto", "automatic"):
            return ""
        if _NAME.fullmatch(answer):
            return answer
        p.say("     Use letters, digits, '_', '-' and '.' only, starting with a "
              "letter or digit")


def _describe(cfg: Config, options: RunOptions) -> list:
    built = [n for n in DER_NAMES if cfg.der_enabled.get(n, False)]
    plants = ", ".join(built) or "none of the built-in plants"
    added = "; ".join(f"{q.name} {q.kind} {q.mw:g} MW at {q.at}" for q in cfg.added_der)
    loads = "; ".join(f"{q.name} {q.mw:g} MW at {q.at}" for q in cfg.added_loads)
    battery = (f"{cfg.storage_p_mw:g} MW / {cfg.storage_e_mwh:g} MWh at {cfg.storage_bus}"
               if cfg.storage_enabled else "none")
    outputs = [f"{cfg.out_dir}/{cfg.stem}/", "figures" if options.plots else "no figures"]
    if options.compare:
        outputs.append("compared with the static rating")
    rows = [("Plants", f"{plants}{' + ' + added if added else ''} "
                       f"({cfg.der_fleet_mw:g} MW)"),
            ("Loads", loads or "none added"),
            ("Battery", battery),
            ("Corridor", f"{cfg.conductor_label}, {RATING_OPTIONS[cfg.dlr_mode][1]} rating"),
            ("Window", f"{cfg.start}, {cfg.days} day{'s' if cfg.days != 1 else ''}"),
            ("Outputs", ", ".join(outputs))]
    return [f"     {name:<9} {text}" for name, text in rows]


def ask_scenario(base: Config, options: RunOptions = RunOptions(), ask=None, say=None):
    """Ask for every choice of a run, defaulting to `base` and `options`.

    Returns the (Config, RunOptions) to run, or None when the answer to
    "start the run?" is no. Raises Cancelled at the end of input.
    """
    p = Prompter(ask, say)
    say = p.say
    total = 8
    say("corridor-sim  ·  set up a run")
    say("  Answer each question, or press Enter to take the default in [brackets].")
    say("  Ctrl+C stops at any point; nothing runs until you confirm at the end.")

    _section(p, 1, total, "Plants already on the corridor")
    der = _built_in_plants(p, base)

    _section(p, 2, total, "Added plants")
    say("     Wind or solar plants of any size, each with its own step-up transformer.")
    plants = _added(p, "plants", base.added_der, _one_plant(p))

    _section(p, 3, total, "Large loads")
    say("     A constant demand on a 110 kV busbar, such as a data centre or electrolyser.")
    loads = _added(p, "loads", base.added_loads, _one_load(p))

    _section(p, 4, total, "Battery")
    storage = p.yes_no(f"Connect the {base.storage_p_mw:g} MW / {base.storage_e_mwh:g} MWh "
                       f"battery?", base.storage_enabled)
    storage_at = base.storage_at
    if storage:
        say("     Where it connects:")
        storage_at = p.choose("Connection point", _point_options(C.STORAGE_POINTS),
                              base.storage_at, numbered=True)

    _section(p, 5, total, "Corridor conductor")
    conductors = [(name, f"{opt['label']}, static rating {opt['max_i_ka'] * 1000:.0f} A"
                         + (", as built" if name == "single" else ", reinforcement"))
                  for name, opt in C.CONDUCTOR_OPTIONS.items()]
    conductor = p.choose("Conductor", conductors, base.conductor)

    _section(p, 6, total, "Line rating")
    rating = int(p.choose("Rating mode", [(key, f"{name:<16}  {text}")
                                          for key, name, text in RATING_OPTIONS],
                          str(base.dlr_mode), aliases=RATING_ALIASES))

    _section(p, 7, total, "Time window")
    start, days = _window(p, base)

    answers = {"der_enabled": der, "added_der": plants, "added_loads": loads,
               "storage_enabled": storage, "storage_at": storage_at,
               "conductor": conductor, "dlr_mode": rating,
               "start": start, "days": days}
    draft = from_record({**to_record(base), **answers})

    _section(p, 8, total, "Outputs")
    compare = False
    if rating > 0:
        compare = p.yes_no("Also run the static rating and compare the two? "
                           "Slower the first time", options.compare)
    plots = p.yes_no("Draw the figures?", options.plots)
    label = _run_name(p, from_record({**to_record(draft), "label": ""}).stem, base.label)

    cfg = from_record({**to_record(draft), "label": label})
    chosen = options._replace(plots=plots, compare=compare, interactive=False)

    say("")
    say("  Ready to run")
    for line in _describe(cfg, chosen):
        say(line)
    # A bare corridor-sim in a terminal asks again, so the window is spelt out
    # when nothing else would be.
    argv = to_argv(cfg, chosen) or ["--days", str(cfg.days)]
    say("  The same run without the questions:")
    say("     " + shlex.join(["corridor-sim", *argv]))
    if not p.yes_no("Start the run now?", True):
        say("  Nothing was run.")
        return None
    say("")
    return cfg, chosen
