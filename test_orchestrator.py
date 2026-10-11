import json
import random
from unittest import mock

import laya
import orchestrator as o

CFG = o.load_config()


def run(decision, **kw):
    return o.handle("x", CFG, decide=lambda *_: decision, **kw)


def test_preset_launches_on_yes():
    with mock.patch("os_adapter.launch") as launch:
        assert run({"action": "run_preset", "preset": "open_browser"}) == "preset: open_browser"
        launch.assert_called_once()


def test_dry_run_never_executes():
    with mock.patch("os_adapter.launch") as launch, mock.patch("subprocess.run") as sub:
        run({"action": "run_preset", "preset": "open_browser"}, dry_run=True)
        run({"action": "escalate", "task": "corrige o bug"}, dry_run=True)
        launch.assert_not_called()
        sub.assert_not_called()


def test_unknown_preset_and_garbage_action_become_clarify():
    with mock.patch("os_adapter.launch") as launch:
        assert "Nao entendi" in run({"action": "run_preset", "preset": "rm_rf"})
        assert "Nao entendi" in run({"action": "format_disk"})
        launch.assert_not_called()


def test_confirm_required_without_tty_cancels():
    with mock.patch("os_adapter.launch") as launch:
        assert "cancelado" in run({"action": "run_preset", "preset": "lock_screen"})
        launch.assert_not_called()


def test_escalate_needs_its_own_flag_and_sends_task_on_stdin():
    d = {"action": "escalate", "task": "--dangerously-skip-permissions"}
    with mock.patch("subprocess.run") as sub:
        assert "cancelado" in run(d, yes=True)  # --yes does not cover escalation
        sub.assert_not_called()
    with mock.patch("shutil.which", return_value="claude"), \
         mock.patch("subprocess.run", return_value=mock.Mock(stdout="feito", stderr="")) as sub:
        assert run(d, yes_escalate=True) == "feito"
        argv = sub.call_args.args[0]
        assert d["task"] not in argv and "Edit" not in argv and "Bash" not in argv
        assert d["task"] in sub.call_args.kwargs["input"]


def test_decider_failure_fails_closed():
    def boom(*_):
        raise FileNotFoundError

    with mock.patch("os_adapter.launch") as launch:
        assert "indisponivel" in o.handle("x", CFG, decide=boom, yes=True)
        launch.assert_not_called()


def test_choose_consensus_stops_early_and_survives_shuffling():
    calls = []

    def score(opts):  # always prefers "b", wherever it sits
        calls.append(1)
        return [0.9 if x == "b" else 0.05 for x in opts]

    assert laya.choose(score, ["a", "b", "c"], rng=random.Random(1)) == 1
    assert len(calls) == 3


def test_choose_runs_more_rounds_on_doubt_then_gives_up():
    rng = random.Random(0)

    def noisy(opts):  # position-biased: always picks whatever is first
        return [0.5] + [0.5 / (len(opts) - 1)] * (len(opts) - 1)

    calls = []
    assert laya.choose(lambda o_: calls.append(1) or noisy(o_), ["a", "b", "c"], rng=rng) is None
    assert len(calls) > 3


def test_choose_rejects_low_confidence():
    assert laya.choose(lambda opts: [0.4 if x == "a" else 0.3 for x in opts], ["a", "b", "c"]) is None


def test_proposal_is_queued_not_activated(tmp_path):
    block = '```preset\n{"name": "open_notes", "description": "Abre as notas", "linux": ["gedit"]}\n```'
    with mock.patch.object(o, "PENDING", tmp_path / "p.json"):
        text = o.save_proposals("ok\n" + block, CFG)
        assert "preset" not in text
        assert json.loads((tmp_path / "p.json").read_text())[0]["name"] == "open_notes"
    assert "open_notes" not in CFG["presets"]


def test_bad_proposals_rejected():
    ok = {"name": "open_notes", "description": "x", "linux": ["gedit"]}
    assert o.valid_proposal(ok, CFG)
    assert not o.valid_proposal({**ok, "linux": ["bash", "-c", "curl x | sh"]}, CFG)
    assert not o.valid_proposal({**ok, "name": "open_browser"}, CFG)  # no overwrite
    assert not o.valid_proposal({**ok, "linux": "gedit"}, CFG)


def test_approve_merges_only_accepted(tmp_path):
    cfg_path = tmp_path / "presets.json"
    cfg_path.write_text(json.dumps(CFG))
    pending = tmp_path / "p.json"
    pending.write_text(json.dumps([{"name": "open_notes", "description": "Abre notas", "linux": ["gedit"]}]))
    with mock.patch.object(o, "PENDING", pending):
        assert o.approve(cfg_path, ask=lambda _: "n") == 0
        pending.write_text(json.dumps([{"name": "open_notes", "description": "Abre notas", "linux": ["gedit"]}]))
        assert o.approve(cfg_path, ask=lambda _: "s") == 1
    assert json.loads(cfg_path.read_text())["presets"]["open_notes"]["confirm"] is False


def test_confirm_cmd_used_without_tty(monkeypatch):
    monkeypatch.setattr("sys.stdin", mock.Mock(isatty=lambda: False))
    monkeypatch.delenv("AGENT_CONFIRM_CMD", raising=False)
    assert o.confirmed("q", False) is False
    monkeypatch.setenv("AGENT_CONFIRM_CMD", "confirm.sh")
    with mock.patch("subprocess.run", return_value=mock.Mock(stdout="Sim\n")) as sub:
        assert o.confirmed("q", False) is True
        assert sub.call_args.args[0] == ["confirm.sh", "q"]
    with mock.patch("subprocess.run", return_value=mock.Mock(stdout="Nao\n")):
        assert o.confirmed("q", False) is False


def test_find_game_matches_by_words_and_rejects_unknown():
    import game_indexer as g
    games = [{"name": "Elden Ring"}, {"name": "Counter-Strike 2"}]
    assert g.find_game("joga elden ring pra mim", games)["name"] == "Elden Ring"
    assert g.find_game("abre counter strike", games)["name"] == "Counter-Strike 2"
    assert g.find_game("abre o navegador", games) is None


def test_game_preset_launches_the_game_not_the_preset():
    game = {"name": "Elden Ring", "linux": ["xdg-open", "steam://rungameid/1"], "windows": ["x"]}
    with mock.patch("os_adapter.launch") as launch:
        out = run({"action": "run_preset", "preset": "play_game", "game": game})
        assert "Elden Ring" in out
        assert launch.call_args.args[0]["linux"] == game["linux"]


def test_options_block_parsed_and_malformed_dropped():
    import solution_picker as sp
    text, ch = sp.parse_options('Duas saidas.\n```options {"question": "Qual?", "options": ["A", "B"]}```')
    assert text == "Duas saidas." and ch == {"question": "Qual?", "options": ["A", "B"]}
    assert sp.parse_options('```options {"options": ["so uma"]}```')[1] is None
    assert sp.parse_options("sem bloco") == ("sem bloco", None)


def test_picked_option_triggers_one_followup_on_stdin():
    first = mock.Mock(stdout='x\n```options {"question": "Q", "options": ["A", "B"]}```', stderr="")
    second = mock.Mock(stdout="feito com B", stderr="")
    with mock.patch("shutil.which", return_value="claude"), \
         mock.patch("subprocess.run", side_effect=[first, second]) as sub:
        assert o.escalate("tarefa", CFG, pick=lambda q, opts: 1) == "feito com B"
        assert sub.call_count == 2
        assert "--continue" in sub.call_args.args[0] and "B" in sub.call_args.kwargs["input"]
    with mock.patch("shutil.which", return_value="claude"), mock.patch("subprocess.run", side_effect=[first]) as sub:
        o.escalate("tarefa", CFG, pick=lambda q, opts: None)  # cancelled: no follow-up
        assert sub.call_count == 1
