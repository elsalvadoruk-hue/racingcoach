import React, { useEffect, useId, useState } from 'react';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '../components/ui/card';
import { Field, FieldGroup, FieldLabel } from '../components/ui/field';
import { NativeSelect, NativeSelectOption } from '../components/ui/native-select';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '../components/ui/table';
import { data, labRequest } from './sessionData';
import { LmuFeedback, LmuNoSource, useLmuResource } from './LmuAnalysisPanels';
import { isSelectableRecording } from '../lib/recordingFiles';

const reasonText = {
  SETUP_MISSING: '이 주행에는 셋업 메타데이터가 없습니다.', SETUP_UNAVAILABLE: '두 주행 중 셋업을 확인할 수 없는 기록이 있습니다.',
  METADATA_MISMATCH: '차량·트랙 정보가 달라 셋업을 비교하지 않았습니다.',
  SETUP_TOO_LARGE_OR_UNSUPPORTED: '기록된 셋업 형식을 지원하지 않습니다.', SETUP_INVALID_JSON: '기록된 셋업 형식을 읽을 수 없습니다.',
  SETUP_UNSUPPORTED_SCHEMA: '지원하는 셋업 항목이 없습니다.',
};
const stateLabel = { CHANGED: '변경', UNCHANGED: '동일', UNKNOWN: '확인 필요' };
const value = item => item?.status === 'AVAILABLE' ? item.stringValue : '확인 필요';

export default function LmuSetupPanel({ onChooseSource }) {
  const id = useId();
  const [files, setFiles] = useState([]), [reference, setReference] = useState(''), [filter, setFilter] = useState('ALL'), [filesError, setFilesError] = useState('');
  const filename = data.filename;
  useEffect(() => {
    const controller = new AbortController();
    labRequest('files', {}, controller.signal).then(result => {
      if (!controller.signal.aborted) setFiles(result.files || []);
    }).catch(e => { if (!controller.signal.aborted) setFilesError(e.message); });
    return () => controller.abort();
  }, []);
  useEffect(() => { setReference(''); }, [filename]);
  // Recordings are addressed by file name; the setup routes take the file's ID.
  const fileId = name => {
    const file = files.find(item => item.name === name);
    return isSelectableRecording(file || {}) ? file?.file_id || (/^[a-f0-9]{24}$/.test(name || '') ? name : null) : null;
  };
  const targetId = fileId(filename), referenceId = fileId(reference);
  const single = useLmuResource(targetId ? `/api/lmu/telemetry/setup?file_id=${encodeURIComponent(targetId)}` : null);
  const compared = useLmuResource(targetId && referenceId && referenceId !== targetId ? `/api/lmu/telemetry/setup-comparison?reference_file_id=${encodeURIComponent(referenceId)}&target_file_id=${encodeURIComponent(targetId)}` : null);
  const setup = single.payload?.setup, report = compared.payload?.comparison;
  const rows = (report?.rows || []).filter(row => filter === 'ALL' || row.state === filter);
  if (!filename) return <LmuNoSource onChooseSource={onChooseSource} />;
  return <section className="wide-workspace gap-4 overflow-auto" aria-label="LMU 셋업 비교"><div className="wide-heading"><div><h2>셋업</h2><p>기록된 셋업을 확인하고 이전 주행과 바뀐 항목을 비교하세요.</p></div>{onChooseSource && <Button variant="outline" onClick={onChooseSource}>주행 선택</Button>}</div><LmuFeedback error={filesError || single.error || compared.error} loading={single.loading || compared.loading} />
    <Card><CardHeader><CardTitle>현재 주행의 셋업</CardTitle><CardDescription>{data.file_name || files.find(file => file.name === filename)?.display_name || '현재 선택한 주행'}</CardDescription></CardHeader><CardContent>{setup && <><p>{reasonText[setup.reason] || `표시 가능한 항목 ${setup.counts?.AVAILABLE ?? 0}개 · 확인 필요 ${setup.counts?.UNAVAILABLE ?? 0}개`}</p><Table><TableHeader><TableRow><TableHead>항목</TableHead><TableHead>기록값</TableHead></TableRow></TableHeader><TableBody>{(setup.items || []).map(item => <TableRow key={item.key}><TableCell>{item.label}</TableCell><TableCell>{value(item)}</TableCell></TableRow>)}</TableBody></Table></>}</CardContent><CardFooter><p>파일 전체의 셋업 메타데이터입니다. 표시는 내보내기 프로그램의 원문이며 랩 도중 조정값과 실제 물리 단위는 확인 필요입니다.</p></CardFooter></Card>
    <Card><CardHeader><CardTitle>A/B 셋업 항목 비교</CardTitle><CardDescription>기준 A와 현재 주행 B에서 기록된 변경을 확인합니다.</CardDescription></CardHeader><CardContent className="flex flex-col gap-4"><FieldGroup><Field><FieldLabel htmlFor={`${id}-reference`}>기준 A 주행</FieldLabel><NativeSelect id={`${id}-reference`} value={reference} onChange={event => setReference(event.target.value)}><NativeSelectOption value="">비교할 기준 주행 선택</NativeSelectOption>{files.filter(file => file.name !== filename && isSelectableRecording(file)).map(file => <NativeSelectOption key={file.name} value={file.name}>{file.display_name || file.file_name || file.name}</NativeSelectOption>)}</NativeSelect></Field>{report && <Field><FieldLabel htmlFor={`${id}-filter`}>표시할 항목</FieldLabel><NativeSelect id={`${id}-filter`} value={filter} onChange={event => setFilter(event.target.value)}><NativeSelectOption value="ALL">전체 항목</NativeSelectOption><NativeSelectOption value="CHANGED">변경 항목</NativeSelectOption><NativeSelectOption value="UNKNOWN">확인 필요한 항목</NativeSelectOption></NativeSelect></Field>}</FieldGroup>{report && <><div className="flex flex-wrap gap-2"><Badge variant="outline">변경 {report.counts?.CHANGED ?? 0}개</Badge><Badge variant="outline">동일 {report.counts?.UNCHANGED ?? 0}개</Badge><Badge variant="outline">확인 필요 {report.counts?.UNKNOWN ?? 0}개</Badge></div>{report.reason && <p>{reasonText[report.reason] || '조건과 셋업 기록을 확인하세요.'}</p>}<Table><TableHeader><TableRow><TableHead>항목</TableHead><TableHead>기준 A</TableHead><TableHead>현재 B</TableHead><TableHead>관측</TableHead></TableRow></TableHeader><TableBody>{rows.map(row => <TableRow key={row.key}><TableCell>{row.label}</TableCell><TableCell>{value(row.reference)}</TableCell><TableCell>{value(row.target)}</TableCell><TableCell>{stateLabel[row.state] || '확인 필요'}</TableCell></TableRow>)}</TableBody></Table>{rows.length === 0 && <p>선택한 조건에 표시할 항목이 없습니다.</p>}</>}</CardContent><CardFooter><p>변경 내역 비교이며 셋업 효과는 미평가입니다. 기온·연료·타이어·게임 유효성 등 조건이 확인되지 않아 랩타임 차이를 셋업 효과로 저장하지 않습니다.</p></CardFooter></Card>
  </section>;
}
