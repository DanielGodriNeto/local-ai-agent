import dev_overlay as d


def test_describe_tools_show_file_name_only():
    ev = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": "C:/secret/dir/app.py"}}
    assert d.describe(ev) == ("accent", "Editando app.py")


def test_bash_shows_first_word_never_arguments_or_env_secrets():
    pre = lambda cmd: {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}}
    assert d.describe(pre("pytest -q tests/ --token abc"))[1] == "Rodando pytest"
    assert d.describe(pre("API_KEY=hunter2 curl x"))[1] == "Rodando um comando"
    assert "hunter2" not in d.describe(pre("API_KEY=hunter2 curl x"))[1]


def test_lifecycle_events_and_ignored_ones():
    assert d.describe({"hook_event_name": "Stop"}) == ("done", "Claude terminou")
    assert d.describe({"hook_event_name": "Notification", "message": "x" * 500})[1] == "Claude precisa de você"
    assert d.describe({"hook_event_name": "PostToolUse"}) is None
    assert d.describe({}) is None


def test_clean_strips_control_chars_and_limits_length():
    assert d.clean("a\x1b[31m\nb" + "z" * 100, 10) == "a [31m bzz"
