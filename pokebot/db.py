"""SQLite persistence: players, Pokémon, inventory, badges, market. Thread-safe, transactional."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS players(
  user_id INTEGER PRIMARY KEY, name TEXT, region TEXT NOT NULL DEFAULT 'kanto',
  money INTEGER NOT NULL DEFAULT 0, last_daily TEXT, created REAL,
  wins INTEGER NOT NULL DEFAULT 0, losses INTEGER NOT NULL DEFAULT 0,
  pvp_wins INTEGER NOT NULL DEFAULT 0, pvp_losses INTEGER NOT NULL DEFAULT 0, fav_mon INTEGER,
  ev_stat TEXT, ev_left INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS mons(
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner INTEGER NOT NULL, species_id INTEGER NOT NULL,
  species_name TEXT NOT NULL, nickname TEXT, level INTEGER NOT NULL, xp INTEGER NOT NULL,
  iv TEXT NOT NULL, moves TEXT NOT NULL, ability TEXT NOT NULL, tera_type TEXT NOT NULL,
  hp INTEGER NOT NULL, slot INTEGER, listed INTEGER NOT NULL DEFAULT 0, caught REAL,
  shiny INTEGER NOT NULL DEFAULT 0, friendship INTEGER NOT NULL DEFAULT 70, evs TEXT NOT NULL DEFAULT '{}',
  form TEXT NOT NULL DEFAULT '', nature TEXT NOT NULL DEFAULT 'Hardy');
CREATE TABLE IF NOT EXISTS dex(
  user_id INTEGER NOT NULL, species_id INTEGER NOT NULL, caught INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id, species_id));
CREATE INDEX IF NOT EXISTS idx_mons_owner ON mons(owner);
CREATE TABLE IF NOT EXISTS inventory(
  user_id INTEGER NOT NULL, item TEXT NOT NULL, qty INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(user_id, item));
CREATE TABLE IF NOT EXISTS badges(
  user_id INTEGER NOT NULL, region TEXT NOT NULL, idx INTEGER NOT NULL, earned REAL,
  PRIMARY KEY(user_id, region, idx));
CREATE TABLE IF NOT EXISTS league(
  user_id INTEGER NOT NULL, region TEXT NOT NULL, idx INTEGER NOT NULL, earned REAL,
  PRIMARY KEY(user_id, region, idx));
CREATE TABLE IF NOT EXISTS quests(user_id INTEGER NOT NULL, scope TEXT NOT NULL, slot INTEGER NOT NULL, period TEXT NOT NULL,
  qid TEXT NOT NULL, arg TEXT NOT NULL DEFAULT '', progress INTEGER NOT NULL DEFAULT 0, goal INTEGER NOT NULL, claimed INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(user_id, scope, slot));
CREATE TABLE IF NOT EXISTS milestones(user_id INTEGER NOT NULL, n INTEGER NOT NULL, PRIMARY KEY(user_id, n));
CREATE TABLE IF NOT EXISTS eggs(id INTEGER PRIMARY KEY AUTOINCREMENT, owner INTEGER NOT NULL, species_id INTEGER NOT NULL,
  origin TEXT NOT NULL, iv TEXT NOT NULL DEFAULT '{}', steps INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS banned(user_id INTEGER PRIMARY KEY, reason TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS safari(user_id INTEGER PRIMARY KEY, balls INTEGER NOT NULL, steps INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS league_run(
  user_id INTEGER PRIMARY KEY, region TEXT NOT NULL, mons TEXT NOT NULL, cleared INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS market(
  id INTEGER PRIMARY KEY AUTOINCREMENT, seller INTEGER NOT NULL, kind TEXT NOT NULL,
  mon_id INTEGER, item TEXT, qty INTEGER NOT NULL DEFAULT 1, price INTEGER NOT NULL, created REAL);
"""


class DB:
    def __init__(self, path: str = None):
        self.conn = sqlite3.connect(path or config.DB_PATH, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
            self._migrate()

    def backup(self, dest_dir, keep: int = 7, stamp: str = None) -> str:
        """Consistent snapshot of the whole save (safe while the bot runs); old snapshots beyond `keep` are deleted."""
        import datetime as _dt
        from pathlib import Path
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"pokebot-{stamp or _dt.datetime.utcnow().strftime('%Y%m%d-%H%M')}.sqlite3"
        with self.lock:
            out = sqlite3.connect(str(path))
            try:
                self.conn.backup(out)
            finally:
                out.close()
        for old in sorted(dest_dir.glob("pokebot-*.sqlite3"))[:-max(1, keep)]:
            old.unlink()
        return str(path)

    def _migrate(self):
        """Add columns introduced after the first release so existing save files keep working."""
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(players)")}
        for col, ddl in [("wins", "INTEGER NOT NULL DEFAULT 0"), ("losses", "INTEGER NOT NULL DEFAULT 0"),
                         ("pvp_wins", "INTEGER NOT NULL DEFAULT 0"), ("pvp_losses", "INTEGER NOT NULL DEFAULT 0"),
                         ("fav_mon", "INTEGER"), ("ev_stat", "TEXT"), ("ev_left", "INTEGER NOT NULL DEFAULT 0"),
                         ("gender", "TEXT"), ("trainer", "TEXT"), ("card_color", "INTEGER"),
                         ("streak", "INTEGER NOT NULL DEFAULT 0"), ("legend_pending", "INTEGER NOT NULL DEFAULT 0"),
                         ("event_claimed", "TEXT"), ("pvp_prize_day", "TEXT"), ("pvp_prize_n", "INTEGER NOT NULL DEFAULT 0")]:
            if col not in have:
                self.conn.execute(f"ALTER TABLE players ADD COLUMN {col} {ddl}")
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(mons)")}
        for col, ddl in [("shiny", "INTEGER NOT NULL DEFAULT 0"), ("friendship", "INTEGER NOT NULL DEFAULT 70"),
                         ("evs", "TEXT NOT NULL DEFAULT '{}'"), ("form", "TEXT NOT NULL DEFAULT ''"),
                         ("nature", "TEXT NOT NULL DEFAULT 'Hardy'"), ("item", "TEXT NOT NULL DEFAULT ''")]:
            if col not in have:
                self.conn.execute(f"ALTER TABLE mons ADD COLUMN {col} {ddl}")
        if "legs" not in {r["name"] for r in self.conn.execute("PRAGMA table_info(safari)")}:
            self.conn.execute("ALTER TABLE safari ADD COLUMN legs INTEGER NOT NULL DEFAULT 0")
        if "shiny" not in {r["name"] for r in self.conn.execute("PRAGMA table_info(dex)")}:
            self.conn.execute("ALTER TABLE dex ADD COLUMN shiny INTEGER NOT NULL DEFAULT 0")
        # everything already owned counts as caught in the Pokédex
        self.conn.execute("INSERT OR IGNORE INTO dex(user_id,species_id,caught) SELECT owner,species_id,1 FROM mons")
        self.conn.execute("UPDATE dex SET shiny=1 WHERE EXISTS (SELECT 1 FROM mons m WHERE m.owner=dex.user_id AND m.species_id=dex.species_id AND m.shiny=1)")

    # ---- plumbing
    def q(self, sql: str, args=()) -> List[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args=()) -> Optional[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchone()

    def x(self, sql: str, args=()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, args)

    @contextmanager
    def tx(self):
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

    # ---- players
    def player(self, uid: int) -> Optional[Dict[str, Any]]:
        r = self.one("SELECT * FROM players WHERE user_id=?", (uid,))
        return dict(r) if r else None

    def create_player(self, uid: int, name: str):
        self.x("INSERT OR IGNORE INTO players(user_id,name,region,money,created,card_color) VALUES(?,?,?,?,?,?)",
               (uid, name, "kanto", config.START_MONEY, time.time(), __import__("random").randrange(10)))

    def set_trainer(self, uid: int, gender: str, trainer: str):
        self.x("UPDATE players SET gender=?, trainer=? WHERE user_id=?", (gender, trainer, uid))

    def set_region(self, uid: int, region: str):
        self.x("UPDATE players SET region=? WHERE user_id=?", (region, uid))

    def set_name(self, uid: int, name: str):
        self.x("UPDATE players SET name=? WHERE user_id=?", (name, uid))

    def record_result(self, uid: int, win: bool, pvp: bool = False):
        col = ("pvp_" if pvp else "") + ("wins" if win else "losses")
        self.x(f"UPDATE players SET {col}={col}+1 WHERE user_id=?", (uid,))
        if pvp:   # friendly battles also count towards the overall record
            col = "wins" if win else "losses"
            self.x(f"UPDATE players SET {col}={col}+1 WHERE user_id=?", (uid,))

    def friendly_prize(self, uid: int, day: str, prize: int, cap: int) -> int:
        """Pays the friendly-battle win prize unless today's cap is used up; returns the amount paid."""
        p = self.player(uid)
        n = p["pvp_prize_n"] if p.get("pvp_prize_day") == day else 0
        if n >= cap:
            return 0
        self.x("UPDATE players SET pvp_prize_day=?, pvp_prize_n=?, money=money+? WHERE user_id=?", (day, n + 1, prize, uid))
        return prize

    def is_banned(self, uid: int) -> bool:
        return self.one("SELECT 1 AS x FROM banned WHERE user_id=?", (uid,)) is not None

    def ban(self, uid: int, reason: str = ""):
        self.x("INSERT OR REPLACE INTO banned(user_id,reason,ts) VALUES(?,?,?)", (uid, reason, time.time()))

    def unban(self, uid: int):
        self.x("DELETE FROM banned WHERE user_id=?", (uid,))

    def all_user_ids(self):
        return [r["user_id"] for r in self.q("SELECT user_id FROM players")]

    def set_fav(self, uid: int, mid: int):
        self.x("UPDATE players SET fav_mon=? WHERE user_id=?", (mid, uid))

    def favourite(self, uid: int):
        """The chosen favourite, else the player's highest-level Pokémon."""
        p = self.player(uid)
        if p and p.get("fav_mon"):
            m = self.mon(p["fav_mon"])
            if m and m["owner"] == uid and not m["listed"]:
                return m
        r = self.one("SELECT * FROM mons WHERE owner=? AND listed=0 ORDER BY level DESC, xp DESC LIMIT 1", (uid,))
        return self._mon(r) if r else None

    def money(self, uid: int) -> int:
        r = self.one("SELECT money FROM players WHERE user_id=?", (uid,))
        return r["money"] if r else 0

    def add_money(self, uid: int, amt: int):
        self.x("UPDATE players SET money=money+? WHERE user_id=?", (amt, uid))

    def spend(self, uid: int, amt: int) -> bool:
        """Atomically deduct money; False if insufficient."""
        with self.tx():
            if self.money(uid) < amt:
                return False
            self.conn.execute("UPDATE players SET money=money-? WHERE user_id=?", (amt, uid))
            return True

    def claim_daily(self, uid: int, today: str) -> bool:
        with self.tx():
            r = self.conn.execute("SELECT last_daily FROM players WHERE user_id=?", (uid,)).fetchone()
            if r is None or r["last_daily"] == today:
                return False
            self.conn.execute("UPDATE players SET last_daily=?, money=money+? WHERE user_id=?",
                              (today, config.DAILY_BONUS, uid))
            return True

    # ---- inventory
    def qty(self, uid: int, item: str) -> int:
        r = self.one("SELECT qty FROM inventory WHERE user_id=? AND item=?", (uid, item))
        return r["qty"] if r else 0

    def inventory(self, uid: int) -> Dict[str, int]:
        return {r["item"]: r["qty"] for r in self.q("SELECT item,qty FROM inventory WHERE user_id=? AND qty>0", (uid,))}

    def add_item(self, uid: int, item: str, n: int = 1):
        self.x("INSERT INTO inventory(user_id,item,qty) VALUES(?,?,?) "
               "ON CONFLICT(user_id,item) DO UPDATE SET qty=qty+?", (uid, item, n, n))

    def take_item(self, uid: int, item: str, n: int = 1) -> bool:
        with self.tx():
            r = self.conn.execute("SELECT qty FROM inventory WHERE user_id=? AND item=?", (uid, item)).fetchone()
            if not r or r["qty"] < n:
                return False
            self.conn.execute("UPDATE inventory SET qty=qty-? WHERE user_id=? AND item=?", (n, uid, item))
            return True

    # ---- badges
    def badge_count(self, uid: int, region: str) -> int:
        return self.one("SELECT COUNT(*) c FROM badges WHERE user_id=? AND region=?", (uid, region))["c"]

    def total_badges(self, uid: int) -> int:
        return self.one("SELECT COUNT(*) c FROM badges WHERE user_id=?", (uid,))["c"]

    def badges(self, uid: int) -> List[sqlite3.Row]:
        return self.q("SELECT region,idx FROM badges WHERE user_id=? ORDER BY region,idx", (uid,))

    def has_badge(self, uid: int, region: str, idx: int) -> bool:
        return self.one("SELECT 1 FROM badges WHERE user_id=? AND region=? AND idx=?", (uid, region, idx)) is not None

    def give_badge(self, uid: int, region: str, idx: int) -> bool:
        c = self.x("INSERT OR IGNORE INTO badges(user_id,region,idx,earned) VALUES(?,?,?,?)",
                   (uid, region, idx, time.time()))
        return c.rowcount > 0

    # ---- league (Elite Four idx 0-3, Champion idx 4)
    def league_count(self, uid: int, region: str) -> int:
        return self.one("SELECT COUNT(*) c FROM league WHERE user_id=? AND region=?", (uid, region))["c"]

    def give_league_seen(self, uid: int, region: str, idx: int) -> bool:
        return self.one("SELECT 1 FROM league WHERE user_id=? AND region=? AND idx=?", (uid, region, idx)) is not None

    def give_league(self, uid: int, region: str, idx: int) -> bool:
        c = self.x("INSERT OR IGNORE INTO league(user_id,region,idx,earned) VALUES(?,?,?,?)", (uid, region, idx, time.time()))
        return c.rowcount > 0

    # ---- safari zone
    def safari(self, uid: int) -> Optional[Dict[str, Any]]:
        r = self.one("SELECT * FROM safari WHERE user_id=?", (uid,))
        return dict(r) if r else None

    def enter_safari(self, uid: int, fee: int, balls: int, steps: int, legs: int = 0) -> bool:
        with self.tx():
            r = self.conn.execute("SELECT money FROM players WHERE user_id=?", (uid,)).fetchone()
            if not r or r["money"] < fee or self.conn.execute("SELECT 1 FROM safari WHERE user_id=?", (uid,)).fetchone():
                return False
            self.conn.execute("UPDATE players SET money=money-? WHERE user_id=?", (fee, uid))
            self.conn.execute("INSERT INTO safari(user_id,balls,steps,legs) VALUES(?,?,?,?)", (uid, balls, steps, legs))
            return True

    def set_safari(self, uid: int, balls: int, steps: int):
        self.x("UPDATE safari SET balls=?, steps=? WHERE user_id=?", (balls, steps, uid))

    def safari_legend_seen(self, uid: int):
        self.x("UPDATE safari SET legs=MAX(0, legs-1) WHERE user_id=?", (uid,))

    def leave_safari(self, uid: int):
        self.x("DELETE FROM safari WHERE user_id=?", (uid,))

    # ---- trading
    def swap_mons(self, a: int, a_mid: int, b: int, b_mid: int) -> bool:
        """Atomically swap two Pokémon between trainers (each arrives in a free party slot, else the box)."""
        with self.tx():
            ra = self.conn.execute("SELECT owner,listed FROM mons WHERE id=?", (a_mid,)).fetchone()
            rb = self.conn.execute("SELECT owner,listed FROM mons WHERE id=?", (b_mid,)).fetchone()
            if not ra or not rb or ra["owner"] != a or rb["owner"] != b or ra["listed"] or rb["listed"]:
                return False
            self.conn.execute("UPDATE mons SET owner=?, slot=NULL WHERE id=?", (b, a_mid))
            self.conn.execute("UPDATE mons SET owner=?, slot=NULL WHERE id=?", (a, b_mid))
            for uid, mid in ((b, a_mid), (a, b_mid)):
                used = {r["slot"] for r in self.conn.execute(
                    "SELECT slot FROM mons WHERE owner=? AND slot IS NOT NULL AND listed=0", (uid,))}
                slot = next((s for s in range(config.MAX_PARTY) if s not in used), None)
                self.conn.execute("UPDATE mons SET slot=? WHERE id=?", (slot, mid))
            self.conn.execute("UPDATE players SET fav_mon=NULL WHERE (user_id=? AND fav_mon=?) OR (user_id=? AND fav_mon=?)",
                              (a, a_mid, b, b_mid))
            return True

    def exchange(self, a: int, a_mid: Optional[int], a_money: int, b: int, b_mid: Optional[int], b_money: int) -> bool:
        """Atomic trade: each side gives a Pokémon and/or money. All-or-nothing."""
        with self.tx():
            for uid, mid, amt in ((a, a_mid, a_money), (b, b_mid, b_money)):
                if amt < 0 or self.conn.execute("SELECT money FROM players WHERE user_id=?", (uid,)).fetchone()["money"] < amt:
                    return False
                if mid:
                    r = self.conn.execute("SELECT owner,listed FROM mons WHERE id=?", (mid,)).fetchone()
                    if not r or r["owner"] != uid or r["listed"]:
                        return False
            self.conn.execute("UPDATE players SET money=money-?+? WHERE user_id=?", (a_money, b_money, a))
            self.conn.execute("UPDATE players SET money=money-?+? WHERE user_id=?", (b_money, a_money, b))
            for new_owner, mid in ((b, a_mid), (a, b_mid)):
                if not mid:
                    continue
                self.conn.execute("UPDATE mons SET owner=?, slot=NULL WHERE id=?", (new_owner, mid))
                used = {r["slot"] for r in self.conn.execute(
                    "SELECT slot FROM mons WHERE owner=? AND slot IS NOT NULL AND listed=0", (new_owner,))}
                slot = next((x for x in range(config.MAX_PARTY) if x not in used), None)
                self.conn.execute("UPDATE mons SET slot=? WHERE id=?", (slot, mid))
                self.conn.execute("UPDATE players SET fav_mon=NULL WHERE fav_mon=?", (mid,))
            return True

    def league_run(self, uid: int) -> Optional[Dict[str, Any]]:
        r = self.one("SELECT * FROM league_run WHERE user_id=?", (uid,))
        if not r:
            return None
        d = dict(r)
        d["mons"] = json.loads(d["mons"])
        return d

    def start_league_run(self, uid: int, region: str, mon_ids: List[int]):
        self.x("INSERT OR REPLACE INTO league_run(user_id,region,mons,cleared) VALUES(?,?,?,0)", (uid, region, json.dumps(mon_ids)))

    def advance_league_run(self, uid: int):
        self.x("UPDATE league_run SET cleared=cleared+1 WHERE user_id=?", (uid,))

    def end_league_run(self, uid: int):
        self.x("DELETE FROM league_run WHERE user_id=?", (uid,))

    def conquered(self, uid: int) -> List[str]:
        """Regions whose Champion you have beaten."""
        return [r["region"] for r in self.q("SELECT region FROM league WHERE user_id=? AND idx=4", (uid,))]

    # ---- mons
    @staticmethod
    def _mon(r) -> Dict[str, Any]:
        d = dict(r)
        d["iv"] = json.loads(d["iv"])
        d["moves"] = json.loads(d["moves"])
        d["evs"] = json.loads(d.get("evs") or "{}")
        return d

    def mon(self, mid: int) -> Optional[Dict[str, Any]]:
        r = self.one("SELECT * FROM mons WHERE id=?", (mid,))
        return self._mon(r) if r else None

    def party(self, uid: int) -> List[Dict[str, Any]]:
        return [self._mon(r) for r in self.q(
            "SELECT * FROM mons WHERE owner=? AND slot IS NOT NULL AND listed=0 ORDER BY slot", (uid,))]

    def box(self, uid: int) -> List[Dict[str, Any]]:
        return [self._mon(r) for r in self.q(
            "SELECT * FROM mons WHERE owner=? AND slot IS NULL AND listed=0 ORDER BY id", (uid,))]

    def count_mons(self, uid: int) -> int:
        return self.one("SELECT COUNT(*) c FROM mons WHERE owner=? AND listed=0", (uid,))["c"]

    def _free_slot(self, uid: int) -> Optional[int]:
        used = {r["slot"] for r in self.q("SELECT slot FROM mons WHERE owner=? AND slot IS NOT NULL AND listed=0", (uid,))}
        for s in range(config.MAX_PARTY):
            if s not in used:
                return s
        return None

    def add_mon(self, uid: int, rec: Dict[str, Any]) -> int:
        with self.tx():
            used = {r["slot"] for r in self.conn.execute(
                "SELECT slot FROM mons WHERE owner=? AND slot IS NOT NULL AND listed=0", (uid,))}
            slot = next((s for s in range(config.MAX_PARTY) if s not in used), None)
            c = self.conn.execute(
                "INSERT INTO mons(owner,species_id,species_name,nickname,level,xp,iv,moves,ability,tera_type,hp,slot,caught,"
                "shiny,friendship,evs,form,nature,item) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uid, rec["species_id"], rec["species_name"], rec.get("nickname"), rec["level"], rec["xp"],
                 json.dumps(rec["iv"]), json.dumps(rec["moves"]), rec["ability"], rec["tera_type"], rec["hp"],
                 slot, time.time(), int(bool(rec.get("shiny"))), rec.get("friendship", 70), json.dumps(rec.get("evs") or {}),
                 rec.get("form") or "", rec.get("nature") or "Hardy", rec.get("item") or ""))
            sh = int(bool(rec.get("shiny")))
            self.conn.execute("INSERT INTO dex(user_id,species_id,caught,shiny) VALUES(?,?,1,?) ON CONFLICT(user_id,species_id)"
                              " DO UPDATE SET caught=1, shiny=MAX(shiny, excluded.shiny)", (uid, rec["species_id"], sh))
            return c.lastrowid

    def update_mon(self, mid: int, **fields):
        if not fields:
            return
        cols = []
        vals = []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            vals.append(json.dumps(v) if k in ("iv", "moves", "evs") else v)
        self.x(f"UPDATE mons SET {','.join(cols)} WHERE id=?", (*vals, mid))

    # ---- pokédex / training state
    def dex_seen(self, uid: int, sid: int):
        self.x("INSERT OR IGNORE INTO dex(user_id,species_id,caught) VALUES(?,?,0)", (uid, sid))

    def dex_caught(self, uid: int, sid: int) -> bool:
        r = self.one("SELECT caught FROM dex WHERE user_id=? AND species_id=?", (uid, sid))
        return bool(r and r["caught"])

    def eggs(self, uid: int):
        return [dict(r, iv=json.loads(r["iv"])) for r in self.q("SELECT * FROM eggs WHERE owner=? ORDER BY id", (uid,))]

    def add_egg(self, uid: int, species_id: int, origin: str, iv: dict, steps: int) -> int:
        return self.x("INSERT INTO eggs(owner,species_id,origin,iv,steps) VALUES(?,?,?,?,?)",
                      (uid, species_id, origin, json.dumps(iv), steps)).lastrowid

    def del_egg(self, eid: int):
        self.x("DELETE FROM eggs WHERE id=?", (eid,))

    def dex_shiny_count(self, uid: int) -> int:
        return self.one("SELECT COUNT(*) AS c FROM dex WHERE user_id=? AND shiny=1", (uid,))["c"]

    def dex_count(self, uid: int) -> int:
        return self.one("SELECT COUNT(*) AS c FROM dex WHERE user_id=? AND caught=1", (uid,))["c"]

    def milestones(self, uid: int) -> set:
        return {r["n"] for r in self.q("SELECT n FROM milestones WHERE user_id=?", (uid,))}

    def claim_milestone(self, uid: int, n: int) -> bool:
        if n in self.milestones(uid):
            return False
        self.x("INSERT INTO milestones(user_id,n) VALUES(?,?)", (uid, n))
        return True

    def top(self, cat: str, limit: int = 10):
        sql = {
            "badges": "SELECT p.name, (SELECT COUNT(*) FROM badges b WHERE b.user_id=p.user_id) AS v FROM players p ORDER BY v DESC, p.money DESC",
            "dex": "SELECT p.name, (SELECT COUNT(*) FROM dex d WHERE d.user_id=p.user_id AND d.caught=1) AS v FROM players p ORDER BY v DESC",
            "money": "SELECT p.name, p.money AS v FROM players p ORDER BY v DESC",
            "wins": "SELECT p.name, p.wins AS v FROM players p ORDER BY v DESC",
            "pvp": "SELECT p.name, p.pvp_wins AS v FROM players p ORDER BY v DESC",
        }[cat]
        return self.q(sql + " LIMIT ?", (limit,))

    def dex_mark_caught(self, uid: int, sid: int):
        self.x("INSERT INTO dex(user_id,species_id,caught) VALUES(?,?,1) ON CONFLICT(user_id,species_id) DO UPDATE SET caught=1",
               (uid, sid))

    def start_training(self, uid: int, stat: str, cost: int, encounters: int) -> bool:
        with self.tx():
            r = self.conn.execute("SELECT money FROM players WHERE user_id=?", (uid,)).fetchone()
            if not r or r["money"] < cost:
                return False
            self.conn.execute("UPDATE players SET money=money-?, ev_stat=?, ev_left=? WHERE user_id=?",
                              (cost, stat, encounters, uid))
            return True

    def use_training_encounter(self, uid: int):
        self.x("UPDATE players SET ev_left=MAX(0, ev_left-1) WHERE user_id=?", (uid,))

    def set_friendship(self, mid: int, value: int):
        self.x("UPDATE mons SET friendship=? WHERE id=?", (max(0, min(255, value)), mid))

    def set_slot(self, mid: int, slot: Optional[int]):
        self.x("UPDATE mons SET slot=? WHERE id=?", (slot, mid))

    def move_to_party(self, uid: int, mid: int) -> bool:
        with self.tx():
            used = {r["slot"] for r in self.conn.execute(
                "SELECT slot FROM mons WHERE owner=? AND slot IS NOT NULL AND listed=0", (uid,))}
            slot = next((s for s in range(config.MAX_PARTY) if s not in used), None)
            if slot is None:
                return False
            self.conn.execute("UPDATE mons SET slot=? WHERE id=? AND owner=? AND listed=0", (slot, mid, uid))
            return True

    def move_to_box(self, uid: int, mid: int) -> bool:
        """Refuses to empty the party completely."""
        with self.tx():
            n = self.conn.execute("SELECT COUNT(*) c FROM mons WHERE owner=? AND slot IS NOT NULL AND listed=0",
                                  (uid,)).fetchone()["c"]
            if n <= 1:
                return False
            self.conn.execute("UPDATE mons SET slot=NULL WHERE id=? AND owner=? AND listed=0", (mid, uid))
            return True

    def swap_party(self, uid: int, a: int, b: int) -> bool:
        with self.tx():
            ra = self.conn.execute("SELECT slot FROM mons WHERE id=? AND owner=? AND slot IS NOT NULL", (a, uid)).fetchone()
            rb = self.conn.execute("SELECT slot FROM mons WHERE id=? AND owner=? AND slot IS NOT NULL", (b, uid)).fetchone()
            if not ra or not rb:
                return False
            self.conn.execute("UPDATE mons SET slot=? WHERE id=?", (rb["slot"], a))
            self.conn.execute("UPDATE mons SET slot=? WHERE id=?", (ra["slot"], b))
            return True

    def release_mon(self, uid: int, mid: int) -> bool:
        with self.tx():
            r = self.conn.execute("SELECT slot FROM mons WHERE id=? AND owner=? AND listed=0", (mid, uid)).fetchone()
            if not r:
                return False
            if r["slot"] is not None:
                n = self.conn.execute("SELECT COUNT(*) c FROM mons WHERE owner=? AND slot IS NOT NULL AND listed=0",
                                      (uid,)).fetchone()["c"]
                if n <= 1:
                    return False
            self.conn.execute("DELETE FROM mons WHERE id=?", (mid,))
            return True

    # ---- market
    def list_mon(self, uid: int, mid: int, price: int) -> Optional[int]:
        with self.tx():
            r = self.conn.execute("SELECT slot FROM mons WHERE id=? AND owner=? AND listed=0", (mid, uid)).fetchone()
            if not r:
                return None
            if r["slot"] is not None:
                n = self.conn.execute("SELECT COUNT(*) c FROM mons WHERE owner=? AND slot IS NOT NULL AND listed=0",
                                      (uid,)).fetchone()["c"]
                if n <= 1:
                    return None
            self.conn.execute("UPDATE mons SET listed=1, slot=NULL WHERE id=?", (mid,))
            c = self.conn.execute("INSERT INTO market(seller,kind,mon_id,qty,price,created) VALUES(?,?,?,?,?,?)",
                                  (uid, "mon", mid, 1, price, time.time()))
            return c.lastrowid

    def list_item(self, uid: int, item: str, qty: int, price: int) -> Optional[int]:
        with self.tx():
            r = self.conn.execute("SELECT qty FROM inventory WHERE user_id=? AND item=?", (uid, item)).fetchone()
            if not r or r["qty"] < qty:
                return None
            self.conn.execute("UPDATE inventory SET qty=qty-? WHERE user_id=? AND item=?", (qty, uid, item))
            c = self.conn.execute("INSERT INTO market(seller,kind,item,qty,price,created) VALUES(?,?,?,?,?,?)",
                                  (uid, "item", item, qty, price, time.time()))
            return c.lastrowid

    def listing(self, lid: int):
        r = self.one("SELECT * FROM market WHERE id=?", (lid,))
        return dict(r) if r else None

    def listings(self, offset: int = 0, limit: int = 8, seller: Optional[int] = None, kind: Optional[str] = None):
        where, args = [], []
        if seller is not None:
            where.append("seller=?")
            args.append(seller)
        if kind:
            where.append("kind=?")
            args.append(kind)
        w = ("WHERE " + " AND ".join(where)) if where else ""
        rows = self.q(f"SELECT * FROM market {w} ORDER BY id DESC LIMIT ? OFFSET ?", (*args, limit, offset))
        total = self.one(f"SELECT COUNT(*) c FROM market {w}", tuple(args))["c"]
        return [dict(r) for r in rows], total

    def cancel_listing(self, uid: int, lid: int) -> Optional[Dict[str, Any]]:
        with self.tx():
            r = self.conn.execute("SELECT * FROM market WHERE id=? AND seller=?", (lid, uid)).fetchone()
            if not r:
                return None
            self.conn.execute("DELETE FROM market WHERE id=?", (lid,))
            self._return_goods(r, uid)
            return dict(r)

    def _return_goods(self, r, to_uid: int):
        if r["kind"] == "mon":
            used = {x["slot"] for x in self.conn.execute(
                "SELECT slot FROM mons WHERE owner=? AND slot IS NOT NULL AND listed=0", (to_uid,))}
            slot = next((s for s in range(config.MAX_PARTY) if s not in used), None)
            self.conn.execute("UPDATE mons SET owner=?, listed=0, slot=? WHERE id=?", (to_uid, slot, r["mon_id"]))
        else:
            self.conn.execute("INSERT INTO inventory(user_id,item,qty) VALUES(?,?,?) "
                              "ON CONFLICT(user_id,item) DO UPDATE SET qty=qty+?", (to_uid, r["item"], r["qty"], r["qty"]))

    def buy_listing(self, buyer: int, lid: int):
        """Atomic purchase. Returns (ok, message, listing_dict)."""
        with self.tx():
            r = self.conn.execute("SELECT * FROM market WHERE id=?", (lid,)).fetchone()
            if not r:
                return False, "That listing no longer exists.", None
            if r["seller"] == buyer:
                return False, "You can't buy your own listing (use /cancel).", None
            bal = self.conn.execute("SELECT money FROM players WHERE user_id=?", (buyer,)).fetchone()
            if not bal or bal["money"] < r["price"]:
                return False, "Not enough money.", None
            fee = int(r["price"] * config.MARKET_FEE)
            self.conn.execute("UPDATE players SET money=money-? WHERE user_id=?", (r["price"], buyer))
            self.conn.execute("UPDATE players SET money=money+? WHERE user_id=?", (r["price"] - fee, r["seller"]))
            self.conn.execute("DELETE FROM market WHERE id=?", (lid,))
            self._return_goods(r, buyer)
            d = dict(r)
            d["fee"] = fee
            return True, "ok", d
