import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pokebot import showdown as sd

pytestmark = pytest.mark.skipif(not sd.node_available(), reason="engine not installed (cd engine && npm install)")
run = asyncio.run


def team(nums, mv=("flamethrower", "surf", "earthquake", "protect"), level=50):
    return [{"num": n, "level": level, "moves": list(mv), "ability": ""} for n in nums]


async def play(gimmick, fmt="singles", seed=3):
    e = sd.Engine()
    try:
        n = [6, 9, 3] if fmt == "singles" else [6, 9, 3, 25]
        r = await e.call("new", bid="t", format=fmt, seed=seed, p1={"name": "A", "team": team(n), "gimmick": gimmick},
                         p2={"name": "B", "team": team(n), "gimmick": gimmick})
        assert r["ok"], r
        log, offered = list(r["log"]), set()
        for _ in range(150):
            if r["ended"]:
                break
            for i, side in enumerate(("p1", "p2")):
                rq = r["requests"][i]
                if not rq or rq.get("wait"):
                    continue
                for a in rq.get("active", []):
                    offered |= {k for k in ("canMegaEvo", "canZMove", "canDynamax", "canTerastallize") if a.get(k)}
                c = await e.call("ai", bid="t", side=side, gimmick=gimmick)
                r = await e.call("choose", bid="t", side=side, choice=c["choice"])
                assert r["ok"], (c, r.get("error"))
                log += r["log"]
        return r, log, offered
    finally:
        await e.close()


@pytest.mark.parametrize("g,flag,marker", [("mega", "canMegaEvo", "|-mega|"), ("z", "canZMove", "|-zpower|"),
                                           ("dynamax", "canDynamax", "|-start|"), ("tera", "canTerastallize", "|-terastallize|"),
                                           (None, None, None)])
def test_singles_gimmick_lock_in(g, flag, marker):
    r, log, offered = run(play(g))
    assert r["ended"] and r["winner"] in ("p1", "p2")
    gim = {"canMegaEvo", "canZMove", "canDynamax", "canTerastallize"}
    assert offered == ({flag} if flag else set()) and offered <= gim      # only the locked-in gimmick is ever offered
    if marker:
        assert any(marker in l for l in log)


def test_doubles_plays_out():
    r, log, _ = run(play(None, "doubles"))
    assert r["ended"]
    assert len(r["state"]["sides"][0]["active"]) == 2


def test_idle_turn_spends_the_players_turn_only():
    async def go():
        e = sd.Engine()
        try:
            await e.call("new", bid="i", format="singles", p1={"name": "A", "team": team([6])}, p2={"name": "B", "team": team([9])})
            r = await e.call("idle", bid="i", side="p1")
            r = await e.call("choose", bid="i", side="p2", choice="move 1")
            ms = [l for l in r["log"] if l.startswith("|move|")]
            assert len(ms) == 1 and "p2a" in ms[0]            # only the foe attacked
        finally:
            await e.close()
    run(go())


def test_initial_hp_is_carried_and_formatter_reads_log():
    async def go():
        e = sd.Engine()
        try:
            t = team([6]); t[0]["hp"] = 10
            r = await e.call("new", bid="h", format="singles", p1={"name": "A", "team": t}, p2={"name": "B", "team": team([9])})
            mon = r["state"]["sides"][0]["pokemon"][0]
            assert mon["hp"] == 10 and mon["maxhp"] > 10
            r = await e.call("choose", bid="h", side="p1", choice="move 1")
            r = await e.call("choose", bid="h", side="p2", choice="move 2")
            text = "\n".join(sd.format_log(r["log"]))
            assert "used" in text
        finally:
            await e.close()
    run(go())


def test_invalid_choice_is_rejected_not_crashing():
    async def go():
        e = sd.Engine()
        try:
            await e.call("new", bid="x", format="singles", p1={"name": "A", "team": team([6])}, p2={"name": "B", "team": team([9])})
            r = await e.call("choose", bid="x", side="p1", choice="move 9")
            assert not r["ok"]
            r = await e.call("choose", bid="x", side="p1", choice="move 1")
            assert r["ok"]
        finally:
            await e.close()
    run(go())


def test_format_log_keeps_one_copy_of_split_damage_lines():
    lines = ["|move|p1a: A|Tackle|p2a: B", "|split|p2", "|-damage|p2a: B|10/30", "|-damage|p2a: B|33/100"]
    out = sd.format_log(lines)
    assert out.count("   B → 33% HP") == 1 and len(out) == 2


@pytest.mark.parametrize("g", ["mega", "z", "dynamax", "tera"])
def test_doubles_with_every_gimmick_plays_out(g):
    r, log, offered = run(play(g, "doubles"))
    assert r["ended"] and r["winner"] in ("p1", "p2")


def test_player_needs_the_mega_stone_in_hand_but_ai_gets_one_free():
    async def offered_mega(megaAuto, item):
        e = sd.Engine()
        try:
            t = team([6], mv=("flamethrower", "protect"))
            t[0]["megaAuto"] = megaAuto
            if item:
                t[0]["item"] = item
            r = await e.call("new", bid="m", format="singles", seed=1, p1={"name": "A", "team": t, "gimmick": "mega"},
                             p2={"name": "B", "team": team([9]), "gimmick": None})
            assert r["ok"], r
            return any(a.get("canMegaEvo") for a in (r["requests"][0] or {}).get("active", []))
        finally:
            await e.close()
    assert run(offered_mega(False, None)) is False            # a player without the stone can't Mega Evolve
    assert run(offered_mega(False, "Charizardite X")) is True
    assert run(offered_mega(None, None)) is True              # AI trainers still get theirs automatically


def test_player_needs_a_z_crystal_too():
    async def offered_z(megaAuto, item):
        e = sd.Engine()
        try:
            t = team([6], mv=("flamethrower", "protect"))
            t[0]["megaAuto"] = megaAuto
            if item:
                t[0]["item"] = item
            r = await e.call("new", bid="z", format="singles", seed=1, p1={"name": "A", "team": t, "gimmick": "z"},
                             p2={"name": "B", "team": team([9]), "gimmick": None})
            return any(a.get("canZMove") for a in (r["requests"][0] or {}).get("active", []))
        finally:
            await e.close()
    assert run(offered_z(False, None)) is False
    assert run(offered_z(False, "Firium Z")) is True
    assert run(offered_z(None, None)) is True
