"""Bridge to the Pokémon Showdown simulator (engine/worker.js, a Node process speaking JSON lines)."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

ENGINE_DIR = Path(__file__).resolve().parent.parent / "engine"
NODE_BIN = os.environ.get("NODE_BIN", "node")


class EngineError(RuntimeError):
    pass


def node_available() -> bool:
    return shutil.which(NODE_BIN) is not None and (ENGINE_DIR / "node_modules" / "@pkmn" / "sim").exists()


def toid(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


class Engine:
    """One long-lived worker process; calls are serialized."""

    def __init__(self) -> None:
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.lock = asyncio.Lock()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.rid = 0

    async def _spawn(self) -> None:
        if not node_available():
            raise EngineError("Node.js or the engine packages are missing - run `npm install` inside the engine/ folder.")
        self.proc = await asyncio.create_subprocess_exec(
            NODE_BIN, str(ENGINE_DIR / "worker.js"), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=2 ** 24)

    async def call(self, op: str, **kw: Any) -> Dict[str, Any]:
        loop = asyncio.get_running_loop()
        if self.loop is not loop:                 # new event loop (tests run asyncio.run repeatedly): start fresh
            if self.proc and self.proc.returncode is None:
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass
            self.proc, self.loop, self.lock = None, loop, asyncio.Lock()
        async with self.lock:
            if self.proc is None or self.proc.returncode is not None:
                await self._spawn()
            self.rid += 1
            req = dict(kw, op=op, rid=self.rid)
            assert self.proc and self.proc.stdin and self.proc.stdout
            self.proc.stdin.write((json.dumps(req) + "\n").encode())
            await self.proc.stdin.drain()
            line = await self.proc.stdout.readline()
            if not line:
                self.proc = None
                raise EngineError("battle engine stopped unexpectedly")
            return json.loads(line)

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
        self.proc = None


_engine: Optional[Engine] = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = Engine()
    return _engine


# --------------------------------------------------------------------------- team conversion
def rec_to_set(rec: Dict[str, Any], *, full_hp: bool = False, max_hp: Optional[int] = None) -> Dict[str, Any]:
    """A DB/record dict -> a worker team entry. HP is only carried when a (smaller) current value is known."""
    s: Dict[str, Any] = {
        "num": rec["species_id"], "level": rec["level"], "moves": [toid(m) for m in rec["moves"]],
        "ability": toid(rec.get("ability", "")), "ivs": rec["iv"], "evs": rec.get("evs") or {},
        "teraType": (rec.get("tera_type") or "").capitalize() or None, "uid": rec.get("id"),
        "nick": rec.get("nickname") or "", "shiny": bool(rec.get("shiny")),
        "nature": (rec.get("nature") or "Hardy").capitalize(),
    }
    if rec.get("item"):
        from . import world
        it = world.ITEMS.get(rec["item"])
        if it:
            s["item"] = it.name
    form = (rec.get("form") or "")
    if form and not form.startswith("mon"):            # Showdown spells e.g. tauros-paldea-combat-breed without the suffix
        s["species"] = form.replace("-breed", "").replace("-standard", "")
    elif rec.get("species"):
        s["species"] = rec["species"]
    if not full_hp and rec.get("hp") is not None:
        s["hp"] = rec["hp"]
    return s


# --------------------------------------------------------------------------- protocol -> readable log
def _nm(ident: str, names: Dict[str, str]) -> str:
    """'p1a: Charizard' -> display name (mon nickname/species)."""
    ident = ident.split(": ", 1)[-1] if ": " in ident else ident
    return names.get(ident, ident)


def _pct(cond: str) -> str:
    try:
        hp, mx = cond.split(" ")[0].split("/")
        return f"{round(int(hp) * 100 / int(mx))}%"
    except Exception:
        return ""


_STATUS = {"brn": "was burned 🔥", "par": "is paralyzed ⚡", "psn": "was poisoned ☠️", "tox": "was badly poisoned ☠️",
           "slp": "fell asleep 💤", "frz": "was frozen solid ❄️"}
_CURE = {"brn": "burn", "par": "paralysis", "psn": "poison", "tox": "poison", "slp": "wakes up", "frz": "thawed out"}
_STATS = {"atk": "Attack", "def": "Defense", "spa": "Sp. Atk", "spd": "Sp. Def", "spe": "Speed", "accuracy": "accuracy",
          "evasion": "evasiveness"}


def format_log(lines: List[str], names: Optional[Dict[str, str]] = None) -> List[str]:
    """Turn Showdown protocol lines into short human lines. Unknown lines are skipped."""
    names = names or {}
    out: List[str] = []
    skip = False
    for ln in lines:
        if ln.startswith("|split|"):          # next line is the owner's exact copy, the one after is the public copy: keep one
            skip = True
            continue
        if skip:
            skip = False
            continue
        p = ln.split("|")
        if len(p) < 2:
            continue
        k = p[1]
        a = p[2:] if len(p) > 2 else []
        nm = lambda i: _nm(a[i], names) if len(a) > i else "?"
        if k == "move":
            out.append(f"▫️ {nm(0)} used {a[1]}!")
        elif k == "cant":
            why = a[1] if len(a) > 1 else ""
            if why == "recharge":
                continue
            out.append(f"▫️ {nm(0)} can't move" + {"slp": " (asleep)", "frz": " (frozen)", "par": " (paralyzed)"}.get(why, "") + ("!" if why else "!"))
        elif k in ("switch", "drag"):
            out.append(f"🔁 {'Go' if a[0].startswith('p1') else 'Sent out'}: {nm(0)}!")
        elif k == "-damage" and len(a) > 2 and a[2].startswith("[from]"):
            src = a[2].replace("[from] ", "").replace("item: ", "").replace("ability: ", "")
            out.append(f"   {nm(0)} is hurt by {src} ({_pct(a[1])})")
        elif k == "-damage":
            pc = _pct(a[1])
            if pc and pc != "0%":
                out.append(f"   {nm(0)} → {pc} HP")
        elif k == "-heal":
            out.append(f"   {nm(0)} recovered HP ({_pct(a[1])})")
        elif k == "faint":
            out.append(f"💀 {nm(0)} fainted!")
        elif k == "-supereffective":
            out.append("   It's super effective!")
        elif k == "-resisted":
            out.append("   It's not very effective…")
        elif k == "-immune":
            out.append(f"   It doesn't affect {nm(0)}…")
        elif k == "-crit":
            out.append("   A critical hit!")
        elif k == "-miss":
            out.append(f"   {nm(0)} avoided the attack!")
        elif k == "-fail":
            out.append("   But it failed!")
        elif k == "-status":
            out.append(f"   {nm(0)} {_STATUS.get(a[1], 'is afflicted')}")
        elif k == "-curestatus":
            out.append(f"   {nm(0)} recovered ({_CURE.get(a[1], a[1])})")
        elif k in ("-boost", "-unboost"):
            n = int(a[2]) if len(a) > 2 and a[2].lstrip("-").isdigit() else 1
            word = ("rose" if k == "-boost" else "fell") + (" sharply" if n == 2 else " drastically" if n >= 3 else "")
            out.append(f"   {nm(0)}'s {_STATS.get(a[1], a[1])} {word}!")
        elif k == "-weather":
            if a and a[0] != "none" and not (len(a) > 1 and a[1] == "[upkeep]"):
                out.append({"RainDance": "🌧 It started to rain!", "Sandstorm": "🏜 A sandstorm kicked up!",
                            "SunnyDay": "☀️ The sunlight turned harsh!", "Snow": "❄️ It started to snow!",
                            "Hail": "🌨 It started to hail!"}.get(a[0], f"Weather: {a[0]}"))
            elif a and a[0] == "none":
                out.append("🌤 The weather cleared.")
        elif k == "-fieldstart":
            out.append("🌐 " + a[0].replace("move: ", "") + " began!")
        elif k == "-sidestart":
            out.append("   " + a[1].replace("move: ", "") + " was set up!")
        elif k == "-ability" and len(a) > 1:
            out.append(f"   [{nm(0)}'s {a[1]}]")
        elif k == "-terastallize":
            out.append(f"💎 {nm(0)} Terastallized into the {a[1]} type!")
        elif k == "-mega":
            out.append(f"🌀 {nm(0)} Mega Evolved!")
        elif k == "-burst":
            out.append(f"✨ {nm(0)} went all out!")
        elif k == "-zpower":
            out.append(f"💠 {nm(0)} surrounded itself with Z-Power!")
        elif k == "-start" and a and a[1] == "Dynamax":
            out.append(f"🔴 {nm(0)} Dynamaxed!")
        elif k == "-end" and len(a) > 1 and a[1] == "Dynamax":
            out.append(f"   {nm(0)} returned to normal size.")
        elif k == "-start" and len(a) > 1 and a[1] in ("confusion", "Substitute", "Leech Seed", "Yawn", "Encore", "Taunt"):
            out.append(f"   {nm(0)}: {a[1].lower()}!")
        elif k == "-activate" and len(a) > 1 and "confusion" in a[1]:
            out.append(f"   {nm(0)} is confused!")
        elif k == "-activate" and len(a) > 1 and "Protect" in a[1]:
            out.append(f"   {nm(0)} protected itself!")
        elif k == "-message":
            out.append("   " + a[0])
    return out


def active_label(state: Dict[str, Any], side: int, slot: int = 0) -> Optional[Dict[str, Any]]:
    s = state["sides"][side]
    i = s["active"][slot] if slot < len(s["active"]) else -1
    return s["pokemon"][i] if i >= 0 else None
