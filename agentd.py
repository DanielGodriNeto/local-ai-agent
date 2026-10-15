"""Resident agent: loads once, then waits for the hotkey.

    python agentd.py            run it (start with the system; the hotkey also starts it when it is not running)
    python agentd.py --toggle   what the hotkey sends: start a voice session, or cancel the one running
    python agentd.py --quit

The hotkey is a tiny client on 127.0.0.1. The daemon sends a nonce, the client answers `<hmac> <command>` with
HMAC-SHA256(token, nonce + command), so the per-user token never crosses the socket, even to a process squatting the port.
Mode "leve" frees the speech and intent models after `unload_after_s` idle; "rapido" keeps them. The AI conversation
memory survives either way.
"""
import argparse
import hashlib
import hmac
import os
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

PORT = 48765
HOME = Path.home() / ".local-ai-agent"
TOKEN = HOME / "agentd.token"
COMMANDS = {"toggle", "quit"}


def token(path: Optional[Path] = None) -> str:
    """Readable only by this user (home dir on Windows, 0600 on POSIX), so only they can drive the daemon."""
    path = path or TOKEN
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        for _ in range(40):  # another process may have created it and not written it yet
            t = path.read_text().strip()
            if t:
                return t
            time.sleep(0.05)
        raise RuntimeError(f"token vazio em {path}")
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_hex(16))
    return path.read_text().strip()


def sign(secret: str, nonce: str, cmd: str) -> str:
    return hmac.new(secret.encode(), f"{nonce} {cmd}".encode(), hashlib.sha256).hexdigest()


# ---- daemon side ---------------------------------------------------------------------------------------------------

def serve(sock: socket.socket, secret: str, on_command: Callable[[str], None]) -> None:
    """Accept loop. Fixed command set; a wrong signature or command gets "no" and changes nothing."""
    while True:
        try:
            conn, _ = sock.accept()
        except OSError:
            return  # socket closed: shutting down
        with conn:
            conn.settimeout(2)
            nonce = secrets.token_hex(16)  # fresh per connection: a recorded answer cannot be replayed
            try:
                conn.sendall(f"{nonce}\n".encode())
                line = conn.makefile(encoding="utf-8", errors="replace").readline(100)
            except OSError:
                continue
            mac, _, cmd = line.strip().partition(" ")
            ok = cmd in COMMANDS and hmac.compare_digest(mac.encode(), sign(secret, nonce, cmd).encode())
            try:
                conn.sendall(b"ok\n" if ok else b"no\n")
            except OSError:
                pass
            if ok:
                on_command(cmd)


class Agent:
    """At most one session. toggle() starts one, or cancels the one running."""

    def __init__(self, start: Callable[[threading.Event], threading.Thread]):
        self.start, self.thread, self.cancel = start, None, threading.Event()

    def busy(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def toggle(self) -> str:
        if self.busy():
            self.cancel.set()
            return "cancel"
        self.cancel = threading.Event()
        self.thread = self.start(self.cancel)
        return "start"


class Models:
    """Speech and intent models, loaded on first use and dropped after idle (mode "leve")."""

    def __init__(self, load: Callable[[], tuple], unload_after_s: float):
        self.load, self.unload_after_s, self.pair, self.last = load, unload_after_s, None, 0.0

    def __call__(self) -> tuple:
        self.last = time.time()
        if self.pair is None:
            self.pair = self.load()
        return self.pair

    def unload_if_idle(self, busy: bool) -> bool:
        if busy:
            self.last = time.time()  # idle counts from the end of a session, not its start
        if self.pair is None or busy or time.time() - self.last < self.unload_after_s:
            return False
        self.pair = None
        import gc
        gc.collect()
        return True


def run_daemon() -> None:
    import tkinter as tk

    import listen
    import orchestrator as o
    import overlay

    cfg = o.load_config()
    d = cfg.get("daemon", {})
    try:
        sock = socket.create_server(("127.0.0.1", PORT))
    except OSError:
        sys.exit("agentd ja esta rodando (ou a porta %d esta ocupada)" % PORT)
    root = tk.Tk()
    root.withdraw()
    v = cfg["voice"]
    models = Models(lambda: (listen.LazyWhisper(v["model"], v["language"]), listen.preload_intent(cfg)),
                    d.get("unload_after_s", 300))
    if d.get("mode", "leve") == "rapido":
        models()

    def start(cancel: threading.Event) -> threading.Thread:  # Tk thread: windows are created here
        ui = overlay.Overlay(master=root)
        t = threading.Thread(target=listen.session, args=(ui, o.load_config()),
                             kwargs={"models": models, "cancel": cancel}, daemon=True)
        t.start()
        return t

    agent, commands = Agent(start), queue.Queue()

    def pump():
        try:
            while not commands.empty():
                if commands.get() == "quit":
                    agent.cancel.set()
                    sock.close()
                    root.after(300, root.destroy)
                    return
                agent.toggle()
            if d.get("mode", "leve") == "leve":
                models.unload_if_idle(agent.busy())
        except Exception:  # a bad config or Tk error must not leave a daemon that says "ok" and does nothing
            import traceback
            with open(HOME / "agentd.log", "a", encoding="utf-8") as log:
                traceback.print_exc(file=log)
        root.after(100, pump)

    threading.Thread(target=serve, args=(sock, token(), commands.put), daemon=True).start()
    root.after(100, pump)
    root.mainloop()


# ---- client side (the hotkey) ----------------------------------------------------------------------------------------

def spawn_daemon() -> None:
    exe = Path(sys.executable)
    if sys.platform == "win32":
        quiet = exe.with_name("pythonw.exe")  # no console window
        subprocess.Popen([str(quiet if quiet.exists() else exe), str(Path(__file__).resolve())],
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen([str(exe), str(Path(__file__).resolve())], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def send(cmd: str, start_if_down: bool = True, wait_s: float = 15, port: int = PORT) -> bool:
    """Send one command; start the daemon first if it is down. True when it answered "ok"."""
    deadline, spawned = time.time() + wait_s, False
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2) as c:
                f = c.makefile("rwb")
                nonce = f.readline(40).decode(errors="replace").strip()
                f.write(f"{sign(token(), nonce, cmd)} {cmd}\n".encode())
                f.flush()
                return f.readline(8).startswith(b"ok")
        except (ConnectionRefusedError, socket.timeout):
            if not start_if_down or time.time() > deadline:
                return False
            if not spawned:
                spawn_daemon()
                spawned = True
            time.sleep(0.2)


def main(argv: Optional[list] = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group()
    g.add_argument("--toggle", action="store_true")
    g.add_argument("--quit", action="store_true")
    a = p.parse_args(argv)
    if a.toggle:
        sys.exit(0 if send("toggle") else 1)
    if a.quit:
        sys.exit(0 if send("quit", start_if_down=False) else 1)
    run_daemon()


if __name__ == "__main__":
    main()
