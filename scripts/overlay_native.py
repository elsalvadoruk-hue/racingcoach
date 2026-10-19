"""Win32 window ownership, click-through, placement and monitor geometry."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
from contextlib import contextmanager


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('width', wintypes.LONG), ('height', wintypes.LONG),
                ('planes', wintypes.WORD), ('bits', wintypes.WORD), ('compression', wintypes.DWORD),
                ('image_size', wintypes.DWORD), ('x_ppm', wintypes.LONG), ('y_ppm', wintypes.LONG),
                ('colors_used', wintypes.DWORD), ('colors_important', wintypes.DWORD)]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [('header', _BitmapInfoHeader), ('colors', wintypes.DWORD * 1)]


class _BlendFunction(ctypes.Structure):
    _fields_ = [('operation', ctypes.c_ubyte), ('flags', ctypes.c_ubyte),
                ('opacity', ctypes.c_ubyte), ('alpha', ctypes.c_ubyte)]


class LayeredImage:
    """An atomic, premultiplied-alpha image below the text and above the backdrop.

    Tk's color-key window cannot compose a PhotoImage's partial alpha correctly:
    its edges are first mixed with the color key and become opaque dark fringes.
    Windows composes this separate image surface directly over the actual scene.
    """

    def __init__(self, native):
        self.native, self.user, self.gdi = native, native.user, native.gdi
        self.window = self.dc = self.bitmap = self.original_bitmap = None
        self.bits = ctypes.c_void_p()
        self.size, self.offset = None, (0, 0)
        self.clip = None
        self.has_frame = self.visible = False
        self.user.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
        self.user.CreateWindowExW.restype = wintypes.HWND
        self.user.DestroyWindow.argtypes = [wintypes.HWND]
        self.user.DestroyWindow.restype = wintypes.BOOL
        self.user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user.ShowWindow.restype = wintypes.BOOL
        self.user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user.GetWindowRect.restype = wintypes.BOOL
        self.user.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC,
            ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
            ctypes.POINTER(wintypes.POINT), wintypes.COLORREF, ctypes.POINTER(_BlendFunction), wintypes.DWORD]
        self.user.UpdateLayeredWindow.restype = wintypes.BOOL
        self.gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self.gdi.CreateCompatibleDC.restype = wintypes.HDC
        self.gdi.DeleteDC.argtypes = [wintypes.HDC]
        self.gdi.DeleteDC.restype = wintypes.BOOL
        self.gdi.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(_BitmapInfo),
            wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
        self.gdi.CreateDIBSection.restype = wintypes.HANDLE
        self.gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
        self.gdi.SelectObject.restype = wintypes.HANDLE
        try:
            # Layered, click-through, tool window and no activation; no taskbar item.
            self.window = self.user.CreateWindowExW(0x080800A8, 'STATIC', '', 0x80000000,
                                                    0, 0, 1, 1, None, None, None, None)
            if not self.window:
                raise ctypes.WinError(ctypes.get_last_error())
            self.dc = self.gdi.CreateCompatibleDC(None)
            if not self.dc:
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise

    def present(self, widget, frame, offset, opacity, visible):
        width, height = frame.size
        if self.size != frame.size:
            info = _BitmapInfo()
            info.header = _BitmapInfoHeader(ctypes.sizeof(_BitmapInfoHeader), width, -height,
                                             1, 32, 0, width * height * 4, 0, 0, 0, 0)
            bits = ctypes.c_void_p()
            bitmap = self.gdi.CreateDIBSection(self.dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
            if not bitmap:
                raise ctypes.WinError(ctypes.get_last_error())
            previous = self.gdi.SelectObject(self.dc, bitmap)
            if not previous or previous == ctypes.c_void_p(-1).value:
                error = ctypes.get_last_error()
                self.gdi.DeleteObject(bitmap)
                raise ctypes.WinError(error)
            if self.bitmap:
                self.gdi.DeleteObject(self.bitmap)
            else:
                self.original_bitmap = previous
            self.bitmap, self.bits, self.size = bitmap, bits, frame.size
        # UpdateLayeredWindow requires BGRA with each color multiplied by alpha.
        pixels = frame.convert('RGBa').tobytes('raw', 'BGRa')
        ctypes.memmove(self.bits, pixels, len(pixels))
        self.offset, self.has_frame = offset, True
        self.sync(widget, opacity, visible)

    def sync(self, widget, opacity, visible):
        if not self.has_frame or not visible:
            if self.visible:
                self.user.ShowWindow(self.window, 0)
            self.visible = False
            return
        owner = self.native.handle(widget)
        rect = wintypes.RECT()
        if not self.user.GetWindowRect(owner, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        rounded = self.native.rounded_sizes.get(owner)
        clip = (rounded, self.offset)
        if rounded and clip != self.clip:
            width, height, radius = rounded
            dx, dy = self.offset
            region = self.gdi.CreateRoundRectRgn(-dx, -dy, width-dx+1, height-dy+1, radius*2, radius*2)
            if not region:
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.user.SetWindowRgn(self.window, region, True):
                self.gdi.DeleteObject(region)
                raise ctypes.WinError(ctypes.get_last_error())
            self.clip = clip
        target = wintypes.POINT(rect.left + self.offset[0], rect.top + self.offset[1])
        size, source = wintypes.SIZE(*self.size), wintypes.POINT(0, 0)
        blend = _BlendFunction(0, 0, round(max(0, min(100, opacity)) * 255 / 100), 1)
        if not self.user.UpdateLayeredWindow(self.window, None, ctypes.byref(target), ctypes.byref(size),
                self.dc, ctypes.byref(source), 0, ctypes.byref(blend), 2):
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.visible:
            self.user.ShowWindow(self.window, 4)  # SW_SHOWNOACTIVATE
            self.visible = True
        # Keep text on top; inserting here also keeps this above the background.
        self.user.SetWindowPos(self.window, owner, target.x, target.y, 0, 0, 0x0011)

    def clear(self):
        self.has_frame = False
        if self.visible:
            self.user.ShowWindow(self.window, 0)
        self.visible = False

    def close(self):
        if self.dc and self.original_bitmap:
            self.gdi.SelectObject(self.dc, self.original_bitmap)
        if self.bitmap:
            self.gdi.DeleteObject(self.bitmap)
        if self.dc:
            self.gdi.DeleteDC(self.dc)
        if self.window:
            self.user.DestroyWindow(self.window)
        self.window = self.dc = self.bitmap = self.original_bitmap = None
        self.has_frame = self.visible = False


class NativeWindows:
    """Keep pointer-sized Win32 signatures explicit on 64-bit Python."""

    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        self.gdi.CreateRoundRectRgn.argtypes = [ctypes.c_int]*6
        self.gdi.CreateRoundRectRgn.restype = wintypes.HANDLE
        self.gdi.DeleteObject.argtypes = [wintypes.HANDLE]
        self.gdi.DeleteObject.restype = wintypes.BOOL
        self.user.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
        self.user.SetWindowRgn.restype = ctypes.c_int
        self.rounded_sizes = {}
        self.image_layer = None
        self.user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user.GetAncestor.restype = wintypes.HWND
        long_type = ctypes.c_ssize_t
        self.get_style = self.user.GetWindowLongPtrW
        self.get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        self.get_style.restype = long_type
        self.set_style = self.user.SetWindowLongPtrW
        self.set_style.argtypes = [wintypes.HWND, ctypes.c_int, long_type]
        self.set_style.restype = long_type
        self.user.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        self.user.SetWindowPos.restype = wintypes.BOOL
        self.user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user.ShowWindow.restype = wintypes.BOOL

    @contextmanager
    def block_activation(self, enabled=True):
        """Veto managed HUD activation on this GUI thread only."""
        if not enabled:
            yield
            return
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                          wintypes.WPARAM, wintypes.LPARAM)
        self.user.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type,
                                               wintypes.HINSTANCE, wintypes.DWORD]
        self.user.SetWindowsHookExW.restype = wintypes.HANDLE
        self.user.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                           wintypes.WPARAM, wintypes.LPARAM]
        self.user.CallNextHookEx.restype = ctypes.c_ssize_t
        self.user.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
        self.user.UnhookWindowsHookEx.restype = wintypes.BOOL
        def prevent(code, window, data):
            if code in (5, 9):  # HCBT_ACTIVATE / HCBT_SETFOCUS; mouse editing still works.
                return 1
            return self.user.CallNextHookEx(None, code, window, data)
        callback = callback_type(prevent)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        thread = kernel.GetCurrentThreadId()
        hook = self.user.SetWindowsHookExW(5, callback, None, thread)  # WH_CBT
        if not hook:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            self.user.UnhookWindowsHookEx(hook)

    def present_image(self, widget, frame, offset, opacity, visible):
        if self.image_layer is None:
            self.image_layer = LayeredImage(self)
        self.image_layer.present(widget, frame, offset, opacity, visible and widget.winfo_viewable())

    def sync_image(self, widget, opacity, visible):
        if self.image_layer is not None:
            self.image_layer.sync(widget, opacity, visible and widget.winfo_viewable())

    def clear_image(self):
        if self.image_layer is not None:
            self.image_layer.clear()

    def close_image(self):
        if self.image_layer is not None:
            self.image_layer.close()
            self.image_layer = None

    def handle(self, widget):
        return self.user.GetAncestor(widget.winfo_id(), 2)

    def round_corners(self, widget, width, height, radius):
        hwnd = self.handle(widget)
        size = (width, height, radius)
        if self.rounded_sizes.get(hwnd) == size:
            return
        region = self.gdi.CreateRoundRectRgn(0, 0, width+1, height+1, radius*2, radius*2)
        if not region:
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.user.SetWindowRgn(hwnd, region, True):
            self.gdi.DeleteObject(region)
            raise ctypes.WinError(ctypes.get_last_error())
        # Windows owns a successfully assigned region; never delete that handle.
        self.rounded_sizes[hwnd] = size

    def move(self, widget, x, y):
        # Absolute desktop coordinates also work on monitors left of the primary.
        self.user.SetWindowPos(self.handle(widget), None, x, y, 0, 0, 0x0015)

    def show_without_activation(self, widget):
        # Tk observes WM_SHOWWINDOW and updates its mapped state. Its own
        # deiconify path can activate the HUD despite WS_EX_NOACTIVATE.
        self.user.ShowWindow(self.handle(widget), 4)  # SW_SHOWNOACTIVATE

    def click_through(self, widget, enabled, no_activate=False):
        hwnd = self.handle(widget)
        style = self.get_style(hwnd, -20)
        flags = 0x00000020 | 0x08000000  # WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
        updated = style | flags if enabled else style & ~flags
        if no_activate:
            updated |= 0x08000000
        ctypes.set_last_error(0)
        previous = self.set_style(hwnd, -20, updated)
        if not previous and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        self.user.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037)

    def place_behind(self, widget, foreground, x, y, width, height):
        self.user.SetWindowPos(self.handle(widget), self.handle(foreground),
                               x, y, width, height, 0x0210)

    def primary_work_area(self):
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        self.user.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        self.user.MonitorFromPoint.restype = wintypes.HANDLE
        self.user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        self.user.GetMonitorInfoW.restype = wintypes.BOOL
        info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
        monitor = self.user.MonitorFromPoint(wintypes.POINT(0, 0), 1)
        if not self.user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        area = info.rcWork
        return area.left, area.top, area.right, area.bottom

    def work_areas(self):
        areas = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                          ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

        def collect(monitor, dc, bounds, data):
            class Info(ctypes.Structure):
                _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                            ("work", wintypes.RECT), ("flags", wintypes.DWORD)]
            info = Info(size=ctypes.sizeof(Info))
            self.user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Info)]
            if self.user.GetMonitorInfoW(monitor, ctypes.byref(info)):
                area = info.work
                areas.append((area.left, area.top, area.right, area.bottom))
            return True

        self.user.EnumDisplayMonitors(None, None, callback_type(collect), 0)
        return areas or [self.primary_work_area()]
