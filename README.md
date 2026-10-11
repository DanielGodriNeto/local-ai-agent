# Local AI Agent

Press a key, speak, and a small local model decides what to do.

1. **Voice**: hotkey → live transcription in a dark, cyberpunk-style overlay (faster-whisper, local).
2. **Decision**: [Laya](https://huggingface.co/leli14/laya_cpu_onnx) (ONNX choice classifier, CPU) votes over shuffled option orders until it reaches a confident majority.
3. **Action**: an allowlisted *preset* runs locally (open browser, lock screen, launch a Steam game…). Anything complex or ambiguous is escalated to **Claude Code** (`claude -p`) after you confirm.
4. **Solution picker**: if Claude Code finds several distinct solutions it asks you to pick one (keys 1-9), then continues.
5. **Dev overlay**: Claude Code hooks show live progress ("Editando app.py") in a screen corner.

Windows and Linux (Hyprland). The Linux scripts are untested on real hardware.

## Setup

```bash
pip install -r requirements.txt
# Laya model (~1.2 GB): download model_quantized.onnx and tokenizer.json into models/laya/
huggingface-cli download leli14/laya_cpu_onnx model_quantized.onnx tokenizer.json --local-dir models/laya
python game_indexer.py          # optional: index Steam games into games.json
python listen.py                # speak; silence ends the session
python orchestrator.py "abre o navegador" --dry-run   # text mode
```

Hyprland: `bind = SUPER, V, exec, ~/.config/local-ai-agent/voice_trigger.sh`
Windows: `python hotkey_windows.py` (Win+F12, easy to bind to a mouse button; Win+V is reserved by Windows)

## Appearance

`python settings.py` opens a live-preview window: size, font, colors, max width, and you can **drag** the two overlays (main and corner) to position them. Saved to `config.json` (git-ignored).

## Dev overlay (Claude Code hooks)

Merge [claude-hooks.example.json](claude-hooks.example.json) into your Claude Code settings (fix the path). The overlay shows only a tool name and file name or first command word, never full commands or contents.

## Safety model

- Presets are argv lists, run without a shell. Shells are rejected in proposals.
- Claude may *propose* presets; they are queued in `presets.pending.json` and only active after `python orchestrator.py --approve`.
- Escalation sends the task on stdin, read-only tools by default (`Read, Grep, Glob`), and always asks first (`--yes` does not cover it; `--yes-escalate` does).
- The log stores metadata only (action, preset, input length).
- Unsure decisions escalate or ask; the model never guesses a preset.

## Tests

```bash
python -m pytest -q
python eval_laya.py   # labelled phrases; rerun after adding presets
```

## Notes

`assets/logo_src.jpg` is the author's own mark; replace it with yours if you fork. Josefin Sans is under the SIL OFL (`fonts/OFL.txt`).
