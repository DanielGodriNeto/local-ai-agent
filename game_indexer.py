"""Scan Steam libraries -> games.json. Usage: python game_indexer.py"""
import json
import re
import sys
from pathlib import Path

import os_adapter

HERE = Path(__file__).parent
STEAM_ROOTS = [Path(p).expanduser() for p in (
    "C:/Program Files (x86)/Steam", "~/.local/share/Steam", "~/.steam/steam")]
SKIP = re.compile(r"redistributable|proton|steam linux runtime|steamworks|sdk|dedicated server", re.I)


def libraries() -> list[Path]:
    libs = []
    for root in filter(Path.exists, STEAM_ROOTS):
        libs.append(root / "steamapps")
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            libs += [Path(p.replace("\\\\", "\\")) / "steamapps"
                     for p in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="ignore"))]
    return list(dict.fromkeys(libs))


def launch_for(appid: str) -> dict:
    uri = f"steam://rungameid/{appid}"
    return {"linux": ["xdg-open", uri], "windows": ["cmd", "/c", "start", "", uri]}


def scan() -> list[dict]:
    games = {}
    for lib in libraries():
        for acf in lib.glob("appmanifest_*.acf"):
            text = acf.read_text(encoding="utf-8", errors="ignore")
            appid, name = (re.search(rf'"{k}"\s+"([^"]*)"', text) for k in ("appid", "name"))
            if appid and name and not SKIP.search(name.group(1)):
                games[appid.group(1)] = {"name": name.group(1), "appid": appid.group(1), **launch_for(appid.group(1))}
    return sorted(games.values(), key=lambda g: g["name"].lower())


def find_game(text: str, games: list[dict]) -> dict | None:
    """Best game whose significant name words (3+ letters, not numbers) appear in the request (>=50%)."""
    tw = set(re.findall(r"\w+", text.lower()))
    best, best_score = None, (0.0, 0)
    for g in games:
        gw = {w for w in re.findall(r"\w+", g["name"].lower()) if len(w) >= 3 and not w.isdigit()}
        hit = len(gw & tw)
        score = (hit / len(gw) if gw else 0.0, hit)
        if score[0] >= 0.5 and score > best_score:
            best, best_score = g, score
    return {**best, "exact": best_score[0] == 1.0} if best else None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    games = scan()
    (HERE / "games.json").write_text(json.dumps(games, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(games)} jogos indexados em games.json")
