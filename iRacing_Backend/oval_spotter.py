"""Read-only track classification and bounded oval diagnostics, with no race commands."""

# SDK type strings documented by SIMRacingApps' own Track.Type/constant-values API.
# Unknown newer values are logged for review rather than inferred from a car/category.
ASPHALT_TYPES = frozenset(('short oval', 'medium oval', 'long oval', 'super speedway'))
ROAD_TYPES = frozenset(('road course', 'road', 'street course', 'dirt road'))
META_FIELDS = {'track_type': 'TrackType', 'track_direction': 'TrackDirection', 'track_category': 'Category'}
RULE_FIELDS = {'course_cautions': 'CourseCautions', 'restarts': 'Restarts', 'standing_start': 'StandingStart'}
OVAL_FIELDS = ('session_state', 'session_flags', 'pace_mode', 'pits_open',
               'player_pace_line', 'player_pace_row', 'player_pace_flags',
               'side_closing_mps', 'side_relative_mps', 'session_time_remaining_s', 'session_laps_remaining')
POLICY_VERSION = 'oval-overlap-v1'


def text(value):
    return value.strip()[:160] if isinstance(value, str) else ''


def track_context(weekend, game):
    """Session metadata only; a car or a sporting category cannot enable this policy."""
    if game != 'iracing':
        return {}
    result = {key: text(weekend.get(source)) for key, source in META_FIELDS.items()}
    options = weekend.get('WeekendOptions') or {}
    for key, source in RULE_FIELDS.items():
        value = options.get(source)
        result[key] = text(value) if isinstance(value, str) else str(value) if type(value) in (int, bool) else ''
    return result


def classify(identity):
    if identity.get('game') != 'iracing':
        return 'UNKNOWN', 'OTHER_GAME'
    kind = text(identity.get('track_type')).casefold()
    path = text(identity.get('track_path')).casefold()
    config = text(identity.get('track_config')).casefold()
    if 'centripetal' in path or 'skidpad' in path or 'skidpad' in config:
        return 'UNKNOWN', 'TEST_TRACK'
    if kind in ROAD_TYPES or any(word in config.replace('-', ' ').replace('_', ' ').split()
                                 for word in ('road', 'roadcourse', 'roval')):
        return 'ROAD', 'ROAD_LAYOUT'
    category = text(identity.get('track_category')).casefold().replace('_', ' ')
    if 'dirt' in kind or 'dirt' in category:
        return ('DIRT_OVAL' if 'oval' in kind or 'oval' in category else 'UNKNOWN'), 'DIRT_UNSUPPORTED'
    track_id = str(identity.get('track_id') or '')
    if not track_id.isascii() or not track_id.isdigit() or int(track_id) <= 0 or not path:
        return 'UNKNOWN', 'MISSING_LAYOUT_IDENTITY'
    if kind in ASPHALT_TYPES:
        return 'ASPHALT_OVAL', 'SDK_TRACK_TYPE'
    return 'UNKNOWN', 'UNKNOWN_TRACK_TYPE'


def is_oval(identity):
    return classify(identity)[0] == 'ASPHALT_OVAL'


def integer(value, maximum=2**31-1):
    return value if type(value) is int and 0 <= value <= maximum else None


def read_pacing(sdk, names, player_idx):
    """Read only the player's assignments. Missing/invalid values stay None, never zero."""
    mode = sdk['PaceMode'] if 'PaceMode' in names else None
    opened = sdk['PitsOpen'] if 'PitsOpen' in names else None
    result = {'pace_mode': integer(mode, 4),
              'pits_open': bool(opened) if type(opened) in (int, bool) and opened in (0, 1) else None}
    for target, source in (('player_pace_line', 'CarIdxPaceLine'), ('player_pace_row', 'CarIdxPaceRow'),
                           ('player_pace_flags', 'CarIdxPaceFlags')):
        value = None
        if type(player_idx) is int and 0 <= player_idx < 64 and source in names:
            values = sdk[source]
            if isinstance(values, (list, tuple)) and player_idx < len(values):
                value = integer(values[player_idx])
        result[target] = value
    return result


# Optional fixed clips. Never part of VARIANTS or automatic speech synthesis.
# Left/right event keys and alert categories remain compatible with existing packs.
OPTIONAL_PHRASES = {
    'inside': '안쪽 차량.', 'outside': '바깥쪽 차량.',
    'two_inside': '안쪽 두 대.', 'two_outside': '바깥쪽 두 대.',
    'inside_only': '안쪽만 남았습니다.', 'outside_only': '바깥쪽만 남았습니다.',
    'inside_one': '안쪽 한 대.', 'outside_one': '바깥쪽 한 대.',
    'clear_inside': '안쪽 클리어.', 'clear_outside': '바깥쪽 클리어.',
    'hold_inside': '계속 안쪽.', 'hold_outside': '계속 바깥쪽.',
    'hold_two_inside': '계속 안쪽 두 대.', 'hold_two_outside': '계속 바깥쪽 두 대.',
}


def audio_key(key, direction):
    if not isinstance(key, str) or direction not in ('left', 'right'):
        return None
    inside = 'left' if direction == 'left' else 'right'
    return ('oval_' + key.replace('left', 'inside' if inside == 'left' else 'outside')
            .replace('right', 'inside' if inside == 'right' else 'outside')) if ('left' in key or 'right' in key) else None


OPTIONAL_CLIPS = {'oval_' + key: value for key, value in OPTIONAL_PHRASES.items()}
