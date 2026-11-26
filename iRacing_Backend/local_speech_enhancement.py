"""Optional, bounded noise retry for questions; never creates a pit command."""
import hashlib
from pathlib import Path
import re

MODEL = {
    'file': 'gtcrn_simple.onnx', 'size': 535638,
    'sha256': 'e77603ac0c23dac3227dd2d7135b3a585cbee2679048aecfa886657d3ae1b534',
    'url': 'https://github.com/k2-fsa/sherpa-onnx/releases/download/speech-enhancement-models/gtcrn_simple.onnx',
}


def installed(folder, verify=False):
    path = Path(folder) / MODEL['file']
    try:
        return path.stat().st_size == MODEL['size'] and (not verify or hashlib.sha256(path.read_bytes()).hexdigest() == MODEL['sha256'])
    except OSError:
        return False


def download(folder):
    from urllib.request import urlopen
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if installed(folder, verify=True):
        return
    temporary = folder / (MODEL['file'] + '.part')
    try:
        with urlopen(MODEL['url'], timeout=90) as response:
            data = response.read(MODEL['size'] + 1)
        if len(data) != MODEL['size'] or hashlib.sha256(data).hexdigest() != MODEL['sha256']:
            raise ValueError('잡음 제거 모델 검증에 실패했습니다.')
        temporary.write_bytes(data)
        temporary.replace(folder / MODEL['file'])
    finally:
        temporary.unlink(missing_ok=True)


def anchors(text):
    # Compare stable subjects, not the syllables the model is trying to repair.
    groups = {'ahead': r'앞차|앞쪽차|전방차', 'behind': r'뒤차|뒷차|뒤쪽차|후방차|기차',
              'leader': r'선두|일등|1등', 'fuel': r'연료|기름', 'energy': r'에너지'}
    return {key for key, pattern in groups.items() if re.search(pattern, text)}


def eligible(text, game):
    from local_question_intents import classify, normalize
    result = classify(text, game)
    t = normalize(text)
    if not t or result['topic'] or result['reason'] != 'unsupported':
        return False
    # No incomplete commands, negation, multiple requests or numbers. Never
    # guess a damaged subject such as 아차/주차, even on a second transcript.
    if re.search(r'^(?:피트|히트|키트|핏스톱|pit)|교체|설정|취소|급유|공기압|압력|수리|'
                 r'말고|아니|하지|않|안해|그만|됐어|그리고|이랑|하고|아차|합차|주차|[0-9]', t):
        return False
    return bool(anchors(t) or re.search(r'랩타임|베스트|코너|스틴트|순위|인시던트', t))


def select(raw, candidate, game):
    from local_question_intents import classify, normalize
    if not eligible(raw, game) or not candidate:
        return None
    # A retry may repair a question, but cannot introduce/change its subject.
    if anchors(normalize(raw)) != anchors(normalize(candidate)):
        return None
    if re.search(r'^(?:피트|히트|키트|핏스톱|pit)|교체|설정|취소|[0-9]', normalize(candidate)):
        return None
    result = classify(candidate, game)
    return result if result['topic'] else None
