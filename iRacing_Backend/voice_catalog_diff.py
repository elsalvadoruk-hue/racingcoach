"""Current distributable wordings, without telemetry, synthesis or user data."""
from functools import lru_cache

@lru_cache(maxsize=1)
def current_bindings():
    from radio_phrases import catalog
    from radio_audio import fixed_catalog
    from spotter import VARIANTS
    from oval_spotter import OPTIONAL_CLIPS
    return tuple(dict.fromkeys([*catalog(),*fixed_catalog(),*(('spotter',t) for choices in VARIANTS.values() for t in choices),
                               *(('spotter',t) for t in OPTIONAL_CLIPS.values())]))
