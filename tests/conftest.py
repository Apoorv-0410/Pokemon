import asyncio

import pytest

LOOP = asyncio.new_event_loop()


def run(coro):
    """One shared event loop for every test so the battle-engine subprocess survives between calls."""
    return LOOP.run_until_complete(coro)


@pytest.fixture(scope="session", autouse=True)
def _close_engine():
    yield
    from pokebot import showdown
    try:
        LOOP.run_until_complete(showdown.get_engine().close())
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _no_random_trainers():
    """Keep /explore deterministic: wild encounters unless a test asks for trainers."""
    from pokebot import session, world
    old, old_ev = session.Game.trainer_chance, world.EVENT_CHANCE
    old_eg = (world.EGG_FIND_CHANCE, world.EGG_NPC_CHANCE)
    world.EGG_FIND_CHANCE = world.EGG_NPC_CHANCE = 0.0
    session.Game.trainer_chance = 0.0
    world.EVENT_CHANCE = 0.0
    yield
    session.Game.trainer_chance = old
    world.EVENT_CHANCE = old_ev
    world.EGG_FIND_CHANCE, world.EGG_NPC_CHANCE = old_eg
