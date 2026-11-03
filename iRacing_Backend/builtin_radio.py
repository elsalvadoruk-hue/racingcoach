"""Short versions of the standard voices, delivered by the normal voice updater."""
VERSION = '2.5.0'
VOICES = {'spotter': ('Fenrir', 'Algieba', 'Schedar', 'Aoede'),
          'coach': ('Algenib',), 'race': ('Charon',), 'strategy': ('Orus',)}
PACKS = {f'standard-short-{role}-{voice.lower()}@{VERSION}': (role, voice)
         for role, voices in VOICES.items() for voice in voices}
PREFIX = 'data/short_radio/'
