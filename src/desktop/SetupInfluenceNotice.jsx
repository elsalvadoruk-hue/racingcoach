import React from 'react';
import { Info } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';

export default function SetupInfluenceNotice({ comparison, external = [], game }) {
  if (game !== 'iracing' || !external.length || comparison?.setupInfluence?.status !== 'POSSIBLE'
      || !Number.isFinite(comparison.delta) || comparison.delta < .05) return null;
  return <Alert variant="warning" role="status" aria-label="셋업 영향 가능성" className="setup-influence-notice">
    <Info aria-hidden="true" />
    <AlertTitle><span>셋업 영향 가능성 있음</span><Badge variant="outline">랩 전체</Badge></AlertTitle>
    <AlertDescription>코너에서 시간을 벌지만 직선에서 손실이 반복됩니다. 셋업 차이가 영향을 줬을 수 있습니다.</AlertDescription>
  </Alert>;
}
