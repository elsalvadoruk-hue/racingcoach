import React, { lazy, Suspense } from 'react';
import { Button } from './ui/button';

// Recharts is needed only when these measurement panels are opened. Keep the
// native canvas telemetry charts and the desktop shell in the initial bundle.
const loadDamperHistogram = () => import('./DamperHistogram');
const loadLapTimeUse = () => import('./LapTimeUse');

class ChartBoundary extends React.Component {
  state = { failed: false, Chart: lazy(this.props.load) };

  static getDerivedStateFromError() { return { failed: true }; }

  retry = () => this.setState({ failed: false, Chart: lazy(this.props.load) });

  render() {
    if (this.state.failed) return <div className="flex flex-col items-start gap-2">
      <p role="alert">그래프를 불러오지 못했습니다. 다시 시도해 주세요.</p>
      <Button variant="outline" size="sm" onClick={this.retry}>그래프 다시 불러오기</Button>
    </div>;
    const Chart = this.state.Chart;
    return <Suspense fallback={<p role="status">{this.props.pending}</p>}><Chart {...this.props.chartProps} /></Suspense>;
  }
}

export function DeferredDamperHistogram(props) {
  return <ChartBoundary load={loadDamperHistogram} pending="댐퍼 그래프를 준비하고 있습니다." chartProps={props} />;
}

export function DeferredLapTimeUse(props) {
  return <ChartBoundary load={loadLapTimeUse} pending="랩 시간 그래프를 준비하고 있습니다." chartProps={props} />;
}
