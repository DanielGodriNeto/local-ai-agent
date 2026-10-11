from unittest import mock

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


def test_unknown_preset_becomes_clarify():
    with mock.patch("os_adapter.launch") as launch:
        assert "Nao tenho" in run({"action": "run_preset", "preset": "rm_rf"})
        launch.assert_not_called()


def test_confirm_required_without_tty_cancels():
    with mock.patch("os_adapter.launch") as launch:
        assert "cancelado" in run({"action": "run_preset", "preset": "lock_screen"})
        launch.assert_not_called()


def test_escalate_calls_claude_headless_with_limited_tools():
    with mock.patch("shutil.which", return_value="claude"), \
         mock.patch("subprocess.run", return_value=mock.Mock(stdout="feito", stderr="")) as sub:
        assert run({"action": "escalate", "task": "explica o repo"}, yes=True) == "feito"
        argv = sub.call_args.args[0]
        assert argv[:3] == ["claude", "-p", "explica o repo"]
        assert "Edit" not in argv and "Bash" not in argv


def test_decider_failure_fails_closed():
    def boom(*_):
        raise ConnectionError

    with mock.patch("os_adapter.launch") as launch:
        assert "indisponivel" in o.handle("x", CFG, decide=boom, yes=True)
        launch.assert_not_called()


def test_garbage_action_becomes_clarify():
    assert run({"action": "format_disk"}) == "Nao entendi, pode repetir?"
