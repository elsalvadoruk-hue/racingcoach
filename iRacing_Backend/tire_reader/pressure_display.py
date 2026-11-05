"""Display the newest validated OCR value per wheel; failures keep prior numbers."""
from .sdk import finite
from .pressure_ocr import WHEELS,KPA_PER_UNIT


class PressureDisplay:
    def __init__(self,unit='bar',decimals=2):
        # Display units are fixed independently of OCR/input profiles.
        self.unit,self.decimals='bar',2
        self.reset()

    def reset(self):
        self.values=dict.fromkeys(WHEELS)
        self.temperatures=dict.fromkeys(WHEELS)
        self.temperature_kind=None
        self.status='측정 대기'
        self.updates=0

    def update(self,row):
        updated=False
        if row.get('status') in ('reading','waiting_for_agreement'):
            self.temperature_kind=row.get('temperature_kind',self.temperature_kind)
            for wheel in WHEELS:
                value=finite(row.get('raw_kpa_'+wheel))
                if value is not None and 40<=value<=400:
                    self.values[wheel]=value
                    updated=True
                temperature=finite(row.get('raw_temp_c_'+wheel))
                if temperature is not None and -50<=temperature<=250:
                    self.temperatures[wheel]=temperature
                    updated=True
        if updated:
            self.updates+=1
        self.status=('측정 중 · 1초 간격' if updated else
                     '이전 값 유지 · 다시 측정 중' if any(v is not None for v in (*self.values.values(),*self.temperatures.values())) else '측정 대기')
        return updated

    def formatted(self):
        return {w:f'{value/KPA_PER_UNIT[self.unit]:.{self.decimals}f}' if value is not None else '—'
                for w,value in self.values.items()}

    def formatted_temperatures(self):
        return {w:f'{value:.0f}°C' if value is not None else '—'
                for w,value in self.temperatures.items()}
