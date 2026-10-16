import json
from unittest import mock

import orchestrator as o

CFG = o.load_config()
CFG = {**CFG, "brain": {**CFG["brain"], "use": "claude", "quick": "main"}}  # not whatever this machine has


def run(decision, **kw):
    return o.handle("x", CFG, decide=lambda *_: decision, **kw)


def test_preset_launches_on_yes():
    with mock.patch("os_adapter.launch") as launch:
        assert run({"action": "run_preset", "preset": "open_browser"}) == "preset: open_browser"
        launch.assert_called_once()


def test_dry_run_never_executes():
    with mock.patch("os_adapter.launch") as launch, mock.patch("brains.ask") as ask:
        run({"action": "run_preset", "preset": "open_browser"}, dry_run=True)
        run({"action": "escalate", "task": "corrige o bug"}, dry_run=True)
        launch.assert_not_called()
        ask.assert_not_called()


def test_unknown_preset_and_garbage_action_become_clarify():
    with mock.patch("os_adapter.launch") as launch:
        assert "Nao entendi" in run({"action": "run_preset", "preset": "rm_rf"})
        assert "Nao entendi" in run({"action": "format_disk"})
        launch.assert_not_called()


def test_confirm_required_without_tty_cancels():
    with mock.patch("os_adapter.launch") as launch:
        assert "cancelado" in run({"action": "run_preset", "preset": "lock_screen"})
        launch.assert_not_called()


def fake_ask(*answers):
    """Stand-in for brains.ask: records (prompt, mode) and returns the given answers in order."""
    calls = []

    def ask(cfg, prompt, mode, conv, on_event, cancel=None, dirs=()):
        calls.append((prompt, mode, dirs))
        return answers[len(calls) - 1]
    ask.calls = calls
    return ask


def test_tasks_need_their_own_flag_and_quick_questions_go_straight():
    task = {"action": "escalate", "mode": "task", "task": "--dangerously-skip-permissions"}
    with mock.patch("brains.ask", fake_ask()) as ask:
        assert "cancelado" in run(task, yes=True)  # --yes does not cover sending files to the AI
        assert ask.calls == []
    with mock.patch("brains.ask", fake_ask("feito")) as ask:
        assert run(task, yes_escalate=True) == "feito"
        assert ask.calls[0][0].startswith(task["task"]) and ask.calls[0][1] == "task"
    with mock.patch("brains.ask", fake_ask("Paris")) as ask:
        assert run({"action": "escalate", "mode": "quick", "task": "capital da franca"}) == "Paris"
        assert ask.calls[0] == ("capital da franca", "quick", ())  # no preset/options wrapper on quick questions


def test_quick_questions_can_be_made_to_ask_first():
    cfg = {**CFG, "brain": {**CFG["brain"], "confirm_quick": True}}
    with mock.patch("brains.ask", fake_ask("Paris")) as ask:
        out = o.handle("x", cfg, decide=lambda *_: {"action": "escalate", "mode": "quick", "task": "capital"})
        assert "cancelado" in out and ask.calls == []


def test_cancel_and_ai_failure_are_reported_not_raised():
    def cancelled(*a, **k):
        raise o.brains.Cancelled

    def broken(*a, **k):
        raise FileNotFoundError("claude")

    d = {"action": "escalate", "mode": "quick", "task": "oi"}
    with mock.patch("brains.ask", cancelled):
        assert run(d) == "(cancelado)"
    with mock.patch("brains.ask", broken):
        assert "falhou" in run(d)


def test_decider_failure_fails_closed():
    def boom(*_):
        raise FileNotFoundError

    with mock.patch("os_adapter.launch") as launch:
        assert "indisponivel" in o.handle("x", CFG, decide=boom, yes=True)
        launch.assert_not_called()


def test_close_presets_ask_the_user_then_run_the_pick():
    d = {"action": "choose", "options": ["open_browser", "open_terminal"], "task": "x"}
    with mock.patch("os_adapter.launch") as launch:
        assert run(d, pick=lambda q, opts: 1) == "preset: open_terminal"
        assert launch.call_args.args[0] == {**CFG["presets"]["open_terminal"]}
    with mock.patch("os_adapter.launch") as launch:
        assert "cancelado" in run(d, pick=lambda q, opts: None)
        assert "?" in run(d)  # no way to ask: it asks to repeat, never guesses
        launch.assert_not_called()


def test_choose_with_unknown_preset_is_rejected():
    with mock.patch("os_adapter.launch") as launch:
        assert "Nao entendi" in run({"action": "choose", "options": ["open_browser", "rm_rf"]}, pick=lambda q, o_: 1)
        launch.assert_not_called()


def test_confirmation_echoes_what_was_heard():
    asked = []
    with mock.patch("os_adapter.launch"):
        o.handle("trava a tela", CFG, decide=lambda *_: {"action": "run_preset", "preset": "lock_screen"},
                 confirm=lambda q: asked.append(q) or True)
    assert "Entendi" in asked[0] and "trava a tela" in asked[0]


def test_intent_decider_uses_matcher_and_exact_game_names():
    m = mock.Mock(decide=lambda text: {"action": "run_preset", "preset": "open_browser"})
    assert o.intent_decide("abre o navegador", CFG, m) == {"action": "run_preset", "preset": "open_browser"}


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
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"voice": {"model": "small"}}))
    pending = tmp_path / "p.json"
    pending.write_text(json.dumps([{"name": "open_notes", "description": "Abre notas", "linux": ["gedit"]}]))
    with mock.patch.object(o, "PENDING", pending):
        assert o.approve(cfg_path, ask=lambda _: "n") == 0
        pending.write_text(json.dumps([{"name": "open_notes", "description": "Abre notas", "linux": ["gedit"]}]))
        assert o.approve(cfg_path, ask=lambda _: "s") == 1
    saved = json.loads(cfg_path.read_text())
    assert saved["presets"]["open_notes"]["confirm"] is False
    assert saved["voice"] == {"model": "small"} and "open_browser" not in saved["presets"]  # only the change is written


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


def test_picked_option_triggers_one_followup_in_the_same_conversation():
    options = 'x\n```options {"question": "Q", "options": ["A", "B"]}```'
    with mock.patch("brains.ask", fake_ask(options, "feito com B")) as ask:
        assert o.escalate("tarefa", CFG, pick=lambda q, opts: 1) == "feito com B"
        assert len(ask.calls) == 2 and "B" in ask.calls[1][0]
    with mock.patch("brains.ask", fake_ask(options)) as ask:
        o.escalate("tarefa", CFG, pick=lambda q, opts: None)  # cancelled: no follow-up
        assert len(ask.calls) == 1


def test_screen_request_confirms_then_sends_untrusted_text_on_stdin():
    with mock.patch("screen.capture", return_value=object()), mock.patch("screen.ocr_text", return_value="SEGREDO na tela"),          mock.patch("monitors.list_monitors", return_value=[{"n": 1, "x": 0, "y": 0, "w": 1, "h": 1}]),          mock.patch("brains.ask", fake_ask("resumo")) as ask:
        qs = []
        assert "cancelado" in o.handle("o que tem na tela", CFG, confirm=lambda q: qs.append(q) or False)
        assert qs[0].startswith('Entendi: "o que tem na tela"') and "SEGREDO" in qs[0]
        assert ask.calls == []  # nothing leaves the PC without a yes
        assert o.handle("o que tem na tela", CFG, yes_escalate=True, confirm=lambda q: True) == "resumo"  # --yes-escalate never skips it
        assert "SEGREDO" in ask.calls[0][0] and "nao confiavel" in ask.calls[0][0]


def test_screen_parse_and_missing_monitor():
    import screen
    assert screen.parse_request("o que tem no monitor 2") == {"monitor": 2, "image": False}
    assert screen.parse_request("olha o segundo monitor")["image"] is True
    assert screen.parse_request("abre o navegador") is None
    with mock.patch("monitors.list_monitors", return_value=[{"n": 1}]):
        assert "nao existe" in o.handle("le o monitor 3", CFG, confirm=lambda q: True)


def test_codex_quick_still_asks_because_its_shell_can_read_any_file():
    d = {"action": "escalate", "mode": "quick", "task": "capital"}
    asked = []
    with mock.patch("brains.pick", return_value="codex"), mock.patch("brains.ask", fake_ask("Paris")) as ask:
        assert "cancelado" in o.handle("x", CFG, decide=lambda *_: d, confirm=lambda q: asked.append(q) or False)
        assert ask.calls == [] and "qualquer arquivo" in asked[0]
    with mock.patch("brains.pick", return_value="claude"), mock.patch("brains.ask", fake_ask("Paris")):
        assert run(d) == "Paris"


def test_log_rotates_past_its_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(o, "HERE", tmp_path)
    monkeypatch.setattr(o, "LOG_MAX", 100)
    for i in range(10):
        o.log({"action": "x", "i": i})
    old, new = (tmp_path / "agent.log.1.jsonl").read_text(), (tmp_path / "agent.log.jsonl").read_text()
    assert len(new) <= 100 + 60 and '"i": 9' in new and old and not (tmp_path / "agent.log.2.jsonl").exists()


def test_chains_split_only_before_an_action_verb():
    assert o.chain_parts("abre o navegador e pesquisa receita de bolo") == ["abre o navegador", "pesquisa receita de bolo"]
    assert o.chain_parts("abre o terminal, depois me explica o que é git") == ["abre o terminal", "me explica o que é git"]
    assert o.chain_parts("pesquisa arroz e feijão") == ["pesquisa arroz e feijão"]
    assert o.chain_parts("explica como criar e rodar um script") == ["explica como criar e rodar um script"]
    assert o.chain_parts("como abre o terminal e pesquisa algo?") == ["como abre o terminal e pesquisa algo?"]


def chain(text, decide, yes=True):
    asked = []
    with mock.patch("os_adapter.launch") as launch, mock.patch("webbrowser.open") as web, \
            mock.patch("orchestrator.escalate", return_value="resposta") as esc:
        out = o.handle(text, CFG, decide=decide, confirm=lambda q: asked.append(q) or yes)
    return out, asked, launch, web, esc


BY_WORD = {"abre o navegador": {"action": "run_preset", "preset": "open_browser"},
           "abre o terminal": {"action": "run_preset", "preset": "open_terminal"},
           "trava a tela": {"action": "run_preset", "preset": "lock_screen"},
           "me explica git": {"action": "escalate", "task": "me explica git", "mode": "task"}}


def by_word(text, cfg):
    return BY_WORD.get(text, {"action": "clarify", "reply": "?"})


def test_open_the_browser_and_search_is_one_confirmed_search():
    out, asked, launch, web, _ = chain("abre o navegador e pesquisa receita de pão & bolo", by_word)
    assert len(asked) == 1 and "1. pesquisar “receita de pão & bolo” no navegador" in asked[0]
    web.assert_called_once_with("https://duckduckgo.com/?q=receita+de+p%C3%A3o+%26+bolo")
    launch.assert_not_called()


def test_a_chain_asks_once_up_front_even_for_presets_that_never_ask():
    out, asked, launch, web, _ = chain("abre o terminal e trava a tela", by_word, yes=False)
    assert out == "(cancelado)" and "1. executar 'open_terminal'\n2. executar 'lock_screen'" in asked[0]
    launch.assert_not_called()
    out, asked, launch, _, _ = chain("abre o terminal e trava a tela", by_word)
    assert len(asked) == 1 and launch.call_count == 2


def test_one_unclear_part_runs_nothing():
    out, asked, launch, web, esc = chain("abre o terminal e abre o xyz", by_word)
    assert "abre o xyz" in out and asked == [] and not launch.called and not web.called and not esc.called


def test_the_ai_step_in_a_chain_still_asks_its_own_consent():
    out, asked, launch, _, esc = chain("abre o terminal e me explica git", by_word)
    assert len(asked) == 2 and "Pedir para" in asked[1] and launch.call_count == 1 and esc.called
    assert out.endswith("resposta")


def test_search_only_when_asked_for_the_web_or_after_the_browser():
    out, asked, _, web, _ = chain("pesquisa no google previsão do tempo", by_word)
    web.assert_called_once_with("https://duckduckgo.com/?q=previs%C3%A3o+do+tempo")
    out, asked, _, web, _ = chain("abre o terminal e procura nos meus arquivos", by_word)
    assert not web.called and "procura nos meus arquivos" in out  # that one is a task for the AI, not a web search


def test_chains_refuse_clicks_and_more_than_three_steps():
    out, asked, launch, _, _ = chain("abre o terminal e clica em salvar", by_word)
    assert "um pedido de cada vez" in out and not asked and not launch.called
    out, asked, launch, _, _ = chain("abre o terminal e abre o navegador e abre o terminal e trava a tela", by_word)
    assert "3 passos" in out and not asked and not launch.called


def test_saying_no_to_the_ai_step_stops_the_rest_of_the_chain():
    answers = iter([True, False])
    with mock.patch("os_adapter.launch") as launch, mock.patch("orchestrator.escalate") as esc:
        out = o.handle("me explica git e abre o terminal", CFG, decide=by_word, confirm=lambda q: next(answers))
    assert out.endswith("(cancelado)") and not launch.called and not esc.called


def test_searching_this_pc_is_never_a_web_search():
    for said in ("procura nos meus arquivos", "pesquisa na pasta downloads", "busca no computador o relatório"):
        assert o.web_query(said, after_browser=True) is None
    assert o.web_query("pesquisa receita de bolo", after_browser=True) == "receita de bolo"


def test_latency_report_skips_requests_that_waited_for_the_user():
    lines = ['{"action": "latency", "kind": "ai", "asked": false, "text_s": 1.0, "done_s": 5.0}',
             '{"action": "latency", "kind": "ai", "asked": false, "text_s": 3.0, "done_s": 9.0}',
             '{"action": "latency", "kind": "ai", "asked": true, "text_s": 50.0}', "not json", '{"action": "x"}']
    report = o.latency_report(lines)
    assert "ai      text       2     2.00    3.00" in report and "50" not in report
    assert "Sem medições" in o.latency_report([])


def test_a_local_quick_answer_needs_no_consent_even_when_the_main_ai_is_codex():
    cfg = {**CFG, "brain": {**CFG["brain"], "use": "codex", "quick": "local"}}
    asked = []
    with mock.patch("brains.quick_model", return_value="qwen3:1.7b"), mock.patch("orchestrator.escalate", return_value="391"):
        assert o.handle("x", cfg, decide=lambda *_: {"action": "escalate", "task": "17x23", "mode": "quick"},
                        confirm=lambda q: asked.append(q) or True) == "391"
        o.handle("x", cfg, decide=lambda *_: {"action": "escalate", "task": "le o log", "mode": "task"},
                 confirm=lambda q: asked.append(q) or True)
    assert len(asked) == 1 and "Codex" in asked[0]  # only the task asks: Codex can read any file
