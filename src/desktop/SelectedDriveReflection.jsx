import React, { useState } from 'react';
import { Button } from '../components/ui/button';
import { DriverFeelingCard } from './DriverJournal';
import useDriverJournal from './useDriverJournal';

const validId = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
function Reflection({base,game,driveId}) {
  const journal=useDriverJournal(base,game,0,driveId);
  return <DriverFeelingCard journal={journal} fixed />;
}

// Bind a feeling to this analysis's ledger IDs, never the latest global drive.
export default function SelectedDriveReflection({base,game,link}) {
  const ids=Array.isArray(link?.drive_ids)?link.drive_ids.filter(validId):[];
  const signature=ids.join('|');
  const [choice,setChoice]=useState({signature:'',id:''});
  const selected=ids.length===1?ids[0]:choice.signature===signature&&ids.includes(choice.id)?choice.id:null;
  if (!ids.length) return <p className="driver-journal-note driver-reflection">이 파일의 주행 기록이 연결되면 체감을 남길 수 있습니다.{link?.reason==='HISTORY_SAVE_FAILED'?' 기록 저장에 실패했습니다. 파일을 다시 분석해 주세요.':''}</p>;
  return <section className="driver-reflection" aria-label="선택한 파일의 체감">
    {ids.length>1?<div className="driver-journal-actions"><p>이 파일에 여러 주행이 있어요. 체감을 남길 주행을 선택하세요.</p>{ids.map((id,index)=>{
      const row=link?.drive_options?.find(item=>item.drive_id===id);
      return <Button key={id} variant={selected===id?'secondary':'outline'} onClick={()=>setChoice({signature,id})} aria-pressed={selected===id}>{row?.recorded_at?.slice(0,16).replace('T',' ') || `주행 ${index+1}`} · {row?.labels?.car || '기록 확인'}</Button>;
    })}</div>:null}
    {selected?<Reflection key={`${game}|${selected}`} base={base} game={game} driveId={selected}/>:null}
  </section>;
}
