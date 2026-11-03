import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import VoiceLengthSelect from './VoiceLengthSelect';
import RoleVoiceSelect from './RoleVoiceSelect';

const library = { view: { packs: [{ key: 'brief@1', name: 'long short pack name', version: '1', roles: { spotter: 1 }, radioStyle: { length: 'short', voice: 'Fenrir' } }] } };
test('voice options show the speaker once while a separate control chooses length', () => {
  const onChange = jest.fn(), settings = { voice: 'Fenrir', voice_packs: { spotter: 'brief@1' } };
  render(<><RoleVoiceSelect role="spotter" library={library} settings={settings} onChange={onChange} defaults={[["Fenrir", 'Fenrir'], ['Aoede', 'Aoede']]} />
    <VoiceLengthSelect scope="spotter" settings={settings} library={library} onChange={onChange} /></>);
  expect(screen.getByRole('combobox', { name: '스포터 목소리' })).toHaveValue('Fenrir');
  expect(screen.queryByRole('option', { name: /long short/ })).not.toBeInTheDocument();
  expect(screen.getByRole('radio', { name: '짧은 무전' })).toHaveAttribute('aria-checked', 'true');
  fireEvent.click(screen.getByRole('radio', { name: '기본' }));
  expect(onChange).toHaveBeenCalledWith({ voice: 'Fenrir', voice_packs: {}, custom_pack: null });
});
test('missing packs and active playback disable the corresponding length actions', () => {
  const { rerender } = render(<VoiceLengthSelect scope="pitwall" settings={{}} library={library} onChange={jest.fn()} />);
  expect(screen.getByRole('radio', { name: '짧은 무전' })).toBeDisabled();
  rerender(<VoiceLengthSelect scope="spotter" settings={{ voice: 'Fenrir' }} library={library} disabled onChange={jest.fn()} />);
  expect(screen.getByRole('radio', { name: '기본' })).toBeDisabled();
  expect(screen.getByRole('radio', { name: '짧은 무전' })).toBeDisabled();
});
