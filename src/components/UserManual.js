import { Input } from './ui/input';
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from './ui/card';
import { Field, FieldLabel } from './ui/field';
import { Empty, EmptyHeader, EmptyDescription, EmptyTitle } from './ui/empty';
import { Button } from './ui/button';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { BookOpen } from 'lucide-react';
import LmuFeatureGuide, { lmuFeatures } from '../desktop/LmuFeatureGuide';

const blockText = (block) => typeof block === 'string' ? block : block.component === 'lmu-features'
  ? lmuFeatures.map(([name, status, , note]) => `${name} ${status} ${note}`).join(' ')
  : [block.h, block.tip, ...(block.steps || []), ...(block.list || []), ...(block.say || [])].filter(Boolean).join(' ');

function ManualBlock({ block, onNavigate }) {
  if (typeof block === 'string') return <p>{block}</p>;
  if (block.component === 'lmu-features') return <LmuFeatureGuide onNavigate={onNavigate} />;
  if (block.h) return <h4>{block.h}</h4>;
  if (block.steps) return <ol>{block.steps.map(step => <li key={step}>{step}</li>)}</ol>;
  if (block.list) return <ul>{block.list.map(item => <li key={item}>{item}</li>)}</ul>;
  if (block.say) return <ul className="coach-manual-say" aria-label="음성 예시">{block.say.map(line => <li key={line}>{line}</li>)}</ul>;
  if (block.tip) return <p className="coach-manual-tip"><strong>알아 두기</strong>{block.tip}</p>;
  return null;
}

export default function UserManual({ onNavigate, hasAnalysis, sections: allSections, heading = '기능별 사용법',
  intro = '2.0.3 데스크톱 화면 기준입니다. 처음이면 1장, 비교 조작은 2~3장, 셋업 실험은 4~6장, 기록은 7장, 주행 중 음성은 8~12장, 폴더와 키 설정은 18장을 보세요.',
  emptyHint = '주행 분석이 없어도 매뉴얼을 읽을 수 있습니다. 기능을 사용하려면 IBT 파일을 선택하세요.' }) {
  const [search, setSearch] = useState('');
  const [activeChapter, setActiveChapter] = useState(allSections[0]?.id);
  const searchRef = useRef(null);
  const query = search.trim().toLocaleLowerCase();
  const sections = useMemo(() => allSections.filter(section => !query || [section.title, ...section.blocks.map(blockText)].join(' ').toLocaleLowerCase().includes(query)), [query, allSections]);
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver(entries => {
      const current = entries.find(entry => entry.isIntersecting);
      if (current) setActiveChapter(current.target.id.replace('guide-', ''));
    }, { rootMargin: '-10% 0px -70% 0px', threshold: 0 });
    sections.forEach(section => { const element = document.getElementById(`guide-${section.id}`); if (element) observer.observe(element); });
    return () => observer.disconnect();
  }, [sections]);
  return <Card asChild><article className="coach-manual" aria-label="사용 매뉴얼">
    <CardHeader className="coach-manual-intro p-0">
      <div><CardTitle asChild><h2 className="flex items-center gap-2"><BookOpen aria-hidden="true" size={18} />{heading}</h2></CardTitle><CardDescription>{intro}</CardDescription></div>
      <Field className="coach-manual-search"><FieldLabel htmlFor="manual-search" className="sr-only">사용 가이드 검색</FieldLabel><Input ref={searchRef} id="manual-search" aria-label="사용 가이드 검색" type="search" placeholder="기능이나 문제를 검색하세요" value={search} onChange={event => setSearch(event.target.value)} /></Field>
    </CardHeader>
    {!hasAnalysis && <p className="mt-4 text-sm text-muted-foreground">{emptyHint}</p>}
    {sections.length ? <CardContent className="coach-manual-layout p-0">
      <nav aria-label="매뉴얼 목차">{sections.map(({ id, title }) => <a key={id} href={`#guide-${id}`} aria-current={activeChapter === id ? 'location' : undefined} onClick={() => setActiveChapter(id)}>{title}</a>)}</nav>
      <div className="coach-manual-chapters">{sections.map(({ id, title, blocks, action }) => <section key={id} id={`guide-${id}`}>
        <h3>{title}</h3><div className="coach-manual-copy">{blocks.map((block, index) => <ManualBlock key={index} block={block} onNavigate={onNavigate} />)}</div>
        {action && <Button variant="outline" disabled={!hasAnalysis && !['settings', 'live-coach'].includes(action[1])} onClick={() => onNavigate(action[1])} className="mt-5">{action[0]}</Button>}
      </section>)}</div>
    </CardContent> : <Empty className="py-12"><EmptyHeader><EmptyTitle>찾는 사용법이 없습니다.</EmptyTitle><EmptyDescription>다른 검색어를 입력하거나 전체 가이드를 확인하세요.</EmptyDescription></EmptyHeader><Button variant="outline" onClick={() => { setSearch(''); searchRef.current?.focus(); }}>전체 가이드 보기</Button></Empty>}
  </article></Card>;
}
