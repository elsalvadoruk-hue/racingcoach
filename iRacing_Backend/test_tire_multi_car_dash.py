import unittest
import cv2,numpy as np
from tire_reader.multi_dash_locator import MultiCarDashLocator,VehicleDashLocator,ocr_frame
from tire_reader.pressure_ocr import load_image,make_profile
from tire_reader.vehicle_dash import ROOT,VEHICLES,canonical_car


class MultiCarTests(unittest.TestCase):
    def test_twelve_official_dashboards_have_visible_pressure_regions(self):
        self.assertEqual(len(VEHICLES),12)
        for car,spec in VEHICLES.items():
            with self.subTest(car=car):
                image,_=load_image(ROOT/'samples/gt3_dash'/spec['image'])
                found=MultiCarDashLocator(car).locate(image)
                self.assertEqual(found['status'],'located')
                self.assertEqual(set(found['location']['boxes']),{'FL','FR','RL','RR'})
                frame,pressure,temperature=ocr_frame(image,found['location'])
                profile=make_profile(frame,'','',pressure,'bar',2,car_path=car,
                    temperature_boxes=temperature,automatic_partial=True)
                self.assertEqual(profile['car_path'],car)
                if car in ('bmwm4gt3','acuransxevo22gt3','amvantageevogt3'):
                    self.assertFalse(temperature)

    def test_each_car_is_relocated_after_translation_scale_and_rotation(self):
        for car in VEHICLES:
            with self.subTest(car=car):
                locator=MultiCarDashLocator(car)
                reference=locator.reference
                for dx,dy,angle,scale in [(40,30,1.5,.9),(280,200,-2,.8)]:
                    rotation=cv2.getRotationMatrix2D((0,0),angle,scale)
                    rotation[:,2]+=(dx+20,dy+40)
                    image=cv2.warpAffine(reference,rotation,(1800,1150))
                    found=locator.locate(image)
                    self.assertEqual(found['status'],'located')
                    frame,p,t=ocr_frame(image,found['location'])
                    make_profile(frame,'','',p,'bar',2,car_path=car,temperature_boxes=t,automatic_partial=True)

    def test_partial_cup_excludes_cut_off_pressure_instead_of_extrapolating(self):
        spec=VEHICLES['porsche9922cup']
        image,_=load_image(ROOT/'samples/gt3_dash'/spec['image'])
        found=MultiCarDashLocator('porsche9922cup').locate(image[:551])
        self.assertIsNotNone(found['location'])
        self.assertEqual(found['location']['boxes'],{})
        self.assertEqual(set(found['location']['temperature_boxes']),{'FL','FR'})

    def test_ferrari_temperature_label_is_required_separately_from_pressure(self):
        locator=VehicleDashLocator('ferrari296gt3')
        image,_=load_image(ROOT/'samples/gt3_dash'/locator.spec['image'])
        found=locator.locate(image)
        self.assertTrue(found['location']['temperature_boxes'])
        a,b,c,d=locator.spec['temperature_anchor'];image[b:d,a:c]=0
        location,status=locator._validate(image,np.asarray(found['location']['homography']))
        self.assertEqual(status,'located')
        self.assertEqual(len(location['boxes']),4)
        self.assertFalse(location['temperature_boxes'])

    def test_sdk_car_selection_does_not_fall_back_to_another_dashboard(self):
        image,_=load_image(ROOT/'samples/gt3_dash'/VEHICLES['mclaren720sgt3']['image'])
        self.assertIsNone(MultiCarDashLocator('porsche9922cup').locate(image)['location'])
        self.assertIsNone(canonical_car('porsche992cup'))
        self.assertEqual(canonical_car('Porsche9922Cup'),'porsche9922cup')

    def test_changing_tire_numbers_do_not_anchor_tracking_to_the_manual_values(self):
        for car,spec in VEHICLES.items():
            with self.subTest(car=car):
                image,_=load_image(ROOT/'samples/gt3_dash'/spec['image'])
                for regions,text in [(spec['pressure'],'1.72'),(spec['temperature'],'87')]:
                    for a,b,c,d in regions.values():
                        image[b:d,a:c]=15
                        cv2.putText(image,text,(a+2,d-4),cv2.FONT_HERSHEY_SIMPLEX,(d-b)/45,(200,200,200),1)
                self.assertEqual(MultiCarDashLocator(car).locate(image)['status'],'located')


if __name__=='__main__':unittest.main()
