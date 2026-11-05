"""Telegram front-end: commands, inline keyboards, message plumbing."""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import logging
import os
import random
import time
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.constants import ChatType
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError, TimedOut
from telegram.ext import Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, TypeHandler, filters

from . import config, extras, game as gm, images, quests, sprites, world
from .data import get_provider
from .db import DB
from . import dex as dexmod, eggs as eggmod, info as infomod, tournament as tmod
from .game import pretty
from .session import Game, Out, caught_text, mon_kb

log = logging.getLogger("pokebot")

db: DB = None            # set in build_app / tests
G: Game = None
STARTER_REGIONS = world.STARTER_REGIONS
MSG: Dict[int, Tuple[int, int]] = {}                  # user id -> (chat_id, message_id) of their control message
LOCKS: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
CHALLENGES: Dict[str, tuple] = {}      # id -> (challenger, target, chat_id, rules)

BATTLE_OPS = "mgswxbcrfpnlt"
DIV = "━━━━━━━━━━━━━━"
FRAME_DELAY = 0.9          # seconds between catch-suspense frames


def bar(hp: int, mh: int, width: int = 10) -> str:
    n = 0 if hp <= 0 else max(1, round(hp / max(1, mh) * width))
    return "█" * n + "░" * (width - n)


def lines_of(*parts) -> str:
    """Join strings / lists of strings into one message (lists are flattened)."""
    out = []
    for p in parts:
        out += p if isinstance(p, list) else [p]
    return "\n".join(out)


def init(database: Optional[DB] = None):
    global db, G
    db = database or DB()
    G = Game(db)


def money(n: int) -> str:
    return f"${n:,}"


def markup(rows) -> Optional[InlineKeyboardMarkup]:
    if not rows:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows])


def item_key(text: str) -> str:
    return text.strip().lower().replace(" ", "-").replace("é", "e")


async def reply(update: Update, text: str, rows=None):
    return await update.effective_chat.send_message(text[:4000], reply_markup=markup(rows))


async def me(update: Update) -> Optional[dict]:
    u = update.effective_user
    p = db.player(u.id)
    if not p:
        await reply(update, "You haven't started your journey yet! Send /start in a private chat with me.")
    return p


async def send_sprite(bot, chat_id: int, species_id: int, back: bool = False):
    spec = sprites.sprite_for(species_id, back=back)
    if not spec:
        return
    url, kind = spec
    try:
        if kind == "animation":
            await bot.send_animation(chat_id, url)
        else:
            await bot.send_photo(chat_id, url)
    except TelegramError as e:      # sprites are decoration; never fail a command over them
        log.info("sprite failed for %s: %s", species_id, e)


PHOTO: Dict[Any, tuple] = {}        # uid (or "pvp<id>") -> (chat_id, message_id, key) of the live battle picture


async def update_scene(bot, uid, chat_id: int, spec: dict, new: bool):
    """Send the battle picture, or swap it in place when something changed. Pictures are decoration: never fail."""
    key = repr([sorted(spec[k].items()) if spec.get(k) else None for k in ("foe", "own", "foe2", "own2")]) + spec["weather"] + spec["terrain"]
    cur = PHOTO.get(uid)
    if cur and not new and cur[2] == key:
        return
    try:
        png = await images.render_battle(spec["foe"], spec["own"], spec["weather"], spec["terrain"], spec.get("foe2"), spec.get("own2"),
                                         public=bool(spec.get("public")))
        if cur and not new:
            await retry(bot.edit_message_media, chat_id=cur[0], message_id=cur[1], media=InputMediaPhoto(io.BytesIO(png)))
            PHOTO[uid] = (cur[0], cur[1], key)
        else:
            m = await retry(bot.send_photo, chat_id, io.BytesIO(png))
            PHOTO[uid] = (chat_id, m.message_id, key)
    except Exception as e:       # noqa: BLE001
        log.info("battle picture failed: %s", e)


def fit_caption(text: str, limit: int = 1000) -> str:
    """Photo captions hold 1024 characters: keep the header and as many of the newest log lines as fit."""
    if len(text) <= limit:
        return text
    head, _, logpart = text.partition("📜 LOG")
    lines = [x for x in logpart.split("\n") if x.strip()]
    keep: List[str] = []
    for ln in reversed(lines):
        if len("\n".join([head.rstrip(), "📜 LOG"] + [ln] + keep)) > limit:
            break
        keep.insert(0, ln)
    return "\n".join([head.rstrip(), "📜 LOG"] + keep)[:limit]


async def retry(call, *a, **kw):
    """Run a Telegram call. Flood control: wait as long as Telegram asks (then try again, up to 3 times) so the screen is never left stale;
    a dropped connection or timeout gets one quick retry."""
    for attempt in range(3):
        try:
            return await call(*a, **kw)
        except RetryAfter as e:
            ra = e.retry_after
            wait = ra.total_seconds() if hasattr(ra, "total_seconds") else float(ra)
            if attempt == 2:
                raise
            log.info("flood control: waiting %.0fs", wait)
            await asyncio.sleep(min(wait + 1, 60))
        except (TimedOut, NetworkError):
            if attempt >= 1:
                raise
            await asyncio.sleep(1)


SHOWN: Dict[tuple, str] = {}        # (chat, message) -> what it currently shows, so identical edits are skipped (saves Telegram calls)


async def pvp_scene(bot, chat_id: int, spec: dict, text: str):
    """ONE message per friendly battle: the battle picture with the scene and log as its caption, edited every turn."""
    key = "pvp" + spec["pid"]
    pkey = repr([sorted(spec[k].items()) if spec.get(k) else None for k in ("foe", "own", "foe2", "own2")]) + spec["weather"] + spec["terrain"]
    cap = fit_caption(text)
    cur = PHOTO.get(key)                                   # (chat id, message id, picture key, caption)
    try:
        if cur and cur[2] == pkey and cur[3] == cap:
            return
        if cur and cur[2] == pkey:                         # same picture, new log: only the caption changes
            await retry(bot.edit_message_caption, caption=cap, chat_id=cur[0], message_id=cur[1])
            PHOTO[key] = (cur[0], cur[1], pkey, cap)
            return
        png = await images.render_battle(spec["foe"], spec["own"], spec["weather"], spec["terrain"], spec.get("foe2"), spec.get("own2"), public=True)
        if cur:
            await retry(bot.edit_message_media, chat_id=cur[0], message_id=cur[1], media=InputMediaPhoto(io.BytesIO(png), caption=cap))
            PHOTO[key] = (cur[0], cur[1], pkey, cap)
        else:
            m = await retry(bot.send_photo, chat_id, io.BytesIO(png), caption=cap)
            PHOTO[key] = (chat_id, m.message_id, pkey, cap)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            log.info("friendly scene failed: %s", e)
    except Exception as e:       # noqa: BLE001 - the battle goes on even when the picture can't be drawn
        log.info("friendly scene failed: %s", e)
        try:
            await bot.send_message(chat_id, text[:4000])
        except TelegramError:
            pass


async def apply_outs(bot, outs: List[Out], chat_id: int, kind: str):
    for o in outs:
        if o.img and o.to != "chat":
            await update_scene(bot, o.to, chat_id, o.img, o.new)
            if o.new:
                o = o._replace(photo=None)
        if o.to == "chat":
            if o.img and o.img.get("pid"):
                await pvp_scene(bot, chat_id, o.img, o.text)
                continue
            try:
                await retry(bot.send_message, chat_id, o.text[:4000])
            except TelegramError as e:
                log.warning("public send failed: %s", e)
            continue
        uid = o.to
        rm = markup(o.kb)
        target = chat_id          # everything happens in the chat where the battle was started
        if o.new or uid not in MSG:
            if o.photo:
                await send_sprite(bot, target, o.photo[0], o.photo[1])
            try:
                m = await retry(bot.send_message, target, o.text[:4000], reply_markup=rm)
                MSG[uid] = (target, m.message_id)
                SHOWN[(target, m.message_id)] = o.text[:4000] + repr(o.kb)
            except TelegramError as e:
                log.warning("send failed: %s", e)
        else:
            cid, mid = MSG[uid]
            for frame in (o.frames or []):          # suspense: ". . ." before the catch result
                try:
                    await bot.edit_message_text(frame, chat_id=cid, message_id=mid)
                except TelegramError:
                    pass
                await asyncio.sleep(FRAME_DELAY)
            sig = o.text[:4000] + repr(o.kb)
            if SHOWN.get((cid, mid)) == sig:
                continue                                  # nothing changed: don't spend a Telegram call
            try:
                await retry(bot.edit_message_text, o.text[:4000], chat_id=cid, message_id=mid, reply_markup=rm)
                SHOWN[(cid, mid)] = sig
            except BadRequest as e:
                if "not modified" not in str(e).lower():
                    log.warning("edit failed: %s", e)
            except TelegramError as e:
                log.warning("edit failed: %s", e)
    if G.tourney_results:
        await tourney_sync(bot)


# ======================================================================= onboarding
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db.create_player(u.id, u.first_name or u.username or f"Trainer{u.id}")
    if db.count_mons(u.id) > 0:
        await reply(update, f"Welcome back, {db.player(u.id)['name']}! Use /help to see what you can do.")
        return
    NAMING.add(u.id)
    await reply(update, f"👋 Welcome, Trainer! Professor Oak is waiting.\n\n✏️ What's your trainer name? Send it as a message "
                        f"(max {NAME_MAX} characters) - or skip to keep \"{db.player(u.id)['name']}\".\n\nFirst things first - are you a...", GENDER_ROWS(u.id))


NAMING: set = set()          # players whose next plain message is their trainer name
NAME_MAX = 12


def clean_name(raw: str) -> str:
    s = " ".join(raw.split())
    s = "".join(c for c in s if c.isprintable() and ord(c) < 0x250)       # the card font is Latin-only
    return s[:NAME_MAX].strip()


async def cmd_name(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    raw = " ".join(ctx.args or [])
    if not raw:
        NAMING.add(p["user_id"])
        await reply(update, f"✏️ Send me your new trainer name (max {NAME_MAX} characters).")
        return
    await set_trainer_name(update, p["user_id"], raw)


async def set_trainer_name(update: Update, uid: int, raw: str):
    n = clean_name(raw)
    if len(n) < 2:
        await reply(update, "That name won't work - use 2-12 normal letters/numbers. Try again!")
        return
    NAMING.discard(uid)
    db.set_name(uid, n)
    await reply(update, f"✅ Nice to meet you, {n}! It's on your /card now.")


# ======================================================================= who's that pokemon
WHO: Dict[int, dict] = {}              # chat id -> the live round
WHO_REWARD, WHO_SECONDS, WHO_DAILY_CAP = 20, 45, 15
WHO_WINS: Dict[tuple, int] = {}        # (uid, day) -> rewards paid today


def _norm(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum())


async def cmd_whosthat(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    cid = update.effective_chat.id
    live = WHO.get(cid)
    if live and time.time() - live["t"] < WHO_SECONDS:
        await reply(update, "A round is already running - guess with /guess <name> (or just type it)!")
        return
    sid = random.randint(1, 1025)
    try:
        sp = await get_provider().get_species(sid)
        png = await images.dex_picture(sid, False)
    except Exception:                                     # noqa: BLE001
        png = None
    if not png:
        await reply(update, "Couldn't load a picture right now - try again!")
        return
    rnd = {"sid": sid, "name": pretty(sp.name), "ok": {_norm(sp.name), _norm(sp.name.split("-")[0])}, "t": time.time()}
    WHO[cid] = rnd
    await ctx.bot.send_photo(cid, io.BytesIO(png), caption=f"❓ Who's that Pokémon?\nFirst correct guess wins {money(WHO_REWARD)}! "
                             f"Type the name or /guess <name> ({WHO_SECONDS}s)")

    async def timeout():
        await asyncio.sleep(WHO_SECONDS)
        if WHO.get(cid) is rnd:
            WHO.pop(cid, None)
            await ctx.bot.send_message(cid, f"⏰ Time's up! It was {rnd['name']}!")

    asyncio.create_task(timeout())


async def check_guess(update: Update, ctx, text: str) -> bool:
    cid = update.effective_chat.id
    rnd = WHO.get(cid)
    u = update.effective_user
    if not rnd or not u or _norm(text) not in rnd["ok"]:
        return False
    p = db.player(u.id)
    if not p:
        return False
    WHO.pop(cid, None)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    paid = WHO_WINS.get((u.id, day), 0) < WHO_DAILY_CAP
    if paid:
        WHO_WINS[(u.id, day)] = WHO_WINS.get((u.id, day), 0) + 1
        db.add_money(u.id, WHO_REWARD)
    cap = f"✅ {p['name']} got it - it's {rnd['name']}! " + (f"+{money(WHO_REWARD)}" if paid else f"(daily reward limit of {WHO_DAILY_CAP} reached)")
    try:
        png = await images.dex_picture(rnd["sid"], True)
        await ctx.bot.send_photo(cid, io.BytesIO(png), caption=cap)
    except Exception:                                     # noqa: BLE001
        await ctx.bot.send_message(cid, cap)
    return True


async def cmd_guess(update: Update, ctx):
    if not WHO.get(update.effective_chat.id):
        await reply(update, "No round running - start one with /whosthat!")
        return
    if not await check_guess(update, ctx, " ".join(ctx.args or [])):
        await reply(update, "❌ Nope, try again!")


async def on_text(update: Update, ctx):
    u = update.effective_user
    if not u or not update.message or not update.message.text:
        return
    if update.effective_chat.id in WHO and await check_guess(update, ctx, update.message.text):
        return
    if u.id in NICKING:
        r = db.mon(NICKING.pop(u.id))
        nick = " ".join(update.message.text.split())[:20]
        if r and r["owner"] == u.id and nick and not nick.startswith("/"):
            db.update_mon(r["id"], nickname=nick)
            r = db.mon(r["id"])
            await reply(update, f"✅ {r['species_name']} is now called {nick}!\n\n{caught_text(r)}", mon_kb(u.id, r["id"], r["slot"] is not None))
        return
    if u.id not in NAMING:
        return
    if update.effective_chat.type != ChatType.PRIVATE or not db.player(u.id):
        return
    await set_trainer_name(update, u.id, update.message.text)


def GENDER_ROWS(uid: int):
    return [[("👦 Boy", f"gd|{uid}|boy"), ("👧 Girl", f"gd|{uid}|girl")], [("🎲 Surprise me!", f"gd|{uid}|any")]]


def region_rows(uid: int):
    return [[(r.name, f"st|{uid}|{r.key}")] for r in world.REGIONS[:STARTER_REGIONS]]


REGION_PROMPT = ("Choose the region where your journey begins "
                 "(Kalos, Alola, Galar and Paldea open later):")


async def cmd_trainer(update: Update, ctx):
    p = await me(update)
    if p:
        await reply(update, "🎨 Pick a new look!", GENDER_ROWS(p["user_id"]))


async def cb_look(update: Update, ctx):
    """gd|uid|boy/girl/any -> trainer list; tp|uid|slug -> save (then region menu when onboarding)."""
    q = update.callback_query
    parts = q.data.split("|")
    uid = int(parts[1])
    if q.from_user.id != uid:
        await q.answer("This isn't your choice!", show_alert=True)
        return
    await q.answer()
    onboarding = db.count_mons(uid) == 0
    if parts[0] == "gd":
        g = parts[2]
        if g == "any":
            import random
            g = random.choice(["boy", "girl"])
            slug = images.random_trainer(g)
            db.set_trainer(uid, g, slug)
            await _look_done(q, uid, slug, onboarding, surprise=True)
            return
        pairs = images.trainers_for(g)
        rows = [[(pairs[i][1], f"tp|{uid}|{pairs[i][0]}")] + ([(pairs[i + 1][1], f"tp|{uid}|{pairs[i + 1][0]}")] if i + 1 < len(pairs) else [])
                for i in range(0, len(pairs), 2)]
        await q.edit_message_text("Who do you want to be?", reply_markup=markup(rows))
    else:
        slug = parts[2]
        if slug not in images.TRAINERS:
            return
        db.set_trainer(uid, "boy" if slug in dict(images.BOY_TRAINERS) else "girl", slug)
        await _look_done(q, uid, slug, onboarding)


async def _look_done(q, uid: int, slug: str, onboarding: bool, surprise: bool = False):
    who = images.TRAINERS[slug]
    if onboarding:
        await q.edit_message_text(f"{'🎲 Fate says: ' if surprise else '✨ '}you're {who}!\n\n{REGION_PROMPT}",
                                  reply_markup=markup(region_rows(uid)))
    else:
        await q.edit_message_text(f"✨ Looking sharp, {who}! Check /card.")


async def starters_for(region_key: str):
    p = get_provider()
    reg = world.REGION_BY_KEY[region_key]
    return [(sid, pretty((await p.get_species(sid)).name)) for sid in reg.starters]


async def cb_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split("|")
    uid = int(parts[1])
    if q.from_user.id != uid:
        await q.answer("This isn't your choice!", show_alert=True)
        return
    await q.answer()
    if db.count_mons(uid) > 0:
        await q.edit_message_text("You already have a starter!")
        return
    if parts[0] == "st":
        if parts[2] not in [r.key for r in world.REGIONS[:STARTER_REGIONS]]:
            return
        names = await starters_for(parts[2])
        rows = [[(n, f"sp|{uid}|{sid}")] for sid, n in names]
        await q.edit_message_text("Choose your starter Pokémon:", reply_markup=markup(rows))
    else:
        sid = int(parts[2])
        home = next((r for r in world.REGIONS[:STARTER_REGIONS] if sid in r.starters), None)
        if home is None:
            return
        import random
        rec = await gm.new_record(sid, 5, random.Random())
        db.add_mon(uid, rec)
        for k, n in (("poke-ball", 30), ("great-ball", 15), ("ultra-ball", 10)):
            db.add_item(uid, k, n)
        db.set_region(uid, home.key)                     # your journey starts in the region you picked
        await q.edit_message_text(
            f"🎉 You chose {rec['species_name']}! You also received 30 Poké Balls, 15 Great Balls and 10 Ultra Balls.\n\n"
            "Try /explore to find wild Pokémon, /gym to challenge the first leader, and /help for everything else.")
        await send_sprite(ctx.bot, q.message.chat_id, sid)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text, rows = infomod.help_home()
    await reply(update, text, rows)


async def cb_help(update: Update, ctx):
    q = update.callback_query
    await q.answer()
    key = q.data.split("|")[1]
    text, rows = infomod.help_home() if key == "home" else infomod.help_section(key)
    try:
        await q.edit_message_text(text, reply_markup=markup(rows))
    except BadRequest:
        pass


async def cmd_info(update: Update, ctx):
    if ctx.args:
        res = infomod.search(" ".join(ctx.args))
        await reply(update, res or "I couldn't find that. Try /info and browse abilities, natures and held items.")
        return
    text, rows = infomod.info_home()
    await reply(update, text, rows)


async def cb_info(update: Update, ctx):
    q = update.callback_query
    await q.answer()
    _, kind, page = q.data.split("|")
    text, rows = infomod.info_view(kind, int(page))
    try:
        await q.edit_message_text(text[:4000], reply_markup=markup(rows))
    except BadRequest:
        pass


async def cmd_gimmicks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await reply(update, lines_of(
        "✨ BATTLE GIMMICKS", DIV,
        ["• Buy the key item in /shop to unlock one", "• Each trainer may use ONE gimmick per battle"], "",
        "🧬 MEGA EVOLUTION — Mega Bracelet",
        ["• Pokémon with a Mega form gain stats, typing and ability", "• Buy its Mega Stone in /shop, /give it, and choose Mega before the fight"], "",
        "💥 Z-MOVE — Z-Ring",
        ["• Turns a damaging move into a strong, never-missing Z-Move", "• Buy a Z-Crystal in /shop and /give it to the holder"], "",
        "🔴 DYNAMAX — Dynamax Band",
        ["• 3 turns of 1.5× HP", "• Moves become Max Moves with side effects"], "",
        "💎 TERASTALLIZE — Tera Orb",
        ["• Become your Tera type", "• Boosts STAB (2× if it matches an original type)"], "",
        "🏟 Gym leaders in Kalos, Alola, Galar and Paldea use their region's gimmick on their ace!"))


# ======================================================================= info
def hp_str(rec, mh):
    return f"{rec['hp']}/{mh}" if rec["hp"] > 0 else f"FAINTED (0/{mh})"


async def mon_line(rec) -> str:
    mh = await gm.max_hp_of(rec)
    name = rec.get("nickname") or rec["species_name"]
    return f"#{rec['id']} {name} Lv{rec['level']} — HP {hp_str(rec, mh)}"


def badge_rows(uid: int, p: dict):
    got = {(r["region"], r["idx"]) for r in db.badges(uid)}
    done = set(db.conquered(uid))
    open_ = {r.key for r in unlocked_regions(uid)}
    btns = []
    for r in world.REGIONS:
        n = sum((r.key, i) in got for i in range(8))
        label = f"🏆 {r.name}" if r.key in done else (f"{r.name} {n}/8" if r.key in open_ else f"🔒 {r.name}")
        btns.append((label, f"bc|{uid}|{r.key}"))
    return [btns[i:i + 3] for i in range(0, len(btns), 3)]


def dex_title(n: int) -> str:
    got = [v[3] for k, v in world.DEX_MILESTONES.items() if n >= k]
    return got[-1] if got else "Newcomer"


def card_caption(p: dict) -> str:
    n = db.dex_count(p["user_id"])
    return (f"🪪 {p['name']}'s Trainer Card\n📖 Pokédex: {n} caught · {dex_title(n)}\n"
            "Tap a region to peek into your badge case 👇")


async def cmd_card(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    uid = p["user_id"]
    fav = db.favourite(uid)
    inv = db.inventory(uid)
    rg = world.REGION_BY_KEY[p["region"]]
    png = await images.render_card(
        name=p["name"], color_idx=p["card_color"] if p.get("card_color") is not None else uid, region_key=rg.key, region_name=rg.name, trainer=p.get("trainer") or "red",
        money=p["money"], balls=sum(inv.get(k, 0) for k in world.BALL_KEYS), owned=db.count_mons(uid),
        wins=p["wins"], losses=p["losses"], since=time.strftime("%d %b %Y", time.localtime(p.get("created") or time.time())),
        fav_species=fav["species_id"] if fav else None,
        fav_name=(fav.get("nickname") or fav["species_name"]) if fav else "", shiny=bool(fav and fav.get("shiny")),
        badges=db.total_badges(uid))
    await update.effective_message.reply_photo(io.BytesIO(png), caption=card_caption(p), reply_markup=markup(badge_rows(uid, p)))


async def cb_badgecase(update: Update, ctx):
    q = update.callback_query
    _, uid, key = q.data.split("|")
    uid = int(uid)
    if q.from_user.id != uid:
        await q.answer("That's someone else's card!", show_alert=True)
        return
    p = db.player(uid)
    if key == "home":
        await q.answer()
        await q.edit_message_caption(card_caption(p), reply_markup=markup(badge_rows(uid, p)))
        return
    reg = world.REGION_BY_KEY[key]
    if reg not in unlocked_regions(uid):
        await q.answer("🔒 Conquer any region first to open this one!", show_alert=True)
        return
    await q.answer()
    got = {r["idx"] for r in db.badges(uid) if r["region"] == key}
    crown = " 🏆 Conquered!" if key in db.conquered(uid) else ""
    head = f"🏝 {reg.name.upper()} ISLAND TRIALS" if key == "alola" else f"🎖 {reg.name.upper()} BADGES"
    lines = [f"{head}  {len(got)}/8{crown}", DIV]
    for i, g in enumerate(reg.gyms):
        lines.append(f"{'✅' if i in got else '▫️'} {g.badge} - {g.leader} ({g.town})")
    await q.edit_message_caption("\n".join(lines), reply_markup=markup([[("⬅️ Back", f"bc|{uid}|home")]]))


cmd_profile = cmd_card


async def cmd_fav(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    r = own_mon(p["user_id"], ctx.args[0] if ctx.args else "")
    if not r:
        await reply(update, "Usage: /fav <pokemon id>  — shown on your /card")
        return
    db.set_fav(p["user_id"], r["id"])
    await reply(update, f"⭐ {r.get('nickname') or r['species_name']} is now your favorite Pokémon!")


async def cmd_party(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    party = db.party(p["user_id"])
    out = [f"👥 YOUR PARTY ({len(party)}/{config.MAX_PARTY})", DIV]
    for i, r in enumerate(party, 1):
        mh = await gm.max_hp_of(r)
        name = r.get("nickname") or r["species_name"]
        out += [f"{i}. {name} Lv{r['level']} — #{r['id']}",
                f"   • HP {hp_str(r, mh)}  {bar(r['hp'], mh)}",
                f"   • Moves: {', '.join(pretty(m) for m in r['moves'])}"]
    await reply(update, "\n".join(out))


async def cmd_box(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    page = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 1
    box = db.box(p["user_id"])
    per = 15
    pages = max(1, -(-len(box) // per))
    page = max(1, min(page, pages))
    out = [f"📦 BOX — page {page}/{pages} ({len(box)} Pokémon)", DIV]
    for r in box[(page - 1) * per: page * per]:
        out.append("• " + await mon_line(r))
    if not box:
        out.append("• Empty")
    await reply(update, "\n".join(out))


def own_mon(uid: int, arg: str):
    if not arg or not arg.isdigit():
        return None
    r = db.mon(int(arg))
    return r if r and r["owner"] == uid and not r["listed"] else None


def ev_line(r) -> str:
    evs = r.get("evs") or {}
    if not evs:
        return "none yet"
    return " · ".join(f"{k.upper()} {v}" for k, v in evs.items() if v) + f"  ({sum(evs.values())}/{gm.EV_TOTAL})"


async def mon_text(r: dict) -> str:
    form = await get_provider().get_form(gm.fkey(r))
    st = gm.calc_stats(form.stats, r["level"], r["iv"], r.get("evs"), r.get("nature"))
    nxt = (r["level"] + 1) ** 3
    iv_pct = round(sum(r["iv"].values()) / (31 * 6) * 100)
    nick = f" \"{r['nickname']}\"" if r.get("nickname") else ""
    bs, iv = form.stats, r["iv"]
    order = [("hp", "HP"), ("atk", "ATK"), ("def", "DEF"), ("spa", "SPA"), ("spd", "SPD"), ("spe", "SPE")]
    return lines_of(
        f"🔎 #{r['id']} {'✨ ' if r.get('shiny') else ''}{r['species_name']}{nick} — Lv{r['level']}", DIV,
        [f"• Type: {'/'.join(t.title() for t in form.types)}", f"• Tera type: {r['tera_type'].title()}",
         *([f"• Form: {gm.form_label(r['form'])}"] if r.get("form") else []),
         f"• Nature: {gm.nature_text(r.get('nature') or 'Hardy')}", f"• Held item: {world.ITEMS[r['item']].name if r.get('item') in world.ITEMS else 'none'}", f"• Ability: {pretty(r['ability'])}" + (f" — {infomod.ability_desc(r['ability'])}" if infomod.ability_desc(r['ability']) else ""), f"• HP: {hp_str(r, st['hp'])}", f"• XP: {r['xp']}/{nxt}",
         f"• Stats: ATK {st['atk']} · DEF {st['def']} · SPA {st['spa']} · SPD {st['spd']} · SPE {st['spe']}",
         "• Base: " + " · ".join(f"{n} {bs.get(k, 0)}" for k, n in order),
         "• IVs: " + " · ".join(f"{n} {iv.get(k, 0)}" for k, n in order) + f"  ({iv_pct}%)",
         f"• EVs: {ev_line(r)}", f"• Friendship: {r.get('friendship', 70)}/255",
         f"• Moves: {', '.join(pretty(m) for m in r['moves'])}"])


async def cmd_mon(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    r = own_mon(p["user_id"], ctx.args[0] if ctx.args else "")
    if not r:
        await reply(update, "Usage: /mon <id>  (IDs are shown in /party and /box)")
        return
    await send_sprite(ctx.bot, update.effective_chat.id, r["species_id"])
    await reply(update, await mon_text(r), mon_kb(p["user_id"], r["id"], r["slot"] is not None, stats=False))


NICKING: Dict[int, int] = {}          # uid -> Pokémon id waiting for a nickname


async def cb_mon(update: Update, ctx):
    """cm|uid|mid|act - the button card under a caught Pokémon and /mon."""
    q = update.callback_query
    _, uid, mid, act = q.data.split("|")
    uid, mid = int(uid), int(mid)
    if q.from_user.id != uid:
        await q.answer("Not yours!", show_alert=True)
        return
    r = db.mon(mid)
    if not r or r["owner"] != uid or r["listed"]:
        await q.answer("That Pokémon isn't available.", show_alert=True)
        return
    inp = r["slot"] is not None
    card = lambda: (caught_text(r), mon_kb(uid, mid, inp))
    if act in ("party", "rel", "relyes") and db.league_run(uid):
        await q.answer("Your team is locked during a League run.", show_alert=True)
        return
    await q.answer()
    if act == "nick":
        NICKING[uid] = mid
        await q.edit_message_text(f"✏️ Send the new nickname for {r['species_name']} (max 20 characters) as a message.",
                                  reply_markup=markup([[("↩ Back", f"cm|{uid}|{mid}|back")]]))
    elif act == "stats":
        await q.edit_message_text(await mon_text(r), reply_markup=markup([[("↩ Back", f"cm|{uid}|{mid}|back")]]))
    elif act == "party":
        ok = db.move_to_box(uid, mid) if inp else db.move_to_party(uid, mid)
        if not ok:
            await q.answer("Party is full (6) - move one to the box first." if not inp else "Keep at least one Pokémon in your party.", show_alert=True)
            return
        r = db.mon(mid)
        await q.edit_message_text(*[caught_text(r)], reply_markup=markup(mon_kb(uid, mid, r["slot"] is not None)))
    elif act == "rel":
        await q.edit_message_text(f"Release #{mid} {r.get('nickname') or r['species_name']} Lv{r['level']} forever?",
                                  reply_markup=markup([[("Yes, release", f"cm|{uid}|{mid}|relyes"), ("Cancel", f"cm|{uid}|{mid}|back")]]))
    elif act == "relyes":
        ok = db.release_mon(uid, mid)
        await q.edit_message_text("Released. Bye-bye! 👋" if ok else "Can't release that (is it your last party member?).")
    elif act == "done":
        NICKING.pop(uid, None)
        await q.edit_message_text(caught_text(r).rsplit("\n", 1)[0] + "\n✅ Kept!")
    else:
        NICKING.pop(uid, None)
        await q.edit_message_text(*card()[:1], reply_markup=markup(card()[1]))


async def cmd_give(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    uid = p["user_id"]
    key = item_key(" ".join(ctx.args[:-1])) if len(ctx.args) >= 2 else ""
    it = world.ITEMS.get(key)
    r = own_mon(uid, ctx.args[-1]) if ctx.args else None
    if not it or it.kind not in world.HELD_KINDS or not r:
        await reply(update, "Usage: /give <held item> <pokemon id>  — e.g. /give leftovers 12 (buy items in /shop → Held Items)")
        return
    if G.active(uid):
        await reply(update, "Not during a battle!")
        return
    if it.kind == "mega":
        sp = await get_provider().get_species(r["species_id"])
        base, form = world.MEGA_STONE_FOR[key]
        if form not in sp.mega_forms and sp.name != base.lower().replace(" ", "-"):
            await reply(update, f"{it.name} only works on {base}!")
            return
    if it.kind == "zcrystal":
        kind, who, _ = world.Z_CRYSTAL_FOR[key]
        sp = await get_provider().get_species(r["species_id"])
        if kind == "sig" and not any(u.lower().replace(" ", "-") == sp.name or u.lower().replace(" ", "-").startswith(sp.name + "-")
                                     for u in who.split("|")):
            await reply(update, f"{it.name} only works on {who.replace('|', ' / ')}!")
            return
    if not db.take_item(uid, key, 1):
        await reply(update, f"You don't have a {it.name}. Buy one in /shop.")
        return
    old = r.get("item")
    if old:
        db.add_item(uid, old, 1)
    db.x("UPDATE mons SET item=? WHERE id=?", (key, r["id"]))
    await reply(update, f"🎒 {r.get('nickname') or r['species_name']} is now holding {it.name}." + (f" ({world.ITEMS[old].name} went back to your bag.)" if old in world.ITEMS else "")
                + ("\n🧬 Remember: Mega Evolution also needs the Mega Bracelet and choosing Mega before the fight." if it.kind == "mega" else "")
                + ("\n🔶 Remember: Z-Moves also need the Z-Ring and choosing Z before the fight." if it.kind == "zcrystal" else ""))


async def cmd_take(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    r = own_mon(p["user_id"], ctx.args[0] if ctx.args else "")
    if not r or G.active(p["user_id"]):
        await reply(update, "Usage: /take <pokemon id> — returns its held item to your bag (not during a battle).")
        return
    if r.get("item") not in world.ITEMS:
        await reply(update, "It isn't holding anything.")
        return
    db.add_item(p["user_id"], r["item"], 1)
    db.x("UPDATE mons SET item='' WHERE id=?", (r["id"],))
    await reply(update, f"Took {world.ITEMS[r['item']].name} back to your bag.")


async def cmd_nick(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    r = own_mon(p["user_id"], ctx.args[0] if ctx.args else "")
    nick = " ".join(ctx.args[1:]).strip()[:20] if ctx.args else ""
    if not r or not nick:
        await reply(update, "Usage: /nick <id> <new name>")
        return
    db.update_mon(r["id"], nickname=nick)
    await reply(update, f"Your {r['species_name']} is now called {nick}!")


async def cmd_addparty(update: Update, ctx):
    p = await me(update)
    if p and await team_locked(update, p["user_id"]):
        return
    if not p:
        return
    r = own_mon(p["user_id"], ctx.args[0] if ctx.args else "")
    if not r or r["slot"] is not None:
        await reply(update, "Usage: /addparty <id of a Pokémon in your box>")
        return
    ok = db.move_to_party(p["user_id"], r["id"])
    await reply(update, "Added to your party!" if ok else "Your party is full (6). Use /tobox first.")


async def cmd_tobox(update: Update, ctx):
    p = await me(update)
    if p and await team_locked(update, p["user_id"]):
        return
    if not p:
        return
    r = own_mon(p["user_id"], ctx.args[0] if ctx.args else "")
    if not r or r["slot"] is None:
        await reply(update, "Usage: /tobox <id of a Pokémon in your party>")
        return
    ok = db.move_to_box(p["user_id"], r["id"])
    await reply(update, "Moved to your box." if ok else "You must keep at least one Pokémon in your party.")


async def cmd_swap(update: Update, ctx):
    p = await me(update)
    if p and await team_locked(update, p["user_id"]):
        return
    if not p or len(ctx.args) != 2:
        if p:
            await reply(update, "Usage: /swap <id> <id>  (swaps two party members' order)")
        return
    ok = db.swap_party(p["user_id"], int(ctx.args[0]) if ctx.args[0].isdigit() else -1,
                       int(ctx.args[1]) if ctx.args[1].isdigit() else -1)
    await reply(update, "Swapped!" if ok else "Both must be Pokémon in your party.")


async def cmd_release(update: Update, ctx):
    p = await me(update)
    if p and await team_locked(update, p["user_id"]):
        return
    if not p:
        return
    r = own_mon(p["user_id"], ctx.args[0] if ctx.args else "")
    if not r:
        await reply(update, "Usage: /release <id>")
        return
    uid = p["user_id"]
    await reply(update, f"Release #{r['id']} {r.get('nickname') or r['species_name']} Lv{r['level']} forever?",
                [[("Yes, release", f"rel|{uid}|{r['id']}"), ("Cancel", f"rel|{uid}|no")]])


async def cb_release(update: Update, ctx):
    q = update.callback_query
    _, uid, arg = q.data.split("|")
    if q.from_user.id != int(uid):
        await q.answer("Not yours!", show_alert=True)
        return
    await q.answer()
    if arg == "no":
        await q.edit_message_text("Cancelled.")
        return
    ok = db.release_mon(int(uid), int(arg))
    await q.edit_message_text("Released. Bye-bye!" if ok else "Can't release that (is it your last party member?).")


# ======================================================================= adventure
async def team_locked(update: Update, uid: int) -> bool:
    if db.league_run(uid):
        await reply(update, "🔒 Your team is locked while you're in a League run. Finish it, or leave via /league.")
        return True
    return False


async def busy(update: Update, uid: int) -> bool:
    if G.active(uid):
        await reply(update, "You're in the middle of a battle!")
        return True
    if db.safari(uid):
        await reply(update, "🦒 You're inside the Safari Zone! Use /safari to keep going (or leave it there).")
        return True
    return False


async def cmd_explore(update: Update, ctx):
    p = await me(update)
    if not p or await busy(update, p["user_id"]):
        return
    uid = p["user_id"]
    if len(db.eggs(uid)) < world.EGG_CAP and random.random() < world.EGG_NPC_CHANCE:
        o = await eggmod.offer(p["region"], random)
        EGG_OFFERS[uid] = o
        reg = world.REGION_BY_KEY[o["origin"]]
        await reply(update, lines_of("🧑‍🌾 A wandering Egg Merchant from " + reg.name + "!", DIV,
                                     f"\"Psst! A mystery egg from {reg.name} — only {money(o['price'])}!\""),
                    [[(f"🥚 Buy ({money(o['price'])})", f"eg|{uid}|buy"), ("No thanks", f"eg|{uid}|no")]])
        return
    res = await G.start_wild(uid, update.effective_chat.id)
    if res[0] is None:
        await reply(update, res[1])
        return
    s, outs = res
    await apply_outs(ctx.bot, outs, s.chat_id, s.kind)
    await egg_events(ctx.bot, s.chat_id, uid, p["region"])


async def egg_events(bot, chat_id: int, uid: int, region: str):
    """After an /explore step: hatch ready eggs and maybe stumble upon a new one. Never breaks exploring."""
    try:
        for m in await eggmod.tick(db, uid, random):
            await send_sprite(bot, chat_id, m["species_id"])
            star = "✨ " if m.get("shiny") else ""
            await bot.send_message(chat_id, f"🐣 Oh? Your egg hatched into {star}{m['species_name']}! (ID {m['id']})"
                                   + (f" It inherited {m['inherited']} IVs from its parents." if m["inherited"] else ""))
        if len(db.eggs(uid)) < world.EGG_CAP and random.random() < world.EGG_FIND_CHANCE:
            sid = await eggmod.random_species(region, random)
            eggmod.found(db, uid, region, random, sid)
            await bot.send_message(chat_id, f"🥚 You found a mysterious egg lying in the grass! It will hatch after {world.EGG_STEPS} more /explore trips.")
    except Exception:                                    # noqa: BLE001
        pass


EGG_OFFERS: Dict[int, Dict[str, Any]] = {}


async def cb_egg(update: Update, ctx):
    q = update.callback_query
    _, uid, act = q.data.split("|")
    uid = int(uid)
    if q.from_user.id != uid:
        await q.answer("That merchant is talking to someone else!", show_alert=True)
        return
    o = EGG_OFFERS.pop(uid, None)
    if act == "no" or not o:
        await q.answer()
        await q.edit_message_text("The merchant wanders off… 👋" if act == "no" else "The merchant is gone.")
        return
    if len(db.eggs(uid)) >= world.EGG_CAP:
        await q.answer(f"You can't carry more than {world.EGG_CAP} eggs!", show_alert=True)
        EGG_OFFERS[uid] = o
        return
    if not db.spend(uid, o["price"]):
        await q.answer("You can't afford that.", show_alert=True)
        EGG_OFFERS[uid] = o
        return
    db.add_egg(uid, o["species"], o["origin"], {}, world.EGG_STEPS)
    await q.answer()
    await q.edit_message_text(f"🥚 Deal! You bought a mystery egg from {world.REGION_BY_KEY[o['origin']].name}. See /eggs.")


async def cmd_eggs(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    eggs = db.eggs(p["user_id"])
    out = [f"🥚 YOUR EGGS ({len(eggs)}/{world.EGG_CAP})", DIV]
    for i, e in enumerate(eggs, 1):
        frm = "bred" if e["origin"] == "bred" else world.REGION_BY_KEY[e["origin"]].name
        out.append(f"{i}. Mystery egg ({frm}) — hatches in {e['steps']} /explore trips")
    if not eggs:
        out.append("No eggs yet. Breed two with /breed, or find them while exploring!")
    out += ["", f"💡 /breed <id> <id> — same family (or a Ditto) · {money(world.BREED_COST)}"]
    await reply(update, "\n".join(out))


async def cmd_breed(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    uid = p["user_id"]
    a = own_mon(uid, ctx.args[0]) if len(ctx.args) >= 2 else None
    c = own_mon(uid, ctx.args[1]) if len(ctx.args) >= 2 else None
    if not a or not c:
        await reply(update, f"Usage: /breed <id> <id> — two of your Pokémon from the same family (or one Ditto). Costs {money(world.BREED_COST)}.")
        return
    ok, msg = await eggmod.breed(db, uid, a, c, random)
    await reply(update, msg + (f"\nIt hatches after {world.EGG_STEPS} /explore trips. Babies inherit {world.BREED_IVS} IVs from their parents!" if ok else ""))


async def cmd_gym(update: Update, ctx):
    p = await me(update)
    if not p or await busy(update, p["user_id"]):
        return
    uid = p["user_id"]
    reg = world.REGION_BY_KEY[p["region"]]
    n = db.badge_count(uid, p["region"])
    idx = min(n, 7)
    gym = reg.gyms[idx]
    kind = "ISLAND TRIAL" if reg.key == "alola" else "GYM"
    done = " — rematch (reduced prize) · try /league!" if n >= 8 else ""
    text = lines_of(
        f"🏟 {reg.name.upper()} {kind} {idx + 1}/8{done}", DIV,
        [f"• Leader: {gym.leader} — {gym.town}", f"• Specialty: {gym.type.title()}", f"• Reward: {gym.badge}",
         f"• Team: {world.GYM_TEAM_SIZES[idx]} Pokémon around Lv{world.gym_level(reg.key, idx)}"
         + ("  ⚔️ DOUBLE battle!" if idx >= world.DOUBLES_FROM_GYM else ""),
         f"• Prize: {money(gm.gym_prize(reg.key, idx, n < 8))}"])
    await reply(update, text, [[("⚔️ Challenge!", f"gy|{uid}|go")]])


async def cb_gym(update: Update, ctx):
    q = update.callback_query
    _, uid, _ = q.data.split("|")
    if q.from_user.id != int(uid):
        await q.answer("This challenge isn't yours!", show_alert=True)
        return
    await q.answer()
    if G.active(int(uid)):
        return
    res = await G.start_gym(int(uid), q.message.chat_id)
    if res[0] is None:
        await q.message.reply_text(res[1])
        return
    s, outs = res
    await apply_outs(ctx.bot, outs, s.chat_id, s.kind)


async def cmd_league(update: Update, ctx):
    p = await me(update)
    if not p or await busy(update, p["user_id"]):
        return
    uid = p["user_id"]
    run = db.league_run(uid)
    reg = world.REGION_BY_KEY[run["region"] if run else p["region"]]
    n = db.badge_count(uid, reg.key)
    if not run and n < 8:
        await reply(update, f"🔒 The {reg.name} Pokémon League opens when you have all 8 badges ({n}/8). Use /gym!")
        return
    idx = min(run["cleared"], world.LEAGUE_SIZE - 1) if run else 0
    name, types, title, size, level = world.league_foe(reg.key, idx)
    info = [f"• {title}: {name}", f"• Specialty: {' / '.join(t.title() for t in dict.fromkeys(types))}",
            f"• Team: {size} Pokémon around Lv{level}  ⚔️ DOUBLE battle!",
            f"• Prize: {money(gm.league_prize(reg.key, idx, not db.give_league_seen(uid, reg.key, idx)))}"]
    if run:
        info.append("• Your team is locked until the run ends — healed for free before every fight")
        rows = [[("⚔️ Next fight!", f"lg|{uid}|go")], [("🚪 Leave the League", f"lg|{uid}|leave")]]
    else:
        info.append("• Starting a run locks your current party for all 5 fights. You're fully healed before each one,")
        info.append("  but you can't change your team until you win or lose.")
        rows = [[("⚔️ Enter the League!", f"lg|{uid}|go")]]
    await reply(update, lines_of(f"🏆 {reg.name.upper()} LEAGUE {idx + 1}/{world.LEAGUE_SIZE}", DIV, info), rows)


async def cb_league(update: Update, ctx):
    q = update.callback_query
    _, uid, act = q.data.split("|")
    if q.from_user.id != int(uid):
        await q.answer("This challenge isn't yours!", show_alert=True)
        return
    await q.answer()
    if G.active(int(uid)):
        return
    if act == "leave":
        db.end_league_run(int(uid))
        await q.edit_message_text("🚪 You left the League. Your team is unlocked.")
        return
    res = await G.start_league(int(uid), q.message.chat_id)
    if res[0] is None:
        await q.message.reply_text(res[1])
        return
    s, outs = res
    await apply_outs(ctx.bot, outs, s.chat_id, s.kind)


def unlocked_regions(uid: int):
    conquered = bool(db.conquered(uid))
    return [r for i, r in enumerate(world.REGIONS) if i < STARTER_REGIONS or conquered]


def travel_options(uid: int, current: str):
    """Regions you may travel to right now. You must beat your current region's Champion before moving on;
    until then you can only go back to a region you've already conquered."""
    done = set(db.conquered(uid))
    opened = unlocked_regions(uid)
    if current in done:
        return opened, True
    return [r for r in opened if r.key in done], False


async def cmd_travel(update: Update, ctx):
    p = await me(update)
    if p and await team_locked(update, p["user_id"]):
        return
    if not p or await busy(update, p["user_id"]):
        return
    uid = p["user_id"]
    cur = world.REGION_BY_KEY[p["region"]]
    allowed, free = travel_options(uid, p["region"])
    rows = [[(r.name, f"tr|{uid}|{r.key}")] for r in allowed if r.key != p["region"]]
    info = [f"• Current region: {cur.name}"]
    if not free:
        n = db.badge_count(uid, p["region"])
        info.append(f"• 🔒 You can't leave {cur.name} until you beat its Champion (badges {n}/8, then /league).")
        if not rows:
            info.append("• Finish your journey here first — then the whole world opens up!")
    elif len(unlocked_regions(uid)) < len(world.REGIONS):
        info.append(f"• 🔒 {', '.join(r.name for r in world.REGIONS[STARTER_REGIONS:])}: conquer any region first")
    if rows:
        await reply(update, lines_of("✈️ TRAVEL", DIV, info, "", "Where to?"), rows)
    else:
        await reply(update, lines_of("✈️ TRAVEL", DIV, info))


async def cb_travel(update: Update, ctx):
    q = update.callback_query
    _, uid, key = q.data.split("|")
    uid = int(uid)
    if q.from_user.id != uid:
        await q.answer("Not yours!", show_alert=True)
        return
    if G.active(uid):
        await q.answer("Finish your battle first.", show_alert=True)
        return
    p = db.player(uid)
    if key not in [r.key for r in unlocked_regions(uid)]:
        await q.answer("That region is still locked.", show_alert=True)
        return
    if key not in [r.key for r in travel_options(uid, p["region"])[0]]:
        await q.answer(f"Beat {world.REGION_BY_KEY[p['region']].name}'s Champion before you travel on!", show_alert=True)
        return
    await q.answer()
    db.set_region(uid, key)
    await q.edit_message_text(f"✈️ Welcome to {world.REGION_BY_KEY[key].name}!")


async def cmd_heal(update: Update, ctx):
    p = await me(update)
    if not p or await busy(update, p["user_id"]):
        return
    await gm.heal_party(db, p["user_id"])
    quests.record(db, p["user_id"], "heal")
    await reply(update, "💖 Your Pokémon are fully healed. We hope to see you again!")


async def cmd_daily(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    r = quests.streak_claim(db, p["user_id"])
    if not r:
        await reply(update, "You already claimed today's bonus. Come back tomorrow (UTC)! Check /quests for today's challenges.")
        return
    nxt = 7 - (r["streak"] % 7) if r["streak"] % 7 else 7
    await reply(update, lines_of(f"🎁 DAILY BONUS — 🔥 {r['streak']}-day streak", DIV, r["lines"], "",
                                 f"Balance: {money(db.money(p['user_id']))}   •   Week bonus in {nxt} day(s)",
                                 "Don't miss a day or the streak resets! /quests has today's challenges."))


def bar_text(done: int, goal: int) -> str:
    n = min(goal, done)
    return "▰" * min(n, 10) + "▱" * (min(goal, 10) - min(n, 10)) if goal <= 10 else f"{round(n * 10 / goal) * '▰'}{(10 - round(n * 10 / goal)) * '▱'}"


def quest_view(uid: int):
    q = quests.listing(db, uid)
    st = db.player(uid)
    lines = [f"📜 QUESTS   🔥 Streak: {st.get('streak') or 0}", DIV, "☀️ TODAY"]
    rows = []
    for r in q["d"]:
        icon = "✅" if r["claimed"] else ("🎁" if r["progress"] >= r["goal"] else "▫️")
        lines.append(f"{icon} {r['text']}  {bar_text(r['progress'], r['goal'])} {r['progress']}/{r['goal']}")
        if r["progress"] >= r["goal"] and not r["claimed"]:
            rows.append([(f"🎁 Claim: {r['text'][:28]}", f"qs|{uid}|d|{r['slot']}")])
    lines += ["", "📅 THIS WEEK"]
    for r in q["w"]:
        icon = "✅" if r["claimed"] else ("🎁" if r["progress"] >= r["goal"] else "▫️")
        lines.append(f"{icon} {r['text']}  {bar_text(r['progress'], r['goal'])} {r['progress']}/{r['goal']}")
        if r["progress"] >= r["goal"] and not r["claimed"]:
            rows.append([(f"🎁 Claim weekly: {r['text'][:22]}", f"qs|{uid}|w|{r['slot']}")])
    lines += ["", "Finish all 3 daily quests for a bonus!"]
    return "\n".join(lines), rows


async def cmd_event(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    sid = await gm.event_species(quests.today())
    sp = await get_provider().get_species(sid)
    form = await get_provider().get_form(sid)
    done = p.get("event_claimed") == quests.today()
    await send_sprite(ctx.bot, update.effective_chat.id, sid)
    await reply(update, lines_of("🎉 TODAY'S EVENT POKÉMON", DIV,
                                 [f"• {pretty(sp.name)} (#{sid})", f"• Type: {' / '.join(x.title() for x in form.types)}",
                                  f"• Bonus: {money(world.EVENT_BONUS)} the first time you catch it today", f"• {'✅ Bonus claimed today!' if done else '⏳ Bonus not claimed yet'}"],
                                 "", f"It can appear when you /explore ({round(world.EVENT_CHANCE * 100)}% of encounters). A new one arrives at 00:00 UTC."))


async def cmd_quests(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    text, rows = quest_view(p["user_id"])
    await reply(update, text, rows)


async def cb_quests(update: Update, ctx):
    q = update.callback_query
    _, uid, scope, slot = q.data.split("|")
    uid = int(uid)
    if q.from_user.id != uid:
        await q.answer("These are someone else's quests!", show_alert=True)
        return
    ok, msg = quests.claim(db, uid, scope, int(slot))
    await q.answer()
    text, rows = quest_view(uid)
    await q.edit_message_text(("✅ Reward claimed!\n" + msg + "\n\n" if ok else "") + text, reply_markup=markup(rows))


async def cmd_badges(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    got = {(r["region"], r["idx"]) for r in db.badges(p["user_id"])}
    out = ["🎖 BADGE CASE", DIV]
    for r in world.REGIONS:
        n = sum((r.key, i) in got for i in range(8))
        lg = db.league_count(p["user_id"], r.key)
        crown = " 🏆" if lg >= world.LEAGUE_SIZE else (f" ⭐{lg}/{world.LEAGUE_SIZE}" if lg else "")
        out.append(f"• {r.name}: " + "".join("●" if (r.key, i) in got else "○" for i in range(8)) + f" ({n}/8){crown}")
    await reply(update, "\n".join(out))


LB_CATS = [("badges", "🎖 Badges", "badges"), ("dex", "📖 Pokédex", "caught"), ("money", "💰 Money", ""),
           ("wins", "⚔️ Wins", "wins"), ("pvp", "🥊 PvP", "PvP wins")]


def lb_view(cat: str):
    label = next(c for c in LB_CATS if c[0] == cat)
    medals = ["🥇", "🥈", "🥉"]
    rows = db.top(cat)
    body = [f"{medals[i] if i < 3 else str(i + 1) + '.'} {r['name']} — {money(r['v']) if cat == 'money' else str(r['v']) + ' ' + label[2]}"
            for i, r in enumerate(rows)] or ["Nobody yet!"]
    return lines_of(f"🏆 TOP TRAINERS — {label[1]}", DIV, body), [[(c[1], f"lb|{c[0]}") for c in LB_CATS[:3]], [(c[1], f"lb|{c[0]}") for c in LB_CATS[3:]]]


async def cmd_leaderboard(update: Update, ctx):
    text, rows = lb_view("badges")
    await reply(update, text, rows)


async def cb_leaderboard(update: Update, ctx):
    q = update.callback_query
    await q.answer()
    text, rows = lb_view(q.data.split("|")[1])
    try:
        await q.edit_message_text(text, reply_markup=markup(rows))
    except BadRequest:
        pass


def milestone_view(uid: int):
    have, done = db.dex_count(uid), db.milestones(uid)
    out, rows = ["📖 POKÉDEX MILESTONES", DIV, f"Caught: {have}", ""], []
    for n, (cash, item, qty, title) in world.DEX_MILESTONES.items():
        reward = f"{money(cash)}" + (f" + {qty}× {world.ITEMS[item].name}" if item else "")
        mark = "✅" if n in done else ("🎁" if have >= n else "🔒")
        out.append(f"{mark} {n} — {title}: {reward}")
        if have >= n and n not in done:
            rows.append([(f"🎁 Claim {n}", f"ms|{uid}|{n}")])
    return "\n".join(out), rows


async def cmd_milestones(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    text, rows = milestone_view(p["user_id"])
    await reply(update, text, rows)


async def cb_milestones(update: Update, ctx):
    q = update.callback_query
    _, uid, n = q.data.split("|")
    uid, n = int(uid), int(n)
    if q.from_user.id != uid:
        await q.answer("These are someone else's milestones!", show_alert=True)
        return
    spec = world.DEX_MILESTONES.get(n)
    if not spec or db.dex_count(uid) < n or not db.claim_milestone(uid, n):
        await q.answer("Nothing to claim.")
        return
    cash, item, qty, title = spec
    db.add_money(uid, cash)
    if item:
        db.add_item(uid, item, qty)
    await q.answer()
    text, rows = milestone_view(uid)
    await q.edit_message_text(f"🎉 {title}! Reward claimed.\n\n" + text, reply_markup=markup(rows))


# ======================================================================= items & shop
async def cmd_bag(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    inv = {k: n for k, n in db.inventory(p["user_id"]).items() if k in world.ITEMS}
    balls = [f"• {world.ITEMS[k].name} ×{n}" for k, n in inv.items() if world.ITEMS[k].kind == "ball" and n > 0]
    other = [f"• {world.ITEMS[k].name} ×{n}" for k, n in inv.items() if world.ITEMS[k].kind in ("stone", "candy") and n > 0]
    keys = [f"• {world.ITEMS[k].name}" for k, n in inv.items() if world.ITEMS[k].kind == "key" and n > 0]
    held = [f"• {world.ITEMS[k].name} ×{n}" for k, n in inv.items() if world.ITEMS[k].kind in world.HELD_KINDS and n > 0]
    await reply(update, lines_of("🎒 BAG", DIV, f"💰 Money: {money(p['money'])}", "",
                                 "🎾 BALLS", balls or ["• none — buy some in /shop"],
                                 *(["", "💎 STONES & ITEMS", other] if other else []),
                                 *(["", "🎒 HELD ITEMS (/give)", held] if held else []),
                                 *(["", "✨ KEY ITEMS", keys] if keys else [])))


SHOP_SECTIONS = [("ball", "🎾 Poké Balls"), ("stone", "💎 Evolution Stones"), ("candy", "🍬 Candy & Items"), ("held", "🎒 Held Items"),
                 ("mega", "🧬 Mega Stones"), ("zcrystal", "🔶 Z-Crystals"), ("key", "✨ Key Items")]
SHOP_PAGE = 10


def _shop_home(uid: int):
    bal = db.money(uid)
    rows = [[(label, f"sh|{uid}|s|{kind}")] for kind, label in SHOP_SECTIONS]
    return lines_of("🛒 POKÉ MART", DIV, f"💰 Your money: {money(bal)}", "", "Pick a shelf:"), rows


def _shop_section(uid: int, kind: str, page: int = 0):
    items = [i for i in world.ITEMS.values() if i.kind == kind and i.price > 0]
    pages = max(1, -(-len(items) // SHOP_PAGE))
    page = min(max(page, 0), pages - 1)
    rows = [[(f"{i.name} — {money(i.price)}", f"sh|{uid}|i|{i.key}")] for i in items[page * SHOP_PAGE:(page + 1) * SHOP_PAGE]]
    if pages > 1:
        rows.append([b for b in [("◀️", f"sh|{uid}|s|{kind}:{page - 1}") if page else None,
                                 (f"{page + 1}/{pages}", f"sh|{uid}|s|{kind}:{page}"),
                                 ("▶️", f"sh|{uid}|s|{kind}:{page + 1}") if page < pages - 1 else None] if b])
    rows.append([("⬅️ Back", f"sh|{uid}|h|-")])
    label = dict(SHOP_SECTIONS)[kind]
    return lines_of(f"🛒 {label}", DIV, f"💰 Your money: {money(db.money(uid))}"), rows


def _shop_item(uid: int, key: str, note: str = ""):
    it = world.ITEMS[key]
    owned = db.qty(uid, key)
    sizes = [1] if it.kind in ("key", "mega", "zcrystal") else [1, 5, 10]
    rows = [[(f"Buy ×{n} — {money(it.price * n)}", f"sh|{uid}|b|{key}|{n}") for n in sizes]]
    if it.kind == "key" and owned:
        rows = []
    rows.append([("⬅️ Back", f"sh|{uid}|s|{it.kind}")])
    text = lines_of(f"🛒 {it.name}", DIV, f"• {it.desc}", *(["• Give it with /give " + it.key + " <pokemon id>"] if it.kind in world.HELD_KINDS else []), f"• Price: {money(it.price)}", f"• You own: {owned}",
                    f"• Your money: {money(db.money(uid))}", *([ "", note] if note else []))
    return text, rows


async def cmd_shop(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    text, rows = _shop_home(p["user_id"])
    await reply(update, text, rows)


async def cb_shop(update: Update, ctx):
    q = update.callback_query
    parts = q.data.split("|")
    uid, view, arg = int(parts[1]), parts[2], parts[3]
    if q.from_user.id != uid:
        await q.answer("Open your own shop with /shop!", show_alert=True)
        return
    note = ""
    if view == "h":
        text, rows = _shop_home(uid)
    elif view == "s" and arg.split(":")[0] in dict(SHOP_SECTIONS):
        kind, _, pg = arg.partition(":")
        text, rows = _shop_section(uid, kind, int(pg) if pg.isdigit() else 0)
    elif view in ("i", "b") and arg in world.ITEMS and world.ITEMS[arg].price > 0:
        if view == "b":
            it, n = world.ITEMS[arg], int(parts[4])
            if it.kind == "key" and db.qty(uid, arg) > 0:
                note = "You only need one of those!"
            elif n not in (1, 5, 10) or not db.spend(uid, it.price * n):
                await q.answer(f"Not enough money for {n}× {it.name}.", show_alert=True)
                return
            else:
                db.add_item(uid, arg, n)
                quests.record(db, uid, "buy")
                note = f"✅ Bought {n}× {it.name} for {money(it.price * n)}!"
        text, rows = _shop_item(uid, arg, note)
    else:
        await q.answer()
        return
    await q.answer()
    try:
        await q.edit_message_text(text[:4000], reply_markup=markup(rows))
    except BadRequest:
        pass


def parse_item_qty(args):
    if not args:
        return None, 0
    if args[-1].isdigit() and len(args) > 1:
        return item_key(" ".join(args[:-1])), int(args[-1])
    return item_key(" ".join(args)), 1


async def cmd_buy(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    key, n = parse_item_qty(ctx.args)
    it = world.ITEMS.get(key or "")
    if not it or not 1 <= n <= 99:
        await reply(update, "Usage: /buy <item> [quantity 1-99]. See /shop.")
        return
    uid = p["user_id"]
    if it.price <= 0:
        await reply(update, "That item isn't sold in shops.")
        return
    if it.kind == "key" and (db.qty(uid, key) > 0 or n > 1):
        await reply(update, "You only need one of those!")
        return
    cost = it.price * n
    if not db.spend(uid, cost):
        await reply(update, f"Not enough money. {n}× {it.name} costs {money(cost)}.")
        return
    db.add_item(uid, key, n)
    quests.record(db, uid, "buy")
    await reply(update, f"Bought {n}× {it.name} for {money(cost)}. Balance: {money(db.money(uid))}")


async def cmd_sell(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    key, n = parse_item_qty(ctx.args)
    it = world.ITEMS.get(key or "")
    if not it or it.kind == "key" or n < 1:
        await reply(update, "Usage: /sell <item> [quantity]. Key items can't be sold.")
        return
    uid = p["user_id"]
    if not db.take_item(uid, key, n):
        await reply(update, "You don't have that many.")
        return
    gain = int(it.price * world.SELL_RATIO) * n
    db.add_money(uid, gain)
    await reply(update, f"Sold {n}× {it.name} for {money(gain)}.")


# ======================================================================= market
async def listing_line(l) -> str:
    if l["kind"] == "mon":
        r = db.mon(l["mon_id"])
        what = f"{r['species_name']} Lv{r['level']} ({pretty(r['ability'])})" if r else "?"
    else:
        what = f"{l['qty']}× {world.ITEMS[l['item']].name}" if l["item"] in world.ITEMS else l["item"]
    return f"• [{l['id']}] {what} — {money(l['price'])}"


async def cmd_market(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    page = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 1
    per = 8
    rows, total = db.listings(offset=(page - 1) * per, limit=per)
    pages = max(1, -(-total // per))
    out = [f"🏪 MARKET — page {page}/{pages}", DIV]
    for l in rows:
        out.append(await listing_line(l))
    if not rows:
        out.append("• Nothing for sale")
    out += ["", f"Buy with /buylisting <id>  ·  {round(config.MARKET_FEE * 100)}% market fee on sales"]
    await reply(update, "\n".join(out))


async def cmd_mylistings(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    rows, _ = db.listings(limit=50, seller=p["user_id"])
    out = ["📋 YOUR LISTINGS", DIV] + [await listing_line(l) for l in rows]
    if not rows:
        out.append("• None")
    out += ["", "Withdraw with /cancel <id>"]
    await reply(update, "\n".join(out))


def parse_price(s: str) -> Optional[int]:
    if s and s.isdigit() and 1 <= int(s) <= 10_000_000:
        return int(s)
    return None


async def cmd_sellmon(update: Update, ctx):
    p = await me(update)
    if p and await team_locked(update, p["user_id"]):
        return
    if not p or await busy(update, p["user_id"]):
        return
    r = own_mon(p["user_id"], ctx.args[0] if ctx.args else "")
    price = parse_price(ctx.args[1]) if len(ctx.args) > 1 else None
    if not r or price is None:
        await reply(update, "Usage: /sellmon <id> <price 1-10,000,000>")
        return
    lid = db.list_mon(p["user_id"], r["id"], price)
    if lid is None:
        await reply(update, "Couldn't list it (you must keep at least one Pokémon in your party).")
        return
    await reply(update, f"Listed {r['species_name']} Lv{r['level']} for {money(price)} (listing {lid}).")


async def cmd_sellitem(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    if len(ctx.args) < 3:
        await reply(update, "Usage: /sellitem <item> <quantity> <price for the whole stack>")
        return
    key = item_key(" ".join(ctx.args[:-2]))
    qty = int(ctx.args[-2]) if ctx.args[-2].isdigit() else 0
    price = parse_price(ctx.args[-1])
    it = world.ITEMS.get(key)
    if not it or it.kind == "key" or qty < 1 or price is None:
        await reply(update, "Usage: /sellitem <item> <quantity> <total price>. Key items can't be traded.")
        return
    lid = db.list_item(p["user_id"], key, qty, price)
    await reply(update, f"Listed {qty}× {it.name} for {money(price)} (listing {lid})." if lid
                else "You don't have that many.")


async def cmd_buylisting(update: Update, ctx):
    p = await me(update)
    if not p or await busy(update, p["user_id"]):
        return
    if not ctx.args or not ctx.args[0].isdigit():
        await reply(update, "Usage: /buylisting <listing id>")
        return
    ok, msg, l = db.buy_listing(p["user_id"], int(ctx.args[0]))
    if not ok:
        await reply(update, msg)
        return
    await reply(update, f"✅ Purchased for {money(l['price'])}! Check /party or /box, or /bag.")
    try:
        await ctx.bot.send_message(l["seller"], f"💰 Your listing {l['id']} sold for {money(l['price'])} "
                                                f"(you received {money(l['price'] - l['fee'])} after the market fee).")
    except TelegramError:
        pass


async def cmd_cancel(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    if not ctx.args or not ctx.args[0].isdigit():
        await reply(update, "Usage: /cancel <listing id>")
        return
    l = db.cancel_listing(p["user_id"], int(ctx.args[0]))
    await reply(update, "Listing withdrawn — your goods are back." if l else "No such listing of yours.")


# ======================================================================= PvP
async def cmd_challenge(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    msg = update.message
    target = msg.reply_to_message.from_user if msg and msg.reply_to_message else None
    if update.effective_chat.type == ChatType.PRIVATE or not target:
        await reply(update, "Use /challenge in a group chat, as a reply to the player you want to battle.")
        return
    if target.is_bot or target.id == p["user_id"]:
        await reply(update, "Pick another human trainer!")
        return
    if not db.player(target.id):
        await reply(update, f"{target.first_name} hasn't started yet (they need to /start the bot).")
        return
    cid = uuid.uuid4().hex[:6]
    CHALLENGES[cid] = (p["user_id"], target.id, update.effective_chat.id, dict(PRESETS["casual"]))
    await reply(update, challenge_text(cid, p["name"], target.first_name), challenge_rows(cid))


PRESETS = {"casual": {"size": 6, "level": 0, "gimmicks": True, "doubles": False},
           "standard": {"size": 6, "level": 50, "gimmicks": True, "doubles": False},
           "vgc": {"size": 4, "level": 50, "gimmicks": True, "doubles": True}}
SIZES, DSIZES, LEVELS = [1, 3, 6], [2, 3, 4, 6], [0, 50, 100]


def rules_line(r: dict) -> str:
    return (f"• {'Doubles' if r.get('doubles') else 'Singles'}   • Team: up to {r['size']}   "
            f"• Level: {'your own' if not r['level'] else 'all Lv' + str(r['level'])}   • Gimmicks: {'on' if r['gimmicks'] else 'off'}")


def challenge_text(cid: str, a_name: str, b_name: str) -> str:
    r = CHALLENGES[cid][3]
    return (f"🤝 {a_name} challenges {b_name} to a friendly battle!\n{DIV}\n{rules_line(r)}\n{DIV}\n"
            f"{a_name} can tweak the rules; {b_name} taps Accept when ready.")


def challenge_rows(cid: str):
    r = CHALLENGES[cid][3]
    return [[("🎲 Casual", f"ch|{cid}|casual"), ("🏅 Standard", f"ch|{cid}|standard"), ("🏆 VGC", f"ch|{cid}|vgc")],
            [(f"👥 Team: {r['size']}", f"ch|{cid}|size"), (f"📈 Level: {r['level'] or 'own'}", f"ch|{cid}|level")],
            [(f"🎮 {'Doubles' if r.get('doubles') else 'Singles'}", f"ch|{cid}|dbl"),
             (f"✨ Gimmicks: {'on' if r['gimmicks'] else 'off'}", f"ch|{cid}|gim")],
            [("✅ Accept", f"ch|{cid}|y"), ("❌ Decline", f"ch|{cid}|n")]]


async def cb_challenge(update: Update, ctx):
    q = update.callback_query
    _, cid, ans = q.data.split("|")
    ch = CHALLENGES.get(cid)
    if not ch:
        await q.answer("This challenge expired.", show_alert=True)
        return
    a, b, chat, rules = ch
    if ans in ("casual", "standard", "vgc", "size", "level", "gim", "dbl"):
        if q.from_user.id != a:
            await q.answer("Only the challenger can change the rules.", show_alert=True)
            return
        if ans in PRESETS:
            rules.clear()
            rules.update(PRESETS[ans])
        elif ans == "size":
            sz = DSIZES if rules.get("doubles") else SIZES
            rules["size"] = sz[(sz.index(rules["size"]) + 1) % len(sz)] if rules["size"] in sz else sz[-1]
        elif ans == "dbl":
            rules["doubles"] = not rules.get("doubles")
            rules["size"] = 4 if rules["doubles"] else 6
        elif ans == "level":
            rules["level"] = LEVELS[(LEVELS.index(rules["level"]) + 1) % len(LEVELS)]
        else:
            rules["gimmicks"] = not rules["gimmicks"]
        await q.answer()
        an, bn = db.player(a)["name"], db.player(b)["name"]
        await q.edit_message_text(challenge_text(cid, an, bn), reply_markup=markup(challenge_rows(cid)))
        return
    if q.from_user.id != b:
        await q.answer("Only the challenged player can answer.", show_alert=True)
        return
    del CHALLENGES[cid]
    await q.answer()
    if ans != "y":
        await q.edit_message_text("Challenge declined.")
        return
    res = await G.start_pvp(a, b, chat, rules)
    if res[0] is None:
        await q.edit_message_text(res[1])
        return
    s, outs = res
    await q.edit_message_text(f"⚔️ {s.title}\n{rules_line(rules)}\nEach trainer picks their move below — nobody sees your choice until the turn plays out!")
    await apply_outs(ctx.bot, outs, chat, "pvp")


# ======================================================================= tournaments
TOURNEYS: Dict[str, "tmod.Tourney"] = {}


def _pname(uid: int) -> str:
    p = db.player(uid)
    return p["name"] if p else str(uid)


def lobby_view(t):
    pr = t.prizes()
    names = [f"{i}. {_pname(u)}" for i, u in enumerate(t.players, 1)] or ["Nobody yet — be the first!"]
    text = lines_of(f"🏆 TOURNAMENT — hosted by {_pname(t.host)}", DIV, rules_line(t.rules),
                    f"• Entry fee: {money(t.fee)}   • Prize pool: {money(t.pool)}",
                    f"• 🥇 {money(pr['champion'])}   🥈 {money(pr['runner_up'])}", "",
                    f"Players ({len(t.players)}/{tmod.MAX_PLAYERS}):", names, "",
                    "Join the pool! The host starts it; opponents are drawn at random and you play your match whenever you're both ready.")
    rows = [[("✅ Join", f"to|{t.id}|j"), ("🚪 Leave", f"to|{t.id}|l")], [("▶️ Start (host)", f"to|{t.id}|s"), ("✖️ Cancel (host)", f"to|{t.id}|c")]]
    return text, rows


def board_view(t):
    out = [f"🏆 TOURNAMENT — {t.round_name()}", DIV]
    rows = []
    for i, m in enumerate(t.matches):
        a, b2 = _pname(m["a"]), _pname(m["b"])
        if m["w"] is not None:
            out.append(f"✅ {a} vs {b2} — 🏆 {_pname(m['w'])}")
            continue
        rdy = " ".join(f"✔{_pname(u)}" for u in sorted(m["ready"]))
        out.append(f"⚔️ {a} vs {b2}" + (" — in battle!" if m.get("live") else f" {rdy}"))
        if not m.get("live"):
            rows.append([(f"⚔️ Ready: {a} vs {b2}", f"to|{t.id}|r|{i}")])
            rows.append([(f"⏭ Walkover → {a}", f"to|{t.id}|w|{i}|0"), (f"⏭ Walkover → {b2}", f"to|{t.id}|w|{i}|1")])
    if t.byes:
        out.append("🍀 Free pass: " + ", ".join(_pname(u) for u in t.byes))
    out += ["", "Both players tap Ready to start their match. Only the host can award walkovers."]
    return "\n".join(out), rows


async def cmd_tournament(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE:
        await reply(update, "Tournaments are for group chats: /tournament [entry fee] [casual|standard|vgc]")
        return
    if any(x.chat_id == chat.id and x.state in ("lobby", "running") for x in TOURNEYS.values()):
        await reply(update, "This chat already has a tournament going!")
        return
    fee = next((int(a) for a in ctx.args if a.isdigit()), 0)
    preset = next((a.lower() for a in ctx.args if a.lower() in PRESETS), "standard")
    if not 0 <= fee <= 5000:
        await reply(update, "Entry fee must be between $0 and $5,000.")
        return
    t = tmod.Tourney(uuid.uuid4().hex[:6], chat.id, p["user_id"], fee, preset, dict(PRESETS[preset]))
    TOURNEYS[t.id] = t
    text, rows = lobby_view(t)
    await reply(update, text, rows)


async def _edit(q, text, rows):
    try:
        await q.edit_message_text(text[:4000], reply_markup=markup(rows))
    except BadRequest:
        pass


async def tourney_finish(bot, t):
    pr = t.prizes()
    db.add_money(t.champion, pr["champion"])
    db.add_money(t.runner_up, pr["runner_up"])
    TOURNEYS.pop(t.id, None)
    await bot.send_message(t.chat_id, lines_of("🏆 TOURNAMENT OVER!", DIV, f"🥇 Champion: {_pname(t.champion)} — {money(pr['champion'])}",
                                               f"🥈 Runner-up: {_pname(t.runner_up)} — {money(pr['runner_up'])}", "", "GG everyone! 👏"))


async def tourney_result(bot, t, i: int, winner: int):
    status = t.report(i, winner, random)
    t.matches[i]["live"] = False
    if status == "done":
        await tourney_finish(bot, t)
        return
    if status == "next":
        text, rows = board_view(t)
        m = await bot.send_message(t.chat_id, "🎉 Round complete!\n\n" + text, reply_markup=markup(rows))
        t.msg_id = m.message_id
        return
    await tourney_refresh(bot, t)


async def tourney_refresh(bot, t):
    if t.msg_id:
        text, rows = board_view(t)
        try:
            await bot.edit_message_text(text, chat_id=t.chat_id, message_id=t.msg_id, reply_markup=markup(rows))
        except TelegramError:
            pass


async def tourney_sync(bot):
    """Feed finished tournament battles (queued by the game) back into their brackets."""
    while G.tourney_results:
        tid, i, w = G.tourney_results.pop(0)
        t = TOURNEYS.get(tid)
        if not t or t.state != "running":
            continue
        if w is None:                                           # draw: replay
            t.matches[i]["ready"].clear()
            t.matches[i]["live"] = False
            await bot.send_message(t.chat_id, f"🤝 {_pname(t.matches[i]['a'])} vs {_pname(t.matches[i]['b'])} was a draw — tap Ready to replay!")
            await tourney_refresh(bot, t)
        else:
            await tourney_result(bot, t, i, w)


async def cb_tournament(update: Update, ctx):
    q = update.callback_query
    parts = q.data.split("|")
    t = TOURNEYS.get(parts[1])
    act, uid = parts[2], q.from_user.id
    if not t or t.state in ("done", "cancelled"):
        await q.answer("This tournament is over.", show_alert=True)
        return
    if act in ("j", "l", "s", "c") and t.state != "lobby":
        await q.answer("It already started!", show_alert=True)
        return
    if act in ("s", "c", "w") and uid != t.host:
        await q.answer("Only the host can do that.", show_alert=True)
        return
    if act == "j":
        if not db.player(uid):
            await q.answer("Send /start to the bot in private first!", show_alert=True)
        elif uid in t.players:
            await q.answer("You're already in!")
        elif len(t.players) >= tmod.MAX_PLAYERS:
            await q.answer("The pool is full.", show_alert=True)
        elif not db.spend(uid, t.fee):
            await q.answer(f"You need {money(t.fee)} to enter.", show_alert=True)
        else:
            t.add(uid)
            await q.answer("You're in! 🎉")
            await _edit(q, *lobby_view(t))
    elif act == "l":
        if t.remove(uid):
            db.add_money(uid, t.fee)
            await q.answer("Left the pool (fee refunded).")
            await _edit(q, *lobby_view(t))
        else:
            await q.answer()
    elif act == "c":
        for u in t.players:
            db.add_money(u, t.fee)
        t.state = "cancelled"
        TOURNEYS.pop(t.id, None)
        await q.answer()
        await q.edit_message_text("✖️ Tournament cancelled — entry fees refunded.")
    elif act == "s":
        if not t.start(random):
            await q.answer(f"Need at least {tmod.MIN_PLAYERS} players.", show_alert=True)
            return
        await q.answer()
        text, rows = board_view(t)
        await q.edit_message_text("🎬 The tournament begins! Opponents were drawn at random.\n\n" + text, reply_markup=markup(rows))
        t.msg_id = q.message.message_id
    elif act == "r":
        i = int(parts[3])
        m = t.matches[i]
        if m.get("live"):
            await q.answer("Your match is already running!", show_alert=True)
            return
        res = t.ready(i, uid)
        if not res:
            await q.answer("That's not your match!", show_alert=True)
        elif res == "wait":
            await q.answer("Ready! Waiting for your opponent…")
            await _edit(q, *board_view(t))
        else:
            await q.answer()
            m["live"] = True
            r = await G.start_pvp(m["a"], m["b"], t.chat_id, dict(t.rules, tid=t.id, tmatch=i))
            if r[0] is None:
                m["live"] = False
                m["ready"].clear()
                await ctx.bot.send_message(t.chat_id, f"⚠️ Couldn't start: {r[1]} Tap Ready again when both of you are free.")
                await _edit(q, *board_view(t))
                return
            await _edit(q, *board_view(t))
            await ctx.bot.send_message(t.chat_id, f"⚔️ {r[0].title}\n{rules_line(t.rules)}")
            await apply_outs(ctx.bot, r[1], t.chat_id, "pvp")
    elif act == "w":
        i, side = int(parts[3]), int(parts[4])
        m = t.matches[i]
        if m.get("live") or m["w"] is not None:
            await q.answer("Not possible right now.", show_alert=True)
            return
        await q.answer("Walkover awarded.")
        await tourney_result(ctx.bot, t, i, m["a"] if side == 0 else m["b"])
        if t.state == "running" and t.msg_id == q.message.message_id:
            await _edit(q, *board_view(t))


# ======================================================================= safari zone
def safari_head(st: dict) -> str:
    return f"🦒 SAFARI ZONE\n{DIV}\n🎯 Safari Balls: {st['balls']}   🐾 Spawns left: {st['steps']}"


def safari_rows(uid: int, in_fight: bool):
    if in_fight:
        return [[("⚪ Safari Ball", f"sf|{uid}|ball"), ("🏃 Run", f"sf|{uid}|run")]]
    return [[("🌿 Search the grass", f"sf|{uid}|search")], [("🚪 Leave the Safari Zone", f"sf|{uid}|leave")]]


def enc_text(uid: int, st: dict, extra: str = "") -> str:
    e = extras.ENC[uid]
    r = e["rec"]
    mood = "is watching you carefully."
    star = "✨ " if r.get("shiny") else ""
    return f"{safari_head(st)}\n{DIV}\n{extra}\n🌿 A wild {star}{r['species_name']} (Lv{r['level']}) {mood}".replace("\n\n\n", "\n\n")


async def cmd_safari(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    if G.active(p["user_id"]):
        await reply(update, "⚔️ You're in the middle of a battle! Finish it first (a stuck battle clears itself after 30 minutes).")
        return
    uid = p["user_id"]
    st = db.safari(uid)
    if st and uid in extras.ENC:
        await reply(update, enc_text(uid, st), safari_rows(uid, True))
    elif st:
        await reply(update, safari_head(st), safari_rows(uid, False))
    else:
        await reply(update, f"🦒 SAFARI ZONE\n{DIV}\nEntry {money(extras.SAFARI_FEE)}: {extras.SAFARI_BALLS} Safari Balls and "
                            f"{extras.SAFARI_STEPS} Pokémon to find.\nThrow a Safari Ball or run - "
                            "but don't wait too long, it might flee!",
                    [[(f"🎟 Enter ({money(extras.SAFARI_FEE)})", f"sf|{uid}|enter")]])


async def cb_safari(update: Update, ctx):
    import random
    q = update.callback_query
    _, uid, act = q.data.split("|")
    uid = int(uid)
    if q.from_user.id != uid:
        await q.answer("This isn't your Safari trip!", show_alert=True)
        return
    st = db.safari(uid)
    if act == "enter":
        if st:
            await q.answer("You're already inside!", show_alert=True)
            return
        if G.active(uid):
            await q.answer("Finish your battle first!", show_alert=True)
            return
        if not db.enter_safari(uid, extras.SAFARI_FEE, extras.SAFARI_BALLS, extras.SAFARI_STEPS, extras.SAFARI_LEGENDS):
            await q.answer(f"You need {money(extras.SAFARI_FEE)} to enter!", show_alert=True)
            return
        await q.answer()
        await q.edit_message_text(safari_head(db.safari(uid)) + "\n\nWelcome! Good luck out there.", reply_markup=markup(safari_rows(uid, False)))
        return
    if not st:
        await q.answer("Your Safari trip is over. Use /safari to enter again.", show_alert=True)
        return
    await q.answer()
    rng = random.Random()
    if act == "leave":
        extras.ENC.pop(uid, None)
        db.leave_safari(uid)
        if st["steps"] < extras.SAFARI_STEPS:
            quests.record(db, uid, "safari")
        await q.edit_message_text("🚪 You left the Safari Zone. See you next time!")
        return
    if act == "search":
        if uid in extras.ENC:
            return
        if st["steps"] <= 0:
            db.leave_safari(uid)
            await q.edit_message_text("⏰ Ding-dong! Your Safari trip is over.")
            return
        p = db.player(uid)
        await extras.new_encounter(db, uid, p["region"], rng)
        await q.edit_message_text(f"🌿 You rustle the tall grass...")
        await send_sprite(ctx.bot, q.message.chat_id, extras.ENC[uid]["rec"]["species_id"])
        st = db.safari(uid)
        m = await ctx.bot.send_message(q.message.chat_id, enc_text(uid, st), reply_markup=markup(safari_rows(uid, True)))
        return
    if uid not in extras.ENC:
        await q.edit_message_text(safari_head(st), reply_markup=markup(safari_rows(uid, False)))
        return
    rec = extras.ENC[uid]["rec"]
    caught_mid = None
    log, outcome = extras.act(db, uid, act, rng)
    text = "\n".join(log)
    if outcome == "caught":
        mid = db.add_mon(uid, extras.keep_record(rec))
        db.dex_mark_caught(uid, rec["species_id"])
        quests.record(db, uid, "catch", typ=(await get_provider().get_form(rec["species_id"])).types)
        where = "your party" if any(r["id"] == mid for r in db.party(uid)) else "your box"
        text += f"\n🎉 Gotcha! {'✨ ' if rec.get('shiny') else ''}{rec['species_name']} was caught and sent to {where}! (ID {mid})"
        caught_mid = mid
    st = db.safari(uid)
    if st["balls"] <= 0 or (outcome != "go" and st["steps"] <= 0):
        extras.ENC.pop(uid, None)
        db.leave_safari(uid)
        quests.record(db, uid, "safari")
        reason = "You're out of Safari Balls!" if st["balls"] <= 0 else "That was the last Pokémon!"
        await q.edit_message_text(f"{text}\n\n⏰ Ding-dong! {reason} Your Safari trip is over.")
        await safari_card(ctx.bot, q.message.chat_id, uid, caught_mid)
        return
    if outcome == "go":
        await q.edit_message_text(enc_text(uid, st, text), reply_markup=markup(safari_rows(uid, True)))
    else:
        await q.edit_message_text(f"{safari_head(st)}\n{DIV}\n{text}", reply_markup=markup(safari_rows(uid, False)))
    await safari_card(ctx.bot, q.message.chat_id, uid, caught_mid)


async def safari_card(bot, chat_id: int, uid: int, mid):
    if mid:
        r = db.mon(mid)
        if r:
            await bot.send_message(chat_id, caught_text(r), reply_markup=markup(mon_kb(uid, mid, r["slot"] is not None)))


# ======================================================================= trading
TRADES: Dict[str, dict] = {}


def mon_desc(r: dict) -> str:
    return f"{r.get('nickname') or r['species_name']} Lv{r['level']} ({(r.get('nature') or 'Hardy')}, #{r['id']})"


def _side(mid, cash: int) -> str:
    parts = ([mon_desc(db.mon(mid))] if mid else []) + ([money(cash)] if cash else [])
    return " + ".join(parts) or "nothing"


def trade_text(t: dict) -> str:
    a, b = db.player(t["a"])["name"], db.player(t["b"])["name"]
    lines = ["🔁 TRADE", DIV, f"• {a} offers: {_side(t['a_mid'], t['a_cash'])}"]
    if t["b_set"]:
        lines.append(f"• {b} offers: {_side(t['b_mid'], t['b_cash'])}")
        lines.append(f"\n✅ Confirmed: {', '.join(n for n, k in ((a, 'ok_a'), (b, 'ok_b')) if t[k]) or 'nobody yet'}")
    else:
        lines.append(f"\n{b}: reply with /offer <Pokémon id or 0> [money] to propose yours.")
    return "\n".join(lines)


def trade_rows(tid: str, t: dict):
    rows = [[("✅ Confirm", f"tdr|{tid}|ok")] if t["b_set"] else []]
    rows.append([("❌ Cancel", f"tdr|{tid}|no")])
    return [r for r in rows if r]


def parse_trade_side(args):
    """'<mon id | 0> [money]' -> (mon id or None, money) ; None when malformed."""
    if not args or not args[0].isdigit() or (len(args) > 1 and not args[1].isdigit()):
        return None
    mid, cash = int(args[0]), int(args[1]) if len(args) > 1 else 0
    return (mid or None), cash


def trade_check(uid: int, mid: Optional[int], cash: int = 0) -> Optional[str]:
    if G.active(uid) or db.safari(uid):
        return "Finish your battle / Safari trip first."
    if mid and db.league_run(uid):
        return "Your team is locked during a League run."
    if mid and not extras.tradable(db, uid, mid):
        return "That isn't one of your Pokémon (or it's listed on the market)."
    if cash > db.money(uid):
        return f"You only have {money(db.money(uid))}."
    return None


async def cmd_trade(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    msg = update.message
    target = msg.reply_to_message.from_user if msg and msg.reply_to_message else None
    side = parse_trade_side(ctx.args)
    if update.effective_chat.type == ChatType.PRIVATE or not target or not side:
        await reply(update, "Usage: reply to a trainer in a group with /trade <your Pokémon id or 0> [money to add]\n"
                            "• /trade 12 — swap #12   • /trade 12 500 — #12 plus $500   • /trade 0 2000 — pay $2,000 (they name what they give)")
        return
    if target.is_bot or target.id == p["user_id"] or not db.player(target.id):
        await reply(update, "Pick another trainer who has started the bot!")
        return
    a_mid, a_cash = side
    if not a_mid and not a_cash:
        await reply(update, "Offer a Pokémon and/or some money!")
        return
    err = trade_check(p["user_id"], a_mid, a_cash)
    if err:
        await reply(update, err)
        return
    tid = uuid.uuid4().hex[:6]
    t = TRADES[tid] = {"a": p["user_id"], "b": target.id, "chat": update.effective_chat.id, "a_mid": a_mid, "a_cash": a_cash,
                       "b_mid": None, "b_cash": 0, "b_set": False, "ok_a": False, "ok_b": False, "msg": None}
    m = await reply(update, trade_text(t), trade_rows(tid, t))
    t["msg"] = m.message_id


async def cmd_offer(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    uid = p["user_id"]
    tid = next((k for k, t in TRADES.items() if t["b"] == uid and t["chat"] == update.effective_chat.id), None)
    side = parse_trade_side(ctx.args)
    if not tid or not side:
        await reply(update, "Usage: /offer <your Pokémon id or 0> [money] — answering a /trade aimed at you in this chat.")
        return
    b_mid, b_cash = side
    t = TRADES[tid]
    if not b_mid and not b_cash:
        await reply(update, "Offer a Pokémon and/or some money!")
        return
    if not b_mid and not t["a_mid"]:
        await reply(update, "A trade needs at least one Pokémon in it.")
        return
    err = trade_check(uid, b_mid, b_cash)
    if err:
        await reply(update, err)
        return
    t.update(b_mid=b_mid, b_cash=b_cash, b_set=True, ok_a=False, ok_b=False)
    try:
        await ctx.bot.edit_message_text(trade_text(t), chat_id=t["chat"], message_id=t["msg"], reply_markup=markup(trade_rows(tid, t)))
    except TelegramError:
        m = await reply(update, trade_text(t), trade_rows(tid, t))
        t["msg"] = m.message_id


async def cb_trade(update: Update, ctx):
    q = update.callback_query
    _, tid, act = q.data.split("|")
    t = TRADES.get(tid)
    if not t:
        await q.answer("This trade is over.", show_alert=True)
        return
    uid = q.from_user.id
    if uid not in (t["a"], t["b"]):
        await q.answer("This isn't your trade!", show_alert=True)
        return
    if act == "no":
        del TRADES[tid]
        await q.answer()
        await q.edit_message_text("❌ Trade cancelled.")
        return
    side = "ok_a" if uid == t["a"] else "ok_b"
    t[side] = True
    await q.answer()
    if not (t["ok_a"] and t["ok_b"]):
        await q.edit_message_text(trade_text(t), reply_markup=markup(trade_rows(tid, t)))
        return
    del TRADES[tid]
    for u, m, c in ((t["a"], t["a_mid"], t["a_cash"]), (t["b"], t["b_mid"], t["b_cash"])):
        err = trade_check(u, m, c)
        if err:
            await q.edit_message_text(f"❌ Trade failed: {err}")
            return
    ra, rb = (db.mon(t["a_mid"]) if t["a_mid"] else None), (db.mon(t["b_mid"]) if t["b_mid"] else None)
    if not db.exchange(t["a"], t["a_mid"], t["a_cash"], t["b"], t["b_mid"], t["b_cash"]):
        await q.edit_message_text("❌ Trade failed: a Pokémon or the money is no longer available.")
        return
    if ra:
        db.dex_mark_caught(t["b"], ra["species_id"])
    if rb:
        db.dex_mark_caught(t["a"], rb["species_id"])
    out = [f"🔁 Trade complete! {db.player(t['a'])['name']} ↔ {db.player(t['b'])['name']}"]
    for u, m in ((t["b"], t["a_mid"]), (t["a"], t["b_mid"])):
        if m:
            out += await extras.trade_evolve(db, u, m)
    await q.edit_message_text("\n".join(out))


# ======================================================================= battle callbacks
async def safe_answer(q, *a, **kw):
    """Answering a button press can fail (expired, flood wait); that must never swallow the move itself."""
    try:
        await q.answer(*a, **kw)
    except TelegramError as e:
        log.info("answer failed: %s", e)


async def cb_battle(update: Update, ctx):
    q = update.callback_query
    uid = q.from_user.id
    try:
        sid = q.data.split("|")[1]
    except IndexError:
        await safe_answer(q)
        return
    async with LOCKS[sid]:
        s = G.sessions.get(sid)
        if s is None:
            await safe_answer(q, "This battle is over.", show_alert=True)
            return
        if s.side_of(uid) is None:
            await safe_answer(q, "This isn't your battle!", show_alert=True)
            return
        await safe_answer(q)
        MSG[uid] = (q.message.chat_id, q.message.message_id)
        chat_id, kind = s.chat_id, s.kind
        outs = await G.handle(uid, q.data)
        # Telegram edits are applied while still holding the lock, so two quick taps can never paint an older screen over a newer one
        await apply_outs(ctx.bot, outs, chat_id, kind)
    if sid not in G.sessions:
        LOCKS.pop(sid, None)
        PHOTO.pop("pvp" + sid, None)


EV_STATS = [("hp", "HP"), ("atk", "Attack"), ("def", "Defense"), ("spa", "Sp. Atk"), ("spd", "Sp. Def"), ("spe", "Speed")]
EV_COST, EV_ENCOUNTERS = 200, 30


async def cmd_train(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    uid = p["user_id"]
    if (p.get("ev_left") or 0) > 0:
        await reply(update, f"🏋️ You're already training {p['ev_stat'].upper()} EVs — {p['ev_left']} encounters left.\n"
                            "Keep using /explore: only Pokémon that give those EVs will show up!")
        return
    rows = [[(n, f"trn|{uid}|{k}")] for k, n in EV_STATS]
    await reply(update, lines_of("🏋️ EV TRAINING", DIV,
                                 [f"• Entry fee: {money(EV_COST)}" + (" (you have a free voucher!)" if db.qty(uid, "ev-voucher") else ""), f"• {EV_ENCOUNTERS} encounters with Pokémon that give your chosen EV",
                                  "• Win battles to bank EVs (max 252 per stat, 510 total)"], "", "Pick a stat to train:"), rows)


async def cb_train(update: Update, ctx):
    q = update.callback_query
    _, uid, stat = q.data.split("|")
    uid = int(uid)
    if q.from_user.id != uid:
        await q.answer("Not yours!", show_alert=True)
        return
    if stat not in dict(EV_STATS) or (db.player(uid) or {}).get("ev_left", 0) > 0:
        await q.answer("You're already training!", show_alert=True)
        return
    free = db.qty(uid, "ev-voucher") > 0
    if not db.start_training(uid, stat, 0 if free else EV_COST, EV_ENCOUNTERS):
        await q.answer(f"You need {money(EV_COST)} to enter.", show_alert=True)
        return
    if free:
        db.take_item(uid, "ev-voucher", 1)
    await q.answer()
    await q.edit_message_text(f"🏋️ Training {dict(EV_STATS)[stat]}! " + ("Used an EV Training Voucher - free!" if free else f"Paid {money(EV_COST)}.") + "\n"
                              f"Your next {EV_ENCOUNTERS} /explore encounters will be Pokémon that give {dict(EV_STATS)[stat]} EVs.")


async def cmd_evolve(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    uid = p["user_id"]
    r = own_mon(uid, ctx.args[0] if ctx.args else "")
    if not r:
        await reply(update, "Usage: /evolve <id>  (IDs are shown in /party)")
        return
    opts = await gm.evolution_options(r)
    name = r.get("nickname") or r["species_name"]
    if not opts:
        await reply(update, f"{name} doesn't evolve any further.")
        return
    lines, rows = [], []
    for o in opts:
        have = ""
        if o["kind"] == "stone":
            have = f" (you have {db.qty(uid, o['stone'])})"
        lines.append(f"• → {o['name']}: {o['need']}{have}")
        if o["kind"] == "stone" and db.qty(uid, o["stone"]) > 0:
            rows.append([(f"💎 Use {world.ITEMS[o['stone']].name} → {o['name']}", f"ev|{uid}|{r['id']}|{o['to']}")])
        elif o["kind"] == "friendship" and o["ready"]:
            rows.append([(f"💖 Evolve → {o['name']}", f"ev|{uid}|{r['id']}|{o['to']}")])
    head = f"🧬 {name} — Lv{r['level']} · Friendship {r.get('friendship', 70)}/255"
    await reply(update, lines_of(head, DIV, lines, "", "Buy stones in /shop. Friendship grows by winning battles together." if not rows else "Choose:"), rows)


async def cb_evolve(update: Update, ctx):
    q = update.callback_query
    _, uid, mid, to = q.data.split("|")
    uid, mid, to = int(uid), int(mid), int(to)
    if q.from_user.id != uid:
        await q.answer("Not yours!", show_alert=True)
        return
    r = db.mon(mid)
    if not r or r["owner"] != uid or G.active(uid):
        await q.answer("Can't do that right now.", show_alert=True)
        return
    o = next((x for x in await gm.evolution_options(r) if x["to"] == to), None)
    if not o or o["kind"] not in ("stone", "friendship") or not o["ready"]:
        await q.answer("That evolution isn't available.", show_alert=True)
        return
    if o["kind"] == "stone" and not db.take_item(uid, o["stone"], 1):
        await q.answer("You don't have that stone.", show_alert=True)
        return
    await q.answer()
    msgs = await gm.evolve_mon(db, r, to)
    await q.edit_message_text("\n".join(msgs))
    await send_sprite(ctx.bot, q.message.chat_id, r["species_id"])


USABLE = ("rare-candy", "ability-capsule", "ability-patch", "nature-mint")


async def cmd_use(update: Update, ctx):
    p = await me(update)
    if not p or await busy(update, p["user_id"]):
        return
    uid = p["user_id"]
    args = ctx.args or []
    usage = "Usage: /use <rare-candy | ability-capsule | ability-patch | nature-mint> <pokemon id>\n(Stones: /evolve <id>)"
    if len(args) < 2 or not args[-1].isdigit():
        await reply(update, usage)
        return
    key = item_key(" ".join(args[:-1]))
    r = own_mon(uid, args[-1])
    if key not in USABLE or not r:
        await reply(update, usage)
        return
    if db.qty(uid, key) < 1:
        await reply(update, f"You don't have a {world.ITEMS[key].name}. Buy one in /shop.")
        return
    if key == "rare-candy":
        if r["level"] >= 100:
            await reply(update, "That Pokémon is already Lv100!")
            return
        db.take_item(uid, key, 1)
        msgs = await gm.use_rare_candy(db, r)
        await reply(update, lines_of("🍬 RARE CANDY", DIV, [f"• {m}" for m in msgs] or ["• Nothing happened."]))
        return
    if key == "nature-mint":
        rows, row = [], []
        for nm in gm.NATURES:
            if nm != r.get("nature"):
                row.append((nm, f"nm|{uid}|{r['id']}|{nm}"))
                if len(row) == 3:
                    rows.append(row)
                    row = []
        await reply(update, f"🌿 Choose a nature for {r.get('nickname') or r['species_name']} "
                            f"(now {gm.nature_text(r.get('nature') or 'Hardy')}):", rows + ([row] if row else []))
        return
    ok, msg = await gm.use_ability_item(db, r, key)
    if ok:
        db.take_item(uid, key, 1)
    await reply(update, ("✨ " if ok else "") + msg)


async def cb_mint(update: Update, ctx):
    q = update.callback_query
    _, uid, mid, nature = q.data.split("|")
    uid, mid = int(uid), int(mid)
    if q.from_user.id != uid:
        await q.answer("Not yours!", show_alert=True)
        return
    r = db.mon(mid)
    if not r or r["owner"] != uid or G.active(uid) or db.qty(uid, "nature-mint") < 1:
        await q.answer("Can't do that right now (do you still have a Nature Mint?).", show_alert=True)
        return
    ok, msg = await gm.use_nature_mint(db, r, nature)
    if not ok:
        await q.answer(msg, show_alert=True)
        return
    db.take_item(uid, "nature-mint", 1)
    await q.answer()
    await q.edit_message_text("🌿 " + msg)


# ======================================================================= pokédex
async def cmd_dex(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    uid = p["user_id"]
    if ctx.args:
        sid = await dexmod.find_species(db, uid, " ".join(ctx.args))
        if sid is None:
            await reply(update, "I don't know that one — use a Pokédex number, or the name of a Pokémon you've met.")
            return
        await send_dex_picture(ctx.bot, update.effective_chat.id, uid, sid)
        text, rows = await dexmod.dex_entry(db, uid, sid)
    else:
        text, rows = dexmod.overview(db, uid)
    await reply(update, text, rows)


async def send_dex_picture(bot, chat_id: int, uid: int, sid: int):
    """Entry picture: colour once caught, silhouette when only seen, nothing if never met."""
    state = dexmod.rows_of(db, uid)
    if sid not in state:
        return
    try:
        png = await images.dex_picture(sid, bool(state[sid]), shiny=sid in dexmod.shinies_of(db, uid))
        if png:
            await bot.send_photo(chat_id, io.BytesIO(png))
    except Exception:                                    # noqa: BLE001 - a missing picture never blocks the entry
        pass


async def cb_dex(update: Update, ctx):
    q = update.callback_query
    _, uid, view, arg, page = q.data.split("|")
    uid = int(uid)
    if q.from_user.id != uid:
        await q.answer("Open your own Pokédex with /dex!", show_alert=True)
        return
    await q.answer()
    if view == "p":
        await send_dex_picture(ctx.bot, q.message.chat_id, uid, int(arg))
        return
    if view == "o":
        text, rows = dexmod.overview(db, uid)
    elif view == "l":
        text, rows = await dexmod.dex_list(db, uid, arg, int(page))
    elif view == "e":
        text, rows = await dexmod.dex_entry(db, uid, int(arg))
    else:
        text, rows = await dexmod.dex_moves(db, uid, int(arg))
    try:
        await q.edit_message_text(text[:4000], reply_markup=markup(rows))
    except BadRequest:
        pass


# ======================================================================= moves & forms
KIND_NAME = {"r": "🔁 Relearn (free)", "t": "🎓 Tutor (free)", "m": "💿 TMs (reusable)"}


async def cmd_moves(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    uid = p["user_id"]
    r = own_mon(uid, ctx.args[0] if ctx.args else "")
    if not r:
        await reply(update, "Usage: /moves <id>  (IDs are shown in /party)")
        return
    lists = await gm.learn_lists(r)
    rows = [[(f"{KIND_NAME[k]} · {len(lists[k])}", f"mv|{uid}|{r['id']}|{k}|0")] for k in ("r", "t", "m") if lists[k]]
    cur = ", ".join(pretty(m) for m in r["moves"])
    await reply(update, lines_of(f"🎒 MOVES — {r.get('nickname') or r['species_name']} Lv{r['level']}", DIV, f"• Knows: {cur}", "",
                                 "Relearn and tutor moves are free. TMs are bought once and reusable." if rows else
                                 "Nothing new to learn right now."), rows)


async def cb_moves(update: Update, ctx):
    """mv = list a category, mx = pick a move, mz = confirm (with the slot to forget)."""
    q = update.callback_query
    parts = q.data.split("|")
    op, uid, mid = parts[0], int(parts[1]), int(parts[2])
    if q.from_user.id != uid:
        await q.answer("Not yours!", show_alert=True)
        return
    r = db.mon(mid)
    if not r or r["owner"] != uid or G.active(uid):
        await q.answer("Can't do that right now.", show_alert=True)
        return
    await q.answer()
    kind = parts[3]
    lists = await gm.learn_lists(r)
    if op == "mv":
        page = int(parts[4])
        items = lists.get(kind, [])
        per = 8
        rows = []
        for mname in items[page * per:(page + 1) * per]:
            label = pretty(mname)
            if kind == "m":
                own = db.qty(uid, gm.tm_key(mname)) > 0
                label += " ✅" if own else f" — ${await gm.tm_price(mname):,}"
            rows.append([(label, f"mx|{uid}|{mid}|{kind}|{mname}")])
        nav = []
        if page > 0:
            nav.append(("◀", f"mv|{uid}|{mid}|{kind}|{page - 1}"))
        if (page + 1) * per < len(items):
            nav.append(("▶", f"mv|{uid}|{mid}|{kind}|{page + 1}"))
        await q.edit_message_text(f"{KIND_NAME[kind]} — choose a move ({len(items)} available):",
                                  reply_markup=markup(rows + ([nav] if nav else [])))
    elif op == "mx":
        mname = parts[4]
        if len(r["moves"]) < 4:
            ok, msg = await gm.teach_move(db, uid, r, kind, mname, None)
            await q.edit_message_text(("✅ " if ok else "⚠️ ") + msg)
        else:
            rows = [[(f"Forget {pretty(m)}", f"mz|{uid}|{mid}|{kind}|{mname}|{k}")] for k, m in enumerate(r["moves"])]
            await q.edit_message_text(f"{r.get('nickname') or r['species_name']} already knows 4 moves. "
                                      f"Which one should make room for {pretty(mname)}?", reply_markup=markup(rows))
    else:
        ok, msg = await gm.teach_move(db, uid, r, kind, parts[4], int(parts[5]))
        await q.edit_message_text(("✅ " if ok else "⚠️ ") + msg)


async def cmd_form(update: Update, ctx):
    p = await me(update)
    if not p:
        return
    uid = p["user_id"]
    r = own_mon(uid, ctx.args[0] if ctx.args else "")
    if not r:
        await reply(update, "Usage: /form <id>  (for Pokémon with several forms, like Arceus)")
        return
    opts = await gm.form_options(r)
    if not opts:
        await reply(update, "This Pokémon has no forms you can change. (Regional, Dynamax and Gmax forms can't be bought.)")
        return
    cur = r.get("form") or ""
    rows, row = [], []
    for slug, label in opts:
        if slug == cur:
            continue
        row.append((label, f"fm|{uid}|{r['id']}|{slug or '-'}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    now = dict(opts).get(cur, "Normal")
    await reply(update, lines_of(f"🎭 FORMS — {r['species_name']} (now: {now})", DIV,
                                 f"• Each change costs {money(gm.FORM_CHANGE_PRICE)} — change as often as you like"), rows)


async def cb_form(update: Update, ctx):
    q = update.callback_query
    _, uid, mid, slug = q.data.split("|")
    uid, mid = int(uid), int(mid)
    if q.from_user.id != uid:
        await q.answer("Not yours!", show_alert=True)
        return
    r = db.mon(mid)
    if not r or r["owner"] != uid or G.active(uid):
        await q.answer("Can't do that right now.", show_alert=True)
        return
    ok, msg = await gm.change_form(db, uid, r, "" if slug == "-" else slug)
    if not ok:
        await q.answer(msg, show_alert=True)
        return
    await q.answer()
    await q.edit_message_text(msg)


# ======================================================================= creator powers (ADMIN_IDS only, hidden from /help)
ADMIN_HELP = lines_of(
    "🛠 CREATOR POWERS", DIV,
    ["• /admin_stats — players, Pokémon, money, live battles", "• /admin_who <id | reply> — look up a player",
     "• /admin_money <amount> [id | reply] — add/remove money (default: yourself)",
     "• /admin_item <item> [n] [id | reply] — give an item", "• /admin_mon <species no.> [level] [shiny] [id | reply] — give a Pokémon",
     "• /admin_ban <id | reply> [reason] · /admin_unban <id | reply>", "• /admin_announce <text> — message every player",
     "• /admin_backup — save a snapshot now and send it to you"])


def is_admin(update: Update) -> bool:
    return bool(update.effective_user) and update.effective_user.id in config.ADMIN_IDS


def admin_target(update: Update, args: list):
    """(target user id, remaining args): a replied-to user, a trailing numeric id, else yourself."""
    msg = update.message
    if msg and msg.reply_to_message and msg.reply_to_message.from_user:
        return msg.reply_to_message.from_user.id, list(args)
    if args and args[-1].isdigit() and len(args[-1]) >= 6:
        return int(args[-1]), list(args[:-1])
    return update.effective_user.id, list(args)


async def cmd_admin(update: Update, ctx):
    if is_admin(update):
        await reply(update, ADMIN_HELP)


async def cmd_admin_stats(update: Update, ctx):
    if not is_admin(update):
        return
    r = db.one("SELECT COUNT(*) AS n, COALESCE(SUM(money),0) AS m FROM players")
    mons = db.one("SELECT COUNT(*) AS n FROM mons")["n"]
    shin = db.one("SELECT COALESCE(SUM(shiny),0) AS s FROM mons")["s"]
    banned = db.one("SELECT COUNT(*) AS n FROM banned")["n"]
    await reply(update, lines_of("📊 STATS", DIV, [f"• Players: {r['n']} (banned {banned})", f"• Pokémon: {mons} (✨ {shin})",
                                                  f"• Money in circulation: {money(r['m'])}", f"• Live battles: {len(G.sessions)}",
                                                  f"• Open tournaments: {len(TOURNEYS)}"]))


async def cmd_admin_who(update: Update, ctx):
    if not is_admin(update):
        return
    uid, _ = admin_target(update, ctx.args)
    p = db.player(uid)
    if not p:
        await reply(update, "No such player.")
        return
    await reply(update, lines_of(f"🔎 {p['name']} ({uid})", DIV, [
        f"• Region: {p['region']}   • Money: {money(p['money'])}", f"• Pokémon: {db.count_mons(uid)}   • Dex: {db.dex_count(uid)}",
        f"• Badges: {db.total_badges(uid)}   • Wins/Losses: {p['wins']}/{p['losses']}   • PvP wins: {p['pvp_wins']}",
        f"• Streak: {p.get('streak', 0)}   • Banned: {'yes' if db.is_banned(uid) else 'no'}"]))


async def cmd_admin_money(update: Update, ctx):
    if not is_admin(update):
        return
    uid, rest = admin_target(update, ctx.args)
    n = int(rest[0]) if rest and rest[0].lstrip("-").isdigit() else 0
    if not db.player(uid):
        await reply(update, "No such player.")
        return
    db.add_money(uid, n)
    await reply(update, f"{db.player(uid)['name']}'s balance: {money(db.money(uid))}")


async def cmd_admin_item(update: Update, ctx):
    if not is_admin(update):
        return
    uid, rest = admin_target(update, ctx.args)
    n = int(rest[-1]) if len(rest) > 1 and rest[-1].isdigit() else 1
    name = rest[:-1] if len(rest) > 1 and rest[-1].isdigit() else rest
    it = world.ITEMS.get(item_key(" ".join(name)))
    if not it or not db.player(uid):
        await reply(update, "Usage: /admin_item <item> [n] [id | reply]  (item keys like rare-candy, master-ball)")
        return
    db.add_item(uid, it.key, n)
    await reply(update, f"Gave {n}× {it.name} to {db.player(uid)['name']}.")


async def cmd_admin_mon(update: Update, ctx):
    if not is_admin(update):
        return
    uid, rest = admin_target(update, ctx.args)
    shiny = "shiny" in [a.lower() for a in rest]
    nums = [int(a) for a in rest if a.isdigit()]
    if not nums or not db.player(uid) or not 1 <= nums[0] <= 1025:
        await reply(update, "Usage: /admin_mon <species number> [level] [shiny] [id | reply]")
        return
    rec = await gm.new_record(nums[0], max(1, min(100, nums[1] if len(nums) > 1 else 5)), random)
    if shiny:
        rec["shiny"] = 1
    mid = db.add_mon(uid, rec)
    await reply(update, f"Gave {'✨ ' if shiny else ''}{rec['species_name']} Lv{rec['level']} (ID {mid}) to {db.player(uid)['name']}.")


async def cmd_admin_ban(update: Update, ctx):
    if not is_admin(update):
        return
    uid, rest = admin_target(update, ctx.args)
    if uid in config.ADMIN_IDS or not db.player(uid):
        await reply(update, "Can't ban that user.")
        return
    db.ban(uid, " ".join(rest))
    await reply(update, f"🚫 {db.player(uid)['name']} is banned.")


async def cmd_admin_unban(update: Update, ctx):
    if not is_admin(update):
        return
    uid, _ = admin_target(update, ctx.args)
    db.unban(uid)
    await reply(update, f"✅ {uid} is unbanned.")


async def cmd_admin_announce(update: Update, ctx):
    if not is_admin(update) or not ctx.args:
        return
    text = "📢 " + " ".join(ctx.args)
    sent = 0
    for uid in db.all_user_ids():
        try:
            await ctx.bot.send_message(uid, text)
            sent += 1
        except TelegramError:
            pass
        await asyncio.sleep(0.05)                 # stay well under Telegram's broadcast limits
    await reply(update, f"Announcement delivered to {sent} players.")


async def cmd_admin_backup(update: Update, ctx):
    if not is_admin(update):
        return
    path = await asyncio.to_thread(db.backup, config.BACKUP_DIR, config.BACKUP_KEEP)
    try:
        with open(path, "rb") as f:
            await ctx.bot.send_document(update.effective_user.id, f, filename=os.path.basename(path))
        await reply(update, "💾 Backup saved and sent to you privately.")
    except TelegramError:
        await reply(update, f"💾 Backup saved at {path} (couldn't DM it — open a private chat with me first).")


async def ban_gate(update: object, ctx):
    """Runs before every other handler: banned players are ignored."""
    u = getattr(update, "effective_user", None)
    if u and db.is_banned(u.id):
        q = getattr(update, "callback_query", None)
        if q:
            await q.answer("You are banned from this bot.", show_alert=True)
        elif getattr(update, "effective_message", None):
            await update.effective_message.reply_text("🚫 You are banned from this bot.")
        raise ApplicationHandlerStop


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    err = ctx.error
    if isinstance(err, BadRequest) and "not modified" in str(err).lower():
        return                                              # a button pressed twice: nothing to fix
    if isinstance(err, (NetworkError, TimedOut, RetryAfter)):
        log.warning("Telegram connection hiccup: %s", err)  # transient: no scary message to the players
        return
    log.exception("Unhandled error", exc_info=err)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await update.effective_chat.send_message("⚠️ Something went wrong. Please try again.")
        except TelegramError:
            pass


COMMANDS = [
    ("start", "Begin your journey"), ("help", "All commands"), ("info", "Abilities, natures, held items"), ("explore", "Find wild Pokémon (/hunt, /find)"),
    ("card", "Your trainer card"), ("trainer", "Change your trainer look"), ("name", "Set your trainer name"), ("whosthat", "Guess the Pokémon (+$20)"),
    ("gym", "Challenge the next gym leader"), ("league", "Elite Four & Champion"), ("travel", "Change region"), ("heal", "Heal your team"),
    ("party", "Your team"), ("box", "Stored Pokémon"), ("mon", "Pokémon details"), ("fav", "Set favorite Pokémon"),
    ("bag", "Your items"), ("shop", "Poké Mart"), ("market", "Player market"), ("challenge", "Friendly battle (reply)"),
    ("badges", "Badge case"), ("daily", "Daily bonus"), ("gimmicks", "Mega/Z/Dynamax/Tera info"), ("evolve", "Evolve a Pokémon"), ("train", "EV training"),
    ("use", "Use a Rare Candy / ability item"),
    ("dex", "Pokédex"), ("moves", "Relearn moves / TMs"), ("form", "Change form"),
    ("quests", "Daily & weekly quests"), ("milestones", "Pokédex rewards"), ("tournament", "Start a group tournament"), ("give", "Give a held item"), ("take", "Take a held item"), ("breed", "Breed two Pokémon"), ("eggs", "Your eggs"), ("top", "Leaderboards"), ("event", "Today's event Pokémon"), ("safari", "Safari Zone"), ("trade", "Trade Pokémon (reply)"), ("offer", "Answer a trade"),
]


def build_app(token: Optional[str] = None) -> Application:
    token = token or config.BOT_TOKEN
    if not token:
        raise SystemExit("Set BOT_TOKEN (see .env.example).")
    init()

    async def backup_loop():
        """Daily snapshot of the save file (kept for POKEBOT_BACKUP_KEEP days) in data/backups."""
        while True:
            now = dt.datetime.utcnow()
            nxt = now.replace(hour=config.BACKUP_HOUR_UTC, minute=0, second=0, microsecond=0)
            if nxt <= now:
                nxt += dt.timedelta(days=1)
            await asyncio.sleep((nxt - now).total_seconds())
            try:
                log.info("backup written: %s", await asyncio.to_thread(db.backup, config.BACKUP_DIR, config.BACKUP_KEEP))
            except Exception as e:                           # noqa: BLE001
                log.error("backup failed: %s", e)

    async def post_init(app: Application):
        await app.bot.set_my_commands([BotCommand(c, d) for c, d in COMMANDS])
        log.info("SAVE DATA: %s (backups in %s)", config.DB_PATH, config.BACKUP_DIR)
        try:
            await asyncio.to_thread(db.backup, config.BACKUP_DIR, config.BACKUP_KEEP)       # one on every start too
        except Exception as e:                                                               # noqa: BLE001
            log.error("startup backup failed: %s", e)
        app.bot_data["backup_task"] = asyncio.create_task(backup_loop())

    app = Application.builder().token(token).post_init(post_init).build()
    handlers = {
        "start": cmd_start, "help": cmd_help, "info": cmd_info, "gimmicks": cmd_gimmicks, "profile": cmd_card, "card": cmd_card,
        "trainer": cmd_trainer, "name": cmd_name, "whosthat": cmd_whosthat, "guess": cmd_guess, "league": cmd_league, "fav": cmd_fav, "hunt": cmd_explore, "find": cmd_explore, "party": cmd_party,
        "box": cmd_box, "mon": cmd_mon, "nick": cmd_nick, "addparty": cmd_addparty, "tobox": cmd_tobox,
        "swap": cmd_swap, "release": cmd_release, "explore": cmd_explore, "gym": cmd_gym, "travel": cmd_travel,
        "heal": cmd_heal, "daily": cmd_daily, "badges": cmd_badges, "leaderboard": cmd_leaderboard, "top": cmd_leaderboard, "milestones": cmd_milestones, "bag": cmd_bag,
        "shop": cmd_shop, "buy": cmd_buy, "sell": cmd_sell, "market": cmd_market,
        "mylistings": cmd_mylistings, "sellmon": cmd_sellmon, "sellitem": cmd_sellitem,
        "buylisting": cmd_buylisting, "cancel": cmd_cancel, "challenge": cmd_challenge,
        "admin": cmd_admin, "admin_stats": cmd_admin_stats, "admin_who": cmd_admin_who, "admin_money": cmd_admin_money, "admin_item": cmd_admin_item, "admin_mon": cmd_admin_mon, "admin_ban": cmd_admin_ban, "admin_unban": cmd_admin_unban, "admin_announce": cmd_admin_announce, "admin_backup": cmd_admin_backup, "train": cmd_train, "evolve": cmd_evolve, "use": cmd_use, "dex": cmd_dex, "pokedex": cmd_dex,
        "moves": cmd_moves, "form": cmd_form, "safari": cmd_safari, "tournament": cmd_tournament, "give": cmd_give, "take": cmd_take, "eggs": cmd_eggs, "breed": cmd_breed, "quests": cmd_quests, "event": cmd_event, "quest": cmd_quests, "trade": cmd_trade, "offer": cmd_offer,
    }
    app.add_handler(TypeHandler(Update, ban_gate), group=-1)
    for name, fn in handlers.items():
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(cb_start, pattern=r"^(st|sp)\|"))
    app.add_handler(CallbackQueryHandler(cb_look, pattern=r"^(gd|tp)\|"))
    app.add_handler(CallbackQueryHandler(cb_badgecase, pattern=r"^bc\|"))
    app.add_handler(CallbackQueryHandler(cb_release, pattern=r"^rel\|"))
    app.add_handler(CallbackQueryHandler(cb_gym, pattern=r"^gy\|"))
    app.add_handler(CallbackQueryHandler(cb_league, pattern=r"^lg\|"))
    app.add_handler(CallbackQueryHandler(cb_travel, pattern=r"^tr\|"))
    app.add_handler(CallbackQueryHandler(cb_challenge, pattern=r"^ch\|"))
    app.add_handler(CallbackQueryHandler(cb_train, pattern=r"^trn\|"))
    app.add_handler(CallbackQueryHandler(cb_evolve, pattern=r"^ev\|"))
    app.add_handler(CallbackQueryHandler(cb_dex, pattern=r"^dx\|"))
    app.add_handler(CallbackQueryHandler(cb_shop, pattern=r"^sh\|"))
    app.add_handler(CallbackQueryHandler(cb_moves, pattern=r"^(mv|mx|mz)\|"))
    app.add_handler(CallbackQueryHandler(cb_form, pattern=r"^fm\|"))
    app.add_handler(CallbackQueryHandler(cb_safari, pattern=r"^sf\|"))
    app.add_handler(CallbackQueryHandler(cb_quests, pattern=r"^qs\|"))
    app.add_handler(CallbackQueryHandler(cb_help, pattern=r"^hp\|"))
    app.add_handler(CallbackQueryHandler(cb_info, pattern=r"^if\|"))
    app.add_handler(CallbackQueryHandler(cb_tournament, pattern=r"^to\|"))
    app.add_handler(CallbackQueryHandler(cb_egg, pattern=r"^eg\|"))
    app.add_handler(CallbackQueryHandler(cb_leaderboard, pattern=r"^lb\|"))
    app.add_handler(CallbackQueryHandler(cb_milestones, pattern=r"^ms\|"))
    app.add_handler(CallbackQueryHandler(cb_trade, pattern=r"^tdr\|"))
    app.add_handler(CallbackQueryHandler(cb_mint, pattern=r"^nm\|"))
    app.add_handler(CallbackQueryHandler(cb_mon, pattern=r"^cm\|"))
    app.add_handler(CallbackQueryHandler(cb_battle, pattern=rf"^[{BATTLE_OPS}]\|"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    return app
