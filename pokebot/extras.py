"""Safari Zone and trading logic (pure game rules; Telegram glue lives in bot.py)."""
from __future__ import annotations

import random
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from . import game
from .battle import catch_attempt
from .data import get_provider
from .game import pretty

SAFARI_FEE, SAFARI_BALLS, SAFARI_STEPS = 300, 30, 67
SAFARI_LEGENDS = 3                      # legendary/mythical encounters guaranteed in every trip
FLEE_AFTER_THROW, FLEE_LEGEND = 0.35, 0.50      # a Pokémon that breaks free may bolt
DROP_CHANCE, MASTER_DROP_CHANCE = 0.05, 0.005   # when something flees: small item / (legendaries) Master Ball
DROPS = [("great-ball", 3, "Great Balls"), ("ultra-ball", 2, "Ultra Balls"), ("rare-candy", 1, "Rare Candy")]
BALL_POWER = 5.0
ENC: Dict[int, Dict[str, Any]] = {}          # uid -> the Pokémon currently in front of you


async def new_encounter(db, uid: int, region: str, rng: random.Random) -> Dict[str, Any]:
    badges = db.badge_count(uid, region)
    st = db.safari(uid)
    # the owed legendaries are spread at random over the remaining spawns, so every trip meets exactly SAFARI_LEGENDS
    if st["legs"] > 0 and rng.random() < st["legs"] / max(1, st["steps"]):
        rec = await game.legendary_record(region, rng)
        rec["area"] = "the Safari Zone"
        rec["legend"] = True
        db.safari_legend_seen(uid)
    else:
        rec = await game.wild_record(region, badges, rng)
    db.dex_seen(uid, rec["species_id"])
    db.set_safari(uid, st["balls"], st["steps"] - 1)
    ENC[uid] = {"rec": rec, "mood": ""}
    return ENC[uid]


def flee_chance(enc: Dict[str, Any]) -> float:
    return FLEE_LEGEND if enc.get("rec", {}).get("legend") else FLEE_AFTER_THROW


def catch_power(enc: Dict[str, Any]) -> float:
    return BALL_POWER


def zone_over(db, uid: int) -> bool:
    st = db.safari(uid)
    return bool(st) and (st["balls"] <= 0 or (st["steps"] <= 0 and uid not in ENC))


def act(db, uid: int, action: str, rng: random.Random) -> Tuple[List[str], str]:
    """One Safari turn. Returns (log lines, outcome) with outcome in: go | caught | fled | gone."""
    enc = ENC[uid]
    rec = enc["rec"]
    name = rec["species_name"]
    if action == "run":
        ENC.pop(uid, None)
        return [f"🏃 You ran away from {name}."], "gone"
    log: List[str] = []
    if action == "ball":
        st = db.safari(uid)
        db.set_safari(uid, st["balls"] - 1, st["steps"])
        shim = SimpleNamespace(hp=rec["hp"], max_hp=rec["hp"], status=None)
        caught, shakes = catch_attempt(shim, rec["capture_rate"], catch_power(enc), rng)
        log.append(f"⚪ You threw a Safari Ball! ({st['balls'] - 1} left)")
        if caught:
            ENC.pop(uid, None)
            return log, "caught"
        log.append(f"💨 {name} broke free!")
    if action == "ball" and rng.random() < flee_chance(enc):
        ENC.pop(uid, None)
        log.append(f"💨 {name} broke free and fled!")
        if rec.get("legend") and rng.random() < MASTER_DROP_CHANCE:
            db.add_item(uid, "master-ball", 1)
            log.append("✨ It dropped a MASTER BALL as it fled!")
        elif rng.random() < DROP_CHANCE:
            key, n, label = rng.choice(DROPS)
            db.add_item(uid, key, n)
            log.append(f"🎁 It dropped {n} {label} as it fled!")
        return log, "fled"
    return log, "go"


def keep_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in rec.items() if k not in ("capture_rate", "area")}


# --------------------------------------------------------------------------- trading
async def trade_evolve(db, uid: int, mid: int) -> List[str]:
    """Trade evolutions fire when a Pokémon arrives through a trade."""
    rec = db.mon(mid)
    sp = await get_provider().get_species(rec["species_id"])
    for e in sp.evos:
        if e.kind == "trade":
            return await game.evolve_mon(db, rec, e.to)
    return []


def tradable(db, uid: int, mid: Optional[int]) -> Optional[Dict[str, Any]]:
    r = db.mon(mid) if mid else None
    return r if r and r["owner"] == uid and not r["listed"] else None
