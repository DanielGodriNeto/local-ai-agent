import json
import subprocess
import sys
import threading
import time
from unittest import mock

import pytest

import brains

CFG = {"brain": {"use": "claude", "workdir": "", "timeout_s": 30, "memory_idle_s": 600,
                 "claude": {"model": "", "fast_model": "haiku", "tools": ["Read", "Grep", "Glob"]},
                 "codex": {"model": "", "fast_model": ""},
                 "ollama": {"url": "http://127.0.0.1:11434", "model": ""}}}

# shapes recorded from a real `claude -p --output-format stream-json --include-partial-messages` run
CLAUDE_LINES = [
    {"type": "system", "subtype": "init", "session_id": "s1"},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read",
                                                   "input": {"file_path": "C:\\secret\\dir\\nota.txt"}}]}, "session_id": "s1"},
    {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "aba"}}},
    {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "caxi"}}},
    {"type": "result", "subtype": "success", "is_error": False, "result": "abacaxi", "session_id": "s1"},
]


def fake_stream(lines):
    def stream(argv, stdin_text, cwd, timeout, cancel):
        stream.argv, stream.stdin = argv, stdin_text
        yield from (json.dumps(x) + "\n" for x in lines)
    return stream


def test_claude_events_show_only_file_names_and_keep_the_session():
    events = []
    for obj in CLAUDE_LINES:
        events += brains.claude_parse(obj)[0]
    assert [e.kind for e in events] == ["tool", "text", "text"]
    assert events[0].text == "Lendo nota.txt"  # the path never reaches the screen
    assert brains.claude_parse(CLAUDE_LINES[-1])[1:] == ("s1", "abacaxi")
    assert brains.claude_parse({"type": "result", "is_error": True, "result": "x"})[2].startswith("Erro")


def test_claude_argv_is_restricted_and_read_only():
    task = brains.claude_argv("claude", CFG["brain"]["claude"], "task", "s1", ())
    quick = brains.claude_argv("claude", CFG["brain"]["claude"], "quick", None, ())
    for argv in (task, quick):
        assert "--restricted" in argv and "--strict-mcp-config" in argv
        assert not {"Bash", "Edit", "Write", "--dangerously-skip-permissions", "--continue"} & set(argv)
    assert task[-3:] == ["Read", "Grep", "Glob"] and task[task.index("--resume") + 1] == "s1"
    assert quick[-1] == "--tools=" and quick[quick.index("--model") + 1] == "haiku"


def test_codex_argv_always_read_only_with_prompt_on_stdin():
    argv = brains.codex_argv("codex", {}, "task", "t1", ())
    assert argv[:4] == ["codex", "exec", "resume", "t1"]
    assert argv[argv.index("--sandbox") + 1] == "read-only" and argv[-1] == "-"
    assert "--full-auto" not in argv and not any("danger" in a for a in argv)
    assert brains.codex_parse({"type": "thread.started", "thread_id": "t9"})[1] == "t9"
    msg = {"type": "item.completed", "item": {"type": "agent_message", "text": "oi"}}
    assert brains.codex_parse(msg)[2] == "oi"


def test_ask_streams_and_resumes_the_same_conversation(tmp_path):
    cfg = {"brain": {**CFG["brain"], "workdir": str(tmp_path)}}
    conv, seen = brains.Conversation(), []
    with mock.patch("shutil.which", return_value="claude"), \
         mock.patch("brains.stream_lines", fake_stream(CLAUDE_LINES)) as stream:
        assert brains.ask(cfg, "--help le a nota", "task", conv, seen.append) == "abacaxi"
        assert "--help le a nota" not in stream.argv and stream.stdin.startswith("--help le a nota")
        assert "--resume" not in stream.argv
        brains.ask(cfg, "e agora?", "quick", conv, seen.append)
        assert stream.argv[stream.argv.index("--resume") + 1] == "s1"
    assert "".join(e.text for e in seen if e.kind == "text") == "abacaxiabacaxi"


def test_conversation_forgets_after_idle_or_brain_switch():
    conv = brains.Conversation(idle_s=60).current("claude")
    conv.remember("s1", "a", "b")
    assert conv.current("claude").session == "s1"
    conv.last -= 61
    assert conv.current("claude").session is None
    conv.remember("s2", "a", "b")
    assert conv.current("codex").session is None and conv.turns == []
    for i in range(20):
        conv.remember(None, str(i), str(i))
    assert len(conv.turns) == 12


def test_ollama_must_be_loopback():
    cfg = {"brain": {**CFG["brain"], "ollama": {"url": "http://evil.example:11434", "model": "m"}}}
    with mock.patch("http.client.HTTPConnection") as net:
        assert "localhost" in brains.run_ollama(cfg, "oi", brains.Conversation(), lambda e: None, threading.Event())
        assert brains.first_ollama_model("http://evil.example:11434") is None
        net.assert_not_called()


def test_ollama_cancel_works_while_the_model_is_still_loading():
    import socket
    srv = socket.create_server(("127.0.0.1", 0))  # accepts, never answers: a model still loading
    port = srv.getsockname()[1]
    cfg = {"brain": {**CFG["brain"], "ollama": {"url": f"http://127.0.0.1:{port}", "model": "m"}}}
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    t = time.time()
    with pytest.raises(brains.Cancelled):
        brains.run_ollama(cfg, "oi", brains.Conversation(), lambda e: None, cancel)
    assert time.time() - t < 3
    srv.close()


def test_failed_resume_drops_the_session(tmp_path):
    cfg = {"brain": {**CFG["brain"], "workdir": str(tmp_path)}}
    conv = brains.Conversation().current("claude")
    conv.session = "stale"
    err = [{"type": "result", "is_error": True, "result": "No conversation found", "session_id": "stale"}]
    with mock.patch("shutil.which", return_value="claude"), mock.patch("brains.stream_lines", fake_stream(err)):
        assert brains.ask(cfg, "oi", "quick", conv).startswith("Erro")
    assert conv.session is None


CHILD = """
import subprocess, sys, time
print(sys.stdin.read().upper(), flush=True)
g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
print(g.pid, flush=True)
time.sleep(60)
"""


def alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    import os
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_cancel_kills_the_whole_process_tree(tmp_path):
    cancel = threading.Event()
    lines = brains.stream_lines([sys.executable, "-c", CHILD], "prompt via stdin", tmp_path, 30, cancel)
    assert next(lines).strip() == "PROMPT VIA STDIN"
    grandchild = int(next(lines))
    t = time.time()
    cancel.set()
    with pytest.raises(brains.Cancelled):
        list(lines)
    assert time.time() - t < 5
    time.sleep(0.5)
    assert not alive(grandchild)


def test_timeout_also_kills(tmp_path):
    lines = brains.stream_lines([sys.executable, "-c", CHILD], "x", tmp_path, 1, threading.Event())
    with pytest.raises(TimeoutError):
        list(lines)
