import React, { useState } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import VoiceAlertSettings from './VoiceAlertSettings';
import { defaultAlerts, readAlerts, voiceEventAllowed, alertCategory } from '../lib/voiceAlerts';

test('migration keeps explicit choices, legacy traffic/pitbox and the report scope', () => {
  const spotter = readAlerts('spotter', 'iracing', { traffic_enabled: false, pit_box_enabled: false, formation_calls: 0,
    alerts: { hazard: false, lap_time: false, yellow: true, unknown: false } });
  expect(spotter).toMatchObject({ class_approach: false, lapping: false, close: false, pit_box: false, formation: false, hazard: false, lap_time: false, yellow: true });
  expect(spotter.unknown).toBeUndefined();
  expect(readAlerts('pitwall', 'iracing', { level: 'important' })).toMatchObject({ battle: false, corner: false, fuel_critical: true });
  expect(readAlerts('pitwall', 'iracing', { level: 'all', alerts: { tc: false } })).toMatchObject({ battle: true, corner: true, tc: false });
  expect(readAlerts('pitwall', 'lmu', { level: 'all' }).arb).toBeUndefined();
});

test('bulk choices include collapsed groups and examples never make audio requests', () => {
  function Harness() {
    const [alerts, change] = useState(defaultAlerts('spotter'));
    return <VoiceAlertSettings scope="spotter" alerts={alerts} onChange={change} />;
  }
  render(<Harness />);
  const panel = screen.getByRole('region', { name: '스포터 상황별 음성 안내' });
  fireEvent.click(within(panel).getByRole('button', { name: '전체 끄기' }));
  expect(within(panel).getAllByRole('switch', { hidden: true }).every(s => s.getAttribute('aria-checked') === 'false')).toBe(true);
  expect(screen.getByLabelText('클리어')).toBeDisabled();
  fireEvent.click(within(panel).getByRole('button', { name: '전체 켜기' }));
  expect(within(panel).getAllByRole('switch', { hidden: true }).every(s => s.getAttribute('aria-checked') === 'true')).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '좌우 차량 예시' }));
  expect(screen.getByText('“왼쪽 차량.”')).toBeVisible();
  fireEvent.click(within(panel).getByRole('button', { name: '기본값으로' }));
  expect(screen.getByLabelText('피트박스 카운트다운')).not.toBeChecked();
});

test('attached categories take precedence over the containing event key', () => {
  expect(alertCategory('setup_front_arb_live')).toBe('pitwall.arb');
  expect(alertCategory('control_bias_forward')).toBe('pitwall.bbal');
  expect(voiceEventAllowed({ key: 'lap_time', alert_categories: ['spotter.position'] }, { lap_time: false, position: true })).toBe(true);
  expect(voiceEventAllowed({ key: 'lap_time', alert_categories: ['spotter.lap_time', 'spotter.position'] }, { position: false })).toBe(false);
});
