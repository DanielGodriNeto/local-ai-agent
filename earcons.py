"""Short synthesized cues, no audio files: listening, heard, done, error. Soft sine chimes, ~0.2 s each."""
import numpy as np

RATE = 44100


def chime(freqs: list[float], note_s: float = 0.09, gap_s: float = 0.015, volume: float = 0.16) -> np.ndarray:
    parts = []
    for f in freqs:
        t = np.arange(int(RATE * note_s)) / RATE
        env = np.minimum(1, t / 0.004) * np.exp(-t * 30)  # 4 ms attack (no click), quick natural decay
        parts += [(np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t)) * env, np.zeros(int(RATE * gap_s))]
    return (np.concatenate(parts) * volume).astype(np.float32)


CUES = {"listen": chime([659, 880]), "heard": chime([880, 659]), "done": chime([1047], note_s=0.14),
        "error": chime([392, 311])}


def play(name: str, enabled: bool = True) -> None:
    """Fire and forget; a missing or busy audio device never breaks a session."""
    if not enabled:
        return
    try:
        import sounddevice as sd
        sd.play(CUES[name], RATE)
    except Exception:  # noqa: BLE001 - sound is a nicety
        pass
