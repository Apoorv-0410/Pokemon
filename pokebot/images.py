"""Picture trainer card (Pillow). Art is fetched from public sprite sets on demand and cached on disk;
every download has a drawn fallback, so the card always renders (also offline / in tests)."""
from __future__ import annotations

import asyncio
import io
import random
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from . import config, sprites

TRAINER_DIR = config.DATA_DIR / "trainers"          # drop your own <slug>.png here to override any download
TRAINER_URL = "https://play.pokemonshowdown.com/sprites/trainers/{slug}.png"

# (slug, display name) - slugs follow the public Pokémon Showdown trainer-sprite names
BOY_TRAINERS: List[Tuple[str, str]] = [
    ("ash", "Ash Ketchum"), ("red", "Red"), ("ethan", "Ethan"), ("brendan", "Brendan"), ("lucas", "Lucas"),
    ("hilbert", "Hilbert"), ("nate", "Nate"), ("calem", "Calem"), ("elio", "Elio"), ("victor", "Victor"),
    ("florian", "Florian"), ("rei", "Rei"), ("za-boy", "Z-A Hero"),
]
GIRL_TRAINERS: List[Tuple[str, str]] = [
    ("leaf", "Leaf"), ("lyra", "Lyra"), ("may", "May"), ("dawn", "Dawn"), ("hilda", "Hilda"), ("rosa", "Rosa"),
    ("serena", "Serena"), ("selene", "Selene"), ("gloria", "Gloria"), ("juliana", "Juliana"),
    ("akari", "Akari"), ("za-girl", "Z-A Hero"),
]
# extra file names tried in order when the first one isn't found (unverified guesses; or drop <slug>.png in data/trainers)
ALT_SLUGS = {"za-boy": ["lzaprotagonist", "protagonistza", "zaboy"], "za-girl": ["lzaprotagonistf", "protagonistzaf", "zagirl"],
             "rei": ["rei-legendsarceus", "legendsarceusm"], "akari": ["akari-legendsarceus", "legendsarceusf"]}
CARD_COLORS = [(214, 64, 64), (226, 150, 40), (46, 140, 96), (70, 110, 200), (120, 80, 190), (200, 90, 150),
               (40, 150, 170), (110, 120, 135), (180, 110, 60), (60, 160, 70)]
TRAINERS: Dict[str, str] = dict(BOY_TRAINERS + GIRL_TRAINERS)

REGION_COLORS = {"kanto": (214, 64, 64), "johto": (226, 170, 40), "hoenn": (46, 140, 96), "sinnoh": (86, 110, 190),
                 "unova": (90, 90, 100), "kalos": (200, 90, 150), "alola": (240, 130, 50), "galar": (150, 60, 190),
                 "paldea": (220, 60, 90)}


def trainers_for(gender: str) -> List[Tuple[str, str]]:
    return BOY_TRAINERS if gender == "boy" else GIRL_TRAINERS


def random_trainer(gender: str, rng: Optional[random.Random] = None) -> str:
    return (rng or random).choice(trainers_for(gender))[0]


# --------------------------------------------------------------------------- fonts / download
def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _download(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pokebot/1.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = r.read()
        Image.open(io.BytesIO(data)).verify()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True
    except Exception:
        return False


async def _cached(url: str, dest: Path) -> Optional[Image.Image]:
    if not dest.exists():
        if not await asyncio.to_thread(_download, url, dest):
            return None
    try:
        return Image.open(dest).convert("RGBA")
    except Exception:
        return None


async def trainer_art(slug: str) -> Optional[Image.Image]:
    dest = TRAINER_DIR / f"{slug}.png"
    for name in [slug] + ALT_SLUGS.get(slug, []):
        img = await _cached(TRAINER_URL.format(slug=name), dest)
        if img:
            return img
    return None


async def mon_art(species_id: int, shiny: bool = False) -> Optional[Image.Image]:
    return await _cached(sprites.artwork_url(species_id, shiny=shiny), config.CACHE_DIR / "img" / f"art{'s' if shiny else ''}{species_id}.png")


# --------------------------------------------------------------------------- drawing helpers
def _fallback_trainer(slug: str, size: int) -> Image.Image:
    """A friendly drawn avatar used when the sprite can't be downloaded."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rnd = random.Random(slug)
    body = tuple(rnd.randint(60, 200) for _ in range(3)) + (255,)
    cx = size // 2
    d.ellipse([cx - size * .28, size * .50, cx + size * .28, size * 1.15], fill=body)          # body
    d.ellipse([cx - size * .20, size * .12, cx + size * .20, size * .52], fill=(255, 224, 189, 255))  # head
    d.pieslice([cx - size * .23, size * .06, cx + size * .23, size * .50], 180, 360, fill=(200, 40, 40, 255))  # cap
    d.rectangle([cx - size * .26, size * .27, cx + size * .26, size * .31], fill=(150, 25, 25, 255))
    d.ellipse([cx - size * .09, size * .34, cx - size * .05, size * .38], fill=(30, 30, 30, 255))
    d.ellipse([cx + size * .05, size * .34, cx + size * .09, size * .38], fill=(30, 30, 30, 255))
    d.arc([cx - size * .08, size * .38, cx + size * .08, size * .48], 20, 160, fill=(120, 40, 40, 255), width=3)
    return img


def _fit(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    scale = min(box_w / img.width, box_h / img.height)
    if img.width < 120:                                 # small pixel sprites: keep them crisp
        return img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.NEAREST)
    return img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)


def _shadow(d: ImageDraw.ImageDraw, cx: int, y: int, w: int):
    d.ellipse([cx - w // 2, y - 10, cx + w // 2, y + 12], fill=(0, 0, 0, 70))


def _chip(d: ImageDraw.ImageDraw, x: int, y: int, w: int, label: str, value: str):
    d.rounded_rectangle([x, y, x + w, y + 78], 16, fill=(255, 255, 255, 235))
    d.text((x + 16, y + 10), label.upper(), font=font(15), fill=(120, 120, 135))
    d.text((x + 16, y + 34), value, font=font(30), fill=(35, 35, 50))


def _gradient(w: int, h: int, c: Tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (w, h))
    px = img.load()
    for y in range(h):
        t = y / h
        for x in range(w):
            k = 1 - 0.35 * t - 0.1 * (x / w)
            px[x, y] = (int(c[0] * k), int(c[1] * k), int(c[2] * k), 255)
    return img


async def render_card(*, name: str, color_idx: int = 0, region_key: str = "", region_name: str, trainer: str, money: int, balls: int,
                      owned: int, wins: int, losses: int, since: str, fav_species: Optional[int],
                      fav_name: str = "", shiny: bool = False, badges: int = 0) -> bytes:
    W, H = 1000, 600
    color = CARD_COLORS[color_idx % len(CARD_COLORS)]
    img = _gradient(W, H, color).convert("RGB")        # RGB base so translucent shapes blend
    d = ImageDraw.Draw(img, "RGBA")
    for i in range(7):                                                   # soft decorative circles
        r = 60 + i * 38
        d.ellipse([W - 120 - r, -r + 40, W - 120 + r, r + 40], outline=(255, 255, 255, 18), width=3)
    d.rounded_rectangle([14, 14, W - 14, H - 14], 28, outline=(255, 255, 255, 120), width=4)
    d.text((44, 30), "TRAINER CARD", font=font(40), fill=(255, 255, 255, 255))
    d.text((W - 44, 40), f"{region_name.upper()}", font=font(26), fill=(255, 255, 255, 220), anchor="ra")
    d.text((W - 44, 74), f"{badges} badges", font=font(20, False), fill=(255, 255, 255, 200), anchor="ra")

    # trainer (left) and favourite Pokémon (right) on rounded spotlight panels
    for x0 in (44, W - 44 - 290):
        d.rounded_rectangle([x0, 110, x0 + 290, 400], 24, fill=(255, 255, 255, 60))
    t_img = await trainer_art(trainer)
    t_img = _fit(t_img if t_img else _fallback_trainer(trainer, 300), 250, 262)
    _shadow(d, 44 + 145, 386, 180)
    img.paste(t_img, (44 + 145 - t_img.width // 2, 386 - t_img.height), t_img)
    f_img = await mon_art(fav_species, shiny) if fav_species else None
    if f_img:
        f_img = _fit(f_img, 260, 262)
        _shadow(d, W - 44 - 145, 386, 190)
        img.paste(f_img, (W - 44 - 145 - f_img.width // 2, 386 - f_img.height), f_img)
    else:
        d.text((W - 44 - 145, 255), "?", font=font(90), fill=(255, 255, 255, 140), anchor="mm")

    # name in the middle
    cx = W // 2
    d.text((cx, 190), name[:16], font=font(44), fill=(255, 255, 255, 255), anchor="mm")
    d.text((cx, 238), f"{wins}W  -  {losses}L", font=font(30, False), fill=(255, 255, 255, 230), anchor="mm")
    d.text((cx, 284), "Journey began", font=font(18, False), fill=(255, 255, 255, 190), anchor="mm")
    d.text((cx, 310), since, font=font(24), fill=(255, 255, 255, 240), anchor="mm")
    if fav_name:
        d.text((W - 44 - 145, 428), ("* " if shiny else "") + fav_name[:18], font=font(26), fill=(255, 255, 255, 255), anchor="mm")

    cw = (W - 88 - 3 * 16) // 4
    for i, (lab, val) in enumerate([("Money", f"${money:,}"), ("Poké Balls", str(balls)), ("Pokémon", str(owned)),
                                    ("Region", region_name)]):
        _chip(d, 44 + i * (cw + 16), 470, cw, lab, val)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


# =========================================================================== battle scene
STATUS_PILL = {"brn": ("BRN", (230, 90, 40)), "par": ("PAR", (225, 190, 30)), "psn": ("PSN", (150, 70, 190)),
               "tox": ("TOX", (120, 40, 160)), "slp": ("SLP", (110, 120, 140)), "frz": ("FRZ", (80, 170, 220))}
WEATHER_LOOK = {  # sky colour pair + label
    "raindance": ((70, 85, 115), (120, 135, 160), "RAIN"), "primordialsea": ((55, 70, 100), (100, 115, 145), "HEAVY RAIN"),
    "sunnyday": ((255, 190, 90), (255, 230, 150), "SUN"), "desolateland": ((240, 140, 60), (255, 200, 120), "HARSH SUN"),
    "sandstorm": ((190, 160, 110), (225, 205, 160), "SANDSTORM"), "hail": ((150, 175, 200), (215, 230, 240), "HAIL"),
    "snow": ((165, 190, 215), (225, 238, 248), "SNOW"), "snowscape": ((165, 190, 215), (225, 238, 248), "SNOW"),
    "deltastream": ((90, 140, 150), (150, 195, 200), "WINDS"),
}
_BG_CACHE: Dict[tuple, Image.Image] = {}


def _scene_bg(w: int, h: int, weather: str) -> Image.Image:
    key = (w, h, weather)
    if key in _BG_CACHE:
        return _BG_CACHE[key].copy()
    top, bot, _ = WEATHER_LOOK.get(weather, ((110, 175, 235), (200, 232, 250), ""))
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    horizon = int(h * 0.52)
    for y in range(horizon):
        t = y / horizon
        d.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    for y in range(horizon, h):
        t = (y - horizon) / (h - horizon)
        d.line([(0, y), (w, y)], fill=(int(96 - 30 * t), int(176 - 40 * t), int(86 - 25 * t)))
    d = ImageDraw.Draw(img, "RGBA")
    d.ellipse([-120, horizon - 40, w * 0.55, horizon + 60], fill=(70, 150, 70, 120))        # soft hills
    d.ellipse([w * 0.4, horizon - 55, w + 160, horizon + 50], fill=(60, 140, 66, 120))
    if weather in ("raindance", "primordialsea"):
        rnd = random.Random(3)
        for _ in range(110):
            x, y = rnd.randint(0, w), rnd.randint(0, h)
            d.line([(x, y), (x - 8, y + 22)], fill=(210, 225, 255, 120), width=2)
    elif weather in ("hail", "snow", "snowscape"):
        rnd = random.Random(4)
        for _ in range(90):
            x, y = rnd.randint(0, w), rnd.randint(0, h)
            d.ellipse([x, y, x + 5, y + 5], fill=(255, 255, 255, 200))
    elif weather == "sandstorm":
        d.rectangle([0, 0, w, h], fill=(200, 170, 110, 55))
    elif weather in ("sunnyday", "desolateland"):
        d.ellipse([w - 130, -70, w + 70, 130], fill=(255, 240, 160, 150))
    _BG_CACHE[key] = img.copy()
    return img


_FORM_IDS: Dict[str, int] = {}


async def _art_id(mon: dict) -> int:
    """PokeAPI id for the exact form on the field (Mega Charizard X, Alolan Raichu...); the base id when unknown."""
    sp = str(mon.get("species") or "")
    if "-" not in sp:
        if not (mon.get("dynamaxed") and sp):
            return mon["num"]
        sp += "-Gmax"                                       # Gigantamax art for species that have it (decoration only)
    slug = sp.lower().replace(" ", "-").replace(".", "").replace("'", "")
    if slug not in _FORM_IDS:
        try:
            from .data import get_provider
            _FORM_IDS[slug] = (await get_provider().get_form(slug)).id
        except Exception:
            _FORM_IDS[slug] = mon["num"]
    return _FORM_IDS[slug]


async def _battle_sprite(num: int, back: bool, shiny: bool, base: Optional[int] = None) -> Optional[Image.Image]:
    """Same sprite set for both sides (front for the foe, back for you); HD artwork only as a fallback."""
    path = f"{'back/' if back else ''}{'shiny/' if shiny else ''}{num}.png"
    img = await _cached(f"{sprites._RAW}/{path}", config.CACHE_DIR / "img" / f"{'b' if back else 'f'}{'s' if shiny else ''}{num}.png")
    if img is not None:
        return img
    url = sprites.artwork_url(num, shiny=shiny)
    img = await _cached(url, config.CACHE_DIR / "img" / f"art{'s' if shiny else ''}{num}.png")
    if img is None and shiny:
        img = await _cached(sprites.artwork_url(num), config.CACHE_DIR / "img" / f"art{num}.png")
    if img is None and base and base != num:               # form art missing: show the base species instead
        return await _battle_sprite(base, back, shiny)
    if img is not None and back:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    return img


TYPE_COLORS = {"normal": (168, 168, 120), "fire": (240, 128, 48), "water": (104, 144, 240), "electric": (248, 208, 48),
               "grass": (120, 200, 80), "ice": (152, 216, 216), "fighting": (192, 48, 40), "poison": (160, 64, 160),
               "ground": (224, 192, 104), "flying": (168, 144, 240), "psychic": (248, 88, 136), "bug": (168, 184, 32),
               "rock": (184, 160, 56), "ghost": (112, 88, 152), "dragon": (112, 56, 248), "dark": (112, 88, 72),
               "steel": (184, 184, 208), "fairy": (238, 153, 172), "stellar": (120, 220, 200)}


def _tera_effect(d: ImageDraw.ImageDraw, tera: str, cx: int, top: int, bottom: int):
    """Crystal aura + a jewel floating above the Pokémon, in the Tera type's colour."""
    c = TYPE_COLORS.get(str(tera).lower(), (150, 220, 255))
    d.ellipse([cx - 125, top - 15, cx + 125, bottom + 5], fill=c + (60,), outline=c + (170,), width=3)
    jy = top - 26
    d.polygon([(cx, jy - 22), (cx + 18, jy), (cx, jy + 22), (cx - 18, jy)], fill=c + (235,), outline=(255, 255, 255, 255))
    d.line([(cx - 18, jy), (cx + 18, jy)], fill=(255, 255, 255, 200), width=2)
    for dx, dy in ((-60, -4), (62, 6), (-34, 40), (40, 52)):
        d.polygon([(cx + dx, top + 20 + dy - 7), (cx + dx + 4, top + 20 + dy), (cx + dx, top + 20 + dy + 7),
                   (cx + dx - 4, top + 20 + dy)], fill=(255, 255, 255, 220))


def _hp_color(frac: float):
    return (70, 200, 90) if frac > .5 else (240, 195, 40) if frac > .2 else (225, 70, 60)


def _info_box(d: ImageDraw.ImageDraw, x: int, y: int, m: dict, exact: bool, compact: bool = False):
    if compact:
        _info_compact(d, x, y, m, exact)
        return
    w, h = 330, 92
    d.rounded_rectangle([x, y, x + w, y + h], 16, fill=(255, 255, 255, 235), outline=(60, 60, 80, 255), width=3)
    nm = (("* " if m.get("shiny") else "") + str(m["name"]))[:15]
    d.text((x + 14, y + 8), nm, font=font(23), fill=(35, 35, 50))
    d.text((x + w - 14, y + 11), f"Lv{m['level']}", font=font(20), fill=(70, 70, 90), anchor="ra")
    mx = max(1, m["bmax"] if m.get("dynamaxed") else m["maxhp"])
    hp = max(0, m["bhp"] if m.get("dynamaxed") else m["hp"])
    frac = min(1.0, hp / mx)
    d.text((x + 14, y + 44), "HP", font=font(16), fill=(200, 120, 40))
    bx0, bx1, by = x + 48, x + w - 14, y + 46
    d.rounded_rectangle([bx0, by, bx1, by + 16], 8, fill=(70, 70, 80))
    if frac > 0:
        d.rounded_rectangle([bx0, by, bx0 + max(10, int((bx1 - bx0) * frac)), by + 16], 8, fill=_hp_color(frac))
    d.text((x + w - 14, y + 66), f"{hp}/{mx}" if exact else f"{round(frac * 100)}%", font=font(16, False),
           fill=(60, 60, 80), anchor="ra")
    px = x + 14
    pills = []
    if m.get("status") in STATUS_PILL:
        pills.append(STATUS_PILL[m["status"]])
    if m.get("dynamaxed"):
        pills.append(("DMAX", (210, 40, 70)))
    if m.get("tera"):
        pills.append(("TERA", (60, 160, 200)))
    if m.get("mega"):
        pills.append(("MEGA", (170, 70, 210)))
    for label, col in pills:
        tw = int(d.textlength(label, font=font(14))) + 16
        d.rounded_rectangle([px, y + 66, px + tw, y + 84], 9, fill=col)
        d.text((px + 8, y + 68), label, font=font(14), fill=(255, 255, 255))
        px += tw + 6


def _info_compact(d: ImageDraw.ImageDraw, x: int, y: int, m: dict, exact: bool):
    w, h = 300, 70
    d.rounded_rectangle([x, y, x + w, y + h], 14, fill=(255, 255, 255, 235), outline=(60, 60, 80, 255), width=3)
    d.text((x + 12, y + 6), (("* " if m.get("shiny") else "") + str(m["name"]))[:14], font=font(18), fill=(35, 35, 50))
    d.text((x + w - 12, y + 8), f"Lv{m['level']}", font=font(16), fill=(70, 70, 90), anchor="ra")
    mx = max(1, m["bmax"] if m.get("dynamaxed") else m["maxhp"])
    hp = max(0, m["bhp"] if m.get("dynamaxed") else m["hp"])
    frac = min(1.0, hp / mx)
    d.rounded_rectangle([x + 12, y + 32, x + w - 12, y + 44], 6, fill=(70, 70, 80))
    if frac > 0:
        d.rounded_rectangle([x + 12, y + 32, x + 12 + max(8, int((w - 24) * frac)), y + 44], 6, fill=_hp_color(frac))
    px = x + 12
    pills = []
    if m.get("status") in STATUS_PILL:
        pills.append(STATUS_PILL[m["status"]])
    if m.get("dynamaxed"):
        pills.append(("DMAX", (210, 40, 70)))
    if m.get("tera"):
        pills.append(("TERA", (60, 160, 200)))
    if m.get("mega"):
        pills.append(("MEGA", (170, 70, 210)))
    for label, col in pills:
        tw = int(d.textlength(label, font=font(12))) + 12
        d.rounded_rectangle([px, y + 49, px + tw, y + 63], 7, fill=col)
        d.text((px + 6, y + 50), label, font=font(12), fill=(255, 255, 255))
        px += tw + 5
    d.text((x + w - 12, y + 49), f"{hp}/{mx}" if exact else f"{round(frac * 100)}%", font=font(13, False), fill=(60, 60, 80), anchor="ra")


async def render_battle(foe: dict, own: dict, weather: str = "", terrain: str = "", foe2: Optional[dict] = None,
                        own2: Optional[dict] = None, public: bool = False) -> bytes:
    """public=True: a spectator view for friendly battles (both sides show % only, nobody's exact HP)."""
    if foe2 or own2:
        return await _render_doubles([m for m in (foe, foe2) if m], [m for m in (own, own2) if m], weather, terrain, public)
    W, H = 800, 450
    img = _scene_bg(W, H, weather)
    d = ImageDraw.Draw(img, "RGBA")
    d.ellipse([480, 215, 740, 262], fill=(40, 90, 40, 140))                 # platforms
    d.ellipse([60, 385, 380, 440], fill=(40, 90, 40, 160))
    for mon, back, cx, base, box in ((foe, False, 610, 245, (220, 200)), (own, True, 220, 415, (260, 240))):
        spr = await _battle_sprite(await _art_id(mon), back, bool(mon.get("shiny")), mon["num"])
        if mon.get("hp", 1) <= 0:
            continue
        if spr is None:
            d.ellipse([cx - 50, base - 120, cx + 50, base - 20], fill=(255, 255, 255, 160))
            d.text((cx, base - 70), "?", font=font(60), fill=(60, 60, 80), anchor="mm")
            continue
        scale = 1.12 if mon.get("dynamaxed") else 1.0
        spr = _fit(spr, int(box[0] * scale), int(box[1] * scale))
        if mon.get("dynamaxed"):
            d.ellipse([cx - 120, base - 215, cx + 120, base + 8], fill=(230, 40, 70, 70))
        if mon.get("tera"):
            _tera_effect(d, mon["tera"], cx, base - spr.height + 8, base + 8)
        img.paste(spr, (cx - spr.width // 2, base - spr.height + 8), spr)
    _info_box(d, 24, 20, foe, exact=False)
    _info_box(d, W - 24 - 330, 332, own, exact=not public)
    look = WEATHER_LOOK.get(weather)
    tags = ([look[2]] if look else []) + ([terrain.replace("terrain", "").upper() + " TERRAIN"] if terrain else [])
    x = W - 24
    for t in tags:
        tw = int(d.textlength(t, font=font(15))) + 20
        x -= tw
        d.rounded_rectangle([x, 20, x + tw, 46], 13, fill=(20, 20, 40, 190))
        d.text((x + 10, 24), t, font=font(15), fill=(255, 255, 255))
        x -= 8
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


async def _render_doubles(foes: List[dict], owns: List[dict], weather: str, terrain: str, public: bool = False) -> bytes:
    W, H = 800, 450
    img = _scene_bg(W, H, weather)
    d = ImageDraw.Draw(img, "RGBA")
    foe_pos = [(540, 190, (150, 150)), (700, 235, (150, 150))]
    own_pos = [(130, 350, (190, 190)), (330, 410, (190, 190))]
    for mons, pos, back in ((foes, foe_pos, False), (owns, own_pos, True)):
        for mon, (cx, base, box) in zip(mons, pos):
            d.ellipse([cx - 80, base - 12, cx + 80, base + 16], fill=(40, 90, 40, 140))
            if mon.get("hp", 1) <= 0:
                continue
            spr = await _battle_sprite(await _art_id(mon), back, bool(mon.get("shiny")), mon["num"])
            if spr is None:
                continue
            spr = _fit(spr, *box)
            if mon.get("tera"):
                _tera_effect(d, mon["tera"], cx, base - spr.height + 8, base + 8)
            img.paste(spr, (cx - spr.width // 2, base - spr.height + 8), spr)
    for i, m in enumerate(foes):
        _info_compact(d, 14, 10 + i * 78, m, exact=False)
    for i, m in enumerate(owns):
        _info_compact(d, W - 314, 296 + i * 78, m, exact=not public)
    look = WEATHER_LOOK.get(weather)
    tags = ([look[2]] if look else []) + ([terrain.replace("terrain", "").upper() + " TERRAIN"] if terrain else [])
    x = W - 14
    for t in tags:
        tw = int(d.textlength(t, font=font(15))) + 20
        x -= tw
        d.rounded_rectangle([x, 12, x + tw, 38], 13, fill=(20, 20, 40, 190))
        d.text((x + 10, 16), t, font=font(15), fill=(255, 255, 255))
        x -= 8
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


async def dex_picture(species_id: int, revealed: bool, shiny: bool = False) -> Optional[bytes]:
    """Pokédex entry picture: full-colour artwork once caught, a black silhouette while only seen."""
    art = await mon_art(species_id, shiny=shiny and revealed)
    if art is None:
        return None
    W = 520
    img = _gradient(W, W, (70, 90, 150) if revealed else (40, 44, 60)).convert("RGB")
    art = art.convert("RGBA")
    art.thumbnail((W - 70, W - 70))
    if not revealed:
        art = Image.merge("RGBA", (*(Image.new("L", art.size, 12),) * 3, art.getchannel("A")))
    img.paste(art, ((W - art.width) // 2, (W - art.height) // 2), art)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
