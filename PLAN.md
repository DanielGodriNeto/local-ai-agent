# Redesign plan

Goal: feel like a resident personal assistant ("Jarvis"), light on RAM, works with any AI the user already has
(Claude Code, Codex, Gemini CLI, or local Ollama), no API keys.

## Phase A: base (this branch)

| Step | What | Check (gate) |
|---|---|---|
| A1 | Replace Laya (~1.2 GB) with `intent.py`: multilingual-e5-small int8 ONNX (~118 MB) + example phrases per preset. Close top-2 scores ask the user ("rodar ou parar?") instead of guessing. | `eval_intent.py`: 0 wrong presets, >= 13/15 original cases, ambiguity cases ask, opposite-verb cases (fecha/para/desliga) never run, ASR-noisy inputs never run a wrong preset. `pytest` green. |
| A2 | `brains.py`: adapters for Claude Code, Codex, Gemini CLI, Ollama. Streaming events, session resume, fast route for simple questions, cancel (kill process tree). | Unit tests with fake processes; live smoke test with `claude`. |
| A3 | `agentd.py` (keeps the Tk overlay in-process, so confirm/choose stay local; client only sends `toggle`) resident daemon, modes Leve (load on demand, unload after idle) / Rapido (keep loaded). Hotkey becomes a tiny client; pressing during a session cancels. | Tests: idle unload keeps history, toggle cancels, no answer/timeout = no. RSS measured idle/active. |
| A4 | Answer kept in RAM history (last 10); remove the 600-char cut and the auto-hide for long answers, overlay stays until dismissed. (Full card UI lands in Phase C.) | Test: a 5,000-char answer is kept whole. |
| A5 | One `config.json` (defaults in repo, user overrides git-ignored), presets included. | `pytest`. |

## Security checklist (Phase A)

- [x] Brains run in a neutral empty working dir (no repo hooks/MCP picked up), prompt on stdin, never argv.
- [x] Read-only by default: Claude `Read Grep Glob`; Codex `--sandbox read-only`; Gemini without auto-approve; Ollama has no tools.
- [x] Ollama URL must be loopback.
- [x] Cancel kills the whole process tree (Windows `taskkill /T /F`).
- [x] Daemon socket on 127.0.0.1 only, accepts a fixed command set, requires a per-user token file.
- [x] Ambiguous or low-confidence intent never runs a preset; confirm-flagged presets still confirm.
- [x] Polarity guard: a request with an opposing verb (fecha, para, desliga, encerra, cancela) never runs an "open/start" preset.
- [ ] Every confirmation echoes the transcribed text ("Entendi: ...").
- [ ] Session ids stored per brain (`--resume <id>`, never `--continue`); Ollama/Gemini replay last N turns from RAM.
- [ ] Screen image mode: pass the image dir explicitly per brain (Claude `--add-dir`), or fall back to OCR text.
- [ ] Preset proposals from any brain stay queued until `approve`.
- [ ] Downloaded models pinned by revision and verified by sha256.
- [ ] Conversation history only in RAM; log stays metadata-only.
- [ ] Screen/OCR content still shown in full before it leaves the PC.

## Later phases

B voice (Silero VAD, whisper small, earcons, Kokoro TTS, "Hey Jarvis"), C UI (PySide6 glass pill, buttons,
Live Activity, settings, a11y), D onboarding, typed input, background tasks + notification,
E voice accessibility (number grid, click by name via UI Automation, AI fallback).
