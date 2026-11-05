"""Group tournaments: join a pool, get random opponents, play your matches whenever you're ready (single elimination)."""
import random
from typing import Any, Dict, List, Optional

MAX_PLAYERS = 16
MIN_PLAYERS = 2
BASE_PRIZE = 500            # sponsored by the bot per player, on top of entry fees
CHAMPION_SHARE = 0.7


class Tourney:
    def __init__(self, tid: str, chat_id: int, host: int, fee: int, preset: str, rules: Dict[str, Any]):
        self.id, self.chat_id, self.host, self.fee, self.preset, self.rules = tid, chat_id, host, fee, preset, rules
        self.players: List[int] = []
        self.state = "lobby"                    # lobby | running | done | cancelled
        self.round = 0
        self.matches: List[Dict[str, Any]] = []
        self.byes: List[int] = []
        self.champion: Optional[int] = None
        self.runner_up: Optional[int] = None
        self.msg_id: Optional[int] = None       # the match board message

    @property
    def pool(self) -> int:
        return (self.fee + BASE_PRIZE) * len(self.players)

    def prizes(self) -> Dict[str, int]:
        champ = int(self.pool * CHAMPION_SHARE)
        return {"champion": champ, "runner_up": self.pool - champ}

    def add(self, uid: int) -> bool:
        if uid in self.players or len(self.players) >= MAX_PLAYERS or self.state != "lobby":
            return False
        self.players.append(uid)
        return True

    def remove(self, uid: int) -> bool:
        if uid not in self.players or self.state != "lobby":
            return False
        self.players.remove(uid)
        return True

    def start(self, rng: random.Random) -> bool:
        if self.state != "lobby" or len(self.players) < MIN_PLAYERS:
            return False
        self.state = "running"
        self._make_round(list(self.players), rng)
        return True

    def _make_round(self, alive: List[int], rng: random.Random):
        rng.shuffle(alive)
        self.round += 1
        self.byes = [alive.pop()] if len(alive) % 2 else []
        self.matches = [{"a": alive[i], "b": alive[i + 1], "w": None, "ready": set()} for i in range(0, len(alive), 2)]

    def ready(self, i: int, uid: int) -> str:
        """'wait' (marked ready), 'go' (both ready), or '' (not your match / already decided)."""
        m = self.matches[i]
        if m["w"] is not None or uid not in (m["a"], m["b"]):
            return ""
        m["ready"].add(uid)
        return "go" if len(m["ready"]) == 2 else "wait"

    def report(self, i: int, winner: int, rng: random.Random) -> str:
        """Record a result. Returns 'pending' | 'next' | 'done'."""
        m = self.matches[i]
        if m["w"] is not None or winner not in (m["a"], m["b"]):
            return "pending"
        m["w"] = winner
        if any(x["w"] is None for x in self.matches):
            return "pending"
        alive = [x["w"] for x in self.matches] + self.byes
        if len(alive) == 1:
            self.state, self.champion = "done", alive[0]
            self.runner_up = m["a"] if m["b"] == winner else m["b"]
            return "done"
        self._make_round(alive, rng)
        return "next"

    def round_name(self) -> str:
        n = len(self.matches) * 2 + len(self.byes)
        return {2: "FINAL", 4: "Semifinals", 8: "Quarterfinals"}.get(n, f"Round {self.round}")
