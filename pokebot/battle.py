"""Turn-based battle engine: damage, status, stages, weather, abilities, Mega/Z/Dynamax/Tera, catching, AI.

Pure Python (no I/O). All Move/Form data must already be resolved before a battle starts.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .data import Form, Move

# --------------------------------------------------------------------------- type chart
_SE = {
    "normal": [], "fire": ["grass", "ice", "bug", "steel"], "water": ["fire", "ground", "rock"],
    "electric": ["water", "flying"], "grass": ["water", "ground", "rock"],
    "ice": ["grass", "ground", "flying", "dragon"], "fighting": ["normal", "ice", "rock", "dark", "steel"],
    "poison": ["grass", "fairy"], "ground": ["fire", "electric", "poison", "rock", "steel"],
    "flying": ["grass", "fighting", "bug"], "psychic": ["fighting", "poison"],
    "bug": ["grass", "psychic", "dark"], "rock": ["fire", "ice", "flying", "bug"],
    "ghost": ["psychic", "ghost"], "dragon": ["dragon"], "dark": ["psychic", "ghost"],
    "steel": ["ice", "rock", "fairy"], "fairy": ["fighting", "dragon", "dark"]}
_NVE = {
    "normal": ["rock", "steel"], "fire": ["fire", "water", "rock", "dragon"], "water": ["water", "grass", "dragon"],
    "electric": ["electric", "grass", "dragon"],
    "grass": ["fire", "grass", "poison", "flying", "bug", "dragon", "steel"],
    "ice": ["fire", "water", "ice", "steel"], "fighting": ["poison", "flying", "psychic", "bug", "fairy"],
    "poison": ["poison", "ground", "rock", "ghost"], "ground": ["grass", "bug"],
    "flying": ["electric", "rock", "steel"], "psychic": ["psychic", "steel"],
    "bug": ["fire", "fighting", "poison", "flying", "ghost", "steel", "fairy"],
    "rock": ["fighting", "ground", "steel"], "ghost": ["dark"], "dragon": ["steel"],
    "dark": ["fighting", "dark", "fairy"], "steel": ["fire", "water", "electric", "steel"],
    "fairy": ["fire", "poison", "steel"]}
_IMM = {"normal": ["ghost"], "electric": ["ground"], "fighting": ["ghost"], "poison": ["steel"],
        "ground": ["flying"], "psychic": ["dark"], "ghost": ["normal"], "dragon": ["fairy"]}


def type_mult(atk_type: str, def_types: List[str]) -> float:
    m = 1.0
    for d in def_types:
        if d in _IMM.get(atk_type, ()):
            return 0.0
        if d in _SE.get(atk_type, ()):
            m *= 2
        elif d in _NVE.get(atk_type, ()):
            m *= 0.5
    return m


# --------------------------------------------------------------------------- gimmick tables
Z_NAMES = {"normal": "Breakneck Blitz", "fighting": "All-Out Pummeling", "flying": "Supersonic Skystrike",
           "poison": "Acid Downpour", "ground": "Tectonic Rage", "rock": "Continental Crush", "bug": "Savage Spin-Out",
           "ghost": "Never-Ending Nightmare", "steel": "Corkscrew Crash", "fire": "Inferno Overdrive",
           "water": "Hydro Vortex", "grass": "Bloom Doom", "electric": "Gigavolt Havoc", "psychic": "Shattered Psyche",
           "ice": "Subzero Slammer", "dragon": "Devastating Drake", "dark": "Black Hole Eclipse",
           "fairy": "Twinkle Tackle"}
MAX_NAMES = {"normal": "Max Strike", "fire": "Max Flare", "water": "Max Geyser", "electric": "Max Lightning",
             "grass": "Max Overgrowth", "ice": "Max Hailstorm", "fighting": "Max Knuckle", "poison": "Max Ooze",
             "ground": "Max Quake", "flying": "Max Airstream", "psychic": "Max Mindstorm", "bug": "Max Flutterby",
             "rock": "Max Rockfall", "ghost": "Max Phantasm", "dragon": "Max Wyrmwind", "dark": "Max Darkness",
             "steel": "Max Steelspike", "fairy": "Max Starfall"}
# (weather set) / (stat, delta, on_user)
MAX_EFFECT = {"fire": ("weather", "sun"), "water": ("weather", "rain"), "rock": ("weather", "sand"),
              "ice": ("weather", "hail"), "fighting": ("stat", "atk", 1, True), "poison": ("stat", "spa", 1, True),
              "flying": ("stat", "spe", 1, True), "steel": ("stat", "def", 1, True), "ground": ("stat", "spd", 1, True),
              "bug": ("stat", "spa", -1, False), "ghost": ("stat", "def", -1, False),
              "dragon": ("stat", "atk", -1, False), "dark": ("stat", "spd", -1, False),
              "normal": ("stat", "spe", -1, False)}


def z_power(bp: int) -> int:
    for lim, p in [(55, 100), (65, 120), (75, 140), (85, 160), (95, 175), (100, 180), (110, 185), (125, 190), (130, 195)]:
        if bp <= lim:
            return p
    return 200


def max_power(bp: int, mtype: str) -> int:
    low = mtype in ("fighting", "poison")
    for lim, hi, lo in [(40, 90, 70), (50, 100, 75), (60, 110, 80), (70, 120, 85), (100, 130, 90), (140, 140, 95)]:
        if bp <= lim:
            return lo if low else hi
    return 100 if low else 150


FIXED_DAMAGE = {"seismic-toss": "level", "night-shade": "level", "dragon-rage": 40, "sonic-boom": 20}
WEATHER_MOVES = {"sunny-day": "sun", "rain-dance": "rain", "sandstorm": "sand", "hail": "hail", "snowscape": "hail"}
STRUGGLE = Move("struggle", "normal", "physical", 50, 0, 1, drain=-25)

# Abilities with battle effects (all others are displayed but inert).
IMPLEMENTED_ABILITIES = {
    "intimidate", "drizzle", "drought", "sand-stream", "snow-warning", "huge-power", "pure-power", "guts", "technician",
    "adaptability", "blaze", "torrent", "overgrow", "swarm", "sniper", "tinted-lens", "sheer-force", "hustle",
    "mold-breaker", "teravolt", "turboblaze", "thick-fat", "multiscale", "filter", "solid-rock", "prism-armor",
    "marvel-scale", "sturdy", "wonder-guard", "levitate", "flash-fire", "volt-absorb", "water-absorb", "storm-drain",
    "lightning-rod", "sap-sipper", "motor-drive", "dry-skin", "magic-guard", "static", "poison-point", "flame-body",
    "rough-skin", "iron-barbs", "speed-boost", "shed-skin", "natural-cure", "moxie", "defiant", "competitive",
    "contrary", "simple", "clear-body", "white-smoke", "hyper-cutter", "inner-focus", "immunity", "limber",
    "insomnia", "vital-spirit", "water-veil", "magma-armor", "swift-swim", "chlorophyll", "sand-rush", "slush-rush",
    "prankster", "no-guard", "scrappy"}

STAGE_NAMES = {"atk": "Attack", "def": "Defense", "spa": "Sp. Atk", "spd": "Sp. Def", "spe": "Speed",
               "acc": "accuracy", "eva": "evasiveness"}
STATUS_NAMES = {"brn": "burned", "par": "paralyzed", "psn": "poisoned", "tox": "badly poisoned",
                "slp": "put to sleep", "frz": "frozen solid"}


# --------------------------------------------------------------------------- battle mon
NATURES = {  # name: (raised stat, lowered stat); neutral natures raise and lower nothing
    "Hardy": None, "Docile": None, "Serious": None, "Bashful": None, "Quirky": None,
    "Lonely": ("atk", "def"), "Brave": ("atk", "spe"), "Adamant": ("atk", "spa"), "Naughty": ("atk", "spd"),
    "Bold": ("def", "atk"), "Relaxed": ("def", "spe"), "Impish": ("def", "spa"), "Lax": ("def", "spd"),
    "Timid": ("spe", "atk"), "Hasty": ("spe", "def"), "Jolly": ("spe", "spa"), "Naive": ("spe", "spd"),
    "Modest": ("spa", "atk"), "Mild": ("spa", "def"), "Quiet": ("spa", "spe"), "Rash": ("spa", "spd"),
    "Calm": ("spd", "atk"), "Gentle": ("spd", "def"), "Sassy": ("spd", "spe"), "Careful": ("spd", "spa"),
}


def calc_stats(base: Dict[str, int], level: int, ivs: Dict[str, int], evs: Optional[Dict[str, int]] = None,
               nature: Optional[str] = None) -> Dict[str, int]:
    evs = evs or {}
    out = {"hp": (2 * base["hp"] + ivs.get("hp", 15) + evs.get("hp", 0) // 4) * level // 100 + level + 10}
    for s in ("atk", "def", "spa", "spd", "spe"):
        out[s] = (2 * base[s] + ivs.get(s, 15) + evs.get(s, 0) // 4) * level // 100 + 5
        n = NATURES.get(nature or "Hardy")
        if n:
            out[s] = out[s] * 110 // 100 if n[0] == s else out[s] * 90 // 100 if n[1] == s else out[s]
    return out


@dataclass
class BMove:
    move: Move
    pp: int

    @property
    def name(self):
        return self.move.name.replace("-", " ").title()


class BMon:
    def __init__(self, *, name: str, species_id: int, level: int, form: Form, ivs: Dict[str, int],
                 moves: List[Move], ability: str, tera_type: Optional[str] = None, hp: Optional[int] = None,
                 mega_form: Optional[Form] = None, uid: Optional[int] = None, status: Optional[str] = None):
        self.uid = uid
        self.name = name
        self.species_id = species_id
        self.level = level
        self.form = form
        self.ivs = ivs
        self.base_types = list(form.types)
        self.types = list(form.types)
        self.stats = calc_stats(form.stats, level, ivs)
        self.max_hp = self.stats["hp"]
        self.hp = self.max_hp if hp is None else max(0, min(hp, self.max_hp))
        self.moves = [BMove(m, m.pp) for m in moves[:4]] or [BMove(STRUGGLE, 1)]
        self.ability = ability
        self.tera_type = tera_type or self.base_types[0]
        self.mega_form = mega_form
        self.status = status
        self.reset_volatile()
        self.tera = False
        self.mega = False
        self.dyn_turns = 0
        self.flash_fire = False
        self.toxic_n = 0
        self.sleep_turns = 0

    def reset_volatile(self):
        self.stages = {k: 0 for k in ("atk", "def", "spa", "spd", "spe", "acc", "eva")}
        self.confused = 0
        self.flinch = False
        self.protected = False
        self.last_protect = False
        self.moved = False

    @property
    def fainted(self):
        return self.hp <= 0

    @property
    def dynamaxed(self):
        return self.dyn_turns > 0

    def hp_pct(self) -> float:
        return self.hp / self.max_hp if self.max_hp else 0


class Side:
    def __init__(self, name: str, team: List[BMon], gimmicks=(), ai: bool = False, ai_gimmick: Optional[str] = None,
                 user_id: Optional[int] = None):
        self.name = name
        self.team = team
        self.active = 0
        self.gimmicks = set(gimmicks)
        self.used_gimmick: Optional[str] = None
        self.ai = ai
        self.ai_gimmick = ai_gimmick
        self.user_id = user_id

    @property
    def mon(self) -> BMon:
        return self.team[self.active]

    def alive_idx(self):
        return [i for i, m in enumerate(self.team) if m.hp > 0]


Action = Tuple  # ("move", idx, gimmick|None) or ("switch", idx)


# --------------------------------------------------------------------------- the battle
class Battle:
    def __init__(self, a: Side, b: Side, rng: Optional[random.Random] = None):
        self.sides = [a, b]
        self.rng = rng or random.Random()
        self.weather: Optional[str] = None
        self.weather_turns = 0
        self.turn = 0
        self.log: List[str] = []
        self.winner: Optional[int] = None
        self.pending: List[int] = []   # sides that must choose a replacement

    # ---- helpers
    def L(self, s: str):
        self.log.append(s)

    def foe(self, i: int) -> int:
        return 1 - i

    def mon(self, i: int) -> BMon:
        return self.sides[i].mon

    def _abil(self, m: BMon, attacker: Optional[BMon] = None) -> str:
        """Defender's ability, ignored if attacker has Mold Breaker."""
        if attacker is not None and attacker.ability in ("mold-breaker", "teravolt", "turboblaze"):
            return ""
        return m.ability

    def stage_mult(self, s: int, acc: bool = False) -> float:
        if acc:
            return (3 + s) / 3 if s >= 0 else 3 / (3 - s)
        return (2 + s) / 2 if s >= 0 else 2 / (2 - s)

    def speed(self, m: BMon) -> float:
        v = m.stats["spe"] * self.stage_mult(m.stages["spe"])
        if m.status == "par":
            v *= 0.5
        w = self.weather
        if (m.ability == "swift-swim" and w == "rain") or (m.ability == "chlorophyll" and w == "sun") or \
           (m.ability == "sand-rush" and w == "sand") or (m.ability == "slush-rush" and w == "hail"):
            v *= 2
        return v

    def eff_stat(self, m: BMon, stat: str, ignore_stage: bool = False) -> float:
        v = m.stats[stat] * (1 if ignore_stage else self.stage_mult(m.stages[stat]))
        ab = m.ability
        if stat == "atk":
            if ab in ("huge-power", "pure-power"):
                v *= 2
            elif ab == "guts" and m.status:
                v *= 1.5
            elif ab == "hustle":
                v *= 1.5
        elif stat == "def" and ab == "marvel-scale" and m.status:
            v *= 1.5
        elif stat == "spd" and self.weather == "sand" and "rock" in m.types:
            v *= 1.5
        elif stat == "def" and self.weather == "hail" and "ice" in m.types:
            v *= 1.5
        return max(1.0, v)

    # ---- start
    def start(self) -> List[str]:
        self.log = []
        order = sorted((0, 1), key=lambda i: -self.speed(self.mon(i)))
        for i in order:
            self.L(f"{self.sides[i].name} sends out {self.mon(i).name}! (Lv{self.mon(i).level})")
            self.on_enter(i)
        return self.log

    def on_enter(self, i: int):
        m, f = self.mon(i), self.mon(self.foe(i))
        ab = m.ability
        w = {"drizzle": "rain", "drought": "sun", "sand-stream": "sand", "snow-warning": "hail"}.get(ab)
        if w:
            self.set_weather(w, f"{m.name}'s {ab.replace('-', ' ').title()}")
        if ab == "intimidate" and not f.fainted:
            self.L(f"{m.name}'s Intimidate!")
            if f.ability in ("inner-focus", "scrappy", "clear-body", "white-smoke", "hyper-cutter"):
                self.L(f"{f.name}'s {f.ability.replace('-', ' ').title()} prevents the drop!")
            else:
                self.change_stage(f, "atk", -1, m)

    def set_weather(self, w: str, src: str = ""):
        if self.weather == w:
            return
        self.weather, self.weather_turns = w, 5
        msg = {"sun": "The sunlight turned harsh!", "rain": "It started to rain!", "sand": "A sandstorm kicked up!",
               "hail": "It started to hail!"}[w]
        self.L(f"{src + ': ' if src else ''}{msg}")

    # ---- stat stages / status
    def change_stage(self, m: BMon, stat: str, delta: int, source: Optional[BMon] = None) -> bool:
        if m.ability == "contrary":
            delta = -delta
        elif m.ability == "simple":
            delta *= 2
        if delta < 0 and source is not None and source is not m and m.ability in ("clear-body", "white-smoke"):
            self.L(f"{m.name}'s {m.ability.replace('-', ' ').title()} prevents stat loss!")
            return False
        old = m.stages[stat]
        new = max(-6, min(6, old + delta))
        if new == old:
            self.L(f"{m.name}'s {STAGE_NAMES[stat]} won't go any {'higher' if delta > 0 else 'lower'}!")
            return False
        m.stages[stat] = new
        d = abs(new - old)
        word = {1: "", 2: " sharply", 3: " drastically"}.get(d, " drastically")
        self.L(f"{m.name}'s {STAGE_NAMES[stat]}{word} {'rose' if new > old else 'fell'}!")
        if delta < 0 and source is not None and source is not m:
            if m.ability == "defiant":
                self.L(f"{m.name}'s Defiant!")
                self.change_stage(m, "atk", 2)
            elif m.ability == "competitive":
                self.L(f"{m.name}'s Competitive!")
                self.change_stage(m, "spa", 2)
        return True

    def inflict(self, target: BMon, status: str, source: Optional[BMon] = None, quiet: bool = False) -> bool:
        if target.fainted or target.status:
            return False
        ab = self._abil(target, source)
        t = target.types
        block = ((status in ("psn", "tox") and ("poison" in t or "steel" in t or ab == "immunity")) or
                 (status == "brn" and ("fire" in t or ab in ("water-veil",))) or
                 (status == "par" and ("electric" in t or ab == "limber")) or
                 (status == "frz" and ("ice" in t or ab == "magma-armor" or self.weather == "sun")) or
                 (status == "slp" and ab in ("insomnia", "vital-spirit")))
        if block:
            if not quiet:
                self.L(f"It doesn't affect {target.name}...")
            return False
        target.status = status
        if status == "slp":
            target.sleep_turns = self.rng.randint(1, 3)
        if status == "tox":
            target.toxic_n = 1
        self.L(f"{target.name} was {STATUS_NAMES[status]}!")
        return True

    # ---- damage
    def calc_damage(self, att: BMon, de: BMon, move: Move, *, power: int, mtype: str, category: str,
                    crit: bool = False, roll: Optional[float] = None, bypass: bool = False) -> int:
        if power <= 0:
            return 0
        phys = category == "physical"
        a_stat, d_stat = ("atk", "def") if phys else ("spa", "spd")
        a = self.eff_stat(att, a_stat, ignore_stage=False)
        if crit and att.stages[a_stat] < 0:
            a = self.eff_stat(att, a_stat, ignore_stage=True)
        d = self.eff_stat(de, d_stat, ignore_stage=False)
        if crit and de.stages[d_stat] > 0:
            d = self.eff_stat(de, d_stat, ignore_stage=True)
        aab, dab = att.ability, self._abil(de, att)
        bp = power
        if aab == "technician" and power <= 60:
            bp = int(bp * 1.5)
        if aab == "sheer-force" and (move.ailment_chance or move.flinch_chance or move.stat_chance):
            bp = int(bp * 1.3)
        hp3 = att.hp <= att.max_hp / 3
        if hp3 and ((aab == "blaze" and mtype == "fire") or (aab == "torrent" and mtype == "water") or
                    (aab == "overgrow" and mtype == "grass") or (aab == "swarm" and mtype == "bug")):
            bp = int(bp * 1.5)
        if att.flash_fire and mtype == "fire":
            bp = int(bp * 1.5)
        base = ((2 * att.level // 5 + 2) * bp * a / d) // 50 + 2
        # weather
        if self.weather == "rain":
            base *= 1.5 if mtype == "water" else (0.5 if mtype == "fire" else 1)
        elif self.weather == "sun":
            base *= 1.5 if mtype == "fire" else (0.5 if mtype == "water" else 1)
        if crit:
            base *= 2.25 if aab == "sniper" else 1.5
        base *= roll if roll is not None else self.rng.uniform(0.85, 1.0)
        # STAB
        stab = 1.0
        if mtype in att.base_types or (att.tera and mtype == att.tera_type):
            stab = 1.5
            if att.tera and mtype == att.tera_type and mtype in att.base_types:
                stab = 2.0
            if aab == "adaptability":
                stab = 2.25 if stab == 2.0 else 2.0
        base *= stab
        eff = type_mult(mtype, de.types)
        base *= eff
        if eff > 1 and dab in ("filter", "solid-rock", "prism-armor"):
            base *= 0.75
        if eff < 1 and aab == "tinted-lens":
            base *= 2
        if dab == "thick-fat" and mtype in ("fire", "ice"):
            base *= 0.5
        if dab == "multiscale" and de.hp == de.max_hp:
            base *= 0.5
        if dab == "dry-skin" and mtype == "fire":
            base *= 1.25
        if phys and att.status == "brn" and aab != "guts":
            base *= 0.5
        dmg = int(base)
        if eff > 0 and dmg < 1:
            dmg = 1
        return dmg

    def effective_move(self, m: BMon, bm: BMove, gimmick: Optional[str]):
        """Return (Move-like dict) after applying Z / Max conversion."""
        mv = bm.move
        if gimmick == "z" and mv.category != "status":
            return {"name": Z_NAMES.get(mv.type, "Z-Move"), "type": mv.type, "category": mv.category,
                    "power": z_power(mv.power or 60), "acc": 0, "kind": "z", "move": mv}
        if m.dynamaxed:
            if mv.category == "status":
                return {"name": "Max Guard", "type": "normal", "category": "status", "power": 0, "acc": 0,
                        "kind": "maxguard", "move": mv}
            return {"name": MAX_NAMES.get(mv.type, "Max Strike"), "type": mv.type, "category": mv.category,
                    "power": max_power(mv.power or 60, mv.type), "acc": 0, "kind": "max", "move": mv}
        return {"name": bm.name, "type": mv.type, "category": mv.category, "power": mv.power, "acc": mv.accuracy,
                "kind": "normal", "move": mv}

    # ---- gimmicks
    def can_gimmick(self, i: int, g: str) -> bool:
        s, m = self.sides[i], self.sides[i].mon
        if g not in s.gimmicks or s.used_gimmick or m.fainted:
            return False
        if g == "mega":
            return m.mega_form is not None and not m.mega
        if g == "z":
            return any(bm.move.category != "status" for bm in m.moves)
        return True  # dynamax, tera

    def activate(self, i: int, g: str):
        s, m = self.sides[i], self.sides[i].mon
        s.used_gimmick = g
        if g == "mega":
            f = m.mega_form
            m.mega = True
            m.types = list(f.types)
            ns = calc_stats(f.stats, m.level, m.ivs)
            for k in ("atk", "def", "spa", "spd", "spe"):
                m.stats[k] = ns[k]
            nab = next((a for a, h in f.abilities if not h), m.ability)
            m.ability = nab
            m.name = m.name if m.name.startswith("Mega ") else f"Mega {m.name}"
            self.L(f"{s.name}'s {m.name.replace('Mega ', '')} Mega Evolved into {m.name}!")
        elif g == "tera":
            m.tera = True
            m.types = [m.tera_type]
            self.L(f"{s.name}'s {m.name} Terastallized into the {m.tera_type.title()} type!")
        elif g == "dynamax":
            m.dyn_turns = 3
            m.max_hp = int(m.max_hp * 1.5)
            m.hp = int(m.hp * 1.5)
            self.L(f"{s.name}'s {m.name} Dynamaxed! It towers over the field!")
        elif g == "z":
            self.L(f"{s.name} channels their Z-Power into {m.name}!")

    def end_dynamax(self, m: BMon):
        m.max_hp = m.stats["hp"]
        m.hp = max(1, math.ceil(m.hp / 1.5)) if m.hp > 0 else 0
        m.hp = min(m.hp, m.max_hp)
        self.L(f"{m.name} returned to normal size.")

    # ---- fainting
    def faint(self, i: int):
        m = self.mon(i)
        m.hp = 0
        if m.dynamaxed:
            m.dyn_turns = 0
            m.max_hp = m.stats["hp"]
        self.L(f"{m.name} fainted!")

    # ---- move execution
    def can_move(self, i: int) -> bool:
        m = self.mon(i)
        if m.flinch:
            self.L(f"{m.name} flinched and couldn't move!")
            return False
        if m.status == "slp":
            m.sleep_turns -= 1
            if m.sleep_turns <= 0:
                m.status = None
                self.L(f"{m.name} woke up!")
            else:
                self.L(f"{m.name} is fast asleep.")
                return False
        if m.status == "frz":
            if self.rng.random() < 0.2:
                m.status = None
                self.L(f"{m.name} thawed out!")
            else:
                self.L(f"{m.name} is frozen solid!")
                return False
        if m.confused:
            m.confused -= 1
            if m.confused == 0:
                self.L(f"{m.name} snapped out of its confusion!")
            else:
                self.L(f"{m.name} is confused!")
                if self.rng.random() < 1 / 3:
                    dmg = self.calc_damage(m, m, Move("confusion"), power=40, mtype="typeless", category="physical")
                    m.hp = max(0, m.hp - dmg)
                    self.L("It hurt itself in its confusion!")
                    if m.fainted:
                        self.faint(i)
                    return False
        if m.status == "par" and self.rng.random() < 0.25:
            self.L(f"{m.name} is paralyzed! It can't move!")
            return False
        return True

    def use_move(self, i: int, idx: int, gimmick: Optional[str]):
        s, m = self.sides[i], self.mon(i)
        fi = self.foe(i)
        t = self.mon(fi)
        m.moved = True
        if not self.can_move(i):
            return
        usable = [k for k, bm in enumerate(m.moves) if bm.pp > 0]
        if idx >= len(m.moves) or m.moves[idx].pp <= 0:
            bm = BMove(STRUGGLE, 1) if not usable else m.moves[usable[0]]
        else:
            bm = m.moves[idx]
        if bm.move is not STRUGGLE:
            bm.pp -= 1
        info = self.effective_move(m, bm, gimmick)
        mv: Move = info["move"]
        if info["kind"] == "z":
            self.L(f"{m.name} unleashed {info['name']}!")
        else:
            self.L(f"{m.name} used {info['name']}!")
        # --- Protect / Max Guard
        if info["kind"] == "maxguard" or mv.name in ("protect", "detect"):
            if m.last_protect and self.rng.random() > 1 / 3:
                self.L("But it failed!")
                m.last_protect = False
            else:
                m.protected = True
                m.last_protect = True
                self.L(f"{m.name} protected itself!")
            return
        m.last_protect = False
        if mv.name in WEATHER_MOVES:
            self.set_weather(WEATHER_MOVES[mv.name])
            return
        targets_self = mv.target in ("user", "user-and-allies", "users-field", "entire-field", "all-allies")
        # --- status moves
        if info["category"] == "status":
            self.status_move(i, mv, targets_self)
            return
        if t.fainted:
            self.L("But there was no target...")
            return
        if t.protected:
            self.L(f"{t.name} protected itself!")
            return
        # --- accuracy
        if mv.meta == "ohko":
            if t.level > m.level:
                self.L("It doesn't affect the target...")
                return
            if self.rng.random() * 100 >= 30 + m.level - t.level:
                self.L(f"{m.name}'s attack missed!")
                return
        elif info["acc"] and m.ability != "no-guard" and t.ability != "no-guard":
            acc = info["acc"] * self.stage_mult(m.stages["acc"] - t.stages["eva"], acc=True)
            if m.ability == "hustle" and info["category"] == "physical":
                acc *= 0.8
            if self.rng.random() * 100 >= acc:
                self.L(f"{m.name}'s attack missed!")
                return
        # --- immunities from abilities
        if self.ability_immune(m, t, info["type"], info["category"]):
            return
        eff = type_mult(info["type"], t.types)
        if self._abil(t, m) == "wonder-guard" and eff <= 1:
            self.L(f"{t.name}'s Wonder Guard protects it!")
            return
        if eff == 0:
            self.L(f"It doesn't affect {t.name}...")
            return
        if mv.meta == "ohko":
            t.hp = 0
            self.L("It's a one-hit KO!")
            self.faint(fi)
            return
        # --- hits
        hits = 1
        if info["kind"] == "normal" and mv.max_hits > 1:
            hits = self.rng.randint(mv.min_hits, mv.max_hits)
            if mv.min_hits == 2 and mv.max_hits == 5:
                hits = self.rng.choices([2, 3, 4, 5], [35, 35, 15, 15])[0]
        total = 0
        fixed = FIXED_DAMAGE.get(mv.name) if info["kind"] == "normal" else None
        power = info["power"] or (0 if fixed else 50)
        crit_stage = min(3, mv.crit_rate)
        crit_chance = [1 / 24, 1 / 8, 1 / 2, 1.0][crit_stage]
        did = 0
        crit_any = False
        for h in range(hits):
            if t.fainted:
                break
            crit = self.rng.random() < crit_chance
            if fixed:
                dmg = m.level if fixed == "level" else fixed
            else:
                dmg = self.calc_damage(m, t, mv, power=power, mtype=info["type"], category=info["category"], crit=crit)
            if self._abil(t, m) == "sturdy" and t.hp == t.max_hp and dmg >= t.hp:
                dmg = t.hp - 1
                self.L(f"{t.name} endured the hit with Sturdy!")
            dmg = min(dmg, t.hp)
            t.hp -= dmg
            total += dmg
            did += 1
            crit_any = crit_any or (crit and not fixed)
            if t.fainted:
                break
        if eff > 1:
            self.L("It's super effective!")
        elif eff < 1:
            self.L("It's not very effective...")
        if crit_any:
            self.L("A critical hit!")
        if hits > 1:
            self.L(f"Hit {did} time(s)!")
        # --- contact punishers
        if info["category"] == "physical" and total > 0 and not m.fainted:
            self.contact_effects(m, t)
        # --- drain / recoil
        if mv.drain and total and info["kind"] == "normal":
            amt = max(1, abs(mv.drain) * total // 100)
            if mv.drain > 0:
                m.hp = min(m.max_hp, m.hp + amt)
                self.L(f"{m.name} drained energy!")
            elif m.ability != "magic-guard":
                if mv is STRUGGLE:
                    amt = max(1, m.max_hp // 4)
                m.hp = max(0, m.hp - amt)
                self.L(f"{m.name} was hurt by recoil!")
        # --- secondary effects (target must survive)
        if info["kind"] == "normal" and not t.fainted and m.ability != "sheer-force":
            if mv.ailment not in ("none", "") and mv.meta in ("damage+ailment",):
                if self.rng.random() * 100 < (mv.ailment_chance or 100):
                    self.apply_ailment(t, mv, m)
            if mv.flinch_chance and self.rng.random() * 100 < mv.flinch_chance and not t.moved \
                    and t.ability != "inner-focus" and not t.dynamaxed:
                t.flinch = True
        if info["kind"] == "normal" and mv.stat_changes and mv.meta in ("damage+raise", "damage+lower") \
                and m.ability != "sheer-force" and (not t.fainted or mv.meta == "damage+raise"):
            if self.rng.random() * 100 < (mv.stat_chance or 100):
                tgt = m if mv.meta == "damage+raise" else t
                for st, d in mv.stat_changes:
                    self.change_stage(tgt, st, d, m)
        if info["kind"] == "max":
            self.max_effect(i, info["type"])
        # --- faint handling
        if t.fainted:
            self.faint(fi)
            if m.ability == "moxie" and not m.fainted:
                self.L(f"{m.name}'s Moxie!")
                self.change_stage(m, "atk", 1)
        if m.fainted:
            self.faint(i)

    def max_effect(self, i: int, mtype: str):
        m, t = self.mon(i), self.mon(self.foe(i))
        eff = MAX_EFFECT.get(mtype)
        if not eff:
            return
        if eff[0] == "weather":
            self.set_weather(eff[1])
        else:
            _, st, d, on_user = eff
            tgt = m if on_user else t
            if not tgt.fainted:
                self.change_stage(tgt, st, d, m)

    def ability_immune(self, att: BMon, t: BMon, mtype: str, category: str) -> bool:
        ab = self._abil(t, att)
        if mtype == "ground" and ab == "levitate":
            self.L(f"{t.name}'s Levitate makes it immune!")
            return True
        if mtype == "fire" and ab == "flash-fire":
            t.flash_fire = True
            self.L(f"{t.name}'s Flash Fire powered it up!")
            return True
        if mtype == "water" and ab in ("water-absorb", "dry-skin", "storm-drain"):
            if ab == "storm-drain":
                self.L(f"{t.name}'s Storm Drain absorbed it!")
                self.change_stage(t, "spa", 1)
            else:
                self.heal(t, t.max_hp // 4, f"{t.name}'s {ab.replace('-', ' ').title()} restored HP!")
            return True
        if mtype == "electric" and ab in ("volt-absorb", "lightning-rod", "motor-drive"):
            if ab == "volt-absorb":
                self.heal(t, t.max_hp // 4, f"{t.name}'s Volt Absorb restored HP!")
            elif ab == "lightning-rod":
                self.L(f"{t.name}'s Lightning Rod absorbed it!")
                self.change_stage(t, "spa", 1)
            else:
                self.L(f"{t.name}'s Motor Drive absorbed it!")
                self.change_stage(t, "spe", 1)
            return True
        if mtype == "grass" and ab == "sap-sipper":
            self.L(f"{t.name}'s Sap Sipper absorbed it!")
            self.change_stage(t, "atk", 1)
            return True
        return False

    def heal(self, m: BMon, amt: int, msg: str = ""):
        if m.fainted or amt <= 0:
            return
        before = m.hp
        m.hp = min(m.max_hp, m.hp + amt)
        if msg and m.hp > before:
            self.L(msg)

    def contact_effects(self, att: BMon, de: BMon):
        ab = self._abil(de, att)
        if att.ability == "magic-guard":
            pass
        if ab in ("rough-skin", "iron-barbs") and att.ability != "magic-guard":
            att.hp = max(0, att.hp - max(1, att.max_hp // 8))
            self.L(f"{att.name} was hurt by {de.name}'s {ab.replace('-', ' ').title()}!")
        elif ab == "static" and self.rng.random() < 0.3:
            self.inflict(att, "par", de, quiet=True)
        elif ab == "poison-point" and self.rng.random() < 0.3:
            self.inflict(att, "psn", de, quiet=True)
        elif ab == "flame-body" and self.rng.random() < 0.3:
            self.inflict(att, "brn", de, quiet=True)

    def apply_ailment(self, t: BMon, mv: Move, src: BMon):
        a = mv.ailment
        if a == "confusion":
            if not t.confused:
                t.confused = self.rng.randint(2, 4)
                self.L(f"{t.name} became confused!")
            return
        code = {"paralysis": "par", "burn": "brn", "poison": "psn", "sleep": "slp", "freeze": "frz"}.get(a)
        if code == "psn" and mv.name in ("toxic", "poison-fang"):
            code = "tox"
        if code:
            self.inflict(t, code, src)

    def status_move(self, i: int, mv: Move, targets_self: bool):
        m, t = self.mon(i), self.mon(self.foe(i))
        did = False
        if mv.meta == "heal" or mv.healing > 0:
            if m.hp >= m.max_hp:
                self.L(f"{m.name}'s HP is full!")
            else:
                self.heal(m, max(1, m.max_hp * (mv.healing or 50) // 100), f"{m.name} regained health!")
            did = True
        if mv.ailment not in ("none", ""):
            did = True
            if t.fainted or t.protected:
                self.L("But it failed!" if not t.fainted else "But there was no target...")
                return
            acc = mv.accuracy
            if acc and m.ability != "no-guard" and t.ability != "no-guard":
                acc *= self.stage_mult(m.stages["acc"] - t.stages["eva"], acc=True)
                if self.rng.random() * 100 >= acc:
                    self.L(f"{m.name}'s attack missed!")
                    return
            if mv.type == "electric" and "ground" in t.types and mv.ailment == "paralysis":
                self.L(f"It doesn't affect {t.name}...")
                return
            if mv.type == "grass" and mv.name.endswith("powder") and "grass" in t.types:
                self.L(f"It doesn't affect {t.name}...")
                return
            self.apply_ailment(t, mv, m)
        if mv.stat_changes:
            did = True
            own = targets_self
            tgt = m if own else t
            if not own and (t.fainted or t.protected):
                self.L("But it failed!")
                return
            if not own and mv.accuracy and m.ability != "no-guard":
                acc = mv.accuracy * self.stage_mult(m.stages["acc"] - t.stages["eva"], acc=True)
                if self.rng.random() * 100 >= acc:
                    self.L(f"{m.name}'s attack missed!")
                    return
            for st, d in mv.stat_changes:
                self.change_stage(tgt, st, d, m)
        if not did:
            self.L("But nothing happened!")

    # ---- turn processing
    def legal_actions(self, i: int) -> Dict[str, list]:
        s, m = self.sides[i], self.sides[i].mon
        moves = [k for k, bm in enumerate(m.moves) if bm.pp > 0] or [0]
        sw = [k for k in s.alive_idx() if k != s.active]
        gims = [g for g in ("mega", "z", "dynamax", "tera") if self.can_gimmick(i, g)]
        return {"moves": moves, "switches": sw, "gimmicks": gims}

    def switch_in(self, i: int, idx: int, forced: bool = False):
        s = self.sides[i]
        old = s.mon
        if not forced and not old.fainted:
            self.L(f"{s.name} withdrew {old.name}!")
        if old.ability == "natural-cure" and old.status and not old.fainted:
            old.status = None
        if old.dynamaxed:
            old.dyn_turns = 0
            self.end_dynamax(old)
        if old.mega:   # mega is lost visually when switched; keep it simple: stays mega for the battle
            pass
        old.reset_volatile()
        s.active = idx
        new = s.mon
        new.reset_volatile()
        self.L(f"{s.name} sends out {new.name}! (Lv{new.level})")
        self.on_enter(i)

    def step(self, acts: List[Action]) -> List[str]:
        self.log = []
        self.pending = []
        self.turn += 1
        for i in (0, 1):
            self.mon(i).moved = False
            self.mon(i).protected = False
        # switches first
        for i in sorted((0, 1), key=lambda k: -self.speed(self.mon(k))):
            if acts[i][0] == "switch":
                self.switch_in(i, acts[i][1])
        # gimmicks
        for i in sorted((0, 1), key=lambda k: -self.speed(self.mon(k))):
            a = acts[i]
            if a[0] == "move" and a[2] in ("mega", "tera", "dynamax") and self.can_gimmick(i, a[2]):
                self.activate(i, a[2])
            elif a[0] == "move" and a[2] == "z" and self.can_gimmick(i, "z"):
                self.activate(i, "z")
        # build move queue
        queue = []
        for i in (0, 1):
            a = acts[i]
            if a[0] != "move":
                continue
            m = self.mon(i)
            idx = a[1] if a[1] < len(m.moves) else 0
            mv = m.moves[idx].move
            gim = a[2] if self.sides[i].used_gimmick == "z" and a[2] == "z" else None
            pr = mv.priority
            if mv.category == "status" and m.ability == "prankster":
                pr += 1
            if m.dynamaxed and mv.category == "status":
                pr = 4
            elif mv.name in ("protect", "detect"):
                pr = 4
            queue.append((-pr, -self.speed(m), self.rng.random(), i, idx, gim))
        queue.sort()
        for _, _, _, i, idx, gim in queue:
            if self.mon(i).fainted or self.winner is not None:
                continue
            if self.mon(self.foe(i)).fainted and False:
                continue
            self.use_move(i, idx, gim)
            self.check_end()
        self.end_of_turn()
        self.check_end()
        return self.log

    def end_of_turn(self):
        if self.weather:
            self.weather_turns -= 1
            if self.weather_turns <= 0:
                self.L({"sun": "The harsh sunlight faded.", "rain": "The rain stopped.",
                        "sand": "The sandstorm subsided.", "hail": "The hail stopped."}[self.weather])
                self.weather = None
        for i in (0, 1):
            m = self.mon(i)
            if m.fainted:
                continue
            if self.weather == "sand" and not set(m.types) & {"rock", "ground", "steel"} and m.ability != "magic-guard":
                m.hp = max(0, m.hp - max(1, m.max_hp // 16))
                self.L(f"{m.name} is buffeted by the sandstorm!")
            elif self.weather == "hail" and "ice" not in m.types and m.ability != "magic-guard":
                m.hp = max(0, m.hp - max(1, m.max_hp // 16))
                self.L(f"{m.name} is pelted by hail!")
            if not m.fainted and m.ability != "magic-guard":
                if m.status == "brn":
                    m.hp = max(0, m.hp - max(1, m.max_hp // 16))
                    self.L(f"{m.name} is hurt by its burn!")
                elif m.status == "psn":
                    m.hp = max(0, m.hp - max(1, m.max_hp // 8))
                    self.L(f"{m.name} is hurt by poison!")
                elif m.status == "tox":
                    m.hp = max(0, m.hp - max(1, m.max_hp * m.toxic_n // 16))
                    m.toxic_n = min(15, m.toxic_n + 1)
                    self.L(f"{m.name} is badly hurt by poison!")
            if not m.fainted and m.status and m.ability == "shed-skin" and self.rng.random() < 1 / 3:
                m.status = None
                self.L(f"{m.name}'s Shed Skin cured its status!")
            if not m.fainted and m.ability == "speed-boost":
                self.change_stage(m, "spe", 1)
            if not m.fainted and m.dynamaxed:
                m.dyn_turns -= 1
                if m.dyn_turns == 0:
                    self.end_dynamax(m)
            m.flinch = False
            if m.fainted:
                self.faint(i)

    def check_end(self):
        if self.winner is not None:
            return
        dead = [not self.sides[i].alive_idx() for i in (0, 1)]
        if dead[0] and dead[1]:
            self.winner = -1   # draw
        elif dead[0]:
            self.winner = 1
        elif dead[1]:
            self.winner = 0
        self.pending = [] if self.winner is not None else [
            i for i in (0, 1) if self.mon(i).fainted and self.sides[i].alive_idx()]

    def replace(self, i: int, idx: int):
        """Send in a replacement after a faint."""
        self.log = []
        self.switch_in(i, idx, forced=True)
        self.pending = [k for k in self.pending if k != i]
        return self.log

    # ---- AI
    def estimate(self, att: BMon, de: BMon, bm: BMove) -> float:
        mv = bm.move
        if mv.category == "status" or (mv.power == 0 and mv.name not in FIXED_DAMAGE and mv.meta != "ohko"):
            return 0.0
        eff = type_mult(mv.type, de.types)
        if eff == 0:
            return 0.0
        if mv.name in FIXED_DAMAGE:
            d = att.level if FIXED_DAMAGE[mv.name] == "level" else FIXED_DAMAGE[mv.name]
        else:
            d = self.calc_damage(att, de, mv, power=mv.power * max(1, (mv.min_hits + mv.max_hits) / 2),
                                 mtype=mv.type, category=mv.category, roll=0.925)
        acc = (mv.accuracy or 100) / 100
        return min(d, de.hp) / max(1, de.max_hp) * acc

    def ai_choose(self, i: int) -> Action:
        s, m = self.sides[i], self.mon(i)
        t = self.mon(self.foe(i))
        gim = None
        if s.ai_gimmick and self.can_gimmick(i, s.ai_gimmick) and (len(s.alive_idx()) == 1 or s.active == len(s.team) - 1):
            gim = s.ai_gimmick
        scores = []
        for k, bm in enumerate(m.moves):
            if bm.pp <= 0:
                continue
            mv = bm.move
            sc = self.estimate(m, t, bm)
            if sc >= t.hp / max(1, t.max_hp):
                sc += 0.5
            if mv.category == "status":
                if mv.ailment not in ("none", "") and not t.status and not t.fainted:
                    sc = 0.4
                elif mv.healing and m.hp_pct() < 0.5:
                    sc = 0.7
                elif mv.stat_changes and mv.target == "user" and all(m.stages.get(st, 0) < 2 for st, _ in mv.stat_changes):
                    sc = 0.2
                elif mv.name in WEATHER_MOVES and self.weather != WEATHER_MOVES[mv.name]:
                    sc = 0.15
                elif mv.name in ("protect", "detect"):
                    sc = 0.05
                else:
                    sc = 0.05
            scores.append((sc + self.rng.random() * 0.12, k))
        if not scores:
            return ("move", 0, None)
        best = max(scores)[1] if self.rng.random() > 0.1 else self.rng.choice(scores)[1]
        return ("move", best, gim)

    def ai_replacement(self, i: int) -> int:
        s = self.sides[i]
        t = self.mon(self.foe(i))
        best, best_sc = None, -9
        for k in s.alive_idx():
            m = s.team[k]
            sc = 0.0
            for bm in m.moves:
                sc = max(sc, self.estimate(m, t, bm))
            if sc > best_sc:
                best, best_sc = k, sc
        return best if best is not None else s.alive_idx()[0]


# --------------------------------------------------------------------------- catching
def catch_attempt(mon: BMon, capture_rate: int, ball_mult: float, rng: random.Random):
    """Gen 3/4 style formula. Returns (caught, shakes 0-3)."""
    status = 2.0 if mon.status in ("slp", "frz") else (1.5 if mon.status else 1.0)
    a = (3 * mon.max_hp - 2 * mon.hp) * capture_rate * ball_mult / (3 * mon.max_hp) * status
    if a >= 255:
        return True, 3
    b = 1048560 / math.sqrt(math.sqrt(16711680 / max(a, 1)))
    shakes = 0
    for _ in range(4):
        if rng.randrange(65536) >= b:
            return False, shakes
        shakes += 1
    return True, 3


# --------------------------------------------------------------------------- misc
def xp_for_level(level: int) -> int:
    return level ** 3


def xp_gain(base_exp: int, foe_level: int, trainer: bool) -> int:
    return max(1, int(base_exp * foe_level / 7 * (1.5 if trainer else 1.0)))


def hp_bar(m: BMon, width: int = 10) -> str:
    n = 0 if m.hp <= 0 else max(1, round(m.hp_pct() * width))
    return "█" * n + "░" * (width - n)
