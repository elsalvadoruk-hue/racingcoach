"""LMU live-collection boundary; deliberately does not attach to an SDK yet."""


def live_status() -> dict:
    return {
        "provider": "LMU_SHARED_MEMORY",
        "state": "NOT_IMPLEMENTED",
        "collecting": False,
        "reason": "LMU 실시간 수집은 아직 구현하지 않았습니다. 설치된 게임의 공식 헤더와 실제 주행으로 검증한 뒤 연결합니다.",
    }
