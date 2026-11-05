"""Pokédex screens: unmet = ???, seen = name, caught = full entry (UI-agnostic text + button rows)."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from . import game, world
from .battle import type_mult
from .data import TYPES, get_provider

PAGE = 20
Rows = List[List[Tuple[str, str]]]


def rows_of(db, uid: int) -> Dict[int, bool]:
    return {r["species_id"]: bool(r["caught"]) for r in db.q("SELECT species_id, caught FROM dex WHERE user_id=?", (uid,))}


def shinies_of(db, uid: int) -> set:
    return {r["species_id"] for r in db.q("SELECT species_id FROM dex WHERE user_id=? AND shiny=1", (uid,))}


def _bar(n: int, total: int, width: int = 10) -> str:
    k = round(n / max(1, total) * width)
    return "█" * k + "░" * (width - k)


def overview(db, uid: int) -> Tuple[str, Rows]:
    """The Pokédex front page: progress in every region, shiny count and the next milestone."""
    state, shiny = rows_of(db, uid), shinies_of(db, uid)
    out = ["📖 POKÉDEX", "━━━━━━━━━━━━━━"]
    tot_c = tot_s = tot = 0
    for r in world.REGIONS:
        ids = range(r.dex_lo, r.dex_hi + 1)
        c, sn = sum(1 for i in ids if state.get(i)), sum(1 for i in ids if i in state)
        tot_c, tot_s, tot = tot_c + c, tot_s + sn, tot + len(ids)
        out.append(f"{'🏆' if c == len(ids) else '•'} {r.name}: {_bar(c, len(ids))} {c}/{len(ids)}")
    out += ["", f"✅ Caught {tot_c} · 👁 Seen {tot_s} · ✨ Shiny {len(shiny)} · {round(tot_c / max(1, tot) * 100)}% complete"]
    nxt = next(((n, v[3]) for n, v in world.DEX_MILESTONES.items() if tot_c < n), None)
    if nxt:
        out.append(f"🎯 Next milestone: {nxt[0]} caught — {nxt[1]} (/milestones)")
    return "\n".join(out), _region_nav(uid, "")


async def _name(sid: int) -> str:
    return game.pretty((await get_provider().get_species(sid)).name)


def _region_nav(uid: int, cur: str) -> Rows:
    keys = [r for r in world.REGIONS]
    rows, row = [], []
    for r in keys:
        row.append((("• " if r.key == cur else "") + r.name, f"dx|{uid}|l|{r.key}|0"))
        if len(row) == 3:
            rows.append(row)
            row = []
    return rows + ([row] if row else [])


async def dex_list(db, uid: int, region_key: str, page: int) -> Tuple[str, Rows]:
    reg = world.REGION_BY_KEY[region_key]
    ids = list(range(reg.dex_lo, reg.dex_hi + 1))
    pages = max(1, (len(ids) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    state, shiny = rows_of(db, uid), shinies_of(db, uid)
    seen = sum(1 for i in ids if i in state)
    caught = sum(1 for i in ids if state.get(i))
    lines = []
    for sid in ids[page * PAGE:(page + 1) * PAGE]:
        if state.get(sid):
            lines.append(f"✅ #{sid:03d} {await _name(sid)}{' ✨' if sid in shiny else ''}")
        elif sid in state:
            lines.append(f"👁 #{sid:03d} {await _name(sid)}")
        else:
            lines.append(f"❓ #{sid:03d} ???")
    head = [f"📖 POKÉDEX — {reg.name}", f"👁 Seen {seen} · ✅ Caught {caught} / {len(ids)}", "━━━━━━━━━━━━━━"]
    foot = ["", f"Page {page + 1}/{pages}  ·  Open an entry: /dex <number or name>"]
    nav = []
    if page > 0:
        nav.append(("◀", f"dx|{uid}|l|{region_key}|{page - 1}"))
    if page < pages - 1:
        nav.append(("▶", f"dx|{uid}|l|{region_key}|{page + 1}"))
    return "\n".join(head + lines + foot), ([nav] if nav else []) + _region_nav(uid, region_key) + [[("📊 Overview", f"dx|{uid}|o|-|0")]]


def _weakness_line(types: List[str]) -> str:
    weak, imm = [], []
    for a in TYPES:
        m = type_mult(a, types)
        if m == 0:
            imm.append(a.title())
        elif m > 1:
            weak.append(f"{a.title()} ×{int(m)}")
    out = "Weak to: " + (", ".join(weak) if weak else "nothing")
    return out + (f" · Immune: {', '.join(imm)}" if imm else "")


async def evolution_paths(sid: int, state: Dict[int, bool]) -> List[str]:
    """Evolution line(s); stages the player hasn't met yet are hidden as ???."""
    p = get_provider()
    base = sid
    for _ in range(10):
        sp = await p.get_species(base)
        if not sp.evolves_from:
            break
        base = sp.evolves_from
    paths: List[List[str]] = []

    async def walk(cur: int, trail: List[str]):
        sp = await p.get_species(cur)
        nm = await _name(cur) if cur in state else "???"
        trail = trail + [nm]
        if not sp.evos:
            paths.append(trail)
            return
        for e in sp.evos:
            if e.kind == "level":
                how = f"Lv{e.level}"
            elif e.kind == "stone":
                how = world.ITEMS[await game.evo_stone(e)].name
            elif e.kind == "friendship":
                how = "Friendship" + (f" ({e.time})" if e.time else "")
            else:
                how = "Trade"
            known = e.to in state
            await walk(e.to, trail + [f"({how if known else '?'})"])

    await walk(base, [])
    return [" ➜ ".join(pth) for pth in paths] or ["—"]


async def dex_entry(db, uid: int, sid: int) -> Tuple[str, Rows]:
    state = rows_of(db, uid)
    if sid not in state:
        return f"❓ #{sid:03d} ???\nYou haven't met this Pokémon yet.", []
    p = get_provider()
    name = await _name(sid)
    back = [[("⬅️ Back", f"dx|{uid}|l|{_region_of(sid)}|{(sid - world.REGION_BY_KEY[_region_of(sid)].dex_lo) // PAGE}")]]
    if not state[sid]:
        back[0].insert(0, ("🖼 Silhouette", f"dx|{uid}|p|{sid}|0"))
        return f"👁 #{sid:03d} {name}\nSeen — catch one to unlock its full entry!", back
    sp, form = await p.get_species(sid), await p.get_form(sid)
    abil = ", ".join(game.pretty(a) + (" (hidden)" if h else "") for a, h in form.abilities)
    lines = [f"📖 #{sid:03d} {name}" + (f" — {sp.genus}" if sp.genus else ""), "━━━━━━━━━━━━━━",
             f"• Type: {'/'.join(t.title() for t in form.types)}",
             f"• Height {form.height / 10:.1f} m · Weight {form.weight / 10:.1f} kg",
             f"• Abilities: {abil}", f"• {_weakness_line(form.types)}"]
    forms = [v for v in sp.varieties if any(f"-{x}" in v for x in ("alola", "galar", "hisui", "paldea"))]
    if forms:
        lines.append("• Regional forms: " + ", ".join(sorted({game.form_label(v) for v in forms})))
    for k, line in enumerate(await evolution_paths(sid, state)):
        lines.append(("• Evolution: " if k == 0 else "             ") + line)
    if sp.flavor:
        lines += ["", f"“{sp.flavor}”"]
    reg = world.REGION_BY_KEY[_region_of(sid)]
    lines.insert(3, f"• Native to: {reg.name}")
    if sp.mega_forms:
        lines.append("• Mega forms: " + ", ".join(sorted({game.form_label(m) or "Mega" for m in sp.mega_forms})))
    if sid in shinies_of(db, uid):
        lines.append("• ✨ You have caught a shiny one!")
    rows = [[("📚 Learnable moves", f"dx|{uid}|m|{sid}|0"), ("🖼 Picture", f"dx|{uid}|p|{sid}|0")]] + back
    return "\n".join(lines), rows


async def dex_moves(db, uid: int, sid: int) -> Tuple[str, Rows]:
    state = rows_of(db, uid)
    if not state.get(sid):
        return "Catch this Pokémon to see its learnable moves.", []
    form = await get_provider().get_form(sid)
    lv = [f"{game.pretty(m)} (Lv{l})" for m, l in form.learnset]
    lines = [f"📚 {await _name(sid)} — learnable moves", "━━━━━━━━━━━━━━", "🔁 Level-up: " + (", ".join(lv) or "—")]
    if form.tutor_moves:
        lines.append("🎓 Tutor (free): " + ", ".join(game.pretty(m) for m in form.tutor_moves))
    if form.tm_moves:
        lines.append("💿 TMs ($5–7k, reusable): " + ", ".join(game.pretty(m) for m in form.tm_moves))
    return "\n".join(lines)[:3900], [[("⬅️ Back", f"dx|{uid}|e|{sid}|0")]]


def _region_of(sid: int) -> str:
    for r in world.REGIONS:
        if r.dex_lo <= sid <= r.dex_hi:
            return r.key
    return world.REGIONS[0].key


async def find_species(db, uid: int, query: str) -> Optional[int]:
    q = query.strip().lower().lstrip("#")
    if q.isdigit():
        return int(q) if 1 <= int(q) <= 1025 else None
    for sid in rows_of(db, uid):                 # names only resolve for Pokémon you've met
        if (await _name(sid)).lower().replace(" ", "-") == q.replace(" ", "-"):
            return sid
    return None
