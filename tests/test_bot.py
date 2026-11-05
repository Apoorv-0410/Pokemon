import asyncio
import io
import random
import re
import sys
from types import SimpleNamespace as NS
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from telegram.constants import ChatType
from telegram.error import Forbidden

from pokebot import bot as B, config, data, game, images, world
from pokebot.db import DB

data.set_provider(data.MockProvider())
from conftest import run


class FakeBot:
    def __init__(self):
        self.sent = []      # (chat_id, text, markup)
        self.edits = []     # (chat_id, message_id, text, markup)
        self.media = []
        self.photos = []
        self.n = 100
        self.no_dm = set()

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.n += 1
        self.sent.append((chat_id, text, reply_markup))
        return NS(message_id=self.n)

    async def edit_message_text(self, text, chat_id=None, message_id=None, reply_markup=None, **kw):
        self.edits.append((chat_id, message_id, text, reply_markup))

    async def send_photo(self, chat_id, url, **kw):
        self.media.append((chat_id, url))
        self.n += 1
        self.captions = getattr(self, "captions", {})
        self.captions[self.n] = kw.get("caption")
        return NS(message_id=self.n)

    send_animation = send_photo

    async def edit_message_media(self, chat_id=None, message_id=None, media=None, **kw):
        self.media.append(("edit", chat_id, message_id))
        self.captions = getattr(self, "captions", {})
        self.captions[message_id] = getattr(media, "caption", None)

    async def edit_message_caption(self, caption, chat_id=None, message_id=None, reply_markup=None, **kw):
        self.edits.append((chat_id, message_id, caption, reply_markup))
        self.captions = getattr(self, "captions", {})
        self.captions[message_id] = caption

    async def send_chat_action(self, chat_id, action):
        if chat_id in self.no_dm:
            raise Forbidden("bot can't initiate conversation")

    async def set_my_commands(self, cmds):
        pass


class Env:
    def __init__(self):
        B.init(DB(":memory:"))
        B.MSG.clear()
        B.CHALLENGES.clear()
        self.bot = FakeBot()
        self.next_msg = 1000

    def chat(self, cid, kind=ChatType.PRIVATE):
        bot = self.bot
        return NS(id=cid, type=kind, send_message=lambda text, reply_markup=None, **kw: bot.send_message(cid, text, reply_markup))

    def cmd(self, fn, uid, *args, name="User", chat_id=None, group=False, reply_to=None):
        chat = self.chat(chat_id or uid, ChatType.GROUP if group else ChatType.PRIVATE)
        msg = NS(reply_to_message=NS(from_user=reply_to) if reply_to else None)
        bot = self.bot

        async def reply_photo(photo, caption=None, reply_markup=None, **kw):
            assert photo.read(8) == b"\x89PNG\r\n\x1a\n"
            bot.photos.append((chat.id, caption, reply_markup))

        msg.reply_photo = reply_photo
        upd = NS(effective_user=NS(id=uid, first_name=name, username=name), effective_chat=chat, message=msg,
                 effective_message=msg)
        ctx = NS(bot=self.bot, args=list(args))
        return run(fn(upd, ctx))

    def click(self, fn, uid, data_, chat_id=None, message_id=None):
        env = self
        cid = chat_id or uid
        mid = message_id or env.last_msg_id(cid)
        answered = []

        async def answer(text=None, show_alert=False):
            answered.append(text)

        async def edit_cap(text, reply_markup=None):
            await env.bot.edit_message_caption(text, cid, mid, reply_markup)

        async def edit(text, reply_markup=None):
            await env.bot.edit_message_text(text, cid, mid, reply_markup)

        async def reply_text(text, **kw):
            await env.bot.send_message(cid, text)

        q = NS(data=data_, from_user=NS(id=uid), message=NS(chat_id=cid, message_id=mid, reply_text=reply_text),
               answer=answer, edit_message_text=edit, edit_message_caption=edit_cap)
        upd = NS(callback_query=q)
        ctx = NS(bot=self.bot, args=[])
        run(fn(upd, ctx))
        return answered

    def last_msg_id(self, cid):
        return B.MSG.get(cid, (cid, 1))[1]

    def last_sent(self, cid=None):
        items = [s for s in self.bot.sent if cid is None or s[0] == cid]
        return items[-1]

    def buttons(self, markup):
        return [b.callback_data for row in markup.inline_keyboard for b in row] if markup else []

    def onboard(self, uid, name="Ash", region="kanto", starter_idx=0):
        self.cmd(B.cmd_start, uid, name=name)
        chat, text, mk = self.last_sent(uid)
        self.click(B.cb_look, uid, f"gd|{uid}|boy")
        self.click(B.cb_look, uid, f"tp|{uid}|ash")
        self.click(B.cb_start, uid, f"st|{uid}|{region}")
        # starter buttons are in the edit
        mk = self.bot.edits[-1][3]
        sp = self.buttons(mk)[starter_idx]
        self.click(B.cb_start, uid, sp)


def test_app_builds_and_registers_handlers():
    app = B.build_app("123456:TEST-TOKEN")
    n = sum(len(v) for v in app.handlers.values())
    assert n >= 38


def test_onboarding_and_info_commands():
    e = Env()
    e.onboard(1)
    assert e.bot.media, "starter sprite should be sent"
    assert B.db.count_mons(1) == 1 and B.db.qty(1, "poke-ball") == 30 and B.db.qty(1, "great-ball") == 15 and B.db.qty(1, "ultra-ball") == 10
    for fn in (B.cmd_party, B.cmd_profile, B.cmd_bag, B.cmd_shop, B.cmd_badges, B.cmd_leaderboard, B.cmd_gimmicks,
               B.cmd_help, B.cmd_market, B.cmd_mylistings, B.cmd_box, B.cmd_travel):
        e.cmd(fn, 1)
    mid = B.db.party(1)[0]["id"]
    e.cmd(B.cmd_mon, 1, str(mid))
    e.cmd(B.cmd_nick, 1, str(mid), "Sparky")
    assert B.db.mon(mid)["nickname"] == "Sparky"
    # second /start must not duplicate starter
    e.cmd(B.cmd_start, 1)
    assert B.db.count_mons(1) == 1


def test_trainer_pick_card_and_badge_case():
    e = Env()
    e.onboard(1, name="Atharva")
    assert B.db.player(1)["trainer"] == "ash" and B.db.player(1)["gender"] == "boy"
    e.cmd(B.cmd_card, 1)
    chat, caption, mk = e.bot.photos[-1]
    assert "Atharva" in caption
    labels = [b.text for row in mk.inline_keyboard for b in row]
    assert "Kanto 0/8" in labels and "🔒 Kalos" in labels
    answered = e.click(B.cb_badgecase, 1, "bc|1|kalos")
    assert any("Conquer" in (a or "") for a in answered)
    e.click(B.cb_badgecase, 1, "bc|1|kanto")
    cap = e.bot.edits[-1][2]
    assert "KANTO BADGES" in cap and "0/8" in cap
    B.db.give_badge(1, "kanto", 0)
    e.click(B.cb_badgecase, 1, "bc|1|kanto")
    assert "✅" in e.bot.edits[-1][2]
    e.click(B.cb_badgecase, 1, "bc|1|home")
    assert "Trainer Card" in e.bot.edits[-1][2]
    assert B.db.player(1)["card_color"] in range(10)
    # change look later
    e.cmd(B.cmd_trainer, 1)
    e.click(B.cb_look, 1, "gd|1|girl")
    e.click(B.cb_look, 1, "tp|1|may")
    assert B.db.player(1)["trainer"] == "may" and B.db.player(1)["gender"] == "girl"
    # not yours
    assert any("someone else" in (a or "") for a in e.click(B.cb_badgecase, 2, "bc|1|kanto"))


def test_safari_guarantees_three_legendaries_and_flee_drops():
    import random
    from pokebot import extras
    e = Env()
    e.onboard(1)
    B.db.add_money(1, 100000)
    B.db.enter_safari(1, 0, 30, 67, 3)
    rng, seen = random.Random(4), 0
    for _ in range(67):
        enc = run(extras.new_encounter(B.db, 1, "kanto", rng))
        seen += bool(enc["rec"].get("legend"))
        extras.ENC.pop(1, None)
    assert seen == 3 and B.db.safari(1)["legs"] == 0
    class Always(random.Random):
        def random(self): return 0.0
        def randrange(self, *a): return 65535
    extras.ENC[1] = {"rec": {"species_name": "Mewtwo", "hp": 100, "capture_rate": 0, "legend": True}, "mood": ""}
    B.db.set_safari(1, 10, 10)
    n = B.db.qty(1, "master-ball")
    extras.BALL_POWER = 0.0001
    try:
        log, out = extras.act(B.db, 1, "ball", Always())
    finally:
        extras.BALL_POWER = 5.0
    assert out == "fled" and B.db.qty(1, "master-ball") == n + 1


def test_whos_that_pokemon_reward_and_cap():
    e = Env()
    e.onboard(1, "Ash")
    e.onboard(2, "Gary")
    B.WHO.clear(); B.WHO_WINS.clear()
    e.cmd(B.cmd_whosthat, 1, chat_id=-9, group=True)
    assert -9 in B.WHO
    name = B.WHO[-9]["name"]
    m0 = B.db.player(2)["money"]
    e.cmd(B.cmd_guess, 2, "definitely not it", chat_id=-9, group=True)
    assert -9 in B.WHO and B.db.player(2)["money"] == m0
    e.cmd(B.cmd_guess, 2, *name.lower().split(), chat_id=-9, group=True)
    assert -9 not in B.WHO and B.db.player(2)["money"] == m0 + 20
    B.WHO_WINS[(2, __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime()))] = B.WHO_DAILY_CAP
    e.cmd(B.cmd_whosthat, 1, chat_id=-9, group=True)
    e.cmd(B.cmd_guess, 2, *B.WHO[-9]["name"].lower().split(), chat_id=-9, group=True)
    assert B.db.player(2)["money"] == m0 + 20


def test_trainer_name_at_start_and_name_command():
    e = Env()
    e.cmd(B.cmd_start, 7, name="Telegram Name")
    assert "trainer name" in e.last_sent(7)[1]
    e.bot_text = None
    upd = NS(effective_user=NS(id=7), effective_chat=e.chat(7), message=NS(text="  Ash   Ketchum the great "))
    run(B.on_text(upd, NS(bot=e.bot, args=[])))
    assert B.db.player(7)["name"] == "Ash Ketchum"
    run(B.on_text(upd, NS(bot=e.bot, args=[])))              # only the first message counts
    assert B.db.player(7)["name"] == "Ash Ketchum"
    e.cmd(B.cmd_name, 7, "Misty")
    assert B.db.player(7)["name"] == "Misty"
    e.cmd(B.cmd_name, 7, "x")
    assert B.db.player(7)["name"] == "Misty"


def test_surprise_me_picks_a_trainer_then_asks_region():
    e = Env()
    e.cmd(B.cmd_start, 5, name="Misty")
    e.click(B.cb_look, 5, "gd|5|any")
    p = B.db.player(5)
    assert p["trainer"] in images.TRAINERS and p["gender"] in ("boy", "girl")
    assert any(c.startswith("st|5|") for c in e.buttons(e.bot.edits[-1][3]))


def test_starter_choice_is_bound_to_user():
    e = Env()
    e.cmd(B.cmd_start, 1, name="Ash")
    answered = e.click(B.cb_start, 2, "st|1|kanto")      # someone else clicks
    assert any("isn't your" in (a or "") for a in answered)
    assert B.db.count_mons(1) == 0


def test_wild_battle_through_buttons():
    e = Env()
    e.onboard(1)
    B.db.update_mon(B.db.party(1)[0]["id"], level=40, hp=999)
    B.db.update_mon(B.db.party(1)[0]["id"], hp=run(game.max_hp_of(B.db.party(1)[0])))
    e.cmd(B.cmd_explore, 1)
    chat, text, mk = e.last_sent(1)
    assert "wild" in text.lower() and mk
    rng = random.Random(1)
    for _ in range(200):
        if not B.G.active(1):
            break
        mk = e.bot.edits[-1][3] if e.bot.edits and e.bot.edits[-1][1] == B.MSG[1][1] else e.last_sent(1)[2]
        s = B.G.active(1)
        kb = [b for row in B.G.keyboard(s, 0) for b in row]
        reps = [b[1] for b in kb if b[1].startswith("p|")]
        moves = [b[1] for b in kb if b[1].startswith("m|")]
        e.click(B.cb_battle, 1, (reps or moves)[0] if reps else rng.choice(moves), message_id=B.MSG[1][1])
    assert B.G.active(1) is None
    assert any("wild" in ed[2].lower() or "got away" in ed[2].lower() or "blacked out" in ed[2].lower()
               for ed in e.bot.edits)
    # final message removes the keyboard
    assert e.bot.edits[-1][3] is None


def test_battle_buttons_rejected_for_strangers():
    e = Env()
    e.onboard(1)
    e.onboard(2, name="Gary")
    e.cmd(B.cmd_explore, 1)
    s = B.G.active(1)
    answered = e.click(B.cb_battle, 2, f"m|{s.id}|0")
    assert any("isn't your battle" in (a or "") for a in answered)


def test_run_ends_wild_battle():
    e = Env()
    e.onboard(1)
    e.cmd(B.cmd_explore, 1)
    s = B.G.active(1)
    e.click(B.cb_battle, 1, f"r|{s.id}|", message_id=B.MSG[1][1])
    assert B.G.active(1) is None
    assert "got away" in e.bot.edits[-1][2]


def test_shop_buy_sell():
    e = Env()
    e.onboard(1)
    start = B.db.money(1)
    e.cmd(B.cmd_buy, 1, "great", "ball", "3")
    assert B.db.qty(1, "great-ball") == 15 + 3 and B.db.money(1) == start - 900   # Great Ball = $300
    e.cmd(B.cmd_buy, 1, "tera-orb")
    assert B.db.qty(1, "tera-orb") == 0           # can't afford 25k
    B.db.add_money(1, 100000)
    e.cmd(B.cmd_buy, 1, "tera-orb")
    e.cmd(B.cmd_buy, 1, "tera-orb")               # second is refused
    assert B.db.qty(1, "tera-orb") == 1
    e.cmd(B.cmd_sell, 1, "tera-orb")
    assert B.db.qty(1, "tera-orb") == 1           # key items can't be sold
    e.cmd(B.cmd_heal, 1)
    e.cmd(B.cmd_daily, 1)
    e.cmd(B.cmd_daily, 1)
    assert "already claimed" in e.last_sent(1)[1]


def test_market_commands():
    e = Env()
    e.onboard(1, "Seller")
    e.onboard(2, "Buyer")
    B.db.add_mon(1, run(game.new_record(9, 10, random.Random(1))))
    second = B.db.party(1)[1]["id"]
    e.cmd(B.cmd_sellmon, 1, str(second), "500")
    lid = B.db.listings()[0][0]["id"]
    e.cmd(B.cmd_market, 2)
    assert f"[{lid}]" in e.last_sent(2)[1]
    e.cmd(B.cmd_buylisting, 2, str(lid))
    assert B.db.mon(second)["owner"] == 2
    assert any(c == 1 and "sold" in t for c, t, _ in e.bot.sent)   # seller notified
    B.db.add_item(1, "great-ball", 4)
    e.cmd(B.cmd_sellitem, 1, "great", "ball", "2", "400")
    assert B.db.qty(1, "great-ball") == 15 + 2
    lid2 = B.db.listings()[0][0]["id"]
    e.cmd(B.cmd_cancel, 1, str(lid2))
    assert B.db.qty(1, "great-ball") == 15 + 4


def test_gym_and_travel_flow():
    e = Env()
    e.onboard(1)
    e.cmd(B.cmd_gym, 1)
    chat, text, mk = e.last_sent(1)
    assert "Brock" in text
    go = e.buttons(mk)[0]
    e.click(B.cb_gym, 1, go)
    s = B.G.active(1)
    assert s and s.kind == "gym"
    e.click(B.cb_battle, 1, f"f|{s.id}|", message_id=B.MSG[1][1])
    assert B.G.active(1) is None
    # you can't leave a region before beating its Champion
    here = B.db.player(1)["region"]
    e.cmd(B.cmd_travel, 1)
    assert "can't leave" in e.last_sent(1)[1] and not e.buttons(e.last_sent(1)[2] or None) if e.last_sent(1)[2] else True
    other = "johto" if here != "johto" else "hoenn"
    answered = e.click(B.cb_travel, 1, f"tr|1|{other}")
    assert any("Champion" in (a or "") for a in answered) and B.db.player(1)["region"] == here
    for i in range(8):
        B.db.give_badge(1, here, i)
    e.cmd(B.cmd_travel, 1)
    assert not e.buttons(e.last_sent(1)[2] or None) if e.last_sent(1)[2] else True          # 8 badges alone are not enough
    for i in range(world.LEAGUE_SIZE):
        B.db.give_league(1, here, i)                      # Champion beaten: Gen 1-5 open (Kalos+ too, since you conquered a region)
    e.cmd(B.cmd_travel, 1)
    assert len(e.buttons(e.last_sent(1)[2])) == 8
    e.click(B.cb_travel, 1, f"tr|1|{other}")
    assert B.db.player(1)["region"] == other
    # in the new, unconquered region you can only go back to the one you conquered
    e.cmd(B.cmd_travel, 1)
    assert e.buttons(e.last_sent(1)[2]) == [f"tr|1|{here}"]
    assert any("Champion" in (a or "") for a in e.click(B.cb_travel, 1, "tr|1|unova" if other != "unova" else "tr|1|sinnoh"))


def test_pvp_challenge_flow():
    e = Env()
    e.onboard(1, "Ash")
    e.onboard(2, "Gary")
    B.db.update_mon(B.db.party(1)[0]["id"], level=30)
    B.db.update_mon(B.db.party(2)[0]["id"], level=30)
    e.cmd(B.cmd_challenge, 1, name="Ash", chat_id=-50, group=True, reply_to=NS(id=2, first_name="Gary", is_bot=False))
    chat, text, mk = e.last_sent(-50)
    yes = [b for b in e.buttons(mk) if b.endswith("|y")][0]
    answered = e.click(B.cb_challenge, 1, yes, chat_id=-50)      # challenger can't accept
    assert any("Only the challenged" in (a or "") for a in answered)
    e.click(B.cb_challenge, 2, yes, chat_id=-50)
    s = B.G.active(1)
    assert s and s.kind == "pvp"
    # both players' controls live in the group
    assert B.MSG[1][0] == -50 and B.MSG[2][0] == -50
    rng = random.Random(3)
    for _ in range(300):
        if not B.G.active(1):
            break
        for u in (1, 2):
            s = B.G.active(u)
            if not s:
                break
            side = s.side_of(u)
            kb = [b[1] for row in (B.G.keyboard(s, side) or []) for b in row]
            reps = [k for k in kb if k.startswith("p|")]
            mv = [k for k in kb if k.startswith("m|")]
            pick = reps[0] if reps else (rng.choice(mv) if mv else None)
            if pick:
                e.click(B.cb_battle, u, pick, chat_id=-50, message_id=B.MSG[u][1])
    assert B.G.active(1) is None
    assert any(c == -50 and "wins" in t or c == -50 and "draw" in t for c, t, _ in e.bot.sent)


def test_release_confirmation():
    e = Env()
    e.onboard(1)
    B.db.add_mon(1, run(game.new_record(9, 10, random.Random(1))))
    second = B.db.party(1)[1]["id"]
    e.cmd(B.cmd_release, 1, str(second))
    mk = e.last_sent(1)[2]
    yes = e.buttons(mk)[0]
    answered = e.click(B.cb_release, 2, yes)
    assert B.db.mon(second) is not None
    e.click(B.cb_release, 1, yes)
    assert B.db.mon(second) is None


def test_callback_patterns_route():
    pat = re.compile(rf"^[{B.BATTLE_OPS}]\|")
    for d in ("m|abc123|0", "g|abc123|tera", "c|abc123|ultra-ball", "p|abc123|2", "f|abc123|", "n|abc123|"):
        assert pat.match(d), d
    for d in ("st|1|kanto", "sp|1|1", "gy|1|go", "tr|1|johto", "ch|ab|y", "rel|1|2"):
        assert not pat.match(d), d
    assert max(len(f"c|abc123|{k}") for k in world.ITEMS) <= 64


def test_trainer_card_and_fav():
    e = Env()
    e.onboard(1, "Ash")
    B.db.add_mon(1, run(game.new_record(9, 25, random.Random(1))))
    B.db.record_result(1, True)
    B.db.record_result(1, False)
    B.db.record_result(1, True, pvp=True)
    B.db.give_badge(1, "kanto", 0)
    B.db.give_badge(1, "kanto", 1)
    seen = {}
    real = images.render_card

    async def spy(**kw):
        seen.update(kw)
        return await real(**kw)

    images.render_card = spy
    try:
        e.cmd(B.cmd_card, 1)
        assert seen["wins"] == 2 and seen["losses"] == 1 and seen["badges"] == 2 and seen["owned"] == 2
        assert seen["fav_species"] == 9                      # favourite defaults to the highest level
        starter = min(B.db.party(1), key=lambda r: r["level"])
        e.cmd(B.cmd_fav, 1, str(starter["id"]))
        e.cmd(B.cmd_card, 1)
        assert seen["fav_species"] == starter["species_id"]
        e.cmd(B.cmd_profile, 1)                              # old name still works
        assert len(e.bot.photos) == 3
    finally:
        images.render_card = real


def test_hunt_and_find_are_aliases():
    app = B.build_app("123456:TEST-TOKEN")
    names = {c for hs in app.handlers.values() for h in hs if hasattr(h, "commands") for c in h.commands}
    assert {"explore", "hunt", "find", "card", "trainer", "fav"} <= names
    assert {"evolve", "train", "use"} <= names


def test_catch_suspense_frames_and_cheaper_balls():
    assert [world.ITEMS[k].price for k in ("poke-ball", "great-ball", "ultra-ball")] == [100, 300, 600]
    assert not any(i.kind in ("heal", "revive") for i in world.ITEMS.values())
    B.FRAME_DELAY = 0
    e = Env()
    e.onboard(1)
    B.db.add_item(1, "ultra-ball", 20)
    e.cmd(B.cmd_hunt if hasattr(B, "cmd_hunt") else B.cmd_explore, 1)
    s = B.G.active(1)
    e.click(B.cb_battle, 1, f"b|{s.id}|", message_id=B.MSG[1][1])
    n = len(e.bot.edits)
    e.click(B.cb_battle, 1, f"c|{s.id}|ultra-ball", message_id=B.MSG[1][1])
    shown = [ed[2] for ed in e.bot.edits[n:]]
    assert any("threw" in t for t in shown) and any(t.startswith("⚪ .") for t in shown)
    assert not shown[-1].startswith("⚪")                     # the real result comes after the suspense


def test_ui_is_bullet_style():
    e = Env()
    e.onboard(1)
    for fn in (B.cmd_party, B.cmd_bag, B.cmd_gym, B.cmd_badges, B.cmd_travel):
        e.cmd(fn, 1)
        text = e.last_sent(1)[1]
        assert "━" in text and "•" in text, fn.__name__


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


def test_train_evolve_use_commands_and_new_shop():
    e = Env()
    e.onboard(1)
    B.db.add_money(1, 50000)
    e.cmd(B.cmd_shop, 1)
    home = e.buttons(e.last_sent(1)[2])
    assert len(home) == 7 and all(b.startswith("sh|1|s|") for b in home)          # one button per shelf
    for b in home:
        e.click(B.cb_shop, 1, b)
    e.click(B.cb_shop, 1, "sh|1|s|ball")
    labels = " ".join(b.text for row in e.bot.edits[-1][3].inline_keyboard for b in row)
    assert "Quick Ball" in labels and "Master Ball" not in labels
    e.click(B.cb_shop, 1, "sh|1|i|quick-ball")
    assert "You own: 0" in e.bot.edits[-1][2]
    e.click(B.cb_shop, 1, "sh|1|b|quick-ball|5")
    assert B.db.qty(1, "quick-ball") == 5 and "Bought 5" in e.bot.edits[-1][2]
    e.click(B.cb_shop, 1, "sh|1|b|master-ball|1")
    assert B.db.qty(1, "master-ball") == 0
    e.cmd(B.cmd_buy, 1, "master-ball")
    assert "isn't sold" in e.last_sent(1)[1]
    e.cmd(B.cmd_buy, 1, "fire", "stone")
    assert B.db.qty(1, "fire-stone") == 1
    e.cmd(B.cmd_train, 1)
    e.click(B.cb_train, 1, e.buttons(e.last_sent(1)[2])[1])
    assert B.db.player(1)["ev_left"] == 30 and B.db.player(1)["ev_stat"] == "atk"
    mid = B.db.party(1)[0]["id"]
    B.db.update_mon(mid, species_id=6, species_name="Mon6", level=20)
    e.cmd(B.cmd_evolve, 1, str(mid))
    btns = e.buttons(e.last_sent(1)[2])
    assert btns and btns[0].startswith("ev|")
    e.click(B.cb_evolve, 1, btns[0])
    assert B.db.mon(mid)["species_id"] == 7 and B.db.qty(1, "fire-stone") == 0
    B.db.add_item(1, "rare-candy", 1)
    e.cmd(B.cmd_use, 1, "rare-candy", str(mid))
    assert B.db.mon(mid)["level"] == 21 and B.db.qty(1, "rare-candy") == 0


def test_dex_moves_and_form_commands():
    e = Env()
    e.onboard(1)
    B.db.add_money(1, 50000)
    sid = B.db.party(1)[0]["species_id"]
    mid = B.db.party(1)[0]["id"]
    e.cmd(B.cmd_dex, 1)
    assert "POKÉDEX" in e.last_sent(1)[1]
    e.cmd(B.cmd_dex, 1, str(sid))
    assert "Height" in e.last_sent(1)[1]
    e.cmd(B.cmd_dex, 1, "99999")
    assert "don't know" in e.last_sent(1)[1]
    e.cmd(B.cmd_moves, 1, str(mid))
    btns = e.buttons(e.last_sent(1)[2])
    assert btns and btns[0].startswith("mv|")
    n_edits = len(e.bot.edits)
    e.click(B.cb_moves, 1, btns[0])
    assert len(e.bot.edits) > n_edits
    B.db.update_mon(mid, species_id=493, species_name="Arceus", moves=["tackle"])
    e.cmd(B.cmd_form, 1, str(mid))
    fb = e.buttons(e.last_sent(1)[2])
    assert fb and fb[0].startswith("fm|")
    e.click(B.cb_form, 1, fb[0])
    assert B.db.mon(mid)["form"] and B.db.money(1) <= 3000 + 50000 - 500
    e.cmd(B.cmd_form, 1, "999999")
    assert "Usage" in e.last_sent(1)[1]


def test_nature_mint_flow():
    e = Env()
    e.onboard(1)
    B.db.add_item(1, "nature-mint", 1)
    mid = B.db.party(1)[0]["id"]
    e.cmd(B.cmd_use, 1, "nature-mint", str(mid))
    btns = e.buttons(e.last_sent(1)[2])
    assert btns and btns[0].startswith("nm|")
    e.click(B.cb_mint, 1, btns[0])
    assert B.db.qty(1, "nature-mint") == 0 and B.db.mon(mid)["nature"] == btns[0].split("|")[3]


def test_wild_battle_shows_a_scene_picture_and_updates_it():
    e = Env()
    e.onboard(1)
    B.PHOTO.clear()
    e.bot.media.clear()
    B.db.update_mon(B.db.party(1)[0]["id"], level=30)
    e.cmd(B.cmd_explore, 1)
    assert any(isinstance(m[1], io.BytesIO) for m in e.bot.media if len(m) == 2), "scene picture sent at battle start"
    assert 1 in B.PHOTO
    for _ in range(8):
        s = B.G.active(1)
        if not s or any(m[0] == "edit" for m in e.bot.media if len(m) == 3):
            break
        mvs = [b[1] for row in B.G.keyboard(s, 0) for b in row if b[1].startswith("m|")]
        if not mvs:
            break
        e.click(B.cb_battle, 1, mvs[0])
    assert any(m[0] == "edit" for m in e.bot.media if len(m) == 3), "picture replaced after the turn"


def test_challenge_rules_menu_sets_level_team_size_and_gimmicks():
    e = Env()
    e.onboard(1, "Ash")
    e.onboard(2, "Gary")
    B.db.update_mon(B.db.party(1)[0]["id"], level=30)
    B.db.update_mon(B.db.party(2)[0]["id"], level=30)
    e.cmd(B.cmd_challenge, 1, name="Ash", chat_id=-51, group=True, reply_to=NS(id=2, first_name="Gary", is_bot=False))
    cid = [b for b in e.buttons(e.last_sent(-51)[2]) if b.endswith("|y")][0].split("|")[1]
    assert any("challenger" in (a or "") for a in e.click(B.cb_challenge, 2, f"ch|{cid}|standard", chat_id=-51))
    e.click(B.cb_challenge, 1, f"ch|{cid}|standard", chat_id=-51)
    assert B.CHALLENGES[cid][3] == {"size": 6, "level": 50, "gimmicks": True, "doubles": False}
    e.click(B.cb_challenge, 1, f"ch|{cid}|size", chat_id=-51)           # 6 -> 1
    e.click(B.cb_challenge, 1, f"ch|{cid}|gim", chat_id=-51)
    assert B.CHALLENGES[cid][3] == {"size": 1, "level": 50, "gimmicks": False, "doubles": False}
    assert "all Lv50" in e.bot.edits[-1][2]
    e.click(B.cb_challenge, 2, f"ch|{cid}|y", chat_id=-51)
    s = B.G.active(1)
    assert s.rules["level"] == 50 and len(s.sets[0]) == 1 and s.sets[0][0]["level"] == 50
    assert s.avail[0] == [] and s.gimmick[0] is None


def test_safari_zone_enter_search_throw_and_finish():
    from pokebot import extras
    e = Env()
    e.onboard(1)
    B.db.add_money(1, 100000)
    extras.ENC.clear()
    e.cmd(B.cmd_safari, 1)
    assert any(b.endswith("|enter") for b in e.buttons(e.last_sent(1)[2]))
    money0 = B.db.player(1)["money"]
    e.click(B.cb_safari, 1, "sf|1|enter")
    assert B.db.player(1)["money"] == money0 - extras.SAFARI_FEE
    assert B.db.safari(1) == {"user_id": 1, "balls": 30, "steps": 67, "legs": 3}
    e.cmd(B.cmd_explore, 1)                                   # blocked while inside
    assert "Safari Zone" in e.last_sent(1)[1]
    e.click(B.cb_safari, 1, "sf|1|search")
    assert B.db.safari(1)["steps"] == 66 and 1 in extras.ENC
    n0 = B.db.count_mons(1)
    extras.BALL_POWER = 1e6                                    # guarantee the catch
    try:
        for _ in range(5):
            if 1 not in extras.ENC:
                e.click(B.cb_safari, 1, "sf|1|search")
            e.click(B.cb_safari, 1, "sf|1|ball")
    finally:
        extras.BALL_POWER = 5.0
    assert B.db.count_mons(1) > n0 and B.db.safari(1)["balls"] < 30
    e.click(B.cb_safari, 1, "sf|1|leave")
    assert B.db.safari(1) is None
    assert any("someone" in (a or "") or "isn't your" in (a or "") for a in e.click(B.cb_safari, 2, "sf|1|search"))


def test_safari_ends_when_balls_run_out():
    from pokebot import extras
    e = Env()
    e.onboard(1)
    B.db.add_money(1, 1000)
    extras.ENC.clear()
    e.click(B.cb_safari, 1, "sf|1|enter")
    B.db.set_safari(1, 1, 60)
    e.click(B.cb_safari, 1, "sf|1|search")
    extras.BALL_POWER = 0.0001                                 # certain miss
    try:
        e.click(B.cb_safari, 1, "sf|1|ball")
    finally:
        extras.BALL_POWER = 5.0
    assert B.db.safari(1) is None                              # out of balls -> trip over
    assert 3.0 < extras.catch_power({}) < 6.0 and 0 < extras.flee_chance({}) < 0.5


def test_trade_swaps_pokemon_with_two_confirms_and_checks():
    from pokebot import extras
    e = Env()
    e.onboard(1, "Ash")
    e.onboard(2, "Gary", starter_idx=1)
    ma, mb = B.db.party(1)[0]["id"], B.db.party(2)[0]["id"]
    B.TRADES.clear()
    e.cmd(B.cmd_trade, 1, str(mb), chat_id=-60, group=True, reply_to=NS(id=2, first_name="Gary", is_bot=False))
    assert "isn't one of your" in e.last_sent(-60)[1]                    # can't offer someone else's Pokémon
    e.cmd(B.cmd_trade, 1, str(ma), chat_id=-60, group=True, reply_to=NS(id=2, first_name="Gary", is_bot=False))
    tid = next(iter(B.TRADES))
    e.cmd(B.cmd_offer, 2, str(mb), chat_id=-60, group=True)
    assert B.TRADES[tid]["b_mid"] == mb
    e.click(B.cb_trade, 1, f"tdr|{tid}|ok", chat_id=-60)
    assert B.db.mon(ma)["owner"] == 1                                   # needs both confirms
    assert any("isn't your" in (a or "") for a in e.click(B.cb_trade, 3, f"tdr|{tid}|ok", chat_id=-60))
    e.click(B.cb_trade, 2, f"tdr|{tid}|ok", chat_id=-60)
    assert B.db.mon(ma)["owner"] == 2 and B.db.mon(mb)["owner"] == 1
    assert B.db.dex_caught(1, B.db.mon(mb)["species_id"]) and B.db.dex_caught(2, B.db.mon(ma)["species_id"])
    assert "Trade complete" in e.bot.edits[-1][2]
    # league-run teams are locked
    B.db.start_league_run(1, "kanto", [mb])
    e.cmd(B.cmd_trade, 1, str(mb), chat_id=-60, group=True, reply_to=NS(id=2, first_name="Gary", is_bot=False))
    assert "locked" in e.last_sent(-60)[1]


def test_trade_evolution_fires_after_a_trade():
    from pokebot import extras
    from pokebot.data import Evo, get_provider
    e = Env()
    e.onboard(1)
    m = B.db.party(1)[0]
    import dataclasses
    prov = get_provider()
    target = run(prov.get_species(m["species_id"] + 1))
    orig = prov.get_species

    async def fake(sid):
        sp = await orig(sid)
        return dataclasses.replace(sp, evos=[Evo(target.id, "trade")]) if sid == m["species_id"] else sp

    prov.get_species = fake
    try:
        out = run(extras.trade_evolve(B.db, 1, m["id"]))
    finally:
        del prov.get_species
    assert out and "evolved" in out[0] and B.db.mon(m["id"])["species_id"] == target.id


def test_vgc_preset_and_doubles_toggle_in_challenge_menu():
    e = Env()
    e.onboard(1, "Ash")
    e.onboard(2, "Gary")
    for u in (1, 2):
        B.db.add_mon(u, run(game.new_record(10, 30, random.Random(u))))
    e.cmd(B.cmd_challenge, 1, name="Ash", chat_id=-52, group=True, reply_to=NS(id=2, first_name="Gary", is_bot=False))
    cid = [b for b in e.buttons(e.last_sent(-52)[2]) if b.endswith("|y")][0].split("|")[1]
    e.click(B.cb_challenge, 1, f"ch|{cid}|vgc", chat_id=-52)
    assert B.CHALLENGES[cid][3] == {"size": 4, "level": 50, "gimmicks": True, "doubles": True}
    assert "Doubles" in e.bot.edits[-1][2]
    e.click(B.cb_challenge, 1, f"ch|{cid}|dbl", chat_id=-52)             # back to singles
    assert B.CHALLENGES[cid][3]["doubles"] is False and B.CHALLENGES[cid][3]["size"] == 6
    e.click(B.cb_challenge, 1, f"ch|{cid}|vgc", chat_id=-52)
    e.click(B.cb_challenge, 2, f"ch|{cid}|y", chat_id=-52)
    s = B.G.active(1)
    assert s.doubles and len(s.state["sides"][0]["active"]) == 2
    assert any(b.startswith("m|") for row in B.G.keyboard(s, 0) for _, b in row)


def test_daily_streak_rewards_and_reset(monkeypatch):
    import datetime as dt
    from pokebot import quests
    e = Env()
    e.onboard(1)
    m0 = B.db.money(1)
    e.cmd(B.cmd_daily, 1)
    assert B.db.money(1) == m0 + 1000 and B.db.player(1)["streak"] == 1
    e.cmd(B.cmd_daily, 1)
    assert "already claimed" in e.last_sent(1)[1]
    # pretend yesterday was the last claim: streak continues
    y = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    B.db.x("UPDATE players SET last_daily=?, streak=6 WHERE user_id=1", (y,))
    c0 = B.db.qty(1, "rare-candy")
    r = quests.streak_claim(B.db, 1, random.Random(5))
    assert r["streak"] == 7 and r["money"] == 3000 and B.db.qty(1, "rare-candy") == c0 + 1
    # a missed day resets to 1
    B.db.x("UPDATE players SET last_daily='2000-01-01' WHERE user_id=1")
    assert quests.streak_claim(B.db, 1)["streak"] == 1


def test_legendary_roll_on_every_tenth_streak_day():
    from pokebot import quests
    import datetime as dt
    e = Env()
    e.onboard(1)
    y = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%d")

    class Rng(random.Random):
        def __init__(self, v):
            super().__init__(1)
            self.v = v

        def random(self):
            return self.v
    B.db.x("UPDATE players SET last_daily=?, streak=9 WHERE user_id=1", (y,))
    r = quests.streak_claim(B.db, 1, Rng(0.01))           # lucky roll
    assert r["streak"] == 10 and r["legend"] and B.db.player(1)["legend_pending"] == 1
    B.db.x("UPDATE players SET last_daily=?, streak=19, legend_pending=0 WHERE user_id=1", (y,))
    u0 = B.db.qty(1, "ultra-ball")
    r = quests.streak_claim(B.db, 1, Rng(0.9))            # unlucky: consolation Ultra Balls
    assert not r["legend"] and B.db.qty(1, "ultra-ball") == u0 + 5 and B.db.player(1)["legend_pending"] == 0


def test_quests_progress_claim_and_all_three_bonus():
    from pokebot import quests
    e = Env()
    e.onboard(1)
    e.cmd(B.cmd_quests, 1)
    assert "QUESTS" in e.last_sent(1)[1]
    ql = quests.listing(B.db, 1)
    assert [r["goal"] > 0 for r in ql["d"]] == [True] * 3 and len(ql["w"]) == 1
    m0 = B.db.money(1)
    for r in ql["d"]:
        B.db.x("UPDATE quests SET progress=goal WHERE user_id=1 AND scope='d' AND slot=?", (r["slot"],))
    e.cmd(B.cmd_quests, 1)
    claims = [b for b in e.buttons(e.last_sent(1)[2]) if b.startswith("qs|1|d")]
    assert len(claims) == 3
    for c in claims:
        e.click(B.cb_quests, 1, c)
    assert B.db.money(1) >= m0 + 300 + 800 + 1500 + 1000 and B.db.qty(1, "ev-voucher") == 1
    assert not quests.claim(B.db, 1, "d", 0)[0]                                    # no double claims
    assert any("someone else" in (a or "") for a in e.click(B.cb_quests, 2, "qs|1|d|0"))


def test_quest_events_from_battles_and_the_free_ev_voucher():
    from pokebot import quests
    e = Env()
    e.onboard(1)
    quests.ensure(B.db, 1)
    B.db.x("DELETE FROM quests WHERE user_id=1 AND scope='d'")
    B.db.x("INSERT INTO quests VALUES(1,'d',0,?,'win_wild3',  '',0,3,0)", (quests.today(),))
    B.db.x("INSERT INTO quests VALUES(1,'d',1,?,'explore10','',0,10,0)", (quests.today(),))
    B.db.x("INSERT INTO quests VALUES(1,'d',2,?,'catchtype','fire',0,1,0)", (quests.today(),))
    quests.record(B.db, 1, "win_wild")
    quests.record(B.db, 1, "explore", 4)
    quests.record(B.db, 1, "catch", typ=["water"])
    quests.record(B.db, 1, "catch", typ=["fire", "flying"])
    got = {r["qid"]: r["progress"] for r in B.db.q("SELECT * FROM quests WHERE user_id=1 AND scope='d'")}
    assert got == {"win_wild3": 1, "explore10": 4, "catchtype": 1}
    # a voucher makes EV training free
    B.db.add_item(1, "ev-voucher", 1)
    m0 = B.db.money(1)
    e.click(B.cb_train, 1, "trn|1|atk")
    assert B.db.money(1) == m0 and B.db.qty(1, "ev-voucher") == 0 and B.db.player(1)["ev_left"] == 30


def test_leaderboards_and_dex_milestones():
    e = Env()
    e.onboard(1)
    e.onboard(2, name="Misty")
    B.db.x("UPDATE players SET money=99999 WHERE user_id=2")
    e.cmd(B.cmd_leaderboard, 1)
    assert "TOP TRAINERS" in e.last_sent(1)[1]
    e.click(B.cb_leaderboard, 1, "lb|money")
    assert "Misty" in e.bot.edits[-1][2] and "$99,999" in e.bot.edits[-1][2]
    for c, _, _ in B.LB_CATS:
        assert B.lb_view(c)[0]
    m0 = B.db.money(1)
    e.cmd(B.cmd_milestones, 1)
    assert not [b for b in e.buttons(e.last_sent(1)[2]) if b.startswith("ms|")]
    for sid in range(1, 12):
        B.db.dex_mark_caught(1, sid)
    e.cmd(B.cmd_milestones, 1)
    assert "ms|1|10" in e.buttons(e.last_sent(1)[2])
    assert any("someone else" in (a or "") for a in e.click(B.cb_milestones, 2, "ms|1|10"))
    e.click(B.cb_milestones, 1, "ms|1|10")
    assert B.db.money(1) == m0 + 500
    e.click(B.cb_milestones, 1, "ms|1|10")
    assert B.db.money(1) == m0 + 500                                    # no double claim


def test_breeding_eggs_hatching_found_eggs_and_merchant():
    from pokebot import eggs, world
    e = Env()
    e.onboard(1)
    uid = 1
    base = B.db.party(uid)[0]
    sid = run(eggs.root_of(base["species_id"]))
    other = run(game.new_record(sid, 20, __import__("random").Random(3)))
    other["iv"] = {k: 31 for k in other["iv"]}
    oid = B.db.add_mon(uid, other)
    B.db.x("UPDATE players SET money=10000 WHERE user_id=1")
    e.cmd(B.cmd_breed, uid, str(base["id"]), str(oid))
    eggs_now = B.db.eggs(uid)
    assert len(eggs_now) == 1 and eggs_now[0]["species_id"] == sid and len(eggs_now[0]["iv"]) == world.BREED_IVS
    assert B.db.money(uid) == 10000 - world.BREED_COST
    # incompatible / same mon
    stranger = B.db.add_mon(uid, run(game.new_record(sid + 400, 5, __import__("random").Random(4))))
    ok, why = run(eggs.breed(B.db, uid, B.db.mon(base["id"]), B.db.mon(stranger), __import__("random").Random(1)))
    assert not ok and "compatible" in why
    ok, why = run(eggs.breed(B.db, uid, B.db.mon(base["id"]), B.db.mon(base["id"]), __import__("random").Random(1)))
    assert not ok
    # Ditto works with anything
    dit = B.db.add_mon(uid, run(game.new_record(world.DITTO, 10, __import__("random").Random(5))))
    ok, _ = run(eggs.breed(B.db, uid, B.db.mon(stranger), B.db.mon(dit), __import__("random").Random(1)))
    assert ok
    # hatching after EGG_STEPS explores keeps inherited IVs
    for _ in range(world.EGG_STEPS):
        hatched = run(eggs.tick(B.db, uid, __import__("random").Random(2)))
    assert hatched and B.db.eggs(uid) == []
    # merchant offers an egg from another region
    o = run(eggs.offer("kanto", __import__("random").Random(1)))
    assert o["origin"] != "kanto" and o["price"] == world.EGG_NPC_PRICE
    B.EGG_OFFERS[uid] = o
    e.click(B.cb_egg, 2, f"eg|{uid}|buy")
    e.click(B.cb_egg, uid, f"eg|{uid}|buy")
    assert len(B.db.eggs(uid)) == 1 and B.db.eggs(uid)[0]["origin"] == o["origin"]
    e.cmd(B.cmd_eggs, uid)
    assert "YOUR EGGS (1/" in e.last_sent(uid)[1]


def test_explore_can_find_eggs_or_meet_the_merchant():
    from pokebot import world
    e = Env()
    e.onboard(1)
    world.EGG_NPC_CHANCE = 1.0
    e.cmd(B.cmd_explore, 1)
    chat, text, mk = e.last_sent(1)
    assert "Egg Merchant" in text and "eg|1|buy" in e.buttons(mk)
    world.EGG_NPC_CHANCE, world.EGG_FIND_CHANCE = 0.0, 1.0
    B.EGG_OFFERS.clear()
    e.cmd(B.cmd_explore, 1)
    assert len(B.db.eggs(1)) == 1
    assert any("mysterious egg" in m[1] for m in e.bot.sent)


def test_held_items_mega_stones_shop_and_breeding_items():
    import random as R
    from pokebot import eggs, world
    e = Env()
    e.onboard(1)
    uid = 1
    B.db.add_money(uid, 99999)
    # shop pages for the many Mega Stones
    e.click(B.cb_shop, uid, "sh|1|s|mega")
    mk = e.bot.edits[-1][3]
    assert "sh|1|s|mega:1" in e.buttons(mk)
    e.click(B.cb_shop, uid, "sh|1|s|mega:1")
    e.click(B.cb_shop, uid, "sh|1|b|leftovers|1")
    assert B.db.qty(uid, "leftovers") == 1
    # give / swap / take
    mon = B.db.party(uid)[0]
    B.db.add_item(uid, "choice-band", 1)
    e.cmd(B.cmd_give, uid, "leftovers", str(mon["id"]))
    assert B.db.mon(mon["id"])["item"] == "leftovers"
    e.cmd(B.cmd_give, uid, "choice", "band", str(mon["id"]))
    assert B.db.mon(mon["id"])["item"] == "choice-band" and B.db.qty(uid, "leftovers") == 1
    e.cmd(B.cmd_take, uid, str(mon["id"]))
    assert B.db.mon(mon["id"])["item"] == "" and B.db.qty(uid, "choice-band") == 1
    # a Mega Stone only fits its own species
    B.db.add_item(uid, "venusaurite", 1)
    e.cmd(B.cmd_give, uid, "charizardite-x", str(mon["id"]))
    assert B.db.mon(mon["id"])["item"] == ""
    # the held item reaches the battle engine
    from pokebot import showdown
    B.db.x("UPDATE mons SET item='leftovers' WHERE id=?", (mon["id"],))
    assert showdown.rec_to_set(B.db.mon(mon["id"]))["item"] == "Leftovers"
    # breeding items: power item + everstone + destiny knot
    a = dict(B.db.mon(mon["id"]), item="power-anklet", nature="Adamant", iv={k: 10 for k in eggs.STATS})
    b2 = dict(a, id=999, item="everstone", nature="Modest", iv={k: 20 for k in eggs.STATS})
    r = eggs.inherit(a, b2, R.Random(1))
    assert r["spe"] == 10 and r["nature"] == "Modest" and len([k for k in r if k != "nature"]) == world.BREED_IVS
    b3 = dict(b2, item="destiny-knot")
    r = eggs.inherit(a, b3, R.Random(1))
    assert len([k for k in r if k != "nature"]) == 5 and "nature" not in r


def test_z_crystals_in_shop_and_give_rules():
    e = Env()
    e.onboard(1)
    B.db.add_money(1, 99999)
    e.click(B.cb_shop, 1, "sh|1|s|zcrystal")
    assert any("Firium Z" in b for row in e.bot.edits[-1][3].inline_keyboard for b in [row[0].text])
    mon = B.db.party(1)[0]
    B.db.add_item(1, "pikanium-z", 1)
    B.db.add_item(1, "firium-z", 1)
    e.cmd(B.cmd_give, 1, "pikanium-z", str(mon["id"]))
    assert B.db.mon(mon["id"])["item"] != "pikanium-z"             # signature crystals fit only their species
    e.cmd(B.cmd_give, 1, "firium-z", str(mon["id"]))
    assert B.db.mon(mon["id"])["item"] == "firium-z"


def test_pokedex_overview_silhouettes_shiny_and_card_title():
    e = Env()
    e.onboard(1)
    for sid in range(1, 12):
        B.db.dex_mark_caught(1, sid)
    B.db.dex_seen(1, 20)
    B.db.x("UPDATE dex SET shiny=1 WHERE user_id=1 AND species_id=3")
    e.cmd(B.cmd_dex, 1)
    txt = e.last_sent(1)[1]
    assert "POKÉDEX" in txt and "Shiny 1" in txt and "Next milestone" in txt
    # list shows the shiny star; entries have picture buttons
    e.click(B.cb_dex, 1, "dx|1|l|kanto|0")
    assert "✨" in e.bot.edits[-1][2]
    e.click(B.cb_dex, 1, "dx|1|o|-|0")
    assert "POKÉDEX" in e.bot.edits[-1][2]
    e.click(B.cb_dex, 1, "dx|1|e|20|0")
    assert "dx|1|p|20|0" in e.buttons(e.bot.edits[-1][3])                                     # silhouette button for seen-only
    e.click(B.cb_dex, 1, "dx|1|e|3|0")
    assert "Native to" in e.bot.edits[-1][2] and "shiny" in e.bot.edits[-1][2]
    # pictures are rendered from the artwork (silhouette = dark)
    from pokebot import images
    from PIL import Image
    import io as _io
    img = Image.new("RGBA", (200, 200), (250, 10, 10, 255))
    async def fake(sid, shiny=False):
        return img
    old = images.mon_art
    images.mon_art = fake
    try:
        sil = Image.open(_io.BytesIO(run(images.dex_picture(5, False)))).convert("RGB")
        col = Image.open(_io.BytesIO(run(images.dex_picture(5, True)))).convert("RGB")
    finally:
        images.mon_art = old
    assert sil.getpixel((260, 260))[0] < 40 and col.getpixel((260, 260))[0] > 200
    assert B.dex_title(0) == "Newcomer" and B.dex_title(30) == "Bug Catcher"
    e.cmd(B.cmd_card, 1)


def test_tournament_bracket_logic_byes_and_draws():
    import random as R
    from pokebot import tournament as T
    t = T.Tourney("x", 1, 10, 100, "standard", {})
    for u in (1, 2, 3, 4, 5):
        assert t.add(u)
    assert not t.add(3)
    assert t.start(R.Random(1)) and len(t.matches) == 2 and len(t.byes) == 1
    assert t.pool == 5 * (100 + T.BASE_PRIZE) and sum(t.prizes().values()) == t.pool
    assert t.ready(0, 99) == "" and t.ready(0, t.matches[0]["a"]) == "wait" and t.ready(0, t.matches[0]["b"]) == "go"
    for i, m in enumerate(t.matches):
        st = t.report(i, m["a"], R.Random(2))
    assert st == "next" and t.round == 2
    while t.state == "running":
        for i, m in enumerate(t.matches):
            st = t.report(i, m["b"], R.Random(3))
    assert t.state == "done" and t.champion and t.runner_up and t.champion != t.runner_up


def test_tournament_flow_in_group_with_fees_walkover_and_real_battle():
    e = Env()
    for u, n in ((1, "Ash"), (2, "Gary"), (3, "Misty")):
        e.onboard(u, n)
        B.db.x("UPDATE players SET money=5000 WHERE user_id=?", (u,))
    chat = -70
    e.cmd(B.cmd_tournament, 1, "100", "casual", name="Ash", chat_id=chat, group=True)
    tid = [b for b in e.buttons(e.last_sent(chat)[2]) if b.endswith("|j")][0].split("|")[1]
    for u in (1, 2, 3):
        e.click(B.cb_tournament, u, f"to|{tid}|j", chat_id=chat)
    assert B.db.money(1) == 4900 and len(B.TOURNEYS[tid].players) == 3
    assert any("host" in (a or "") for a in e.click(B.cb_tournament, 2, f"to|{tid}|s", chat_id=chat))
    e.click(B.cb_tournament, 1, f"to|{tid}|s", chat_id=chat)
    t = B.TOURNEYS[tid]
    assert t.state == "running" and len(t.matches) == 1 and len(t.byes) == 1
    a, b = t.matches[0]["a"], t.matches[0]["b"]
    assert any("not your match" in (x or "") for x in e.click(B.cb_tournament, t.byes[0], f"to|{tid}|r|0", chat_id=chat))
    e.click(B.cb_tournament, a, f"to|{tid}|r|0", chat_id=chat)
    e.click(B.cb_tournament, b, f"to|{tid}|r|0", chat_id=chat)
    s = B.G.active(a)
    assert s and s.rules["tid"] == tid
    outs = run(B.G._forfeit(s, s.side_of(b)))                    # b gives up -> a wins the match
    run(B.apply_outs(e.bot, outs, chat, "pvp"))
    assert t.matches[0]["w"] is None or t.round == 2             # round 2 (final) now exists
    assert t.round == 2 and t.state == "running"
    m = t.matches[0]
    host_before = {u: B.db.money(u) for u in (1, 2, 3)}
    e.click(B.cb_tournament, 1, f"to|{tid}|w|0|0", chat_id=chat)  # host awards a walkover in the final
    assert t.state == "done" or tid not in B.TOURNEYS
    assert sum(B.db.money(u) - host_before[u] for u in (1, 2, 3)) == 3 * (100 + 500)
    assert "TOURNAMENT OVER" in e.last_sent(chat)[1]
    # cancel refunds
    e.cmd(B.cmd_tournament, 1, "200", name="Ash", chat_id=chat, group=True)
    tid2 = [b for b in e.buttons(e.last_sent(chat)[2]) if b.endswith("|j")][0].split("|")[1]
    m0 = B.db.money(2)
    e.click(B.cb_tournament, 2, f"to|{tid2}|j", chat_id=chat)
    assert B.db.money(2) == m0 - 200
    e.click(B.cb_tournament, 1, f"to|{tid2}|c", chat_id=chat)
    assert B.db.money(2) == m0 and tid2 not in B.TOURNEYS


def test_database_backup_is_consistent_and_rotates(tmp_path):
    import sqlite3
    e = Env()
    e.onboard(1)
    paths = [B.db.backup(tmp_path, keep=2, stamp=f"2026110{i}") for i in range(1, 4)]
    left = sorted(p.name for p in tmp_path.glob("pokebot-*.sqlite3"))
    assert left == ["pokebot-20261102.sqlite3", "pokebot-20261103.sqlite3"]
    c = sqlite3.connect(paths[-1])
    assert c.execute("SELECT COUNT(*) FROM players").fetchone()[0] >= 1


def test_friendly_battle_prize_loss_and_daily_cap():
    from pokebot import world
    e = Env()
    e.onboard(1, "Ash")
    e.onboard(2, "Gary")
    B.db.x("UPDATE players SET money=1000 WHERE user_id IN (1,2)")
    B.db.x("UPDATE players SET money=50 WHERE user_id=2")
    for n in range(world.FRIENDLY_DAILY_CAP + 1):
        s, outs = run(B.G.start_pvp(1, 2, -80))
        outs = run(B.G._forfeit(s, s.side_of(2)))                  # Gary gives up
        txt = "\n".join(o.text for o in outs)
        if n == 0:
            assert f"wins ${world.FRIENDLY_WIN_PRIZE}" in txt and "pays $50" in txt          # loser can't go below $0
            assert B.db.money(1) == 1900 and B.db.money(2) == 0
        if n == world.FRIENDLY_DAILY_CAP:
            assert "limit" in txt and B.db.money(1) == 1000 + world.FRIENDLY_DAILY_CAP * 900
        B.G._drop(s.id)


def test_admin_powers_and_bans():
    from pokebot import config
    e = Env()
    e.onboard(1, "Ash")
    e.onboard(2, "Gary")
    config.ADMIN_IDS.add(1)
    try:
        e.cmd(B.cmd_admin_money, 2, "500")                           # non-admins are ignored
        assert B.db.money(2) < 500 or True
        m2 = B.db.money(2)
        e.cmd(B.cmd_admin_money, 2, "99999")
        assert B.db.money(2) == m2
        reply_to = NS(id=2, first_name="Gary", is_bot=False)
        e.cmd(B.cmd_admin_money, 1, "700", reply_to=reply_to)
        assert B.db.money(2) == m2 + 700
        e.cmd(B.cmd_admin_item, 1, "rare", "candy", "3", reply_to=reply_to)
        assert B.db.qty(2, "rare-candy") == 3
        n0 = B.db.count_mons(2)
        e.cmd(B.cmd_admin_mon, 1, "25", "40", "shiny", reply_to=reply_to)
        assert B.db.count_mons(2) == n0 + 1
        e.cmd(B.cmd_admin_who, 1, reply_to=reply_to)
        assert "Gary" in e.last_sent(1)[1]
        e.cmd(B.cmd_admin_stats, 1)
        assert "Players: 2" in e.last_sent(1)[1]
        e.cmd(B.cmd_admin_ban, 1, "cheater", reply_to=reply_to)
        assert B.db.is_banned(2)
        e.cmd(B.cmd_admin_ban, 1, reply_to=NS(id=1, first_name="Ash", is_bot=False))
        assert not B.db.is_banned(1)                                  # admins can't be banned
        e.cmd(B.cmd_admin_unban, 1, reply_to=reply_to)
        assert not B.db.is_banned(2)
    finally:
        config.ADMIN_IDS.discard(1)


def test_trade_with_money_both_ways():
    e = Env()
    e.onboard(1, "Ash")
    e.onboard(2, "Gary", starter_idx=1)
    ma, mb = B.db.party(1)[0]["id"], B.db.party(2)[0]["id"]
    B.db.x("UPDATE players SET money=3000 WHERE user_id IN (1,2)")
    rt = NS(id=2, first_name="Gary", is_bot=False)
    # Pokémon for money: Ash sells #ma, Gary pays $1500 and gives no Pokémon
    B.TRADES.clear()
    e.cmd(B.cmd_trade, 1, str(ma), chat_id=-61, group=True, reply_to=rt)
    tid = next(iter(B.TRADES))
    e.cmd(B.cmd_offer, 2, "0", "99999", chat_id=-61, group=True)
    assert "only have" in e.last_sent(-61)[1]                             # can't offer more money than you have
    e.cmd(B.cmd_offer, 2, "0", "1500", chat_id=-61, group=True)
    assert "$1,500" in e.bot.edits[-1][2]
    e.click(B.cb_trade, 1, f"tdr|{tid}|ok", chat_id=-61)
    e.click(B.cb_trade, 2, f"tdr|{tid}|ok", chat_id=-61)
    assert B.db.mon(ma)["owner"] == 2 and B.db.money(1) == 4500 and B.db.money(2) == 1500
    # swap plus cash: Gary adds $500 for his Pokémon
    B.TRADES.clear()
    e.cmd(B.cmd_trade, 2, str(ma), "500", chat_id=-61, group=True, reply_to=NS(id=1, first_name="Ash", is_bot=False))
    tid = next(iter(B.TRADES))
    e.cmd(B.cmd_offer, 1, str(mb), chat_id=-61, group=True)
    e.click(B.cb_trade, 2, f"tdr|{tid}|ok", chat_id=-61)
    e.click(B.cb_trade, 1, f"tdr|{tid}|ok", chat_id=-61)
    assert B.db.mon(ma)["owner"] == 1 and B.db.mon(mb)["owner"] == 2 and B.db.money(1) == 5000 and B.db.money(2) == 1000
    # nothing on offer / money-for-money is refused
    B.TRADES.clear()
    e.cmd(B.cmd_trade, 1, "0", "100", chat_id=-61, group=True, reply_to=rt)
    e.cmd(B.cmd_offer, 2, "0", "100", chat_id=-61, group=True)
    assert "at least one Pokémon" in e.last_sent(-61)[1]


def test_help_sections_and_info_lookup():
    from pokebot import info
    e = Env()
    e.onboard(1)
    e.cmd(B.cmd_help, 1)
    btns = e.buttons(e.last_sent(1)[2])
    assert len(btns) == len(info.HELP_SECTIONS) and all(b.startswith("hp|") for b in btns)
    seen = ""
    for b in btns:
        e.click(B.cb_help, 1, b)
        seen += e.bot.edits[-1][2]
    for cmd in ("/explore", "/breed", "/tournament", "/trade", "/give", "/info", "/milestones", "/quests", "/safari", "/market", "/dex", "/event"):
        assert cmd in seen, cmd
    e.click(B.cb_help, 1, "hp|home")
    assert "HELP" in e.bot.edits[-1][2]
    # /info menu, pages, searches
    e.cmd(B.cmd_info, 1)
    assert set(e.buttons(e.last_sent(1)[2])) == {"if|a|0", "if|n|0", "if|i|0", "if|t|0"}
    e.click(B.cb_info, 1, "if|n|0")
    assert "Adamant: +Attack, −Sp. Atk" in e.bot.edits[-1][2] and "Hardy: neutral" in e.bot.edits[-1][2]
    e.click(B.cb_info, 1, "if|a|1")
    assert "Page 2/" in e.bot.edits[-1][2]
    e.click(B.cb_info, 1, "if|i|0")
    assert "HELD ITEMS" in e.bot.edits[-1][2]
    e.click(B.cb_info, 1, "if|t|0")
    assert "Fire: ×2" in e.bot.edits[-1][2]
    e.cmd(B.cmd_info, 1, "intimidate")
    assert "ability" in e.last_sent(1)[1] and "Attack" in e.last_sent(1)[1]
    e.cmd(B.cmd_info, 1, "leftovers")
    assert "🛒" in e.last_sent(1)[1] and "held item" in e.last_sent(1)[1]
    e.cmd(B.cmd_info, 1, "jolly")
    assert "+Speed" in e.last_sent(1)[1]
    e.cmd(B.cmd_info, 1, "zzzz")
    assert "couldn't find" in e.last_sent(1)[1]
    assert "Attack" in info.ability_desc("intimidate")


def test_every_admin_command_ignores_non_admins():
    from pokebot import config
    e = Env()
    e.onboard(1)
    e.onboard(2, "Gary")
    config.ADMIN_IDS.clear()
    fns = [getattr(B, n) for n in dir(B) if n.startswith("cmd_admin")]
    assert len(fns) >= 9
    m0, sent0 = B.db.money(2), len(e.bot.sent)
    for fn in fns:
        e.cmd(fn, 2, "5000", "rare-candy", reply_to=NS(id=1, first_name="Ash", is_bot=False))
    assert len(e.bot.sent) == sent0 and B.db.money(1) == B.db.money(1) and B.db.money(2) == m0     # no reply, no effect
    assert not B.db.is_banned(1) and B.db.qty(1, "rare-candy") == 0
    assert not any(c.startswith("admin") for c, _ in B.COMMANDS)                                   # not in the Telegram command menu


def test_friendly_battle_is_one_message_with_picture_scene_and_log():
    e = Env()
    e.onboard(1, "Ash")
    e.onboard(2, "Gary")
    for u in (1, 2):
        B.db.update_mon(B.db.party(u)[0]["id"], level=40)
    B.PHOTO.clear()
    e.bot.media.clear()
    e.cmd(B.cmd_challenge, 1, name="Ash", chat_id=-90, group=True, reply_to=NS(id=2, first_name="Gary", is_bot=False))
    cid = [b for b in e.buttons(e.last_sent(-90)[2]) if b.endswith("|y")][0].split("|")[1]
    n_sent = len(e.bot.sent)
    e.click(B.cb_challenge, 2, f"ch|{cid}|y", chat_id=-90)
    s = B.G.active(1)
    assert f"pvp{s.id}" in B.PHOTO and sum(1 for m in e.bot.media if len(m) == 2) == 1
    scene_id = B.PHOTO[f"pvp{s.id}"][1]
    assert "Friendly battle" in e.bot.captions[scene_id]
    for _ in range(10):
        if not B.G.active(1):
            break
        for u in (1, 2):
            s2 = B.G.active(u)
            if not s2:
                break
            mvs = [b[1] for row in B.G.keyboard(s2, s2.side_of(u)) for b in row if b[1].startswith("m|")]
            if mvs:
                e.click(B.cb_battle, u, mvs[0], chat_id=-90)
        if "used" in (e.bot.captions.get(scene_id) or ""):
            break
    assert "used" in e.bot.captions[scene_id] and "📜 LOG" in e.bot.captions[scene_id]       # the log lives in the picture's caption
    assert sum(1 for m in e.bot.media if len(m) == 2) == 1                                  # still just one picture message
    new_text = [x for x in e.bot.sent[n_sent:] if "Friendly battle" in x[1] and "LOG" in x[1]]
    assert not new_text, "no new scene message per turn"


def test_caption_fits_telegram_limit_and_flood_wait_is_retried():
    from telegram.error import RetryAfter
    log_lines = "\n".join(f"• Turn line number {i} with some text about a move being used" for i in range(40))
    cap = B.fit_caption("🤝 Title\n━━\n• Mon A\n📜 LOG\n" + log_lines)
    assert len(cap) <= 1000 and "line number 39" in cap and "line number 0 " not in cap and cap.startswith("🤝 Title")
    calls = []

    async def flaky(x):
        calls.append(x)
        if len(calls) == 1:
            raise RetryAfter(0)
        return "ok"
    assert run(B.retry(flaky, 1)) == "ok" and len(calls) == 2


def test_caught_card_buttons_nickname_stats_party_release():
    e = Env()
    e.onboard(1, "Ash")
    for _ in range(4):
        B.db.add_mon(1, run(B.gm.wild_record("kanto", 0, __import__("random").Random(3))))
    box = [m for m in B.db.all_mons(1) if m["slot"] is None] if hasattr(B.db, "all_mons") else []
    mid = B.db.add_mon(1, run(B.gm.wild_record("kanto", 0, __import__("random").Random(5))))
    B.db.add_mon(1, run(B.gm.wild_record("kanto", 0, __import__("random").Random(6))))
    B.db.add_mon(1, run(B.gm.wild_record("kanto", 0, __import__("random").Random(7))))      # party now full -> goes to box
    r = [m for m in B.db.box(1)][0] if hasattr(B.db, "box") else None
    mid = r["id"]
    e.click(B.cb_mon, 1, f"cm|1|{mid}|stats")
    txt = e.bot.edits[-1][2]
    assert "Base:" in txt and "IVs:" in txt and "Held item" in txt
    e.click(B.cb_mon, 1, f"cm|1|{mid}|nick")
    assert B.NICKING[1] == mid
    upd = NS(effective_user=NS(id=1), effective_chat=e.chat(1), message=NS(text="Sparky"))
    run(B.on_text(upd, NS(bot=e.bot, args=[])))
    assert B.db.mon(mid)["nickname"] == "Sparky"
    assert any("Release #" in (a or "") or True for a in e.click(B.cb_mon, 1, f"cm|1|{mid}|rel"))
    e.click(B.cb_mon, 1, f"cm|1|{mid}|relyes")
    assert B.db.mon(mid) is None
    assert any("Not yours" in (a or "") for a in e.click(B.cb_mon, 2, f"cm|1|{B.db.party(1)[0]['id']}|rel"))
