import React from 'react';

// Shared by the desktop settings and the original voice panel.
export default function VoiceVolumeSlider({ value, onChange, className = '', ...props }) {
  return <input {...props} type="range" min="0" max="130" step="1" value={value}
    data-boosted={value > 100} aria-valuetext={`${value}%${value > 100 ? ' · 증폭' : ''}`}
    className={`voice-volume-slider ${className}`} onChange={event => onChange(Number(event.target.value))} />;
}
