"""Public, credential-free recovery messages shared by Garage61 entry points."""

TOKEN_SETTINGS = '설정 → 설치 설정 → Garage61 개인 API 토큰'
TOKEN_MISSING_MESSAGE = (
    f'Garage61 API 토큰이 등록되지 않았거나 읽을 수 없습니다. {TOKEN_SETTINGS}에서 '
    '토큰을 저장한 뒤 Garage61 다시 확인을 누르세요. 내 랩 비교는 계속 사용할 수 있습니다.'
)
PERMISSION_MESSAGE = (
    f'Garage61 자료 접근 권한이 없습니다. Garage61에서 driving_data 권한과 계정의 자료 접근 권한을 '
    f'확인하고, 필요한 경우 새 토큰을 {TOKEN_SETTINGS}에 저장하세요. 내 랩 비교는 계속 사용할 수 있습니다.'
)


def authentication_error(http_status: int) -> dict[str, str]:
    if http_status == 401:
        return {
            'error_code': 'API_TOKEN_INVALID',
            'message': f'Garage61 토큰 인증에 실패했습니다(HTTP 401). Garage61에서 새 토큰을 발급한 뒤 '
                       f'{TOKEN_SETTINGS}에 저장하세요. 내 랩 비교는 계속 사용할 수 있습니다.',
        }
    if http_status == 403:
        return {'error_code': 'API_PERMISSION_DENIED', 'message': PERMISSION_MESSAGE}
    return {}
