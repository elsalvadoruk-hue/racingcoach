"""Prepared-only personal learning calls; each source phrase has a compact binding."""
VARIANTS = {
    'brake_earlier': ('다음 코너, 제동을 조금 일찍 시작해 보세요.', '다음 코너는 제동 시작을 조금 앞당겨 보세요.', '다음 코너, 조금 일찍 브레이크를 밟아 보세요.'),
    'brake_later': ('다음 코너, 제동을 조금 늦게 시작해 보세요.', '다음 코너는 제동 시작을 조금 늦춰 보세요.', '다음 코너, 조금 늦게 브레이크를 밟아 보세요.'),
    'release_earlier': ('다음 코너, 브레이크를 조금 일찍 풀어 보세요.', '다음 코너는 브레이크 해제를 조금 앞당겨 보세요.', '다음 코너, 브레이크를 일찍 놓는 쪽으로 가 보세요.'),
    'release_later': ('다음 코너, 브레이크를 조금 더 이어가 보세요.', '다음 코너는 브레이크를 조금 늦게 풀어 보세요.', '다음 코너, 제동을 조금 더 이어가는 쪽으로 가 보세요.'),
    'throttle_earlier': ('다음 코너, 가속을 조금 일찍 시작해 보세요.', '다음 코너는 가속 시작을 조금 앞당겨 보세요.', '다음 코너, 조금 일찍 가속 페달을 열어 보세요.'),
    'throttle_later': ('다음 코너, 가속을 조금 기다려 보세요.', '다음 코너는 가속 시작을 조금 늦춰 보세요.', '다음 코너, 가속 페달을 조금 늦게 열어 보세요.'),
    'chain_entry_slower': ('연속 코너입니다. 첫 진입을 조금 낮추고 연결해 보세요.', '다음 연속 코너, 첫 진입 속도를 조금 줄여 보세요.', '연속 코너, 첫 코너를 조금 여유 있게 들어가 보세요.'),
    'impact_less': ('다음 코너, 차에 충격을 적게 주는 쪽으로 통과해 보세요.', '다음 코너는 충격이 적은 쪽으로 가 보세요.', '다음 코너, 차가 덜 튀도록 통과해 보세요.'),
}
SHORT = {
    'brake_earlier': ('다음 코너, 제동 조금 일찍.', '다음 코너, 제동 시작 앞당기세요.', '다음 코너, 브레이크 조금 일찍.'),
    'brake_later': ('다음 코너, 제동 조금 늦게.', '다음 코너, 제동 시작 늦추세요.', '다음 코너, 브레이크 조금 늦게.'),
    'release_earlier': ('다음 코너, 브레이크 조금 일찍 풀어요.', '다음 코너, 제동 해제 앞당기세요.', '다음 코너, 브레이크 일찍 놓아요.'),
    'release_later': ('다음 코너, 제동 조금 더 이어가요.', '다음 코너, 브레이크 조금 늦게 풀어요.', '다음 코너, 브레이크 더 이어가요.'),
    'throttle_earlier': ('다음 코너, 가속 조금 일찍.', '다음 코너, 가속 시작 앞당기세요.', '다음 코너, 페달 조금 일찍 열어요.'),
    'throttle_later': ('다음 코너, 가속 조금 기다려요.', '다음 코너, 가속 시작 늦추세요.', '다음 코너, 페달 조금 늦게 열어요.'),
    'chain_entry_slower': ('연속 코너, 첫 진입 조금 낮춰요.', '연속 코너, 첫 코너 속도 줄여요.', '연속 코너, 첫 진입 여유 있게.'),
    'impact_less': ('다음 코너, 충격 줄여요.', '다음 코너, 덜 튀는 쪽으로.', '다음 코너, 충격 적은 쪽으로.'),
}

# Reviewed radio punctuation uses complete sentences, apart from the legacy box call.
VARIANTS = {key: tuple(text.replace(', ', '. ') for text in choices) for key,choices in VARIANTS.items()}
SHORT = {key: tuple(text.replace(', ', '. ') for text in choices) for key,choices in SHORT.items()}
