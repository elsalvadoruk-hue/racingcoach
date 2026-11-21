import React from 'react';
import UserManual from './UserManual';
import { LMU_SECTIONS } from './helpLmu';

export default function LmuManual(props) {
  return <UserManual sections={LMU_SECTIONS} heading="LMU 사용법"
    intro="iRacing과 다른 점은 1장, 처음 연결은 2장, 주행 중 음성은 6~10장, 폴더와 게임별 설정은 15장을 보세요."
    emptyHint="주행 분석이 없어도 매뉴얼을 읽을 수 있습니다. 기능을 사용하려면 LMU DuckDB 주행 파일을 선택하세요."
    {...props} />;
}
