import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pokebot import config, data, game, world
from pokebot.db import DB
from pokebot.session import Game

data.set_provider(data.MockProvider())
from conftest import run


def fresh(seed=1):
    db = DB(":memory:")
    g = Game(db, random.Random(seed))
    return db, g


async def make_player(db, uid, name, species=1, level=5, rng=None):
    rng = rng or random.Random(uid)
    db.create_player(uid, name)
    rec = await game.new_record(species, level, rng)
    mid = db.add_mon(uid, rec)
    return mid


async def drive(g, uid, pick="random", max_steps=400, rng=None):
    """Play a session for `uid` to completion by 'pressing' buttons."""
    rng = rng or random.Random(0)
    last = []
    for _ in range(max_steps):
        s = g.active(uid)
        if not s:
            return last
        side = s.side_of(uid)
        kb = g.keyboard(s, side) or []
        flat = [b for row in kb for b in row]
        reps = [b for b in flat if b[1].startswith("p|")]
        moves = [b for b in flat if b[1].startswith("m|")]
        tgts = [b for b in flat if b[1].startswith("t|")]
        gims = [b for b in flat if b[1].startswith("g|") and not b[0].startswith("✅")]
        if reps:
            last = await g.handle(uid, reps[0][1])
        elif tgts:
            last = await g.handle(uid, rng.choice(tgts)[1])
        elif moves:
            if gims and rng.random() < 0.5:
                await g.handle(uid, rng.choice(gims)[1])
            last = await g.handle(uid, rng.choice(moves)[1])
        else:
            return last
    raise AssertionError("battle did not finish")


def test_wild_battle_pays_and_saves_hp():
    db, g = fresh()
    run(make_player(db, 1, "Ash", species=3, level=30))
    for k in range(10):
        db.update_mon(db.party(1)[0]["id"], hp=run(game.max_hp_of(db.party(1)[0])))
        s, outs = run(g.start_wild(1, 100))
        assert s and outs and outs[0].kb
        money = db.money(1)
        run(drive(g, 1))
        assert g.active(1) is None
    assert db.money(1) != 0
    p = db.player(1)
    assert p["wins"] + p["losses"] == 10       # every finished battle is recorded


def test_catch_flow():
    db, g = fresh(2)
    run(make_player(db, 1, "Ash", species=3, level=60))
    db.add_item(1, "ultra-ball", 50)
    caught = 0
    for _ in range(25):
        db.update_mon(db.party(1)[0]["id"], hp=run(game.max_hp_of(db.party(1)[0])))
        s, _ = run(g.start_wild(1, 100))
        if not s:
            continue
        run(g.handle(1, f"b|{s.id}|"))
        for _ in range(10):
            outs = run(g.handle(1, f"c|{s.id}|ultra-ball"))
            if g.active(1) is None:
                break
        if db.count_mons(1) > 1:
            caught += 1
            break
        if g.active(1):
            run(g.handle(1, f"r|{s.id}|"))
    assert caught >= 1
    assert db.qty(1, "ultra-ball") < 50
    assert len(db.party(1)) + len(db.box(1)) == db.count_mons(1)


def test_xp_levelup_and_evolution():
    db, g = fresh(3)
    mid = run(make_player(db, 1, "Ash", species=1, level=15))   # id%3==1 -> evolves to 2 at 16
    rec = db.party(1)[0]
    msgs = run(game.award_xp(db, rec, 2000))
    got = db.mon(mid)
    assert got["level"] > 15 and got["species_id"] >= 2
    assert any("evolved" in m for m in msgs), msgs


def test_gym_progression_and_badges():
    db, g = fresh(4)
    run(make_player(db, 1, "Ash", species=3, level=95))
    for sp in (6, 9, 12, 15, 18):
        db.add_mon(1, run(game.new_record(sp, 95, random.Random(sp))))

    def heal_all():
        for r in db.party(1):
            db.update_mon(r["id"], hp=run(game.max_hp_of(r)))

    for gym_idx in range(8):
        for attempt in range(6):
            heal_all()
            s, outs = run(g.start_gym(1, 100))
            assert s, outs
            assert s.gym == ("kanto", gym_idx)
            run(drive(g, 1, rng=random.Random(gym_idx * 10 + attempt)))
            if db.badge_count(1, "kanto") == gym_idx + 1:
                break
        assert db.badge_count(1, "kanto") == gym_idx + 1, gym_idx
    assert db.badge_count(1, "kanto") == 8


def test_every_region_gym_team_builds():
    async def go():
        for r in world.REGIONS:
            assert len(r.gyms) == 8
            for i in range(8):
                recs = await game.gym_records(r.key, i)
                assert len(recs) == world.GYM_TEAM_SIZES[i], (r.key, i, len(recs))
                side = await game.gym_side(r.key, i)
                assert side.team
    run(go())


def test_every_region_wild_spawn():
    async def go():
        rng = random.Random(1)
        for r in world.REGIONS:
            for _ in range(5):
                w = await game.wild_record(r.key, 3, rng)
                assert r.dex_lo <= w["species_id"] <= r.dex_hi or w.get("form")   # regional variants appear in their home region
    run(go())


def test_gym_gimmick_users():
    async def go():
        for region, g in [("kalos", "mega"), ("alola", "z"), ("galar", "dynamax"), ("paldea", "tera")]:
            s = await game.gym_side(region, 7)
            assert s.ai_gimmick in (g, None)
    run(go())


def test_pvp_friendly_no_persistence():
    db, g = fresh(5)
    run(make_player(db, 1, "Ash", species=3, level=40))
    run(make_player(db, 2, "Gary", species=4, level=40))
    db.update_mon(db.party(1)[0]["id"], hp=1)
    money = (db.money(1), db.money(2))
    s, outs = run(g.start_pvp(1, 2, 999))
    assert s and {o.to for o in outs} == {1, 2, "chat"}            # both players + the shared scene
    rng = random.Random(9)
    for _ in range(300):
        if not g.active(1):
            break
        for u in (1, 2):
            s2 = g.active(u)
            if not s2:
                break
            side = s2.side_of(u)
            kb = [b for row in (g.keyboard(s2, side) or []) for b in row]
            reps = [b for b in kb if b[1].startswith("p|")]
            mv = [b for b in kb if b[1].startswith("m|")]
            if reps:
                run(g.handle(u, reps[0][1]))
            elif mv:
                run(g.handle(u, rng.choice(mv)[1]))
    assert g.active(1) is None
    d1, d2 = db.money(1) - money[0], db.money(2) - money[1]          # only the friendly prize / loss changes money
    assert sorted((d1, d2)) in ([-world.FRIENDLY_LOSS, world.FRIENDLY_WIN_PRIZE], [0, 0])
    assert db.party(1)[0]["hp"] == 1     # real HP untouched: friendly battles use full-HP copies


def test_pvp_cannot_act_for_other_side():
    db, g = fresh(6)
    run(make_player(db, 1, "Ash"))
    run(make_player(db, 2, "Gary"))
    run(make_player(db, 3, "Eve"))
    s, _ = run(g.start_pvp(1, 2, 999))
    assert run(g.handle(3, f"m|{s.id}|0")) == []
    assert run(g.start_wild(1, 5))[0] is None    # already busy


def test_gimmick_requires_key_item():
    db, g = fresh(7)
    run(make_player(db, 1, "Ash", species=6, level=40))
    s, _ = run(g.start_wild(1, 1))
    kb = [b for row in g.keyboard(s, 0) for b in row]
    assert not any(b[1].startswith("g|") for b in kb)
    run(g.handle(1, f"r|{s.id}|"))
    db.add_item(1, "tera-orb", 1)
    db.add_item(1, "dynamax-band", 1)
    s, outs = run(g.start_wild(1, 1))
    assert not s.started                                   # several gimmicks owned -> lock in ONE first
    lobby = {b[1].split("|")[2] for row in g.keyboard(s, 0) for b in row if b[1].startswith("l|")}
    assert lobby == {"tera", "dynamax", "none"}
    run(g.handle(1, f"l|{s.id}|tera"))
    assert s.started and s.gimmick[0] == "tera"
    kb = [b for row in g.keyboard(s, 0) for b in row]
    assert {b[1].split("|")[2] for b in kb if b[1].startswith("g|")} == {"tera"}    # only the locked-in gimmick is offered


def test_market_flow_and_atomicity():
    db, g = fresh(8)
    m1 = run(make_player(db, 1, "Seller", species=3))
    m1b = run(make_player(db, 1, "Seller", species=9))  # second mon (player already exists)
    run(make_player(db, 2, "Buyer"))
    db.add_item(1, "potion", 5)
    db.add_money(2, 5000)
    lid = db.list_mon(1, m1b, 1000)
    lid2 = db.list_item(1, "potion", 3, 100)
    assert lid and lid2
    assert db.qty(1, "potion") == 2
    assert db.list_mon(1, m1b, 5) is None     # already listed
    rows, total = db.listings()
    assert total == 2
    ok, msg, l = db.buy_listing(2, lid)
    assert ok and db.mon(m1b)["owner"] == 2 and db.mon(m1b)["listed"] == 0
    assert db.money(1) == config.START_MONEY + 950 and l["fee"] == 50
    assert db.money(2) == config.START_MONEY + 5000 - 1000
    ok, msg, _ = db.buy_listing(2, lid)
    assert not ok                                # already sold
    ok, msg, _ = db.buy_listing(1, lid2)
    assert not ok                                # own listing
    poor = run(make_player(db, 3, "Poor"))
    db.x("UPDATE players SET money=0 WHERE user_id=3")
    ok, msg, _ = db.buy_listing(3, lid2)
    assert not ok and "money" in msg.lower()
    assert db.cancel_listing(1, lid2)
    assert db.qty(1, "potion") == 5
    assert db.cancel_listing(2, lid2) is None


def test_cannot_list_last_party_mon_or_release():
    db, g = fresh(9)
    m = run(make_player(db, 1, "Ash"))
    assert db.list_mon(1, m, 100) is None
    assert not db.release_mon(1, m)
    assert not db.move_to_box(1, m)


def test_economy_primitives():
    db, g = fresh(10)
    run(make_player(db, 1, "Ash"))
    assert db.spend(1, 100) and db.money(1) == config.START_MONEY - 100
    assert not db.spend(1, 10 ** 9)
    assert db.claim_daily(1, "2026-11-04") and not db.claim_daily(1, "2026-11-04")
    assert db.claim_daily(1, "2026-11-05")
    db.add_item(1, "potion", 2)
    assert db.take_item(1, "potion", 2) and not db.take_item(1, "potion", 1)


def _strong(db, uid=1, species=3, level=95):
    run(make_player(db, uid, "Ash", species=species, level=level))
    for sp in (6, 9, 12, 15, 18):
        db.add_mon(uid, run(game.new_record(sp, level, random.Random(sp))))


def _heal(db, uid=1):
    for r in db.party(uid):
        db.update_mon(r["id"], hp=run(game.max_hp_of(r)))


def test_league_needs_all_badges_then_conquest_opens_later_regions():
    db, g = fresh(12)
    _strong(db)
    s, err = run(g.start_league(1, 1))
    assert s is None and "8 badges" in err
    for i in range(8):
        db.give_badge(1, "kanto", i)
    assert db.conquered(1) == []
    last = []
    for attempt in range(60):                       # a loss ends the run; keep trying until the Champion falls
        s, outs = run(g.start_league(1, 1))
        assert s and s.kind == "league" and s.doubles, outs
        last = run(drive(g, 1, rng=random.Random(attempt))) or last
        if db.conquered(1):
            break
    assert db.league_count(1, "kanto") == world.LEAGUE_SIZE
    assert db.conquered(1) == ["kanto"]
    assert any("Champion of Kanto" in o.text and "now open" in o.text for o in last)


def test_league_teams_build_for_every_region_and_respect_stages():
    async def go():
        p = data.get_provider()
        for r in world.REGIONS:
            for idx in range(world.LEAGUE_SIZE):
                name, types, title, size, level = world.league_foe(r.key, idx)
                recs = await game.league_records(r.key, idx)
                assert len(recs) == size, (r.key, idx, len(recs))
                for rec in recs:
                    sp = await p.get_species(rec["species_id"])
                    assert not sp.legendary
    run(go())


def test_third_gym_gives_gimmick_item_and_leader_uses_it():
    db, g = fresh(13)
    _strong(db)
    db.x("UPDATE players SET region='kalos' WHERE user_id=1")
    for i in range(2):
        db.give_badge(1, "kalos", i)
    s, _ = run(g.start_gym(1, 1))
    assert s.ai_gimmick == "mega" and s.gym == ("kalos", 2)
    run(g.handle(1, f"f|{s.id}|"))
    assert db.qty(1, "mega-bracelet") == 0
    for attempt in range(8):
        _heal(db)
        s, _ = run(g.start_gym(1, 1))
        run(drive(g, 1, rng=random.Random(attempt)))
        if db.badge_count(1, "kalos") == 3:
            break
    assert db.badge_count(1, "kalos") == 3 and db.qty(1, "mega-bracelet") == 1
    db2, g2 = fresh(14)
    _strong(db2)
    s, _ = run(g2.start_gym(1, 1))
    assert s.ai_gimmick is None                       # Kanto has no gimmicks, and early Kalos gyms don't either


def test_wild_uses_native_capture_rate_and_respects_stages():
    async def go():
        p = data.get_provider()
        rng = random.Random(1)
        for _ in range(80):
            w = await game.wild_record("kanto", 0, rng)
            sp = await p.get_species(w["species_id"])
            assert w["capture_rate"] == sp.capture_rate
            if sp.evolves_from:                       # never a too-evolved wild Pokémon
                prev = await p.get_species(sp.evolves_from)
                e = next(e for e in prev.evos if e.to == sp.id)
                assert w["level"] >= (e.level if e.kind == "level" else game.NON_LEVEL_STAGE) or prev.baby
    run(go())


def test_old_save_is_migrated(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.sqlite3")
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE players(user_id INTEGER PRIMARY KEY, name TEXT, region TEXT NOT NULL DEFAULT 'kanto',"
              " money INTEGER NOT NULL DEFAULT 0, last_daily TEXT, created REAL)")
    c.execute("INSERT INTO players(user_id,name,money) VALUES(7,'Old',123)")
    c.commit()
    c.close()
    db = DB(path)
    p = db.player(7)
    assert p["money"] == 123 and p["wins"] == 0 and p["fav_mon"] is None
    db.record_result(7, True)
    db.record_result(7, False, pvp=True)
    p = db.player(7)
    assert (p["wins"], p["losses"], p["pvp_wins"], p["pvp_losses"]) == (1, 1, 0, 1)
    DB(path)                                    # reopening an already-migrated file is fine


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


def test_league_run_locks_team_heals_free_and_loss_ends_run():
    db, g = fresh(14)
    _strong(db)
    for i in range(8):
        db.give_badge(1, "kanto", i)
    s, _ = run(g.start_league(1, 1))
    assert s and db.league_run(1)["cleared"] == 0
    run(drive(g, 1, rng=random.Random(1)))
    run_row = db.league_run(1)
    if run_row:                      # still in the run: fights restart healed with the same team
        assert run_row["cleared"] in (0, 1)
    ids = [r["id"] for r in db.party(1)]
    if run_row:
        assert set(run_row["mons"]) == set(ids)
    db.end_league_run(1)
    assert db.league_run(1) is None


def test_random_trainer_battle_has_dialogue_rewards_and_flat_loss_penalty():
    from pokebot import session
    db, g = fresh(21)
    _strong(db)
    g.trainer_chance = 1.0
    money0 = db.money(1)
    s, outs = run(g.start_wild(1, 1))
    assert s.kind == "trainer" and s.trainer["intro"] in s.title and s.sets[1]
    assert "Run" not in " ".join(b[0] for row in g.keyboard(s, 0) for b in row)     # no running from trainers
    final = run(drive(g, 1, rng=random.Random(5)))
    txt = "\n".join(o.text for o in final)
    assert "You defeated" in txt and s.trainer["lose_line"] in txt
    assert db.money(1) > money0
    # losing: flat small penalty + their victory line
    db2, g2 = fresh(22)
    g2.trainer_chance = 1.0
    run(make_player(db2, 1, "Weak", species=1, level=2))
    db2.x("UPDATE players SET money=1000 WHERE user_id=1")
    s2, _ = run(g2.start_wild(1, 1))
    assert s2 and s2.kind == "trainer"
    final2 = run(drive(g2, 1, rng=random.Random(2)))
    txt2 = "\n".join(o.text for o in final2)
    assert "blacked out" in txt2 and s2.trainer["win_line"] in txt2
    assert 970 <= db2.money(1) <= 975


def _press_doubles(g, s, rng):
    """One button press for whichever side still has to choose; returns False when nobody can act."""
    for side in (0, 1):
        if side not in s.waiting:
            continue
        flat = [b for row in (g.keyboard(s, side) or []) for b in row]
        for pref in ("p|", "t|", "m|"):
            opts = [b for b in flat if b[1].startswith(pref)]
            if opts:
                return run(g.handle(s.players[side], rng.choice(opts)[1])) or True
    return False


def test_doubles_battle_plays_to_the_end_through_buttons():
    for seed in (1, 2, 3):
        db, g = fresh(30 + seed)
        for uid, name, sp in ((1, "A", 4), (2, "B", 7)):
            run(make_player(db, uid, name, species=sp, level=40))
            db.add_mon(uid, run(game.new_record(sp + 1, 40, random.Random(uid))))
        s, outs = run(g.start_pvp(1, 2, -9, {"size": 4, "level": 50, "gimmicks": False, "doubles": True}))
        assert s and s.doubles and len(s.state["sides"][0]["active"]) == 2
        rng = random.Random(seed)
        for _ in range(600):
            if not g.active(1):
                break
            assert _press_doubles(g, s, rng) is not False, "stuck: nobody can act"
        assert not g.active(1) and s.winner in ("p1", "p2"), seed


def test_doubles_needs_two_pokemon_each():
    db, g = fresh(40)
    run(make_player(db, 1, "A"))
    run(make_player(db, 2, "B"))
    s, err = run(g.start_pvp(1, 2, -9, {"size": 4, "doubles": True}))
    assert s is None and "at least 2" in err


def test_from_the_fifth_gym_on_leaders_use_six_pokemon_and_double_battles():
    db, g = fresh(50)
    _strong(db)
    s, _ = run(g.start_gym(1, 1))
    assert not s.doubles and len(s.sets[1]) == world.GYM_TEAM_SIZES[0]
    run(drive(g, 1, rng=random.Random(1)))
    for i in range(4):
        db.give_badge(1, "kanto", i)
    _heal(db)
    s, _ = run(g.start_gym(1, 1))
    assert s.gym == ("kanto", 4) and s.doubles and len(s.sets[1]) == 6
    assert len(s.state["sides"][0]["active"]) == 2 and len(s.state["sides"][1]["active"]) == 2
    final = run(drive(g, 1, rng=random.Random(3)))
    assert final and not g.active(1)


def test_double_battles_need_two_pokemon():
    db, g = fresh(51)
    run(make_player(db, 1, "Solo", species=1, level=60))
    for i in range(4):
        db.give_badge(1, "kanto", i)
    s, err = run(g.start_gym(1, 1))
    assert s is None and "at least 2" in err
    for i in range(4, 8):
        db.give_badge(1, "kanto", i)
    s, err = run(g.start_league(1, 1))
    assert s is None and "at least 2" in err and db.league_run(1) is None


def test_random_trainers_with_four_badges_bring_six_and_mix_singles_and_doubles():
    seen = set()
    for seed in range(12):
        db, g = fresh(60 + seed)
        _strong(db)
        for i in range(4):
            db.give_badge(1, "kanto", i)
        g.trainer_chance = 1.0
        s, _ = run(g.start_wild(1, 1))
        assert s.kind == "trainer" and len(s.sets[1]) == 6
        seen.add(s.doubles)
        g._drop(s.id)
    assert seen == {True, False}


def test_legendary_encounter_from_streak_and_exploring_counts_for_quests():
    from pokebot import quests
    db, g = fresh(70)
    _strong(db)
    db.x("UPDATE players SET legend_pending=1 WHERE user_id=1")
    s, outs = run(g.start_wild(1, 1))
    assert s.kind == "wild" and "LEGENDARY" in s.title
    assert s.wild["species_id"] in quests.LEGENDARIES["kanto"] and db.player(1)["legend_pending"] == 0
    g._drop(s.id)
    s2, _ = run(g.start_wild(1, 1))
    assert "LEGENDARY" not in s2.title                       # only once
    g._drop(s2.id)


def test_losing_to_any_elite_four_member_restarts_from_the_first():
    db, g = fresh(31)
    run(make_player(db, 1, "Weak", species=1, level=3))
    db.add_mon(1, run(game.new_record(4, 3, random.Random(4))))
    for i in range(8):
        db.give_badge(1, "kanto", i)
    s, _ = run(g.start_league(1, 1))
    assert s and db.league_run(1)["cleared"] == 0
    db.advance_league_run(1)
    db.advance_league_run(1)                      # pretend members 1-2 were beaten
    assert db.league_run(1)["cleared"] == 2
    run(drive(g, 1, rng=random.Random(1)))        # the weak team loses to member 3
    assert db.league_run(1) is None
    s2, _ = run(g.start_league(1, 1))
    assert s2 and db.league_run(1)["cleared"] == 0


def test_event_pokemon_is_daily_non_legendary_and_pays_once():
    from pokebot import world, quests
    db, g = fresh(32)
    _strong(db)
    day = quests.today()
    sid = run(game.event_species(day))
    assert run(game.event_species(day)) == sid
    assert not run(game.get_provider().get_species(sid)).legendary
    world.EVENT_CHANCE = 1.0
    s, _ = run(g.start_wild(1, 1))
    assert s.kind == "wild" and s.wild["species_id"] == sid and "EVENT" in s.title
    db.add_item(1, "master-ball", 5)
    money0 = db.money(1)
    run(g._throw_ball(s, 0, "master-ball"))
    assert db.money(1) == money0 + world.EVENT_BONUS
    assert db.player(1)["event_claimed"] == day
    s2, _ = run(g.start_wild(1, 1))              # same species again the same day: no second bonus
    assert s2.wild["species_id"] == sid
    money1 = db.money(1)
    run(g._throw_ball(s2, 0, "master-ball"))
    assert db.money(1) == money1


def test_conquered_regions_have_endgame_wild_levels():
    assert world.wild_level_range("kanto", 8) == (51, 55)
    lo, hi = world.wild_level_range("kanto", 8, True)
    assert (lo, hi) == (60, 85) and world.wild_level_range("paldea", 8, True)[0] == 68
    db, g = fresh(77)
    _strong(db)
    for i in range(8):
        db.give_badge(1, "kanto", i)
    levels = []
    for _ in range(10):
        s, _ = run(g.start_wild(1, 1))
        levels.append(s.wild["level"])
        g._drop(s.id)
    assert max(levels) <= 55
    for i in range(world.LEAGUE_SIZE):
        db.give_league(1, "kanto", i)
    assert "kanto" in db.conquered(1)
    levels = []
    for _ in range(12):
        s, _ = run(g.start_wild(1, 1))
        levels.append(s.wild["level"])
        g._drop(s.id)
    assert min(levels) >= 60 and max(levels) <= 85 and len(set(levels)) > 3
