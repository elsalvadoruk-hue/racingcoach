"""Original Korean drafts. Text only: never added to synthesis/audio catalogs."""
VERSION = 'banter-drafts-v1'
ACTIVE_TRIGGERS = ('overlap_resolved', 'sandwich_resolved', 'quiet_running', 'personal_best',
                   'position_gain', 'clean_stint', 'race_finish', 'pit_service_done')
DRAFTS = {
    'overlap_resolved': (
        '옆집이 드디어 이사 갔네요.', '옆자리 손님, 다음에 또 뵙겠습니다.',
        '방금은 나란히 출근했네요.', '단체 사진 시간은 끝났습니다.',
        '어깨동무는 여기까지네요.', '옆 차와의 긴 대화가 끝났네요.',
        '나란히 걷기는 여기까지였습니다.', '무전기 들고 숨 참고 있었네요.',
        '옆자리 예약이 풀렸네요.', '거울 속 출석 확인은 끝났습니다.',
        '방금 장면은 제 수첩에 적어둘게요.', '저도 이제 커피 한 모금 생각나네요.',
    ),
    'sandwich_resolved': (
        '샌드위치 탈출. 속은 우리였네요.', '양옆 회의는 여기서 마쳤습니다.',
        '단체 사진에서 이제 빠져나왔네요.', '양옆 손님은 각자 자리로 갔네요.',
        '방금은 팔걸이 두 개를 빌려드렸네요.', '그 좁은 자리, 오래 앉아 있었네요.',
        '방금 장면 제목은 사이에 낀 우리네요.', '저도 모르게 어깨를 오므렸네요.',
        '양옆 출석 확인은 끝났습니다.', '옆집 두 곳과 인사는 충분했네요.',
        '이 장면은 제 수첩 두 줄입니다.', '저도 숨 한 번 고르겠습니다.',
    ),
    'quiet_running': (
        '조용하죠? 무전기 고장 난 거 아닙니다.', '저도 같이 달리고 있습니다. 말만 쉬고 있어요.',
        '오늘 무전기 배터리는 오래 가겠네요.', '말이 없는 것도 제 업무입니다.',
        '관중석 대신 여기서 지켜보고 있습니다.', '제 자리에서는 계속 보고 있습니다.',
        '잠깐 조용한 시간을 즐기고 있었습니다.', '필요한 말 생기면 바로 부르겠습니다.',
    ),
    'personal_best': (
        '이 랩은 보관합시다. 잘 나왔네요.', '기록표에 새 줄이 생겼네요.',
        '이 숫자는 수첩에 동그라미입니다.', '방금 랩은 다시 읽어도 좋네요.',
        '초시계가 반가운 숫자를 보여주네요.', '오늘의 기념품 하나 생겼네요.',
        '방금 기록은 제가 잘 챙겨둘게요.', '이 랩은 폴더 맨 위에 둡시다.',
    ),
    'position_gain': (
        '한 자리 올라왔습니다. 의자는 제가 잡아둘게요.', '순위표에서 한 칸 위로 이사했네요.',
        '앞으로 온 만큼 제 글씨도 올라갑니다.', '순위표 주소가 바뀌었네요.',
        '새 자리 확인했습니다. 메모해둘게요.', '순위표에 작은 좋은 소식이 있네요.',
        '자리를 옮긴 건 순위표에서도 확인됐네요.', '한 자리 전진, 제 수첩에도 반영했습니다.',
    ),
    'clean_stint': (
        '이번 구간은 수첩이 깔끔하네요.', '메모할 문제 없이 한 구간 왔네요.',
        '제 지우개가 쉴 수 있었네요.', '조용히 쌓인 랩도 꽤 든든하네요.',
        '이번 구간은 체크 표시로 남깁니다.', '여기까지의 기록은 잘 챙겨뒀습니다.',
        '랩이 차곡차곡 쌓이고 있네요.', '수첩에 좋은 쪽으로 여백이 많네요.',
    ),
    'race_finish': (
        '여기까지 함께 달렸습니다. 수고했어요.', '체커까지 왔네요. 제 수첩도 한 장 끝입니다.',
        '오늘 무전은 이것으로 한 편이네요.', '끝까지 함께한 기록이 남았네요.',
        '이 경기 이야기는 주차하고 이어가죠.', '제 자리에서도 긴 경기였습니다.',
        '오늘 기록은 제가 잘 챙겨둘게요.', '경기 한 편, 같이 마쳤네요.',
    ),
    'pit_service_done': (
        '피트팀은 잠깐이었고 제 기다림은 길었네요.', '정비 내역은 수첩에 남겨뒀습니다.',
        '피트팀 손놀림은 볼 때마다 빠르네요.', '잠깐 들렀다가 할 일은 다 했네요.',
        '정비 메뉴 한 장을 마쳤네요.', '피트에서 작은 분주함이 끝났네요.',
        '저는 무전만 했는데 같이 바빴네요.', '정비팀 몫의 체크 표시가 생겼네요.',
    ),
}
# Shared motifs intentionally span situations, so changing situations cannot evade repetition.
MOTIFS = ('home', 'guest', 'office', 'photo', 'shoulder', 'conversation',
          'walk', 'breath', 'seat', 'attendance', 'notebook', 'coffee')
FAMILIES = {
    'overlap_resolved': MOTIFS,
    'sandwich_resolved': ('food', 'office', 'photo', 'guest', 'seat', 'seat', 'food', 'shoulder', 'attendance', 'home', 'notebook', 'breath'),
    'quiet_running': ('radio', 'radio', 'battery', 'office', 'spectator', 'spectator', 'quiet', 'radio'),
    'personal_best': ('archive', 'record', 'notebook', 'record', 'stopwatch', 'souvenir', 'archive', 'archive'),
    'position_gain': ('seat', 'home', 'notebook', 'home', 'seat', 'record', 'seat', 'notebook'),
    'clean_stint': ('notebook', 'notebook', 'eraser', 'lap', 'notebook', 'archive', 'lap', 'notebook'),
    'race_finish': ('team', 'notebook', 'radio', 'archive', 'conversation', 'spectator', 'archive', 'team'),
    'pit_service_done': ('waiting', 'notebook', 'team', 'office', 'menu', 'office', 'radio', 'notebook'),
}
CATALOG = tuple({'id': f'{trigger}.{index+1:02}', 'trigger': trigger,
                 'motif': FAMILIES[trigger][index], 'text': text, 'version': VERSION}
                for trigger, texts in DRAFTS.items() for index, text in enumerate(texts))
