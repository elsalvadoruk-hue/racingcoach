"""오벌 고정 음성의 제작·청취 체크리스트를 만듭니다. 합성·전송은 하지 않습니다."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'iRacing_Backend'))
from oval_spotter import OPTIONAL_CLIPS


def checklist():
    return {'format':'racingcoach-oval-voice-checklist-v1', 'role':'spotter',
        'automatic_synthesis':False, 'ready_for_release':False,
        'entries':[{'event_key':key,'sourceText':text,'generated':False,'auditioned':False,
                    'direction_and_fallback_verified':False} for key,text in OPTIONAL_CLIPS.items()]}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    args.out.write_text(json.dumps(checklist(),ensure_ascii=False,indent=2),encoding='utf-8')
    print('제작 체크리스트를 저장했습니다. 음원 생성·청취 검수는 아직 수행하지 않았습니다.')
