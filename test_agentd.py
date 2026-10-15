import socket
import threading
import time
from unittest import mock

import pytest

import agentd


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    """A real accept loop on a free loopback port; yields (port, received commands)."""
    monkeypatch.setattr(agentd, "TOKEN", tmp_path / "t")
    sock = socket.create_server(("127.0.0.1", 0))
    got = []
    threading.Thread(target=agentd.serve, args=(sock, agentd.token(tmp_path / "t"), got.append), daemon=True).start()
    yield sock.getsockname()[1], got
    sock.close()


def raw(port, answer):
    """answer(nonce) -> the line to send back."""
    with socket.create_connection(("127.0.0.1", port), timeout=2) as c:
        f = c.makefile("rwb")
        nonce = f.readline().decode().strip()
        f.write(answer(nonce).encode())
        f.flush()
        return f.readline()


def test_only_the_token_holder_with_a_known_command_is_obeyed(daemon):
    port, got = daemon
    secret = agentd.token()
    assert agentd.send("toggle", start_if_down=False, port=port)
    assert raw(port, lambda n: "wrong toggle\n") == b"no\n"
    assert raw(port, lambda n: f"{secret} toggle\n") == b"no\n"  # the raw token is never the answer
    assert raw(port, lambda n: f"{agentd.sign(secret, n, 'rm')} rm\n") == b"no\n"
    assert raw(port, lambda n: f"{agentd.sign(secret, 'old', 'toggle')} toggle\n") == b"no\n"  # replay
    assert raw(port, lambda n: "GET / HTTP/1.1\r\n") == b"no\n"  # a web page poking localhost gets nothing
    time.sleep(0.1)
    assert got == ["toggle"]


def test_a_port_squatter_never_sees_the_token(tmp_path, monkeypatch):
    monkeypatch.setattr(agentd, "TOKEN", tmp_path / "t")
    squat = socket.create_server(("127.0.0.1", 0))
    seen = []

    def fake_daemon():
        c, _ = squat.accept()
        with c:
            c.sendall(b"n0nce\n")
            seen.append(c.makefile("rb").readline())
            c.sendall(b"ok\n")
    threading.Thread(target=fake_daemon, daemon=True).start()
    agentd.send("toggle", start_if_down=False, port=squat.getsockname()[1])
    assert seen and agentd.token().encode() not in seen[0]
    squat.close()


def test_token_is_created_once_and_kept(tmp_path):
    t = agentd.token(tmp_path / "x" / "t")
    assert len(t) == 32 and agentd.token(tmp_path / "x" / "t") == t


def test_client_does_not_start_a_daemon_for_quit():
    with mock.patch.object(agentd, "spawn_daemon") as spawn:
        assert agentd.send("quit", start_if_down=False, port=1) is False
        spawn.assert_not_called()


def test_toggle_starts_then_cancels_the_running_session():
    def start(cancel):
        t = threading.Thread(target=cancel.wait, args=(5,), daemon=True)
        t.start()
        return t

    agent = agentd.Agent(start)
    assert agent.toggle() == "start" and agent.busy()
    first = agent.cancel
    assert agent.toggle() == "cancel" and first.is_set()
    agent.thread.join(1)
    assert not agent.busy() and agent.toggle() == "start" and not agent.cancel.is_set()


def test_idle_unload_frees_models_but_keeps_the_conversation():
    import orchestrator as o
    loads = []
    models = agentd.Models(lambda: loads.append(1) or ("stt", "decide"), unload_after_s=60)
    assert models() == ("stt", "decide") and models() and len(loads) == 1
    o.CONV.remember("s1", "a", "b")
    assert not models.unload_if_idle(busy=False)  # not idle yet
    models.last -= 61
    assert not models.unload_if_idle(busy=True)  # never under a running session
    assert not models.unload_if_idle(busy=False)  # idle restarts when the session ends
    models.last -= 61
    assert models.unload_if_idle(busy=False) and models.pair is None
    assert o.CONV.session == "s1"
    models()
    assert len(loads) == 2


def test_closing_the_overlay_answers_no_at_once():
    overlay = pytest.importorskip("overlay")
    try:
        ui = overlay.Overlay()
    except Exception as e:  # no display
        pytest.skip(str(e))
    out = []
    t = threading.Thread(target=lambda: out.append(ui.ask("Executar?", timeout=10)))
    t.start()
    time.sleep(0.1)
    started = time.time()
    ui._close()
    t.join(2)
    assert out == [False] and time.time() - started < 1
    started = time.time()
    assert ui.ask("Depois de fechado?", timeout=10) is False and time.time() - started < 1  # cancelled earlier


def test_a_detached_task_keeps_running_while_a_new_session_starts():
    started = []

    def start(cancel, **kw):
        started.append(kw)
        t = threading.Thread(target=cancel.wait, args=(5,), daemon=True)
        t.start()
        return t

    agent = agentd.Agent(start)
    agent.toggle(typed=True)
    first = agent.cancel
    agent.detach(first)
    assert not agent.busy()  # the hotkey and the wake word are free again
    assert agent.toggle() == "start" and not first.is_set() and started == [{"typed": True}, {}]
    agent.detach(first)  # a stale detach from the old session changes nothing
    assert agent.busy()
    agent.cancel_background()
    assert first.is_set() and not agent.cancel.is_set()
