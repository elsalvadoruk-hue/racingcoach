import React from 'react';

export default function LocalSpeechCorrection({ detail }) {
  if (!detail?.corrections?.length && !detail?.correction_note) return null;
  return <div aria-label="질문 표현 보정">
    {detail.corrections?.length > 0 && <>
      <p>보정 문장: {detail.normalized_text}</p>
      {detail.corrections.map((change, index) => <p key={index}>
        {change.from} → {change.to} · {change.reason}
      </p>)}
    </>}
    {detail.correction_note && <p>{detail.correction_note}</p>}
  </div>;
}
