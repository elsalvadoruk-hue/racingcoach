"""Isolated CPU-only recognizer. Audio travels over pipes, never through files."""
import argparse
import base64
import io
import json
import os
from pathlib import Path
import sys
import time
import wave


def download_sensevoice(folder):
    import hashlib
    from urllib.request import urlopen
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    files = json.loads(Path(__file__).with_name('sensevoice_model.json').read_text(encoding='utf8'))
    for item in files:
        target = folder / item['file']
        if target.is_file():
            with target.open('rb') as existing:
                if hashlib.file_digest(existing, 'sha256').hexdigest() == item['sha256']:
                    continue
        temporary = target.with_suffix(target.suffix + '.part')
        try:
            with urlopen(item['url'], timeout=90) as response, temporary.open('wb') as output:
                size, digest = 0, hashlib.sha256()
                while chunk := response.read(1024*1024):
                    size += len(chunk)
                    if size > item['size']: raise ValueError('model size mismatch')
                    output.write(chunk); digest.update(chunk)
            if size != item['size'] or digest.hexdigest() != item['sha256']:
                raise ValueError('model checksum mismatch')
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def decode_audio(encoded):
    if not isinstance(encoded, str) or len(encoded) > 480000:
        raise ValueError('녹음은 10초 이내로 해 주세요.')
    raw = base64.b64decode(encoded, validate=True)
    with wave.open(io.BytesIO(raw), 'rb') as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 16000, 'NONE'):
            raise ValueError('16kHz 모노 WAV 녹음이 필요합니다.')
        frames = wav.getnframes()
        if not 4000 <= frames <= 160000:
            raise ValueError('0.25~10초 사이로 질문해 주세요.')
        pcm = wav.readframes(frames)
        if len(pcm) != frames * 2:
            raise ValueError('녹음 데이터가 불완전합니다.')
    return pcm


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--packages', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--download', choices=['base', 'small', 'sensevoice'])
    parser.add_argument('--engine', choices=['whisper', 'sensevoice'], default='whisper')
    parser.add_argument('--download-enhancement', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, args.packages)
    sys.path.insert(0, str(Path(__file__).parent))
    import local_speech_enhancement as enhancement
    for key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS'):
        os.environ[key] = '2'
    os.environ['OMP_WAIT_POLICY'] = 'PASSIVE'
    os.environ['KMP_BLOCKTIME'] = '0'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'
    if args.download_enhancement:
        enhancement.download(args.model)
        print(json.dumps({'downloaded': True}), flush=True)
        return
    if args.download:
        if args.download == 'sensevoice':
            download_sensevoice(args.model)
            enhancement.download(args.model)
        else:
            from faster_whisper.utils import download_model
            download_model(args.download, output_dir=args.model)
        print(json.dumps({'downloaded': True}), flush=True)
        return
    os.environ['HF_HUB_OFFLINE'] = '1'
    import numpy as np
    started = time.perf_counter()
    denoiser = None
    if args.engine == 'sensevoice':
        if os.name == 'nt':
            import ctypes
            dll_path = Path(args.packages) / 'Scripts'
            dll_directory = os.add_dll_directory(str(dll_path))
            ort = ctypes.WinDLL(str(dll_path / 'onnxruntime.dll'))
        import sherpa_onnx
        model = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(Path(args.model)/'model.int8.onnx'), tokens=str(Path(args.model)/'tokens.txt'),
            language='ko', use_itn=True, num_threads=2, provider='cpu')
        if enhancement.installed(args.model, verify=True):
            config = sherpa_onnx.OfflineSpeechDenoiserConfig(model=sherpa_onnx.OfflineSpeechDenoiserModelConfig(
                gtcrn=sherpa_onnx.OfflineSpeechDenoiserGtcrnModelConfig(model=str(Path(args.model)/enhancement.MODEL['file'])),
                num_threads=1, provider='cpu', debug=False))
            denoiser = sherpa_onnx.OfflineSpeechDenoiser(config)
    else:
        from faster_whisper import WhisperModel
        model = WhisperModel(args.model, device='cpu', compute_type='int8', cpu_threads=2,
                             num_workers=1, local_files_only=True)
    print(json.dumps({'ready': True, 'enhancement_ready': denoiser is not None,
                      'load_ms': round((time.perf_counter()-started)*1000)}), flush=True)
    for line in sys.stdin:
        try:
            start, cpu = time.perf_counter(), time.process_time()
            request = json.loads(line)
            pcm = decode_audio(request['audio'])
            samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
            if request.get('enhance'):
                if denoiser is None:
                    print(json.dumps({'text': '', 'reason': 'enhancement_unavailable'}), flush=True)
                    continue
                samples = np.asarray(denoiser(samples, 16000).samples, dtype=np.float32)
            if np.sqrt(np.mean(samples * samples)) < .003:
                result = {'text': '', 'reason': 'no_speech'}
            elif args.engine == 'sensevoice':
                stream = model.create_stream()
                stream.accept_waveform(16000, samples)
                model.decode_stream(stream)
                text = stream.result.text.strip()[:500]
                result = {'text':text, 'reason':'recognized' if text else 'uncertain_audio'}
            else:
                segments, _ = model.transcribe(samples, language='ko', task='transcribe', beam_size=1,
                    best_of=1, temperature=0, condition_on_previous_text=False, vad_filter=False,
                    initial_prompt='레이스 질문. 연료, 랩타임, 앞차, 뒤차, 피트, 스틴트, 클래스 순위, 가상 에너지.')
                segments = list(segments)
                usable = [s for s in segments if s.no_speech_prob < .6 and s.avg_logprob > -1.0
                          and s.compression_ratio < 2.4]
                # Never turn a partly rejected utterance into a different question.
                text = ' '.join(s.text.strip() for s in usable).strip()[:500] if len(usable) == len(segments) else ''
                result = {'text': text, 'reason': 'recognized' if text else 'uncertain_audio',
                          'avg_logprob': round(sum(s.avg_logprob for s in usable)/len(usable), 4) if usable else None}
            result.update(asr_ms=round((time.perf_counter()-start)*1000), cpu_ms=round((time.process_time()-cpu)*1000))
        except Exception:
            result = {'error': '녹음을 인식하지 못했습니다. 마이크를 확인하고 다시 질문하세요.'}
        print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
