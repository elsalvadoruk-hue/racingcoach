"""Race result and the approved metal trophies in the native 390 x 150 HUD."""
from functools import lru_cache
from math import cos, sin, pi
from pathlib import Path
from random import Random
from PIL import Image, ImageChops, ImageDraw, ImageFont


def animations_enabled():
    """Respect Windows' accessibility animation preference without changing it."""
    try:
        import ctypes
        enabled=ctypes.c_bool(True)
        if ctypes.windll.user32.SystemParametersInfoW(0x1042,0,ctypes.byref(enabled),0):
            return enabled.value
    except (AttributeError,OSError):
        pass
    return True

ASSET=Path(__file__).with_name('assets')/'podium-trophies.png'
TONES={1:'#f7d783',2:'#e0e9f4',3:'#e9b28d'}


def lap_text(value):
    if not isinstance(value,(int,float)) or not 0<value<86400:return '—'
    millis=round(value*1000); minutes,rest=divmod(millis,60000)
    return f'{minutes}:{rest//1000:02d}.{rest%1000:03d}'


@lru_cache(maxsize=48)
def font(size,bold=False):
    path=Path('C:/Windows/Fonts')/('malgunbd.ttf' if bold else 'malgun.ttf')
    return ImageFont.truetype(str(path),size) if path.exists() else ImageFont.load_default(size=size)


class RaceResultRenderer:
    DURATION=6.
    def __init__(self):
        self.cache={};self.sheet=None;self.cups={}
        rand=Random(51)
        self.particles=[(rand.choice((0,1)),rand.uniform(10,60),rand.uniform(5,130),rand.uniform(30,90),rand.uniform(.5,2.5)) for _ in range(32)]

    def trophy(self,rank,size):
        key=(rank,size)
        if key in self.cups:return self.cups[key]
        if self.sheet is None:
            with Image.open(ASSET) as source:self.sheet=source.convert('RGB')
        left,right={1:(0,640),2:(620,1160),3:(1165,1774)}[rank]
        crop=self.sheet.crop((left,0,right,887))
        crop.thumbnail(size,Image.Resampling.LANCZOS)
        # Composite the approved dark studio matte against the same HUD surface.
        crop=ImageChops.lighter(crop,Image.new('RGB',crop.size,'#090d13')).convert('RGBA')
        self.cups[key]=crop
        if len(self.cups)>12:self.cups.pop(next(iter(self.cups)))
        return crop

    def frame(self,result,elapsed,scale_x=1.,scale_y=1.,celebrate=False,reduced_motion=False):
        width,height=round(390*scale_x),round(150*scale_y)
        rank=result.get('finish_position')
        podium=celebrate and rank in TONES and result.get('status')=='FINISHED' and elapsed<self.DURATION
        key=(repr(sorted(result.items())),width,height,podium)
        if key not in self.cache:
            self.cache={key:self.base(result,scale_x,scale_y,podium)}
        frame=self.cache[key].copy()
        if not podium or reduced_motion:return frame
        draw=ImageDraw.Draw(frame)
        # One small travelling reflection; avoid a full-screen flash.
        if 1.1 <= elapsed < 1.85:
            px=64+78*(elapsed-1.1)/.75;py=41
            draw.line(((px-4)*scale_x,py*scale_y,(px+4)*scale_x,py*scale_y),fill='#fff5d6',width=1)
            draw.line((px*scale_x,(py-3)*scale_y,px*scale_x,(py+3)*scale_y),fill='#fff5d6',width=1)
        if elapsed<2.8:
            tone=TONES[rank]
            for side,x,y,speed,delay in self.particles:
                t=elapsed-delay*.15
                if not 0<t<2.3:continue
                px=(x+speed*t*.25) if side==0 else 390-x-speed*t*.25
                py=y+35*t*t
                if 0<py<150:
                    draw.rectangle((px*scale_x,py*scale_y,(px+2)*scale_x,(py+1)*scale_y),fill=tone)
        if elapsed<.45:
            ratio=.88+.12*min(1.,elapsed/.45)
            small=frame.resize((max(1,round(width*ratio)),max(1,round(height*ratio))),Image.Resampling.LANCZOS)
            frame=Image.new('RGBA',(width,height));frame.alpha_composite(small,((width-small.width)//2,(height-small.height)//2))
        return frame

    def base(self,result,sx,sy,podium):
        # Draw at 2x then downsample, matching the graph's antialiasing path.
        aa=2;w,h=round(390*sx),round(150*sy)
        image=Image.new('RGBA',(w*aa,h*aa));draw=ImageDraw.Draw(image)
        x,y=lambda v:round(v*sx*aa),lambda v:round(v*sy*aa)
        fs=min(sx,sy)*aa
        def text(px,py,value,size=10,color='#bed0e6',bold=False,anchor=None):
            draw.text((x(px),y(py)),str(value),font=font(max(1,round(size*fs)),bold),fill=color,anchor=anchor)
        rank=result.get('finish_position');tone=TONES.get(rank,'#8ddce7')
        text(14,8,'RACE FINISHED' if podium else 'RACE RESULT',7,'#99aebf')
        if podium:
            cup=self.trophy(rank,(x(116),y(116)))
            image.alpha_composite(cup,(x(103)-cup.width//2,y(18)))
            for side in (-1,1):
                points=[]
                for i in range(9):
                    angle=(18+i*16)*pi/180;cx,cy=269+side*sin(angle)*42,60+cos(angle)*45
                    points.append((x(cx),y(cy)))
                    rotation=side*(65-i*12)*pi/180
                    leaf=[]
                    for n in range(24):
                        t=2*pi*n/24;dx,dy=3.1*cos(t),8.2*sin(t)
                        leaf.append((x(cx+dx*cos(rotation)-dy*sin(rotation)),
                                     y(cy+dx*sin(rotation)+dy*cos(rotation))))
                    draw.polygon(leaf,fill=tone)
                draw.line(points,fill=tone,width=max(1,x(.6)))
            text(269,52,rank,53,tone,True,'mm')
            text(269,112,f'클래스 {rank}위',10,tone,True,'mm')
            text(269,132,'우승을 축하합니다' if rank==1 else '멋진 포디움 피니시',8,anchor='mm')
        else:
            text(376,8,'잠정 결과 · 클래스' if result.get('provisional') else '클래스 기준',8,anchor='ra')
            start=result.get('start_position');status=result.get('status')
            text(15,29,f'{start}위' if start else '—',24,'#aab9c8',True)
            text(79,30,'→',20,'#657e94')
            text(113,24,f'{rank}위' if rank and status=='FINISHED' else status if status!='FINISHED' else '—',30,tone,True)
            if start and rank and status=='FINISHED':
                diff=start-rank
                text(376,39,'순위 유지' if diff==0 else f'{abs(diff)}계단 '+('상승' if diff>0 else '하락'),9,'#7de5bd' if diff>=0 else '#e9b28d',anchor='ra')
            draw.line([(x(14),y(65)),(x(376),y(65))],fill='#253646',width=1)
            draw.line([(x(203),y(80)),(x(203),y(139))],fill='#253646',width=1)
            text(15,75,'내 베스트 랩',8);text(15,90,lap_text(result.get('best_lap_s')),20,'#eff6fc',True)
            count,total=result.get('recorded_drivers',0),result.get('class_drivers',0)
            label='베스트 랩 순위' if total and count==total else '기록 확인 차량 중'
            text(15,126,label,8)
            best_rank=result.get('best_lap_rank')
            text(188,126,f'{best_rank} / {count}명' if best_rank and count else '—',9,tone,True,'ra')
            text(219,75,'경기 최고 랩' if total and count==total else '확인된 최고 랩',8)
            text(219,91,lap_text(result.get('fastest_lap_s')),18,'#c2d0dd')
            text(219,126,'최고 랩과',8)
            gap=result.get('best_gap_s')
            text(375,126,f'+{gap:.3f}초' if gap is not None else '—',9,anchor='ra')
        return image.resize((w,h),Image.Resampling.LANCZOS)
