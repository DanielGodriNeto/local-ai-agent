"""Decision model (Ollama) routes text to: run_preset | answer | escalate (Claude Code) | clarify.

Usage: python orchestrator.py "abre o navegador" [--dry-run] [--yes]
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import os_adapter

HERE = Path(__file__).parent
ACTIONS = ["run_preset", "answer", "escalate", "clarify"]

SYSTEM = """Voce e o roteador de decisao de um assistente local. Responda SO com JSON.
Escolha UMA acao:
- run_preset: o pedido corresponde exatamente a um preset da lista. Preencha "preset" com a chave.
- answer: pergunta simples que voce responde sozinho, sem tocar no computador. Preencha "reply".
- escalate: exige ler/editar arquivos, codigo, debugar ou raciocinio longo. Preencha "task" com o pedido completo e autocontido para o Claude Code.
- clarify: pedido ambiguo ou sem preset correspondente. Preencha "reply" com UMA pergunta curta.
Nunca invente presets. Nunca gere comandos de shell.
Presets disponiveis:
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ACTIONS},
        "preset": {"type": "string"},
        "reply": {"type": "string"},
        "task": {"type": "string"},
    },
    "required": ["action"],
}


def load_config(path: Path = HERE / "presets.json") -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def ollama_decide(text: str, cfg: dict) -> dict:
    listing = "\n".join(f"- {k}: {v['description']}" for k, v in cfg["presets"].items())
    body = json.dumps({
        "model": cfg["decider"]["model"],
        "stream": False,
        "format": SCHEMA,
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": SYSTEM + listing},
            {"role": "user", "content": text},
        ],
    }).encode()
    req = urllib.request.Request(cfg["decider"]["url"], body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=cfg["decider"]["timeout_s"]) as r:
        return json.loads(json.loads(r.read())["message"]["content"])


def validate(decision: dict, cfg: dict) -> dict:
    """Never trust model output: unknown action or preset degrades to clarify."""
    action = decision.get("action")
    if action not in ACTIONS:
        return {"action": "clarify", "reply": "Nao entendi, pode repetir?"}
    if action == "run_preset" and decision.get("preset") not in cfg["presets"]:
        return {"action": "clarify", "reply": "Nao tenho esse comando. O que voce quer fazer?"}
    if action == "escalate" and not decision.get("task", "").strip():
        return {"action": "clarify", "reply": "O que exatamente devo pedir ao Claude Code?"}
    return decision


def escalate(task: str, cfg: dict) -> str:
    """Hand the task to Claude Code headless; read-only tools unless presets.json widens them."""
    esc = cfg["escalation"]
    claude = shutil.which("claude")
    if not claude:
        return "claude CLI nao encontrado no PATH"
    argv = [claude, "-p", task, "--permission-mode", esc["permission_mode"],
            "--allowedTools", *esc["allowed_tools"]]
    res = subprocess.run(argv, cwd=Path(esc["workdir"]).expanduser(), capture_output=True,
                         text=True, timeout=esc["timeout_s"], stdin=subprocess.DEVNULL)
    return (res.stdout or res.stderr).strip()


def log(entry: dict) -> None:
    entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(HERE / "agent.log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def confirmed(question: str, yes: bool) -> bool:
    return yes or (sys.stdin.isatty() and input(f"{question} [s/N] ").strip().lower() in ("s", "y"))


def handle(text: str, cfg: dict, decide=ollama_decide, dry_run=False, yes=False) -> str:
    try:
        decision = validate(decide(text, cfg), cfg)
    except Exception as e:  # Ollama down / bad JSON: fail closed, never guess an action
        decision = {"action": "clarify", "reply": f"Modelo de decisao indisponivel ({type(e).__name__})."}
    action = decision["action"]
    out = decision.get("reply", "")

    if action == "run_preset":
        name = decision["preset"]
        preset = cfg["presets"][name]
        out = f"preset: {name}"
        if dry_run:
            out += " (dry-run)"
        elif preset["confirm"] and not confirmed(f"Executar '{name}'?", yes):
            out += " (cancelado)"
        else:
            os_adapter.launch(preset)
    elif action == "escalate":
        task = decision["task"]
        out = f"escalar p/ Claude Code: {task}"
        if dry_run:
            out += " (dry-run)"
        elif confirmed("Enviar ao Claude Code?", yes):
            out = escalate(task, cfg)
        else:
            out += " (cancelado)"
    log({"input": text, "decision": decision, "dry_run": dry_run, "output": out[:500]})
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true", help="skip confirmations")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    print(handle(a.text, load_config(), dry_run=a.dry_run, yes=a.yes))
