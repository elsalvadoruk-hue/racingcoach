import React from 'react';
import { render, screen } from '@testing-library/react';
import RacingLine from './RacingLine';
import Measurements from './Measurements';
import Insights from './Insights';

const line = { quality: 'ok', entry_m: 1, apex_m: -2, exit_m: .4,
  side_words: { entry: '바깥', apex: '안쪽', exit: '바깥' }, path_length_delta_m: -1.6 };

test('measured directions, small offsets and shorter path use Korean descriptions', () => {
  render(<RacingLine line={line} />);
  expect(screen.getByRole('region', { name: '주행선 차이' })).toHaveTextContent(
    '제동 시작에서 기준보다 1.0 m 바깥쪽입니다. 최저 속도 지점에서 기준보다 2.0 m 안쪽입니다. 탈출에서 기준과 비슷합니다. 코너 안 주행 거리가 기준보다 1.6 m 짧습니다.');
});

test('poor alignment and missing data do not turn into similar paths', () => {
  const { container, rerender } = render(<RacingLine line={{ ...line, quality: 'low' }} />);
  expect(container).toBeEmptyDOMElement();
  rerender(<RacingLine line={{ quality: 'ok', entry_m: null, apex_m: NaN, exit_m: null }} />);
  expect(container).toBeEmptyDOMElement();
});

test('zero is observed, exactly half a metre is described, unknown turn direction is omitted', () => {
  render(<RacingLine line={{ quality: 'ok', entry_m: 2, apex_m: 0, exit_m: .5,
    side_words: { exit: '바깥쪽' }, path_length_delta_m: .5 }} />);
  const region = screen.getByRole('region', { name: '주행선 차이' });
  expect(region).not.toHaveTextContent('제동 시작에서');
  expect(region).toHaveTextContent('최저 속도 지점에서 기준과 비슷합니다.');
  expect(region).toHaveTextContent('탈출에서 기준보다 0.5 m 바깥쪽입니다.');
  expect(region).toHaveTextContent('코너 안 주행 거리가 기준보다 0.5 m 깁니다.');
});

test('selected corner line is connected to both detailed measurements and comparison sidebar', () => {
  const corner = { id: 'C1', delta_s: .1, target: {}, reference: {}, line };
  const { rerender } = render(<Measurements corner={corner} />);
  expect(screen.getByRole('region', { name: '주행선 차이' })).toBeInTheDocument();
  rerender(<Insights corner={corner} corners={[corner]} names={{}} notes={[]} onPractice={jest.fn()} />);
  expect(screen.getByRole('region', { name: '주행선 차이' })).toBeInTheDocument();
});
