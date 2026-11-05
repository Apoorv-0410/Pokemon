import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import run
from pokebot import data, game, world
from pokebot.data import Evo
from pokebot.db import DB
from test_flows import drive, fresh, make_player

data.set_provider(data.MockProvider())


def test_shiny_odds_and_flag(monkeypatch):
    assert game.SHINY_ODDS == 4096
    monkeypatch.setattr(game, "SHINY_ODDS", 1)
    assert run(game.new_record(1, 5, random.Random(1)))["shiny"] == 1
    assert run(game.new_record(1, 5, random.Random(1), allow_shiny=False))["shiny"] == 0


def test_balls_have_conditions():
    base = dict(turn=1, foe_level=40, my_level=40, foe_types=["normal"], foe_speed=50, night=False, cave=False, caught_before=False)
    m = lambda k, **kw: game.ball_multiplier(k, **{**base, **kw})
    assert m("poke-ball") == 1 and m("ultra-ball") == 2
    assert m("quick-ball") == 5 and m("quick-ball", turn=2) == 1
    assert m("timer-ball", turn=20) == 4 and m("timer-ball", turn=1) < 2
    assert m("repeat-ball", caught_before=True) == 3.5 and m("repeat-ball") == 1
    assert m("nest-ball", foe_level=10) > 3 and m("nest-ball") == 1
    assert m("net-ball", foe_types=["water"]) == 3.5 and m("net-ball") == 1
    assert m("dusk-ball", night=True) == 3 and m("dusk-ball", cave=True) == 3 and m("dusk-ball") == 1
    assert m("level-ball", my_level=170) == 8 and m("level-ball", my_level=41) == 2
    assert m("fast-ball", foe_speed=120) == 4
    assert world.ITEMS["master-ball"].price == 0 and m("master-ball") == 255


def test_stone_and_friendship_evolution():
    db, g = fresh()
    mid = run(make_player(db, 1, "Ash", species=6, level=20))            # mock 6: stone evolution
    db.add_item(1, "fire-stone", 1)
    rec = db.mon(mid)
    opts = run(game.evolution_options(rec))
    assert opts[0]["kind"] == "stone" and opts[0]["stone"] == "fire-stone" and opts[0]["to"] == 7
    run(game.evolve_mon(db, rec, 7))
    assert db.mon(mid)["species_id"] == 7 and db.dex_caught(1, 7)
    mid2 = run(make_player(db, 1, "Ash", species=21, level=20))          # mock 21: friendship evolution
    rec2 = db.mon(mid2)
    assert not run(game.evolution_options(rec2))[0]["ready"]
    db.set_friendship(mid2, 225)
    assert run(game.evolution_options(db.mon(mid2)))[0]["ready"]


def test_location_evolutions_map_to_stones():
    assert run(game.evo_stone(Evo(470, "stone"))) == "leaf-stone"        # Leafeon
    assert run(game.evo_stone(Evo(471, "stone"))) == "ice-stone"         # Glaceon
    assert run(game.evo_stone(Evo(462, "stone"))) == "thunder-stone"     # Magnezone
    assert run(game.evo_stone(Evo(5, "stone", stone="moon-stone"))) == "moon-stone"
    assert run(game.evo_stone(Evo(9, "stone"))) in {s for s in game.STONE_FOR_TYPE.values()}


def test_rare_candy_and_evs_caps_and_friendship():
    db, g = fresh()
    mid = run(make_player(db, 1, "Ash", species=3, level=30))
    rec = db.mon(mid)
    run(game.use_rare_candy(db, rec))
    assert db.mon(mid)["level"] == 31
    game.apply_evs(db, rec, {"atk": 200})
    game.apply_evs(db, rec, {"atk": 200, "spe": 300})
    evs = db.mon(mid)["evs"]
    assert evs["atk"] == 252 and sum(evs.values()) <= game.EV_TOTAL and evs["spe"] == 252 - 0 - 0 or evs["spe"] <= 252
    assert sum(evs.values()) <= 510
    db.set_friendship(mid, 999)
    assert db.mon(mid)["friendship"] == 255
    assert run(game.max_hp_of(dict(rec, evs={"hp": 252}))) > run(game.max_hp_of(dict(rec, evs={})))


def test_ev_training_spawns_and_wins_grant_evs():
    db, g = fresh(4)
    mid = run(make_player(db, 1, "Ash", species=3, level=60))
    assert not db.start_training(1, "atk", 10 ** 9, 30)
    assert db.start_training(1, "atk", 200, 30)
    assert db.money(1) == 3000 - 200 and db.player(1)["ev_left"] == 30
    rng = random.Random(1)
    for _ in range(15):
        w = run(game.wild_record("kanto", 0, rng, ev_stat="atk"))
        assert run(data.get_provider().get_form(w["species_id"])).ev_yield.get("atk")
    for _ in range(30):
        db.update_mon(mid, hp=run(game.max_hp_of(db.mon(mid))))
        s, _ = run(g.start_wild(1, 1))
        assert "EV training" in s.title
        run(drive(g, 1))
    assert db.player(1)["ev_left"] == 0
    assert db.mon(mid)["evs"].get("atk", 0) > 0 or db.player(1)["losses"] == 30


def test_wins_raise_friendship_losses_lower_it():
    db, g = fresh(5)
    mid = run(make_player(db, 1, "Ash", species=3, level=70))
    s, _ = run(g.start_wild(1, 1))
    run(drive(g, 1))
    f = db.mon(mid)["friendship"]
    assert f != 70 and f in (74, 68)


def test_old_mons_table_is_migrated(tmp_path):
    path = str(tmp_path / "o.sqlite3")
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE players(user_id INTEGER PRIMARY KEY, name TEXT, region TEXT NOT NULL DEFAULT 'kanto', money INTEGER NOT NULL DEFAULT 0, last_daily TEXT, created REAL)")
    c.execute("CREATE TABLE mons(id INTEGER PRIMARY KEY AUTOINCREMENT, owner INTEGER NOT NULL, species_id INTEGER NOT NULL, species_name TEXT NOT NULL, nickname TEXT, level INTEGER NOT NULL, xp INTEGER NOT NULL, iv TEXT NOT NULL, moves TEXT NOT NULL, ability TEXT NOT NULL, tera_type TEXT NOT NULL, hp INTEGER NOT NULL, slot INTEGER, listed INTEGER NOT NULL DEFAULT 0, caught REAL)")
    c.execute("INSERT INTO players(user_id,name) VALUES(1,'Old')")
    c.execute("INSERT INTO mons(owner,species_id,species_name,level,xp,iv,moves,ability,tera_type,hp,slot) VALUES(1,25,'Pikachu',10,1000,'{}','[\"tackle\"]','static','electric',30,0)")
    c.commit()
    c.close()
    db = DB(path)
    m = db.party(1)[0]
    assert m["shiny"] == 0 and m["friendship"] == 70 and m["evs"] == {}
    assert db.dex_caught(1, 25)
    DB(path)


def test_gym_teams_are_stage_appropriate():
    async def go():
        p = data.get_provider()
        for r in world.REGIONS:
            for i in range(8):
                for rec in await game.gym_records(r.key, i):
                    sp = await p.get_species(rec["species_id"])
                    if sp.evolves_from:
                        prev = await p.get_species(sp.evolves_from)
                        e = next(e for e in prev.evos if e.to == sp.id)
                        need = e.level if e.kind == "level" else game.NON_LEVEL_STAGE
                        assert rec["level"] >= need or prev.baby, (r.key, i, rec["species_id"], rec["level"])
                    assert not rec["shiny"]
    run(go())
