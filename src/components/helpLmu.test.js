import { fireEvent, render, screen, within } from '@testing-library/react';
import { UserManual } from './HelpGuide';
import { LMU_SECTIONS } from './helpLmu';
import LmuWorkspacePanel from '../desktop/LmuWorkspacePanel';

const titles = [
  'iRacing과 다른 점', '처음 시작하기 (LMU)', '코너와 그래프 읽는 법', '셋업 실험 · 추후 공개',
  '기록·연결·문제 해결', '주행 중 음성으로 묻고 메모하기', '경기 중 레이스 피트월', '한국어 스포터',
  '버추얼 에너지 안내', '엔지니어가 먼저 말해 주는 것', '코너를 공식 번호로 부르게 하기',
  '음성 받기 · 업데이트', '구간 반복 연습', '실시간 오버레이', '설정 탭 (LMU)', '용어 풀이',
].map((title, index) => `${index + 1}. ${title}`);

test('the LMU workspace shows the shared manual with all sixteen chapters in order', () => {
  render(<LmuWorkspacePanel workspace="guide" onNavigate={jest.fn()} />);
  expect(screen.getByRole('heading', { name: 'LMU 사용법' })).toBeVisible();
  const contents = screen.getByRole('navigation', { name: '매뉴얼 목차' });
  expect(within(contents).getAllByRole('link').map(link => link.textContent)).toEqual(titles);
  expect(screen.getByText(/기능을 사용하려면 LMU DuckDB 주행 파일을 선택하세요/)).toBeVisible();
  const firstChapter = screen.getByRole('heading', { name: titles[0] }).closest('section');
  expect(within(firstChapter).getByRole('table')).toBeInTheDocument();
  expect(within(firstChapter).getByRole('row', { name: /셋업 조회·A\/B 변경 항목/ })).toHaveTextContent('추후 공개');
});

test('search finds energy chapters and the glossary, and indexes the embedded feature table', () => {
  render(<LmuWorkspacePanel workspace="guide" onNavigate={jest.fn()} />);
  const search = screen.getByRole('searchbox', { name: '사용 가이드 검색' });
  fireEvent.change(search, { target: { value: '버추얼' } });
  expect(screen.getByRole('heading', { name: titles[0] })).toBeVisible();
  expect(screen.getByRole('heading', { name: titles[8] })).toBeVisible();
  // The glossary defines the same term and remains searchable as well.
  expect(screen.getByRole('heading', { name: titles[15] })).toBeVisible();
  expect(screen.queryByRole('heading', { name: titles[2] })).not.toBeInTheDocument();
  fireEvent.change(search, { target: { value: '셋업 조회·A/B 변경 항목' } });
  expect(screen.getByRole('heading', { name: titles[0] })).toBeVisible();
  expect(screen.getAllByRole('link')).toHaveLength(1);
  fireEvent.change(search, { target: { value: 'no-such-lmu-topic' } });
  expect(screen.getByText('찾는 사용법이 없습니다.')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '전체 가이드 보기' }));
  expect(search).toHaveFocus();
  expect(screen.getAllByRole('link')).toHaveLength(16);
});

test('table and chapter actions navigate, and analysis actions wait for a recording', () => {
  const onNavigate = jest.fn();
  const view = render(<LmuWorkspacePanel workspace="guide" onNavigate={onNavigate} />);
  expect(screen.getByRole('button', { name: '코너 조작 비교 열기' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '연결 상태 확인' }));
  fireEvent.click(screen.getByRole('button', { name: '셋업 조회·A/B 변경 항목 열기' }));
  expect(onNavigate.mock.calls).toEqual([['settings'], ['setup']]);
  view.rerender(<LmuWorkspacePanel workspace="guide" analysisWorkspace={{ analysis: {} }} onNavigate={onNavigate} />);
  fireEvent.click(screen.getByRole('button', { name: '코너 조작 비교 열기' }));
  expect(onNavigate).toHaveBeenLastCalledWith('corner-inputs');
  fireEvent.click(screen.getByRole('link', { name: titles[8] }));
  expect(screen.getByRole('link', { name: titles[8] })).toHaveAttribute('aria-current', 'location');
});

test('energy explains the pit-wall values and that its voice is still off, without speech examples', () => {
  render(<UserManual sections={LMU_SECTIONS} hasAnalysis onNavigate={jest.fn()} />);
  const energy = screen.getByRole('heading', { name: titles[8] }).closest('section');
  expect(energy).toHaveTextContent('에너지로 갈 수 있는 랩');
  expect(energy).toHaveTextContent('음성으로 안내합니다');
  expect(within(energy).queryByLabelText('음성 예시')).not.toBeInTheDocument();
});

test('the default iRacing manual retains its heading, twenty chapters and empty hint', () => {
  render(<UserManual onNavigate={jest.fn()} />);
  expect(screen.getByRole('heading', { name: '기능별 사용법' })).toBeVisible();
  expect(screen.getByText(/1.4.3 데스크톱 화면 기준입니다/)).toBeVisible();
  expect(screen.getByText(/기능을 사용하려면 IBT 파일을 선택하세요/)).toBeVisible();
  expect(within(screen.getByRole('navigation', { name: '매뉴얼 목차' })).getAllByRole('link')).toHaveLength(20);
  expect(screen.getByRole('heading', { name: '20. 실시간 오버레이: 게임 위 페달 그래프' })).toBeVisible();
  expect(screen.queryByRole('heading', { name: titles[0] })).not.toBeInTheDocument();
});
