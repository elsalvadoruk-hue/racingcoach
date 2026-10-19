import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CornerNames from './CornerNames';

const corners = [
  { id: 'C1', order: 1, entry_pct: .01, apex_pct: .03, exit_pct: .05, number: null, name: null, spoken: null, suggested_number: '1-2', suggested_name: null, labels: ['1', '2'] },
  { id: 'C2', order: 2, entry_pct: .1, apex_pct: .12, exit_pct: .14, number: null, name: null, spoken: null, suggested_number: null, suggested_name: null, labels: [] },
];
const reply = (data, ok = true) => Promise.resolve({ ok, json: () => Promise.resolve(data) });
afterEach(() => jest.restoreAllMocks());

test('fills the map suggestion, lets the driver correct it and confirms once', async () => {
  const fetchMock = jest.spyOn(global, 'fetch')
    .mockImplementationOnce(() => reply({ status: 'SUGGESTED', suggestion_status: 'SUGGESTED', kind: 'numbered', fit_median_m: 8.2, confirmed: false, corners }))
    .mockImplementationOnce(() => reply({ status: 'CONFIRMED', suggestion_status: 'SUGGESTED', kind: 'numbered', fit_median_m: 8.2, confirmed: true,
      corners: [{ ...corners[0], number: '1-2' }, { ...corners[1], number: '3', spoken: '삼번 코너' }] }));
  const onSaved = jest.fn();
  render(<CornerNames apiBase="http://api" filename="lap one.ibt" onSaved={onSaved} />);
  expect(await screen.findByText('확인 필요')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith('http://api/api/corner-names?filename=lap%20one.ibt');
  expect(screen.getByLabelText('자동 구간 1 코너 번호')).toHaveValue('1-2');
  expect(screen.getByLabelText('자동 구간 1 음성으로 부를 말')).toHaveAttribute('placeholder', '1번 코너');
  expect(screen.getByLabelText('자동 구간 2 음성으로 부를 말')).toHaveAttribute('placeholder', '다음 코너');
  await userEvent.type(screen.getByLabelText('자동 구간 2 코너 번호'), '3');
  await userEvent.type(screen.getByLabelText('자동 구간 2 음성으로 부를 말'), '삼번 코너');
  await userEvent.click(screen.getByRole('button', { name: '이대로 확정' }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
  const [url, options] = fetchMock.mock.calls[1];
  expect(url).toBe('http://api/api/corner-names');
  expect(JSON.parse(options.body)).toEqual({ filename: 'lap one.ibt', corners: [
    { id: 'C1', number: '1-2', name: '', spoken: '' }, { id: 'C2', number: '3', name: '', spoken: '삼번 코너' }] });
  expect(await screen.findByText('확정됨')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '수정 내용 확정' })).toBeInTheDocument();
});

test('explains when no suggestion can be made and shows a save error', async () => {
  jest.spyOn(global, 'fetch')
    .mockImplementationOnce(() => reply({ status: 'MAP_FIT_FAILED', suggestion_status: 'MAP_FIT_FAILED', fit_median_m: 40, confirmed: false, corners }))
    .mockImplementationOnce(() => reply({ detail: '트랙 ID가 없어 코너 번호를 저장할 수 없습니다.' }, false));
  render(<CornerNames apiBase="http://api" filename="a.ibt" />);
  expect(await screen.findByText(/공식 지도를 주행 궤적에 맞추지 못했습니다/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '지도 제안으로 다시 채우기' })).toBeDisabled();
  await userEvent.click(screen.getByRole('button', { name: '이대로 확정' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('트랙 ID가 없어');
});
