"""AI back-ends ("brains") behind one call: Claude Code, Codex, or a local Ollama model. No API keys: each CLI
uses the login the user already has. Text and tool use stream as events; a conversation id keeps short-term memory.

    ask(cfg, prompt, mode, conv, on_event, cancel) -> final text
mode "quick": short answer, no tools, fast model.  mode "task": read-only file tools in the work folder.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

import dev_overlay

QUICK = "\n\n---\nResponda curto (no maximo 3 frases), na lingua do pedido, sem markdown."
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


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

    def current(self, brain: str) -> "Conversation":
        if brain != self.brain or time.time() - self.last > self.idle_s:
            self.brain, self.session, self.turns = brain, None, []
        return self

    def remember(self, session: Optional[str], prompt: str, answer: str) -> None:
        self.session, self.last = session or self.session, time.time()
        self.turns = (self.turns + [("user", prompt), ("assistant", answer)])[-12:]


class Cancelled(Exception):
    pass


# ---- process edge -------------------------------------------------------------------------------------------------

def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        os.killpg(proc.pid, signal.SIGTERM)


def stream_lines(argv: list[str], stdin_text: str, cwd: Path, timeout: float, cancel: threading.Event) -> Iterator[str]:
    """Run argv (never a shell), send the prompt on stdin, yield stdout lines. Cancel/timeout kill the whole tree."""
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", creationflags=flags,
                            start_new_session=sys.platform != "win32")
    stopped, deadline = threading.Event(), time.time() + timeout

    def watchdog():
        while not stopped.wait(0.1):
            if cancel.is_set() or time.time() > deadline:
                kill_tree(proc)
                return

    threading.Thread(target=watchdog, daemon=True).start()
    try:
        proc.stdin.write(stdin_text)
        proc.stdin.close()
        yield from proc.stdout
        proc.wait()
    finally:
        stopped.set()
        kill_tree(proc)
    if cancel.is_set():
        raise Cancelled
    if time.time() > deadline:
        raise TimeoutError(f"sem resposta em {timeout:.0f} s")


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
    return answer


def run_ollama(cfg: dict, prompt: str, conv: Conversation, on_event, cancel) -> str:
    """Local model over Ollama's HTTP API. Loopback only; the conversation is replayed from RAM."""
    o = cfg["brain"]["ollama"]
    url = urllib.parse.urlsplit(o["url"])
    if url.scheme != "http" or url.hostname not in LOOPBACK:
        return "Ollama precisa estar em localhost"
    model = o.get("model") or first_ollama_model(o["url"])
    if not model:
        return "Nenhum modelo no Ollama (rode: ollama pull <modelo>)"
    messages = [{"role": r, "content": t} for r, t in conv.turns] + [{"role": "user", "content": prompt}]
    req = urllib.request.Request(o["url"].rstrip("/") + "/api/chat", method="POST",
                                 data=json.dumps({"model": model, "messages": messages, "stream": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    chunks = []
    with urllib.request.urlopen(req, timeout=cfg["brain"]["timeout_s"]) as res:
        for line in res:
            if cancel.is_set():
                raise Cancelled
            piece = json.loads(line).get("message", {}).get("content", "")
            if piece:
                chunks.append(piece)
                on_event(Event("text", piece))
    answer = "".join(chunks)
    conv.remember(None, prompt, answer)
    return answer


def first_ollama_model(base: str) -> Optional[str]:
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/api/tags", timeout=2) as res:
            models = json.load(res).get("models", [])
    except (OSError, ValueError):
        return None
    return models[0]["name"] if models else None


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
    name = pick(cfg)
    if not name:
        return "Nenhuma IA encontrada: instale Claude Code, Codex ou Ollama."
    cancel = cancel or threading.Event()
    conv = conv.current(name)
    text = prompt + QUICK if mode == "quick" else prompt
    if name == "ollama":
        return run_ollama(cfg, text, conv, on_event, cancel)
    return run_cli(name, cfg, text, mode, conv, on_event, cancel, dirs)
