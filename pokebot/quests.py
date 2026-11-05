"""Daily quests, weekly quests, login streak and the 10-day legendary roll. Pure rules + DB; Telegram glue is in bot.py."""
from __future__ import annotations

import datetime as dt
import random
from typing import Any, Dict, List, Optional, Tuple

from . import world

# tier -> [(id, event, goal, text, type_filter?)]
DAILY = {
    "easy": [("win_wild3", "win_wild", 3, "Win 3 wild battles"), ("catch2", "catch", 2, "Catch 2 Pokémon"),
             ("explore10", "explore", 10, "Explore 10 times"), ("heal1", "heal", 1, "Heal your team")],
    "medium": [("trainer2", "win_trainer", 2, "Beat 2 trainers"), ("gym1", "win_gym", 1, "Win a gym or League fight"),
               ("evolve1", "evolve", 1, "Evolve a Pokémon"), ("buy1", "buy", 1, "Buy something in the shop")],
    "hard": [("doubles1", "win_doubles", 1, "Win a double battle"), ("pvp1", "win_pvp", 1, "Win a friendly battle vs a player"),
             ("catchtype", "catch", 1, "Catch a {arg}-type Pokémon"), ("safari1", "safari", 1, "Finish a Safari Zone trip")],
}
WEEKLY = [("catch15", "catch", 15, "Catch 15 Pokémon"), ("gym5", "win_gym", 5, "Win 5 gym or League fights"),
          ("daily7", "daily_claim", 7, "Claim 7 daily quests")]
TYPES = ["fire", "water", "grass", "electric", "normal", "bug", "flying", "poison", "ground", "rock", "psychic", "ice", "dragon",
         "fighting", "ghost", "dark", "steel", "fairy"]
REWARD = {"easy": (300, {"poke-ball": 2}), "medium": (800, {"great-ball": 1}), "hard": (1500, {})}
ALL_THREE_BONUS = 1000
STREAK_MONEY = [1000, 1250, 1500, 1750, 2000, 2500, 3000]          # day 1..7 of each 7-day cycle
MASTER_BALL_CHANCE = 0.01
LEGEND_EVERY, LEGEND_CHANCE = 10, 0.20

LEGENDARIES = {                                        # national-dex ids of each region's legendaries / mythicals
    "kanto": [144, 145, 146, 150, 151], "johto": [243, 244, 245, 249, 250, 251],
    "hoenn": [377, 378, 379, 380, 381, 382, 383, 384, 385, 386], "sinnoh": [480, 481, 482, 483, 484, 485, 487, 488, 491, 492, 493],
    "unova": [638, 639, 640, 641, 642, 643, 644, 645, 646, 647, 648, 649], "kalos": [716, 717, 718, 719, 720, 721],
    "alola": [785, 786, 787, 788, 789, 790, 791, 792, 800, 801, 802], "galar": [888, 889, 890, 894, 895, 896, 897, 898],
    "paldea": [1001, 1002, 1003, 1004, 1007, 1008],
}


def today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def week() -> str:
    y, w, _ = dt.datetime.now(dt.timezone.utc).isocalendar()
    return f"{y}-W{w:02d}"


def _tmpl(scope: str, qid: str) -> Tuple[str, str, int, str]:
    pool = WEEKLY if scope == "w" else [t for ts in DAILY.values() for t in ts]
    return next(t for t in pool if t[0] == qid)


def tier_of(qid: str) -> str:
    return next(k for k, ts in DAILY.items() if any(t[0] == qid for t in ts))


def ensure(db, uid: int, rng: Optional[random.Random] = None) -> None:
    rng = rng or random.Random()
    for scope, period in (("d", today()), ("w", week())):
        rows = db.q("SELECT period FROM quests WHERE user_id=? AND scope=? LIMIT 1", (uid, scope))
        if rows and rows[0]["period"] == period:
            continue
        db.x("DELETE FROM quests WHERE user_id=? AND scope=?", (uid, scope))
        picks = [rng.choice(DAILY[t]) for t in ("easy", "medium", "hard")] if scope == "d" else [rng.choice(WEEKLY)]
        for slot, (qid, ev, goal, _text) in enumerate(picks):
            arg = rng.choice(TYPES) if qid == "catchtype" else ""
            db.x("INSERT INTO quests(user_id,scope,slot,period,qid,arg,progress,goal,claimed) VALUES(?,?,?,?,?,?,0,?,0)",
                 (uid, scope, slot, period, qid, arg, goal))


def listing(db, uid: int) -> Dict[str, List[Dict[str, Any]]]:
    ensure(db, uid)
    out: Dict[str, List[Dict[str, Any]]] = {"d": [], "w": []}
    for r in db.q("SELECT * FROM quests WHERE user_id=? ORDER BY scope,slot", (uid,)):
        d = dict(r)
        t = _tmpl(d["scope"], d["qid"])
        d["text"] = t[3].format(arg=d["arg"].title())
        d["tier"] = tier_of(d["qid"]) if d["scope"] == "d" else "weekly"
        out[d["scope"]].append(d)
    return out


def record(db, uid: int, event: str, n: int = 1, typ=None) -> None:
    """Progress every matching quest. Never raises: quests must not be able to break a battle."""
    try:
        ensure(db, uid)
        types = [typ] if isinstance(typ, str) else list(typ or [])
        for r in db.q("SELECT * FROM quests WHERE user_id=? AND claimed=0 AND progress<goal", (uid,)):
            t = _tmpl(r["scope"], r["qid"])
            if t[1] != event or (r["arg"] and r["arg"] not in types):
                continue
            db.x("UPDATE quests SET progress=MIN(goal, progress+?) WHERE user_id=? AND scope=? AND slot=?",
                 (n, uid, r["scope"], r["slot"]))
    except Exception:       # noqa: BLE001
        pass


def claim(db, uid: int, scope: str, slot: int, rng: Optional[random.Random] = None) -> Tuple[bool, str]:
    rng = rng or random.Random()
    ensure(db, uid)
    r = db.one("SELECT * FROM quests WHERE user_id=? AND scope=? AND slot=?", (uid, scope, slot))
    if not r or r["claimed"] or r["progress"] < r["goal"]:
        return False, "That quest isn't ready yet."
    db.x("UPDATE quests SET claimed=1 WHERE user_id=? AND scope=? AND slot=?", (uid, scope, slot))
    lines: List[str] = []
    if scope == "d":
        tier = tier_of(r["qid"])
        cash, items = REWARD[tier]
        items = dict(items)
        if tier == "hard":
            items["rare-candy" if rng.random() < 0.5 else "ultra-ball"] = 1
        db.add_money(uid, cash)
        for k, n in items.items():
            db.add_item(uid, k, n)
        lines.append("🎁 " + ", ".join([f"${cash:,}"] + [f"{n}× {world.ITEMS[k].name}" for k, n in items.items()]))
        record(db, uid, "daily_claim")
        if all(x["claimed"] for x in db.q("SELECT claimed FROM quests WHERE user_id=? AND scope='d'", (uid,))):
            db.add_money(uid, ALL_THREE_BONUS)
            db.add_item(uid, "ev-voucher", 1)
            lines.append(f"🌟 All 3 done! Bonus: ${ALL_THREE_BONUS:,} + a free EV Training Voucher")
            if rng.random() < 0.10:
                db.add_item(uid, "nature-mint", 1)
                lines.append("🍃 …and a Nature Mint!")
    else:
        opts = ["rare-candy", "ultra-ball"] + ([] if db.qty(uid, "shiny-charm") else ["shiny-charm"])
        pick = rng.choice(opts)
        n = {"rare-candy": 3, "ultra-ball": 10, "shiny-charm": 1}[pick]
        db.add_item(uid, pick, n)
        lines.append(f"🎁 Weekly reward: {n}× {world.ITEMS[pick].name}" + (" ✨ (shinies are a little more likely now!)" if pick == "shiny-charm" else ""))
    return True, "\n".join(lines)


def streak_claim(db, uid: int, rng: Optional[random.Random] = None) -> Optional[Dict[str, Any]]:
    """The /daily bonus with a login streak. None if already claimed today."""
    rng = rng or random.Random()
    td = today()
    r = db.one("SELECT last_daily, streak FROM players WHERE user_id=?", (uid,))
    if r is None or r["last_daily"] == td:
        return None
    yday = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    streak = (r["streak"] or 0) + 1 if r["last_daily"] == yday else 1
    cash = STREAK_MONEY[(streak - 1) % 7]
    lines = [f"💰 +${cash:,}"]
    if streak % 7 == 0:
        db.add_item(uid, "rare-candy", 1)
        lines.append("🍬 Week bonus: a Rare Candy!")
        if rng.random() < MASTER_BALL_CHANCE:
            db.add_item(uid, "master-ball", 1)
            lines.append("🟣 WOW! A Master Ball!")
    legend = False
    if streak % LEGEND_EVERY == 0:
        if rng.random() < LEGEND_CHANCE:
            legend = True
            lines.append("🌟 Something powerful is nearby... your next /explore will be a LEGENDARY encounter!")
        else:
            db.add_item(uid, "ultra-ball", 5)
            lines.append("🎯 Day {0} milestone: 5 Ultra Balls!".format(streak))
    db.x("UPDATE players SET last_daily=?, streak=?, money=money+?, legend_pending=? WHERE user_id=?",
         (td, streak, cash, 1 if legend else r_pending(db, uid), uid))
    return {"streak": streak, "money": cash, "legend": legend, "lines": lines}


def r_pending(db, uid: int) -> int:
    r = db.one("SELECT legend_pending FROM players WHERE user_id=?", (uid,))
    return (r["legend_pending"] or 0) if r else 0
