"""Pokémon data providers: live PokeAPI (disk-cached) and a deterministic offline mock."""
from __future__ import annotations

import asyncio
import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import config

STAT_KEYS = {"hp": "hp", "attack": "atk", "defense": "def", "special-attack": "spa",
             "special-defense": "spd", "speed": "spe", "accuracy": "acc", "evasion": "eva"}
TYPES = ["normal", "fire", "water", "electric", "grass", "ice", "fighting", "poison", "ground",
         "flying", "psychic", "bug", "rock", "ghost", "dragon", "dark", "steel", "fairy"]


@dataclass
class Move:
    name: str
    type: str = "normal"
    category: str = "physical"          # physical | special | status
    power: int = 0
    accuracy: int = 100                 # 0 = never misses
    pp: int = 20
    priority: int = 0
    ailment: str = "none"
    ailment_chance: int = 0
    meta: str = "damage"                # PokeAPI move meta category
    crit_rate: int = 0
    drain: int = 0                      # +% of damage healed, -% recoil
    healing: int = 0                    # % of max HP healed (status heal moves)
    flinch_chance: int = 0
    min_hits: int = 1
    max_hits: int = 1
    stat_changes: List[Tuple[str, int]] = field(default_factory=list)
    stat_chance: int = 0                # 0 = always (for status moves) / guaranteed secondary
    target: str = "selected-pokemon"


@dataclass
class Form:
    id: int
    name: str
    species_id: int
    types: List[str]
    stats: Dict[str, int]
    abilities: List[Tuple[str, bool]]   # (ability, is_hidden)
    base_exp: int
    learnset: List[Tuple[str, int]]     # (move, level) level-up moves
    ev_yield: Dict[str, int] = field(default_factory=dict)   # EVs given when this Pokémon is defeated
    height: int = 0                                          # decimetres
    weight: int = 0                                          # hectograms
    tm_moves: List[str] = field(default_factory=list)        # learnable by TM / machine
    tutor_moves: List[str] = field(default_factory=list)     # learnable from a move tutor


STONES = ("fire-stone", "water-stone", "thunder-stone", "leaf-stone", "ice-stone", "moon-stone", "sun-stone",
          "shiny-stone", "dusk-stone", "dawn-stone")


@dataclass
class Evo:
    to: int
    kind: str            # level | stone | friendship | trade
    level: int = 0       # level evolutions
    stone: str = ""      # stone evolutions ("" = pick the closest stone from the target's type)
    time: str = ""       # day | night | "" (friendship evolutions such as Espeon / Umbreon)


@dataclass
class Species:
    id: int
    name: str
    capture_rate: int
    legendary: bool
    evolutions: List[Tuple[int, int]]   # level evolutions only: (species id, min level)
    mega_forms: List[str]
    evos: List[Evo] = field(default_factory=list)       # every evolution method
    evolves_from: int = 0                                # pre-evolution species id (0 = base form)
    baby: bool = False
    flavor: str = ""
    genus: str = ""
    varieties: List[str] = field(default_factory=list)       # alternate forms (regional, mega, ...)


def _cap(d, cls):
    return cls(**d)


# ============================================================================ PokeAPI
class PokeAPIProvider:
    def __init__(self):
        self._sem = asyncio.Semaphore(6)
        self._mem: Dict[str, object] = {}
        self._types: Dict[str, List[int]] = {}

    # --- low level
    def _fetch_sync(self, url: str):
        key = hashlib.sha1(url.encode()).hexdigest()
        path = config.CACHE_DIR / f"{key}.json"
        if path.exists():
            return json.loads(path.read_text())
        last = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "pokebot-telegram/1.0"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = json.loads(r.read().decode())
                path.write_text(json.dumps(data))
                return data
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise KeyError(url)
                last = e
            except Exception as e:  # network hiccup
                last = e
        raise RuntimeError(f"PokeAPI request failed: {url}: {last}")

    async def _get(self, url: str):
        async with self._sem:
            return await asyncio.to_thread(self._fetch_sync, url)

    def _u(self, path: str) -> str:
        return f"{config.POKEAPI_BASE}/{path}"

    # --- public
    async def get_species(self, sid: int) -> Species:
        k = f"s{sid}"
        if k in self._mem:
            return self._mem[k]
        sp = await self._get(self._u(f"pokemon-species/{sid}"))
        chain = await self._get(sp["evolution_chain"]["url"])
        evos: List[Evo] = []

        def classify(nid: int, det: dict) -> Optional[Evo]:
            trig = det.get("trigger", {}).get("name")
            if trig == "use-item":
                item = (det.get("item") or {}).get("name", "")
                return Evo(nid, "stone", stone=item if item in STONES else "")
            if trig == "trade":
                return Evo(nid, "trade")
            if trig == "level-up":
                if det.get("min_happiness"):
                    return Evo(nid, "friendship", time=det.get("time_of_day") or "")
                if det.get("min_level"):
                    return Evo(nid, "level", level=det["min_level"])
                return Evo(nid, "stone")          # location / move / other conditions become stone evolutions
            if trig == "shed":
                return None
            return Evo(nid, "stone")

        def walk(node):
            if node["species"]["name"] == sp["name"]:
                for nxt in node["evolves_to"]:
                    nid = int(nxt["species"]["url"].rstrip("/").split("/")[-1])
                    for det in nxt["evolution_details"]:
                        e = classify(nid, det)
                        if e:
                            evos.append(e)
                            break
                return True
            return any(walk(n) for n in node["evolves_to"])

        walk(chain["chain"])
        frm = sp.get("evolves_from_species")
        from_id = int(frm["url"].rstrip("/").split("/")[-1]) if frm else 0
        megas = [v["pokemon"]["name"] for v in sp["varieties"] if "-mega" in v["pokemon"]["name"]]
        en = [f["flavor_text"] for f in sp.get("flavor_text_entries", []) if f["language"]["name"] == "en"]
        flavor = " ".join(en[-1].replace("\n", " ").replace("\f", " ").split()) if en else ""
        genus = next((g["genus"] for g in sp.get("genera", []) if g["language"]["name"] == "en"), "")
        obj = Species(sid, sp["name"], sp["capture_rate"], bool(sp["is_legendary"] or sp["is_mythical"]),
                      [(e.to, e.level) for e in evos if e.kind == "level"], megas, evos, from_id, bool(sp.get("is_baby")),
                      flavor, genus, [v["pokemon"]["name"] for v in sp["varieties"] if not v["is_default"]])
        self._mem[k] = obj
        return obj

    async def get_form(self, key) -> Form:
        k = f"f{key}"
        if k in self._mem:
            return self._mem[k]
        d = await self._get(self._u(f"pokemon/{key}"))
        stats = {STAT_KEYS[s["stat"]["name"]]: s["base_stat"] for s in d["stats"]}
        abilities = [(a["ability"]["name"], a["is_hidden"]) for a in d["abilities"]]
        learn: Dict[str, int] = {}
        for m in d["moves"]:
            lv = [vg["level_learned_at"] for vg in m["version_group_details"]
                  if vg["move_learn_method"]["name"] == "level-up"]
            if lv:
                learn[m["move"]["name"]] = min(lv)
        tm, tutor = set(), set()
        for m in d["moves"]:
            for vg in m["version_group_details"]:
                meth = vg["move_learn_method"]["name"]
                if meth == "machine":
                    tm.add(m["move"]["name"])
                elif meth == "tutor":
                    tutor.add(m["move"]["name"])
        species_id = int(d["species"]["url"].rstrip("/").split("/")[-1])
        obj = Form(d["id"], d["name"], species_id, [t["type"]["name"] for t in sorted(d["types"], key=lambda t: t["slot"])],
                   stats, abilities, d.get("base_experience") or 60, sorted(learn.items(), key=lambda x: x[1]),
                   {STAT_KEYS[x["stat"]["name"]]: x["effort"] for x in d["stats"] if x.get("effort")},
                   d.get("height") or 0, d.get("weight") or 0, sorted(tm), sorted(tutor - tm))
        self._mem[k] = obj
        return obj

    async def get_move(self, name: str) -> Move:
        k = f"m{name}"
        if k in self._mem:
            return self._mem[k]
        d = await self._get(self._u(f"move/{name}"))
        meta = d.get("meta") or {}
        sc = [(STAT_KEYS[s["stat"]["name"]], s["change"]) for s in d.get("stat_changes", [])]
        obj = Move(
            name=d["name"], type=d["type"]["name"], category=d["damage_class"]["name"],
            power=d.get("power") or 0, accuracy=d.get("accuracy") or 0, pp=d.get("pp") or 10,
            priority=d.get("priority", 0), ailment=(meta.get("ailment") or {}).get("name", "none"),
            ailment_chance=meta.get("ailment_chance", 0), meta=(meta.get("category") or {}).get("name", "damage"),
            crit_rate=meta.get("crit_rate", 0), drain=meta.get("drain", 0), healing=meta.get("healing", 0),
            flinch_chance=meta.get("flinch_chance", 0), min_hits=meta.get("min_hits") or 1,
            max_hits=meta.get("max_hits") or 1, stat_changes=sc, stat_chance=meta.get("stat_chance", 0),
            target=d["target"]["name"])
        self._mem[k] = obj
        return obj

    async def species_of_type(self, t: str) -> List[int]:
        if t not in self._types:
            d = await self._get(self._u(f"type/{t}"))
            ids = []
            for p in d["pokemon"]:
                pid = int(p["pokemon"]["url"].rstrip("/").split("/")[-1])
                if pid <= 1025:
                    ids.append(pid)
            self._types[t] = sorted(set(ids))
        return self._types[t]


# ============================================================================ Mock (offline)
_MOCK_MOVES: Dict[str, Move] = {}


def _build_mock_moves():
    for t in TYPES:
        _MOCK_MOVES[f"{t}-strike"] = Move(f"{t}-strike", t, "physical", 60, 100, 25)
        _MOCK_MOVES[f"{t}-beam"] = Move(f"{t}-beam", t, "special", 90, 100, 10)
        _MOCK_MOVES[f"{t}-jab"] = Move(f"{t}-jab", t, "physical", 40, 100, 30)
        _MOCK_MOVES[f"{t}-blast"] = Move(f"{t}-blast", t, "special", 110, 85, 5)
    M = _MOCK_MOVES
    M["tackle"] = Move("tackle", "normal", "physical", 40, 100, 35)
    M["quick-attack"] = Move("quick-attack", "normal", "physical", 40, 100, 30, priority=1)
    M["thunder-wave"] = Move("thunder-wave", "electric", "status", 0, 90, 20, ailment="paralysis", meta="ailment")
    M["toxic"] = Move("toxic", "poison", "status", 0, 90, 10, ailment="poison", meta="ailment")
    M["will-o-wisp"] = Move("will-o-wisp", "fire", "status", 0, 85, 15, ailment="burn", meta="ailment")
    M["sleep-powder"] = Move("sleep-powder", "grass", "status", 0, 75, 15, ailment="sleep", meta="ailment")
    M["swords-dance"] = Move("swords-dance", "normal", "status", 0, 0, 20, meta="net-good-stats",
                             stat_changes=[("atk", 2)], target="user")
    M["growl"] = Move("growl", "normal", "status", 0, 100, 40, meta="net-good-stats",
                      stat_changes=[("atk", -1)], target="selected-pokemon")
    M["recover"] = Move("recover", "normal", "status", 0, 0, 10, meta="heal", healing=50, target="user")
    M["giga-drain"] = Move("giga-drain", "grass", "special", 75, 100, 10, meta="damage+heal", drain=50)
    M["double-slap"] = Move("double-slap", "normal", "physical", 15, 85, 10, min_hits=2, max_hits=5)
    M["flamethrower"] = Move("flamethrower", "fire", "special", 90, 100, 15, meta="damage+ailment",
                             ailment="burn", ailment_chance=10)
    M["rain-dance"] = Move("rain-dance", "water", "status", 0, 0, 5, meta="whole-field-effect")
    M["protect"] = Move("protect", "normal", "status", 0, 0, 10, priority=4, meta="unique")
    M["close-combat"] = Move("close-combat", "fighting", "physical", 120, 100, 5, meta="damage+raise",
                             stat_changes=[("def", -1), ("spd", -1)], stat_chance=0)
    M["extreme-speed"] = Move("extreme-speed", "normal", "physical", 80, 100, 5, priority=2)
    M["fissure"] = Move("fissure", "ground", "physical", 0, 30, 5, meta="ohko")


_build_mock_moves()
_MOCK_ABILITIES = ["overgrow", "blaze", "torrent", "intimidate", "levitate", "static", "sturdy", "huge-power",
                   "guts", "technician", "adaptability", "thick-fat", "speed-boost", "multiscale", "drizzle",
                   "drought", "flame-body", "rough-skin", "inner-focus", "pressure"]


_REAL_MOVES = {
    "normal": ("tackle", "bodyslam", "swift", "hyperbeam"), "fire": ("ember", "flamewheel", "flamethrower", "fireblast"),
    "water": ("watergun", "bubblebeam", "surf", "hydropump"), "grass": ("vinewhip", "razorleaf", "energyball", "solarbeam"),
    "electric": ("thundershock", "spark", "thunderbolt", "thunder"), "ice": ("powdersnow", "icepunch", "icebeam", "blizzard"),
    "fighting": ("karatechop", "brickbreak", "aurasphere", "closecombat"), "poison": ("poisonsting", "sludge", "sludgebomb", "gunkshot"),
    "ground": ("mudslap", "dig", "earthpower", "earthquake"), "flying": ("gust", "wingattack", "aerialace", "airslash"),
    "psychic": ("confusion", "psybeam", "psyshock", "psychic"), "bug": ("bugbite", "signalbeam", "xscissor", "bugbuzz"),
    "rock": ("rockthrow", "rockslide", "powergem", "stoneedge"), "ghost": ("lick", "shadowsneak", "shadowball", "shadowclaw"),
    "dragon": ("dragonbreath", "dragonclaw", "dragonpulse", "outrage"), "dark": ("bite", "crunch", "darkpulse", "nightslash"),
    "steel": ("metalclaw", "ironhead", "flashcannon", "meteormash"), "fairy": ("fairywind", "dazzlinggleam", "moonblast", "playrough"),
}


def _h(*parts) -> int:
    return int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest(), 16)


class MockProvider:
    """Deterministic fake dex covering ids 1..1025 so every region/gym can be exercised offline."""

    @staticmethod
    def _evos(sid: int) -> List[Evo]:
        if sid % 3 == 1:
            return [Evo(sid + 1, "level", 16)]
        if sid % 3 == 2:
            return [Evo(sid + 1, "level", 36)]
        if sid % 2 == 0:
            return [Evo(sid + 1, "stone", stone="fire-stone")]
        if sid % 9 == 3:
            return [Evo(sid + 1, "friendship")]
        return []

    async def get_species(self, sid: int) -> Species:
        evos = self._evos(sid)
        frm = sid - 1 if sid > 1 and any(e.to == sid for e in self._evos(sid - 1)) else 0
        megas = [f"mon{sid}-mega"] if sid % 6 == 0 else []
        return Species(sid, f"mon{sid}", 45 + _h("cr", sid) % 211, sid % 50 == 0,
                       [(e.to, e.level) for e in evos if e.kind == "level"], megas, evos, frm, False,
                       f"A mock Pokémon numbered {sid}.", "Mock Pokémon",
                       [f"mon{sid}-alola", f"mon{sid}-galar"] if sid % 10 == 5 else [])

    def _types_of(self, sid: int):
        t1 = TYPES[_h("t1", sid) % 18]
        t2 = TYPES[_h("t2", sid) % 18]
        return [t1] if (t2 == t1 or _h("dual", sid) % 2) else [t1, t2]

    async def get_form(self, key) -> Form:
        mega = isinstance(key, str) and key.endswith("-mega")
        skey = str(key)
        if skey.startswith("mon") or skey.isdigit():
            sid = int(skey.replace("mon", "").split("-")[0])
        else:
            sid = 5000 + _h("form", skey) % 1000          # alternate forms with real names: a stable fake id
        regional = skey.endswith(("-alola", "-galar"))
        stats = {s: 35 + _h(s, sid) % 80 for s in ("hp", "atk", "def", "spa", "spd", "spe")}
        types = self._types_of(sid) if not regional else ["dark"]
        if mega:
            stats = {k: v + 25 for k, v in stats.items()}
        abil = [(_MOCK_ABILITIES[_h("a", sid) % len(_MOCK_ABILITIES)], False),
                (_MOCK_ABILITIES[_h("h", sid) % len(_MOCK_ABILITIES)], True)]
        if mega:
            abil = [("huge-power", False)]
        ls = [("tackle", 1)]
        for i, t in enumerate(types):
            a, b, c, d = _REAL_MOVES[t]
            ls += [(a, 1 + i), (b, 8 + i * 2), (c, 14 + i * 2), (d, 30 + i * 2)]
        extras = ["quick-attack", "thunder-wave", "toxic", "will-o-wisp", "sleep-powder", "swords-dance", "growl",
                  "recover", "giga-drain", "double-slap", "flamethrower", "rain-dance", "protect", "close-combat",
                  "extreme-speed", "fissure"]
        for j in range(3):
            ls.append((extras[_h("x", sid, j) % len(extras)], 4 + j * 9))
        d = {}
        for m, lv in ls:
            d.setdefault(m, lv)
        ev = {("hp", "atk", "def", "spa", "spd", "spe")[_h("ev", sid) % 6]: 1 + _h("evn", sid) % 2}
        extras_tm = ["swords-dance", "toxic", "protect", "rain-dance", "earthquake", "thunderbolt", "shadow-ball", "ice-beam"]
        tm = sorted({extras_tm[_h("tm", sid, j) % len(extras_tm)] for j in range(4)})
        return Form(sid, skey, sid, types, stats, abil, 50 + _h("be", sid) % 200, sorted(d.items(), key=lambda x: x[1]), ev,
                    5 + _h("ht", sid) % 30, 50 + _h("wt", sid) % 900, tm, ["knock-off", "draco-meteor"])

    async def get_move(self, name: str) -> Move:
        return _MOCK_MOVES.get(name) or Move(name, "normal", "physical", 60, 100, 15)

    async def species_of_type(self, t: str) -> List[int]:
        return [i for i in range(1, 1026) if t in self._types_of(i)]


_provider = None


def get_provider():
    global _provider
    if _provider is None:
        _provider = MockProvider() if config.DATA_PROVIDER == "mock" else PokeAPIProvider()
    return _provider


def set_provider(p):
    global _provider
    _provider = p
