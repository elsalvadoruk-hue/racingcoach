import { act, fireEvent, render, screen } from '@testing-library/react';
import { HelpHint, HelpLayer, UserManual } from './HelpGuide';

beforeEach(() => {
  jest.spyOn(Element.prototype, 'getClientRects').mockReturnValue([{ width: 28, height: 28 }]);
  jest.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function () {
    return this.getAttribute('role') === 'tooltip'
      ? { left: 0, top: 0, right: 340, bottom: 180, width: 340, height: 180 }
      : { left: window.innerWidth - 35, top: window.innerHeight - 40, right: window.innerWidth - 7, bottom: window.innerHeight - 12, width: 28, height: 28 };
  });
});
afterEach(() => { jest.restoreAllMocks(); jest.useRealTimers(); });

test('manual search finds relevant chapters, recovers from no results and keeps navigation usable', () => {
  const navigate = jest.fn();
  render(<UserManual hasAnalysis onNavigate={navigate} />);
  const input = screen.getByRole('searchbox', { name: '사용 가이드 검색' });
  fireEvent.change(input, { target: { value: '피트박스' } });
  expect(screen.getByRole('heading', { name: '10. 한국어 스포터 사용하기' })).toBeVisible();
  expect(screen.queryByRole('heading', { name: '1. 처음 시작하기' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '한국어 스포터 열기' }));
  expect(navigate).toHaveBeenCalledWith('live-coach');
  fireEvent.change(input, { target: { value: 'not-a-real-guide-topic' } });
  expect(screen.getByText('찾는 사용법이 없습니다.')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '전체 가이드 보기' }));
  expect(input).toHaveFocus();
  expect(screen.getByRole('heading', { name: '1. 처음 시작하기' })).toBeVisible();
});

function Fixture({ hoverEnabled = true }) {
  return <><HelpLayer hoverEnabled={hoverEnabled} /><HelpHint topic="reference" />
    <button data-help="EXPLORE" aria-describedby="existing-description">탐색 선택</button>
    <p id="existing-description">기존 설명</p><button>다른 작업</button></>;
}

test('disabling hover closes the current explanation while click and keyboard help remain usable', () => {
  const view = render(<Fixture />);
  const hint = screen.getByRole('button', { name: '비교 기준 방식 도움말' });
  fireEvent.mouseOver(hint);
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
  view.rerender(<Fixture hoverEnabled={false} />);
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  expect(hint).not.toHaveAttribute('aria-describedby');
  fireEvent.mouseOver(hint);
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  fireEvent.click(hint);
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
  fireEvent.keyDown(hint, { key: 'Escape' });
  fireEvent.focusIn(screen.getByRole('button', { name: '탐색 선택' }));
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
  fireEvent.keyDown(hint, { key: 'Escape' });
  view.rerender(<Fixture hoverEnabled />);
  fireEvent.mouseOver(hint);
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
});

test('hover opens feature help above a bottom-edge control and Escape dismisses it', () => {
  render(<Fixture />);
  const hint = screen.getByRole('button', { name: '비교 기준 방식 도움말' });
  fireEvent.mouseOver(hint);
  const tooltip = screen.getByRole('tooltip');
  expect(tooltip).toHaveTextContent('SINGLE');
  expect(hint).toHaveAttribute('aria-describedby', tooltip.id);
  expect(parseFloat(tooltip.style.left) + 340).toBeLessThanOrEqual(window.innerWidth - 12);
  expect(parseFloat(tooltip.style.top) + 180).toBeLessThanOrEqual(window.innerHeight - 12);
  fireEvent.keyDown(hint, { key: 'Escape' });
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  expect(hint).not.toHaveAttribute('aria-describedby');
});

test('keyboard focus describes the control without replacing its existing description', () => {
  const view = render(<Fixture />);
  const control = screen.getByRole('button', { name: '탐색 선택' });
  fireEvent.focusIn(control);
  expect(screen.getByRole('tooltip')).toHaveTextContent('탐색 추천');
  expect(control).toHaveAttribute('aria-describedby', 'existing-description coach-feature-tooltip');
  view.unmount();
  expect(control).toHaveAttribute('aria-describedby', 'existing-description');
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
});

test('a help tap pins its explanation; another tap or outside click closes it', () => {
  jest.useFakeTimers();
  render(<Fixture />);
  const hint = screen.getByRole('button', { name: '비교 기준 방식 도움말' });
  fireEvent.focusIn(hint);
  fireEvent.click(hint);
  fireEvent.mouseOut(hint, { relatedTarget: document.body });
  act(() => jest.advanceTimersByTime(200));
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
  fireEvent.click(hint);
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  fireEvent.click(hint);
  fireEvent.click(screen.getByRole('button', { name: '다른 작업' }));
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
});

test('moving onto the explanation lets it remain readable, then leaving dismisses it', () => {
  jest.useFakeTimers();
  render(<Fixture />);
  const hint = screen.getByRole('button', { name: '비교 기준 방식 도움말' });
  fireEvent.mouseOver(hint);
  const tooltip = screen.getByRole('tooltip');
  fireEvent.mouseOut(hint, { relatedTarget: tooltip });
  fireEvent.mouseOver(tooltip);
  act(() => jest.advanceTimersByTime(200));
  expect(tooltip).toBeInTheDocument();
  fireEvent.mouseOut(tooltip, { relatedTarget: document.body });
  act(() => jest.advanceTimersByTime(200));
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
});
