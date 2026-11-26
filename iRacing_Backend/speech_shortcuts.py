"""Independent microphone shortcuts sharing the existing native input pump."""
import asyncio
import json

from fastapi import HTTPException, WebSocketDisconnect
from radio_buttons import HID, keyboard


class SpeechShortcuts:
    def __init__(self, buttons):
        self.buttons = buttons
        self.path = buttons.path.with_name('speech-shortcuts.json') if buttons.path else None
        self.loaded = False
        self.rows = {'local': None, 'cloud': None}
        self.revision = 0
        self.error = ''
        self.listeners = {}
        self.down = {'local': False, 'cloud': False}
        self.blocked, self.game = set(), None

    def settings(self):
        if not self.loaded:
            self.loaded = True
            if self.path and self.path.is_file():
                try:
                    value = json.loads(self.path.read_text(encoding='utf8'))
                    self.rows = self.validate(value['bindings'])
                    self.revision = int(value['revision'])
                except (OSError, ValueError, TypeError, KeyError):
                    self.error = '음성 단축키 설정을 읽지 못했습니다.'
        if self.error:
            raise HTTPException(409, self.error)
        return {'bindings': self.rows, 'revision': self.revision}

    @staticmethod
    def validate(rows):
        if not isinstance(rows, dict) or set(rows) != {'local', 'cloud'}:
            raise ValueError('로컬·Google 단축키를 확인하세요.')
        codes = []
        for binding in rows.values():
            if binding is None:
                continue
            if not isinstance(binding, dict):
                raise ValueError('단축키를 다시 지정하세요.')
            code, label = binding.get('code'), binding.get('label')
            if not isinstance(code, str) or not (keyboard(code) or HID.fullmatch(code)) or not isinstance(label, str) or not 1 <= len(label) <= 100:
                raise ValueError('지원하는 키나 휠 버튼을 눌러 주세요.')
            codes.append(code)
        if len(set(codes)) != len(codes):
            raise ValueError('로컬과 Google 질문에는 서로 다른 단축키를 지정하세요.')
        return rows

    def check_radio(self, rows, bindings=None):
        values = self.settings()['bindings'] if bindings is None else bindings
        reserved = {b['code'] for b in values.values() if b}
        if any(r['enabled'] and r['items'] and any(k['code'] in reserved for k in r['keys']) for r in rows):
            raise ValueError('무전 버튼과 음성 질문에 같은 단축키를 사용할 수 없습니다.')

    def save(self, body):
        self.settings()
        if not self.path:
            raise ValueError('설정 저장소를 사용할 수 없습니다.')
        if body.get('revision') != self.revision:
            raise ValueError('다른 창에서 설정이 바뀌었습니다. 다시 열어 주세요.')
        rows = self.validate(body.get('bindings'))
        self.check_radio(self.buttons.rows, rows)
        value = {'bindings': rows, 'revision': self.revision+1}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')
        temporary.replace(self.path)
        self.release()
        self.game = None
        self.rows, self.revision = rows, value['revision']
        return value

    def shortcuts(self):
        if not self.listeners:
            return []
        return [(value['code'], *binding) for value in self.rows.values() if value
                for binding in [keyboard(value['code'])] if binding]

    def release(self):
        for channel in self.down:
            if self.down[channel]:
                self.emit(channel, False)
        self.down = {'local': False, 'cloud': False}

    def emit(self, channel, down):
        for q, kind in list(self.listeners.items()):
            if kind == channel and not q.full():
                q.put_nowait({'type': 'ptt', 'down': down})

    def poll(self, held, game, capturing=False):
        if game != self.game:
            self.release()
            self.blocked = set(held)
            self.game = game
        if capturing:
            self.blocked.update(held)
        self.blocked.intersection_update(held)
        for channel, binding in self.rows.items():
            pressed = bool(game and not capturing and binding and binding['code'] in held - self.blocked)
            if pressed != self.down[channel]:
                self.down[channel] = pressed
                self.emit(channel, pressed)

    async def listen(self, socket, channel):
        self.settings()
        await socket.accept()
        q = asyncio.Queue(maxsize=16)
        if not self.listeners:
            self.game = None
        self.listeners[q] = channel
        self.buttons.clients[q] = False
        if self.buttons.task is None or self.buttons.task.done():
            self.buttons.task = asyncio.create_task(self.buttons.pump())
        closed = asyncio.create_task(socket.receive())
        try:
            await socket.send_json({'type':'ready'})
            while True:
                item = asyncio.create_task(q.get())
                try:
                    done, _ = await asyncio.wait((closed,item), return_when=asyncio.FIRST_COMPLETED)
                    if closed in done:
                        break
                    event = item.result()
                    if event.get('type') == 'ptt' or 'error' in event:
                        await socket.send_json(event)
                finally:
                    item.cancel()
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass
        finally:
            closed.cancel()
            self.listeners.pop(q,None)
            self.buttons.clients.pop(q,None)


def register(router, coach, buttons):
    service = SpeechShortcuts(buttons)
    buttons.speech_inputs = service
    coach.live.speech_shortcuts_managed = True

    @router.get('/speech-shortcuts')
    async def settings():
        return service.settings()

    @router.post('/speech-shortcuts')
    async def save(body: dict):
        try:
            return service.save(body)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    from fastapi import WebSocket
    @router.websocket('/speech-shortcuts/events/{channel}')
    async def events(socket: WebSocket, channel: str):
        if channel not in ('local', 'cloud'):
            await socket.close(code=1008)
            return
        await service.listen(socket, channel)

    return service
