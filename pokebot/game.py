"""Game services: building Pokémon records, wild/gym generation, leveling/evolution, DB<->battle conversion."""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from . import world
from .battle import BMon, NATURES, Side, xp_for_level, calc_stats
from .data import TYPES, Evo, Form, get_provider


SHINY_ODDS = 4096          # 1 in 4096
EV_CAP, EV_TOTAL = 252, 510
FRIENDSHIP_EVOLVE = 220
NON_LEVEL_STAGE = 36       # stone / friendship / trade evolutions only appear in the wild from this level on
STONE_FOR_TYPE = {"normal": "moon-stone", "fire": "fire-stone", "water": "water-stone", "electric": "thunder-stone",
                  "grass": "leaf-stone", "ice": "ice-stone", "fighting": "dawn-stone", "poison": "dusk-stone",
                  "ground": "sun-stone", "flying": "sun-stone", "psychic": "dawn-stone", "bug": "leaf-stone",
                  "rock": "sun-stone", "ghost": "dusk-stone", "dragon": "shiny-stone", "dark": "dusk-stone",
                  "steel": "dusk-stone", "fairy": "shiny-stone"}
STONE_OVERRIDE = {462: "thunder-stone", 476: "thunder-stone", 738: "thunder-stone",   # Magnezone, Probopass, Vikavolt
                  470: "leaf-stone", 471: "ice-stone", 740: "ice-stone"}               # Leafeon, Glaceon, Crabominable


def pretty(name: str) -> str:
    return name.replace("-", " ").title()


def fkey(rec: Dict[str, Any]):
    """Key for the Pokémon's current form (a form slug such as 'rattata-alola', or the species id)."""
    return rec.get("form") or rec["species_id"]


def form_label(slug: str, species_name: str = "") -> str:
    """'rattata-alola' -> 'Alolan' ; 'arceus-fire' -> 'Fire'."""
    for suf, lab in (("alola", "Alolan"), ("galar", "Galarian"), ("hisui", "Hisuian"), ("paldea", "Paldean")):
        if f"-{suf}" in slug:
            return lab
    return pretty(slug.split("-", 1)[1]) if "-" in slug else ""


# regional variants: base species that have them, and where they spawn
REGIONAL = {
    "alola": [19, 20, 26, 27, 28, 37, 38, 50, 51, 52, 53, 74, 75, 76, 88, 89, 103, 105],
    "galar": [52, 77, 78, 79, 80, 83, 110, 122, 199, 222, 263, 264, 554, 555, 562, 618],
    "hisui": [58, 59, 100, 101, 157, 211, 215, 503, 549, 570, 571, 628, 705, 706, 713, 724],
    "paldea": [128, 194],
}
REGION_SUFFIX = {"alola": "alola", "galar": "galar", "sinnoh": "hisui", "paldea": "paldea"}
REGIONAL_CHANCE = 0.25

_T = ["fire", "water", "electric", "grass", "ice", "fighting", "poison", "ground", "flying", "psychic", "bug", "rock",
      "ghost", "dragon", "dark", "steel", "fairy"]
# Pokémon whose form can be bought with /form ($500, any number of times). Regional / Dynamax / Gmax forms are not here.
FORM_CHANGE_PRICE = 500
FORM_CHANGES = {
    493: [("", "Normal")] + [(f"arceus-{x}", x.title()) for x in _T],
    773: [("", "Normal")] + [(f"silvally-{x}", x.title()) for x in _T],
    649: [("", "Normal"), ("genesect-douse", "Douse"), ("genesect-shock", "Shock"), ("genesect-burn", "Burn"), ("genesect-chill", "Chill")],
    487: [("", "Altered"), ("giratina-origin", "Origin")],
    492: [("", "Land"), ("shaymin-sky", "Sky")],
    386: [("", "Normal"), ("deoxys-attack", "Attack"), ("deoxys-defense", "Defense"), ("deoxys-speed", "Speed")],
    479: [("", "Normal"), ("rotom-heat", "Heat"), ("rotom-wash", "Wash"), ("rotom-frost", "Frost"), ("rotom-fan", "Fan"), ("rotom-mow", "Mow")],
    741: [("", "Baile"), ("oricorio-pom-pom", "Pom-Pom"), ("oricorio-pau", "Pa'u"), ("oricorio-sensu", "Sensu")],
    641: [("", "Incarnate"), ("tornadus-therian", "Therian")], 642: [("", "Incarnate"), ("thundurus-therian", "Therian")],
    645: [("", "Incarnate"), ("landorus-therian", "Therian")], 905: [("", "Incarnate"), ("enamorus-therian", "Therian")],
    646: [("", "Normal"), ("kyurem-black", "Black"), ("kyurem-white", "White")],
    647: [("", "Ordinary"), ("keldeo-resolute", "Resolute")], 648: [("", "Aria"), ("meloetta-pirouette", "Pirouette")],
    720: [("", "Confined"), ("hoopa-unbound", "Unbound")],
    892: [("", "Single Strike"), ("urshifu-rapid-strike", "Rapid Strike")],
    745: [("", "Midday"), ("lycanroc-midnight", "Midnight"), ("lycanroc-dusk", "Dusk")],
    849: [("", "Amped"), ("toxtricity-low-key", "Low Key")],
    718: [("", "50%"), ("zygarde-10", "10%"), ("zygarde-complete", "Complete")],
}


def pick_moves(learnset, level: int) -> List[str]:
    known = [m for m, lv in learnset if lv <= level]
    out: List[str] = []
    for m in reversed(known):
        if m not in out:
            out.append(m)
        if len(out) == 4:
            break
    out.reverse()
    return out or ["tackle"]


def pick_ability(form: Form, rng: random.Random) -> str:
    normal = [a for a, h in form.abilities if not h] or [a for a, _ in form.abilities] or ["pressure"]
    hidden = [a for a, h in form.abilities if h]
    if hidden and rng.random() < 0.05:
        return hidden[0]
    return rng.choice(normal)


async def new_record(species_id: int, level: int, rng: random.Random, *, tera: Optional[str] = None,
                     allow_shiny: bool = True, form_slug: str = "") -> Dict[str, Any]:
    p = get_provider()
    form = await p.get_form(form_slug or species_id)
    sp = await p.get_species(species_id)
    ivs = {k: rng.randint(0, 31) for k in ("hp", "atk", "def", "spa", "spd", "spe")}
    stats = calc_stats(form.stats, level, ivs)
    own = form.types[0]
    return {
        "species_id": species_id, "species_name": pretty(sp.name) + (f" ({form_label(form_slug)})" if form_slug else ""),
        "form": form_slug, "nature": rng.choice(list(NATURES)), "nickname": None, "level": level,
        "xp": xp_for_level(level), "iv": ivs, "moves": pick_moves(form.learnset, level),
        "ability": pick_ability(form, rng), "tera_type": tera or (own if rng.random() < 0.5 else rng.choice(TYPES)),
        "hp": stats["hp"], "shiny": int(allow_shiny and rng.randrange(SHINY_ODDS) == 0), "friendship": 70, "evs": {},
    }


async def max_hp_of(rec: Dict[str, Any]) -> int:
    form = await get_provider().get_form(fkey(rec))
    return calc_stats(form.stats, rec["level"], rec["iv"], rec.get("evs"), rec.get("nature"))["hp"]


async def to_bmon(rec: Dict[str, Any], *, full_hp: bool = False) -> BMon:
    p = get_provider()
    form = await p.get_form(fkey(rec))
    sp = await p.get_species(rec["species_id"])
    moves = [await p.get_move(m) for m in rec["moves"]]
    mega = await p.get_form(sp.mega_forms[0]) if sp.mega_forms else None
    name = rec.get("nickname") or rec["species_name"]
    return BMon(name=name, species_id=rec["species_id"], level=rec["level"], form=form, ivs=rec["iv"], moves=moves,
                ability=rec["ability"], tera_type=rec["tera_type"], mega_form=mega, uid=rec.get("id"),
                hp=None if full_hp else rec["hp"])


# --------------------------------------------------------------------------- wild
async def fit_stage(sid: int, level: int) -> int:
    """Walk back to an earlier evolution stage when the level is too low for this one (no Lv5 Dragonite)."""
    p = get_provider()
    for _ in range(50):
        sp = await p.get_species(sid)
        if not sp.evolves_from:
            return sid
        prev = await p.get_species(sp.evolves_from)
        if prev.baby:
            return sid
        e = next((e for e in prev.evos if e.to == sid), None)
        need = e.level if e and e.kind == "level" else NON_LEVEL_STAGE
        if level >= need:
            return sid
        sid = sp.evolves_from
    return sid


async def wild_record(region_key: str, badges_in_region: int, rng: random.Random, ev_stat: Optional[str] = None,
                      conquered: bool = False) -> Dict[str, Any]:
    reg = world.REGION_BY_KEY[region_key]
    p = get_provider()
    lo, hi = world.wild_level_range(region_key, badges_in_region, conquered)
    level = rng.randint(lo, hi)
    sid, sp = reg.dex_lo, None
    for _ in range(30 if ev_stat else 12):
        sid = await fit_stage(rng.randint(reg.dex_lo, reg.dex_hi), level)
        sp = await p.get_species(sid)
        if sp.legendary:
            continue
        if ev_stat and not (await p.get_form(sid)).ev_yield.get(ev_stat):
            continue
        break
    slug = ""
    suf = REGION_SUFFIX.get(region_key)
    if suf and not ev_stat and rng.random() < REGIONAL_CHANCE:           # regional variants live in their home region
        base = await fit_stage(rng.choice(REGIONAL[suf]), level)
        bsp = await p.get_species(base)
        cand = next((v for v in bsp.varieties if f"-{suf}" in v), "")
        if cand and not bsp.legendary:
            sid, sp, slug = base, bsp, cand
    rec = await new_record(sid, level, rng, form_slug=slug)
    rec["capture_rate"] = sp.capture_rate
    rec["area"] = rng.choice(reg.areas)
    if not ev_stat and rng.random() < world.WILD_ITEM_CHANCE:                 # some wild Pokémon carry a held item
        rec["item"] = rng.choice(sorted(k for k, i in world.ITEMS.items() if i.kind == "held"))
    return rec


# --------------------------------------------------------------------------- gyms
async def build_team(region_key: str, types: List[str], size: int, level: int, rng: random.Random,
                     tera: Optional[str] = None) -> List[Dict[str, Any]]:
    """Stage-aware trainer team: Pokémon of the given types (cycled), preferring the region's own dex."""
    reg = world.REGION_BY_KEY[region_key]
    p = get_provider()
    cands: Dict[str, List[int]] = {}
    for typ in set(types):
        allt = await p.species_of_type(typ)
        local = [i for i in allt if reg.dex_lo <= i <= reg.dex_hi]
        other = [i for i in allt if not reg.dex_lo <= i <= reg.dex_hi]
        rng.shuffle(local)
        rng.shuffle(other)
        cands[typ] = local + other
    chosen: List[tuple] = []
    used: set = set()
    for k in range(size):
        typ = types[k % len(types)]
        pick = None
        for sid in cands[typ]:
            if sid in used or (await p.get_species(sid)).legendary:
                continue
            fit = await fit_stage(sid, level)
            if fit == sid:
                pick = sid
                break
            if pick is None and fit not in used:
                pick = fit                       # earlier stage as a fallback; keep looking for a proper fit
        if pick is not None:
            used.add(pick)
            chosen.append((pick, typ))
    recs = []
    for k, (sid, typ) in enumerate(chosen):
        lv = level + (2 if k == len(chosen) - 1 else 0)
        recs.append(await new_record(sid, lv, rng, tera=tera or typ, allow_shiny=False))
    return recs


async def gym_records(region_key: str, idx: int) -> List[Dict[str, Any]]:
    """Deterministic team for a gym leader."""
    gym = world.REGION_BY_KEY[region_key].gyms[idx]
    return await build_team(region_key, [gym.type], world.GYM_TEAM_SIZES[idx], world.gym_level(region_key, idx),
                            random.Random(f"{region_key}-{idx}"), tera=gym.type)


_EVENT_DAY: Dict[str, int] = {}


async def event_species(day: str) -> int:
    """Today's event Pokémon: any species from any region except legendaries/mythicals (same for everyone, changes at 00:00 UTC)."""
    if day in _EVENT_DAY:
        return _EVENT_DAY[day]
    rng = random.Random(f"event-{day}")
    hi = world.REGIONS[-1].dex_hi
    p = get_provider()
    sid = 25
    for _ in range(60):
        cand = rng.randint(1, hi)
        try:
            if not (await p.get_species(cand)).legendary:
                sid = cand
                break
        except Exception:                                    # noqa: BLE001 - species missing from the data source
            continue
    _EVENT_DAY[day] = sid
    return sid


async def event_record(region_key: str, badges: int, rng: random.Random, day: str, conquered: bool = False) -> Dict[str, Any]:
    sid = await event_species(day)
    lo, hi = world.wild_level_range(region_key, badges, conquered)
    rec = await new_record(sid, max(5, rng.randint(lo, hi)), rng)
    rec["capture_rate"] = (await get_provider().get_species(sid)).capture_rate
    rec["area"] = rng.choice(world.REGION_BY_KEY[region_key].areas)
    rec["event"] = True
    return rec


async def legendary_record(region_key: str, rng: random.Random) -> Dict[str, Any]:
    """The region's legendary for a streak-milestone encounter."""
    from . import quests
    sid = rng.choice(quests.LEGENDARIES[region_key])
    level = world.gym_level(region_key, 7) + 6
    rec = await new_record(sid, level, rng)
    rec["capture_rate"] = (await get_provider().get_species(sid)).capture_rate
    rec["area"] = "a mysterious place"
    return rec


async def random_trainer(region_key: str, badges: int, rng: random.Random, conquered: bool = False) -> Dict[str, Any]:
    """A passing trainer: class, name, dialogue and a small themed team a little above wild level."""
    lo, hi = world.wild_level_range(region_key, badges, conquered)
    level = rng.randint(lo, hi) + 1
    cls, intro, lose_line, win_line = rng.choice(world.TRAINER_CLASSES)
    types = rng.sample(sorted(STONE_FOR_TYPE), k=rng.choice([1, 1, 2]))
    size = 6 if badges >= world.DOUBLES_FROM_GYM else min(3, 1 + badges // 3 + rng.randint(0, 1))
    team = await build_team(region_key, types, size, level, rng)
    return {"cls": cls, "name": rng.choice(world.TRAINER_NAMES), "intro": intro, "lose_line": lose_line, "win_line": win_line,
            "types": types, "team": team, "level": level}


async def league_records(region_key: str, idx: int) -> List[Dict[str, Any]]:
    """Deterministic Elite Four / Champion team (idx 0-3 Elite Four, 4 Champion)."""
    name, types, _, size, level = world.league_foe(region_key, idx)
    return await build_team(region_key, list(types), size, level, random.Random(f"{region_key}-league-{idx}"))


def league_prize(region_key: str, idx: int, first_time: bool) -> int:
    base = int((4000 * (idx + 1) if idx < 4 else 30000) * (1 + world.region_index(region_key) * 0.25))
    return base if first_time else base // 4


def gym_ai_gimmick(region_key: str) -> Optional[str]:
    return {"kalos": "mega", "alola": "z", "galar": "dynamax", "paldea": "tera"}.get(region_key)


async def gym_side(region_key: str, idx: int) -> Side:
    gym = world.REGION_BY_KEY[region_key].gyms[idx]
    recs = await gym_records(region_key, idx)
    team = [await to_bmon(r, full_hp=True) for r in recs]
    g = gym_ai_gimmick(region_key)
    if g == "mega" and not team[-1].mega_form:
        # give the ace a mega-capable partner if any teammate has one
        for k, m in enumerate(team):
            if m.mega_form:
                team[k], team[-1] = team[-1], team[k]
                break
        else:
            g = None
    return Side(f"Leader {gym.leader}", team, gimmicks=[g] if g else [], ai=True, ai_gimmick=g)


def gym_prize(region_key: str, idx: int, first_time: bool) -> int:
    base = int(1200 * (idx + 1) * (1 + world.region_index(region_key) * 0.25))
    return base if first_time else base // 4


# --------------------------------------------------------------------------- progression
async def award_xp(db, rec: Dict[str, Any], amount: int) -> List[str]:
    """Add XP to a stored Pokémon, handling level-ups, new moves and level-based evolution."""
    p = get_provider()
    msgs: List[str] = []
    name = rec.get("nickname") or rec["species_name"]
    xp = rec["xp"] + amount
    level = rec["level"]
    old_max = await max_hp_of(rec)
    moves = list(rec["moves"])
    species_id = rec["species_id"]
    species_name = rec["species_name"]
    ability = rec["ability"]
    nickname = rec.get("nickname")
    while level < 100 and xp >= xp_for_level(level + 1):
        level += 1
        form = await p.get_form(rec.get("form") or species_id) if species_id == rec["species_id"] else await p.get_form(species_id)
        for m, lv in form.learnset:
            if lv == level and m not in moves:
                if len(moves) < 4:
                    moves.append(m)
                    msgs.append(f"{name} learned {pretty(m)}!")
                else:
                    worst, worst_pow = None, 10 ** 9
                    for old in moves:
                        mv = await p.get_move(old)
                        if mv.power < worst_pow:
                            worst, worst_pow = old, mv.power
                    new = await p.get_move(m)
                    if new.power >= worst_pow:
                        moves[moves.index(worst)] = m
                        msgs.append(f"{name} forgot {pretty(worst)} and learned {pretty(m)}!")
        sp = await p.get_species(species_id)
        evo = next((e for e in sp.evolutions if e[1] <= level), None)
        if evo:
            nform = await p.get_form(evo[0])
            nsp = await p.get_species(evo[0])
            msgs.append(f"✨ {name} evolved into {pretty(nsp.name)}!")
            species_id, species_name = evo[0], pretty(nsp.name)
            if nickname is None:
                name = species_name
            if ability not in [a for a, _ in nform.abilities]:
                ability = pick_ability(nform, random.Random())
    if level > rec["level"]:
        msgs.insert(0, f"⬆️ {name} grew to Lv{level}!")
    if species_id != rec["species_id"] and rec.get("owner") is not None:
        db.dex_mark_caught(rec["owner"], species_id)
    new_rec = dict(rec, level=level, xp=xp, moves=moves, species_id=species_id, species_name=species_name,
                   ability=ability)
    new_max = await max_hp_of(new_rec)
    hp = min(new_max, max(1, rec["hp"] + (new_max - old_max))) if rec["hp"] > 0 else 0
    db.update_mon(rec["id"], level=level, xp=xp, moves=moves, species_id=species_id, species_name=species_name,
                  ability=ability, hp=hp)
    rec.update(new_rec, hp=hp)
    return msgs


# --------------------------------------------------------------------------- balls
def ball_multiplier(key: str, *, turn: int, foe_level: int, my_level: int, foe_types: List[str], foe_speed: int,
                    night: bool, cave: bool, caught_before: bool) -> float:
    base = world.ITEMS[key].value
    if key == "quick-ball":
        return 5.0 if turn <= 1 else 1.0
    if key == "timer-ball":
        return min(4.0, 1.0 + turn * 0.3)
    if key == "repeat-ball":
        return 3.5 if caught_before else 1.0
    if key == "nest-ball":
        return max(1.0, (41 - foe_level) / 10) if foe_level < 30 else 1.0
    if key == "net-ball":
        return 3.5 if {"water", "bug"} & set(foe_types) else 1.0
    if key == "dusk-ball":
        return 3.0 if night or cave else 1.0
    if key == "level-ball":
        return 8.0 if my_level >= foe_level * 4 else 4.0 if my_level >= foe_level * 2 else 2.0 if my_level > foe_level else 1.0
    if key == "fast-ball":
        return 4.0 if foe_speed >= 100 else 1.0
    return base


# --------------------------------------------------------------------------- evolution, candy, EVs
async def evo_stone(e: Evo) -> str:
    if e.stone:
        return e.stone
    if e.to in STONE_OVERRIDE:
        return STONE_OVERRIDE[e.to]
    form = await get_provider().get_form(e.to)
    return STONE_FOR_TYPE.get(form.types[0], "moon-stone")


def _is_day(hour: Optional[int] = None) -> bool:
    import datetime as _dt
    h = _dt.datetime.now().hour if hour is None else hour
    return 6 <= h < 18


async def evolution_options(rec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Everything this Pokémon can still evolve into, and what each needs."""
    p = get_provider()
    sp = await p.get_species(rec["species_id"])
    out = []
    for e in sp.evos:
        name = pretty((await p.get_species(e.to)).name)
        o: Dict[str, Any] = {"kind": e.kind, "to": e.to, "name": name, "time": e.time}
        if e.kind == "level":
            o.update(need=f"reach Lv{e.level}", ready=False)
        elif e.kind == "stone":
            st = await evo_stone(e)
            o.update(stone=st, need=f"use a {world.ITEMS[st].name}", ready=True)
        elif e.kind == "friendship":
            ok_time = not e.time or (e.time == "day") == _is_day()
            o.update(need=f"friendship {FRIENDSHIP_EVOLVE}+" + (f" ({e.time})" if e.time else ""),
                     ready=rec.get("friendship", 70) >= FRIENDSHIP_EVOLVE and ok_time)
        else:
            o.update(need="trade with another trainer", ready=False)
        out.append(o)
    return out


async def evolve_mon(db, rec: Dict[str, Any], to_id: int) -> List[str]:
    p = get_provider()
    nsp = await p.get_species(to_id)
    slug = ""
    cur = rec.get("form") or ""
    suf = next((x for x in ("alola", "galar", "hisui", "paldea") if f"-{x}" in cur), "")
    if suf:                                                   # regional forms evolve into the regional form
        slug = next((v for v in nsp.varieties if f"-{suf}" in v), "")
    nform = await p.get_form(slug or to_id)
    old_name = rec.get("nickname") or rec["species_name"]
    old_max = await max_hp_of(rec)
    ability = rec["ability"]
    if ability not in [a for a, _ in nform.abilities]:
        ability = pick_ability(nform, random.Random())
    nname = pretty(nsp.name) + (f" ({form_label(slug)})" if slug else "")
    new_rec = dict(rec, species_id=to_id, species_name=nname, ability=ability, form=slug)
    new_max = await max_hp_of(new_rec)
    hp = min(new_max, max(1, rec["hp"] + new_max - old_max)) if rec["hp"] > 0 else 0
    db.update_mon(rec["id"], species_id=to_id, species_name=nname, ability=ability, hp=hp, form=slug)
    rec.update(new_rec, hp=hp)
    if rec.get("owner") is not None:
        db.dex_mark_caught(rec["owner"], to_id)
        from . import quests
        quests.record(db, rec["owner"], "evolve")
    return [f"✨ {old_name} evolved into {nname}!"]


async def use_rare_candy(db, rec: Dict[str, Any]) -> List[str]:
    if rec["level"] >= 100:
        return []
    return await award_xp(db, rec, xp_for_level(rec["level"] + 1) - rec["xp"])


def apply_evs(db, rec: Dict[str, Any], gains: Dict[str, int]) -> bool:
    evs = dict(rec.get("evs") or {})
    changed = False
    for stat, n in gains.items():
        room = min(EV_CAP - evs.get(stat, 0), EV_TOTAL - sum(evs.values()))
        if room > 0:
            evs[stat] = evs.get(stat, 0) + min(n, room)
            changed = True
    if changed:
        rec["evs"] = evs
        db.update_mon(rec["id"], evs=evs)
    return changed


# --------------------------------------------------------------------------- moves, forms, abilities
async def learn_lists(rec: Dict[str, Any]) -> Dict[str, List[str]]:
    form = await get_provider().get_form(fkey(rec))
    have = set(rec["moves"])
    relearn = [m for m, lv in form.learnset if lv <= rec["level"] and m not in have]
    tutor = [m for m in form.tutor_moves if m not in have and m not in relearn]
    tm = [m for m in form.tm_moves if m not in have and m not in relearn and m not in tutor]
    return {"r": relearn, "t": tutor, "m": tm}


async def tm_price(move: str) -> int:
    mv = await get_provider().get_move(move)
    return 7000 if mv.power >= 100 else 6000 if mv.power >= 70 else 5000


def tm_key(move: str) -> str:
    return f"tm-{move}"


async def teach_move(db, uid: int, rec: Dict[str, Any], kind: str, move: str, slot: Optional[int]) -> tuple:
    """Teach a move. kind r=relearn, t=tutor (free), m=TM (paid once, then reusable). Returns (ok, message)."""
    lists = await learn_lists(rec)
    if move not in lists.get(kind, []):
        return False, "That Pokémon can't learn that move right now."
    moves = list(rec["moves"])
    if len(moves) >= 4 and (slot is None or not 0 <= slot < len(moves)):
        return False, "Pick a move to forget."
    cost_note = ""
    if kind == "m" and db.qty(uid, tm_key(move)) == 0:
        price = await tm_price(move)
        if not db.spend(uid, price):
            return False, f"You need ${price:,} for this TM."
        db.add_item(uid, tm_key(move), 1)
        cost_note = f" (bought the TM for ${price:,} — it's yours to reuse!)"
    name = rec.get("nickname") or rec["species_name"]
    if len(moves) >= 4:
        old = moves[slot]
        moves[slot] = move
        msg = f"{name} forgot {pretty(old)} and learned {pretty(move)}!{cost_note}"
    else:
        moves.append(move)
        msg = f"{name} learned {pretty(move)}!{cost_note}"
    db.update_mon(rec["id"], moves=moves)
    rec["moves"] = moves
    return True, msg


def nature_text(name: str) -> str:
    n = NATURES.get(name)
    lab = {"atk": "Atk", "def": "Def", "spa": "SpA", "spd": "SpD", "spe": "Spe"}
    return f"{name} (+{lab[n[0]]} −{lab[n[1]]})" if n else f"{name} (neutral)"


async def use_nature_mint(db, rec: Dict[str, Any], nature: str) -> tuple:
    if nature not in NATURES or nature == rec.get("nature"):
        return False, "That's not a different nature."
    db.update_mon(rec["id"], nature=nature)
    rec["nature"] = nature
    return True, f"{rec.get('nickname') or rec['species_name']} now has a {nature_text(nature)} nature!"


async def form_options(rec: Dict[str, Any]) -> List[tuple]:
    return FORM_CHANGES.get(rec["species_id"], [])


async def change_form(db, uid: int, rec: Dict[str, Any], slug: str) -> tuple:
    opts = dict(await form_options(rec))
    if slug not in opts or slug == (rec.get("form") or ""):
        return False, "That's not a different form this Pokémon can take."
    if not db.spend(uid, FORM_CHANGE_PRICE):
        return False, f"You need ${FORM_CHANGE_PRICE} to change forms."
    p = get_provider()
    form = await p.get_form(slug or rec["species_id"])
    ability = rec["ability"] if rec["ability"] in [a for a, _ in form.abilities] else pick_ability(form, random.Random())
    old_max = await max_hp_of(rec)
    new_rec = dict(rec, form=slug, ability=ability)
    new_max = await max_hp_of(new_rec)
    hp = min(new_max, max(1, rec["hp"] + new_max - old_max)) if rec["hp"] > 0 else 0
    db.update_mon(rec["id"], form=slug, ability=ability, hp=hp)
    rec.update(new_rec, hp=hp)
    return True, f"✨ Changed to the {opts[slug]} form!"


async def use_ability_item(db, rec: Dict[str, Any], key: str) -> tuple:
    """Ability Capsule: swap between regular abilities. Ability Patch: switch to the Hidden Ability."""
    form = await get_provider().get_form(fkey(rec))
    normal = [a for a, h in form.abilities if not h]
    hidden = [a for a, h in form.abilities if h]
    cur = rec["ability"]
    if key == "ability-capsule":
        others = [a for a in normal if a != cur]
        if cur in hidden or not others:
            return False, "This Pokémon has no other regular ability to switch to."
        new = others[0]
    else:
        if not hidden or cur == hidden[0]:
            return False, "This Pokémon has no (other) Hidden Ability."
        new = hidden[0]
    db.update_mon(rec["id"], ability=new)
    rec["ability"] = new
    return True, f"{rec.get('nickname') or rec['species_name']}'s ability is now {pretty(new)}!"


async def heal_party(db, uid: int) -> int:
    n = 0
    for rec in db.party(uid):
        mh = await max_hp_of(rec)
        if rec["hp"] != mh:
            db.update_mon(rec["id"], hp=mh)
            n += 1
    return n
