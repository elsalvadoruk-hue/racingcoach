"""Win32 window ownership, click-through, placement and monitor geometry."""
from __future__ import annotations
import ctypes
from ctypes import wintypes


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
