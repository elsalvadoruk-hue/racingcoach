import React from 'react';
import { render, screen } from '@testing-library/react';
import SetupInfluenceNotice from './SetupInfluenceNotice';

const props = { game: 'iracing', external: ['reference.csv'], comparison: { delta: .114, setupInfluence: { status: 'POSSIBLE' } } };

test('a supported lap-wide possibility uses the approved copy and has no setup-change action', () => {
  render(<SetupInfluenceNotice {...props} />);
  expect(screen.getByRole('status', { name: '셋업 영향 가능성' })).toHaveTextContent('셋업 영향 가능성 있음');
  expect(screen.getByText('랩 전체')).toBeInTheDocument();
  expect(screen.getByText('코너에서 시간을 벌지만 직선에서 손실이 반복됩니다. 셋업 차이가 영향을 줬을 수 있습니다.')).toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});

test.each(['NOT_SHOWN', 'INSUFFICIENT', undefined])('unsupported or missing analysis stays quiet (%s)', status => {
  render(<SetupInfluenceNotice {...props} comparison={{ delta: .114, setupInfluence: { status } }} />);
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
});

test('switching to local or LMU cannot retain an old Garage61 notice', () => {
  const { rerender } = render(<SetupInfluenceNotice {...props} />);
  rerender(<SetupInfluenceNotice {...props} external={[]} />);
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  rerender(<SetupInfluenceNotice {...props} game="lmu" />);
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  rerender(<SetupInfluenceNotice {...props} comparison={{ delta: .114 }} />);
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
});

test.each([-.1, null, NaN, .01])('a comparison without meaningful total lap loss is silent (%s)', delta => {
  render(<SetupInfluenceNotice {...props} comparison={{ ...props.comparison, delta }} />);
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
});
