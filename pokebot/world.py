"""Static game world: regions, gym leaders, items, wild-area flavour."""
import json
import os
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class Gym:
    leader: str
    type: str
    badge: str
    town: str


@dataclass(frozen=True)
class Region:
    key: str
    name: str
    dex_lo: int
    dex_hi: int
    starters: tuple
    gyms: tuple          # 8 Gym entries (Alola uses Island Trials)
    areas: tuple         # flavour names for wild areas
    league: str = "Pokémon League"


G = Gym
REGIONS: List[Region] = [
    Region("kanto", "Kanto", 1, 151, (1, 4, 7), (
        G("Brock", "rock", "Boulder Badge", "Pewter City"), G("Misty", "water", "Cascade Badge", "Cerulean City"),
        G("Lt. Surge", "electric", "Thunder Badge", "Vermilion City"), G("Erika", "grass", "Rainbow Badge", "Celadon City"),
        G("Koga", "poison", "Soul Badge", "Fuchsia City"), G("Sabrina", "psychic", "Marsh Badge", "Saffron City"),
        G("Blaine", "fire", "Volcano Badge", "Cinnabar Island"), G("Giovanni", "ground", "Earth Badge", "Viridian City")),
        ("Route 1", "Viridian Forest", "Mt. Moon", "Route 24", "Rock Tunnel", "Safari Zone", "Seafoam Islands")),
    Region("johto", "Johto", 152, 251, (152, 155, 158), (
        G("Falkner", "flying", "Zephyr Badge", "Violet City"), G("Bugsy", "bug", "Hive Badge", "Azalea Town"),
        G("Whitney", "normal", "Plain Badge", "Goldenrod City"), G("Morty", "ghost", "Fog Badge", "Ecruteak City"),
        G("Chuck", "fighting", "Storm Badge", "Cianwood City"), G("Jasmine", "steel", "Mineral Badge", "Olivine City"),
        G("Pryce", "ice", "Glacier Badge", "Mahogany Town"), G("Clair", "dragon", "Rising Badge", "Blackthorn City")),
        ("Route 29", "Ilex Forest", "Union Cave", "Ruins of Alph", "Lake of Rage", "Ice Path")),
    Region("hoenn", "Hoenn", 252, 386, (252, 255, 258), (
        G("Roxanne", "rock", "Stone Badge", "Rustboro City"), G("Brawly", "fighting", "Knuckle Badge", "Dewford Town"),
        G("Wattson", "electric", "Dynamo Badge", "Mauville City"), G("Flannery", "fire", "Heat Badge", "Lavaridge Town"),
        G("Norman", "normal", "Balance Badge", "Petalburg City"), G("Winona", "flying", "Feather Badge", "Fortree City"),
        G("Tate & Liza", "psychic", "Mind Badge", "Mossdeep City"), G("Wallace", "water", "Rain Badge", "Sootopolis City")),
        ("Route 101", "Petalburg Woods", "Granite Cave", "Route 119", "Meteor Falls", "Safari Zone")),
    Region("sinnoh", "Sinnoh", 387, 493, (387, 390, 393), (
        G("Roark", "rock", "Coal Badge", "Oreburgh City"), G("Gardenia", "grass", "Forest Badge", "Eterna City"),
        G("Maylene", "fighting", "Cobble Badge", "Veilstone City"), G("Crasher Wake", "water", "Fen Badge", "Pastoria City"),
        G("Fantina", "ghost", "Relic Badge", "Hearthome City"), G("Byron", "steel", "Mine Badge", "Canalave City"),
        G("Candice", "ice", "Icicle Badge", "Snowpoint City"), G("Volkner", "electric", "Beacon Badge", "Sunyshore City")),
        ("Route 201", "Eterna Forest", "Mt. Coronet", "Great Marsh", "Lost Tower", "Route 217")),
    Region("unova", "Unova", 494, 649, (495, 498, 501), (
        G("Cheren", "normal", "Basic Badge", "Aspertia City"), G("Roxie", "poison", "Toxic Badge", "Virbank City"),
        G("Burgh", "bug", "Insect Badge", "Castelia City"), G("Elesa", "electric", "Bolt Badge", "Nimbasa City"),
        G("Clay", "ground", "Quake Badge", "Driftveil City"), G("Skyla", "flying", "Jet Badge", "Mistralton City"),
        G("Brycen", "ice", "Freeze Badge", "Icirrus City"), G("Drayden", "dragon", "Legend Badge", "Opelucid City")),
        ("Route 1", "Pinwheel Forest", "Desert Resort", "Chargestone Cave", "Twist Mountain", "Giant Chasm")),
    Region("kalos", "Kalos", 650, 721, (650, 653, 656), (
        G("Viola", "bug", "Bug Badge", "Santalune City"), G("Grant", "rock", "Cliff Badge", "Cyllage City"),
        G("Korrina", "fighting", "Rumble Badge", "Shalour City"), G("Ramos", "grass", "Plant Badge", "Coumarine City"),
        G("Clemont", "electric", "Voltage Badge", "Lumiose City"), G("Valerie", "fairy", "Fairy Badge", "Laverre City"),
        G("Olympia", "psychic", "Psychic Badge", "Anistar City"), G("Wulfric", "ice", "Iceberg Badge", "Snowbelle City")),
        ("Route 2", "Santalune Forest", "Glittering Cave", "Frost Cavern", "Route 14", "Terminus Cave")),
    Region("alola", "Alola", 722, 809, (722, 725, 728), (
        G("Ilima", "normal", "Normalium Z Trial", "Verdant Cavern"), G("Lana", "water", "Waterium Z Trial", "Brooklet Hill"),
        G("Kiawe", "fire", "Firium Z Trial", "Wela Volcano Park"), G("Mallow", "grass", "Grassium Z Trial", "Lush Jungle"),
        G("Sophocles", "electric", "Electrium Z Trial", "Hokulani Observatory"), G("Acerola", "ghost", "Ghostium Z Trial", "Thrifty Megamart"),
        G("Mina", "fairy", "Fairium Z Trial", "Seafolk Village"), G("Hapu", "ground", "Groundium Z Trial", "Mount Lanakila")),
        ("Route 1", "Melemele Meadow", "Ten Carat Hill", "Poni Wilds", "Vast Poni Canyon")),
    Region("galar", "Galar", 810, 905, (810, 813, 816), (
        G("Milo", "grass", "Grass Badge", "Turffield"), G("Nessa", "water", "Water Badge", "Hulbury"),
        G("Kabu", "fire", "Fire Badge", "Motostoke"), G("Bea", "fighting", "Fighting Badge", "Stow-on-Side"),
        G("Opal", "fairy", "Fairy Badge", "Ballonlea"), G("Gordie", "rock", "Rock Badge", "Circhester"),
        G("Piers", "dark", "Dark Badge", "Spikemuth"), G("Raihan", "dragon", "Dragon Badge", "Hammerlocke")),
        ("Route 1", "Slumbering Weald", "Wild Area", "Galar Mine", "Glimwood Tangle", "Route 9")),
    Region("paldea", "Paldea", 906, 1025, (906, 909, 912), (
        G("Katy", "bug", "Bug Badge", "Cortondo"), G("Brassius", "grass", "Grass Badge", "Artazon"),
        G("Iono", "electric", "Electric Badge", "Levincia"), G("Kofu", "water", "Water Badge", "Cascarrafa"),
        G("Larry", "normal", "Normal Badge", "Medali"), G("Ryme", "ghost", "Ghost Badge", "Montenevera"),
        G("Tulip", "psychic", "Psychic Badge", "Alfornada"), G("Grusha", "ice", "Ice Badge", "Glaseado Mountain")),
        ("South Province", "Los Platos", "Asado Desert", "Casseroya Lake", "Glaseado Mountain")),
]
REGION_BY_KEY = {r.key: r for r in REGIONS}
GYM_TEAM_SIZES = [2, 2, 3, 3, 6, 6, 6, 6]
DOUBLES_FROM_GYM = 4          # from the 5th gym on, trainers bring 6 Pokémon and fight DOUBLE battles (League included)
GIMMICK_FROM_GYM = 2      # in Kalos/Alola/Galar/Paldea the 3rd leader hands over the gimmick item and uses it from then on

# Pokémon League: four Elite Four members + a Champion per region. (name, type) and (champion, types).
LEAGUE = {
    "kanto": ((("Lorelei", "ice"), ("Bruno", "fighting"), ("Agatha", "ghost"), ("Lance", "dragon")),
              ("Blue", ("water", "fire", "grass", "psychic", "ground", "flying"))),
    "johto": ((("Will", "psychic"), ("Koga", "poison"), ("Bruno", "fighting"), ("Karen", "dark")),
              ("Lance", ("dragon", "flying"))),
    "hoenn": ((("Sidney", "dark"), ("Phoebe", "ghost"), ("Glacia", "ice"), ("Drake", "dragon")),
              ("Steven", ("steel", "rock", "ground", "psychic", "water"))),
    "sinnoh": ((("Aaron", "bug"), ("Bertha", "ground"), ("Flint", "fire"), ("Lucian", "psychic")),
               ("Cynthia", ("ghost", "ground", "dragon", "fighting", "water", "psychic"))),
    "unova": ((("Shauntal", "ghost"), ("Marshal", "fighting"), ("Grimsley", "dark"), ("Caitlin", "psychic")),
              ("Alder", ("bug", "grass", "fire", "normal", "flying"))),
    "kalos": ((("Malva", "fire"), ("Siebold", "water"), ("Wikstrom", "steel"), ("Drasna", "dragon")),
              ("Diantha", ("fairy", "psychic", "fighting", "normal", "dragon"))),
    "alola": ((("Hala", "fighting"), ("Olivia", "rock"), ("Acerola", "ghost"), ("Kahili", "flying")),
              ("Kukui", ("normal", "fire", "water", "fighting", "flying", "grass"))),
    "galar": ((("Marnie", "dark"), ("Bede", "fairy"), ("Hop", "normal"), ("Raihan", "dragon")),
              ("Leon", ("fire", "dragon", "fighting", "steel", "water", "dark"))),
    "paldea": ((("Rika", "ground"), ("Poppy", "steel"), ("Larry", "flying"), ("Hassel", "dragon")),
               ("Geeta", ("ground", "grass", "flying", "dragon", "fighting", "ice"))),
}
LEAGUE_TITLE = {"alola": "Island Elite", "galar": "Champion Cup"}
LEAGUE_SIZE = 5            # five fights: Elite Four x4, then the Champion (index 4)
STARTER_REGIONS = 5        # Kanto..Unova are open from the start; Kalos, Alola, Galar, Paldea open after one conquest
BADGES_TO_UNLOCK_NEXT = 4       # badges needed in a region to unlock the next one


def region_index(key: str) -> int:
    return [r.key for r in REGIONS].index(key)


def next_region(key: str) -> Optional[Region]:
    i = region_index(key)
    return REGIONS[i + 1] if i + 1 < len(REGIONS) else None


def league_foe(region_key: str, idx: int):
    """(display name, types tuple, title, team size, level) for league fight idx (0-3 Elite Four, 4 Champion)."""
    e4, (champ, ctypes) = LEAGUE[region_key]
    base = gym_level(region_key, 7)
    if idx < 4:
        name, t = e4[idx]
        return name, (t,), LEAGUE_TITLE.get(region_key, "Elite Four"), 6, base + 4 + idx * 2
    return champ, tuple(ctypes), "Champion", 6, base + 12


def gym_level(region_key: str, gym_idx: int) -> int:
    return 12 + gym_idx * 6 + region_index(region_key) * 3


CONQUERED_WILD_LEVELS = (60, 85)      # once you've beaten a region's League, its wild Pokémon are endgame-strong


def wild_level_range(region_key: str, badges_in_region: int, conquered: bool = False):
    if conquered:
        return CONQUERED_WILD_LEVELS[0] + region_index(region_key), CONQUERED_WILD_LEVELS[1]
    lo = 3 + badges_in_region * 6 + region_index(region_key) * 2
    return lo, lo + 4


# --------------------------------------------------------------------------- items
@dataclass(frozen=True)
class Item:
    key: str
    name: str
    price: int
    kind: str          # ball | stone | candy | key
    value: float = 0   # ball multiplier / heal amount (0 = full)
    desc: str = ""


_BALLS = [
    ("poke-ball", "Poké Ball", 100, 1.0, "Basic ball."),
    ("great-ball", "Great Ball", 300, 1.5, "Better catch rate."),
    ("ultra-ball", "Ultra Ball", 600, 2.0, "High catch rate."),
    ("quick-ball", "Quick Ball", 800, 1.0, "x5 on the very first turn."),
    ("timer-ball", "Timer Ball", 800, 1.0, "Gets better the longer the battle lasts (up to x4)."),
    ("repeat-ball", "Repeat Ball", 800, 1.0, "x3.5 on a species you've caught before."),
    ("nest-ball", "Nest Ball", 800, 1.0, "Great against low-level Pokémon."),
    ("net-ball", "Net Ball", 800, 1.0, "x3.5 on Water and Bug types."),
    ("dusk-ball", "Dusk Ball", 800, 1.0, "x3 at night or in caves."),
    ("level-ball", "Level Ball", 800, 1.0, "Better when your Pokémon outlevels the target."),
    ("fast-ball", "Fast Ball", 800, 1.0, "x4 on very fast Pokémon."),
    ("master-ball", "Master Ball", 0, 255.0, "Never fails. Not sold - a rare prize."),
]
_STONES = [("fire-stone", "Fire Stone"), ("water-stone", "Water Stone"), ("thunder-stone", "Thunder Stone"),
           ("leaf-stone", "Leaf Stone"), ("ice-stone", "Ice Stone"), ("moon-stone", "Moon Stone"),
           ("sun-stone", "Sun Stone"), ("shiny-stone", "Shiny Stone"), ("dusk-stone", "Dusk Stone"),
           ("dawn-stone", "Dawn Stone")]
STONE_PRICE = 3000
RARE_CANDY_PRICE = 15000
ITEMS = {i.key: i for i in [
    *[Item(k, n, p, "ball", v, d) for k, n, p, v, d in _BALLS],
    *[Item(k, n, STONE_PRICE, "stone", 0, "Evolves certain Pokémon (/evolve).") for k, n in _STONES],
    Item("rare-candy", "Rare Candy", RARE_CANDY_PRICE, "candy", 0, "Raises a Pokémon by one level (/use)."),
    Item("nature-mint", "Nature Mint", 6000, "candy", 0, "Changes a Pokémon's nature (/use)."),
    Item("ability-capsule", "Ability Capsule", 8000, "candy", 0, "Switches between a Pokémon's regular abilities (/use)."),
    Item("ability-patch", "Ability Patch", 15000, "candy", 0, "Unlocks a Pokémon's Hidden Ability (/use)."),
    Item("ev-voucher", "EV Training Voucher", 0, "key", 0, "A free EV training session (used automatically at /train)."),
    Item("shiny-charm", "Shiny Charm", 0, "key", 0, "Makes shiny Pokémon a little more likely."),
    Item("mega-bracelet", "Mega Bracelet", 25000, "key", 0, "Unlocks Mega Evolution in battle."),
    Item("z-ring", "Z-Ring", 25000, "key", 0, "Unlocks Z-Moves in battle."),
    Item("dynamax-band", "Dynamax Band", 25000, "key", 0, "Unlocks Dynamax in battle."),
    Item("tera-orb", "Tera Orb", 25000, "key", 0, "Unlocks Terastallization in battle."),
]}
# Held items (/give, /take). name = the Pokémon Showdown item name, so battle effects are the real ones.
_HELD = [
    ("leftovers", "Leftovers", 6000, "Restores a little HP every turn."),
    ("choice-band", "Choice Band", 4000, "Boosts Attack but locks you into one move."),
    ("choice-specs", "Choice Specs", 4000, "Boosts Sp. Atk but locks you into one move."),
    ("choice-scarf", "Choice Scarf", 4000, "Boosts Speed but locks you into one move."),
    ("life-orb", "Life Orb", 5000, "Boosts damage, costs a little HP per attack."),
    ("focus-sash", "Focus Sash", 3000, "Survives one KO hit from full HP (once)."),
    ("assault-vest", "Assault Vest", 4000, "Boosts Sp. Def but only attacking moves."),
    ("expert-belt", "Expert Belt", 3000, "Boosts super-effective hits."),
    ("rocky-helmet", "Rocky Helmet", 3000, "Hurts attackers that make contact."),
    ("eviolite", "Eviolite", 4000, "Boosts Def/Sp. Def of Pokémon that can still evolve."),
    ("weakness-policy", "Weakness Policy", 3000, "Sharply raises Atk/SpA when hit super-effectively."),
    ("shell-bell", "Shell Bell", 3000, "Restores HP from damage dealt."),
    ("sitrus-berry", "Sitrus Berry", 800, "Heals when HP gets low (once)."),
    ("lum-berry", "Lum Berry", 1200, "Cures a status condition (once)."),
    ("everstone", "Everstone", 2000, "Held by a breeding parent: its nature passes to the egg. (Consumed.)"),
    ("destiny-knot", "Destiny Knot", 5000, "Held by a breeding parent: eggs inherit 5 IVs instead of 3. (Consumed.)"),
    *[(f"power-{k}", n, 3000, f"Held by a breeding parent: the egg inherits its {st} IV. (Consumed.)")
      for k, n, st in [("weight", "Power Weight", "HP"), ("bracer", "Power Bracer", "Attack"), ("belt", "Power Belt", "Defense"),
                       ("lens", "Power Lens", "Sp. Atk"), ("band", "Power Band", "Sp. Def"), ("anklet", "Power Anklet", "Speed")]],
    *[(k, n, 1500, f"Boosts {t}-type moves.") for k, n, t in [
        ("charcoal", "Charcoal", "Fire"), ("mystic-water", "Mystic Water", "Water"), ("magnet", "Magnet", "Electric"),
        ("miracle-seed", "Miracle Seed", "Grass"), ("never-melt-ice", "Never-Melt Ice", "Ice"), ("black-belt", "Black Belt", "Fighting"),
        ("poison-barb", "Poison Barb", "Poison"), ("soft-sand", "Soft Sand", "Ground"), ("sharp-beak", "Sharp Beak", "Flying"),
        ("twisted-spoon", "Twisted Spoon", "Psychic"), ("silver-powder", "Silver Powder", "Bug"), ("hard-stone", "Hard Stone", "Rock"),
        ("spell-tag", "Spell Tag", "Ghost"), ("dragon-fang", "Dragon Fang", "Dragon"), ("black-glasses", "Black Glasses", "Dark"),
        ("metal-coat", "Metal Coat", "Steel"), ("silk-scarf", "Silk Scarf", "Normal"), ("fairy-feather", "Fairy Feather", "Fairy")]],
]
MEGA_STONE_PRICE = 5000
# [key, name, base species, mega form slug] for every Mega Stone (generated from the battle engine's data)
MEGA_STONES = json.load(open(os.path.join(os.path.dirname(__file__), "megastones.json")))
MEGA_STONE_FOR = {k: (base, form) for k, _, base, form in MEGA_STONES}
ITEMS.update({k: Item(k, n, p, "held", 0, d) for k, n, p, d in _HELD})
WILD_ITEM_CHANCE = 0.08          # wild Pokémon holding an item (you keep it when you catch them)
ITEMS.update({k: Item(k, n, MEGA_STONE_PRICE, "mega", 0, f"Lets {base} Mega Evolve (hold it + own a Mega Bracelet).")
              for k, n, base, _ in MEGA_STONES})
Z_CRYSTAL_PRICE, Z_SIGNATURE_PRICE = 3000, 8000
# [key, name, "type"|"sig", type or species list ("A|B"), signature move]
Z_CRYSTALS = json.load(open(os.path.join(os.path.dirname(__file__), "zcrystals.json")))
Z_CRYSTAL_FOR = {k: (kind, who, move) for k, _, kind, who, move in Z_CRYSTALS}
ITEMS.update({k: Item(k, n, Z_CRYSTAL_PRICE if kind == "type" else Z_SIGNATURE_PRICE, "zcrystal", 0,
                      (f"Lets a holder turn a {who}-type move into a Z-Move (hold it + own a Z-Ring)." if kind == "type"
                       else f"Signature crystal: {who.replace('|', '/')} turns {move} into a special Z-Move."))
              for k, n, kind, who, move in Z_CRYSTALS})
HELD_KINDS = ("held", "mega", "zcrystal")
BALL_KEYS = [k for k, i in ITEMS.items() if i.kind == "ball"]
GIMMICK_KEY = {"mega": "mega-bracelet", "z": "z-ring", "dynamax": "dynamax-band", "tera": "tera-orb"}
GIMMICK_NAME = {"mega": "Mega Evolution", "z": "Z-Move", "dynamax": "Dynamax", "tera": "Terastallize"}
SELL_RATIO = 0.5


# --------------------------------------------------------------------------- random trainers met while exploring
TRAINER_CLASSES = [
    ("Youngster", "Hey! You look like a trainer. My Pokémon are in the top percentage of all Pokémon!", "Wow, you're strong... I need more training!", "Ha! Told you my Pokémon were the best!"),
    ("Lass", "Our eyes met! That means we have to battle!", "Oh no, I lost... you're really good!", "Yay! We won! Better luck next time!"),
    ("Bug Catcher", "I've been catching bugs all day. Wanna see what I found?", "My bugs! They were so shiny...", "Bzzt! My bugs rule!"),
    ("Hiker", "I've climbed every mountain around here. Think you can climb past me?", "Haha, you rolled right over me!", "Rock solid! Come back when you're tougher."),
    ("Fisherman", "Got nothing bitin' today. Let's battle instead!", "Reeled in by a better trainer...", "Another one that got away! Heh."),
    ("Picnicker", "I was having a lovely picnic, but a battle is even better!", "Oh my, you ruined my picnic!... in a good way.", "That was fun! Want a sandwich after?"),
    ("Ace Trainer", "I train every single day. Show me what you've got!", "Hmph. Impressive. I'll train harder.", "Training pays off! Better luck next time."),
    ("Camper", "Nature's calling... and so is a battle!", "You've got real camping spirit... I mean battle spirit!", "That's how we do it in the wild!"),
    ("Sailor", "Ahoy, landlubber! Ready for a battle on dry land?", "Blow me down! You win!", "Shiver me timbers! Another victory for me!"),
    ("Scientist", "My research needs battle data. Do you mind?", "Fascinating. The data says... you're stronger.", "Excellent. Data collected. Thank you!"),
    ("Backpacker", "I've walked thousands of miles and battled everyone on the way!", "Wow, even my long journey didn't prepare me for you.", "One more battle won on the road!"),
    ("Cool Trainer", "Hey you! Yes, you. Let's make this battle cool.", "Wow, that was cool. You win!", "Cool! I win."),
]
TRAINER_NAMES = ["Joey", "Mia", "Ben", "Lily", "Sam", "Nora", "Tyler", "Hana", "Leo", "Ivy", "Max", "Ruby", "Kai", "Zoe", "Finn",
                 "Cleo", "Owen", "Luna", "Jack", "Rosa", "Eli", "Maya", "Theo", "Ada", "Noah", "Ellie", "Oscar", "Sana"]
TRAINER_CHANCE = 0.18
EVENT_CHANCE = 0.12             # chance per /explore that today's event Pokémon shows up
EVENT_BONUS = 800               # paid the first time you catch it each day
TRAINER_DOUBLES_CHANCE = 0.5     # from 4 badges on, passing trainers bring 6 Pokémon; this share of them fight in doubles


# Pokédex milestones: caught count -> (money, item key, qty, title)
DEX_MILESTONES = {
    10: (500, "poke-ball", 10, "Rookie Collector"),
    25: (1000, "great-ball", 10, "Bug Catcher"),
    50: (2000, "ultra-ball", 10, "Pokémon Fan"),
    100: (4000, "rare-candy", 3, "Pokémon Ranger"),
    200: (8000, "rare-candy", 5, "Dex Expert"),
    400: (15000, "ultra-ball", 30, "Pokémon Professor"),
    1000: (30000, "master-ball", 1, "Dex Master"),
}


# Eggs & breeding
EGG_STEPS = 70            # /explore encounters until an egg hatches
EGG_CAP = 6               # eggs you can carry at once
BREED_COST = 500
BREED_IVS = 3             # IVs a bred baby copies from its parents (random stats, random parent)
EGG_FIND_CHANCE = 0.04    # per /explore: you stumble on an egg of this region
EGG_NPC_CHANCE = 0.05     # per /explore: a wandering merchant sells an egg from ANOTHER region
EGG_NPC_PRICE = 1500
EGG_HATCH_LEVEL = 5
DITTO = 132

FRIENDLY_WIN_PRIZE = 900       # winner of a friendly battle (the loser loses nothing)
FRIENDLY_LOSS = 200            # the loser pays this (or whatever they have left), only when the winner is actually paid
FRIENDLY_DAILY_CAP = 5         # rewarded friendly wins per player per day (stops two friends farming money)
