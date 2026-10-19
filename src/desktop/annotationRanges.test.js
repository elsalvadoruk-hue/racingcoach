import assert from 'node:assert/strict';
import { annotationRange } from './annotationRanges.js';

test('a reversed target/reference order preserves the true endpoints while clipping', () => {
  const note={range:{from:.4,to:.2,type:'comparison'}};
  const span=annotationRange(note,[250,500],1000);
  assert.ok(Math.abs(span.from-400)<1e-8 && Math.abs(span.to-200)<1e-8);
  assert.equal(span.left,250); assert.ok(Math.abs(span.right-400)<1e-8);
  assert.equal(span.fromVisible,true); assert.equal(span.toVisible,false);
});
test('crossing the start line renders only the short range on either end of the lap', () => {
  const note={range:{from:.002,to:.997,type:'comparison'}};
  for(const view of [[0,20],[980,1000]]){
    const span=annotationRange(note,view,1000);
    assert.ok(span.right-span.left <= 5.001);
    assert.ok(span.left>=view[0] && span.right<=view[1]);
  }
  assert.equal(annotationRange(note,[400,600],1000),null);
});
test('phase intervals retain their measured extent even when longer than half a lap', () => {
  const span=annotationRange({range:{from:.1,to:.8,type:'interval'}},[0,1000],1000);
  assert.equal(span.left,100); assert.equal(span.right,800);
});
test('missing, zero-width or offscreen endpoints never invent a comparison range', () => {
  for(const range of [undefined,{from:null,to:.3},{from:.2,to:.2},{from:.2,to:.3}]){
    assert.equal(annotationRange({range},[700,800],1000),null);
  }
});
