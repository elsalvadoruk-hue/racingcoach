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
  expect(screen.getByRole('link', { name: '연결 도움말' })).toHaveAttribute('href', '/lmu-bridge-guide.html');
});

test('a missing connection file is prepared with one connect action and no build', async () => {
  requestJson.mockResolvedValueOnce({ state: 'ABI_MISMATCH', reader: 'bridge', buildable: true, message: 'SDK 변경' });
  requestJson.mockResolvedValueOnce({ state: 'READY', buildable: false, message: '빌드 완료' });
  render(<LmuBridgePanel />);
  const connect = await screen.findByRole('button', { name: '실시간 연결' });
  expect(screen.getByText(/연결에 필요한 파일은 앱이 자동으로 준비합니다/)).toBeVisible();
  fireEvent.click(connect);
  expect(await screen.findByText('빌드 완료')).toBeVisible();
  expect(requestJson).toHaveBeenLastCalledWith('/api/lmu/live/connect', { method: 'POST' });
  expect(screen.queryByRole('button', { name: '실시간 브리지 빌드' })).not.toBeInTheDocument();
});

test('preparing connection disables repeat requests and offers no compiler steps', async () => {
  requestJson.mockResolvedValue({ state: 'CONNECTION_PREPARING', message: '연결 파일을 받는 중입니다.' });
  render(<LmuBridgePanel />);
  expect(await screen.findByRole('button', { name: '연결 준비 중…' })).toBeDisabled();
  expect(screen.queryByText(/Visual Studio/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '실시간 브리지 빌드' })).not.toBeInTheDocument();
});

test('a missing SDK does not offer a build or claim the bridge is usable', async () => {
  requestJson.mockResolvedValue({ state: 'SDK_UNAVAILABLE', buildable: false, message: 'LMU 설치를 확인하세요' });
  render(<LmuBridgePanel />);
  expect(await screen.findByText('LMU 설치를 확인하세요')).toBeVisible();
  expect(screen.queryByRole('button', { name: '실시간 브리지 빌드' })).not.toBeInTheDocument();
  expect(screen.queryByText(/브리지를 사용할 수 있습니다/)).not.toBeInTheDocument();
});

test('the default reader explains automatic connection without a download step', async () => {
  requestJson.mockResolvedValue({ state: 'READY', reader: 'python', message: '준비 완료' });
  render(<LmuBridgePanel />);
  expect(await screen.findByText(/실시간 연결은 자동입니다/)).toBeVisible();
  expect(screen.queryByText(/인터넷에서 받습니다/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '실시간 연결' })).not.toBeInTheDocument();
});

test('a changed protocol asks for an app update without offering preparation', async () => {
  requestJson.mockResolvedValue({ state: 'CONNECTION_SDK_UNSUPPORTED', reader: 'python',
    message: 'LMU가 연결 방식을 바꿨습니다. 앱 업데이트가 필요합니다.' });
  render(<LmuBridgePanel />);
  expect(await screen.findByText('앱 업데이트 필요')).toBeVisible();
  expect(screen.queryByRole('button', { name: '실시간 연결' })).not.toBeInTheDocument();
  expect(screen.queryByText(/Visual Studio|파일을 받는 중/)).not.toBeInTheDocument();
});
