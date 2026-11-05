"""Warm the PokeAPI disk cache so the bot never waits on the network during play.

    python -m pokebot.prefetch                # everything (≈ 3,000 requests, a few minutes)
    python -m pokebot.prefetch kanto johto    # only some regions
"""
import asyncio
import sys

from . import world
from .data import PokeAPIProvider


async def main(region_keys):
    p = PokeAPIProvider()
    regions = [world.REGION_BY_KEY[k] for k in region_keys] if region_keys else world.REGIONS
    ids = [i for r in regions for i in range(r.dex_lo, r.dex_hi + 1)]
    moves = set()
    done = 0

    async def one(sid):
        nonlocal done
        try:
            sp = await p.get_species(sid)
            form = await p.get_form(sid)
            for mega in sp.mega_forms:
                await p.get_form(mega)
            moves.update(m for m, _ in form.learnset)
        except Exception as e:  # keep going; the bot retries lazily at play time
            print(f"  ! #{sid}: {e}")
        done += 1
        if done % 50 == 0:
            print(f"  species {done}/{len(ids)}")

    print(f"Fetching {len(ids)} species...")
    await asyncio.gather(*(one(i) for i in ids))
    print("Fetching type lists (for gym teams)...")
    from .data import TYPES
    await asyncio.gather(*(p.species_of_type(t) for t in TYPES))
    print(f"Fetching {len(moves)} moves...")
    moves = sorted(moves)
    fails = 0

    async def mv(name):
        nonlocal fails
        try:
            await p.get_move(name)
        except Exception:
            fails += 1

    await asyncio.gather(*(mv(m) for m in moves))
    print(f"Done. ({fails} moves failed; they will be retried on demand.)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
