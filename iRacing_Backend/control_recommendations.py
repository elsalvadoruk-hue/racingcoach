"""A single measured recommendation, timed by actual audio delivery, not enqueue."""
from live_controls import finite

FIELDS={'brake_bias':'brake_bias','tc':'tc_setting','abs':'abs_setting'}
FIELDS.update({k:k for k in ('front_arb','rear_arb','rear_wing','pressure_lf','pressure_rf','pressure_lr','pressure_rr')})
LABELS={'brake_bias':'BBAL','tc':'TC','abs':'ABS'}
DIRECTIONS={'forward':'앞쪽으로','more':'개입 늘리기','less':'낮추기','raise':'높이기'}


def target_value(item,sample,capabilities):
    cap=capabilities.get(item['control']) or {}
    current=finite(sample.get(FIELDS.get(item['control'],'')))
    low,high,step=(finite(cap.get(k)) for k in ('min','max','step'))
    direction=cap.get('increase_direction')
    if (current is None or None in (low,high,step) or not low<=current<=high or step<=0
            or cap.get('button_adjustable') is not True): return None
    sign=(1 if item['action']=='forward' else direction if item['action'] in ('more','raise')
          else -direction if direction in (-1,1) and item['action'] in ('less','lower') else None)
    if sign not in (-1,1): return None
    target=round(current+sign*step,6)
    return target if low<=target<=high else None


class ControlRecommendations:
    def __init__(self): self.reset()
    def reset(self):
        self.sent={};self.pending={};self.active=None;self.applied_samples=0;self.last_sample=None
        self.bias_values=set();self.bias_previous=None;self.bias_deltas=[]

    def cancel(self):
        """Cockpit/link loss cancels display; it does not erase what was already spoken."""
        self.pending.clear();self.active=None;self.applied_samples=0;self.last_sample=None

    def capabilities(self, sample):
        """Only observed BB steps inside values the driver actually reached.

        The SDKs expose current maps, not their intervention order, keyboard step,
        or vehicle-specific limits. Map numbers never supply those missing facts.
        """
        value=finite(sample.get('brake_bias'))
        if value is not None and 0 < value < 100:
            if self.bias_previous is not None and value != self.bias_previous:
                delta=round(abs(value-self.bias_previous),6)
                self.bias_deltas=(self.bias_deltas+[delta])[-12:]
            self.bias_previous=value
            if len(self.bias_values)<128:self.bias_values.add(value)
        if len(self.bias_deltas)<2:return {}
        step=min(self.bias_deltas)
        # Repeated equal transitions reduce the chance of treating a jump as one click.
        if sum(abs(d-step)<1e-6 for d in self.bias_deltas)<2:return {}
        return {'brake_bias':dict(min=min(self.bias_values),max=max(self.bias_values),
                step=step,button_adjustable=True,source='OBSERVED_TRANSITIONS')}

    def choose(self,advice,sample,capabilities,now):
        if self.active or self.pending:return None
        choices=[a for a in advice if a.get('call') and now-self.sent.get(a['control'],-1e9)>=600
                 and (not a.get('setup') or now-self.sent.get('_setup',-1e9)>=600)]
        if not choices:return None
        item=dict(min(choices,key=lambda a:({'brake_bias':0,'tc':1,'abs':2}.get(a['control'],3))))
        if item.get('setup'):return item
        item.update(name=LABELS[item['control']],current=finite(sample.get(FIELDS[item['control']])),
                    target=target_value(item,sample,capabilities),timing='live',unit='%' if item['control']=='brake_bias' else '',
                    direction=DIRECTIONS.get(item['action'],'설정 확인'))
        cap=capabilities.get(item['control']) or {}
        item['step']=finite(cap.get('step'))
        return item

    def queued(self,event,item):
        if event:self.pending[event['id']]=dict(item)

    def present(self,item,now):
        """A missing, unapproved clip falls back to a measured on-screen trial."""
        self.active={**item,'id':f'setup-screen:{now}', 'shown_at':now,'expires_at':now+30,
                     'delivery':'screen','state':'suggested'}
        self.sent[item['control']]=now;self.sent['_setup']=now
        self.applied_samples=0;self.last_sample=None

    def delivery(self,event_id,status,now):
        item=self.pending.get(event_id)
        if not item:return
        if status=='started':
            if self.active and self.active.get('id')==event_id:return
            self.active={**item,'id':event_id,'shown_at':now,'expires_at':now+30}
            self.sent[item['control']]=now;self.applied_samples=0;self.last_sample=None
            if item.get('setup'):self.sent['_setup']=now
        elif status in ('failed','expired','cancelled','muted'):
            self.pending.pop(event_id,None)
            if self.active and self.active['id']==event_id:self.active=None
        elif status=='completed':self.pending.pop(event_id,None)

    def observe(self,sample,now,pending_ids):
        self.pending={k:v for k,v in self.pending.items()
                      if k in pending_ids and sample.get(v.get('field') or FIELDS[v['control']]) == v['initial']}
        a=self.active
        if not a:return
        if (now>=a['expires_at'] or sample.get('is_replay') is not False
                or sample.get('on_track') is False or sample.get('session_state') in (5,6)
                or a.get('setup') and sample.get('setup_fixed') is not False):
            self.active=None;return
        field=a.get('field') or FIELDS[a['control']]
        current=finite(sample.get(field))
        # Only distinct telemetry samples can confirm the change. Unknown is not success.
        stamp=finite(sample.get('session_time'))
        if stamp is None or stamp==self.last_sample:return
        self.last_sample=stamp
        if not field.endswith('_raw'):a['current']=current
        target=finite(a.get('target'))
        initial=finite(a.get('initial'))
        if a.get('timing')=='pit':
            # A next-stop reservation is not installation. Keep this 30-second
            # card even if the requested or garage value already equals target.
            pending=finite(sample.get(a.get('pending_field')))
            matched=target is not None and pending is not None and abs(pending-target)<.05
            self.applied_samples=self.applied_samples+1 if matched else 0
            if self.applied_samples>=2:a['state']='reserved'
            elif not matched:a['state']='suggested'
            return
        if current is not None and initial is not None:
            opposite=(target is not None and (current-initial)*(target-initial)<-1e-6)
            unknown_map_changed=(target is None and a['control']!='brake_bias' and current!=initial)
            if opposite or unknown_map_changed:
                self.active=None;return
        if target is not None:
            tolerance=max(1e-6,(a.get('step') or .001)*.05)
            matched=current is not None and abs(current-target)<=tolerance
        else:
            # With no verified map there is no numeric target to invent. A forward BB
            # change can still be confirmed directly from the measured front percentage.
            matched=(a['control']=='brake_bias' and current is not None and
                     initial is not None and current>initial)
        self.applied_samples=self.applied_samples+1 if matched else 0
        if self.applied_samples>=2:self.active=None

    def view(self): return dict(self.active) if self.active else None
