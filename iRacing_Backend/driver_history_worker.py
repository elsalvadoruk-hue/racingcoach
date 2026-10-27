"""Interruptible, low priority recording measurement in an owned Python process.

Only compact summaries cross the pipe. The child imports pure readers rather
than the API, never attaches to a game and cannot write application stores.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


_FAILED = '주행 기록의 측정 작업을 완료하지 못했습니다.'
_SOURCE_ID = re.compile(r'^[a-f0-9]{64}$')
_THREAD_ENV = ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS',
               'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'BLIS_NUM_THREADS')


def _environment():
    # Readers have no providers. Do not pass credentials to an offline parser.
    prefixes = ('GOOGLE_', 'GEMINI_', 'OPENAI_', 'ANTHROPIC_', 'AWS_', 'AZURE_')
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(prefixes)
                   and not any(token in key.upper() for token in ('API_KEY', 'TOKEN', 'CREDENTIAL', 'SECRET', 'PASSWORD'))}
    environment.update({key: '1' for key in _THREAD_ENV})
    environment.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
                       PYTHONIOENCODING='utf-8', IRACING_AI_ENABLED='0', GARAGE61_AUTO_COLLECT='0')
    return environment


def _stop_and_reap(process):
    """Return only after this owned child has exited and released its handles."""
    try:
        process.terminate()
    except OSError:
        pass  # It may have exited between the stop request and terminate().
    try:
        process.wait(timeout=1.)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        process.wait()


def summarize_in_process(path, source_id, should_stop):
    return _run_in_process(path, source_id, should_stop)


def enrich_inputs_in_process(path, source_id, observations, should_stop):
    """Read minimal input channels; preserve existing non-input evidence."""
    from driver_history_inputs import input_request
    return _run_in_process(path, source_id, should_stop, input_request(observations))


def _run_in_process(path, source_id, should_stop, observations=None):
    """Read one recording, stopping promptly when foreground work needs the CPU.

    communicate() drains stdout while waiting; polling poll()/wait() alone can
    deadlock when a large compact summary fills the operating system pipe.
    """
    stopped = should_stop if should_stop is not None else lambda: False
    if stopped():
        raise InterruptedError('Library measurement interrupted')
    process = None
    completed = False
    try:
        if not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id):
            raise ValueError('Invalid source identity')
        options = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       text=True, encoding='utf-8', errors='strict', env=_environment())
        if os.name == 'nt':
            options['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
        process = subprocess.Popen([sys.executable, '-B', '-u', str(Path(__file__).resolve())], **options)
        request = {'path': str(path), 'source_id': source_id}
        if observations is not None:
            request.update(mode='inputs', observations=observations)
        request = json.dumps(request)
        while True:
            if stopped():
                raise InterruptedError('Library measurement interrupted')
            try:
                output, _ = process.communicate(input=request, timeout=.2)
                completed = True
                break
            except subprocess.TimeoutExpired:
                # Python forbids sending input again after communication began.
                request = None
        if stopped():
            raise InterruptedError('Library measurement interrupted')
        if process.returncode != 0:
            raise ValueError('Measurement process failed')
        result = json.loads(output)
        if observations is None and result == {'skip_reason': 'IMPORTED_REFERENCE'}:
            return result
        expected_game = 'lmu' if Path(path).suffix.casefold() == '.duckdb' else 'iracing'
        if (not isinstance(result, dict) or result.get('schema_version') != 1
                or result.get('source_id') != source_id or result.get('game') != expected_game
                or not isinstance(result.get('observations'), list)):
            raise ValueError('Invalid measurement result')
        if observations is not None:
            from driver_history_inputs import INPUT_VERSION
            if result.get('input_version') != INPUT_VERSION:
                raise ValueError('Invalid input measurement result')
        return result
    except InterruptedError:
        raise
    except Exception:
        # Neither recording paths nor parser output belong in user-facing errors.
        raise RuntimeError(_FAILED) from None
    finally:
        if process is not None:
            try:
                if not completed:
                    _stop_and_reap(process)
                    process.communicate()
            finally:
                for name in ('stdin', 'stdout', 'stderr'):
                    stream = getattr(process, name, None)
                    if stream is not None:
                        stream.close()


def _read_only_audit(event, args):
    """Keep optional reader helpers from creating stores or contacting services."""
    if event == 'open':
        mode, flags = args[1:3]
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        if (isinstance(mode, str) and any(char in mode for char in 'wax+')) or flags & write_flags:
            raise PermissionError('Measurement is read only')
    elif event in {'os.remove', 'os.rename', 'os.mkdir', 'os.rmdir', 'os.system',
                   'subprocess.Popen', 'sqlite3.connect', 'socket.__new__', 'socket.connect'}:
        raise PermissionError('Measurement is offline and read only')


def _read_recording(path, source_id, *, inputs_only=False):
    from analysis_numeric import file_signature

    path = Path(path)
    signature = file_signature(path)
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != source_id or file_signature(path) != signature:
        raise ValueError('Recording changed')
    if path.suffix.casefold() == '.ibt':
        from ibt_reader import IBTReader
        if inputs_only:
            from driver_style import DRIVER_INPUT_CHANNELS
            telemetry = IBTReader(path).parse(requested_channels=DRIVER_INPUT_CHANNELS)
        else:
            telemetry = IBTReader(path).parse()
    elif path.suffix.casefold() == '.duckdb':
        import duckdb
        import lmu_car_models
        # LmuIbtReader normally learns livery/model associations. Background
        # measurement only reads existing associations and never learns/writes.
        lmu_car_models.learn = lambda *args, **kwargs: None
        connect = duckdb.connect

        def single_thread_connection(*args, **kwargs):
            kwargs['config'] = {**kwargs.get('config', {}), 'threads': '1'}
            return connect(*args, **kwargs)

        duckdb.connect = single_thread_connection
        from lmu_ibt import LmuIbtReader
        telemetry = LmuIbtReader(path).parse()
    else:
        raise ValueError('Unsupported recording')
    if tuple(telemetry.signature) != signature or file_signature(path) != signature:
        raise ValueError('Recording changed')
    if (telemetry.metadata or {}).get('imported') is True:
        if inputs_only:
            raise ValueError('Imported reference cannot enrich driver history')
        return {'skip_reason': 'IMPORTED_REFERENCE'}
    return telemetry


def _summarize_recording(path, source_id):
    from analysis_numeric import file_signature
    from driver_history_summary import build_source_summary
    from driver_progress import attach_recording_results

    path = Path(path)
    telemetry = _read_recording(path, source_id)
    if isinstance(telemetry, dict):
        return telemetry
    signature = telemetry.signature
    result = attach_recording_results(telemetry, build_source_summary(telemetry, source_id))
    if file_signature(path) != signature:
        raise ValueError('Recording changed')
    return result


def _enrich_recording(path, source_id, observations):
    from analysis_numeric import file_signature
    from driver_history_inputs import measure_source_inputs

    telemetry = _read_recording(path, source_id, inputs_only=True)
    result = measure_source_inputs(telemetry, source_id, observations)
    if file_signature(Path(path)) != tuple(telemetry.signature):
        raise ValueError('Recording changed')
    return result


def _main():
    # stdin carries the private source path so it never appears in process args.
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict) or not _SOURCE_ID.fullmatch(request.get('source_id', '')):
            return 1
        sys.dont_write_bytecode = True
        sys.addaudithook(_read_only_audit)
        if request.get('mode') == 'inputs':
            result = _enrich_recording(request['path'], request['source_id'], request['observations'])
        else:
            result = _summarize_recording(request['path'], request['source_id'])
        json.dump(result, sys.stdout, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
        return 0
    except Exception:
        return 1


if __name__ == '__main__':
    raise SystemExit(_main())
