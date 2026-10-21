"""Bounded, fail-closed C++ layout reader for the Windows x64 LMU SDK.

No SDK declarations are shipped here. Only reachable data declarations are
interpreted; executable SDK protocol text is hashed, never executed.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
from pathlib import Path
import re
import threading

from lmu_shared_memory import HEADERS, resolve_sdk_dir


class LayoutUnsupported(ValueError):
    """The installed SDK cannot safely be interpreted by this reader."""


EVENT_NAMES = tuple(('SME_ENTER SME_EXIT SME_STARTUP SME_SHUTDOWN SME_LOAD '
                     'SME_UNLOAD SME_START_SESSION SME_END_SESSION SME_ENTER_REALTIME '
                     'SME_EXIT_REALTIME SME_UPDATE_SCORING SME_UPDATE_TELEMETRY '
                     'SME_INIT_APPLICATION SME_UNINIT_APPLICATION SME_SET_ENVIRONMENT SME_FFB').split())
# Reviewed protocol identities; data structure definitions are excluded.
KNOWN_PROTOCOLS = frozenset({'069fb687abd1e1918ff5fcb22d570d23292130bad9b17a64bb6a92f903841e60'})

# These are reader requirements, not a mirror of SDK structure declarations.
# Numeric widths and offsets come exclusively from installed headers.
REQUIRED_FIELDS = {
    'TelemVect3': {'float': 'x y z'},
    'TelemWheelV01': {
        'float': 'mSuspensionDeflection mRideHeight mBrakeTemp mPressure mWear',
        'bool': 'mFlat mDetached', 'float[3]': 'mTemperature'},
    'TelemInfoV01': {
        'signed': 'mID mGear',
        'float': ('mElapsedTime mLapStartET mEngineRPM mEngineWaterTemp mEngineOilTemp '
                  'mUnfilteredThrottle mUnfilteredBrake mUnfilteredSteering mUnfilteredClutch '
                  'mFuel mLastImpactET mLastImpactMagnitude mFuelCapacity mRearBrakeBias '
                  'mPhysicalSteeringWheelRange mVirtualEnergy mTimeGapCarAhead mTimeGapCarBehind '
                  'mTimeGapPlaceAhead mTimeGapPlaceBehind'),
        'bool': 'mOverheating mDetached mLapInvalidated mABSActive mTCActive mSpeedLimiterActive',
        'unsigned': ('mFrontTireCompoundIndex mRearTireCompoundIndex mTC mTCMax mABS mABSMax mTrackLimitsSteps'),
        'text': 'mVehicleName mTrackName mFrontTireCompoundName mRearTireCompoundName mVehicleModel',
        'TelemVect3': 'mPos mLocalVel mLocalRot', 'TelemVect3[3]': 'mOri', 'TelemWheelV01[4]': 'mWheel'},
    'ScoringInfoV01': {
        'signed': 'mSession mMaxLaps mNumVehicles mYellowFlagState',
        'unsigned': 'mGamePhase mStartLight mNumRedLights mTrackLimitsStepsPerPenalty',
        'float': ('mCurrentET mEndET mLapDist mRaining mAmbientTemp mTrackTemp '
                  'mAvgPathWetness mSessionTimeRemaining'),
        'bool': 'mInRealtime', 'signed[3]': 'mSectorFlag', 'pointer': 'mResultsStream mVehicle'},
    'VehicleScoringInfoV01': {
        'signed': ('mID mTotalLaps mSector mFinishStatus mNumPitstops mNumPenalties mControl '
                   'mLapsBehindNext mLapsBehindLeader'),
        'unsigned': 'mPlace mPitState mFlag mCountLapFlag',
        'float': ('mLapDist mPathLateral mTrackEdge mBestLapTime mLastLapTime mTimeBehindNext '
                  'mTimeBehindLeader mLapStartET mPitLapDist'),
        'bool': 'mIsPlayer mInPits mUnderYellow mInGarageStall',
        'text': 'mVehicleClass', 'TelemVect3': 'mLocalVel'},
    'SharedMemoryGeneric': {'signed': 'gameVersion', 'unsigned[16]': 'events'},
    'SharedMemoryTelemetryData': {'unsigned': 'activeVehicles playerVehicleIdx',
                                'bool': 'playerHasVehicle', 'TelemInfoV01[104]': 'telemInfo'},
    'SharedMemoryScoringData': {'ScoringInfoV01': 'scoringInfo', 'unsigned': 'scoringStreamSize',
                              'VehicleScoringInfoV01[104]': 'vehScoringInfo', 'text': 'scoringStream'},
    'SharedMemoryObjectOut': {'SharedMemoryGeneric': 'generic', 'SharedMemoryPathData': 'paths',
                             'SharedMemoryScoringData': 'scoring', 'SharedMemoryTelemetryData': 'telemetry'},
    'SharedMemoryLayout': {'SharedMemoryObjectOut': 'data'},
}


def _kind(typ):
    return ('signed' if typ.signed else 'unsigned') if typ.kind == 'integer' else typ.kind


def validate_required_fields(types, lock):
    def matches(typ, expected):
        array = re.fullmatch(r'(.+)\[(\d+)\]', expected)
        if array:
            return typ.kind == 'array' and typ.count >= int(array[2]) and matches(typ.element, array[1])
        if expected == 'text':
            return typ.kind == 'array' and typ.count > 0 and typ.element.kind == 'integer' and typ.element.size == 1
        return _kind(typ) == expected or (typ.kind in ('struct', 'union') and typ.name == expected)
    for name, groups in REQUIRED_FIELDS.items():
        if name not in types:
            raise LayoutUnsupported('missing required type: ' + name)
        for expected, fields in groups.items():
            for field_name in fields.split():
                member = types[name].find(field_name)
                if not matches(member.type, expected):
                    raise LayoutUnsupported('required field kind changed: ' + name + '.' + field_name)
    # The only SDK-independent native code operates specifically on Windows LONG.
    if lock.size != 8 or lock.find('waiters').offset != 0 or lock.find('busy').offset != 4:
        raise LayoutUnsupported('lock word layout changed')
    for name in ('waiters', 'busy'):
        typ = lock.find(name).type
        if typ.kind != 'integer' or not typ.signed or typ.size != 4:
            raise LayoutUnsupported('lock word kind changed')

_LEX = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_]\w*|0[xX][0-9a-fA-F]+|\d+|::|[^\s]')
_COMMENTS = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/', re.S)


def _uncomment(text):
    return _COMMENTS.sub(lambda m: '\n' * m[0].count('\n') + ' ' if m[0].startswith(('/', '/*')) else m[0], text)


@dataclass(frozen=True)
class Field:
    name: str
    offset: int
    type: 'Type'

    @property
    def size(self):
        return self.type.size

    @property
    def kind(self):
        return self.type.kind


@dataclass(frozen=True)
class Type:
    name: str
    size: int
    alignment: int
    kind: str
    signed: bool = False
    fields: dict = field(default_factory=dict)
    element: 'Type | None' = None
    count: int = 0
    values: dict = field(default_factory=dict)

    def find(self, path):
        """Resolve dotted fields and optional array indices, with bounds checks."""
        offset, current = 0, self
        for part in path.split('.'):
            match = re.fullmatch(r'(\w+)((?:\[\d+\])*)', part)
            if not match or match[1] not in current.fields:
                raise LayoutUnsupported(f'missing field: {self.name}.{path}')
            member = current.fields[match[1]]
            offset += member.offset
            current = member.type
            for index in re.findall(r'\[(\d+)\]', match[2]):
                if current.kind != 'array' or int(index) >= current.count:
                    raise LayoutUnsupported(f'array index out of range: {path}')
                offset += int(index) * current.element.size
                current = current.element
        return Field(path, offset, current)


def _primitives():
    types = {}
    groups = [
        (1, True, 'char|signed char|int8_t'), (1, False, 'unsigned char|uint8_t'),
        (2, True, 'short|short int|signed short|signed short int|int16_t'),
        (2, False, 'unsigned short|unsigned short int|uint16_t|wchar_t'),
        (4, True, 'int|signed|signed int|long|long int|signed long|signed long int|LONG|int32_t'),
        (4, False, 'unsigned|unsigned int|unsigned long|unsigned long int|DWORD|uint32_t'),
        (8, True, 'long long|long long int|signed long long|int64_t'),
        (8, False, 'unsigned long long|unsigned long long int|uint64_t|size_t'),
    ]
    for size, signed, names in groups:
        for name in names.split('|'):
            types[name] = Type(name, size, size, 'integer', signed)
    # MSVC <cmath> aliases, independently checked by the developer oracle.
    for name, size in [('float', 4), ('float_t', 4), ('double', 8), ('double_t', 8)]:
        types[name] = Type(name, size, size, 'float', True)
    types['bool'] = Type('bool', 1, 1, 'bool')
    for name in ['HWND', 'HANDLE']:
        types[name] = Type(name, 8, 8, 'pointer')
    return types


@dataclass
class _Declaration:
    name: str
    kind: str
    prefix: list
    body: list
    pack: int
    raw: list


class _Headers:
    def __init__(self, folder):
        self.folder = folder.resolve()
        self.files, self.defines = {}, {'MAX_PATH': '260'}
        self.pack, self.stack = 8, []

    def read(self, relative):
        path = (self.folder / relative).resolve()
        if not path.is_relative_to(self.folder):
            raise LayoutUnsupported('SDK include leaves the SDK directory')
        key = path.relative_to(self.folder).as_posix()
        if key in self.files:
            return ''
        data = path.read_bytes()
        self.files[key] = data
        source = _uncomment(data.decode('utf-8-sig'))
        lines = source.splitlines()
        useful = [i for i, line in enumerate(lines) if line.strip()]
        guard = None
        for index, line_index in enumerate(useful[:-2]):
            first = re.fullmatch(r'\s*#\s*ifndef\s+(\w+)\s*', lines[line_index])
            if first and re.fullmatch(r'\s*#\s*define\s+' + re.escape(first[1]) + r'\s*', lines[useful[index+1]]) and re.fullmatch(r'\s*#\s*endif\s*', lines[useful[-1]]):
                guard = {line_index, useful[index+1], useful[-1]}
                break
        result = []
        for i, line in enumerate(lines):
            if guard and i in guard:
                continue
            if not line.lstrip().startswith('#'):
                result.append(line)
                continue
            directive = re.fullmatch(r'\s*#\s*(\w+)\s*(.*?)\s*', line)
            if not directive:
                raise LayoutUnsupported('invalid preprocessor directive')
            command, value = directive.groups()
            if command == 'include':
                inc = re.fullmatch(r'"([^"\n]+)"', value)
                if inc:
                    result.append(self.read(path.parent / inc[1]))
                elif not re.fullmatch(r'<[^>]+>', value):
                    raise LayoutUnsupported('unsupported include')
            elif command == 'pragma':
                if value == 'once':
                    continue
                pack = re.fullmatch(r'pack\s*\(\s*(push\s*,\s*(1|2|4|8|16)|pop)\s*\)', value)
                if not pack:
                    raise LayoutUnsupported('unsupported #pragma')
                if pack[1] == 'pop':
                    if not self.stack:
                        raise LayoutUnsupported('unbalanced #pragma pack')
                    self.pack = self.stack.pop()
                else:
                    self.stack.append(self.pack)
                    self.pack = int(pack[2])
                result.append(f' __LMU_PACK_{self.pack} ')
            elif command == 'define':
                define = re.fullmatch(r'(\w+)\s+(.+)', value)
                if not define:
                    raise LayoutUnsupported('unsupported macro definition')
                if define[1] in self.defines and self.defines[define[1]] != define[2]:
                    raise LayoutUnsupported('conflicting macro definition')
                self.defines[define[1]] = define[2]
            else:
                raise LayoutUnsupported('conditional or unsupported preprocessor directive: #' + command)
        return '\n'.join(result)


class _Parser:
    def __init__(self, source, defines):
        self.tokens = _LEX.findall(source)
        self.match = {}
        stack = []
        for i, token in enumerate(self.tokens):
            if token in ('(', '[', '{'):
                stack.append((token, i))
            elif token in (')', ']', '}'):
                if not stack or stack[-1][0] != {')': '(', ']': '[', '}': '{'}[token]:
                    raise LayoutUnsupported('unbalanced C++ delimiters')
                _, begin = stack.pop()
                self.match[begin] = i
        if stack:
            raise LayoutUnsupported('unbalanced C++ delimiters')
        self.defines, self.constants = defines, {'MAX_PATH': 260}
        self.types, self.declarations = _primitives(), {}
        self.building = set()
        self._scan()

    def _scan(self):
        tokens, i, pack = self.tokens, 0, 8
        while i < len(tokens):
            if tokens[i].startswith('__LMU_PACK_'):
                pack = int(tokens[i].rsplit('_', 1)[1]); i += 1; continue
            if tokens[i] in ('struct', 'union', 'class', 'enum'):
                start, kind = i, tokens[i]
                j = i + 1
                if kind == 'enum' and tokens[j] in ('class', 'struct'):
                    j += 1
                name = tokens[j]
                if not re.fullmatch(r'\w+', name):
                    raise LayoutUnsupported('unnamed top-level type')
                k = j + 1
                while k < len(tokens) and tokens[k] not in (';', '{'):
                    k += 1
                if k == len(tokens):
                    raise LayoutUnsupported('incomplete type declaration')
                if tokens[k] == '{':
                    end = self.match[k]
                    if name in self.declarations:
                        raise LayoutUnsupported('duplicate type: ' + name)
                    self.declarations[name] = _Declaration(name, kind, tokens[j+1:k], tokens[k+1:end], pack, tokens[start:end+1])
                    i = end + 1
                    continue
            if tokens[i] in ('(', '[', '{'):
                i = self.match[i] + 1
            else:
                i += 1

    def integer(self, tokens):
        text = ''.join(tokens)
        if re.fullmatch(r'[-+]?\d+[uUlL]*|0[xX][0-9a-fA-F]+[uUlL]*', text):
            return int(re.sub(r'[uUlL]+$', '', text), 16 if text.lower().startswith('0x') else 10)
        if text in self.constants:
            return self.constants[text]
        if text in self.defines:
            value = self.defines[text]
            if not re.fullmatch(r'[-+]?\d+[uUlL]*|0[xX][0-9a-fA-F]+[uUlL]*', value):
                raise LayoutUnsupported('non-integer macro: ' + text)
            return self.integer(_LEX.findall(value))
        enum_name = text.split('::')[0] if '::' in text else None
        candidates = [enum_name] if enum_name else [n for n, d in self.declarations.items() if d.kind == 'enum']
        for name in candidates:
            if name not in self.building:
                self.resolve(name)
                if text in self.constants:
                    return self.constants[text]
        raise LayoutUnsupported('unknown integer constant: ' + text)

    def resolve(self, name):
        name = ' '.join(name.split())
        if name in self.types:
            return self.types[name]
        if name not in self.declarations or name in self.building:
            raise LayoutUnsupported('unknown or recursive type: ' + name)
        self.building.add(name)
        try:
            d = self.declarations[name]
            result = self._enum(d) if d.kind == 'enum' else self._record(d)
            self.types[name] = result
            return result
        finally:
            self.building.remove(name)

    def _enum(self, d):
        base = self.resolve(' '.join(d.prefix[1:])) if d.prefix and d.prefix[0] == ':' else self.types['int']
        if base.kind != 'integer':
            raise LayoutUnsupported('non-integer enum base')
        values, next_value = {}, 0
        for entry in self._split(d.body, ','):
            if not entry:
                continue
            if len(entry) != 1 and (len(entry) < 3 or entry[1] != '='):
                raise LayoutUnsupported('unsupported enum expression')
            name = entry[0]
            value = self.integer(entry[2:]) if len(entry) > 1 else next_value
            if name in values or not -(1 << (base.size*8-1)) <= value < (1 << (base.size*8)):
                raise LayoutUnsupported('invalid enum value')
            values[name] = value
            self.constants[name] = self.constants[d.name + '::' + name] = value
            next_value = value + 1
        return Type(d.name, base.size, base.alignment, 'integer', base.signed, values=values)

    @staticmethod
    def _split(tokens, delimiter):
        result, current, depth = [], [], 0
        for token in tokens:
            if token in ('[', '(', '{'):
                depth += 1
            elif token in (']', ')', '}'):
                depth -= 1
            if token == delimiter and depth == 0:
                result.append(current); current = []
            else:
                current.append(token)
        if current:
            result.append(current)
        return result

    def _record(self, d):
        if 'virtual' in d.body or 'template' in d.body or 'template' in d.prefix:
            raise LayoutUnsupported('virtual or template reachable type: ' + d.name)
        offset, alignment, members = 0, 1, {}
        if d.prefix:
            if len(d.prefix) != 3 or d.prefix[:2] != [':', 'public']:
                raise LayoutUnsupported('unsupported inheritance: ' + d.name)
            parent = self.resolve(d.prefix[2])
            if parent.kind != 'struct':
                raise LayoutUnsupported('unsupported base type')
            offset, alignment, members = parent.size, min(parent.alignment, d.pack), dict(parent.fields)
        tokens, i = d.body, 0
        def add(name, typ, anonymous=False):
            nonlocal offset, alignment
            align = min(typ.alignment, d.pack)
            alignment = max(alignment, align)
            position = 0 if d.kind == 'union' else (offset + align - 1) // align * align
            offset = max(offset, typ.size) if d.kind == 'union' else position + typ.size
            fields = [replace(f, offset=f.offset + position) for f in typ.fields.values()] if anonymous else [Field(name, position, typ)]
            for member in fields:
                if member.name in members:
                    raise LayoutUnsupported('duplicate field: ' + d.name + '.' + member.name)
                members[member.name] = member
        while i < len(tokens):
            if tokens[i] == ';':
                i += 1; continue
            if tokens[i] in ('public', 'private', 'protected') and tokens[i+1:i+2] == [':']:
                i += 2; continue
            if tokens[i].startswith('__LMU_PACK_'):
                raise LayoutUnsupported('pack changes inside reachable record')
            if tokens[i] in ('struct', 'union', 'enum', 'class'):
                j = i + 1
                while j < len(tokens) and tokens[j] not in ('{', ';'):
                    j += 1
                if j >= len(tokens) or tokens[j] != '{':
                    raise LayoutUnsupported('unsupported nested declaration')
                end = self._end(tokens, j)
                nested_name = tokens[i+1] if j > i+1 else d.name + '::<anonymous>'
                nested = _Declaration(nested_name, tokens[i], tokens[i+2:j] if j > i+1 else [], tokens[j+1:end], d.pack, tokens[i:end+1])
                typ = self._enum(nested) if nested.kind == 'enum' else self._record(nested)
                k = end + 1
                while k < len(tokens) and tokens[k] != ';':
                    k += 1
                suffix = tokens[end+1:k]
                if j > i+1:
                    if nested_name in self.types:
                        raise LayoutUnsupported('duplicate nested type')
                    self.types[nested_name] = typ
                if suffix:
                    for name, member_type in self._declarators([nested_name] + suffix, override=typ):
                        add(name, member_type)
                elif j == i+1:
                    add('', typ, anonymous=True)
                i = k + 1
                continue
            j = i
            while j < len(tokens) and tokens[j] not in (';', '{'):
                # Array brackets and method parameters can contain delimiters.
                if tokens[j] in ('[', '('):
                    j = self._end(tokens, j)
                j += 1
            statement = tokens[i:j]
            if j == len(tokens) and statement:
                raise LayoutUnsupported('unterminated record member')
            if j < len(tokens) and tokens[j] == '{':
                if '(' not in statement:
                    raise LayoutUnsupported('unsupported data initializer')
                i = self._end(tokens, j) + 1
                continue
            if statement and statement[0] in ('static', 'using'):
                i = j + 1; continue
            # Function declarations (including operators), never function pointers.
            if '(' in statement:
                first = statement.index('(')
                if first+1 < len(statement) and statement[first+1] in ('*', '&'):
                    raise LayoutUnsupported('function pointer member is unsupported')
                i = j + 1; continue
            for name, typ in self._declarators(statement):
                add(name, typ)
            i = j + 1
        size = (offset + alignment - 1) // alignment * alignment
        if not size:
            size = 1
        return Type(d.name, size, alignment, 'union' if d.kind == 'union' else 'struct', fields=members)

    @staticmethod
    def _end(tokens, start):
        opening, closing = tokens[start], {'(': ')', '[': ']', '{': '}'}[tokens[start]]
        depth = 1
        for end in range(start+1, len(tokens)):
            if tokens[end] == opening:
                depth += 1
            elif tokens[end] == closing:
                depth -= 1
                if not depth:
                    return end
        raise LayoutUnsupported('unbalanced nested declaration')

    def _declarators(self, tokens, override=None):
        tokens = [t for t in tokens if t not in ('const', 'volatile')]
        if not tokens:
            return []
        if any(t in tokens for t in (':', '<', '>', '&', 'typedef')):
            raise LayoutUnsupported('unsupported member declaration (bitfield, template or reference)')
        entries = self._split(tokens, ',')
        first = entries[0]
        bound = first.index('[') if '[' in first else first.index('=') if '=' in first else len(first)
        if bound < 2:
            raise LayoutUnsupported('incomplete member declaration')
        name_index = bound-1
        base_tokens = first[:name_index]
        pointer_start = base_tokens.index('*') if '*' in base_tokens else len(base_tokens)
        base_name = ' '.join(base_tokens[:pointer_start])
        entries[0] = base_tokens[pointer_start:] + first[name_index:]
        result = []
        for entry in entries:
            if '=' in entry:
                entry = entry[:entry.index('=')]
            pointer = '*' in entry
            while entry and entry[0] == '*':
                entry = entry[1:]
            if not entry or not re.fullmatch(r'[A-Za-z_]\w*', entry[0]):
                raise LayoutUnsupported('unsupported field name')
            name = entry[0]
            typ = Type(base_name+'*', 8, 8, 'pointer') if pointer else override or self.resolve(base_name)
            dimensions, i = [], 1
            while i < len(entry):
                if entry[i] != '[':
                    raise LayoutUnsupported('unsupported member suffix')
                end = self._end(entry, i)
                count = self.integer(entry[i+1:end])
                if count <= 0 or count > 16_777_216:
                    raise LayoutUnsupported('invalid array bound')
                dimensions.append(count); i = end+1
            for count in reversed(dimensions):
                typ = Type(f'{typ.name}[{count}]', typ.size*count, typ.alignment, 'array', element=typ, count=count)
            result.append((name, typ))
        return result


@dataclass(frozen=True)
class SdkLayout:
    types: dict
    root: Type
    lock: Type
    fingerprint: str
    protocol_fingerprint: str
    dependencies: tuple
    events: dict

    def find(self, path):
        return self.root.find(path)

    def require_known_protocol(self):
        if self.protocol_fingerprint not in KNOWN_PROTOCOLS:
            raise LayoutUnsupported('unrecognized synchronization/copy protocol')


def _protocol(parser):
    event = parser.resolve('SharedMemoryEvent')
    values = event.values
    names = list(values)
    if names[:16] != list(EVENT_NAMES) or not names or names[-1] != 'SME_MAX' or any(values.get(name) != i for i, name in enumerate(EVENT_NAMES)):
        raise LayoutUnsupported('existing event names/order/values changed')
    if list(values.values()) != list(range(len(values))):
        raise LayoutUnsupported('event values must remain consecutive; SME_MAX must be last')
    pieces = []
    for name in ('SharedMemoryUpdateWaiter', 'SharedMemoryLock'):
        if name not in parser.declarations:
            raise LayoutUnsupported('missing protocol class: ' + name)
        pieces.append(' '.join(parser.declarations[name].raw))
    tokens = parser.tokens
    found = [i for i, token in enumerate(tokens) if token == 'CopySharedMemoryObj' and tokens[i+1:i+2] == ['(']]
    functions = []
    for i in found:
        end = parser.match[i+1]
        if tokens[end+1:end+2] == ['{']:
            begin = i
            while begin and tokens[begin-1] not in (';', '}', '{'):
                begin -= 1
            functions.append(' '.join(tokens[begin:parser.match[end+1]+1]))
    if len(functions) != 1:
        raise LayoutUnsupported('missing or ambiguous copy protocol')
    pieces.extend(functions)
    pieces.extend(name+' '+' '.join(_LEX.findall(value)) for name, value in sorted(parser.defines.items()) if name.startswith('LMU_'))
    pieces.extend(f'{name}={values[name]}' for name in EVENT_NAMES)
    return hashlib.sha256('\n'.join(pieces).encode('utf-8')).hexdigest(), values


_CACHE, _DEPENDENCIES = {}, {}
_GUARD = threading.RLock()


def _fingerprint(folder, dependencies):
    digest = hashlib.sha256()
    for name in dependencies:
        digest.update(name.encode('utf-8') + b'\0')
        digest.update((folder / name).read_bytes())
    return digest.hexdigest()


def load_layout(sdk_dir=None, *, require_protocol=True, use_cache=True):
    """Read every dependency on each call; reuse only identical header content."""
    folder = resolve_sdk_dir(sdk_dir)
    with _GUARD:
        dependencies = _DEPENDENCIES.get(folder, HEADERS)
        identity = _fingerprint(folder, dependencies)
        if use_cache and identity in _CACHE:
            result = _CACHE[identity]
        else:
            headers = _Headers(folder)
            source = '\n'.join(headers.read(name) for name in HEADERS)
            if headers.stack:
                raise LayoutUnsupported('unbalanced #pragma pack')
            dependencies = tuple(HEADERS) + tuple(sorted(set(headers.files)-set(HEADERS)))
            identity = _fingerprint(folder, dependencies)
            parser = _Parser(source, headers.defines)
            root = parser.resolve('SharedMemoryLayout')
            lock_class = parser.declarations.get('SharedMemoryLock')
            if not lock_class:
                raise LayoutUnsupported('missing lock protocol')
            body = lock_class.body
            matches = [i for i in range(len(body)-2) if body[i:i+3] == ['struct', 'LockData', '{']]
            if len(matches) != 1:
                raise LayoutUnsupported('missing lock data declaration')
            begin = matches[0]+2
            lock = parser._record(_Declaration('LockData', 'struct', [], body[begin+1:parser._end(body, begin)], lock_class.pack, []))
            protocol, events = _protocol(parser)
            validate_required_fields(parser.types, lock)
            if parser.types['SharedMemoryGeneric'].find('events').type.count != events['SME_MAX']:
                raise LayoutUnsupported('event array length differs from SME_MAX')
            result = SdkLayout(dict(parser.types), root, lock, identity, protocol, dependencies, events)
            _CACHE[identity] = result
            _DEPENDENCIES[folder] = dependencies
        if require_protocol:
            result.require_known_protocol()
        return result
