import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AppSettings from './AppSettings';

const base = {
  version: '1.0.0', data_dir: 'C:\\coach\\data', settings: { telemetry_dirs: [], gcp_project_id: '', gcp_location: 'global', speech_region: 'us' },
  telemetry_folders: [{ path: 'C:\\Users\\me\\Documents\\iRacing\\telemetry', exists: true, added: false }],
  google: { key_present: false, key_project_id: null, key_account: null, project_id: null, ai_enabled: false, ai_switched_off: false },
  garage61: { token_present: false }, audio: { radio_clips: 50183, spotter_clips: 219 },
};
const reply = (data, ok = true) => Promise.resolve({ ok, json: () => Promise.resolve(data) });
afterEach(() => jest.restoreAllMocks());

test('shows the data folder and adds a telemetry folder', async () => {
  const withFolder = { ...base, settings: { ...base.settings, telemetry_dirs: ['D:\\laps'] },
    telemetry_folders: [...base.telemetry_folders, { path: 'D:\\laps', exists: true, added: true }] };
  const fetchMock = jest.spyOn(global, 'fetch').mockImplementationOnce(() => reply(base)).mockImplementationOnce(() => reply(withFolder));
  render(<AppSettings apiBase="http://api" />);
  expect(await screen.findByText('C:\\coach\\data')).toBeInTheDocument();
  expect(screen.getByText('iRacing 주행 코치 v1.0.0 · 데이터 폴더')).toBeInTheDocument();
  expect(screen.getByText('받아 둔 음성: 엔지니어 50,183개 · 스포터 219개')).toBeInTheDocument();
  expect(screen.getByText('키가 등록되지 않았습니다.')).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText('추가할 주행 파일 폴더'), 'D:\\laps');
  await userEvent.click(screen.getByRole('button', { name: '폴더 추가' }));
  expect(fetchMock).toHaveBeenLastCalledWith('http://api/api/settings', expect.objectContaining({ method: 'PUT', body: JSON.stringify({ telemetry_dirs: ['D:\\laps'] }) }));
  expect(await screen.findByRole('button', { name: 'D:\\laps 빼기' })).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('폴더를 추가했습니다.');
});

test('registers a key file without ever showing it and reports a rejected one', async () => {
  const keyed = { ...base, google: { ...base.google, key_present: true, key_project_id: 'my-project', key_account: 'coach@my-project.iam.gserviceaccount.com', ai_enabled: true } };
  const fetchMock = jest.spyOn(global, 'fetch').mockImplementationOnce(() => reply(base))
    .mockImplementationOnce(() => reply({ detail: 'Google Cloud 서비스 계정 키 파일이 아닙니다. 콘솔에서 받은 JSON 키 파일을 선택하세요.' }, false))
    .mockImplementationOnce(() => reply(keyed));
  render(<AppSettings apiBase="http://api" />);
  const input = await screen.findByLabelText('Google 서비스 계정 키 파일');
  const file = (text) => Object.assign(new File([text], 'key.json', { type: 'application/json' }), { text: () => Promise.resolve(text) });
  await userEvent.upload(input, file('{"not":"a key"}'));
  expect(await screen.findByRole('alert')).toHaveTextContent('서비스 계정 키 파일이 아닙니다');
  const secret = '{"type":"service_account","private_key":"SECRET"}';
  await userEvent.upload(input, file(secret));
  await waitFor(() => expect(screen.getByText(/키 등록됨 · 프로젝트 my-project/)).toBeInTheDocument());
  expect(fetchMock).toHaveBeenLastCalledWith('http://api/api/settings/google-key', expect.objectContaining({ method: 'PUT', body: JSON.stringify({ value: secret }) }));
  expect(document.body.textContent).not.toContain('SECRET');
  expect(screen.getByText('AI 연결: 사용 가능')).toBeInTheDocument();
});

test('saves the Garage61 token and clears the field', async () => {
  const fetchMock = jest.spyOn(global, 'fetch').mockImplementationOnce(() => reply(base))
    .mockImplementationOnce(() => reply({ ...base, garage61: { token_present: true } }));
  render(<AppSettings apiBase="http://api" />);
  const field = await screen.findByLabelText('Garage61 토큰');
  await userEvent.type(field, 'abc123');
  await userEvent.click(screen.getAllByRole('button', { name: '저장' })[1]);
  expect(fetchMock).toHaveBeenLastCalledWith('http://api/api/settings/garage61-token', expect.objectContaining({ method: 'PUT' }));
  expect(await screen.findByText('토큰 등록됨')).toBeInTheDocument();
  expect(field).toHaveValue('');
});
