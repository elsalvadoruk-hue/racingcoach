"""Bounded local-ASR diagnostics, without cloud questions, audio or device IDs."""
import io
import json
import zipfile

import session_log


def batch(store, consent, previous, now, version):
    if not callable(getattr(store, 'connect', None)):
        return None
    after = max(consent['given_at'], previous.get('through', 0))
    after_id = previous.get('through_id','') if after == previous.get('through') else ''
    with store.connect() as db:
        notes = db.execute("SELECT id,created_at,text,reply,context_json FROM voice_notes "
                           "WHERE source='LOCAL_VOICE' AND (created_at>? OR (created_at=? AND id>?)) AND created_at<? "
                           "ORDER BY created_at,id LIMIT 100", (after, after, after_id, now-60)).fetchall()
    if not notes: return None
    rows = []
    for note in notes:
        context = json.loads(note['context_json'] or '{}')
        detail = context.get('local_question') or {}
        # Only logs collected under this exact consent are eligible, including tests.
        if detail.get('log_consent_at') != consent['given_at']: continue
        rows.append({'at':note['created_at'], 'text':note['text'][:500], 'reply':(note['reply'] or '')[:2000],
                     'game':context.get('game'), 'recognition': {key:detail.get(key) for key in (
                         'model','engine','topic','topic_name','result','test','normalized_text','corrections',
                         'correction_status','classifier_version','asr_ms','cpu_ms','total_ms','sent','noise_retry',
                         'readback_text','readback_result','readback_audio_missing','race_focus',
                         'classify_ms','missing_data','missing_audio','semantic','dialogue','dialogue_outcome',
                         'answer_detail','answer_facts','answer_explanation','spoken_text','delivery',
                         'language','data_notes','additional_topics','additional_evidence')},
                     'command':{key:(detail.get('command') or {}).get(key) for key in ('id','name','calls')},
                     'snapshot':{key:(context.get('snapshot') or {}).get(key) for key in (
                         'lap_number','speed_mps','on_track','is_replay')},
                     'audio_saved':False})
    through = notes[-1]['created_at']
    through_id = notes[-1]['id']
    if not rows: return {'through':through,'through_id':through_id}
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json',json.dumps({'format':session_log.FORMAT,
            'format_version':session_log.FORMAT_VERSION,'kind':'local-speech-diagnostics',
            'app_version':version,'records':len(rows),'audio_included':False,'exported_at':now}))
        archive.writestr('radio.jsonl','')
        archive.writestr('reports.jsonl','')
        archive.writestr('local-speech.jsonl','\n'.join(json.dumps(row,ensure_ascii=False) for row in rows))
    data=buffer.getvalue()
    if len(data)>session_log.SEND_LIMIT: raise ValueError('음성 인식 진단 파일이 너무 큽니다.')
    return {'through':through,'through_id':through_id,'name':f'coach-log-speech-{int(consent["given_at"])}-{int(through*1000)}-{through_id[:8]}.zip',
            'data':data,'records':len(rows)}
