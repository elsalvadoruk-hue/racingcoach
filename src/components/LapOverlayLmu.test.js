import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import LapOverlay from './LapOverlay';

const laps = [{ lap_number: 2, lap_time_s: 117, measurement_eligible: true }];
const other = 'a'.repeat(24);
const extra = { source: 'IBT', filename: other, lap: 2 };
const baseKey = `ibt:${other}#2`;
const response = body => ({
  base_key: body.base_key, laps: [], channels: [], distance_pct: [0, 1],
  series: Object.fromEntries([...body.laps.map(String), baseKey].map(key => [key, {}])),
});

test('keeps an external reference as the base when its lap number also exists in the target file', async () => {
  const load = jest.fn(async body => response(body));
  render(<LapOverlay laps={laps} preferredLaps={[2]} preferredExtras={[extra]} preferredBaseKey={baseKey} load={load} />);
  await waitFor(() => expect(load).toHaveBeenCalled());
  expect(load.mock.calls[0][0]).toMatchObject({ laps: [2], extra: [extra], base_key: baseKey });
  expect(screen.getByTestId('overlay-extra')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '+1 빼기' }));
  await waitFor(() => expect(load.mock.calls.at(-1)[0]).toMatchObject({ extra: [], base_key: null }));
});

test('shows recording names and keeps unknown validity distinct from insufficient measurements', async () => {
  const loadLaps = jest.fn(async () => ({ laps: [
    { lap_number: 1, lap_time_s: 119, measurement_eligible: true, valid: null },
    { lap_number: 2, lap_time_s: 120, measurement_eligible: false, valid: null },
  ] }));
  render(<LapOverlay laps={laps} load={async body => response(body)} loadLaps={loadLaps}
    otherFiles={[{ name: other, display_name: 'Monza qualifying.duckdb', analyzable: true }]} />);
  expect(screen.getByRole('option', { name: 'Monza qualifying.duckdb' })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('다른 주행 파일'), { target: { value: other } });
  expect(await screen.findByRole('option', { name: /랩 1 .*유효성 확인 필요/ })).toBeEnabled();
  expect(screen.getByRole('option', { name: /랩 2 .*측정 자료 부족/ })).toBeDisabled();
});

test('LMU steering uses percent and gear renders discrete shifts without creating intermediate gears', async () => {
  const load = jest.fn(async () => ({ provider: 'LMU', base_key: '2',
    laps: [{ key: '2', lap: 2 }], distance_pct: [0, .5, 1], track_length_m: 5000,
    channels: [{ key: 'steering', label: '조향 입력', unit: '%' }, { key: 'gear', label: '기어', unit: 'gear' }],
    series: { 2: { steering: [-20, 0, 15], gear: [1, 2, 3] } },
    channel_support: { brake_ratio: { status: 'UNAVAILABLE', reason: '입력 처리 단계가 서로 다릅니다.' } },
  }));
  render(<LapOverlay laps={laps} preferredLaps={[2]} load={load} />);
  expect(await screen.findByText(/브레이크: 입력 처리 단계가 서로 다릅니다/)).toBeInTheDocument();
  expect(screen.getByTestId('overlay-trace-steering-2')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '기어' }));
  const gear = screen.getByTestId('overlay-trace-gear-2');
  expect(gear).toHaveAttribute('d', expect.stringMatching(/^M[\d.]+ [\d.]+H[\d.]+V[\d.]+H[\d.]+V[\d.]+$/));
  expect(gear.getAttribute('d')).not.toContain('L');
});
