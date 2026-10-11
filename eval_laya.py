"""Measure the real Laya decider on labelled phrases. Run after adding presets: python eval_laya.py
Wrong preset = dangerous (counted separately); escalate-by-doubt = safe but costs a Claude call."""
import sys

import laya
import orchestrator as o

CASES = [  # (phrase, expected preset name | "escalate")
    ("abre o navegador", "open_browser"), ("quero navegar na internet", "open_browser"),
    ("abre um site pra mim", "open_browser"), ("abre um terminal pra mim", "open_terminal"),
    ("abre o console", "open_terminal"), ("trava a tela do pc", "lock_screen"),
    ("bloqueia o computador", "lock_screen"), ("joga counter strike", "play_game"),
    ("abre o brawlhalla", "play_game"), ("quero jogar beamng", "play_game"),
    ("tem um bug no meu login que dá erro 500", "escalate"), ("refatora o módulo de pagamentos", "escalate"),
    ("explica o que esse repositório faz", "escalate"), ("qual a capital da França?", "escalate"),
    ("escreve um teste pra essa função", "escalate"),
]

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = o.load_config()
    sc = laya.LayaScorer(o.HERE / cfg["laya"]["model_dir"], cfg["laya"]["threads"])
    ok = wrong = doubt = 0
    for text, want in CASES:
        d = o.laya_decide(text, cfg, sc)
        got = d.get("preset") if d["action"] == "run_preset" else d["action"]
        if got == want:
            ok += 1
        elif got in ("escalate", "clarify"):
            doubt += 1
            print(f"  duvida   {text!r}: queria {want}, foi {got}")
        else:
            wrong += 1
            print(f"  ERRADO   {text!r}: queria {want}, foi {got}")
    print(f"{ok}/{len(CASES)} certos, {doubt} em duvida (seguro), {wrong} preset errado")
