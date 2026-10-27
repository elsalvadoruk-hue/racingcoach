import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { readFileSync } from 'fs';
import { join } from 'path';
import { DriverFeelingCard, DriverChallengeCard } from './DriverJournal';
import { DriverStyleCard, DriverGrowthCard } from './DriverInsights';
import DriverResults from './DriverResults';

const driveId = 'a'.repeat(64);
const journal = {
  busy:false, error:'', message:'',
  data:{selected_drive_id:driveId, drives:[{drive_id:driveId, recorded_at:'2026-10-27T12:00:00Z', labels:{car:'GT3',track:'GP'}}],
    candidates:[{candidate_id:'b'.repeat(64), drive_id:driveId, title:'측정 페이스 3랩 재현', description:'새 독립 주행에서 측정 랩 3개 이상을 확인합니다.'}],
    challenge_history:[]},
};
let styles;
beforeEach(()=>{
  // CRA's CSS transform discards styles. Load the actual layout rules so this
  // regression covers the scene/card cascade as well as dialog composition.
  styles=document.createElement('style');
  styles.textContent=['DriverJournal.css','DriverReport.css'].map(file=>readFileSync(join(__dirname,file),'utf8')).join('\n');
  document.head.append(styles);
});
afterEach(()=>styles.remove());

/* eslint-disable testing-library/no-node-access -- CSS cascade checks need the dialog's actual body and footer nodes. */
function expectVerticalDialog(dialog,bodyClass,hasCard=true) {
  const layout=getComputedStyle(dialog);
  expect(layout.display).toBe('flex');
  expect(layout.flexDirection).toBe('column');
  expect(layout.overflow).toBe('hidden');
  const body=dialog.querySelector(`.${bodyClass}`);
  expect(body).not.toBeNull();
  expect(getComputedStyle(body).overflowY).toBe('auto');
  expect(getComputedStyle(body).minHeight).toBe('0');
  expect(getComputedStyle(body).flexShrink).toBe('1');
  // The full card must be contained in the body, never be an implicitly
  // positioned grid child beside the dialog title and footer.
  if(hasCard) {
    expect(body.children).toHaveLength(1);
    expect(body.firstElementChild).toHaveAttribute('aria-label');
  }
  const footer=within(dialog).getAllByRole('button',{name:'닫기',exact:true})[0].parentElement;
  expect(footer.parentElement).toBe(dialog);
  expect(getComputedStyle(footer).flexShrink).toBe('0');
}
/* eslint-enable testing-library/no-node-access */

test.each([
  ['도전 목표 상세','도전 상세',DriverChallengeCard],
  ['주행 체감 상세','체감 상세 보기',DriverFeelingCard],
])('%s keeps the full card between its title and closing footer',(title,trigger,Component)=>{
  render(<Component journal={journal} compact/>);
  fireEvent.click(screen.getByRole('button',{name:trigger}));
  expectVerticalDialog(screen.getByRole('dialog',{name:title}),'driver-journal-detail-body');
});

test.each([
  ['운전 성향 자세히',DriverStyleCard],
  ['주행 변화 자세히',DriverGrowthCard],
])('%s keeps the evidence body shrinkable without moving the close button',(title,Component)=>{
  render(<Component compact/>);
  fireEvent.click(screen.getByRole('button',{name:title}));
  expectVerticalDialog(screen.getByRole('dialog',{name:title}),'driver-insight-dialog-body');
});

test('the results detail shares the same bounded body and fixed closing footer',async()=>{
  const previous=global.fetch;
  global.fetch=jest.fn(async()=>({ok:true,json:async()=>({game:'iracing',period_days:0,results:[]})}));
  try {
    render(<DriverResults base="" game="iracing" period="0" compact/>);
    await screen.findByText('아직 보관한 최종 결과가 없어요');
    fireEvent.click(screen.getByRole('button',{name:'경기 결과 상세'}));
    expectVerticalDialog(screen.getByRole('dialog',{name:'보관한 경기 결과 상세'}),'driver-results-dialog-body',false);
  } finally { global.fetch=previous; }
});
