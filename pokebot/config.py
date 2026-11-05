"""Central configuration. Values come from environment variables / .env."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
# Saves live OUTSIDE the project folder (in your user folder), so replacing/re-extracting the bot never touches them.
LEGACY_DATA_DIR = BASE_DIR / "data"
DATA_DIR = Path(os.getenv("POKEBOT_DATA_DIR") or (Path.home() / "PokebotData"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
if DATA_DIR.resolve() != LEGACY_DATA_DIR.resolve() and LEGACY_DATA_DIR.is_dir():
    # one-time move of an older in-project save (only when the new location has none yet)
    import shutil
    for _name in ("pokebot.sqlite3", "pokebot.sqlite3-wal", "pokebot.sqlite3-shm"):
        _old, _new = LEGACY_DATA_DIR / _name, DATA_DIR / _name
        if _old.exists() and not (DATA_DIR / "pokebot.sqlite3").exists():
            shutil.copy2(_old, _new)
    for _sub in ("cache", "backups", "trainers"):
        if (LEGACY_DATA_DIR / _sub).is_dir() and not (DATA_DIR / _sub).exists():
            shutil.copytree(LEGACY_DATA_DIR / _sub, DATA_DIR / _sub)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = os.getenv("POKEBOT_DB", str(DATA_DIR / "pokebot.sqlite3"))
CACHE_DIR = Path(os.getenv("POKEBOT_CACHE", DATA_DIR / "cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# "pokeapi" = live data from pokeapi.co (cached on disk). "mock" = small offline dataset.
DATA_PROVIDER = os.getenv("POKEBOT_PROVIDER", "pokeapi")
POKEAPI_BASE = os.getenv("POKEAPI_BASE", "https://pokeapi.co/api/v2")

# "bw" = Black/White sprites for Gen 1-5 (official artwork for Gen 6-9),
# "bw-animated" = animated BW gifs for Gen 1-5, "artwork" = official artwork everywhere, "none".
SPRITE_STYLE = os.getenv("POKEBOT_SPRITES", "artwork")

ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}

START_MONEY = 3000
DAILY_BONUS = 1000
MARKET_FEE = 0.05          # fraction of sale price taken by the market
MAX_PARTY = 6

BACKUP_DIR = Path(os.getenv("POKEBOT_BACKUPS", DATA_DIR / "backups"))
BACKUP_KEEP = int(os.getenv("POKEBOT_BACKUP_KEEP", "7"))            # daily copies to keep
BACKUP_HOUR_UTC = int(os.getenv("POKEBOT_BACKUP_HOUR", "4"))
