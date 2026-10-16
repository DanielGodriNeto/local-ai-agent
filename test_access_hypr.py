"""The Hyprland adapter with a fake hyprctl/ydotool and a fake AT-SPI tree: no Linux session needed."""
import json
import sys
import types

import access

ME = 4242
NOTES = {"address": "0xa1", "title": "Notas", "at": [100, 50], "size": [800, 600], "pid": 77, "mapped": True,
         "focusHistoryID": 2, "workspace": {"id": 1}}
HIDDEN = {**NOTES, "address": "0xc3", "title": "Outra área", "focusHistoryID": 1, "workspace": {"id": 9}}
OURS = {"address": "0xb2", "title": "Jarvis", "at": [10, 10], "size": [400, 60], "pid": ME, "focusHistoryID": 0}
MONITORS = [{"x": 0, "y": 0, "width": 3840, "height": 2160, "scale": 2, "transform": 0, "activeWorkspace": {"id": 1}},
            {"x": 1920, "y": 0, "width": 1920, "height": 1080, "scale": 1, "transform": 1, "activeWorkspace": {"id": 2}}]


class Hyprctl:
    def __init__(self, active):
        self.active, self.calls = active, []

    def __call__(self, argv):
        self.calls.append(argv)
        replies = {"activewindow": self.active, "clients": [NOTES, HIDDEN, OURS, {"title": "agent-grid"}],
                   "monitors": MONITORS, "cursorpos": {"x": 2000, "y": 100}}
        return json.dumps(replies[argv[1]]) if argv[0] == "hyprctl" and argv[-1] == "-j" else ""


def desk(active=NOTES, monkeypatch=None):
    if monkeypatch:
        monkeypatch.setattr(access.os, "getpid", lambda: ME)
    return access.HyprDesk(lambda f: f(), None, sh=Hyprctl(active))


def test_target_skips_our_own_window(monkeypatch):
    assert desk(NOTES, monkeypatch).target() == {"hwnd": "0xa1", "title": "Notas", "rect": (100, 50, 800, 600),
                                                 "pid": 77}
    assert desk(OURS, monkeypatch).target()["title"] == "Notas"  # the text box has focus: the window under it,
    # not the more recent one on a workspace nobody is looking at


def test_monitor_in_layout_pixels_for_scaled_and_rotated_screens():
    assert access.hypr_monitor(MONITORS, (500, 500)) == (0, 0, 1920, 1080)  # 4K at scale 2
    assert access.hypr_monitor(MONITORS, (2000, 1500)) == (1920, 0, 1080, 1920)  # a 1920x1080 panel turned portrait
    assert desk().monitor(None) == (1920, 0, 1080, 1920)  # no window: where the cursor is


def test_click_moves_the_cursor_then_clicks():
    d = desk()
    d.click((300, 200), "double")
    d.click((5, 6), "right")
    assert d.sh.calls == [["hyprctl", "dispatch", "movecursor", "300", "200"],
                          ["ydotool", "click", "--repeat", "2", "--next-delay", "80", "0xC0"],
                          ["hyprctl", "dispatch", "movecursor", "5", "6"], ["ydotool", "click", "0xC1"]]


def test_root_windows_are_refused(monkeypatch):
    monkeypatch.setattr(access.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(access, "_root_owned", lambda pid: {77: True, 78: False}.get(pid))
    d = desk()
    assert d.elevated_target({"pid": 77}) and not d.elevated_target({"pid": 78})
    assert d.elevated_target({"pid": 79})  # unknown counts as root


class Node:
    def __init__(self, name, role="botão", ext=(0, 0, 10, 10), showing=True, enabled=True, kids=(), pid=0):
        self.name, self.role, self.ext, self.kids, self.pid = name, role, ext, list(kids), pid
        self.states = {"SHOWING"} if showing else set()
        self.states |= {"ENABLED"} if enabled else set()

    def get_name(self): return self.name
    def get_localized_role_name(self): return self.role
    def get_process_id(self): return self.pid
    def get_child_count(self): return len(self.kids)
    def get_child_at_index(self, i): return self.kids[i]
    def get_state_set(self): return types.SimpleNamespace(contains=lambda s: s in self.states)
    def get_extents(self, _coord): return types.SimpleNamespace(x=self.ext[0], y=self.ext[1], width=self.ext[2],
                                                               height=self.ext[3])


def test_find_walks_the_apps_window_in_window_coordinates(monkeypatch):
    frame = Node("Notas", "frame", (0, 0, 800, 600), kids=[
        Node("Salvar", ext=(20, 30, 80, 24)),
        Node("Oculto", showing=False, kids=[Node("Dentro do oculto")]),
        Node("Desligado", enabled=False),
        Node("", "painel", kids=[Node("Enviar", ext=(200, 30, 80, 24))])])
    other = Node("Outra janela", "frame", kids=[Node("Não é esta")])
    top = Node("", kids=[Node("app", pid=77, kids=[other, frame]), Node("x", pid=5)])
    atspi = types.SimpleNamespace(get_desktop=lambda _i: top, CoordType=types.SimpleNamespace(WINDOW=1),
                                  StateType=types.SimpleNamespace(SHOWING="SHOWING", ENABLED="ENABLED"))
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_a: None
    repo = types.ModuleType("gi.repository")
    repo.Atspi = atspi
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repo)
    found = desk().find({"pid": 77, "title": "Notas", "rect": (100, 50, 800, 600)})
    assert {f["name"]: f["rect"] for f in found if f["type"] == "botão"} == {"Salvar": (120, 80, 80, 24),
                                                                            "Enviar": (300, 80, 80, 24)}
    assert desk().find({"pid": 77, "title": "Renomeada", "rect": (100, 50, 800, 600)}) == []  # never another window's


def test_desk_factory_picks_the_adapter(monkeypatch):
    monkeypatch.setattr(access.sys, "platform", "linux")
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    assert access.desk(None) is None
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    assert isinstance(access.desk(None), access.HyprDesk)


def test_consent_says_the_image_includes_what_covers_the_window():
    assert "por cima" in access.HyprDesk.SHOT  # grim grabs the screen area, not the window alone


def test_typing_prefers_wtype_for_accents_and_falls_back_to_ydotool(monkeypatch):
    for found, typer in (("/usr/bin/wtype", ["wtype", "--"]), (None, ["ydotool", "type", "--"])):
        monkeypatch.setattr(access.shutil, "which", lambda name, found=found: found)
        d = desk()
        assert d.type_text({"hwnd": "0xa1"}, "olá João") and d.sh.calls[-1] == typer + ["olá João"]
