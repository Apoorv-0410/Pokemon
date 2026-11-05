import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pokebot import data
from pokebot.battle import (BMon, Battle, Side, catch_attempt, type_mult, z_power, max_power,
                            IMPLEMENTED_ABILITIES)

P = data.MockProvider()
from conftest import run


async def mk(sid, level, rng, name=None, mega=False):
    sp = await P.get_species(sid)
    form = await P.get_form(sid)
    moves = [await P.get_move(m) for m, lv in form.learnset if lv <= level][-4:]
    mf = await P.get_form(sp.mega_forms[0]) if sp.mega_forms else None
    return BMon(name=name or f"Mon{sid}", species_id=sid, level=level, form=form,
                ivs={k: rng.randint(0, 31) for k in ("hp", "atk", "def", "spa", "spd", "spe")},
                moves=moves, ability=form.abilities[0][0], mega_form=mf)


def team(ids, level, rng):
    return [run(mk(i, level, rng)) for i in ids]


def play(seed, gimmicks=("mega", "z", "dynamax", "tera"), max_turns=300):
    rng = random.Random(seed)
    ids = [rng.randint(1, 1025) for _ in range(6)]
    a = Side("A", team(ids[:3], rng.randint(20, 60), rng), gimmicks=[rng.choice(gimmicks)], ai=True)
    b = Side("B", team(ids[3:], rng.randint(20, 60), rng), gimmicks=[rng.choice(gimmicks)], ai=True)
    bt = Battle(a, b, rng)
    bt.start()
    turns = 0
    while bt.winner is None and turns < max_turns:
        acts = [bt.ai_choose(0), bt.ai_choose(1)]
        # randomly force gimmick usage to exercise code paths
        for i in (0, 1):
            if acts[i][0] == "move" and rng.random() < 0.3:
                g = bt.legal_actions(i)["gimmicks"]
                if g:
                    acts[i] = ("move", acts[i][1], rng.choice(g))
        bt.step(acts)
        for i in list(bt.pending):
            bt.replace(i, bt.ai_replacement(i))
        for s in bt.sides:
            for m in s.team:
                assert 0 <= m.hp <= m.max_hp, (m.name, m.hp, m.max_hp)
        turns += 1
    return bt, turns


def test_type_chart():
    assert type_mult("fire", ["grass"]) == 2
    assert type_mult("fire", ["water"]) == 0.5
    assert type_mult("normal", ["ghost"]) == 0
    assert type_mult("electric", ["ground", "flying"]) == 0
    assert type_mult("ice", ["dragon", "flying"]) == 4
    assert type_mult("fairy", ["dragon"]) == 2
    assert type_mult("dragon", ["fairy"]) == 0


def test_tables():
    assert z_power(40) == 100 and z_power(150) == 200
    assert max_power(60, "fire") == 110 and max_power(60, "fighting") == 80


def test_many_battles_no_crash():
    finished = 0
    gim_seen = set()
    for seed in range(400):
        bt, turns = play(seed)
        if bt.winner is not None:
            finished += 1
        for s in bt.sides:
            if s.used_gimmick:
                gim_seen.add(s.used_gimmick)
    assert finished >= 380, finished
    assert gim_seen == {"mega", "z", "dynamax", "tera"}, gim_seen


def test_each_gimmick_effect():
    rng = random.Random(1)
    # Mega: id 6 has a mega form in the mock dex
    a = Side("A", [run(mk(6, 50, rng))], gimmicks=["mega"])
    b = Side("B", [run(mk(7, 50, rng))], gimmicks=[])
    bt = Battle(a, b, rng)
    bt.start()
    before = dict(a.mon.stats)
    bt.step([("move", 0, "mega"), ("move", 0, None)])
    assert a.mon.mega and a.mon.stats["atk"] > before["atk"] and a.mon.name.startswith("Mega")
    # Tera
    a = Side("A", [run(mk(8, 50, rng))], gimmicks=["tera"])
    a.mon.tera_type = "dragon"
    bt = Battle(a, Side("B", [run(mk(9, 50, rng))]), rng)
    bt.start()
    bt.step([("move", 0, "tera"), ("move", 0, None)])
    assert a.mon.types == ["dragon"] and a.mon.tera
    # Dynamax: HP x1.5 then reverts after 3 turns
    a = Side("A", [run(mk(10, 50, rng))], gimmicks=["dynamax"])
    b = Side("B", [run(mk(11, 50, rng))])
    for m in b.team:
        m.stats["atk"] = m.stats["spa"] = 1
    bt = Battle(a, b, rng)
    bt.start()
    base_hp = a.mon.max_hp
    bt.step([("move", 0, "dynamax"), ("move", 0, None)])
    assert a.mon.max_hp == int(base_hp * 1.5) and a.mon.dynamaxed
    assert any("Max" in l for l in bt.log)
    bt.step([("move", 0, None), ("move", 0, None)])
    bt.step([("move", 0, None), ("move", 0, None)])
    assert not a.mon.dynamaxed and a.mon.max_hp == base_hp
    # Z-move
    a = Side("A", [run(mk(12, 50, rng))], gimmicks=["z"])
    bt = Battle(a, Side("B", [run(mk(13, 50, rng))]), rng)
    bt.start()
    bt.step([("move", 1, "z"), ("move", 0, None)])
    assert any("unleashed" in l for l in bt.log), bt.log
    assert a.used_gimmick == "z"
    # one gimmick per battle
    assert not bt.can_gimmick(0, "z")


def test_status_and_ability():
    rng = random.Random(3)
    a = Side("A", [run(mk(14, 50, rng))])
    b = Side("B", [run(mk(15, 50, rng))])
    bt = Battle(a, b, rng)
    bt.start()
    b.mon.types = ["normal"]
    assert bt.inflict(b.mon, "brn")
    hp = b.mon.hp
    assert not bt.inflict(b.mon, "psn")      # already has a status -> no-op
    bt.log = []
    bt.end_of_turn()
    assert b.mon.hp < hp
    # intimidate lowers attack on entry
    a2 = Side("A", [run(mk(16, 50, rng))])
    a2.mon.ability = "intimidate"
    b2 = Side("B", [run(mk(17, 50, rng))])
    b2.mon.ability = "pressure"
    bt2 = Battle(a2, b2, rng)
    bt2.start()
    assert b2.mon.stages["atk"] == -1
    # drizzle sets rain
    a3 = Side("A", [run(mk(18, 50, rng))])
    a3.mon.ability = "drizzle"
    bt3 = Battle(a3, Side("B", [run(mk(19, 50, rng))]), rng)
    bt3.start()
    assert bt3.weather == "rain"


def test_catching():
    rng = random.Random(5)
    m = run(mk(20, 10, rng))
    m.hp = 1
    caught = sum(catch_attempt(m, 255, 2.0, rng)[0] for _ in range(100))
    assert caught == 100
    m2 = run(mk(21, 10, rng))
    c2 = sum(catch_attempt(m2, 3, 1.0, rng)[0] for _ in range(300))
    assert c2 < 30


def test_switching_and_faint_replacement():
    rng = random.Random(7)
    a = Side("A", team([30, 31, 32], 30, rng))
    b = Side("B", team([33, 34], 30, rng))
    bt = Battle(a, b, rng)
    bt.start()
    bt.step([("switch", 2), ("move", 0, None)])
    assert a.active == 2
    a.mon.hp = 1
    b.mon.stats["spe"] = 9999
    bt.step([("move", 0, None), ("move", 0, None)])
    assert bt.pending == [0] or a.mon.hp > 0 or bt.winner is not None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
