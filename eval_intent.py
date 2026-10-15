"""Measure the real intent matcher on labelled phrases. Run after adding presets: python eval_intent.py
Gate: 0 wrong presets. "doubt" (asked/escalated instead of running) is safe but costs a question or an AI call."""
import sys

import intent
import orchestrator as o

SAFE = "no_preset"  # any outcome except running a preset
CASES = [  # (phrase, expected: preset name | "quick" | "task" | SAFE)
    ("abre o navegador", "open_browser"), ("quero navegar na internet", "open_browser"),
    ("abre um site pra mim", "open_browser"), ("abre um terminal pra mim", "open_terminal"),
    ("abre o console", "open_terminal"), ("trava a tela do pc", "lock_screen"),
    ("bloqueia o computador", "lock_screen"), ("joga counter strike", "play_game"),
    ("abre o brawlhalla", "play_game"), ("quero jogar beamng", "play_game"),
    ("tem um bug no meu login que dá erro 500", "task"), ("refatora o módulo de pagamentos", "task"),
    ("explica o que esse repositório faz", "task"), ("qual a capital da França?", "quick"),
    ("escreve um teste pra essa função", "task"),
    # opposite verbs: must never run the "open" preset
    ("fecha o navegador", SAFE), ("para o jogo", SAFE), ("desliga o terminal", SAFE),
    ("encerra o counter strike", SAFE), ("pode parar o navegador", SAFE), ("cancela isso", SAFE),
    ("não abre o navegador", SAFE), ("nunca trava a tela", SAFE), ("pára o jogo", SAFE), ("sai do terminal", SAFE),
    ("don't lock the screen", SAFE), ("não quero jogar agora", SAFE),
    # speech-recognition noise: right preset or safe, never a wrong one
    ("abre o navegado", "open_browser|" + SAFE), ("a bri o terminal", "open_terminal|" + SAFE),
    ("trava a tela do pecê", "lock_screen|" + SAFE), ("abre o na vegador", "open_browser|" + SAFE),
    ("quero jogá", "play_game|" + SAFE),
    # things that look like presets but are questions/tasks
    ("como eu abro o terminal no linux?", "quick|task|" + SAFE), ("por que meu navegador está lento?", "quick|task|" + SAFE),
    ("quanto é 12 vezes 8", "quick"), ("me explica o que é uma API", "quick|task"),
]


def outcome(d: dict) -> str:
    if d["action"] == "run_preset":
        return d["preset"]
    return d.get("mode", d["action"]) if d["action"] == "escalate" else d["action"]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    m = intent.Matcher(o.load_config()["presets"])
    ok = wrong = doubt = 0
    for text, want in CASES:
        allowed, got = want.split("|"), outcome(m.decide(text))
        top = m.scores(text)[:2]
        if got in allowed or (SAFE in allowed and got not in o.load_config()["presets"]):
            ok += 1
        elif got in o.load_config()["presets"]:
            wrong += 1
            print(f"  ERRADO  {text!r}: queria {want}, foi {got}  {top}")
        else:
            doubt += 1
            print(f"  duvida  {text!r}: queria {want}, foi {got}  {top}")
    print(f"{ok}/{len(CASES)} certos, {doubt} em duvida (seguro), {wrong} preset errado")
    sys.exit(1 if wrong else 0)
