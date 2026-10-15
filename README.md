# Local AI Agent

Press a key, speak, and a small local model decides what to do.

1. **Voice**: hotkey → live transcription in a dark, cyberpunk-style overlay (faster-whisper, local).
2. **Decision**: a local intent matcher ([multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small), int8 ONNX, ~190 MB RAM, ~5 ms per decision) compares what you said with example phrases of each preset. Questions, "stop/close" verbs, low scores and near-ties never run a preset: they go to the AI or ask you to pick.
3. **Action**: an allowlisted *preset* runs locally (open browser, lock screen, launch a Steam game…). Anything else goes to **your own AI**, no API key: Claude Code, Codex or a local Ollama model (`brain.use`, default `auto` = first one installed). Short questions get a fast answer (Haiku on Claude); tasks that read files ask first. Answers stream in, and the AI remembers the conversation for 10 minutes (`brain.memory_idle_s`, RAM only).
4. **Solution picker**: if the AI finds several distinct solutions it asks you to pick one (keys 1-9), then continues.
5. **Dev overlay**: Claude Code hooks show live progress ("Editando app.py") in a screen corner.

Windows and Linux (Hyprland). The Linux scripts are untested on real hardware.

## Setup

```bash
pip install -r requirements.txt
python intent.py               # downloads the intent model (~125 MB, pinned and sha256-checked) into models/e5/
python game_indexer.py          # optional: index Steam games into games.json
python listen.py                # speak; silence ends the session
python orchestrator.py "abre o navegador" --dry-run   # text mode
```

Hyprland: `bind = SUPER, V, exec, ~/.config/local-ai-agent/voice_trigger.sh`
Windows: `python hotkey_windows.py` (Win+F12, easy to bind to a mouse button; Win+V is reserved by Windows)

The hotkey talks to a resident agent (`agentd.py`), started on the first press. Press again to cancel (mic, AI and its
child processes). `daemon.mode` in `presets.json`: `leve` (default) frees the speech and intent models after
`unload_after_s` idle, about 53 MB before first use, ~400 MB while listening, ~145 MB after unloading; `rapido` keeps them
loaded for instant start. The conversation memory survives either way. `python agentd.py --quit` stops it.

## Screen reading

Monitors are numbered: **1 = primary**, the rest left to right (`python monitors.py` lists them). Ask by voice:
"o que tem no monitor 2" (local OCR via RapidOCR, sends text) or "olha o monitor 2" (sends the image). Nothing is captured
unless you ask. You always confirm first; the confirmation shows the text that would leave your PC. Screen content is
passed to the AI as untrusted data (read-only tools, image saved only to a private temp folder and deleted).

## Appearance

`python settings.py` opens a live-preview window: size, font, colors, max width, and you can **drag** the two overlays (main and corner) to position them. Saved to `config.json` (git-ignored).

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
- Add `examples` (a few phrasings) to each preset in `presets.json` for better matching.

## Tests

```bash
python -m pytest -q
python eval_intent.py # labelled phrases incl. stop verbs and misheard speech; must show 0 wrong presets
```

## Notes

`assets/logo_src.jpg` is the author's own mark; replace it with yours if you fork. Josefin Sans is under the SIL OFL (`fonts/OFL.txt`).
