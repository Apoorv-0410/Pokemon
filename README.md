# Pokébot – a Pokémon RPG for Telegram

All 9 regions (Kanto → Paldea), wild encounters, catching, gym battles, abilities, Mega / Z-Move / Dynamax /
Terastallization, friendly PvP battles, an economy, and a player-to-player market. Pokémon data comes live from
[PokeAPI](https://pokeapi.co) (cached on disk); battle and Pokédex pictures use official artwork / sprites hot-linked from PokeAPI.

## Setup

0. Install [Node.js](https://nodejs.org) 18+ (free) and run `cd engine && npm install` once — battles run on the real Pokémon Showdown simulator.

1. Create a bot with [@BotFather](https://t.me/BotFather) (`/newbot`) and copy the token.
2. ```bash
   cd pokebot
   python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   cp .env.example .env                                    # then paste your BOT_TOKEN
   python -m pokebot.prefetch kanto                        # optional: warm the cache (omit region for all)
   python -m pokebot
   ```
3. Message your bot `/start`. To use PvP in a group, add the bot to the group .

Python 3.10+ is recommended. The first encounter of each new species fetches data from PokeAPI, so it can take a
second or two; after that it is cached under `data/cache`. Run the prefetch script to avoid that.

## How the game works

| Feature | Details |
|---|---|
| Regions | Kanto → Paldea. Kanto, Johto, Hoenn, Sinnoh and Unova are open from the start (you begin in the one whose starter you pick). Kalos, Alola, Galar and Paldea open once you **conquer any region**: 8 gym badges, then `/league` (4 Elite Four fights + the Champion). |
| Gyms | 8 per region, fought in order (`/gym`). Teams are generated from the leader's type; the last Pokémon is the ace (+2 levels). Alola uses the 8 Island Trials. In Kalos/Alola/Galar/Paldea the 3rd leader gives you that region's gimmick item for free and, from then on, leaders and the league use the gimmick. |
| Wild | `/explore` – species come from the region's dex (no legendaries); levels scale with your badges in that region. |
| Catching | Gen 3/4-style formula with each species' real capture rate. Lower HP and status (sleep/freeze best) help. 11 balls in `/shop`: Poké, Great, Ultra and the situational Quick, Timer, Repeat, Nest, Net, Dusk, Level, Fast (Master Ball is a rare prize). A ". . ." suspense plays before the result. |
| XP & growth | Exp. Share: the whole healthy team gets full XP. New moves on level-up. Evolution: by level, by **stone** (`/evolve`, stones $3,000 in `/shop`; location/other conditions became stone evolutions), or by **friendship** (grows with wins together; 220+). Trade evolutions wait for the trade feature. Rare Candy ($15,000, `/use`). Wild and gym Pokémon respect evolution stages. |
| Pokédex | `/dex`: ❓ unmet → 👁 seen (name) → ✅ caught (full entry: type, size, abilities, weaknesses, evolution line, description, learnable moves). Unmet evolution stages stay hidden. Open one with `/dex <number or name>`. |
| Moves | `/moves <id>`: relearning and tutor moves are free; TMs cost $5–7k once and are then reusable on any Pokémon that can learn them. |
| Forms | Regional forms (Alolan, Galarian, Hisuian, Paldean) spawn in their home regions. `/form <id>` changes Arceus-style forms for $500, as often as you like (not for regional, Dynamax or Gmax forms). Nature Mint ($6,000, `/use`) changes a nature (natures now exist: random on every new Pokémon, +10%/−10% stats). Ability Capsule ($8,000) swaps regular abilities and Ability Patch ($15,000) unlocks the Hidden Ability (`/use`). |
| Shinies & EVs | Shiny odds 1/4096 (✨). EVs (252/stat, 510 total) come from defeated Pokémon; `/train` ($200) gives 30 encounters with Pokémon that yield the stat you pick. |
| Gimmicks | Buy a key item (Mega Bracelet, Z-Ring, Dynamax Band, Tera Orb). If you own several you lock in **one** before each battle; its button then appears in the fight (Mega stones / Z-Crystals are assigned automatically). Gym leaders in Kalos/Alola/Galar/Paldea use the matching gimmick on their ace. |
| Economy | Money from battles and `/daily`; spend at the Poké Mart. Pokémon Centers (`/heal`) are free, so there are no healing items. |
| Trainer card | `/card`: name, region, money, battle record (overall + friendly), favorite Pokémon (`/fav <id>`), badges. |
| Market | List Pokémon or items for sale (`/sellmon`, `/sellitem`); a 5% fee is taken from sales. Purchases are atomic. |
| Friendly battles | Reply to a player's message in a group with `/challenge`. Full-HP copies of both parties, no stakes. Everything happens in the group; each player taps their own buttons and nobody sees the choice until the turn plays out. |

`/explore`, `/hunt` and `/find` all start a wild encounter. Run `/help` in the bot for all commands.

## Simplifications (worth knowing)

- **Engine**: Pokémon Showdown's simulator (`@pkmn/sim`) — essentially every move and ability works. Dynamax can be used in gen-9 rules (patched). Singles and doubles (gyms 5-8, League, many trainers and VGC friendlies are doubles).
- **Exp. Share**: every healthy party member gets the full XP of a won battle.
- Heavy Ball is skipped (needs weight data). Raids were dropped from the plan.
- Tournaments, trades and Safari trips live in memory (a restart cancels them); saves are in SQLite with a daily backup in data/backups.
- Admin commands (/admin) only work for the Telegram ids in ADMIN_IDS.

## Development

```bash
pytest -q          # 125 tests, fully offline (uses a deterministic fake dex)
POKEBOT_PROVIDER=mock python -m pokebot   # run the bot without network access to PokeAPI
```

Layout: `showdown.py` + `engine/worker.js` (battle engine bridge) · `battle.py` (legacy helpers: stats, catch maths) · `game.py` (records, wild/gym generation, XP) · `session.py` (battle flow, UI-agnostic)
· `bot.py` (Telegram handlers) · `db.py` (SQLite) · `data.py` (PokeAPI + mock) · `world.py` (regions, gyms, items).

Pokémon and all related names are trademarks of Nintendo / Game Freak / The Pokémon Company. This is a fan project;
sprites are hot-linked from the PokeAPI sprites repository and not redistributed here.
