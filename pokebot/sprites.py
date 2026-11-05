"""Sprite URL helpers. Telegram downloads the image from the URL, nothing is bundled."""
from . import config

_RAW = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon"
BW_MAX_ID = 649  # Black/White sprites exist for national dex #1-649 only


def artwork_url(species_id: int, shiny: bool = False) -> str:
    return f"{_RAW}/other/official-artwork/{'shiny/' if shiny else ''}{species_id}.png"


def bw_url(species_id: int, *, back: bool = False, shiny: bool = False, animated: bool = False) -> str:
    path = f"{_RAW}/versions/generation-v/black-white"
    if animated:
        path += "/animated"
    if back:
        path += "/back"
    if shiny:
        path += "/shiny"
    ext = "gif" if animated else "png"
    return f"{path}/{species_id}.{ext}"


def sprite_for(species_id: int, *, back: bool = False, shiny: bool = False):
    """Return (url, kind) where kind is 'photo' or 'animation', or None when sprites are off."""
    style = config.SPRITE_STYLE
    if style == "none":
        return None
    if style in ("bw", "bw-animated") and species_id <= BW_MAX_ID:
        animated = style == "bw-animated"
        return bw_url(species_id, back=back, shiny=shiny, animated=animated), ("animation" if animated else "photo")
    return artwork_url(species_id), "photo"
