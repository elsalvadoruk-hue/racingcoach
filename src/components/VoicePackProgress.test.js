import React from 'react';
import {render,screen} from '@testing-library/react';
import VoicePackProgress from './VoicePackProgress';

test('a completed transfer still explains that validation follows, without claiming installation success',()=>{
  render(<VoicePackProgress operation={{kind:'install',state:'RUNNING',stage:'uploading',done:1048576,total:1048576,observedAt:Date.now()}}/>);
  expect(screen.getByRole('status')).toHaveTextContent('파일 전송 100%');
  expect(screen.getByRole('status')).toHaveTextContent('전송 후 음성 검사와 복사가 이어집니다');
  expect(screen.getByRole('status')).not.toHaveTextContent('설치 완료');
});

test('selection verification stays visible outside disabled controls and reports failure',()=>{
  const {rerender}=render(<VoicePackProgress operation={{kind:'selection',state:'RUNNING',stage:'checking',done:37,elapsed_s:5,observedAt:Date.now()}}/>);
  expect(screen.getByRole('status',{name:'목소리 적용 현황'})).toHaveTextContent('목소리 적용 중');
  expect(screen.getByRole('status')).toHaveTextContent('37개 확인');
  rerender(<VoicePackProgress operation={{kind:'selection',state:'FAILED',error:'음성 파일 해시 불일치',elapsed_s:5}}/>);
  expect(screen.getByRole('status')).toHaveTextContent('목소리를 적용하지 못했습니다');
  expect(screen.getByRole('status')).toHaveTextContent('음성 파일 해시 불일치');
});
