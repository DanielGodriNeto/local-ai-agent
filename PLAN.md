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
- [x] Every confirmation echoes the transcribed text ("Entendi: ...").
- [x] Session ids stored per brain (`--resume <id>`, never `--continue`); Ollama/Gemini replay last N turns from RAM.
- [x] Screen image mode: pass the image dir explicitly per brain (Claude `--add-dir`), or fall back to OCR text.
- [x] Preset proposals from any brain stay queued until `approve`.
- [x] Downloaded models pinned by revision and verified by sha256.
- [x] Conversation history only in RAM; log stays metadata-only.
- [x] Screen/OCR content still shown in full before it leaves the PC.

## Later phases

B voice (Silero VAD, whisper small, earcons, Kokoro TTS, "Hey Jarvis"), C UI (PySide6 glass pill, buttons,
Live Activity, settings, a11y), D onboarding, typed input, background tasks + notification,
E voice accessibility (number grid, click by name via UI Automation, AI fallback).

## Phase E: voice accessibility (Windows first)

Commands, matched before the intent model (strict, start of the sentence, never a question):
"clica em X" / "clique duplo em X" / "clique direito em X" / "aperta X"; "mostrar grade" / "grade".

| Step | What | Check (gate) |
|---|---|---|
| E1 | `access.py` pure part: parse commands and spoken numbers (5, "cinco"), rank UI names (exact > word prefix > contains, accent-insensitive), grid cell math (3x3, zoom into a cell), strict parse of the AI's coordinates. | Unit tests. |
| E2 | Windows adapter: target = top visible window not from this process (skip tool/cloaked windows). UI Automation via `comtypes` (one cached FindAll: name, type, rect, offscreen, enabled). Left click = Invoke pattern when there is one, else a real click at the centre; right/double = real click. Our own overlay windows turn click-through (WS_EX_TRANSPARENT) during a real click. | Flow tests with fakes; live smoke on Notepad. |
| E3 | Number grid: input-transparent window over the target's monitor, 3x3 numbers; a number zooms in, "clica" / "duplo" / "direito" clicks the centre, "clica no 5" clicks that cell, "cancela" or silence closes. Voice rounds reuse the mic; typed mode uses the text box. | Flow test with scripted answers; screenshot. |
| E4 | AI fallback when nothing matches: asks first, then sends only the target window (downscaled to <= 1280 px) to Claude Code, which returns JSON coordinates; a marker shows the point and asks "Clicar aqui?" before clicking. Codex/Ollama: suggest the grid instead. | Flow tests; coordinate validation tests. |

Security checklist (Phase E):
- [x] A single exact UI match clicks at once (the user named it); several matches ask which; the AI's point always asks, with a marker.
- [x] The window image is sent only after a yes that names the window and warns it may hold private data; temp file deleted in `finally`.
- [x] The AI's reply is data: only `{"x": int, "y": int}` inside the image is accepted; anything else = not found.
- [x] Never clicks our own windows; never runs when the overlay closed or the session was cancelled.
- [x] Log is metadata only (action, match count), never the names spoken or seen.
- [x] Elevated target windows are refused up front (UIPI would drop the click silently); unknown elevation = refuse.
- [x] Right before any click the target window must still be the top one, with the same rect; else "a janela mudou".
- [x] The image is the window alone (PrintWindow), not a crop of the screen with whatever overlaps it.
- [x] All coordinates are physical pixels (UIA, SendInput, grid/marker mapped per screen); AI points scaled back
      explicitly, tested with a negative-origin monitor.
- [x] Grid rounds: a fresh Segmenter per round, at most 8 rounds, no move to the background while it is open.
