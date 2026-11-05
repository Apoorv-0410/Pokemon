"""Battle sessions: glue between the Showdown engine, the database and a (Telegram-agnostic) UI.

Every public coroutine returns a list of `Out` objects describing what to show to whom.
`to` is a user id (their control message) or "chat" (a public message in the session's chat).
Battles run in the chat they were started in; in friendly battles both players' buttons are in the group.
"""
from __future__ import annotations

import asyncio
import random
import time
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, List, NamedTuple, Optional, Set

from . import game, quests, world
from .battle import catch_attempt, xp_gain
from .data import get_provider
from .showdown import EngineError, format_log, get_engine, rec_to_set

KB = List[List[tuple]]
STATUS_TAG = {"brn": "🔥BRN", "par": "⚡PAR", "psn": "☠️PSN", "tox": "☠️TOX", "slp": "💤SLP", "frz": "🧊FRZ"}
WEATHER_TAG = {"sunnyday": "☀️ Harsh sunlight", "desolateland": "☀️ Extremely harsh sunlight", "raindance": "🌧 Rain",
               "primordialsea": "🌧 Heavy rain", "sandstorm": "🏜 Sandstorm", "hail": "🌨 Hail", "snow": "❄️ Snow",
               "snowscape": "❄️ Snow", "deltastream": "🌪 Strong winds"}
GIMMICK_FLAG = {"mega": "canMegaEvo", "z": "canZMove", "dynamax": "canDynamax", "tera": "canTerastallize"}
GIMMICK_SUFFIX = {"mega": " mega", "z": " zmove", "dynamax": " dynamax", "tera": " terastallize"}
DIV = "━━━━━━━━━━━━━━"


class Out(NamedTuple):
    to: Any                       # user id | "chat"
    text: str
    kb: Optional[KB] = None
    photo: Optional[tuple] = None  # (species_id, back) -> handler turns into a sprite
    new: bool = False              # send a fresh message instead of editing
    frames: Optional[list] = None  # texts shown one after another before this message (catch suspense)
    img: Optional[dict] = None     # battle-scene picture spec: {foe, own, weather, terrain}


def mon_kb(uid: int, mid: int, in_party: bool, stats: bool = True) -> KB:
    """Buttons under a freshly caught Pokémon (and under /mon)."""
    top = [("✏️ Nickname", f"cm|{uid}|{mid}|nick")] + ([("📊 Stats", f"cm|{uid}|{mid}|stats")] if stats else [])
    return [top, [("📦 Send to box" if in_party else "👥 Add to party", f"cm|{uid}|{mid}|party"),
                  ("🗑 Release", f"cm|{uid}|{mid}|rel")], [("✅ Keep it", f"cm|{uid}|{mid}|done")]]


def caught_text(r: Dict[str, Any]) -> str:
    item = world.ITEMS[r["item"]].name if r.get("item") in world.ITEMS else "none"
    nick = f' "{r["nickname"]}"' if r.get("nickname") else ""
    return (f"🎉 {'✨ ' if r.get('shiny') else ''}{r['species_name']}{nick} Lv{r['level']} (ID {r['id']}) - "
            f"{'in your party' if r['slot'] is not None else 'in your box'}\n"
            f"Nature: {r.get('nature') or 'Hardy'} · Held item: {item}\nWhat now?")


@dataclass
class Session:
    id: str
    kind: str                       # wild | gym | pvp
    players: List[Optional[int]]    # user id per side (None = AI/wild)
    chat_id: int
    title: str = ""
    wild: Optional[Dict[str, Any]] = None
    trainer: Optional[Dict[str, Any]] = None                 # random trainer met while exploring
    gym: Optional[tuple] = None
    sets: Dict[int, List[Dict[str, Any]]] = field(default_factory=dict)     # side -> engine team entries
    recs: Dict[int, List[Dict[str, Any]]] = field(default_factory=dict)     # side -> DB records (human sides)
    ai_gimmick: Optional[str] = None
    gimmick: Dict[int, Optional[str]] = field(default_factory=dict)         # locked-in gimmick per side
    avail: Dict[int, List[str]] = field(default_factory=dict)
    started: bool = False
    state: Dict[str, Any] = field(default_factory=dict)
    reqs: List[Optional[dict]] = field(default_factory=lambda: [None, None])
    waiting: Set[int] = field(default_factory=set)
    gsel: Dict[int, Optional[str]] = field(default_factory=dict)
    mode: Dict[int, str] = field(default_factory=dict)
    participants: Set[int] = field(default_factory=set)       # DB mon ids that fought
    doubles: bool = False
    pend: Dict[int, List[str]] = field(default_factory=dict)      # doubles: choices made so far this turn, per side
    tsel: Dict[int, Any] = field(default_factory=dict)            # doubles: move waiting for a target (index, gimmick)
    rules: Dict[str, Any] = field(default_factory=dict)      # friendly-battle rules: size, level, gimmicks
    last_log: List[str] = field(default_factory=list)
    winner: Optional[str] = None
    done: bool = False
    touched: float = field(default_factory=time.time)

    def side_of(self, uid: int) -> Optional[int]:
        for i, p in enumerate(self.players):
            if p == uid:
                return i
        return None

    def mons(self, side: int) -> List[dict]:
        return self.state["sides"][side]["pokemon"] if self.state else []

    def actives(self, side: int) -> List[dict]:
        if not self.state:
            return []
        sd = self.state["sides"][side]
        return [sd["pokemon"][i] for i in sd["active"] if i >= 0]

    def active_mon(self, side: int) -> Optional[dict]:
        if not self.state:
            return None
        i = self.state["sides"][side]["active"][0]
        return self.state["sides"][side]["pokemon"][i] if i >= 0 else None


def _bar(hp: int, mx: int, width: int = 10) -> str:
    n = 0 if hp <= 0 else max(1, round(hp / max(1, mx) * width))
    return "█" * n + "░" * (width - n)


class Game:
    trainer_chance = world.TRAINER_CHANCE
    def __init__(self, db, rng: Optional[random.Random] = None):
        self.db = db
        self.rng = rng or random.Random()
        self.sessions: Dict[str, Session] = {}
        self.by_user: Dict[int, str] = {}
        self.tourney_results: list = []        # (tournament id, match index, winner uid or None) waiting for the bot to process

    # ------------------------------------------------------------------ bookkeeping
    def _cleanup(self):
        now = time.time()
        for sid in [k for k, s in self.sessions.items() if now - s.touched > 1800 or s.done]:
            self._drop(sid)

    def _drop(self, sid: str):
        s = self.sessions.pop(sid, None)
        if s:
            for u in s.players:
                if u is not None and self.by_user.get(u) == sid:
                    del self.by_user[u]
            if s.started:
                try:
                    asyncio.get_running_loop().create_task(get_engine().call("end", bid=sid))
                except RuntimeError:
                    pass

    def abort(self, sid: str):
        self._drop(sid)

    def active(self, uid: int) -> Optional[Session]:
        self._cleanup()
        sid = self.by_user.get(uid)
        return self.sessions.get(sid) if sid else None

    def _gimmicks(self, uid: int) -> List[str]:
        return [g for g, k in world.GIMMICK_KEY.items() if self.db.qty(uid, k) > 0]

    def _usable(self, uid: int, full_hp: bool) -> List[Dict[str, Any]]:
        return [r for r in self.db.party(uid) if r["hp"] > 0 or full_hp]   # fainted mons sit out PvE battles

    # ------------------------------------------------------------------ starting battles
    async def start_wild(self, uid: int, chat_id: int):
        if self.active(uid):
            return None, "You're already in a battle. Finish it first."
        p = self.db.player(uid)
        recs = self._usable(uid, False)
        if not recs:
            return None, "All your Pokémon have fainted! Use /heal first."
        training = p["ev_stat"] if (p.get("ev_left") or 0) > 0 else None
        quests.record(self.db, uid, "explore")
        legend = None
        if p.get("legend_pending") and not training:
            try:
                legend = await game.legendary_record(p["region"], self.rng)
            except Exception:                                    # noqa: BLE001 - unknown species etc.: fall back to a normal encounter
                legend = None
            self.db.x("UPDATE players SET legend_pending=0 WHERE user_id=?", (uid,))
        conq = p["region"] in self.db.conquered(uid)              # beaten this region's League: endgame wild levels
        event = None
        if not training and not legend and self.rng.random() < world.EVENT_CHANCE:
            try:
                event = await game.event_record(p["region"], self.db.badge_count(uid, p["region"]), self.rng, quests.today(), conq)
            except Exception:                                    # noqa: BLE001
                event = None
        if not training and not legend and not event and self.rng.random() < self.trainer_chance:
            return await self.start_trainer(uid, chat_id, recs)
        wr = legend or event or await game.wild_record(p["region"], self.db.badge_count(uid, p["region"]), self.rng, ev_stat=training, conquered=conq)
        if not wr.get("shiny") and self.db.qty(uid, "shiny-charm") and self.rng.randrange(8192) == 0:
            wr["shiny"] = 1                                       # Shiny Charm: roughly 1.5x better odds
        self.db.dex_seen(uid, wr["species_id"])
        s = Session(uuid.uuid4().hex[:6], "wild", [uid, None], chat_id, wild=wr)
        shiny = "✨ shiny " if wr.get("shiny") else ""
        s.title = (f"🌟 A LEGENDARY {shiny}{wr['species_name']} appeared!" if legend
                   else f"🎉 TODAY'S EVENT POKÉMON! A wild {shiny}{wr['species_name']} appeared — catch it for ${world.EVENT_BONUS}!" if event
                   else f"🌿 A wild {shiny}{wr['species_name']} appeared in {wr['area']}!")
        if training:
            self.db.use_training_encounter(uid)
            s.title += f"\n🏋️ EV training ({training.upper()}): {self.db.player(uid)['ev_left']} encounters left"
        s.recs[0] = recs
        s.sets[0] = [rec_to_set(r) for r in recs]
        s.sets[1] = [rec_to_set(wr, full_hp=True)]
        return await self._begin(s)

    async def start_trainer(self, uid: int, chat_id: int, recs=None):
        p = self.db.player(uid)
        recs = recs or self._usable(uid, False)
        badges = self.db.badge_count(uid, p["region"])
        t = await game.random_trainer(p["region"], badges, self.rng, p["region"] in self.db.conquered(uid))
        s = Session(uuid.uuid4().hex[:6], "trainer", [uid, None], chat_id, trainer=t)
        s.doubles = badges >= world.DOUBLES_FROM_GYM and len(recs) >= 2 and self.rng.random() < world.TRAINER_DOUBLES_CHANCE
        s.title = f"🧑 {t['cls']} {t['name']} wants to battle!\n💬 \"{t['intro']}\""
        s.recs[0] = recs
        s.sets[0] = [rec_to_set(r) for r in recs]
        for r in t["team"]:
            self.db.dex_seen(uid, r["species_id"])
        s.sets[1] = [rec_to_set(r, full_hp=True) for r in t["team"]]
        return await self._begin(s)

    async def start_gym(self, uid: int, chat_id: int):
        if self.active(uid):
            return None, "You're already in a battle. Finish it first."
        p = self.db.player(uid)
        region = world.REGION_BY_KEY[p["region"]]
        idx = min(self.db.badge_count(uid, p["region"]), 7)     # rematch the final leader
        recs = self._usable(uid, False)
        if not recs:
            return None, "All your Pokémon have fainted! Use /heal first."
        gym = region.gyms[idx]
        s = Session(uuid.uuid4().hex[:6], "gym", [uid, None], chat_id, gym=(p["region"], idx),
                    title=f"🏟 {gym.town} — {gym.leader} wants to battle! ({gym.type.title()}-type)")
        s.doubles = idx >= world.DOUBLES_FROM_GYM
        if s.doubles and len(recs) < 2:
            return None, "⚔️ This is a DOUBLE battle! You need at least 2 healthy Pokémon in your party."
        s.recs[0] = recs
        s.sets[0] = [rec_to_set(r) for r in recs]
        grecs = await game.gym_records(p["region"], idx)
        for r in grecs:
            self.db.dex_seen(uid, r["species_id"])
        s.sets[1] = [rec_to_set(r, full_hp=True) for r in grecs]
        # in gimmick regions the 3rd leader onward uses the region's gimmick (that is how you learn it exists)
        s.ai_gimmick = game.gym_ai_gimmick(p["region"]) if idx >= world.GIMMICK_FROM_GYM else None
        return await self._begin(s)

    async def start_league(self, uid: int, chat_id: int):
        """One League run = 5 fights in a row with the SAME team, fully healed before each fight. A loss ends the run."""
        if self.active(uid):
            return None, "You're already in a battle. Finish it first."
        p = self.db.player(uid)
        run = self.db.league_run(uid)
        region = run["region"] if run else p["region"]
        if not run:
            if self.db.badge_count(uid, region) < 8:
                return None, "You need all 8 badges in this region before you can enter the Pokémon League!"
            ids = [r["id"] for r in self.db.party(uid)]
            if len(ids) < 2:
                return None, "League fights are DOUBLE battles! You need at least 2 Pokémon in your party."
            self.db.start_league_run(uid, region, ids)
            run = self.db.league_run(uid)
        await game.heal_party(self.db, uid)                      # free healing between fights
        recs = [r for r in self.db.party(uid) if r["id"] in run["mons"]]
        if not recs:
            self.db.end_league_run(uid)
            return None, "Your League team is gone. The run was cancelled - try again!"
        idx = min(run["cleared"], world.LEAGUE_SIZE - 1)
        name, types, title, _, _ = world.league_foe(region, idx)
        s = Session(uuid.uuid4().hex[:6], "league", [uid, None], chat_id, gym=(region, idx), doubles=True,
                    title=f"🏆 League fight {idx + 1}/{world.LEAGUE_SIZE}: {title} {name}! "
                          f"({'/'.join(t.title() for t in dict.fromkeys(types))})")
        if len(recs) < 2:
            return None, "⚔️ League fights are DOUBLE battles! Your team needs at least 2 Pokémon."
        s.recs[0] = recs
        s.sets[0] = [rec_to_set(r) for r in recs]
        lrecs = await game.league_records(region, idx)
        for r in lrecs:
            self.db.dex_seen(uid, r["species_id"])
        s.sets[1] = [rec_to_set(r, full_hp=True) for r in lrecs]
        s.ai_gimmick = game.gym_ai_gimmick(region)
        return await self._begin(s)

    async def start_pvp(self, uid_a: int, uid_b: int, chat_id: int, rules: Optional[Dict[str, Any]] = None):
        rules = dict({"size": 6, "level": 0, "gimmicks": True, "doubles": False}, **(rules or {}))
        for u in (uid_a, uid_b):
            if self.active(u):
                return None, "One of you is already in a battle."
        s = Session(uuid.uuid4().hex[:6], "pvp", [uid_a, uid_b], chat_id)
        s.rules = rules
        s.doubles = bool(rules.get("doubles"))
        names = []
        for i, u in enumerate((uid_a, uid_b)):
            recs = self._usable(u, True)[:rules["size"]]
            if not recs:
                return None, "Both players need at least one Pokémon in their party."
            if s.doubles and len(recs) < 2:
                return None, "Doubles needs at least 2 Pokémon in each party (and a team size of 2+)."
            s.recs[i] = recs
            s.sets[i] = [dict(rec_to_set(r, full_hp=True), **({"level": rules["level"]} if rules["level"] else {})) for r in recs]
            names.append(self.db.player(u)["name"])
        s.title = f"🤝 {'Doubles' if s.doubles else 'Friendly'} battle: {names[0]} vs {names[1]}"
        return await self._begin(s)

    async def _begin(self, s: Session):
        self.sessions[s.id] = s
        for u in s.players:
            if u is not None:
                self.by_user[u] = s.id
        for i, u in enumerate(s.players):
            s.mode[i] = "main"
            s.gsel[i] = None
            if u is None:
                continue
            s.avail[i] = self._gimmicks(u) if s.rules.get("gimmicks", True) else []
            if len(s.avail[i]) == 1:
                s.gimmick[i] = s.avail[i][0]
            elif not s.avail[i]:
                s.gimmick[i] = None
            else:
                s.mode[i] = "lobby"          # several gimmicks owned: lock in ONE before the fight
        if any(s.mode[i] == "lobby" for i in (0, 1)):
            return s, self._views(s, first=True)
        try:
            await self._launch(s)
        except EngineError as e:
            self._drop(s.id)
            return None, f"⚠️ Battle engine unavailable: {e}"
        return s, self._views(s, first=True)

    async def _launch(self, s: Session):
        def side(i):
            return {"name": (self.db.player(s.players[i])["name"] if s.players[i] is not None else
                             ("Wild" if s.kind == "wild" else s.trainer["name"] if s.trainer else "Leader")),
                    "team": ([dict(t, megaAuto=False) for t in s.sets[i]] if s.players[i] is not None else s.sets[i]), "gimmick": s.gimmick.get(i) if s.players[i] is not None else s.ai_gimmick}
        r = await get_engine().call("new", bid=s.id, format="doubles" if s.doubles else "singles", seed=self.rng.getrandbits(31), p1=side(0), p2=side(1))
        if not r.get("ok"):
            raise EngineError(r.get("error", "could not start the battle"))
        s.started = True
        self._absorb(s, r)
        s.last_log = []

    # ------------------------------------------------------------------ engine state
    def _absorb(self, s: Session, r: Dict[str, Any]) -> List[str]:
        s.state = r["state"]
        s.reqs = r["requests"]
        s.waiting = {i for i, sid in enumerate(("p1", "p2")) if sid in r["waiting"]}
        s.winner = r["winner"] if r["ended"] else None
        for sd in s.state["sides"][:1]:
            for m in sd["pokemon"]:
                if m["active"] and m["uid"] is not None:
                    s.participants.add(m["uid"])
        return r["log"]

    async def _submit(self, s: Session, side: int, choice: Optional[str]) -> List[str]:
        """Send one side's choice (None = lose the turn, used for ball throws), then let AI sides act."""
        eng = get_engine()
        sid = f"p{side + 1}"
        r = await eng.call("idle" if choice is None else "choose", bid=s.id, side=sid, **({} if choice is None else {"choice": choice}))
        if not r.get("ok"):
            return ["⚠️ That choice isn't allowed."]
        raw = list(self._absorb(s, r))
        for _ in range(4):
            ai = [i for i in (0, 1) if s.players[i] is None and i in s.waiting and not s.winner]
            if not ai:
                break
            i = ai[0]
            c = await eng.call("ai", bid=s.id, side=f"p{i + 1}", gimmick=s.ai_gimmick)
            r = await eng.call("choose", bid=s.id, side=f"p{i + 1}", choice=c["choice"])
            if not r.get("ok"):
                break
            raw += self._absorb(s, r)
        return format_log(raw)

    # ------------------------------------------------------------------ rendering
    def _block(self, m: dict, exact: bool) -> List[str]:
        mx = m["maxhp"]
        shown = f"{m['hp']}/{mx}" if exact else f"{round(m['hp'] * 100 / max(1, mx))}%"
        tags = []
        if m["status"]:
            tags.append(STATUS_TAG.get(m["status"], m["status"].upper()))
        if m["dynamaxed"]:
            tags.append("🔴DMAX")
        if m["tera"]:
            tags.append(f"💎{m['tera']}")
        if m["mega"]:
            tags.append("🧬MEGA")
        star = "✨" if m.get("shiny") else ""
        lines = [f"• {star}{m['name']} Lv{m['level']} — HP {shown}", f"  {_bar(m['hp'], mx)}"]
        if tags:
            lines.append(f"• Status: {' '.join(tags)}")
        return lines

    def _field_lines(self, s: Session, side: int) -> List[str]:
        extra = []
        w = s.state.get("weather", "")
        if w:
            extra.append(WEATHER_TAG.get(w, w))
        if s.state.get("terrain"):
            extra.append("🌐 " + s.state["terrain"].replace("terrain", " Terrain").title())
        g = s.gimmick.get(side)
        if g in ("mega", "dynamax", "tera") and any(m["dynamaxed"] or m["tera"] or m["mega"] for m in s.mons(side)):
            extra.append(f"✨ Gimmick used: {world.GIMMICK_NAME[g]}")
        return [f"• {x}" for x in extra]

    def scene(self, s: Session) -> str:
        """Public battlefield: both sides in percentages + the latest log."""
        lines = ([s.title, DIV] if s.title else [])
        for i in (1, 0):
            for m in s.actives(i):
                who = self._who(s, i)
                lines += [f"{'🔴' if i == 1 else '🔵'} {who}"] + self._block(m, exact=(i == 0 and s.kind != "pvp"))
        lines += self._field_lines(s, 0)
        lines += ["", DIV, "📜 LOG"] + [f"• {x}" if not x.startswith(("   ", "▫", "🔁", "💀", "💎", "🧬", "💠", "🔴", "✨", "🌐", "🌧", "🏜", "☀", "❄", "🌨", "🌤", "🌀")) else x
                                         for x in s.last_log[-14:] if x.strip()]
        return "\n".join(lines)[:3900]

    def _prompt(self, s: Session, side: int) -> List[str]:
        """Doubles: tell the player which Pokémon they are choosing for."""
        if not (s.doubles and side in s.waiting and s.started):
            return []
        k = len(s.pend.get(side, []))
        sd = s.state["sides"][side]
        if self._mode(s, side) == "replace":
            return ["", "👉 Choose a replacement Pokémon:"]
        if k < len(sd["active"]) and sd["active"][k] >= 0:
            nm = sd["pokemon"][sd["active"][k]]["name"]
            return ["", f"👉 Choose an action for {nm}" + (" — pick a target!" if self._mode(s, side) == "target" else "")]
        return []

    def _who(self, s: Session, i: int) -> str:
        if s.players[i] is not None:
            return self.db.player(s.players[i])["name"]
        return "FOE" if s.kind == "wild" else s.state["sides"][i]["name"]

    def render(self, s: Session, side: int) -> str:
        if self._mode(s, side) == "lobby":
            names = ", ".join(world.GIMMICK_NAME[g] for g in s.avail[side])
            return (f"{s.title}\n{DIV}\n✨ You own several gimmicks ({names}).\n"
                    "Pick ONE to use in this battle — it can't be changed once the fight starts.")
        if not s.started:
            return f"{s.title}\n{DIV}\n⏳ Waiting for the other trainer to choose their gimmick..."
        if s.kind == "pvp":
            head = f"🎮 {self._who(s, side)} — Turn {s.state['turn']}"
            body = []
            for me in s.actives(side):
                body += self._block(me, exact=True)
            opp = [f"vs {self._who(s, 1 - side)}'s " + " & ".join(f"{f['name']} ({round(f['hp'] * 100 / max(1, f['maxhp']))}%)" for f in s.actives(1 - side))]
            extra = self._prompt(s, side)
            return "\n".join([head, DIV] + body + opp + extra)
        text = self.scene(s)
        extra = self._prompt(s, side)
        return (text + "\n" + "\n".join(extra))[:3990] if extra else text

    def _mode(self, s: Session, side: int) -> str:
        m = s.mode.get(side, "main")
        if m == "lobby":
            return m
        if not s.started:
            return "main"
        rq = s.reqs[side]
        if rq and rq.get("forceSwitch") and side in s.waiting:
            return "replace"
        return m

    def keyboard(self, s: Session, side: int) -> Optional[KB]:
        if s.done:
            return None
        mode = self._mode(s, side)
        if mode == "lobby":
            rows = [[(f"✨ {world.GIMMICK_NAME[g]}", f"l|{s.id}|{g}")] for g in s.avail[side]]
            return rows + [[("🚫 No gimmick", f"l|{s.id}|none")]]
        if not s.started:
            return None
        if side not in s.waiting and mode != "ball":
            return [[("⏳ Waiting for opponent...", f"n|{s.id}|")]]
        mons = s.mons(side)
        rq = s.reqs[side] or {}
        self._advance(s, side)
        if mode == "replace":
            ok = self._bench(s, side) if s.doubles else [i for i, m in enumerate(mons) if not m["fainted"] and not m["active"]]
            return [[(f"{m['name']} Lv{m['level']} ({m['hp']}/{m['maxhp']})", f"p|{s.id}|{i}")] for i, m in enumerate(mons) if i in ok]
        if mode == "switch":
            ok = self._bench(s, side) if s.doubles else [i for i, m in enumerate(mons) if not m["fainted"] and not m["active"]]
            rows = [[(f"{m['name']} Lv{m['level']} ({m['hp']}/{m['maxhp']})", f"w|{s.id}|{i}")] for i, m in enumerate(mons) if i in ok]
            return rows + [[("⬅️ Back", f"x|{s.id}|main")]]
        if mode == "target":
            mv = self._move_for_target(self._slot_act(s, side), *s.tsel[side])
            return [[(lab, f"t|{s.id}|{tg}")] for tg, lab in self._targets(s, side, mv)] + [[("⬅️ Back", f"x|{s.id}|main")]]
        if mode == "ball":
            inv = self.db.inventory(s.players[side])
            rows = [[(f"{world.ITEMS[k].name} x{inv[k]}", f"c|{s.id}|{k}")] for k in world.BALL_KEYS if inv.get(k, 0) > 0]
            return rows + [[("⬅️ Back", f"x|{s.id}|main")]] if rows else [[("No balls! Buy some in /shop", f"x|{s.id}|main")]]
        act = self._slot_act(s, side)
        g = s.gimmick.get(side)
        sel = s.gsel.get(side)
        maxm = (act.get("maxMoves") or {}).get("maxMoves")
        rows, row = [], []
        for k, m in enumerate(act.get("moves", [])):
            if m.get("disabled"):
                continue
            label = f"{m.get('move', m.get('id'))} ({m.get('pp', '?')}/{m.get('maxpp', '?')})" if m.get("maxpp") else m.get("move")
            if maxm and k < len(maxm):
                label = "🔴 " + maxm[k]["move"]
            if sel == "z":
                z = (act.get("canZMove") or [None] * 4)[k] if k < len(act.get("canZMove") or []) else None
                if not z:
                    continue
                label = "💠 " + z["move"]
            row.append((label, f"m|{s.id}|{k}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        used_now = s.doubles and any(GIMMICK_SUFFIX.get(g, "~").strip() in c for c in s.pend.get(side, []))
        if g and act.get(GIMMICK_FLAG[g]) and not used_now:
            rows.append([(("✅ " if sel == g else "✨ ") + world.GIMMICK_NAME[g], f"g|{s.id}|{g}")])
        ctl = []
        can_switch = (bool(self._bench(s, side)) if s.doubles else any(not m["fainted"] and not m["active"] for m in mons)) and not act.get("trapped")
        if can_switch:
            ctl.append(("🔄 Switch", f"s|{s.id}|"))
        if s.kind == "wild":
            ctl += [("⚪ Ball", f"b|{s.id}|"), ("🏃 Run", f"r|{s.id}|")]
        else:
            ctl.append(("🏳️ Forfeit", f"f|{s.id}|"))
        rows.append(ctl)
        return rows

    # ------------------------------------------------------------------ doubles helpers
    def _slot_act(self, s: Session, side: int) -> dict:
        acts = (s.reqs[side] or {}).get("active") or [{}]
        return acts[min(len(s.pend.get(side, [])), len(acts) - 1)] if s.doubles else acts[0]

    def _advance(self, s: Session, side: int) -> None:
        """Auto-fill 'pass' for slots with nothing to do (fainted, or no replacement available)."""
        if not s.doubles:
            return
        rq = s.reqs[side] or {}
        sd = s.state["sides"][side]
        pend = s.pend.setdefault(side, [])
        n = len(sd["active"])
        force = rq.get("forceSwitch")
        while len(pend) < n:
            k = len(pend)
            idx = sd["active"][k]
            fainted = idx < 0 or sd["pokemon"][idx]["fainted"]
            if force:
                picked = {int(c.split()[1]) - 1 for c in pend if c.startswith("switch")}
                bench = [i for i, m in enumerate(sd["pokemon"]) if not m["fainted"] and not m["active"] and i not in picked]
                if not force[k] or not bench:
                    pend.append("pass")
                    continue
            elif fainted:
                pend.append("pass")
                continue
            break

    def _bench(self, s: Session, side: int) -> List[int]:
        picked = {int(c.split()[1]) - 1 for c in s.pend.get(side, []) if c.startswith("switch")}
        return [i for i, m in enumerate(s.mons(side)) if not m["fainted"] and not m["active"] and i not in picked]

    @staticmethod
    def _move_for_target(act: dict, k: int, suffix: str) -> dict:
        """The move to aim: the Max Move when Dynamaxing (its target differs from the base move)."""
        mm = (act.get("maxMoves") or {}).get("maxMoves")
        if "dynamax" in suffix and mm and k < len(mm):
            return mm[k]
        return act["moves"][k]

    def _targets(self, s: Session, side: int, mv: dict) -> List[Tuple[str, str]]:
        """(showdown target, label) choices for a move in doubles; empty when no target is needed."""
        t = mv.get("target")                      # locked moves (Outrage...) come without a target: no choice to make
        if not t:
            return []
        k = len(s.pend.get(side, []))
        foes = [(str(j + 1), m["name"]) for j, m in enumerate(
            [s.state["sides"][1 - side]["pokemon"][i] if i >= 0 else None for i in s.state["sides"][1 - side]["active"]]) if m and not m["fainted"]]
        sd = s.state["sides"][side]
        ally_i = sd["active"][1 - k] if len(sd["active"]) == 2 else -1
        ally = [(str(-(2 - k)), sd["pokemon"][ally_i]["name"] + " (ally)")] if ally_i >= 0 and not sd["pokemon"][ally_i]["fainted"] else []
        if t in ("normal", "any"):
            opts = foes + ally
        elif t == "adjacentFoe":
            opts = foes
        elif t == "adjacentAlly":
            opts = ally
        elif t == "adjacentAllyOrSelf":
            opts = ally + [(str(-(k + 1)), "Itself")]
        else:
            return []
        return opts if len(opts) > 1 else ([(opts[0][0], opts[0][1])] if opts else [])

    def image_spec(self, s: Session, side: int = 0) -> Optional[dict]:
        """What the battle picture should show (None for lobby/pvp/finished states)."""
        if not s.started or not s.state:
            return None
        keep = ("name", "species", "num", "level", "hp", "maxhp", "bhp", "bmax", "status", "shiny", "dynamaxed", "tera", "mega")
        foes, owns = s.actives(1 - side), s.actives(side)
        if not foes or not owns:
            return None
        pick = lambda m: {k: m[k] for k in keep}
        spec = {"foe": pick(foes[0]), "own": pick(owns[0]), "weather": s.state.get("weather", ""), "terrain": s.state.get("terrain", "")}
        if s.kind == "pvp":
            spec["public"] = True
        if s.doubles:
            spec["foe2"] = pick(foes[1]) if len(foes) > 1 else None
            spec["own2"] = pick(owns[1]) if len(owns) > 1 else None
        return spec

    def _views(self, s: Session, first: bool = False) -> List[Out]:
        outs = []
        for i, u in enumerate(s.players):
            if u is None:
                continue
            photo = None
            if first and s.kind == "wild" and s.started:
                photo = (s.active_mon(1)["num"], False)
            outs.append(Out(u, self.render(s, i), self.keyboard(s, i), photo, new=first, img=None if s.kind == "pvp" else self.image_spec(s, i)))
        if first and s.kind == "pvp" and s.started:
            outs.append(Out("chat", self.scene(s), new=True, img=self._pvp_img(s)))        # opening scene + picture for the whole group
        return outs

    def _views_for(self, s: Session, side: int) -> List[Out]:
        u = s.players[side]
        return [Out(u, self.render(s, side), self.keyboard(s, side), img=None if s.kind == "pvp" else self.image_spec(s, side))] if u is not None else []

    def _pvp_scene(self, s: Session) -> List[Out]:
        return [Out("chat", self.scene(s), new=True, img=self._pvp_img(s))] if s.kind == "pvp" and s.started else []

    def _pvp_img(self, s: Session) -> Optional[dict]:
        """Shared spectator picture for a friendly battle (side 1 in front, side 2 facing us)."""
        spec = self.image_spec(s, 0)
        return dict(spec, pid=s.id) if spec else None

    # ------------------------------------------------------------------ input handling
    async def handle(self, uid: int, data: str) -> List[Out]:
        try:
            op, sid, arg = data.split("|", 2)
        except ValueError:
            return []
        s = self.sessions.get(sid)
        if not s or s.done:
            return [Out(uid, "This battle is over.", None)]
        side = s.side_of(uid)
        if side is None:
            return []
        s.touched = time.time()
        try:
            return await self._handle(s, uid, side, op, arg)
        except EngineError as e:
            self._drop(s.id)
            return [Out(uid, f"⚠️ The battle engine stopped: {e}. The battle was cancelled.", None)]

    async def _handle(self, s: Session, uid: int, side: int, op: str, arg: str) -> List[Out]:
        mode = self._mode(s, side)
        if op == "n":
            return []
        if op == "l" and mode == "lobby":
            g = arg if arg in s.avail[side] else None
            s.gimmick[side] = g
            s.mode[side] = "main"
            if any(s.mode[i] == "lobby" for i in (0, 1)):
                return self._views(s)
            await self._launch(s)
            return self._views(s, first=True) + self._pvp_scene(s)
        if not s.started:
            return []
        if op == "x":
            s.mode[side] = "main"
            if s.doubles:
                s.pend[side] = []
                s.tsel.pop(side, None)
                s.gsel[side] = None
            return self._views_for(s, side)
        if op == "f":
            return await self._forfeit(s, side)
        if op == "r" and s.kind == "wild":
            s.last_log = [f"You got away safely from the wild {s.active_mon(1)['name']}."]
            return await self._finish(s, ran=True)
        if side not in s.waiting:
            return self._views_for(s, side)
        rq = s.reqs[side] or {}
        self._advance(s, side)
        act = self._slot_act(s, side)
        if op == "g" and mode == "main":
            g = s.gimmick.get(side)
            if arg == g and act.get(GIMMICK_FLAG.get(g, "")):
                s.gsel[side] = None if s.gsel.get(side) == arg else arg
            return self._views_for(s, side)
        if op == "s" and mode == "main":
            s.mode[side] = "switch"
            return self._views_for(s, side)
        if op == "b" and s.kind == "wild" and mode == "main":
            s.mode[side] = "ball"
            return self._views_for(s, side)
        if op == "c" and s.kind == "wild":
            return await self._throw_ball(s, side, arg)

        choice = None
        if op == "p" and mode == "replace" or op == "w" and mode == "switch":
            i = int(arg)
            mons = s.mons(side)
            if i >= len(mons) or mons[i]["fainted"] or mons[i]["active"] or (s.doubles and i not in self._bench(s, side)):
                return self._views_for(s, side)
            choice = f"switch {i + 1}"
        elif op == "m" and mode == "main":
            k = int(arg)
            moves = act.get("moves", [])
            if k >= len(moves) or moves[k].get("disabled"):
                return self._views_for(s, side)
            sel = s.gsel.get(side)
            suffix = GIMMICK_SUFFIX[sel] if sel and act.get(GIMMICK_FLAG[sel]) else ""
            if s.doubles:
                opts = self._targets(s, side, self._move_for_target(act, k, suffix))
                if len(opts) > 1:                       # ask which Pokémon to aim at
                    s.tsel[side] = (k, suffix)
                    s.mode[side] = "target"
                    return self._views_for(s, side)
                choice = f"move {k + 1}" + (f" {opts[0][0]}" if opts else "") + suffix
            else:
                choice = f"move {k + 1}" + suffix
        elif op == "t" and mode == "target" and s.doubles:
            k, suffix = s.tsel.pop(side)
            if arg not in {tg for tg, _ in self._targets(s, side, self._move_for_target(act, k, suffix))}:
                s.mode[side] = "main"
                return self._views_for(s, side)
            choice = f"move {k + 1} {arg}{suffix}"
        else:
            return self._views_for(s, side)
        s.gsel[side] = None
        s.mode[side] = "main"
        if s.doubles:
            s.pend.setdefault(side, []).append(choice)
            self._advance(s, side)
            n = len(s.state["sides"][side]["active"])
            if len(s.pend[side]) < n:
                return self._views_for(s, side)         # next Pokémon's turn to choose
            choice = ", ".join(s.pend[side])
            s.pend[side] = []
        before = s.state.get("turn")
        log = await self._submit(s, side, choice)
        if log and log[0].startswith("⚠️"):
            s.pend[side] = []
            return self._views_for(s, side)
        return await self._after(s, log, before)

    async def _after(self, s: Session, log: List[str], before_turn: Any) -> List[Out]:
        if s.winner:
            s.last_log = log or s.last_log
            return await self._finish(s)
        turn_done = s.state.get("turn") != before_turn or bool(log)
        if log:
            s.last_log = log
        for i in (0, 1):
            if s.players[i] is not None and s.mode.get(i) in ("switch", "ball"):
                s.mode[i] = "main"
        outs = self._views(s)
        if s.kind == "pvp" and turn_done and log:
            outs.append(Out("chat", self.scene(s), new=True, img=self._pvp_img(s)))
        return outs

    # ------------------------------------------------------------------ wild catching
    async def _throw_ball(self, s: Session, side: int, ball_key: str) -> List[Out]:
        uid = s.players[side]
        item = world.ITEMS.get(ball_key)
        if not item or item.kind != "ball":
            return self._views_for(s, side)
        if not self.db.take_item(uid, ball_key, 1):
            s.mode[side] = "main"
            s.last_log = ["You don't have that ball!"]
            return self._views_for(s, side)
        foe = s.active_mon(1)
        shim = SimpleNamespace(hp=foe["hp"], max_hp=foe["maxhp"], status=foe["status"] or None)
        mine = s.active_mon(side)
        form = await get_provider().get_form(foe["num"])
        area = (s.wild.get("area") or "").lower()
        mult = game.ball_multiplier(
            ball_key, turn=s.state.get("turn", 1), foe_level=foe["level"], my_level=mine["level"],
            foe_types=[t.lower() for t in foe["types"]], foe_speed=form.stats.get("spe", 0), night=not game._is_day(),
            cave=any(w in area for w in ("cave", "cavern", "mine", "tunnel", "tower")),
            caught_before=self.db.dex_caught(uid, foe["num"]))
        caught, shakes = catch_attempt(shim, s.wild["capture_rate"], mult, self.rng)
        frames = self._shake_frames(item.name, shakes)
        log = [f"You threw a {item.name}!"]
        s.mode[side] = "main"
        if caught:
            rec = {k: v for k, v in s.wild.items() if k not in ("capture_rate", "area", "event")}
            rec["hp"] = max(1, foe["hp"])
            mid = self.db.add_mon(uid, rec)
            quests.record(self.db, uid, "catch", typ=[t.lower() for t in foe["types"]])
            bonus = ""
            if s.wild.get("event") and (self.db.player(uid).get("event_claimed") != quests.today()):
                self.db.x("UPDATE players SET event_claimed=? WHERE user_id=?", (quests.today(), uid))
                self.db.add_money(uid, world.EVENT_BONUS)
                bonus = f"🎉 Event bonus: +${world.EVENT_BONUS}!"
            where = "your party" if any(r["id"] == mid for r in self.db.party(uid)) else "your box"
            star = "✨ " if s.wild.get("shiny") else ""
            s.last_log = log + [f"🎉 Gotcha! {star}{foe['name']} was caught and sent to {where}! (ID {mid})"] + ([bonus] if bonus else [])
            if s.wild.get("shiny"):
                s.last_log.append("✨ A SHINY Pokémon! Lucky you!")
            s.last_log += await self._xp_and_save(s, award=False)
            outs = self._attach_frames(await self._finish(s, caught=True), uid, frames)
            r = self.db.mon(mid)
            return list(outs) + [Out(uid, caught_text(r), mon_kb(uid, mid, r["slot"] is not None), new=True)]
        log.append(f"Oh no! {foe['name']} broke free!")
        before = s.state.get("turn")
        res = await self._submit(s, side, None)       # the throw costs your turn: the wild Pokémon attacks
        s.last_log = log + res
        outs = await self._after(s, s.last_log, before)
        return self._attach_frames(outs, uid, frames)

    @staticmethod
    def _shake_frames(ball_name: str, shakes: int) -> List[str]:
        """Suspense shown before the result: the ball wobbles once per shake."""
        frames = [f"🎯 You threw a {ball_name}!"]
        for k in range(1, max(1, shakes) + 1):
            frames.append("⚪ " + " ".join(["."] * k))
        return frames

    @staticmethod
    def _attach_frames(outs: List[Out], uid: int, frames: List[str]) -> List[Out]:
        return [o._replace(frames=frames) if o.to == uid else o for o in outs]

    # ------------------------------------------------------------------ endings
    async def _forfeit(self, s: Session, side: int) -> List[Out]:
        s.winner = "p2" if side == 0 else "p1"
        s.last_log = [f"{self._who(s, side)} forfeited the battle!"]
        return await self._finish(s)

    async def _xp_and_save(self, s: Session, award: bool, foes: Optional[List[dict]] = None, lost: bool = False) -> List[str]:
        """Write HP back to the DB for the human side; optionally grant XP (Exp. Share: every healthy party member)."""
        msgs: List[str] = []
        by_uid = {m["uid"]: m for m in s.mons(0) if m["uid"] is not None}
        recs = s.recs.get(0, [])
        for r in recs:
            m = by_uid.get(r["id"])
            if m:
                r["hp"] = max(0, min(m["bhp"], m["bmax"]))
                self.db.update_mon(r["id"], hp=r["hp"])
        if award and foes:
            p = get_provider()
            total = 0
            for f in foes:
                form = await p.get_form(f["num"])
                total += xp_gain(form.base_exp, f["level"], s.kind in ("gym", "league", "trainer"))
            healthy = [r for r in recs if r["hp"] > 0]
            gains: Dict[str, int] = {}
            for f in foes:
                for st, n in (await p.get_form(f["num"])).ev_yield.items():
                    gains[st] = gains.get(st, 0) + n
            for r in recs:
                if r["id"] in s.participants:
                    if r["hp"] > 0:
                        game.apply_evs(self.db, r, gains)
                    self.db.set_friendship(r["id"], r.get("friendship", 70) + 4)
            if healthy:
                msgs.append(f"Your whole team gained {total} XP (Exp. Share).")
                for r in healthy:
                    msgs += await game.award_xp(self.db, r, total)
        elif lost:
            for r in recs:                       # a loss slightly dents the bond of those who fought
                if r["id"] in s.participants:
                    self.db.set_friendship(r["id"], r.get("friendship", 70) - 2)
        return msgs

    async def _finish(self, s: Session, caught: bool = False, ran: bool = False) -> List[Out]:
        """Settle a finished battle: persist HP, grant XP/money/badges, record W/L, build the final messages."""
        s.done = True
        sid = s.id
        log = [x for x in s.last_log if x.strip()]
        res: List[str] = []
        uid0 = s.players[0]
        won = s.winner == "p1"
        if s.kind in ("wild", "gym", "league", "trainer") and not ran and not caught:
            foes = s.mons(1) if s.kind in ("gym", "league", "trainer") else [s.active_mon(1)]
            msgs = await self._xp_and_save(s, award=won, foes=foes if won else None, lost=not won)
            self.db.record_result(uid0, won)
            if won:
                quests.record(self.db, uid0, {"wild": "win_wild", "trainer": "win_trainer"}.get(s.kind, "win_gym"))
                if s.doubles:
                    quests.record(self.db, uid0, "win_doubles")
                if s.kind == "wild":
                    foe = s.active_mon(1)
                    money = foe["level"] * 8
                    self.db.add_money(uid0, money)
                    res.append(f"🏆 You defeated the wild {foe['name']}! +${money}")
                elif s.kind == "trainer":
                    t = s.trainer
                    money = sum(m["level"] for m in s.mons(1)) * 12
                    self.db.add_money(uid0, money)
                    res.append(f"🏆 You defeated {t['cls']} {t['name']}! +${money}")
                    res.append(f"💬 {t['name']}: \"{t['lose_line']}\"")
                elif s.kind == "league":
                    region, idx = s.gym
                    first = self.db.give_league(uid0, region, idx)
                    if idx == world.LEAGUE_SIZE - 1:
                        self.db.end_league_run(uid0)
                    else:
                        self.db.advance_league_run(uid0)
                    prize = game.league_prize(region, idx, first)
                    self.db.add_money(uid0, prize)
                    name, _, title, _, _ = world.league_foe(region, idx)
                    res.append(f"🏆 You beat {title} {name}! +${prize}")
                    reg = world.REGION_BY_KEY[region]
                    if first and idx == world.LEAGUE_SIZE - 1:
                        first_conquest = len(self.db.conquered(uid0)) == 1
                        res.append(f"👑 You are the new Champion of {reg.name}!")
                        if first_conquest:
                            later = ", ".join(r.name for r in world.REGIONS[world.STARTER_REGIONS:])
                            res.append(f"🌍 {later} are now open — use /travel!")
                    elif first:
                        pass
                    if idx < world.LEAGUE_SIZE - 1:
                        res.append(f"⭐ Fight {idx + 1}/{world.LEAGUE_SIZE} cleared! Your team is healed for free before the next one — /league.")
                else:
                    region, idx = s.gym
                    first = self.db.give_badge(uid0, region, idx)
                    prize = game.gym_prize(region, idx, first)
                    self.db.add_money(uid0, prize)
                    gym = world.REGION_BY_KEY[region].gyms[idx]
                    res.append(f"🏆 You beat {gym.leader}! +${prize}")
                    if first:
                        res.append(f"🎖 You earned the {gym.badge}! ({self.db.badge_count(uid0, region)}/8)")
                        gkey = world.GIMMICK_KEY.get(game.gym_ai_gimmick(region) or "")
                        if gkey and idx == world.GIMMICK_FROM_GYM and self.db.qty(uid0, gkey) == 0:
                            self.db.add_item(uid0, gkey, 1)
                            res.append(f"🎁 {gym.leader} gave you the {world.ITEMS[gkey].name}! "
                                       f"Pick it in the pre-battle gimmick screen.")
                        if self.db.badge_count(uid0, region) == 8:
                            res.append("🏆 All 8 badges! The Pokémon League awaits — use /league.")
                res += msgs
            else:
                loss = min(self.db.money(uid0), self.rng.randint(25, 30)) if s.kind in ("wild", "trainer") else min(self.db.money(uid0) // 20, 500)
                if loss:
                    self.db.add_money(uid0, -loss)
                res.append(f"💀 You blacked out... lost ${loss}. Use /heal to recover.")
                if s.kind == "trainer":
                    res.append(f"💬 {s.trainer['name']}: \"{s.trainer['win_line']}\"")
                if s.kind == "league":
                    self.db.end_league_run(uid0)
                    res.append("🚪 Your League run has ended. Start again with /league — you can pick a new team.")
        elif s.kind == "pvp":
            if s.winner in ("p1", "p2"):
                w = 0 if s.winner == "p1" else 1
                res.append(f"🏆 {self._who(s, w)} wins the friendly battle!")
                quests.record(self.db, s.players[w], "win_pvp")
                if s.doubles:
                    quests.record(self.db, s.players[w], "win_doubles")
                self.db.record_result(s.players[w], True, pvp=True)
                self.db.record_result(s.players[1 - w], False, pvp=True)
                if s.rules.get("tid"):
                    self.tourney_results.append((s.rules["tid"], s.rules["tmatch"], s.players[w]))
                else:
                    paid = self.db.friendly_prize(s.players[w], quests.today(), world.FRIENDLY_WIN_PRIZE, world.FRIENDLY_DAILY_CAP)
                    lost = 0
                    if paid:
                        lost = min(world.FRIENDLY_LOSS, self.db.money(s.players[1 - w]))
                        self.db.add_money(s.players[1 - w], -lost)
                    res.append(f"💰 {self._who(s, w)} wins ${paid:,}! {self._who(s, 1 - w)} pays ${lost:,}." if paid else
                               f"(Daily friendly-battle prize limit of {world.FRIENDLY_DAILY_CAP} reached — no prize this time.)")
            else:
                res.append("🤝 It's a draw!" + (" The match will be replayed." if s.rules.get("tid") else ""))
                if s.rules.get("tid"):
                    self.tourney_results.append((s.rules["tid"], s.rules["tmatch"], None))
        elif ran:
            await self._xp_and_save(s, award=False)
        lines = ["🏁 BATTLE OVER", DIV] + [f"• {x}" for x in log]
        if res:
            lines += ["", "📋 RESULT"] + [f"• {x}" for x in res]
        text = "\n".join(lines)[:3900]
        outs = [Out(u, text, None, img=self.image_spec(s, i)) for i, u in enumerate(s.players) if u is not None]
        if s.kind == "pvp":
            outs = [Out(s.players[0], text, None), Out(s.players[1], "🏁 Battle over — see the result below.", None),
                    Out("chat", text, None, new=True)]
        self._drop(sid)
        return outs
