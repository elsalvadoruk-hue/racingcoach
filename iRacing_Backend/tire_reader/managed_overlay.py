"""Private-pipe native tire window, with capture/OCR off the Tk and API threads."""
import json
from queue import Empty, Queue
from .pressure_ocr import WHEELS
import sys
import threading
import time


def run(position_path, *, fixed=False, background_opacity=85, content_opacity=100, radius=12,
        game='iracing', display_enabled=True, sampler_factory=None, input_stream=None, output_stream=None):
    import ctypes
    # mss and native dragging both use physical pixels on mixed-DPI monitors.
    ctypes.WinDLL('user32', use_last_error=True).SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    import tkinter as tk
    from .pressure_display import PressureDisplay
    from .pressure_overlay import PressureOverlay

    input_stream, output_stream = input_stream or sys.stdin, output_stream or sys.stdout
    messages, samples, stop = Queue(maxsize=32), Queue(maxsize=1), threading.Event()
    digit_resets = Queue(maxsize=8)  # cars whose local digit learning starts over
    output_lock = threading.Lock()

    def emit(payload):
        with output_lock:
            try:
                output_stream.write(json.dumps(payload, ensure_ascii=True, allow_nan=False) + '\n')
                output_stream.flush()
            except (OSError, ValueError):
                stop.set()

    def read_commands():
        try:
            for line in input_stream:
                if len(line) > 4096:
                    break
                try:
                    message = json.loads(line)
                    if isinstance(message, dict):
                        messages.put(message, timeout=1)
                except (ValueError, TypeError):
                    continue
        finally:
            stop.set()  # Parent exit must also close the native window.

    def measure():
        sampler = None
        try:
            sampler = sampler_factory()
            ocr = getattr(sampler, 'ocr', None)
            # Language tags only, so the app can suggest English Windows OCR.
            emit({'type': 'ready', **({'ocr_language': getattr(ocr, 'language', None),
                  'ocr_languages': list(getattr(ocr, 'languages', None) or [])[:16]} if ocr is not None else {})})
            while not stop.is_set():
                started = time.monotonic()
                while hasattr(sampler, 'reset_digits'):
                    try:
                        sampler.reset_digits(digit_resets.get_nowait())
                    except Empty:
                        break
                row = sampler.sample()
                if stop.is_set():
                    break
                try:
                    samples.get_nowait()
                except Empty:
                    pass
                samples.put_nowait(row)
                # No overlapping measurements or catch-up burst after slow OCR.
                stop.wait(max(0, 1.0 - (time.monotonic() - started)))
        except Exception as error:
            code = 'dependencies_missing' if isinstance(error, ImportError) else 'ocr_unavailable'
            emit({'type': 'fatal', 'code': code})
            stop.set()
        finally:
            if sampler is not None:
                sampler.close()

    if sampler_factory is None:
        try:
            # Load numerical DLLs on the child main thread before Tk starts.
            # On Windows their first import from a worker can stall in the loader.
            if game == 'lmu':
                from .lmu_source import LmuTireReader
                sampler_factory = LmuTireReader
            else:
                import cv2
                from pathlib import Path
                cv2.setNumThreads(1)
                from .digit_model import DigitReader
                from .screen_reader import ScreenTireReader
                # Learned digit shapes stay next to this game's tire settings on this PC.
                digit_folder = Path(position_path).with_name('tires-digits')
                sampler_factory = lambda: ScreenTireReader(digits=DigitReader(digit_folder), weather=True)
        except Exception as error:
            emit({'type': 'fatal', 'code': 'dependencies_missing' if isinstance(error, ImportError) else 'ocr_unavailable'})
            return
    root = tk.Tk()
    root.withdraw()
    visible, epoch, last_capture = False, None, None
    advice = None  # garage cold-pressure advice (bar text per wheel) sent by the app
    display = PressureDisplay()

    def user_close():
        nonlocal display_enabled, visible
        display_enabled = visible = False
        overlay.hide()
        emit({'type': 'user_disabled'})

    overlay = PressureOverlay(root, position_path, on_stop=user_close,
        background_opacity=background_opacity,content_opacity=content_opacity,radius=radius)
    overlay.set_locked(fixed)
    worker = threading.Thread(target=measure, name='tire-measure', daemon=True)
    worker.start()
    threading.Thread(target=read_commands, name='tire-commands', daemon=True).start()

    def tick():
        nonlocal visible, epoch, last_capture, display_enabled, advice
        if stop.is_set():
            overlay.close()
            root.destroy()
            return
        while True:
            try:
                command = messages.get_nowait()
            except Empty:
                break
            action = command.get('action')
            if action == 'display' and type(command.get('enabled')) is bool:
                display_enabled = command['enabled']
                if not display_enabled:
                    overlay.hide()
                    visible = False
            elif action == 'fixed' and type(command.get('fixed')) is bool:
                overlay.set_locked(command['fixed'])
            elif action == 'reset_position':
                overlay.reset_position(last_capture)
            elif action == 'appearance':
                patch={key: command[key] for key, maximum in
                       (('background_opacity',100),('content_opacity',100),('radius',32))
                       if type(command.get(key)) is int and 0 <= command[key] <= maximum}
                if not patch or any(key in command and key not in patch for key in
                                    ('background_opacity','content_opacity','radius')):
                    emit({'type': 'reply', 'id': command.get('id'), 'ok': False})
                    continue
                overlay.set_appearance(**patch)
            elif action == 'advice' and (command.get('values') is None or isinstance(command.get('values'), dict)):
                values = command.get('values')
                advice = ({w: values[w][:6] if isinstance(values.get(w), str) else '—' for w in WHEELS}
                          if values else None)
            elif action == 'reset_digits' and isinstance(command.get('car'), str):
                try:
                    digit_resets.put_nowait(command['car'][:180])
                except Exception:
                    emit({'type': 'reply', 'id': command.get('id'), 'ok': False})
                    continue
            elif action == 'shutdown':
                stop.set()
            else:
                emit({'type': 'reply', 'id': command.get('id'), 'ok': False})
                continue
            emit({'type': 'reply', 'id': command.get('id'), 'ok': True})
        try:
            row = samples.get_nowait()
        except Empty:
            row = None
        if row is not None:
            current_epoch = (row.get('cockpit_epoch'), row.get('car_path'))
            if current_epoch != epoch:
                if visible:
                    overlay.hide()
                    visible=False
                display.reset()
                epoch = current_epoch
                overlay.set_car(row.get('car_path'))
            display.update(row)
            # Stay hidden until real numbers arrive. Once acquired, temporary OCR
            # misses hold values and never flash the window on/off in this stint.
            ready = display_enabled and row.get('overlay_ready') is True and display.updates > 0
            # In the garage the same window shows the advised cold pressures instead.
            garage = display_enabled and advice is not None and (row.get('tire_snapshot') or {}).get('in_garage') is True
            if ready:
                last_capture = row.get('capture') or last_capture
                overlay.render(display)
                if not visible:
                    overlay.show(last_capture)
            elif garage:
                overlay.render_advice(advice)
                if not visible:
                    overlay.show(last_capture)
            elif visible:
                overlay.hide()
            visible = ready or garage
            emit({'type': 'state', 'visible': visible, 'status': row.get('status'),
                  'warmup_remaining_s': row.get('warmup_remaining_s'),
                  'car_name': row.get('car_name'), 'car_path': row.get('car_path'),
                  'measurement': row.get('measurement'), 'pressure': display.formatted(),
                  'temperature': display.formatted_temperatures(),
                  'display_status': display.status, 'temperature_kind': display.temperature_kind,
                  'digit_reader': row.get('digit_reader'),
                  'tire_snapshot': row.get('tire_snapshot')})
        root.after(40, tick)

    root.after(40, tick)
    try:
        root.mainloop()
    finally:
        stop.set()
        worker.join(timeout=2)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='RacingCoach additional tire overlay')
    parser.add_argument('--position', required=True)
    parser.add_argument('--fixed', action='store_true')
    parser.add_argument('--game', choices=('iracing', 'lmu'), default='iracing')
    parser.add_argument('--hidden', action='store_true')
    parser.add_argument('--background-opacity', type=int, default=85)
    parser.add_argument('--content-opacity', type=int, default=100)
    parser.add_argument('--radius', type=int, default=12)
    args = parser.parse_args()
    run(args.position, fixed=args.fixed, background_opacity=args.background_opacity,
        content_opacity=args.content_opacity,radius=args.radius,game=args.game,display_enabled=not args.hidden)
