"""Read-only game window visibility, independent of keyboard/window focus."""
from pathlib import Path

GAME_PROCESSES={'iracingsim64dx11.exe','iracingsim64dx12.exe','iracingsim64.exe'}


class WindowsGameWindows:
    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        self.ctypes,self.w=ctypes,w
        self.user=ctypes.WinDLL('user32',use_last_error=True)
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.callback_type=ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
        self.user.EnumWindows.argtypes=[self.callback_type,w.LPARAM]
        self.user.IsWindowVisible.argtypes=[w.HWND]
        self.user.IsIconic.argtypes=[w.HWND]
        self.user.GetClientRect.argtypes=[w.HWND,ctypes.POINTER(w.RECT)]
        self.user.ClientToScreen.argtypes=[w.HWND,ctypes.POINTER(w.POINT)]
        self.user.WindowFromPoint.argtypes=[w.POINT]
        self.user.WindowFromPoint.restype=w.HWND
        self.user.GetWindowThreadProcessId.argtypes=[w.HWND,ctypes.POINTER(w.DWORD)]
        self.kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD]
        self.kernel.OpenProcess.restype=w.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes=[w.HANDLE,w.DWORD,w.LPWSTR,ctypes.POINTER(w.DWORD)]
        self.kernel.CloseHandle.argtypes=[w.HANDLE]

    def is_game(self,handle,cache):
        if handle in cache:return cache[handle]
        pid=self.w.DWORD()
        self.user.GetWindowThreadProcessId(handle,self.ctypes.byref(pid))
        process=self.kernel.OpenProcess(0x1000,False,pid.value)
        result=False
        if process:
            try:
                capacity=self.w.DWORD(32768)
                buffer=self.ctypes.create_unicode_buffer(capacity.value)
                if self.kernel.QueryFullProcessImageNameW(process,0,buffer,self.ctypes.byref(capacity)):
                    result=Path(buffer.value).name.lower() in GAME_PROCESSES
            finally:
                self.kernel.CloseHandle(process)
        cache[handle]=result
        return result

    def rectangles(self):
        result=[];cache={}
        @self.callback_type
        def visit(handle,_):
            if self.user.IsWindowVisible(handle) and not self.user.IsIconic(handle) and self.is_game(handle,cache):
                rect=self.w.RECT();origin=self.w.POINT()
                if self.user.GetClientRect(handle,self.ctypes.byref(rect)) and self.user.ClientToScreen(handle,self.ctypes.byref(origin)):
                    if rect.right>rect.left and rect.bottom>rect.top:
                        result.append(dict(left=origin.x,top=origin.y,width=rect.right-rect.left,height=rect.bottom-rect.top))
            return True
        self.user.EnumWindows(visit,0)
        return result

    def points_are_game(self,points):
        cache={}
        return all(self.is_game(self.user.WindowFromPoint(self.w.POINT(x,y)),cache) for x,y in points)


class GameVisibility:
    def __init__(self,windows=None,points=None):
        native=WindowsGameWindows() if windows is None or points is None else None
        self.windows=windows or native.rectangles
        self.points=points or native.points_are_game

    def window_visible(self,capture):
        for window in self.windows():
            if (max(capture['left'],window['left'])<min(capture['left']+capture['width'],window['left']+window['width'])
                    and max(capture['top'],window['top'])<min(capture['top']+capture['height'],window['top']+window['height'])):
                return True
        return False

    def pressure_visible(self,capture,boxes):
        # Check the newly located digits rather than the old location. Another
        # monitor's active window does not affect these desktop points.
        points=[]
        for a,b,c,d in boxes.values():
            for x,y in (((a+c)//2,(b+d)//2),(a+1,b+1),(c-2,d-2)):
                points.append((x+capture['left'],y+capture['top']))
        return bool(points) and self.points(points)
