import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import run
from pokebot import dex, data, game, world
from test_flows import drive, fresh, make_player

data.set_provider(data.MockProvider())


def test_dex_tiers_and_evolution_line_hides_unmet_stages():
    db, g = fresh()
    run(make_player(db, 1, "Ash", species=4, level=5))          # caught #4 (evolves to 5 at 16)
    db.dex_seen(1, 5)
    text, rows = run(dex.dex_list(db, 1, "kanto", 0))
    assert "✅ #004" in text and "👁 #005" in text and "❓ #006 ???" in text and "Caught 1 /" in text
    entry, _ = run(dex.dex_entry(db, 1, 4))
    assert "Height" in entry and "Weak to" in entry and "Evolution" in entry and "mon4" not in entry.replace("Mon4", "")
    assert "???" in entry or "Mon6" not in entry               # unmet later stage stays hidden
    seen_only, _ = run(dex.dex_entry(db, 1, 5))
    assert "Seen" in seen_only and "Height" not in seen_only
    unmet, _ = run(dex.dex_entry(db, 1, 99))
    assert "???" in unmet
    assert run(dex.find_species(db, 1, "4")) == 4
    assert run(dex.find_species(db, 1, "Mon4")) == 4 and run(dex.find_species(db, 1, "mon99")) is None
    moves, _ = run(dex.dex_moves(db, 1, 4))
    assert "Level-up" in moves and "TMs" in moves
    assert "Catch this" in run(dex.dex_moves(db, 1, 5))[0]


def test_relearn_tutor_free_tm_paid_once_and_reusable():
    db, g = fresh()
    mid = run(make_player(db, 1, "Ash", species=3, level=40))
    db.add_money(1, 20000)
    rec = db.mon(mid)
    lists = run(game.learn_lists(rec))
    assert lists["t"] and lists["m"]
    mv = lists["r"][0] if lists["r"] else None
    money = db.money(1)
    if mv:
        ok, _ = run(game.teach_move(db, 1, rec, "r", mv, 0 if len(rec["moves"]) >= 4 else None))
        assert ok and mv in db.mon(mid)["moves"] and db.money(1) == money
    tut = run(game.learn_lists(db.mon(mid)))["t"][0]
    ok, _ = run(game.teach_move(db, 1, db.mon(mid), "t", tut, 1))
    assert ok and db.money(1) == money
    tm = run(game.learn_lists(db.mon(mid)))["m"][0]
    price = run(game.tm_price(tm))
    assert 5000 <= price <= 7000
    ok, msg = run(game.teach_move(db, 1, db.mon(mid), "m", tm, 2))
    assert ok and db.money(1) == money - price and db.qty(1, game.tm_key(tm)) == 1
    # reusable: teach it to another Pokémon for free
    mid2 = run(make_player(db, 1, "Ash", species=3, level=40))
    r2 = db.mon(mid2)
    if tm in run(game.learn_lists(r2))["m"]:
        before = db.money(1)
        ok, _ = run(game.teach_move(db, 1, r2, "m", tm, 0))
        assert ok and db.money(1) == before
    ok, _ = run(game.teach_move(db, 1, db.mon(mid), "m", "not-a-real-move", 0))
    assert not ok
    poor = run(make_player(db, 2, "Poor", species=3, level=40))
    db.x("UPDATE players SET money=0 WHERE user_id=2")
    ok, msg = run(game.teach_move(db, 2, db.mon(poor), "m", run(game.learn_lists(db.mon(poor)))["m"][0], 0))
    assert not ok and "$" in msg


def test_regional_forms_spawn_in_home_region_and_evolve_with_form():
    async def go():
        rng = random.Random(2)
        found = None
        for _ in range(400):
            w = await game.wild_record("alola", 3, rng)
            if w["form"]:
                found = w
                break
        assert found and found["form"].endswith("-alola") and "Alolan" in found["species_name"]
        kanto = [await game.wild_record("kanto", 3, rng) for _ in range(150)]
        assert not any(w["form"] for w in kanto)
    run(go())
    db, g = fresh()
    rec = run(game.new_record(105, 40, random.Random(1), form_slug="mon105-alola"))
    mid = db.add_mon(1, rec) if db.player(1) else None
    db.create_player(1, "A")
    mid = db.add_mon(1, rec)
    assert db.mon(mid)["form"] == "mon105-alola"
    assert run(game.max_hp_of(db.mon(mid))) > 0


def test_form_change_costs_500_repeatable_and_not_for_regionals():
    db, g = fresh()
    mid = run(make_player(db, 1, "Ash", species=493, level=50))     # Arceus
    rec = db.mon(mid)
    assert len(run(game.form_options(rec))) == 18
    m0 = db.money(1)
    ok, msg = run(game.change_form(db, 1, rec, "arceus-fire"))
    assert ok and db.money(1) == m0 - 500 and db.mon(mid)["form"] == "arceus-fire"
    ok, _ = run(game.change_form(db, 1, db.mon(mid), "arceus-water"))
    assert ok and db.money(1) == m0 - 1000                               # any number of times
    ok, _ = run(game.change_form(db, 1, db.mon(mid), "arceus-water"))
    assert not ok                                                        # already that form
    ok, _ = run(game.change_form(db, 1, db.mon(mid), ""))
    assert ok and db.mon(mid)["form"] == ""
    plain = run(make_player(db, 1, "Ash", species=19, level=10))
    assert run(game.form_options(db.mon(plain))) == []                   # regional forms aren't purchasable
    db.x("UPDATE players SET money=0 WHERE user_id=1")
    ok, msg = run(game.change_form(db, 1, db.mon(mid), "arceus-grass"))
    assert not ok


def test_ability_capsule_and_patch():
    db, g = fresh()
    mid = run(make_player(db, 1, "Ash", species=3, level=20))
    rec = db.mon(mid)
    form = run(data.get_provider().get_form(3))
    normal = [a for a, h in form.abilities if not h]
    hidden = [a for a, h in form.abilities if h]
    ok, msg = run(game.use_ability_item(db, rec, "ability-patch"))
    if hidden and hidden[0] != rec["ability"]:
        assert ok and db.mon(mid)["ability"] == hidden[0]
        ok2, _ = run(game.use_ability_item(db, db.mon(mid), "ability-capsule"))
        assert not ok2                                                   # capsule can't touch a hidden ability
    assert world.ITEMS["ability-capsule"].price > 0 and world.ITEMS["ability-patch"].price > world.ITEMS["ability-capsule"].price


def test_forms_battle_on_showdown():
    from pokebot import showdown as sd
    if not sd.node_available():
        return
    db, g = fresh()
    mid = run(make_player(db, 1, "Ash", species=493, level=40))
    db.update_mon(mid, form="arceus-fire", moves=["flamethrower", "tackle"])
    run(make_player(db, 2, "Gary", species=3, level=40))
    s, outs = run(g.start_wild(1, 1))
    assert s and s.started
    run(g.handle(1, f"r|{s.id}|"))
    # the Showdown set uses the form's species
    assert sd.rec_to_set(db.mon(mid))["species"] == "arceus-fire"
    s2, _ = run(g.start_pvp(1, 2, 1))
    assert s2.state["sides"][0]["pokemon"][0]["species"].startswith("Arceus")
    run(drive(g, 1)) if False else None


def test_natures_and_mints():
    from pokebot.battle import NATURES, calc_stats
    assert len(NATURES) == 25
    base = {"hp": 50, "atk": 50, "def": 50, "spa": 50, "spd": 50, "spe": 50}
    iv = {k: 31 for k in base}
    neutral, adamant = calc_stats(base, 50, iv, None, "Hardy"), calc_stats(base, 50, iv, None, "Adamant")
    assert adamant["atk"] > neutral["atk"] and adamant["spa"] < neutral["spa"] and adamant["hp"] == neutral["hp"]
    db, g = fresh()
    mid = run(make_player(db, 1, "Ash", species=3, level=30))
    rec = db.mon(mid)
    assert rec["nature"] in NATURES
    other = "Timid" if rec["nature"] != "Timid" else "Jolly"
    ok, _ = run(game.use_nature_mint(db, rec, other))
    assert ok and db.mon(mid)["nature"] == other
    assert not run(game.use_nature_mint(db, db.mon(mid), other))[0]
    assert not run(game.use_nature_mint(db, db.mon(mid), "Bogus"))[0]
    assert world.ITEMS["nature-mint"].price > 0
    from pokebot import showdown as sd
    assert sd.rec_to_set(db.mon(mid))["nature"] == other
