"""Setting a run up by answering questions."""
from __future__ import annotations

import pytest

from corridor_sim import cli, interactive
from corridor_sim.config import build_config, parse_args


class Script:
    """Answers questions from a list, and keeps what was said."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts, self.said = [], []

    def ask(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def say(self, text=""):
        self.said.append(text)

    @property
    def text(self) -> str:
        return "\n".join(self.said)


def _run(script, argv=()):
    cfg, options = parse_args(list(argv))
    return interactive.ask_scenario(cfg, options, ask=script.ask, say=script.say)


# Enter at every question: the built-in plants, no plants and no loads added,
# no battery, single conductor, rating mode 1, from 2024-01-01 for 30 days, no
# comparison, figures, automatic name, and start.
DEFAULTS = [""] * 12
RATING, FIRST_DAY, DAYS = 5, 6, 7


def test_enter_at_every_question_gives_the_default_run():
    script = Script(*DEFAULTS)
    cfg, options = _run(script)
    default, default_options = parse_args([])
    assert cfg == default
    assert options == default_options
    assert script.answers == [], "every question was asked exactly once"
    # Bare, the command would ask again in a terminal, so it names the window.
    assert "     corridor-sim --days 30" in script.said


def test_a_scenario_from_the_answers():
    script = Script(
        "WF_1, PV_1",                       # built-in plants
        "2",                                # plants to add
        "solar", "30 MW", "5",              # PV_2, at the PCC by its number
        "wind", "", "mid_a_c",              # WF_4, default 50 MW, by its name
        "1", "80", "sub_c",                 # one load
        "y", "1",                           # battery, at SUB_A by its number
        "twin",                             # conductor
        "full",                             # rating mode, by name
        "2024-03-01", "3",                  # window
        "yes",                              # compare
        "no",                               # figures
        "my_case",                          # name
        "",                                 # start
    )
    cfg, options = _run(script)
    assert cfg.active_der == ["WF_1", "PV_1", "PV_2", "WF_4"]
    assert [p.spec for p in cfg.added_der] == ["solar:30@PCC", "wind:50@MID_A_C"]
    assert [q.spec for q in cfg.added_loads] == ["80@SUB_C"]
    assert cfg.storage_enabled and cfg.storage_bus == "SUB_A"
    assert cfg.conductor == "twin" and cfg.dlr_mode == 2
    assert (cfg.start, cfg.days, cfg.label) == ("2024-03-01", 3, "my_case")
    assert options.compare and not options.plots and not options.interactive
    command = ("corridor-sim --conductor twin --dlr 2 --der WF_1,PV_1 --add-der solar:30@PCC "
               "--add-der wind:50@MID_A_C --add-load 80@SUB_C --storage-at SUB_A "
               "--start 2024-03-01 "
               "--days 3 --label my_case --no-plots --compare")
    assert command in script.text
    assert parse_args(command.split()[1:]) == (cfg, options)


def test_an_answer_that_matches_no_option_is_asked_again():
    script = Script(
        "WF_9", "all",                      # unknown plant
        "lots", "11", "0",                  # not a number, too many
        "0",
        "maybe", "no",
        "triple", "single",
        "3", "1",
        "2024-02-30", "1999-01-01", "2024-01-01", "400", "30",
        "", "", "bad name!", "", "",
    )
    cfg, _ = _run(script)
    assert cfg == parse_args([])[0]
    for complaint in ("'WF_9' is not an option", "'lots' is not a whole number",
                      "11 is outside 0 to 10", "'maybe' is not an option",
                      "'triple' is not an option", "'3' is not an option",
                      "'2024-02-30' is not a date", "1999-01-01 is outside the data",
                      "400 is outside 1 to 366", "Use letters, digits"):
        assert complaint in script.text, complaint


def test_options_given_with_interactive_are_the_defaults_offered():
    # and the added plant's three questions, and where the battery connects
    script = Script(*DEFAULTS, "", "", "", "")
    cfg, options = _run(script, ["-i", "--preset", "dlr2_der4_bess", "--days", "3",
                                 "--add-der", "wind:40@PCC", "--no-plots",
                                 "--storage-at", "MID_C_PCC"])
    assert cfg.dlr_mode == 2 and cfg.storage_enabled and cfg.days == 3
    assert cfg.storage_bus == "MID_C_PCC", "the battery's place is a default offered too"
    assert [p.spec for p in cfg.added_der] == ["wind:40@PCC"]
    assert not options.plots
    assert any("[1]" in prompt for prompt in script.prompts if "plants to add" in prompt)


def test_static_rating_has_nothing_to_compare_with():
    answers = list(DEFAULTS)
    answers[RATING] = "0"
    script = Script(*answers[:-1])          # one question fewer
    cfg, options = _run(script)
    assert cfg.dlr_mode == 0 and not options.compare
    assert not any("compare" in prompt for prompt in script.prompts)


def test_saying_no_at_the_end_runs_nothing():
    assert _run(Script(*DEFAULTS[:-1], "no")) is None


def test_the_end_of_input_stops_the_questions():
    with pytest.raises(interactive.Cancelled):
        _run(Script("all", "0"))


def test_the_command_asks_with_i_and_never_without_a_terminal(monkeypatch, tmp_path):
    asked = []

    def fake_input(prompt=""):
        asked.append(prompt)
        return "no" if prompt.strip().startswith("Start the run") else ""

    monkeypatch.setattr("builtins.input", fake_input)
    assert cli.main(["-i", "--out", str(tmp_path)]) == 0      # declined at the end
    assert asked and not any(tmp_path.iterdir())

    asked.clear()
    monkeypatch.setattr(interactive, "is_terminal", lambda: True)
    monkeypatch.setattr(cli, "run_scenario", lambda *a, **k: ({}, {}))
    assert cli.main([]) == 0
    assert not asked, "options passed in, even none, are never replaced by questions"


def test_stopping_with_ctrl_c_runs_nothing(monkeypatch, capsys):
    def interrupt(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    assert cli.main(["--interactive"]) == 1
    assert "nothing was run" in capsys.readouterr().err


def test_a_window_inside_the_data_is_offered():
    script = Script(*DEFAULTS[:FIRST_DAY], "2024-12-31", "", *DEFAULTS[DAYS + 1:])
    cfg, _ = _run(script)
    assert (cfg.start, cfg.days) == ("2024-12-31", 1), "30 days would run past the data"
    assert any("(1 to 1)" in prompt for prompt in script.prompts)


def test_an_added_plant_or_load_offers_every_connection_point():
    script = Script("", "1", "", "", "", *DEFAULTS[2:])
    cfg, _ = _run(script)
    assert cfg.added_der[0].at == interactive.DEFAULT_POINT
    for point in ("SUB_A", "MID_A_C", "SUB_C", "MID_C_PCC", "PCC"):
        assert point in script.text
    assert build_config(added_der=[cfg.added_der[0].spec]).added_der == cfg.added_der
