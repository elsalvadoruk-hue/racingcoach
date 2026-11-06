"""Compact numeric tire panel, with independently composited text and backdrop."""
import tkinter as tk
from .positions import TirePositions, position_car
from .pressure_ocr import WHEELS

BACKGROUND='#11161d'
FOREGROUND='#edf3f8'
MUTED='#aab8c8'
TRANSPARENT='#010101'


class PressureOverlay:
    width=220
    height=108

    def __init__(self,parent,settings_path,on_settings=None,on_stop=None,*,
                 background_opacity=85,content_opacity=100,radius=12):
        from scripts.overlay_native import NativeWindows
        self.native=NativeWindows()
        self.parent=parent
        self.positions=TirePositions(settings_path)
        self.car=None
        self.position=self.positions.load()
        self.on_settings=on_settings or (lambda:None)
        self.on_stop=on_stop or (lambda:None)
        self.locked=False
        self.drag=None
        self.visible=False
        self.window=tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title('RacingCoach · 타이어 압력·온도')
        self.window.overrideredirect(True)
        self.window.attributes('-topmost',True)
        self.window.attributes('-transparentcolor',TRANSPARENT)
        self.window.configure(bg=TRANSPARENT)
        self.background=tk.Toplevel(parent)
        self.background.withdraw()
        self.background.title('RacingCoach · 타이어 배경')
        self.background.overrideredirect(True)
        self.background.attributes('-topmost',True)
        self.background.configure(bg=BACKGROUND)
        self.canvas=tk.Canvas(self.window,bg=TRANSPARENT,highlightthickness=0,
                              width=self.width,height=self.height)
        self.canvas.pack(fill='both',expand=True)
        self.labels={}
        self.temperature_labels={}
        for index,wheel in enumerate(WHEELS):
            x=55+(index%2)*110
            y=22+(index//2)*52
            self.labels[wheel]=self.canvas.create_text(x,y,text='—',fill=FOREGROUND,font=('Segoe UI',-26,'bold'))
            self.temperature_labels[wheel]=self.canvas.create_text(x,y+24,text='—',fill=MUTED,font=('Segoe UI',-15))
        menu=tk.Menu(self.window,tearoff=False)
        menu.add_command(label='위치 초기화',command=self.reset_position)
        menu.add_command(label='오버레이 닫기',command=self.on_stop)
        self.menu=menu
        for window in (self.window,self.background):
            window.bind('<ButtonPress-1>',self.press)
            window.bind('<B1-Motion>',self.motion)
            window.bind('<ButtonRelease-1>',self.release)
            window.bind('<Button-3>',lambda event:self.menu.tk_popup(event.x_root,event.y_root))
        self.set_appearance(background_opacity=background_opacity,content_opacity=content_opacity,radius=radius)

    def _save_position(self):
        if self.position is not None:
            try:self.positions.save(self.car,self.position)
            except OSError:pass

    def set_car(self,car):
        car=position_car(car)
        if car is None or car==self.car:return False
        self._save_position()
        self.car=car
        self.position=self.positions.load(car)
        self.drag=None
        return True

    def _place(self,x,y):
        self.position=(int(x),int(y))
        for window in (self.window,self.background):
            window.geometry(f'{self.width}x{self.height}')
        self.window.update_idletasks()
        self.native.user.SetWindowPos(self.native.handle(self.window),-1,*self.position,
                                      self.width,self.height,0x0010)
        self.native.place_behind(self.background,self.window,*self.position,self.width,self.height)
        for window in (self.window,self.background):
            self.native.round_corners(window,self.width,self.height,self.radius)

    def set_appearance(self,*,background_opacity=None,content_opacity=None,radius=None):
        if background_opacity is not None:
            self.background_opacity=max(0,min(100,int(background_opacity)))
            self.background.attributes('-alpha',self.background_opacity/100)
        if content_opacity is not None:
            self.content_opacity=max(0,min(100,int(content_opacity)))
            self.window.attributes('-alpha',self.content_opacity/100)
        if radius is not None:self.radius=max(0,min(32,int(radius)))
        self.window.update_idletasks()
        for window in (self.window,self.background):
            self.native.round_corners(window,self.width,self.height,self.radius)

    def set_locked(self,locked):
        self.locked=bool(locked)
        self.drag=None
        self.window.update_idletasks()
        for window in (self.window,self.background):
            self.native.click_through(window,self.locked,no_activate=True)

    def reset_position(self,capture=None):
        capture=capture or dict(left=0,top=0,width=self.parent.winfo_screenwidth(),height=self.parent.winfo_screenheight())
        self._place(capture['left']+max(0,capture['width']-self.width-24),capture['top']+24)
        self._save_position()

    def show(self,capture=None):
        if self.position is None:
            self.reset_position(capture)
        else:
            api=self.native.user
            left,top=api.GetSystemMetrics(76),api.GetSystemMetrics(77)
            right,bottom=left+api.GetSystemMetrics(78),top+api.GetSystemMetrics(79)
            x,y=self.position
            if x+self.width<=left or y+self.height<=top or x>=right or y>=bottom:
                self.reset_position(capture)
            else:self._place(x,y)
        self.set_locked(self.locked)
        with self.native.block_activation():
            self.background.deiconify()
            self.window.deiconify()
            self.native.show_without_activation(self.background)
            self.native.show_without_activation(self.window)
        # Mapping a previously withdrawn Tk window can apply its pending default
        # position. Set the physical coordinates after both layers are mapped.
        self._place(*self.position)
        self.visible=True

    def hide(self):
        self.window.withdraw()
        self.background.withdraw()
        self.visible=False

    def render(self,display):
        for wheel,value in display.formatted().items():
            self.canvas.itemconfigure(self.labels[wheel],text=value)
        for wheel,value in display.formatted_temperatures().items():
            self.canvas.itemconfigure(self.temperature_labels[wheel],text=value.removesuffix('°C'))

    def render_advice(self,values):
        """Garage: advised cold pressures (bar) in the same 2x2 layout, marked as advice."""
        for wheel in WHEELS:
            self.canvas.itemconfigure(self.labels[wheel],text=values.get(wheel) or '—')
            self.canvas.itemconfigure(self.temperature_labels[wheel],text='추천 냉간')

    def press(self,event):
        if not self.locked and self.position is not None:
            self.drag=(event.x_root,event.y_root,*self.position)

    def motion(self,event):
        if self.locked or self.drag is None:return
        mx,my,x,y=self.drag
        self._place(x+event.x_root-mx,y+event.y_root-my)

    def release(self,event):
        if self.drag is not None:
            self.drag=None
            self._save_position()

    def close(self):
        self._save_position()
        self.background.destroy()
        self.window.destroy()
