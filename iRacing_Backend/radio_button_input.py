"""Windows controller buttons via Raw Input. Keyboard polling is limited to the
user's assigned shortcuts while a simulator is in the foreground."""
import ctypes as c
from ctypes import wintypes as w
import hashlib
from pathlib import Path

from tire_reader.window_visibility import GAME_PROCESSES


class Registration(c.Structure):
    _fields_ = [('page', w.USHORT), ('usage', w.USHORT), ('flags', w.DWORD), ('window', w.HWND)]


class Header(c.Structure):
    _fields_ = [('kind', w.DWORD), ('size', w.DWORD), ('device', w.HANDLE), ('param', w.WPARAM)]


def function(lib, name, result, *args):
    fn = getattr(lib, name)
    fn.restype, fn.argtypes = result, args
    return fn


def modifier_mask(key_state):
    return sum(bit for bit, keys in ((1, (0x11,)), (2, (0x12,)), (4, (0x10,)), (8, (0x5b, 0x5c)))
               if any(key_state(vk) & 0x8000 for vk in keys))


class WindowsButtons:
    usages = ((1, 4), (1, 5), (1, 8), (2, 2))

    def __init__(self):
        user = c.WinDLL('user32', use_last_error=True)
        kernel = c.WinDLL('kernel32', use_last_error=True)
        hid = c.WinDLL('hid', use_last_error=True)
        pointer = c.POINTER
        self.info = function(user, 'GetRawInputDeviceInfoW', w.UINT, w.HANDLE, w.UINT, w.LPVOID, pointer(w.UINT))
        self.raw = function(user, 'GetRawInputData', w.UINT, w.HANDLE, w.UINT, w.LPVOID, pointer(w.UINT), w.UINT)
        self.register = function(user, 'RegisterRawInputDevices', w.BOOL, pointer(Registration), w.UINT, w.UINT)
        self.peek = function(user, 'PeekMessageW', w.BOOL, pointer(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT)
        self.dispatch = function(user, 'DispatchMessageW', c.c_ssize_t, pointer(w.MSG))
        self.destroy = function(user, 'DestroyWindow', w.BOOL, w.HWND)
        self.keys = function(user, 'GetAsyncKeyState', w.SHORT, c.c_int)
        self.foreground = function(user, 'GetForegroundWindow', w.HWND)
        self.window_pid = function(user, 'GetWindowThreadProcessId', w.DWORD, w.HWND, pointer(w.DWORD))
        self.open_process = function(kernel, 'OpenProcess', w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
        self.process_name = function(kernel, 'QueryFullProcessImageNameW', w.BOOL, w.HANDLE, w.DWORD, w.LPWSTR, pointer(w.DWORD))
        self.close_handle = function(kernel, 'CloseHandle', w.BOOL, w.HANDLE)
        self.get_caps = function(hid, 'HidP_GetCaps', w.LONG, w.LPVOID, w.LPVOID)
        self.get_usages = function(hid, 'HidP_GetUsages', w.LONG, c.c_int, w.USHORT, w.USHORT,
                                   pointer(w.USHORT), pointer(w.ULONG), w.LPVOID, w.LPVOID, w.ULONG)
        self.get_hat = function(hid, 'HidP_GetUsageValue', w.LONG, c.c_int, w.USHORT, w.USHORT, w.USHORT,
                                pointer(w.ULONG), w.LPVOID, w.LPVOID, w.ULONG)
        self.get_value_caps = function(hid, 'HidP_GetSpecificValueCaps', w.LONG, c.c_int, w.USHORT, w.USHORT,
                                       w.USHORT, w.LPVOID, pointer(w.USHORT), w.LPVOID)
        create = function(user, 'CreateWindowExW', w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                          c.c_int, c.c_int, c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID)
        self.window = create(0, 'STATIC', None, 0, 0, 0, 0, 0, w.HWND(-3), None, None, None)
        if not self.window:
            raise c.WinError(c.get_last_error())
        regs = (Registration * len(self.usages))(*(Registration(p, u, 0x2100, self.window) for p, u in self.usages))
        if not self.register(regs, len(regs), c.sizeof(Registration)):
            error = c.WinError(c.get_last_error())
            self.destroy(self.window)
            raise error
        self.devices = {}
        self.held_shortcuts = {}
        self.last_pid = None
        self.game = None

    def close(self):
        regs = (Registration * len(self.usages))(*(Registration(p, u, 1, None) for p, u in self.usages))
        self.register(regs, len(regs), c.sizeof(Registration))
        self.destroy(self.window)

    def foreground_game(self):
        pid = w.DWORD()
        self.window_pid(self.foreground(), c.byref(pid))
        if pid.value != self.last_pid:
            self.last_pid, self.game = pid.value, None
            process = self.open_process(0x1000, False, pid.value)
            if process:
                try:
                    size = w.DWORD(32768)
                    name = c.create_unicode_buffer(size.value)
                    if self.process_name(process, 0, name, c.byref(size)):
                        exe = Path(name.value).name.lower()
                        if exe in GAME_PROCESSES:
                            self.game = 'iracing'
                        elif exe == 'le mans ultimate.exe':
                            self.game = 'lmu'
                finally:
                    self.close_handle(process)
        return self.game

    def descriptor(self, device):
        size = w.UINT()
        if self.info(device, 0x20000005, None, c.byref(size)) != 0 or not size.value:
            return None
        descriptor = c.create_string_buffer(size.value)
        if self.info(device, 0x20000005, descriptor, c.byref(size)) == 0xffffffff:
            return None
        if self.info(device, 0x20000007, None, c.byref(size)) != 0:
            return None
        name = c.create_unicode_buffer(size.value + 1)
        if self.info(device, 0x20000007, name, c.byref(size)) == 0xffffffff:
            return None
        identity = hashlib.sha256(name.value.lower().encode()).hexdigest()[:24]
        # HIDP_CAPS contains 32 USHORTs. NumberInputDataIndices sizes the usage list.
        caps = (w.USHORT * 32)()
        if self.get_caps(descriptor, caps) < 0:
            return None
        # HIDP_VALUE_CAPS.LogicalMin/Max at offsets 40/44. Hats can start at 0 or 1.
        hat_caps = c.create_string_buffer(72)
        count = w.USHORT(1)
        hat = None
        if self.get_value_caps(0, 1, 0, 0x39, hat_caps, c.byref(count), descriptor) >= 0:
            hat = (c.c_int32.from_buffer(hat_caps, 40).value, c.c_int32.from_buffer(hat_caps, 44).value)
        return descriptor, identity, max(1, caps[25]), hat

    def report(self, device, report):
        if device not in self.devices:
            self.devices[device] = self.descriptor(device)
        entry = self.devices[device]
        if entry is None:
            return []
        descriptor, identity, maximum, hat = entry
        prefix = f'hid:{identity}:'
        changes = []
        usages = (w.USHORT * maximum)()
        count = w.ULONG(maximum)
        if self.get_usages(0, 9, 0, usages, c.byref(count), descriptor, report, len(report)) >= 0:
            changes.append((prefix + f'buttons:{report[0]}', {prefix + f'b{n}' for n in usages[:count.value]}))
        if hat:
            value = w.ULONG()
            if self.get_hat(0, 1, 0, 0x39, c.byref(value), descriptor, report, len(report)) >= 0:
                active = set()
                if hat[0] <= value.value <= hat[1]:
                    active.add(prefix + f'h{value.value - hat[0]}')
                changes.append((prefix + 'hat', active))
        return changes

    def changes(self):
        changes = []
        message = w.MSG()
        while self.peek(c.byref(message), self.window, 0, 0, 1):
            if message.message == 0xfe and message.wParam == 2:
                entry = self.devices.pop(message.lParam, None)
                if entry:
                    changes.append(('removed', 'hid:' + entry[1] + ':'))
            elif message.message == 0xff:
                size = w.UINT()
                header_size = c.sizeof(Header)
                if self.raw(message.lParam, 0x10000003, None, c.byref(size), header_size) == 0 and size.value >= header_size + 8:
                    data = c.create_string_buffer(size.value)
                    if self.raw(message.lParam, 0x10000003, data, c.byref(size), header_size) == size.value:
                        header = Header.from_buffer(data)
                        stride, count = (w.DWORD * 2).from_buffer(data, header_size)
                        payload = data.raw[header_size + 8:]
                        if header.kind == 2 and stride and stride * count <= len(payload):
                            for start in range(0, stride * count, stride):
                                changes.extend(self.report(header.device, payload[start:start + stride]))
            self.dispatch(c.byref(message))
        return changes

    def shortcuts_down(self, bindings):
        if not bindings:
            self.held_shortcuts.clear()
            return set()
        down = {vk for vk in {vk for _, vk, _ in bindings} if self.keys(vk) & 0x8000}
        mods = modifier_mask(self.keys) if down - self.held_shortcuts.keys() else 0
        self.held_shortcuts = {vk: self.held_shortcuts.get(vk, mods) for vk in down}
        return {key for key, vk, mask in bindings if self.held_shortcuts.get(vk) == mask}
