import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import LmuWorkspacePanel from './LmuWorkspacePanel';
import LmuFeatureGuide from './LmuFeatureGuide';
import { desktopDestination } from './desktopNavigation';

test('the LMU setup route shows the coming soon card and returns to the summary', () => {
  const onNavigate = jest.fn();
  render(<LmuWorkspacePanel workspace="setup" onNavigate={onNavigate} />);
  const section = screen.getByRole('region', { name: 'LMU 셋업 실험 추후 공개' });
  expect(within(section).getByText('셋업 실험 · 추후 공개')).toBeInTheDocument();
  expect(within(section).getByText('LMU 셋업 실험은 준비 중입니다. 다음 업데이트에서 공개합니다.')).toBeInTheDocument();
  expect(screen.queryByLabelText('기준 A 주행')).not.toBeInTheDocument();
  fireEvent.click(within(section).getByRole('button', { name: '주행 요약으로 가기' }));
  expect(onNavigate).toHaveBeenCalledWith('summary');
});

test.each(['ab-manager', 'briefing', 'engineer-briefing', 'setup-decision', 'decision', 'tire-snapshot', 'ride-aero', 'damper-velocity', 'gg-diagram'])(
  'the setup destination %s reaches the same coming soon screen', destination => {
    const route = desktopDestination(destination);
    expect(route.workspace).toBe('setup');
    render(<LmuWorkspacePanel workspace={route.workspace} onNavigate={jest.fn()} />);
    expect(screen.getByRole('region', { name: 'LMU 셋업 실험 추후 공개' })).toBeInTheDocument();
    expect(screen.queryByLabelText('기준 A 주행')).not.toBeInTheDocument();
  }
);

test('both LMU setup features are marked coming soon and keep their navigation', () => {
  const onNavigate = jest.fn();
  render(<LmuFeatureGuide onNavigate={onNavigate} />);
  for (const name of ['셋업 조회·A/B 변경 항목', '셋업 추천·효과 학습']) {
    const row = screen.getByRole('row', { name: new RegExp(name) });
    expect(within(row).getByText('추후 공개')).toHaveClass('text-foreground');
    fireEvent.click(within(row).getByRole('button', { name: `${name} 열기` }));
  }
  expect(onNavigate.mock.calls).toEqual([['setup'], ['setup']]);
});
