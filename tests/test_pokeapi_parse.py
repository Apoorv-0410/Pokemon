"""Checks the live-API provider against PokeAPI-shaped JSON (the sandbox has no network)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pokebot import data

B = "https://pokeapi.co/api/v2"
STAT = lambda n, v: {"base_stat": v, "effort": 0, "stat": {"name": n}}
FIX = {
    f"{B}/pokemon/6": {
        "id": 6, "name": "charizard", "base_experience": 267,
        "abilities": [{"ability": {"name": "blaze"}, "is_hidden": False, "slot": 1},
                      {"ability": {"name": "solar-power"}, "is_hidden": True, "slot": 3}],
        "stats": [STAT("hp", 78), STAT("attack", 84), STAT("defense", 78), STAT("special-attack", 109),
                  STAT("special-defense", 85), STAT("speed", 100)],
        "types": [{"slot": 2, "type": {"name": "flying"}}, {"slot": 1, "type": {"name": "fire"}}],
        "species": {"name": "charizard", "url": f"{B}/pokemon-species/6/"},
        "moves": [
            {"move": {"name": "flamethrower"}, "version_group_details": [
                {"level_learned_at": 46, "move_learn_method": {"name": "level-up"}},
                {"level_learned_at": 36, "move_learn_method": {"name": "level-up"}},
                {"level_learned_at": 0, "move_learn_method": {"name": "machine"}}]},
            {"move": {"name": "mega-punch"}, "version_group_details": [
                {"level_learned_at": 0, "move_learn_method": {"name": "machine"}}]},
            {"move": {"name": "scratch"}, "version_group_details": [
                {"level_learned_at": 1, "move_learn_method": {"name": "level-up"}}]}]},
    f"{B}/pokemon/charizard-mega-x": {
        "id": 10034, "name": "charizard-mega-x", "base_experience": 285,
        "abilities": [{"ability": {"name": "tough-claws"}, "is_hidden": False, "slot": 1}],
        "stats": [STAT("hp", 78), STAT("attack", 130), STAT("defense", 111), STAT("special-attack", 130),
                  STAT("special-defense", 85), STAT("speed", 100)],
        "types": [{"slot": 1, "type": {"name": "fire"}}, {"slot": 2, "type": {"name": "dragon"}}],
        "species": {"name": "charizard", "url": f"{B}/pokemon-species/6/"}, "moves": []},
    f"{B}/pokemon-species/5": {
        "id": 5, "name": "charmeleon", "capture_rate": 45, "is_legendary": False, "is_mythical": False,
        "evolution_chain": {"url": f"{B}/evolution-chain/2/"},
        "varieties": [{"is_default": True, "pokemon": {"name": "charmeleon"}}]},
    f"{B}/pokemon-species/6": {
        "id": 6, "name": "charizard", "capture_rate": 45, "is_legendary": False, "is_mythical": False,
        "evolution_chain": {"url": f"{B}/evolution-chain/2/"},
        "varieties": [{"is_default": True, "pokemon": {"name": "charizard"}},
                      {"is_default": False, "pokemon": {"name": "charizard-mega-x"}},
                      {"is_default": False, "pokemon": {"name": "charizard-mega-y"}},
                      {"is_default": False, "pokemon": {"name": "charizard-gmax"}}]},
    f"{B}/pokemon-species/150": {
        "id": 150, "name": "mewtwo", "capture_rate": 3, "is_legendary": True, "is_mythical": False,
        "evolution_chain": {"url": f"{B}/evolution-chain/78/"}, "varieties": []},
    f"{B}/evolution-chain/78/": {"chain": {"species": {"name": "mewtwo"}, "evolves_to": [], "evolution_details": []}},
    f"{B}/evolution-chain/2/": {"chain": {
        "species": {"name": "charmander", "url": f"{B}/pokemon-species/4/"}, "evolution_details": [],
        "evolves_to": [{
            "species": {"name": "charmeleon", "url": f"{B}/pokemon-species/5/"},
            "evolution_details": [{"min_level": 16, "trigger": {"name": "level-up"}, "item": None}],
            "evolves_to": [{
                "species": {"name": "charizard", "url": f"{B}/pokemon-species/6/"},
                "evolution_details": [{"min_level": 36, "trigger": {"name": "level-up"}}], "evolves_to": []}]}]}},
    f"{B}/move/flamethrower": {
        "name": "flamethrower", "accuracy": 100, "power": 90, "pp": 15, "priority": 0,
        "damage_class": {"name": "special"}, "type": {"name": "fire"}, "target": {"name": "selected-pokemon"},
        "stat_changes": [], "meta": {"ailment": {"name": "burn"}, "category": {"name": "damage+ailment"},
                                      "min_hits": None, "max_hits": None, "drain": 0, "healing": 0, "crit_rate": 0,
                                      "ailment_chance": 10, "flinch_chance": 0, "stat_chance": 0}},
    f"{B}/move/swords-dance": {
        "name": "swords-dance", "accuracy": None, "power": None, "pp": 20, "priority": 0,
        "damage_class": {"name": "status"}, "type": {"name": "normal"}, "target": {"name": "user"},
        "stat_changes": [{"change": 2, "stat": {"name": "attack"}}],
        "meta": {"ailment": {"name": "none"}, "category": {"name": "net-good-stats"}, "min_hits": None,
                 "max_hits": None, "drain": 0, "healing": 0, "crit_rate": 0, "ailment_chance": 0,
                 "flinch_chance": 0, "stat_chance": 0}},
    f"{B}/move/double-slap": {
        "name": "double-slap", "accuracy": 85, "power": 15, "pp": 10, "priority": 0,
        "damage_class": {"name": "physical"}, "type": {"name": "normal"}, "target": {"name": "selected-pokemon"},
        "stat_changes": [], "meta": None},
    f"{B}/type/fire": {"pokemon": [
        {"slot": 1, "pokemon": {"name": "charmander", "url": f"{B}/pokemon/4/"}},
        {"slot": 1, "pokemon": {"name": "charizard", "url": f"{B}/pokemon/6/"}},
        {"slot": 1, "pokemon": {"name": "charizard-mega-x", "url": f"{B}/pokemon/10034/"}}]},
}


def make():
    p = data.PokeAPIProvider()
    p._fetch_sync = lambda url: FIX[url]
    return p


from conftest import run


def test_form_parsing():
    f = run(make().get_form(6))
    assert f.types == ["fire", "flying"]               # sorted by slot
    assert f.stats == {"hp": 78, "atk": 84, "def": 78, "spa": 109, "spd": 85, "spe": 100}
    assert ("blaze", False) in f.abilities and ("solar-power", True) in f.abilities
    assert dict(f.learnset) == {"flamethrower": 36, "scratch": 1}   # level-up only, earliest level
    assert f.species_id == 6 and f.base_exp == 267


def test_species_parsing():
    p = make()
    s = run(p.get_species(6))
    assert s.capture_rate == 45 and not s.legendary and s.evolutions == []
    assert s.mega_forms == ["charizard-mega-x", "charizard-mega-y"]   # gmax excluded
    assert run(p.get_species(5)).evolutions == [(6, 36)]
    assert run(p.get_species(150)).legendary


def test_move_parsing():
    p = make()
    m = run(p.get_move("flamethrower"))
    assert (m.type, m.category, m.power, m.accuracy, m.ailment, m.ailment_chance, m.meta) == \
        ("fire", "special", 90, 100, "burn", 10, "damage+ailment")
    sd = run(p.get_move("swords-dance"))
    assert sd.category == "status" and sd.accuracy == 0 and sd.stat_changes == [("atk", 2)] and sd.target == "user"
    ds = run(p.get_move("double-slap"))        # meta can be null in the API
    assert ds.power == 15 and ds.min_hits == 1


def test_type_listing_filters_forms():
    ids = run(make().species_of_type("fire"))
    assert ids == [4, 6]            # form id 10034 dropped


def test_mega_form_fetch():
    f = run(make().get_form("charizard-mega-x"))
    assert f.types == ["fire", "dragon"] and f.stats["atk"] == 130
