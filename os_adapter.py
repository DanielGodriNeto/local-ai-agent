import subprocess
import sys

OS = "windows" if sys.platform == "win32" else "linux"


def launch(preset: dict) -> None:
    """Start a preset's argv for the current OS, detached, never through a shell."""
    argv = preset.get(OS)
    if not argv:
        raise ValueError(f"preset has no command for {OS}")
    subprocess.Popen(argv, shell=False, stdin=subprocess.DEVNULL)
