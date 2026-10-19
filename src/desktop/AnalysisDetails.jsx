import React, { useState } from 'react';
import { SlidersHorizontal } from 'lucide-react';
import { Button } from '../components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '../components/ui/dialog';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../components/ui/tabs';
import { Panel } from '../analysis/WorkspaceUi';
import { StructuredFeedback } from '../analysis/SummaryPanels';
import { ClassificationPanel, RideAeroPanel, TirePanel } from '../analysis/VehiclePanels';
import { SetupRecommendation } from '../analysis/SetupPanels';
import { BriefingViewer } from '../analysis/AiPanels';
import { QualityBanner } from '../analysis/LapPanels';
import DamperHistogram from '../components/DamperHistogram';
import GGDiagram from '../components/GGDiagram';
import ConnectionControls from './ConnectionControls';

export default function AnalysisDetails({ workspace: w, onNavigate }) {
  const [open, setOpen] = useState(false), a = w.analysis;
  return <><Button variant="outline" size="sm" onClick={() => setOpen(true)}><SlidersHorizontal data-icon="inline-start" />분석·차량 상세</Button>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent className="desktop-details-dialog">
      <DialogHeader><DialogTitle>분석·차량 상세</DialogTitle><DialogDescription>세부 측정, 근거 우선 추천과 주행 기록 설정을 확인합니다.</DialogDescription></DialogHeader>
      <Tabs defaultValue="vehicle"><TabsList><TabsTrigger value="vehicle">차량 측정</TabsTrigger><TabsTrigger value="feedback">근거·피드백</TabsTrigger><TabsTrigger value="session">주행 기록</TabsTrigger></TabsList>
        <TabsContent value="vehicle"><Panel title="원인 분류"><ClassificationPanel classification={w.classification} /></Panel>
          <Panel title="타이어 종료 스냅샷"><TirePanel tires={a.vehicle?.tires} /></Panel>
          <Panel title="차고·서스펜션·에어로"><RideAeroPanel rideHeight={a.vehicle?.ride_height} aero={a.vehicle?.aero} /></Panel>
          <Panel title="댐퍼 속도 분포"><DamperHistogram current={a.vehicle?.damper_velocity} /></Panel>
          <Panel title="G-G 그림 (그립 사용)"><GGDiagram current={a.vehicle?.gg} /></Panel></TabsContent>
        <TabsContent value="feedback"><StructuredFeedback phase={w.feedbackPhase} speed={w.feedbackSpeed} symptom={w.feedbackSymptom}
          setupMode={w.setupMode} onSetupMode={w.setSetupMode} onPhase={w.setFeedbackPhase} onSpeed={w.setFeedbackSpeed}
          onSymptom={w.setFeedbackSymptom} value={w.userFeedback} onChange={w.setUserFeedback} result={a.structured_feedback}
          coach={a.closed_loop_coach} onAnalyze={() => w.runAnalysis({ includeAI: false })} goal={w.goal} onGoal={w.setGoal}
          trialActive={Boolean(w.activeTrial)} loading={w.loading} onNavigate={() => { setOpen(false); onNavigate('setup'); }} />
          <Panel title="셋업 결정"><SetupRecommendation recommendation={a.setup_recommendation} onStart={(...args) => { w.beginTrial(...args); setOpen(false); onNavigate('setup'); }} canStart={w.canStartTrial} /></Panel>
          <Panel title="엔지니어 브리핑"><BriefingViewer briefing={a.briefing} /></Panel></TabsContent>
        <TabsContent value="session"><QualityBanner quality={w.quality} /><ConnectionControls workspace={w} /></TabsContent>
      </Tabs>
    </DialogContent></Dialog></>;
}
