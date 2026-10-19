import assert from 'node:assert/strict';
import { cornerAnnotations, cornerAtWindow } from './cornerAnnotations.js';

test('minimum-speed markers follow each measured trace, including zero, rather than apex', () => {
  const corner = { entry_pct: .2, exit_pct: .5, apex_pct: .3,
    target: { min_speed_kph: 0, exit_speed_kph: 70 }, reference: { min_speed_kph: 30, exit_speed_kph: 75 } };
  const rows = [{ distance_pct: .1, speed: -10, referenceSpeed: -10 }, { distance_pct: .25, speed: 50, referenceSpeed: 30 }, { distance_pct: .4, speed: 0, referenceSpeed: 40 }];
  const notes = cornerAnnotations(corner, rows, 1000);
  const minimum = notes.find(n => n.key === 'min_speed'), exit = notes.find(n => n.key === 'exit_speed');
  assert.equal(notes.length, 2);
  assert.equal(minimum.at, .4); assert.equal(minimum.refAt, .25);
  assert.equal(minimum.difference, -30); assert.equal(exit.difference, -5);
});

test('missing positions, traces and reference values stay unknown', () => {
  const notes = cornerAnnotations({ entry_pct: .2, exit_pct: .5, target: { min_speed_kph: 80 }, reference: {} }, [], 1000);
  assert.deepEqual(notes, []);
  assert.equal(cornerAnnotations(null, [], 1000).length, 0);
});

test('landmark differences wrap across the start line; sub-grid changes do not fill slots', () => {
  const notes = cornerAnnotations({ entry_pct:0, exit_pct:.5, target: { brake_start_pct: .002, brake_release_pct: .4 }, reference: { brake_start_pct: .997, brake_release_pct: .399 } }, [], 1000);
  assert.ok(Math.abs(notes.find(n => n.key === 'brake_start').difference - 5) < 1e-8);
  assert.ok(!notes.some(n => n.key === 'brake_release'));
});

test('five selected points cover different inputs and remain ranked by observed difference', () => {
  const corner = { entry_pct:.2, apex_pct:.3, exit_pct:.5,
    target:{brake_start_pct:.21,brake_release_pct:.31,full_throttle_pct:.4,throttle_first_pct:.35,min_speed_kph:70,exit_speed_kph:90,peak_brake:.9,coast_time_s:.5,steering_corrections:4,throttle_relifts:3},
    reference:{brake_start_pct:.2,brake_release_pct:.3,full_throttle_pct:.35,throttle_first_pct:.32,min_speed_kph:80,exit_speed_kph:100,peak_brake:.7,coast_time_s:0,steering_corrections:1,throttle_relifts:0}};
  const notes = cornerAnnotations(corner, [{distance_pct:.25,speed:70,referenceSpeed:80,throttle:0,brake:0,steering:20,referenceSteering:10}], 5000);
  assert.equal(notes.length, 5);
  assert.ok(new Set(notes.map(n => n.channel)).size >= 3);
  assert.deepEqual(notes.map(n => n.id), notes.map((_,i) => i+1));
  assert.ok(notes.every((n,i) => !i || notes[i-1].score >= n.score));
  const flat = cornerAnnotations({entry_pct:.2,apex_pct:.3,exit_pct:.5,target:{min_speed_kph:90,exit_speed_kph:110},reference:{min_speed_kph:90,exit_speed_kph:110}}, [], 5000);
  assert.deepEqual(flat, []);
});

test('position comparisons expose both actual endpoints; phase time exposes its measured interval', () => {
  const corner={entry_pct:.2,apex_pct:.3,exit_pct:.4,target:{brake_start_pct:.23,full_throttle_pct:.38},reference:{brake_start_pct:.2,full_throttle_pct:.35}};
  const notes=cornerAnnotations(corner,[{distance_pct:.2,delta:10},{distance_pct:.3,delta:10.2},{distance_pct:.4,delta:10.2}],100);
  assert.deepEqual(notes.find(n=>n.key==='brake_start').range,{from:.23,to:.2,type:'comparison'});
  assert.deepEqual(notes.find(n=>n.key==='full_throttle').range,{from:.38,to:.35,type:'comparison'});
  assert.deepEqual(notes.find(n=>n.key==='entry_loss').range,{from:.2,to:.3,type:'interval'});
});
test('invalid input coverage does not produce pedal findings from partial metrics', () => {
  const notes=cornerAnnotations({entry_pct:.2,exit_pct:.5,target:{inputs_usable:false,peak_brake:.9,brake_start_pct:.2},reference:{peak_brake:.5,brake_start_pct:.3}},[],5000);
  assert.deepEqual(notes,[]);
});
test('phase timing removes earlier accumulated gap and rejects missing delta samples', () => {
  const corner={entry_pct:.2,apex_pct:.3,exit_pct:.4,target:{},reference:{}};
  const rows=[{distance_pct:.2,delta:35},{distance_pct:.3,delta:35.2},{distance_pct:.4,delta:35.1}];
  const notes=cornerAnnotations(corner,rows,100);
  assert.ok(Math.abs(notes.find(n=>n.key==='entry_loss').difference-.2)<1e-8);
  assert.ok(Math.abs(notes.find(n=>n.key==='exit_loss').difference+.1)<1e-8);
  assert.deepEqual(cornerAnnotations(corner,[rows[0],{distance_pct:.3,delta:null},rows[2]],100),[]);
});
test('one-sided ABS is observed context, not a fabricated reference comparison', () => {
  const controls={abs:{target:{available:true,coverage:[[.2,.5]],events:[[.25,.26,.1]]}}};
  const [note]=cornerAnnotations({entry_pct:.2,apex_pct:.3,exit_pct:.5,target:{},reference:{}},[],1000,controls);
  assert.equal(note.key,'abs_active');
  assert.match(note.text,/기준 확인 필요/);
  assert.equal(note.difference,0);
});

test('legacy TC payloads never create a comparison point', () => {
  const signal={available:true,coverage:[[.2,.5]],events:[[.25,.26,.1]]};
  const notes=cornerAnnotations({entry_pct:.2,apex_pct:.3,exit_pct:.5,target:{},reference:{}},[],1000,{tc:{target:signal,reference:signal}});
  assert.deepEqual(notes,[]);
});

const corners = [{ id: 'C1', entry_pct: .1, exit_pct: .2 }, { id: 'C2', entry_pct: .4, exit_pct: .5 }, { id: 'C3', entry_pct: .6, exit_pct: .7 }];
test('viewport centre selects a new corner without needing to change viewport width', () => {
  assert.equal(cornerAtWindow(corners, [.35, .55], 'C1'), 'C2');
  assert.equal(cornerAtWindow(corners, [.55, .75], 'C2'), 'C3');
});
test('gaps choose the nearest interval and overlapping definitions retain current selection', () => {
  assert.equal(cornerAtWindow(corners, [.24, .26], 'C2'), 'C1');
  const overlap = [...corners, { id: 'C4', entry_pct: .42, exit_pct: .48 }];
  assert.equal(cornerAtWindow(overlap, [.44, .46], 'C4'), 'C4');
});
test('full lap and empty definitions do not select a phantom corner', () => {
  assert.equal(cornerAtWindow(corners, [0, 1], 'C2'), null);
  assert.equal(cornerAtWindow([], [.4, .5], 'C2'), null);
});
