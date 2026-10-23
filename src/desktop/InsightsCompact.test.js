import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import Insights from './Insights';

const corner = { id: 'C7', label: '구간 7', delta_s: .6, phases: { braking: { delta_s: .29 }, rotation: { delta_s: -.01 }, drive: { delta_s: .33 }, straight_after: { delta_s: .02 } },
  handling: [{ kind: 'oversteer', phase: 'braking', laps: 1, of_laps: 10, time_cost_s: null }], handling_coverage: [{ classifiable: 0, unknown: 29 }] };
const notes = [1, 2, 3, 4, 5].map(id => ({ id, key: `n${id}`, label: `포인트 ${id}`, value: `${id} m`, text: '설명', jumpAt: .1 * id }));
const props = { corner, corners: [corner], names: {}, onJump: jest.fn(), practice: null, onPractice: jest.fn(), notes, activeNote: null, onNote: jest.fn() };

test('the narrow comparison-point panel is a short summary and the wide one shows everything', () => {
  const { rerender } = render(<Insights {...props} compact />);
  expect(screen.getByText('포인트 3')).toBeInTheDocument();
  expect(screen.queryByText('포인트 4')).not.toBeInTheDocument();
  expect(screen.getByText(/비교 포인트 모두 5개/)).toBeInTheDocument();
  expect(screen.queryByText('차 거동 · 내 다른 랩과 비교')).not.toBeInTheDocument();
  expect(screen.getByText('비교 자료 부족 29회')).toBeInTheDocument();
  fireEvent.click(screen.getByTestId('insight-note-2'));
  expect(props.onNote).toHaveBeenCalledWith(notes[1]);
  rerender(<Insights {...props} />);
  expect(screen.getByText('포인트 5')).toBeInTheDocument();
  expect(screen.getByText('차 거동 · 내 다른 랩과 비교')).toBeInTheDocument();
});
