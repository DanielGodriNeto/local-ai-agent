"""Resident agent: loads once, then waits for the hotkey.

    python agentd.py            run it (start with the system; the hotkey also starts it when it is not running)
    python agentd.py --toggle   what the hotkey sends: start a voice session, or cancel the one running
    python agentd.py --type     the same with a text box instead of the mic
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

# numpy's OpenBLAS reserves ~480 MB of buffers for its threads; nothing here does heavy linear algebra
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

PORT = 48765
HERE = Path(__file__).resolve().parent
HOME = Path.home() / ".local-ai-agent"
TOKEN = HOME / "agentd.token"
COMMANDS = {"toggle", "type", "quit"}


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
    """At most one session in front. toggle() starts one, or cancels the one running. detach() lets a long task go on
    in the background (with its own cancel), so the next toggle starts a new session."""

    def __init__(self, start: Callable[..., threading.Thread]):
        self.start, self.thread, self.cancel, self.background = start, None, threading.Event(), []

    def busy(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def toggle(self, **kw) -> str:
        if self.busy():
            self.cancel.set()
            return "cancel"
        self.cancel = threading.Event()
        self.thread = self.start(self.cancel, **kw)
        return "start"

    def detach(self, cancel: threading.Event) -> None:
        """Called by the session that owns `cancel` (any thread)."""
        if self.cancel is cancel and self.thread:
            self.background = [(t, c) for t, c in self.background if t.is_alive()] + [(self.thread, cancel)]
            self.thread = None

    def working(self) -> bool:
        """A session in front or a task in the background: the models must stay loaded."""
        return self.busy() or any(t.is_alive() for t, _c in self.background)

    def cancel_background(self) -> None:
        for _thread, cancel in self.background:
            cancel.set()
        self.background = []


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
        if sys.platform == "win32":  # hand the freed pages back to Windows now, not when it runs short
            import ctypes
            ctypes.windll.kernel32.SetProcessWorkingSetSizeEx(ctypes.c_void_p(-1), ctypes.c_size_t(-1),
                                                              ctypes.c_size_t(-1), 0)
        return True


def run_daemon() -> None:
    import listen
    import orchestrator as o
    import overlay

    cfg = o.load_config()
    d = cfg.get("daemon", {})
    try:
        sock = socket.create_server(("127.0.0.1", PORT))
    except OSError:
        sys.exit("agentd ja esta rodando (ou a porta %d esta ocupada)" % PORT)
    from PySide6.QtCore import QTimer
    qt = overlay.app()
    qt.setQuitOnLastWindowClosed(False)  # a session window closing must not end the daemon
    v = cfg["voice"]
    models = Models(lambda: (listen.LazyWhisper(v["model"], v["language"]), listen.preload_intent(cfg),
                             listen.speaker(cfg)),
                    d.get("unload_after_s", 300))
    if d.get("mode", "leve") == "rapido":
        models()

    def start(cancel: threading.Event, typed: bool = False) -> threading.Thread:  # GUI thread: windows live here
        ui = overlay.Overlay()
        t = threading.Thread(target=listen.session, args=(ui, o.load_config()), daemon=True,
                             kwargs={"models": models, "cancel": cancel, "typed": typed, "notify": notify,
                                     "detach": lambda: agent.detach(cancel)})
        t.start()
        return t

    agent, commands = Agent(start), queue.Queue()
    notify = tray(commands)
    if not cfg.get("onboarded"):  # first run: the welcome window checks the AI, the mic and the models
        subprocess.Popen([sys.executable, str(HERE / "onboarding.py")])

    def pump():
        try:
            while not commands.empty():
                cmd = commands.get()
                if cmd == "cancel-background":
                    agent.cancel_background()
                    continue
                if cmd == "settings":
                    subprocess.Popen([sys.executable, str(HERE / "settings.py")])
                    continue
                if cmd == "quit":
                    agent.cancel.set()
                    agent.cancel_background()
                    stopping.set()
                    sock.close()
                    QTimer.singleShot(300, qt.quit)
                    return
                agent.toggle(typed=cmd == "type")
            if d.get("mode", "leve") == "leve":
                models.unload_if_idle(agent.working())
        except Exception:  # a bad config or Qt error must not leave a daemon that says "ok" and does nothing
            import traceback
            with open(HOME / "agentd.log", "a", encoding="utf-8") as log:
                traceback.print_exc(file=log)

    threading.Thread(target=serve, args=(sock, token(), commands.put), daemon=True).start()
    stopping = threading.Event()
    import wake
    if wake.ready(cfg):  # always-listening mic only once the wake models are installed and wake.enabled
        threading.Thread(target=wake.listen_forever, args=(cfg, lambda: commands.put("toggle"), agent.busy, stopping),
                         daemon=True).start()
    timer = QTimer()
    timer.timeout.connect(pump)
    timer.start(100)
    qt.exec()


def tray(commands: queue.Queue) -> Callable[[str, str], None]:
    """Tray icon: talk, type, settings, cancel background tasks, quit. Returns notify(title, text), any thread."""
    import overlay
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import QMenu, QSystemTrayIcon

    pix = QPixmap(64, 64)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(overlay.ACCENTS["blue"][1]))
    p.setPen(QColor(255, 255, 255, 230))
    p.drawEllipse(8, 8, 48, 48)
    p.end()
    icon = QSystemTrayIcon(QIcon(pix))
    icon.setToolTip("Assistente · Ctrl+Alt+Espaço fala · Ctrl+Alt+D escreve")
    menu = QMenu()
    for label, cmd in (("Falar", "toggle"), ("Escrever", "type"), ("Ajustes", "settings"),
                       ("Cancelar tarefas em segundo plano", "cancel-background"), ("Sair", "quit")):
        menu.addAction(label).triggered.connect(lambda _=False, c=cmd: commands.put(c))
    icon.setContextMenu(menu)
    icon.activated.connect(lambda why: commands.put("type") if why == QSystemTrayIcon.ActivationReason.Trigger
                           else None)
    icon.show()
    gui = overlay.Bridge()
    tray.keep = (icon, menu, gui)  # Qt objects must outlive this function
    return lambda title, text: gui.call.emit(lambda: icon.showMessage(title, text))


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
    g.add_argument("--type", action="store_true")
    g.add_argument("--quit", action="store_true")
    a = p.parse_args(argv)
    if a.toggle:
        sys.exit(0 if send("toggle") else 1)
    if a.type:
        sys.exit(0 if send("type") else 1)
    if a.quit:
        sys.exit(0 if send("quit", start_if_down=False) else 1)
    run_daemon()


if __name__ == "__main__":
    main()
