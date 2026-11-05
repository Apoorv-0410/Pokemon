"""Eggs and breeding (PokeMMO-style: no genders; a Ditto or same-family pair makes an egg; some IVs are inherited)."""
import random
from typing import Any, Dict, List, Optional, Tuple

from . import world
from .data import get_provider
from .game import new_record

BREED_ITEMS = ('everstone', 'destiny-knot', 'power-weight', 'power-bracer', 'power-belt', 'power-lens', 'power-band', 'power-anklet')
STATS = ("hp", "atk", "def", "spa", "spd", "spe")


async def root_of(sid: int) -> int:
    p = get_provider()
    for _ in range(5):
        sp = await p.get_species(sid)
        if not sp.evolves_from:
            return sid
        sid = sp.evolves_from
    return sid


async def pair_species(a: Dict[str, Any], b: Dict[str, Any]) -> Tuple[Optional[int], str]:
    """The species an egg from this pair would hatch into, or (None, why not)."""
    if a["id"] == b["id"]:
        return None, "Pick two different Pokémon."
    p = get_provider()
    for r in (a, b):
        if (await p.get_species(r["species_id"])).legendary:
            return None, "Legendary Pokémon can't breed."
    da, db_ = a["species_id"] == world.DITTO, b["species_id"] == world.DITTO
    if da and db_:
        return None, "Two Dittos can't make an egg."
    if da or db_:
        return await root_of((b if da else a)["species_id"]), ""
    ra, rb = await root_of(a["species_id"]), await root_of(b["species_id"])
    if ra != rb:
        return None, "Those two aren't compatible — use the same Pokémon family, or a Ditto."
    return ra, ""


POWER = {"power-weight": "hp", "power-bracer": "atk", "power-belt": "def", "power-lens": "spa", "power-band": "spd", "power-anklet": "spe"}


def inherit(a: Dict[str, Any], b: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    """Which IVs (and maybe the nature) the egg gets. Power items fix a stat, Destiny Knot passes 5 IVs, Everstone passes the nature."""
    held = [a.get("item") or "", b.get("item") or ""]
    out: Dict[str, Any] = {}
    for parent in (a, b):
        stat = POWER.get(parent.get("item") or "")
        if stat and stat not in out:
            out[stat] = parent["iv"][stat]
    n = 5 if "destiny-knot" in held else world.BREED_IVS
    rest = [x for x in STATS if x not in out]
    for stat in rng.sample(rest, max(0, min(len(rest), n - len(out)))):
        out[stat] = rng.choice((a, b))["iv"][stat]
    stoned = [p for p in (a, b) if p.get("item") == "everstone"]
    if stoned:
        out["nature"] = rng.choice(stoned).get("nature") or "Hardy"
    return out


async def breed(db, uid: int, a: Dict[str, Any], b: Dict[str, Any], rng: random.Random) -> Tuple[bool, str]:
    if len(db.eggs(uid)) >= world.EGG_CAP:
        return False, f"You're carrying {world.EGG_CAP} eggs already — hatch some first!"
    sid, why = await pair_species(a, b)
    if sid is None:
        return False, why
    if not db.spend(uid, world.BREED_COST):
        return False, f"Breeding costs ${world.BREED_COST:,}."
    db.add_egg(uid, sid, "bred", inherit(a, b, rng), world.EGG_STEPS)
    for parent in (a, b):                                   # breeding items are used up
        if parent.get("item") in BREED_ITEMS:
            db.x("UPDATE mons SET item='' WHERE id=?", (parent["id"],))
    return True, "🥚 The Nursery gave you an egg!"


async def random_species(region_key: str, rng: random.Random) -> int:
    reg = world.REGION_BY_KEY[region_key]
    p = get_provider()
    for _ in range(60):
        sid = rng.randint(reg.dex_lo, reg.dex_hi)
        try:
            sp = await p.get_species(sid)
        except Exception:                       # noqa: BLE001
            continue
        if not sp.evolves_from and not sp.legendary:
            return sid
    return reg.dex_lo


def found(db, uid: int, region_key: str, rng: random.Random, species: int) -> int:
    return db.add_egg(uid, species, region_key, {}, world.EGG_STEPS)


async def offer(region_key: str, rng: random.Random) -> Dict[str, Any]:
    """A wandering merchant sells an egg from a region other than yours."""
    others = [r.key for r in world.REGIONS if r.key != region_key]
    key = rng.choice(others)
    return {"species": await random_species(key, rng), "origin": key, "price": world.EGG_NPC_PRICE}


async def tick(db, uid: int, rng: random.Random) -> List[Dict[str, Any]]:
    """One /explore step for every egg; returns the hatched Pokémon records (already saved) as dicts with id."""
    hatched = []
    for e in db.eggs(uid):
        if e["steps"] > 1:
            db.x("UPDATE eggs SET steps=steps-1 WHERE id=?", (e["id"],))
            continue
        rec = await new_record(e["species_id"], world.EGG_HATCH_LEVEL, rng)
        ivs = dict(e["iv"])
        nature = ivs.pop("nature", None)
        rec["iv"].update(ivs)
        if nature:
            rec["nature"] = nature
        from .game import max_hp_of
        rec["hp"] = await max_hp_of(rec)
        rec["friendship"] = 120
        mid = db.add_mon(uid, rec)
        db.del_egg(e["id"])
        hatched.append(dict(rec, id=mid, origin=e["origin"], inherited=len([k for k in e["iv"] if k != "nature"])))
    return hatched
