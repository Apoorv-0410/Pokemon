# Pokébot dev log

## 2026-11-04 (day 1)
- Showdown engine, 9 regions, gyms/League, gimmicks (Mega/Z/Dynamax/Tera), shop sections, EVs, evolutions, Pokédex, TMs, /form ($500), nature mints, ability capsule/patch.
- Travel: Kanto–Unova open; Kalos/Alola/Galar/Paldea unlock after conquering any region.
- League runs: team locked for the 5 fights, free full heal before each fight, a loss ends the run.

## 2026-11-05 (day 2)
- Pictures phase, step 1: /start asks Boy/Girl/Surprise me → trainer picker (Ash for boys) → region → starter. `/trainer` changes the look later.
- /card is now a picture (trainer left, favorite Pokémon right, money, balls, Pokémon, wins/losses, region, journey start).
- Badge case = region buttons under the card (🏆 conquered, "Kanto 3/8", 🔒 locked); tap → that region's 8 badges, ⬅️ Back.
- New: pokebot/images.py (Pillow), `Pillow` in requirements, 77 tests.
- Trainer art is downloaded on first use into data/trainers/<slug>.png (public Showdown sprite names). If a download fails a drawn avatar is used; drop your own PNG there to override.

## Next
- Step 2: battle scene pictures (HP bars, sprites, weather/status), Pokédex silhouettes.
- Friendly-battle rules menu / doubles / VGC, Safari Zone, trading, random trainer encounters, raids + redeem shards, creator powers, daily backup.
- Card: removed the trainer-name caption under the trainer picture (picture only).
- Random card color per player at start (10 colors, stored in players.card_color; old saves derive one from user id). Added Legends protagonists: Rei & Akari (Arceus), Z-A hero boy/girl (art filenames unverified; fallback avatar or own PNG in data/trainers).

## 2026-11-05 (pictures step 2)
- Battle scene picture for wild / gym / League fights: foe (top-right) and your Pokémon (bottom-left) over a sky/grass scene, HP bars, status pills (PAR/BRN/…), DMAX/TERA/MEGA tags, weather & terrain badges (rain/snow/sand scenes). Foe shows %, you show HP numbers.
- The picture is ONE message edited in place each turn (editMessageMedia); the log + buttons stay in the text message below. Final picture shows fainted Pokémon gone.
- Sprites: Gen 1–5 use Black/White pixel sprites (front/back); Gen 6+ use official artwork (your back view is the mirrored artwork). Forms/Mega sprites still show the base species.
- Friendly PvP stays text-only for now (needs a shared scene).
- Not yet: Pokédex silhouettes.
- Switched everything to HD official artwork (battle scenes, card, starter/wild sprites; shiny artwork too). Default POKEBOT_SPRITES=artwork.
- Battle pictures now switch to the exact form art (Mega X/Y, Primal, regional, etc.) via the PokeAPI form id; falls back to the base species if the form art can't be found.
- Battle art: your side uses real back sprites (all gens, incl. Mega/forms/shiny), foe uses HD front artwork; Mega art kept; Gigantamax art when Dynamaxed (visual only, no Gmax moves); Tera crystal aura + jewel in the type colour.
- Battle pictures: both sides now use the same sprite set (front for foe, back for you) so they match; HD artwork only as fallback. Card keeps HD artwork.
- Fixed duplicate HP lines in the battle log (Showdown sends a private + public copy of each damage line; now only one is shown).

## 2026-11-05 (friendly battle rules)
- /challenge now shows a rules menu (challenger only): presets 🎲 Casual / 🏅 Standard (all Lv50) + toggles for team size (1/3/6), level (own/50/100) and gimmicks on/off. The challenged player sees the rules and taps Accept.
- Rules are stored on the battle (`Session.rules`), level override applies to engine sets only (no effect on your saved Pokémon).
- Not yet: doubles / VGC preset (needs two-active-Pokémon battle UI + targeting).

## 2026-11-05 (Safari Zone + trading)
- /safari: $300 entry → 30 Safari Balls and 67 Pokémon to find. 🌿 Search spawns one (regional wild pool); ⚪ Ball (catch power ×3), 🍎 Bait (calmer, harder to catch), 🪨 Rock (easier to catch, flees more), 🏃 Run. Trip ends when balls or spawns run out; /explore, /gym etc. are blocked while inside. (pokebot/extras.py)
- /trade <your id> (reply to a trainer in a group) → they answer /offer <their id> → both tap ✅ Confirm → swap. Arrives in a free party slot, else box; Pokédex updated; trade evolutions fire on arrival (any Pokémon whose evolution kind is "trade"). Blocked during League runs/battles/Safari and for market-listed Pokémon.
- Not yet: held-item conditions for trade evolutions (they evolve without the item), Safari-only species.
- Safari simplified to Ball / Run only (catch power ×5, 15% flee chance per turn).

## 2026-11-05 (random trainers)
- /explore now has an 18% chance (not while EV training) of a passing trainer: 12 classes (Youngster, Lass, Hiker, ...), random name, intro line, 1–3 themed Pokémon a bit above wild level. No running; win = ~12× their total levels in money + trainer-bonus XP + their "I lost" line; loss = flat $25–30 + their victory line. (`TRAINER_CHANCE` in world.py)
- Abilities: handled by the Showdown engine; the log shows "[X's Intimidate]", stat falls, Static, immunities, etc.

## 2026-11-05 (doubles)
- Friendly battles can now be DOUBLES (two Pokémon per side). Rules menu: 🎲 Casual / 🏅 Standard / 🏆 VGC (doubles, 4 Pokémon, Lv50, gimmicks on) + 🎮 Singles/Doubles toggle.
- Each turn you choose for your first Pokémon, then your second: moves (with a target menu when a move can hit more than one Pokémon), switch (never the same bench Pokémon twice), one gimmick per turn. Fainted slots auto-pass; replacements are picked in turn.
- Locked moves (Outrage…) need no target. PvP only — wild/gym/trainer fights stay singles. No team preview (first N of your party are brought).

## 2026-11-05 (double battles for the late game)
- From the 5th gym on (gyms 5–8), every Gym Leader, Elite Four member and Champion brings 6 Pokémon and fights a DOUBLE battle. Random trainers do too once you have 4+ badges in the region. Wild, Safari and gyms 1–4 stay singles.
- You need at least 2 healthy Pokémon for a double battle (the League checks before locking your team).
- Battle pictures now show two Pokémon per side in doubles (compact HP boxes).
- AI uses its gimmick on only one slot per turn; Max Moves ask for a target.
- Tunable constants: world.DOUBLES_FROM_GYM, GYM_TEAM_SIZES, league_foe sizes.
- Random trainers (4+ badges): always 6 Pokémon, but only 50% fight in doubles (world.TRAINER_DOUBLES_CHANCE); gyms 5–8 / League stay always doubles.

## 2026-11-05 (quests, streak, starter kit)
- New players start with 30 Poké Balls, 15 Great Balls and 10 Ultra Balls.
- /quests: 3 daily quests (one easy $300+2 Poké Balls, one medium $800+Great Ball, one hard $1,500+Rare Candy or Ultra Ball) with claim buttons; all 3 → $1,000 + free EV Training Voucher (+10% Nature Mint). 1 weekly quest (catch 15 / win 5 gym-League fights / claim 7 dailies) → 3 Rare Candy, 10 Ultra Balls or a one-time Shiny Charm (≈1.5× shiny odds). Resets at 00:00 UTC. (pokebot/quests.py)
- /daily is now a login streak: $1,000 → $3,000 over a 7-day cycle; day 7 also gives a Rare Candy and a 1% Master Ball roll. A missed day resets the streak (no shield).
- Every 10th streak day: 20% roll → your next /explore is a LEGENDARY of your region (Lv gym-8+6, normal legendary catch rate); otherwise 5 Ultra Balls.
- Quest hooks: explore, wild/trainer/gym/League/doubles/PvP wins, catches (with type), evolve, buy, heal, Safari trips. Quests can never break a battle (errors are swallowed).

## 2026-11-05 (daily event Pokémon)
- /event shows today's event Pokémon: one random species from any region, no legendaries/mythicals, same for everyone, changes at 00:00 UTC.
- 12% of /explore encounters (world.EVENT_CHANCE) are the event Pokémon. Catching it the first time each day pays $800 (world.EVENT_BONUS); later catches that day pay nothing.
- Elite Four/Champion: losing to ANY member ends the run; /league starts again at member 1 (now covered by a test).
- Tests tweak: autouse fixture also zeroes EVENT_CHANCE. 102 tests.

## 2026-11-05 (leaderboards + Pokédex milestones)
- /top (or /leaderboard): top 10 with category buttons — Badges, Pokédex, Money, Wins, PvP. Medals for top 3.
- /milestones: caught-Pokédex milestones at 10/25/50/100/200/400/600 with titles and one-time rewards (world.DEX_MILESTONES: money + balls/candy, Master Ball at 600). Claim buttons appear when reached.
- 103 tests.

## 2026-11-05 (eggs & breeding)
- /breed <id> <id> ($500): two Pokémon of the same family, or any non-legendary + a Ditto, make an egg of the base form. No genders. The baby copies 3 random IVs from random parents (world.BREED_IVS); the rest are random. (Natures via Everstone etc. come with held items.)
- Eggs hatch after 10 /explore trips (world.EGG_STEPS) at Lv5 with friendship 120; max 6 eggs carried (EGG_CAP). /eggs shows progress.
- While exploring: 4% to find an egg of your region (EGG_FIND_CHANCE); 5% a wandering Egg Merchant offers a mystery egg from ANOTHER region for $1,500 (EGG_NPC_CHANCE / EGG_NPC_PRICE) instead of a battle.
- pokebot/eggs.py; eggs table; tests zero the random egg chances by default. 105 tests.

## 2026-11-05 (egg time + held items)
- Eggs now hatch after 70 /explore trips (world.EGG_STEPS).
- Held items: mons.item column; /give <item> <id>, /take <id>; shown in /mon and /bag; the item goes into the real Showdown battle (Leftovers, Choice items, Life Orb, Focus Sash, Assault Vest, Expert Belt, Rocky Helmet, Eviolite, Weakness Policy, Shell Bell, Sitrus/Lum Berry, 18 type boosters).
- Mega Stones (93, from pokebot/megastones.json) are sold in /shop → Mega Stones ($5,000, paged). Players now NEED the stone held on the right species (+ Mega Bracelet + Mega chosen in the lobby); AI trainers/gyms still get stones automatically (worker `megaAuto`). Z-Crystals are still automatic.
- Breeding items (consumed): Everstone passes the nature, Destiny Knot = 5 IVs instead of 3, Power Weight/Bracer/Belt/Lens/Band/Anklet fix that stat's IV. Sold in /shop → Held Items.
- Shop has 6 shelves now. 107 tests.

## 2026-11-05 (Z-Crystals + Pokédex complete)
- Mega Stones: all 93 in the engine's data are in the shop, including every Legends Z-A Mega (Absolite Z, Garchompite Z, Lucarionite Z, Clefablite, Starminite, Dragoninite, Chesnaughtite…). Rayquaza needs no stone in the games, so none is sold.
- Z-Crystals: 18 type crystals ($3,000) + 17 signature crystals ($8,000, species-locked) in /shop → Z-Crystals. Players now must hold one (+ Z-Ring + pick Z); AI still gets one free (worker `megaAuto:false` for players). pokebot/zcrystals.json.
- Pokédex: /dex = overview (per-region progress bars, caught/seen/shiny/% complete, next milestone). Entries get a picture button (full-colour once caught, black SILHOUETTE when only seen), "Native to" region, Mega forms, ✨ shiny-caught marker (also in the list). dex.shiny column (backfilled).
- Trainer card caption shows Pokédex count + title (Dex milestone titles).
- 110 tests.

## 2026-11-05 (tournaments + daily backup)
- /tournament [fee 0-5000] [casual|standard|vgc] in a group: lobby with Join/Leave, host Start/Cancel (fees refunded on leave/cancel). Single elimination, random draw, odd player gets a free pass; max 16 players.
- Each match has a Ready button; when both players tap it the friendly battle starts automatically with the tournament's rules. Results feed the bracket by themselves (draw = replay); host can award walkovers. Prize pool = (fee + $500 bot bonus) per player: 70% champion, 30% runner-up. pokebot/tournament.py; Game.tourney_results hook. Tournaments live in memory (a bot restart cancels running ones).
- Daily backup: Database.backup() (SQLite online snapshot) → data/backups/pokebot-YYYYMMDD-HHMM.sqlite3 at 04:00 UTC and on each start; keeps 7 (POKEBOT_BACKUP_KEEP). To restore, stop the bot and copy a snapshot over data/pokebot.sqlite3.
- 113 tests.

## 2026-11-05 (friendly prizes, trade money, creator powers)
- Friendly battles: winner +$900 (world.FRIENDLY_WIN_PRIZE), loser pays $200 (or whatever they have; FRIENDLY_LOSS). Only the first 5 rewarded wins per player per day pay out (FRIENDLY_DAILY_CAP) — beyond that nothing moves either way, so two friends can't farm money. Tournament matches don't pay this (the tournament has its own prizes).
- Trading with money: /trade <mon id | 0> [money] and the other player answers /offer <mon id | 0> [money]. Pokémon for money, Pokémon + cash for Pokémon, etc.; at least one Pokémon must be in the deal; balances re-checked at confirmation; changing an offer resets confirmations. DB.exchange() is atomic.
- Creator powers (only Telegram ids in ADMIN_IDS; hidden from /help; type /admin for the list): /admin_stats, /admin_who, /admin_money, /admin_item, /admin_mon (optional shiny), /admin_ban + /admin_unban (banned users are blocked from every command/button), /admin_announce, /admin_backup (snapshot DM'd to you).
- 116 tests.

## 2026-11-05 (sectioned /help + /info)
- /help is now a menu of 6 topic buttons (Adventure, Pokémon, Pokédex & eggs, Items & shop, Battles & social, Market) with a back button; admin commands stay hidden (/admin).
- New /info: Abilities (310, paged), Natures (all 25 with +/− stats), Held items (249, paged, 🛒 marks what's sold in /shop), Type chart. `/info <name>` looks up any ability, item or nature directly (suggests close matches). Data from the battle engine (pokebot/info.json).
- /mon now shows a one-line description under the ability.
- Raids dropped from the roadmap (Atharva isn't sure how to balance them).
- 117 tests.

## 2026-11-05 (friendly-battle pictures)
- Friendly battles now show ONE shared battle picture for the whole group (opening scene + the same picture edited after every turn), drawn like the wild ones (doubles, Mega/Tera/Dmax pills, weather). HP is shown as % for both sides so nobody's exact HP leaks; the player menus stay text. Trainer names no longer carry "(Legends: Arceus)"-style brackets.
- 119 tests.

## 2026-11-05 (friendly-battle fixes from Telegram test + endgame wild levels)
- Friendly battles were getting stuck on "Waiting for opponent…" and then erroring. Causes fixed: (1) screen updates were sent outside the battle lock, so two quick taps could paint an older screen over a newer one — now applied inside the lock; (2) a failed button-acknowledge (expired/flood-wait) threw before the move was registered — acknowledging is now best-effort; (3) Telegram flood control (a new scene message + picture every turn) — flood waits are now retried once and the message volume is far lower.
- Friendly battles now use ONE message: the battle picture with the scene + log as its caption, edited in place each turn (caption trimmed to Telegram's 1024 limit, newest log lines kept). Player move menus are edited in place too, so nothing pushes the buttons up the chat. 80 random full-length PvP battles (singles+doubles) also ran with no stuck/error states.
- Endgame wild levels: once you beat a region's Champion, that region's wild Pokémon (and random trainers / the daily event Pokémon) are Lv 60–85 (world.CONQUERED_WILD_LEVELS; +1 minimum per region index). Not applied to Safari.
- 121 tests.

## 2026-11-05 (from the terminal log of the first friendly-battle test)
- The log showed Telegram flood control ("Retry in 9–40 seconds") on almost every edit, plus timeouts and one ConnectError: edits that hit a flood wait used to be dropped, leaving screens stale ("stuck on Waiting for opponent"). Now every battle send/edit waits out flood control (up to 3 tries) or retries once after a timeout, and identical edits are skipped (SHOWN cache) so fewer calls are made. A first tap now costs 1 call, a finished turn 3 (two menus + the scene).
- Harmless errors no longer print a traceback or tell players "Something went wrong": "Message is not modified" (tapping the same challenge preset twice) and transient network errors/timeouts/flood waits.

## Travel gate
- You can't leave a region until you've conquered it (8 badges + Elite Four + Champion). Until then only regions you've already conquered are reachable. Kalos+ still unlock after conquering any region. Enforced in both /travel and the button callback.

## Save-data safety
- Saves/cache/backups now default to `~/PokebotData` (outside the bot folder), so re-extracting a new zip can't wipe them. An old `data/` folder inside the project is copied over once automatically if the new location has no save. Path is printed at startup ("SAVE DATA: ...").

## Trainer name
- /start now invites a trainer name (send it as the next message, max 12 Latin chars; skipping keeps the Telegram name). `/name <new name>` changes it any time. Shown on /card and in battles. 122 tests.

## Safari tuning
- A Pokémon that breaks free of a Safari Ball flees 35% of the time (legendaries 50%). Safari Ball power unchanged (x5).
- 5% chance a fleeing Pokémon drops Great/Ultra Balls or a Rare Candy; fleeing legendaries/mythicals have a 0.5% chance to drop a Master Ball.
- Every trip now contains exactly 3 legendary/mythical encounters at random points (region legendary, Lv above E4); `safari.legs` column tracks how many are still owed.

## Who's that Pokémon
- `/whosthat` posts a silhouette (any of #1-1025); first correct guess in the chat (typed, or `/guess <name>`) wins $20 and the colour reveal. 45s timer, one round per chat, 15 paid wins/player/day (resets on restart).

## Catch card + wild held items
- After every catch (battle or Safari) a button card appears: Nickname (type it as next message), Stats (base stats, IVs, EVs, nature, held item, ability, moves), Add to party / Send to box, Release (with confirm), Keep it. Same buttons under `/mon <id>`. Blocked during League runs.
- 8% of wild Pokémon hold a random held item (not Mega Stones / Z-Crystals); you keep it when you catch them.
