"""Official dash pixels under transforms; no game, personal screenshots or TTS."""
import asyncio,itertools,sys,unittest
from unittest.mock import patch
import cv2,numpy as np
from tire_reader.multi_dash_locator import MultiCarDashLocator,ocr_frame
from tire_reader.pressure_ocr import WindowsPressureOcr,load_image,make_profile
from tire_reader.vehicle_dash import ROOT,VEHICLES


def scene(car,scale=.65,angle=3.,light=1.):
    spec=VEHICLES[car];image,_=load_image(ROOT/'samples/gt3_dash'/spec['image'])
    a,b,c,d=spec['crop'];image=image[b:d,a:c]
    matrix=cv2.getRotationMatrix2D((0,0),angle,scale);matrix[:,2]+=(330,190)
    return np.uint8(cv2.warpAffine(image,matrix,(1600,1000))*light)


class OtherDashTests(unittest.TestCase):
    def test_all_dashboard_locations_survive_dim_sunlight_without_previous_boxes(self):
        for car in VEHICLES:
            with self.subTest(car=car):
                locator=MultiCarDashLocator(car)
                self.assertIsNotNone(locator.locate(scene(car,scale=.8,angle=0,light=.15))['location'])
                self.assertIsNone(locator.locate(np.zeros((700,1100,3),np.uint8))['location'])

    def test_mercedes_page_indicator_must_be_visible_and_match_tire_page(self):
        car='mercedesamgevogt3';spec=VEHICLES[car]
        image,_=load_image(ROOT/'samples/gt3_dash'/spec['image']);locator=MultiCarDashLocator(car)
        good=locator.locate(image);self.assertIsNotNone(good['location'])
        # The shared gear/ABS headings are present on both pages. The page
        # marker must independently prove that tire data is shown.
        image[310:331,611:629]=0
        self.assertIsNone(locator.locate(image)['location'])

    @unittest.skipUnless(sys.platform=='win32','Windows OCR')
    def test_audi_small_tilt_keeps_all_temperature_digits(self):
        car='audir8lmsevo2gt3';image=scene(car);found=MultiCarDashLocator(car).locate(image)
        self.assertIsNotNone(found['location']);frame,p,t=ocr_frame(image,found['location'])
        profile=make_profile(frame,'','',p,'bar',2,car_path=car,temperature_boxes=t,
            temperature_unit='F',screen_unit='auto',automatic_partial=True)
        result=asyncio.run(WindowsPressureOcr().recognize(frame,profile))
        for wheel in ('FL','FR','RL','RR'):
            self.assertAlmostEqual(result['temperatures'][wheel]['celsius'],(112-32)*5/9)

    @unittest.skipUnless(sys.platform=='win32','Windows OCR')
    def test_corvette_small_cell_does_not_read_112_as_222(self):
        car='chevyvettez06rgt3';image=scene(car,scale=.5,angle=0);found=MultiCarDashLocator(car).locate(image)
        frame,p,t=ocr_frame(image,found['location'])
        profile=make_profile(frame,'','',p,'bar',2,car_path=car,temperature_boxes=t,
            temperature_unit='F',screen_unit='auto',automatic_partial=True)
        result=asyncio.run(WindowsPressureOcr().recognize(frame,profile))
        for wheel in ('FL','FR','RL','RR'):
            self.assertAlmostEqual(result['temperatures'][wheel]['celsius'],(112-32)*5/9)

    @unittest.skipUnless(sys.platform=='win32','Windows OCR')
    def test_mustang_wide_pressure_font_reads_the_four_actual_values(self):
        car='fordmustanggt3';image=scene(car,scale=.8,angle=0);found=MultiCarDashLocator(car).locate(image)
        frame,p,t=ocr_frame(image,found['location'])
        profile=make_profile(frame,'','',p,'bar',2,car_path=car,temperature_boxes=t,
            temperature_unit='F',screen_unit='auto',automatic_partial=True)
        result=asyncio.run(WindowsPressureOcr().recognize(frame,profile))
        for wheel,value in zip(('FL','FR','RL','RR'),(24.3,24.2,24.3,24.3)):
            self.assertAlmostEqual(result['wheels'][wheel]['kpa'],value*6.894757293)

    def test_cleaned_cells_still_reject_conflicting_or_single_valid_numbers(self):
        car='audir8lmsevo2gt3'
        engine=object.__new__(WindowsPressureOcr);engine.language='test';engine.languages=[];engine.max_dimension=2048
        profile=make_profile(np.zeros((60,160,3),np.uint8),'','',{},'bar',2,car_path=car,
            temperature_boxes={'FL':[0,0,60,30]},temperature_unit='C',automatic_partial=True)
        for readings in (['112','122','112'],['112','','']):
            with self.subTest(readings=readings):
                values=itertools.chain(readings,itertools.repeat(''))
                async def text(_):return next(values)
                with patch.object(engine,'_text',side_effect=text):result=asyncio.run(engine.recognize(np.zeros((60,160,3),np.uint8),profile))
                self.assertIsNone(result['temperatures']['FL']['celsius'])
