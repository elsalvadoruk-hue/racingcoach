import React, { useLayoutEffect, useRef, useState } from 'react';

// Greedy packing preserves the caller's priority; only whole sections are shown.
export function fitSections(sections, height, columns = 2, gap = 10) {
  const used = Array(columns).fill(0), lanes = Array.from({ length: columns }, () => []);
  for (const section of sections) {
    const lane = used.indexOf(Math.min(...used));
    const required = section.height + (lanes[lane].length ? gap : 0);
    if (!Number.isFinite(required) || required <= 0 || used[lane] + required > height) continue;
    lanes[lane].push(section.key); used[lane] += required;
  }
  return lanes;
}

export default function FittedSections({ sections, label }) {
  const root = useRef(null), measurements = useRef(null);
  const [layout, setLayout] = useState(null);
  const canMeasure = typeof ResizeObserver !== 'undefined';
  useLayoutEffect(() => {
    if (!canMeasure) return;
    let frame;
    const measure = () => {
      const host = root.current, probe = measurements.current;
      if (!host?.clientHeight || !probe?.clientWidth) return;
      const columns = probe.clientWidth >= host.clientWidth - 1 ? 1 : 2;
      const rows = Array.from(probe.children).map(node => ({ key: node.dataset.fitKey, height: Math.ceil(node.getBoundingClientRect().height) }));
      const next = fitSections(rows, host.clientHeight, columns);
      setLayout(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
    };
    const observer = new ResizeObserver(() => { cancelAnimationFrame(frame); frame = requestAnimationFrame(measure); });
    observer.observe(root.current);
    for (const child of measurements.current.children) observer.observe(child);
    measure();
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [canMeasure, sections]);
  const lanes = canMeasure ? layout || [[], []] : [sections.map(section => section.key)];
  return <div className="fitted-sections" ref={root} aria-label={label}>
    {canMeasure && <div className="fit-probe-viewport" aria-hidden="true" inert=""><div className="fit-measure" ref={measurements}>
      {sections.map(section => <section className="fit-section" data-fit-key={section.key} key={section.key}>{section.content}</section>)}
    </div></div>}
    <div className="fit-grid" style={{ gridTemplateColumns: `repeat(${lanes.length},minmax(0,1fr))` }}>
      {lanes.map((keys, index) => <div className="fit-lane" key={index}>{keys.map(key => {
        const section = sections.find(item => item.key === key);
        return section ? <section className="fit-section" data-visible-section={key} key={key}>{section.content}</section> : null;
      })}</div>)}
    </div>
  </div>;
}
