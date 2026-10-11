"""Claude Code progress in a screen corner, fed by Claude Code hooks (see README / claude-hooks.example.json).

    python dev_overlay.py hook   # hook command: reads the event JSON on stdin, forwards it, starts the overlay if needed
    python dev_overlay.py show   # the overlay process (the hook starts it; it exits on its own when idle)

Events travel over a loopback TCP socket, nothing is written to disk. The overlay only ever *displays* a short,
sanitised summary (tool + file name / first command word): never full commands, paths or file contents.
"""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time

TOOL_VERBS = {"Edit": "Editando", "MultiEdit": "Editando", "Write": "Escrevendo", "NotebookEdit": "Editando",
              "Read": "Lendo", "Grep": "Procurando", "Glob": "Procurando", "WebFetch": "Lendo a web",
              "WebSearch": "Pesquisando", "Task": "Subagente", "Agent": "Subagente"}


def clean(s: object, limit: int = 60) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(s)).strip()[:limit]


def describe(ev: dict) -> tuple[str, str] | None:
    """Hook event -> (state, text), or None when it is not worth showing. state: idle | accent | done."""
    name = ev.get("hook_event_name")
    if name == "UserPromptSubmit":
        return "idle", "Pensando…"
    if name == "Notification":
        return "accent", "Claude precisa de você"
    if name == "Stop":
        return "done", "Claude terminou"
    if name != "PreToolUse":
        return None
    tool, inp = ev.get("tool_name", ""), ev.get("tool_input") or {}
    if tool == "Bash":
        first = re.split(r"\s+", str(inp.get("command", "")).strip())[0] if inp.get("command") else ""
        # an env assignment (FOO=secret cmd) must never reach the screen
        return "accent", "Rodando " + (os.path.basename(first) if first and "=" not in first else "um comando")
    target = inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or ""
    verb = TOOL_VERBS.get(tool, clean(tool, 24) or "Trabalhando")
    return "accent", clean(f"{verb} {os.path.basename(str(target))}" if target else verb)


def port() -> int:
    import config
    return config.load()["dev_overlay"]["port"]


def send(payload: dict, p: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", p), timeout=0.3) as s:
            s.sendall((json.dumps(payload) + "\n").encode())
        return True
    except OSError:
        return False


def hook() -> None:
    """Never raise, never print, never block Claude Code: an overlay problem must be invisible to it."""
    try:
        shown = describe(json.loads(sys.stdin.read() or "{}"))
        if not shown:
            return
        payload, p = {"state": shown[0], "text": shown[1]}, port()
        if send(payload, p):
            return
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW) if sys.platform == "win32" else 0
        subprocess.Popen([sys.executable, __file__, "show"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags, start_new_session=sys.platform != "win32")
        for _ in range(20):  # wait up to ~3 s for the overlay to start listening
            time.sleep(0.15)
            if send(payload, p):
                return
    except Exception:
        pass


def show() -> None:
    import config
    import overlay

    cfg = config.load()
    dev = cfg["dev_overlay"]
    try:
        srv = socket.create_server(("127.0.0.1", dev["port"]))
    except OSError:
        return  # another overlay already owns the port
    ui = overlay.Overlay(cfg["ui"], "corner")
    last = [time.time(), dev["idle_exit_s"]]  # [last event time, seconds of silence before exiting]
    states = {"idle": overlay.IDLE, "accent": overlay.LISTEN, "done": overlay.GREEN}

    def serve() -> None:
        while True:
            conn, _ = srv.accept()
            with conn:
                conn.settimeout(1)
                try:
                    ev = json.loads(conn.makefile().readline(512))
                except (OSError, ValueError):
                    continue
            if not isinstance(ev, dict) or not isinstance(ev.get("state", "idle"), str):
                continue
            state, text = ev.get("state", "idle"), clean(ev.get("text", ""))
            ui.status("", states.get(state, overlay.IDLE))
            ui.text(text)
            last[:] = [time.time(), dev["done_hold_s"] if state == "done" else
                       dev["ask_hold_s"] if text.startswith("Claude precisa") else dev["idle_exit_s"]]

    def watchdog() -> None:
        while time.time() - last[0] < last[1]:
            time.sleep(0.5)
        ui.close()

    threading.Thread(target=serve, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    ui.run()


if __name__ == "__main__":
    {"hook": hook, "show": show}.get(sys.argv[1] if len(sys.argv) > 1 else "", show)()
