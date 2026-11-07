import React from 'react';
import { render, screen } from '@testing-library/react';
import DrivingLearning from './DrivingLearning';
import { readAlerts, alertGroups, voiceEventAllowed } from '../lib/voiceAlerts';

test('learning and unsupported records explain withholding without inventing a result',()=>{
  const view=render(<DrivingLearning learning={{state:'LEARNING',observations:12,eligible:0,rejections:{INPUT_MISSING:2}}} />);
  expect(screen.getByText('반복 비교를 모으고 있습니다')).toBeInTheDocument();
  expect(screen.getByText('저장 구간 12회 · 주변 차량까지 확인된 구간 0회')).toBeInTheDocument();
  expect(screen.getByText('필수 채널 없음 · 2회')).toBeInTheDocument();
  view.rerender(<DrivingLearning learning={{state:'UNSUPPORTED'}} />);
  expect(screen.getByText('이 기록은 필요한 채널이 부족합니다')).toBeInTheDocument();
});

test('all four result kinds show support and observed effect',()=>{
  const common={state:'CONFIRMED',window:'window',members:['C1'],pairs:6,validation_pairs:3,gain_s:.24};
  render(<DrivingLearning learning={{state:'CONFIRMED',observations:16,eligible:16,
    technique:[{...common,text:'제동 해제 반복 이득'}],settings:[{...common,text:'ABS 관측 비교'}],
    chains:[{...common,members:['C1','C2'],text:'연결 구간 반복 이득'}],impacts:[{...common,text:'충격 비교',kerb_verified:false}]}} />);
  expect(screen.getByText('C1 → C2')).toBeInTheDocument();
  expect(screen.getByText('ABS 관측 비교')).toBeInTheDocument();
  expect(screen.getAllByText(/관측 이득 0.24초/)).toHaveLength(4);
  expect(screen.getByText(/연석 여부 미확인/)).toBeInTheDocument();
});

test('stale live result disappears when disconnected',()=>{
  render(<DrivingLearning connected={false} learning={{state:'CONFIRMED',technique:[{text:'stale advice'}]}} />);
  expect(screen.queryByText('stale advice')).not.toBeInTheDocument();
  expect(screen.getByText('주행 대기')).toBeInTheDocument();
});

test('new categories have per-game defaults, migrate and filter held events',()=>{
  for(const key of ['learned_technique','learned_chain','learned_impact']) {
    expect(readAlerts('pitwall','iracing',{level:'all'})[key]).toBe(true);
    expect(readAlerts('pitwall','iracing',{level:'all',alerts:{[key]:false}})[key]).toBe(false);
    expect(readAlerts('pitwall','lmu',{level:'all'})[key]).toBeUndefined();
    expect(voiceEventAllowed({key},{},{[key]:false})).toBe(false);
  }
  expect(alertGroups('pitwall','iracing').flatMap(g=>g.items).filter(i=>i.id.startsWith('learned_'))).toHaveLength(3);
});
