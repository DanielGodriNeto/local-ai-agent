"""Appearance settings with live preview. Drag the two preview windows to place them. Run: python settings.py"""
import copy
import tkinter as tk
import tkinter.font as tkfont
from tkinter import colorchooser, ttk

import config
import overlay

BG, FG, MUTED, FIELD = "#0f1115", "#e8f1ee", "#8b93a1", "#1a1e25"
SLIDERS = [  # (label, key, min, max)
    ("Fonte: tamanho máximo", "size_max", 20, 120), ("Fonte: tamanho mínimo", "size_min", 10, 60),
    ("Largura mínima", "min_width", 200, 900), ("Largura máxima (% da tela)", "max_width_pct", 20, 95),
    ("Raio da borda", "radius", 0, 60), ("Margem interna", "pad", 8, 80), ("Símbolo (px)", "logo_px", 16, 140),
]
COLORS = [("Fundo", "bg"), ("Texto", "fg"), ("Dica", "hint"), ("Pensando", "idle"), ("Destaque", "accent"), ("Erro", "error")]
SAMPLES = {"main": "abre o navegador e procura receitas", "corner": "Editando orchestrator.py"}


class Settings:
    def __init__(self):
        overlay.load_bundled_fonts()
        self.ui = config.load()["ui"]
        self.root = r = tk.Tk()
        r.title("Local AI Agent · Aparência")
        r.option_add("*Font", ("Segoe UI", 12))
        r.configure(bg=BG, padx=20, pady=16)
        r.geometry("+60+60")
        self.kind = tk.StringVar(value="main")
        self.previews = {k: self._preview(k) for k in ("main", "corner")}
        self.pos = tk.StringVar()
        self._build()
        self._sync()

    def _preview(self, kind: str) -> overlay.Overlay:
        ov = overlay.Overlay(self.ui, kind, master=self.root)
        ov.edit, ov.on_move = True, lambda *_: self._sync()
        ov.status("", overlay.LISTEN)
        ov.text(SAMPLES[kind])
        return ov

    def _label(self, parent, text, **kw):
        return tk.Label(parent, text=text, bg=BG, fg=kw.pop("fg", FG), anchor="w", **kw)

    def _build(self) -> None:
        r = self.root
        self._label(r, "Janela a ajustar", fg=MUTED).pack(fill="x")
        row = tk.Frame(r, bg=BG)
        row.pack(fill="x", pady=(2, 10))
        for text, kind in (("Principal (fala)", "main"), ("Canto (progresso)", "corner")):
            tk.Radiobutton(row, text=text, value=kind, variable=self.kind, command=self._sync, bg=BG, fg=FG,
                           selectcolor=FIELD, activebackground=BG, activeforeground=FG).pack(side="left", padx=(0, 14))
        self.scales = {}
        for label, key, lo, hi in SLIDERS:
            self._label(r, label, fg=MUTED).pack(fill="x")
            s = tk.Scale(r, from_=lo, to=hi, orient="horizontal", bg=BG, fg=FG, troughcolor=FIELD, highlightthickness=0,
                         command=lambda v, k=key: self._set(k, int(float(v))))
            s.pack(fill="x", pady=(0, 6))
            self.scales[key] = s
        self._label(r, "Fonte", fg=MUTED).pack(fill="x")
        families = ["(automática)"] + sorted({f for f in tkfont.families(r) if not f.startswith("@")})
        self.font_box = ttk.Combobox(r, values=families, state="readonly")
        self.font_box.pack(fill="x", pady=(0, 10))
        self.font_box.bind("<<ComboboxSelected>>", self._font)
        self._label(r, "Cores", fg=MUTED).pack(fill="x")
        grid = tk.Frame(r, bg=BG)
        grid.pack(fill="x", pady=(2, 10))
        self.swatches = {}
        for i, (text, key) in enumerate(COLORS):
            b = tk.Button(grid, text=text, command=lambda k=key: self._color(k), relief="flat", bd=0, padx=10, pady=6)
            b.grid(row=i // 3, column=i % 3, padx=3, pady=3, sticky="ew")
            grid.columnconfigure(i % 3, weight=1)
            self.swatches[key] = b
        self._label(r, "", textvariable=self.pos, fg=MUTED).pack(fill="x")
        self._label(r, "Arraste as janelas de pré-visualização para posicioná-las.", fg=MUTED).pack(fill="x", pady=(0, 10))
        bar = tk.Frame(r, bg=BG)
        bar.pack(fill="x")
        for text, cmd in (("Salvar", self._save), ("Restaurar padrão", self._reset), ("Fechar", r.destroy)):
            tk.Button(bar, text=text, command=cmd, bg=FIELD, fg=FG, relief="flat", bd=0, padx=14, pady=8,
                      activebackground="#262b33", activeforeground=FG).pack(side="left", padx=(0, 8))

    def _redraw(self) -> None:
        for ov in self.previews.values():
            ov.apply(self.ui)

    def _set(self, key: str, value: int) -> None:
        if self.ui[self.kind.get()].get(key) != value:
            self.ui[self.kind.get()][key] = value
            self._redraw()

    def _font(self, _e=None) -> None:
        self.ui["font"] = "" if self.font_box.get() == "(automática)" else self.font_box.get()
        self._redraw()

    def _color(self, key: str) -> None:
        picked = colorchooser.askcolor(self.ui["colors"][key], parent=self.root)[1]
        if picked:
            self.ui["colors"][key] = picked
            self._redraw()
            self._sync()

    def _sync(self) -> None:
        """Push the selected window's values into the widgets."""
        kind = self.kind.get()
        for key, s in self.scales.items():
            s.set(self.ui[kind][key])
        self.font_box.set(self.ui["font"] or "(automática)")
        for key, b in self.swatches.items():
            c = self.ui["colors"][key]
            b.configure(bg=c, fg="#000000" if sum(int(c[i:i + 2], 16) for i in (1, 3, 5)) > 380 else "#ffffff",
                        activebackground=c)
        self.pos.set("   ".join(f"{k}: x {self.ui[k]['x_pct']}%  y {self.ui[k]['y_pct']}%" for k in ("main", "corner")))

    def _save(self) -> None:
        config.save({"ui": self.ui})
        self.pos.set("Salvo em config.json")

    def _reset(self) -> None:
        self.ui = copy.deepcopy(config.load(user=config.HERE / "does-not-exist.json")["ui"])
        for ov in self.previews.values():
            ov.apply(self.ui)
        self._sync()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    Settings().run()
