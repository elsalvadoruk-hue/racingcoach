import { fireEvent, render, screen } from '@testing-library/react';
import { requestJson } from '../analysis/analysisClient';
import { LmuBridgePanel } from './LmuWorkspacePanel';

jest.mock('../analysis/analysisClient', () => ({
  ...jest.requireActual('../analysis/analysisClient'),
  requestJson: jest.fn(),
}));

beforeEach(() => requestJson.mockReset());

test('a ready bundled bridge guides the driver to connect without offering a build', async () => {
  requestJson.mockResolvedValue({ state: 'READY', buildable: false, message: '브리지 준비됨' });
  render(<LmuBridgePanel />);
  expect(await screen.findByText(/LMU를 실행하고 차에 탄 뒤 음성 코치에서/)).toBeVisible();
  expect(screen.queryByRole('button', { name: '실시간 브리지 빌드' })).not.toBeInTheDocument();
  expect(requestJson).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('link', { name: '브리지 사용법' })).toHaveAttribute('href', '/lmu-bridge-guide.html');
});

test('an SDK mismatch explains and preserves the manual build fallback', async () => {
  requestJson.mockResolvedValueOnce({ state: 'ABI_MISMATCH', buildable: true, message: 'SDK 변경' });
  requestJson.mockResolvedValueOnce({ state: 'READY', buildable: false, message: '빌드 완료' });
  render(<LmuBridgePanel />);
  const build = await screen.findByRole('button', { name: '실시간 브리지 빌드' });
  expect(screen.getByText(/현재 LMU SDK에 맞는 브리지가 없습니다/)).toBeVisible();
  fireEvent.click(build);
  expect(await screen.findByText('빌드 완료')).toBeVisible();
  expect(requestJson).toHaveBeenLastCalledWith('/api/lmu/live/bridge/build', { method: 'POST', timeoutMs: 180000 });
  expect(screen.queryByRole('button', { name: '실시간 브리지 빌드' })).not.toBeInTheDocument();
});

test('a missing SDK does not offer a build or claim the bridge is usable', async () => {
  requestJson.mockResolvedValue({ state: 'SDK_UNAVAILABLE', buildable: false, message: 'LMU 설치를 확인하세요' });
  render(<LmuBridgePanel />);
  expect(await screen.findByText('LMU 설치를 확인하세요')).toBeVisible();
  expect(screen.queryByRole('button', { name: '실시간 브리지 빌드' })).not.toBeInTheDocument();
  expect(screen.queryByText(/브리지를 사용할 수 있습니다/)).not.toBeInTheDocument();
});
