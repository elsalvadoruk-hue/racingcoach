"""Current distributable wordings, without telemetry, synthesis or user data."""
from functools import lru_cache

@lru_cache(maxsize=1)
def current_bindings():
    from radio_phrases import catalog
    from radio_audio import fixed_catalog
    from spotter import VARIANTS
    return tuple(dict.fromkeys([*catalog(),*fixed_catalog(),*(('spotter',t) for choices in VARIANTS.values() for t in choices)]))
