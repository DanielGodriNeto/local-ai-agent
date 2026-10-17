# Local AI Agent

Press a key, speak, and a small local model decides what to do.

1. **Voice**: hotkey or "Hey Jarvis" → live transcription in a light pill at the top of the screen (faster-whisper, local). It follows the system theme (on Omarchy, the colours of whichever theme is on; Settings can switch to the plain Apple look), high contrast and "show animations" settings; questions come with buttons (Enter/Esc/1-9 work too).
2. **Decision**: a local intent matcher ([multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small), int8 ONNX, ~190 MB RAM, ~5 ms per decision) compares what you said with example phrases of each preset. Questions, "stop/close" verbs, low scores and near-ties never run a preset: they go to the AI or ask you to pick.
3. **Action**: an allowlisted *preset* runs locally (open browser, lock screen, launch a Steam game…). Anything else goes to **your own AI**, no API key: Claude Code, Codex or a local Ollama model (`brain.use`, default `auto` = first one installed). Short questions get a fast answer (Haiku on Claude); tasks that read files ask first. Answers stream in, and the AI remembers the conversation for 10 minutes (`brain.memory_idle_s`, RAM only).
4. **Solution picker**: if the AI finds several distinct solutions it shows one button per solution (or keys 1-9), then continues.
5. **Live Activity**: Claude Code hooks show live progress ("Editando app.py") in a small pill in a screen corner.

Windows and Linux (Hyprland). The Linux scripts are untested on real hardware.

## Setup

One command creates `.venv`, installs the exact versions (each file checked by hash) and runs the first-run checks:

```bash
powershell -ExecutionPolicy Bypass -File setup.ps1   # Windows
./setup.sh                                           # Linux
```

By hand (then use `.venv` python, or your own, for the commands below):

```bash
pip install --require-hashes -r requirements.txt   # edit requirements.in to change versions; a monthly workflow refreshes the lock
python onboarding.py            # checks the AI and the mic, downloads the models (also opens on the agent's first run)
python intent.py               # or just the intent model (~125 MB, pinned and sha256-checked) into models/e5/
python game_indexer.py          # optional: index Steam games into games.json
python listen.py                # speak; silence ends the session
python orchestrator.py "abre o navegador" --dry-run   # text mode
```

Hyprland: `bind = SUPER, V, exec, ~/.config/local-ai-agent/voice_trigger.sh` (talk) and `text_trigger.sh` (type)
Windows: `python hotkey_windows.py`: Ctrl+Alt+Space talks, Ctrl+Alt+D opens a text box (easy to bind to mouse buttons; Win+V is
reserved by Windows). The tray icon has the same actions, plus Settings.

A task that takes longer than `ui.background_after_s` (12 s) shrinks into a pill in the corner, so you can keep
working and even start another request. When it ends the pill turns green, a notification says so (`ui.notify`), and
a click opens the answer. Tray > "Cancelar tarefas em segundo plano" stops them.

The hotkey talks to a resident agent (`agentd.py`), started on the first press. Press again to cancel (mic, AI and its
child processes). `daemon.mode` in the config: `leve` (default) frees the speech and intent models after
`unload_after_s` idle, about 53 MB before first use, ~400 MB while listening, ~145 MB after unloading; `rapido` keeps them
loaded for instant start. The conversation memory survives either way. `python agentd.py --quit` stops it.

## Quick questions

Short questions ("quanto é 17 vezes 23?") go to a small local model when it is installed: answers in about 1 s and
nothing leaves the PC. `ollama pull qwen3:1.7b` once; it loads while you talk. Settings > "Perguntas rápidas" (or
`brain.quick: "main"`) sends them to the main AI's fast model instead (Claude Haiku). Without Ollama or the model, the
main AI answers as before. Tasks always go to the main AI. Measured on an RX 7600: qwen3:1.7b 0.3 s for the whole
answer, qwen3:8b 4.8 s, Claude Haiku over the CLI ~6.7 s.

## Chained requests

"abre o terminal e me explica o que é git", "abre o navegador e pesquisa receita de bolo": up to 3 steps, split only
before an action verb ("pesquisa arroz e feijão" stays one). One confirmation lists every step first; if any part is
not understood, nothing runs; an AI step still asks its own consent. "pesquisa no google X" searches on its own.
Clicks and typing are one request at a time.

## Voice

- Speech detection: Silero VAD (bundled with faster-whisper), so fans, music and clicks do not count as speech.
- Transcription: Whisper `small` (`voice.model`; `base` is lighter and less accurate).
- Short chimes when it starts listening, hears you, finishes or fails (`voice.earcons`).
- Spoken answers (optional): `pip install kokoro-onnx==0.6.1` then `python tts.py` (downloads ~340 MB, sha256-checked).
  Voices for pt, en, es, fr, it, hi, ja, zh follow `voice.language`. Only AI answers are spoken, up to ~400 characters;
  the hotkey or Esc cuts it. Uses ~430 MB while loaded; `leve` mode frees it when idle. Its phonemizer and espeak-ng
  are GPL-3 (installed by you, not shipped here).

## Wake word

`python wake.py` downloads three small models (~3.5 MB, sha256-checked); from then on the agent wakes when you say
**"Hey Jarvis"**, no key needed. Your own phrase: `python wake.py --record "ola computador"` (say it 3 times), then set
`"wake": {"phrase": "ola computador"}` in `config.json`. Turn it off with `"wake": {"enabled": false}`.

The mic stays open while the agent waits, but audio is processed locally in 80 ms pieces and never stored or sent.
It closes while a session runs. Idle cost measured: ~90 MB, ~4% of one CPU core. The "hey jarvis" model is
openWakeWord's (CC BY-NC-SA 4.0: personal, non-commercial use); your own phrase uses only the Apache-2.0 parts.

## Screen reading

Monitors are numbered: **1 = primary**, the rest left to right (`python monitors.py` lists them). Ask by voice:
"o que tem no monitor 2" (local OCR via RapidOCR, sends text) or "olha o monitor 2" (sends the image). Nothing is captured
unless you ask. You always confirm first; the confirmation shows the text that would leave your PC. Screen content is
passed to the AI as untrusted data (read-only tools, image saved only to a private temp folder and deleted).

## Click by voice (Windows, Hyprland)

- "clica em Salvar", "clique duplo em Documentos", "clique com o botão direito em Lixeira": finds the control by its
  name in the window you are using (UI Automation) and clicks it. Several matches: it asks which one.
- Nothing matches: with `ollama pull qwen3-vl:4b-instruct` installed, a local vision model finds it and the image
  never leaves the PC (~4 s on a mid GPU). Otherwise it offers to send an image of **that window only** to Claude
  Code. Either way it shows a ring where the AI points and asks "Clicar aqui?" before clicking. With Codex and no
  local model it suggests the grid instead. `brain.ollama.vision_model` picks the model ("" turns it off).
- "digita Olá, tudo bem?": types the text into the window you are using, after a yes that shows the text and the
  window. Never Enter, tab or other control keys, at most 500 characters, admin windows refused, and the text is not
  logged. ("escreve …" still goes to the AI, which writes it for you.)
- "mostrar grade": numbers 1-9 over the screen. Say a number to zoom into it, "clica" / "duplo" / "direito" to click
  its centre, "clica no 5" to click a cell at once, "cancela" to close. Works with the text box too (Ctrl+Alt+D).
- Admin windows are refused (Windows would silently drop the click), and nothing is clicked if the window moved or
  changed in the meantime.
- Hyprland (untested on a real session): needs `hyprctl`, `grim`, `ydotool` with `ydotoold` running (`wtype` too, for accents when typing), and AT-SPI
  (`python-gobject`, and apps with accessibility on). Windows owned by root are refused.

## Config

One file: defaults live in `config.default.json`; put only what you change in `config.json` (git-ignored), e.g.
`{"brain": {"use": "ollama"}, "voice": {"model": "small"}, "daemon": {"mode": "rapido"}}`. Your own presets go there too.

## Appearance

`python settings.py` opens the settings (General, Voice, Appearance): the AI to use, memory mode, language, spoken answers, wake word, theme, accent colour, text size and where the windows appear. Every change is saved at once to `config.json` (git-ignored). The UI is Qt (`PySide6-Essentials`, LGPL).

## Dev overlay (Claude Code hooks)

Merge [claude-hooks.example.json](claude-hooks.example.json) into your Claude Code settings (fix the path). The overlay shows only a tool name and file name or first command word, never full commands or contents.

## Safety model

- Presets are argv lists, run without a shell. Shells are rejected in proposals.
- Claude may *propose* presets; they are queued in `presets.pending.json` and only active after `python orchestrator.py --approve`.
- The AI gets the request on stdin, runs read-only (Claude: `--restricted`, tools `Read, Grep, Glob`, your hooks and settings not loaded; Codex: `--sandbox read-only`; Ollama: localhost only) inside its own empty folder (`brain.workdir`, default `~/.local-ai-agent/work`).
- Tasks always ask first (`--yes` does not cover them; `--yes-escalate` does). Quick questions go straight unless `brain.confirm_quick` is true.
- Cancel kills the AI's whole process tree; `brain.timeout_s` caps a run.
- The log stores metadata only (action, preset, input length).
- Unsure decisions escalate or ask; the model never guesses a preset. Presets that ask for confirmation repeat what was heard.
- Add `examples` (a few phrasings) to each preset for better matching.

## Tests

```bash
python -m pytest -q
python eval_intent.py # labelled phrases incl. stop verbs and misheard speech; must show 0 wrong presets
```
