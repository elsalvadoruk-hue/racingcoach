import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import CustomVoicePacks from './CustomVoicePacks';
import RoleVoiceSelect from './RoleVoiceSelect';
const pack = { key:'coach@1', name:'코치팩', version:'1', entries:1, roles:{coach:1}, baselineKnown:true, updates:{newCount:2} };
const library = () => ({view:{packs:[pack]}, install:jest.fn(), remove:jest.fn(), preview:jest.fn().mockResolvedValue({text:'미리 듣기 문장',audio_base64:'data:audio/wav;base64,QQ=='}),updates:jest.fn(),reload:jest.fn()});

test('a partial role pack appears only in its role selector and preserves other choices',()=>{
  const onChange=jest.fn(), lib=library(), settings={voice:'Fenrir',voice_packs:{spotter:'spot@1',strategy:'fuel@1'}};
  const {rerender}=render(<RoleVoiceSelect role="coach" library={lib} settings={settings} onChange={onChange} defaults={[["",'기본']]} />);
  fireEvent.change(screen.getByLabelText('주행 코치 목소리'),{target:{value:'pack:coach@1'}});
  expect(onChange).toHaveBeenCalledWith({voice_packs:{spotter:'spot@1',strategy:'fuel@1',coach:'coach@1'},custom_pack:'spot@1'});
  rerender(<RoleVoiceSelect role="race" library={lib} settings={settings} onChange={onChange} defaults={[["",'기본']]} />);
  expect(screen.queryByRole('option',{name:/코치팩/})).not.toBeInTheDocument();
});
test('unapply and installed-file deletion are separate explicit actions',async()=>{
  const lib=library();render(<CustomVoicePacks library={lib} settings={{voice_packs:{coach:'coach@1'}}}/>);
  fireEvent.click(screen.getByText('음성팩 설치·관리 · 추가 제작 알림'));
  fireEvent.click(screen.getByRole('button',{name:'적용 해제·삭제'}));
  expect(screen.getByRole('dialog')).toHaveTextContent('원본 ZIP·폴더는 삭제하지 않습니다');
  fireEvent.click(screen.getByRole('button',{name:'적용만 해제 · 파일 보관'}));
  await waitFor(()=>expect(lib.remove).toHaveBeenCalledWith('coach@1',false));
  await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button',{name:'적용 해제·삭제'}));fireEvent.click(screen.getByRole('button',{name:'설치 파일까지 삭제'}));
  await waitFor(()=>expect(lib.remove).toHaveBeenCalledWith('coach@1',true));
});
test('installation errors leave settings intact and show the actual reason',async()=>{
  const lib=library();lib.install.mockRejectedValue(Error('해시 불일치'));
  render(<CustomVoicePacks library={lib} settings={{voice_packs:{coach:'coach@1'}}}/>);
  fireEvent.click(screen.getByText('음성팩 설치·관리 · 추가 제작 알림'));
  fireEvent.change(screen.getByLabelText('커스텀 음성팩 ZIP'),{target:{files:[new File(['bad'],'bad.zip')]}});
  expect(await screen.findByText('해시 불일치')).toBeInTheDocument();expect(lib.remove).not.toHaveBeenCalled();
});
test('preview does not select or apply a pack',async()=>{
  const lib=library();render(<CustomVoicePacks library={lib} settings={{}}/>);fireEvent.click(screen.getByText('음성팩 설치·관리 · 추가 제작 알림'));
  fireEvent.click(screen.getByRole('button',{name:'미리 듣기'}));expect(await screen.findByText('미리 듣기 문장')).toBeInTheDocument();
  expect(lib.preview).toHaveBeenCalledWith('coach@1');expect(lib.remove).not.toHaveBeenCalled();expect(lib.install).not.toHaveBeenCalled();
});
