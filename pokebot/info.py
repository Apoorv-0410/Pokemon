"""/help (sectioned) and /info (abilities, natures, held items, type chart)."""
import json
import os
from typing import List, Optional, Tuple

from . import world
from .battle import NATURES, type_mult
from .data import TYPES

DIV = "━━━━━━━━━━━━━━"
Rows = List[List[Tuple[str, str]]]
_DATA = json.load(open(os.path.join(os.path.dirname(__file__), "info.json")))
ABILITIES: List[Tuple[str, str]] = [tuple(x) for x in _DATA["abilities"]]
ITEM_INFO: List[Tuple[str, str]] = [tuple(x) for x in _DATA["items"]]
PAGE = 12

# --------------------------------------------------------------------------- /help
HELP_SECTIONS = [
    ("adv", "🌿 Adventure", [
        "/explore (/hunt, /find) — wild encounter, trainers, eggs, events", "/gym — next gym leader", "/league — Elite Four & Champion (8 badges)",
        "/travel — change region", "/heal — free Pokémon Center", "/safari — Safari Zone ($300): 3 legendaries per trip, fleeing Pokémon may drop items", "/daily — login streak bonus",
        "/quests — daily & weekly quests", "/event — today's event Pokémon ($800 bonus)"]),
    ("mon", "🐾 Pokémon", [
        "/party · /box — team and storage", "/mon <id> — stats, IVs/EVs, nickname, party/box and release buttons (also shown after every catch)", "/nick <id> <name> · /fav <id>", "/addparty <id> · /tobox <id> · /swap <id> <id>",
        "/release <id>", "/evolve <id> — stones & friendship", "/use <item> <id> — candy, mints, ability items", "/moves <id> — relearn, tutor, TMs",
        "/form <id> — change form ($500)", "/train — EV training ($200)", "/give <item> <id> · /take <id> — held items"]),
    ("dex", "📖 Pokédex & eggs", [
        "/dex — overview · /dex <name or no.> — entry + picture", "/milestones — Pokédex rewards", "/breed <id> <id> — make an egg ($500)",
        "/eggs — hatch progress (70 /explore trips)", "/top — leaderboards"]),
    ("shop", "🎒 Items & shop", [
        "/card — trainer card · /trainer — change look · /name <name> — trainer name", "/bag · /shop", "/buy <item> [n] · /sell <item> [n]",
        "/gimmicks — Mega / Z / Dynamax / Tera (Mega Stones and Z-Crystals are in /shop)", "/info — abilities, natures, held items"]),
    ("fight", "⚔️ Battles & social", [
        "/challenge — reply to a player: friendly battle (singles, doubles, VGC). Winner +$900, loser −$200 (5 prizes/day)",
        "/tournament [fee] [casual|standard|vgc] — group tournament, random draw", "/trade <id|0> [money] — reply to a player; they answer /offer <id|0> [money]",
        "/badges — your badge case",
        "/whosthat — silhouette guessing game; first right answer (type it or /guess <name>) wins $20"]),
    ("mkt", "🏪 Market", [
        "/market [page] · /mylistings", "/sellmon <id> <price>", "/sellitem <item> <n> <price>", "/buylisting <id> · /cancel <id>"]),
]


def help_home() -> Tuple[str, Rows]:
    btns = [(label, f"hp|{key}") for key, label, _ in HELP_SECTIONS]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    return "\n".join(["🎮 POKÉBOT — HELP", DIV, "Pick a topic below 👇", "", "💡 New here? /explore to find Pokémon, /shop for balls, /gym to start your badge hunt!"]), rows


def help_section(key: str) -> Tuple[str, Rows]:
    for k, label, cmds in HELP_SECTIONS:
        if k == key:
            return "\n".join([label, DIV] + [f"• {c}" for c in cmds]), [[("⬅️ All topics", "hp|home")]]
    return help_home()


# --------------------------------------------------------------------------- /info
def info_home() -> Tuple[str, Rows]:
    text = "\n".join(["📚 INFO", DIV, "Look things up:", "• 🧬 Abilities — what each ability does", "• 🎭 Natures — which stats they raise/lower",
                      "• 🎒 Held items — what they do (🛒 = sold in /shop)", "• 🌀 Type chart — what beats what", "",
                      "Search directly: /info <name>   e.g. /info intimidate · /info leftovers · /info adamant"])
    return text, [[("🧬 Abilities", "if|a|0"), ("🎭 Natures", "if|n|0")], [("🎒 Held items", "if|i|0"), ("🌀 Type chart", "if|t|0")]]


def _pages(kind: str, page: int, entries, title: str) -> Tuple[str, Rows]:
    pages = max(1, -(-len(entries) // PAGE))
    page = max(0, min(page, pages - 1))
    lines = [title, DIV] + [f"• {n} — {d}" for n, d in entries[page * PAGE:(page + 1) * PAGE]] + ["", f"Page {page + 1}/{pages}"]
    nav = [b for b in [("◀", f"if|{kind}|{page - 1}") if page else None, ("▶", f"if|{kind}|{page + 1}") if page < pages - 1 else None] if b]
    return "\n".join(lines), ([nav] if nav else []) + [[("⬅️ Info", "if|h|0")]]


def _shop_mark(name: str) -> str:
    return " 🛒" if any(i.name == name for i in world.ITEMS.values()) else ""


def nature_lines() -> List[str]:
    lab = {"atk": "Attack", "def": "Defense", "spa": "Sp. Atk", "spd": "Sp. Def", "spe": "Speed"}
    out = []
    for n, v in NATURES.items():
        out.append(f"• {n}: +{lab[v[0]]}, −{lab[v[1]]}" if v else f"• {n}: neutral")
    return out


def type_chart() -> str:
    lines = ["🌀 TYPE CHART (attacker → hits super-effectively / not very / no effect)", DIV]
    for a in TYPES:
        se = [d.title() for d in TYPES if type_mult(a, [d]) > 1]
        nv = [d.title() for d in TYPES if 0 < type_mult(a, [d]) < 1]
        no = [d.title() for d in TYPES if type_mult(a, [d]) == 0]
        lines.append(f"• {a.title()}: ×2 {', '.join(se) or '—'}" + (f" · ×½ {', '.join(nv)}" if nv else "") + (f" · ×0 {', '.join(no)}" if no else ""))
    return "\n".join(lines)


def info_view(kind: str, page: int) -> Tuple[str, Rows]:
    if kind == "a":
        return _pages("a", page, ABILITIES, "🧬 ABILITIES")
    if kind == "i":
        return _pages("i", page, [(n + _shop_mark(n), d) for n, d in ITEM_INFO], "🎒 HELD ITEMS (🛒 = in /shop)")
    if kind == "n":
        return "\n".join(["🎭 NATURES", DIV] + nature_lines() + ["", "Change a nature with a Nature Mint (/shop → Candy & Items, /use nature-mint <id>)."]), [[("⬅️ Info", "if|h|0")]]
    if kind == "t":
        return type_chart(), [[("⬅️ Info", "if|h|0")]]
    return info_home()


def search(query: str) -> Optional[str]:
    """Free-text lookup across abilities, items and natures."""
    q = query.strip().lower().replace("-", " ")
    if not q:
        return None
    out = []
    for n, d in ABILITIES:
        if n.lower() == q:
            out.append(f"🧬 {n} (ability): {d}")
    for n, d in ITEM_INFO:
        if n.lower().replace("-", " ") == q:
            out.append(f"🎒 {n} (held item){_shop_mark(n)}: {d}")
    for n, v in NATURES.items():
        if n.lower() == q:
            lab = {"atk": "Attack", "def": "Defense", "spa": "Sp. Atk", "spd": "Sp. Def", "spe": "Speed"}
            out.append(f"🎭 {n} (nature): " + (f"+{lab[v[0]]}, −{lab[v[1]]}" if v else "neutral — changes nothing"))
    if out:
        return "\n".join(out)
    near = [n for n, _ in ABILITIES + ITEM_INFO if q in n.lower().replace("-", " ")][:8]
    return ("No exact match. Did you mean: " + ", ".join(near) + "?") if near else None


_AB = {n.lower().replace(" ", "").replace("-", ""): d for n, d in ABILITIES}


def ability_desc(slug: str) -> str:
    return _AB.get((slug or "").lower().replace(" ", "").replace("-", ""), "")
