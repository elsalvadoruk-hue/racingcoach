import React from 'react';
import { render, screen } from '@testing-library/react';
import FuelStrategy from './FuelStrategy';

const view = { fuel_l: 31.903, fuel_l_per_lap: 3.339, fuel_observed_laps: 1, remaining_laps: 9.97,
  finish_method: 'TIMED_ESTIMATE', fuel_save_mode: 'finish', reserve_laps: .5, fuel_reserve_l: 1.67,
  fuel_finish_shortfall_l: 1.39, fuel_goal_save_pct: 4.2, fuel_save_target_l_per_lap: 3.2,
  fuel_to_add_l: 3.06, stops_needed: 1 };

test('a single observed lap explains saving and refuelling as alternatives with different reserve goals', () => {
  render(<FuelStrategy view={view} />);
  expect(screen.getByText(/관측 1랩 · 3.339 L\/랩/)).toBeVisible();
  expect(screen.getByText(/잠정 계산/)).toBeVisible();
  expect(screen.getByText(/무급유 대안.*4.2%/)).toBeVisible();
  expect(screen.getByText(/급유 대안.*3.06 L.*1회/)).toBeVisible();
  expect(screen.getByText(/완주 절약 목표에는 제외, 급유량에는 포함/)).toBeVisible();
  expect(screen.getByText(/약 1.39 L 부족/)).toBeVisible();
});

test('reserve target uses the server goal percentage rather than the finish-only percentage', () => {
  render(<FuelStrategy view={{ ...view, fuel_save_mode: 'reserve', fuel_goal_save_pct: 8.7, fuel_save_pct: 4.2 }} />);
  expect(screen.getByText(/무급유 대안.*8.7%/)).toBeVisible();
  expect(screen.getByText(/절약 목표와 급유량에 포함/)).toBeVisible();
  expect(screen.queryByText(/4.2%/)).not.toBeInTheDocument();
});

test('zero fuel stays zero while missing fuel or distance suppress stale strategy numbers', () => {
  const { rerender } = render(<FuelStrategy view={{ ...view, fuel_l: 0 }} />);
  expect(screen.getByText(/현재 연료 0.00 L/)).toBeVisible();
  for (const patch of [{ fuel_l: null }, { remaining_laps: null }, { fuel_l_per_lap: null }, { fuel_observed_laps: 0 }]) {
    rerender(<FuelStrategy view={{ ...view, ...patch }} />);
    expect(screen.getByText(/확인되면 절약과 급유 대안/)).toBeVisible();
    expect(screen.queryByText(/4.2%/)).not.toBeInTheDocument();
  }
});

test('disabled saving, infeasible reserve and unknown timed distance are explicit', () => {
  const { rerender } = render(<FuelStrategy view={{ ...view, fuel_save_mode: 'off' }} />);
  expect(screen.getByText('절약 안내는 꺼져 있습니다.')).toBeVisible();
  rerender(<FuelStrategy view={{ ...view, fuel_save_mode: 'reserve', fuel_goal_save_pct: 35,
    fuel_save_target_l_per_lap: null, finish_may_extend: true }} />);
  expect(screen.getByText(/35.0%.*안내 범위를 벗어/)).toBeVisible();
  expect(screen.getByText(/한 랩 달라질/)).toBeVisible();
  rerender(<FuelStrategy view={{ energy_pct: 0, remaining_laps: null }} />);
  expect(screen.getByText(/완주 거리 미확인/)).toBeVisible();
  expect(screen.queryByText(/현재 연료 0.00/)).not.toBeInTheDocument();
});
