"""AI back-ends ("brains") behind one call: Claude Code, Codex, or a local Ollama model. No API keys: each CLI
uses the login the user already has. Text and tool use stream as events; a conversation id keeps short-term memory.

    ask(cfg, prompt, mode, conv, on_event, cancel) -> final text
mode "quick": short answer, no tools, fast model.  mode "task": read-only file tools in the work folder.
Quick questions go to a small local model when brain.quick is "local" and it is installed (nothing leaves the PC);
brain.quick "main" sends them to the main AI's fast model (Claude Haiku) instead.
"""
import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

import dev_overlay

QUICK = "\n\n---\nResponda curto (no maximo 3 frases), na lingua do pedido, sem markdown."
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
TOOLLESS_QUICK = {"claude", "ollama"}  # brains whose quick mode truly has no tools (checked live for claude)


@dataclass
class Event:
    kind: str  # "text" (a chunk of the answer) | "tool" (short, sanitised label of what it is doing)
    text: str


@dataclass
class Conversation:
    """RAM only. A brain session id (Claude/Codex) or the turns themselves (Ollama), forgotten after idle_s."""
    idle_s: float = 600
    brain: str = ""
    session: Optional[str] = None
    turns: list = field(default_factory=list)
    last: float = 0.0
    shelf: dict = field(default_factory=dict)  # the other brains' memory: a quick local answer keeps Claude's session

    def current(self, brain: str) -> "Conversation":
        if time.time() - self.last > self.idle_s:
            self.session, self.turns, self.shelf = None, [], {}
        if brain != self.brain:
            self.shelf[self.brain] = (self.session, self.turns)
            self.brain = brain
            self.session, self.turns = self.shelf.pop(brain, (None, []))
        return self

    def remember(self, session: Optional[str], prompt: str, answer: str) -> None:
        self.session, self.last = session or self.session, time.time()
        self.turns = (self.turns + [("user", prompt), ("assistant", answer)])[-12:]


class Cancelled(Exception):
    pass


# ---- process edge -------------------------------------------------------------------------------------------------

def job_for(proc: subprocess.Popen) -> Optional[int]:
    """Windows: a kill-on-close Job Object holding the CLI, so its children die with it even after it exits.
    ponytail: assigned just after start, so a child spawned in the first instant escapes; CREATE_SUSPENDED closes it."""
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("user_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", wintypes.DWORD),
                    ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t), ("procs", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("sched", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", ctypes.c_uint64 * 6), ("mem", ctypes.c_size_t * 4)]

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = k.CreateJobObjectW(None, None)
    info = Extended()
    info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    limited = job and k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    if limited and k.AssignProcessToJobObject(job, int(proc._handle)):
        return job
    if job:
        k.CloseHandle(wintypes.HANDLE(job))
    return None


def kill_tree(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        job = proc.__dict__.pop("job", None)  # atomic: the watch thread and the caller may both get here
        if job:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(job))  # kill-on-close ends the CLI and every child
        elif proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        return
    try:  # the group outlives its leader, so kill it even after the CLI exits
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(2)
            except subprocess.TimeoutExpired:
                pass
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def watch(cancel: threading.Event, deadline: float, stop: Callable[[], None]) -> threading.Event:
    """Call stop() once on cancel or past the deadline. Set the returned event to end the watch."""
    done = threading.Event()

    def loop():
        while not done.wait(0.1):
            if cancel.is_set() or time.time() > deadline:
                stop()
                return

    threading.Thread(target=loop, daemon=True).start()
    return done


def interrupted(cancel: threading.Event, deadline: float, timeout: float) -> bool:
    """Raise Cancelled/TimeoutError if the watch fired; False otherwise."""
    if cancel.is_set():
        raise Cancelled
    if time.time() > deadline:
        raise TimeoutError(f"sem resposta em {timeout:.0f} s")
    return False


def stream_lines(argv: list[str], stdin_text: str, cwd: Path, timeout: float, cancel: threading.Event) -> Iterator[str]:
    """Run argv (never a shell), send the prompt on stdin, yield stdout lines. Cancel/timeout kill the whole tree."""
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", creationflags=flags,
                            start_new_session=sys.platform != "win32")
    if sys.platform == "win32":
        proc.job = job_for(proc)
    deadline = time.time() + timeout
    done = watch(cancel, deadline, lambda: kill_tree(proc))
    try:
        try:
            proc.stdin.write(stdin_text)
            proc.stdin.close()
        except OSError:  # the watch killed it mid-write: report cancel/timeout, not a broken pipe
            if not interrupted(cancel, deadline, timeout):
                raise
        yield from proc.stdout
        proc.wait()
    finally:
        done.set()
        kill_tree(proc)
    interrupted(cancel, deadline, timeout)


# ---- adapters: argv builders + pure line parsers ------------------------------------------------------------------

def tool_label(name: str, inp: dict) -> str:
    shown = dev_overlay.describe({"hook_event_name": "PreToolUse", "tool_name": name, "tool_input": inp or {}})
    return shown[1] if shown else name


def claude_argv(exe: str, c: dict, mode: str, session: Optional[str], dirs: tuple) -> list[str]:
    """--restricted ignores the user's own settings and hooks and confines file tools to the work folder."""
    argv = [exe, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--restricted", "--strict-mcp-config", "--setting-sources=", "--permission-mode", "default"]
    model = c.get("fast_model") if mode == "quick" else c.get("model")
    argv += ["--model", model] if model else []
    argv += ["--resume", session] if session else []
    for d in dirs:
        argv += ["--add-dir", str(d)]
    tools = c.get("tools", ["Read", "Grep", "Glob"]) if mode == "task" else []
    return argv + (["--tools", *tools] if tools else ["--tools="])


def claude_parse(obj: dict) -> tuple[list[Event], Optional[str], Optional[str]]:
    """-> (events, session id, final text). Final text only on the closing `result` line."""
    t = obj.get("type")
    if t == "stream_event":
        delta = obj.get("event", {}).get("delta") or {}
        return ([Event("text", delta["text"])] if delta.get("type") == "text_delta" else []), None, None
    if t == "assistant":
        uses = [c for c in obj.get("message", {}).get("content", []) if c.get("type") == "tool_use"]
        return [Event("tool", tool_label(c.get("name", ""), c.get("input"))) for c in uses], None, None
    if t == "result":
        text = obj.get("result") or ""
        return [], obj.get("session_id"), ("Erro: " + text if obj.get("is_error") else text)
    return [], None, None


def codex_argv(exe: str, c: dict, mode: str, session: Optional[str], dirs: tuple) -> list[str]:
    """Read-only sandbox always. ponytail: built from the Codex docs, not yet run against a real install."""
    argv = [exe, "exec", *(["resume", session] if session else []), "--json", "--skip-git-repo-check",
            "--sandbox", "read-only"]
    model = c.get("fast_model") if mode == "quick" else c.get("model")
    argv += ["--model", model] if model else []
    for d in dirs:
        argv += ["--add-dir", str(d)]
    return argv + ["-"]  # "-" = prompt on stdin


def codex_parse(obj: dict) -> tuple[list[Event], Optional[str], Optional[str]]:
    t, item = obj.get("type"), obj.get("item") or {}
    if t == "thread.started":
        return [], obj.get("thread_id"), None
    if t == "item.started" and item.get("type") == "command_execution":
        return [Event("tool", tool_label("Bash", {"command": item.get("command", "")}))], None, None
    if t == "item.completed" and item.get("type") == "agent_message":
        return [Event("text", item.get("text", ""))], None, item.get("text", "")
    if t in ("turn.failed", "error"):
        return [], None, "Erro: " + str((obj.get("error") or {}).get("message", obj.get("message", "")))[:300]
    return [], None, None


CLI = {"claude": (claude_argv, claude_parse), "codex": (codex_argv, codex_parse)}


def run_cli(name: str, cfg: dict, prompt: str, mode: str, conv: Conversation, on_event, cancel, dirs=()) -> str:
    b = cfg["brain"]
    exe = shutil.which(name)
    if not exe:
        return f"{name} nao encontrado no PATH"
    build, parse = CLI[name]
    argv = build(exe, b.get(name, {}), mode, conv.session, dirs)
    session, final, chunks = conv.session, None, []
    for line in stream_lines(argv, prompt, workdir(cfg), b["timeout_s"], cancel):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        events, sid, done = parse(obj) if isinstance(obj, dict) else ([], None, None)
        session = sid or session
        for e in events:
            chunks += [e.text] if e.kind == "text" else []
            on_event(e)
        final = done if done is not None else final
    answer = final if final is not None else "".join(chunks)
    conv.remember(session, prompt, answer)
    if answer.startswith("Erro: "):
        conv.session = None  # a stale or broken session must not fail every retry
    return answer


def loopback(url: str) -> Optional[urllib.parse.SplitResult]:
    u = urllib.parse.urlsplit(url)
    return u if u.scheme == "http" and u.hostname in LOOPBACK else None


def abort(sock: Optional[socket.socket]) -> None:
    """Wake a read blocked on a model that is still loading. shutdown() does it on POSIX, only closing the handle does
    on Windows; detach() first, because the open response file would otherwise keep the handle alive."""
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        socket.socket(fileno=sock.detach()).close()
    except (OSError, ValueError):  # already closed by the normal path
        pass


def run_ollama(cfg: dict, prompt: str, conv: Conversation, on_event, cancel, quick_model: Optional[str] = None) -> str:
    """Local model over Ollama's HTTP API. Loopback only; the conversation is replayed from RAM.
    quick_model: answer at once, without the model's thinking pass."""
    o, timeout = cfg["brain"]["ollama"], cfg["brain"]["timeout_s"]
    url = loopback(o["url"])
    if not url:
        return "Ollama precisa estar em localhost"
    model = quick_model or o.get("model") or first_ollama_model(o["url"])
    if not model:
        return "Nenhum modelo no Ollama (rode: ollama pull <modelo>)"
    messages = [{"role": r, "content": t} for r, t in conv.turns] + [{"role": "user", "content": prompt}]
    body = json.dumps({"model": model, "messages": messages, "stream": True, **({"think": False} if quick_model else {})})
    conn = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=timeout)
    conn.connect()
    deadline = time.time() + timeout
    done = watch(cancel, deadline, lambda: abort(conn.sock))
    chunks = []
    try:
        conn.request("POST", url.path.rstrip("/") + "/api/chat", body, {"Content-Type": "application/json"})
        for line in conn.getresponse():
            piece = json.loads(line).get("message", {}).get("content", "")
            if piece:
                chunks.append(piece)
                on_event(Event("text", piece))
    except (OSError, http.client.HTTPException, ValueError):
        if not interrupted(cancel, deadline, timeout):
            raise
    finally:
        done.set()
        conn.close()
    interrupted(cancel, deadline, timeout)
    answer = "".join(chunks)
    conv.remember(None, prompt, answer)
    return answer


def ollama_models(base: str) -> list[str]:
    url = loopback(base)
    if not url:
        return []
    conn = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=2)
    try:
        conn.request("GET", url.path.rstrip("/") + "/api/tags")
        return [m["name"] for m in json.load(conn.getresponse()).get("models", [])]
    except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError):
        return []
    finally:
        conn.close()


def first_ollama_model(base: str) -> Optional[str]:
    models = ollama_models(base)
    return models[0] if models else None


def quick_model(cfg: dict) -> Optional[str]:
    """The local model for quick questions, if brain.quick is "local" and Ollama has it."""
    b = cfg.get("brain", {})
    o = b.get("ollama", {})
    if b.get("quick", "local") != "local" or not (model := o.get("quick_model")) or not o.get("url") \
            or model not in ollama_models(o["url"]):
        return None
    return model


def quick_brain(cfg: dict) -> Optional[str]:
    return "ollama" if quick_model(cfg) else pick(cfg)


def warm_quick(cfg: dict) -> None:
    """Load the quick model while the user is still talking: a cold load is ~6 s, a warm answer well under 1 s."""
    model = quick_model(cfg)
    if model:
        ollama_warm(cfg, model)


def ollama_warm(cfg: dict, model: str) -> None:
    """Load the model into memory now (an empty prompt only loads it), so the real question skips the cold start."""
    url = loopback(cfg["brain"]["ollama"]["url"])
    if not url:
        return
    conn = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=cfg["brain"]["timeout_s"])
    try:
        conn.request("POST", url.path.rstrip("/") + "/api/generate", json.dumps({"model": model, "keep_alive": "5m"}),
                     {"Content-Type": "application/json"})
        conn.getresponse().read()
    except (OSError, http.client.HTTPException):
        pass
    finally:
        conn.close()


def ollama_image(cfg: dict, model: str, prompt: str, png: bytes, cancel: threading.Event) -> str:
    """One question about one image to a local vision model: the image never leaves the PC. "" on any failure."""
    import base64
    timeout, url = cfg["brain"]["timeout_s"], loopback(cfg["brain"]["ollama"]["url"])
    if not url:
        return ""
    body = json.dumps({"model": model, "stream": False, "think": False, "options": {"temperature": 0},
                       "messages": [{"role": "user", "content": prompt,
                                     "images": [base64.b64encode(png).decode("ascii")]}]})
    conn = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=timeout)
    done = watch(cancel, time.time() + timeout, lambda: abort(conn.sock))
    try:
        conn.request("POST", url.path.rstrip("/") + "/api/chat", body, {"Content-Type": "application/json"})
        reply = json.load(conn.getresponse())
        return str(reply.get("message", {}).get("content", "")) if isinstance(reply, dict) else ""
    except (OSError, http.client.HTTPException, ValueError):
        return ""
    finally:
        done.set()
        conn.close()


# ---- entry point ---------------------------------------------------------------------------------------------------

def workdir(cfg: dict) -> Path:
    """The folder the AI may read. Default: an empty folder of its own, never this repo."""
    d = Path(cfg["brain"].get("workdir") or Path.home() / ".local-ai-agent" / "work").expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def pick(cfg: dict) -> Optional[str]:
    """Configured brain, or the first one installed: claude, codex, then a running Ollama."""
    use = cfg["brain"]["use"]
    if use != "auto":
        return use
    for name in CLI:
        if shutil.which(name):
            return name
    return "ollama" if first_ollama_model(cfg["brain"]["ollama"]["url"]) else None


def ask(cfg: dict, prompt: str, mode: str, conv: Conversation, on_event: Callable[[Event], None] = lambda e: None,
        cancel: Optional[threading.Event] = None, dirs: tuple = ()) -> str:
    local = quick_model(cfg) if mode == "quick" else None
    name = "ollama" if local else pick(cfg)
    if not name:
        return "Nenhuma IA encontrada: instale Claude Code, Codex ou Ollama."
    cancel = cancel or threading.Event()
    conv = conv.current(name)
    text = prompt + QUICK if mode == "quick" else prompt
    if name == "ollama":
        return run_ollama(cfg, text, conv, on_event, cancel, local)
    return run_cli(name, cfg, text, mode, conv, on_event, cancel, dirs)
