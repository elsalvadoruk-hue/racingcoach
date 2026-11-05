import unittest
import numpy as np
from tire_reader.color_state import states


class ColorTests(unittest.TestCase):
    def test_red_is_low_for_audi_and_aston_but_high_for_mclaren(self):
        image=np.full((40,60,3),(0,0,220),dtype=np.uint8);boxes={'FL':[0,0,60,40]}
        for car,expected in (('audir8lmsevo2gt3','low'),('amvantageevogt3','low'),('mclaren720sgt3','high')):
            pressure,temp=states(image,car,boxes,boxes)
            self.assertEqual(pressure['FL'],expected)
            if car=='mclaren720sgt3':self.assertEqual(temp['FL'],'hot')
            else:self.assertIsNone(temp['FL'])

    def test_blue_white_green_dark_unknown_car_and_outside_values(self):
        boxes={'FL':[0,0,60,40]}
        for bgr,car,expected in (((200,0,0),'mclaren720sgt3','low'),
            ((200,200,200),'mclaren720sgt3','ok'),((0,180,0),'chevyvettez06rgt3','ok'),
            ((5,5,5),'ferrari296gt3',None)):
            pressure,_=states(np.full((40,60,3),bgr,dtype=np.uint8),car,boxes,{})
            self.assertEqual(pressure['FL'],expected)
        image=np.zeros((40,60,3),dtype=np.uint8)
        self.assertEqual(states(image,'bmwm4gt3',boxes,{}),({},{}))
        self.assertIsNone(states(image,'mclaren720sgt3',{'FL':[-1,0,60,40]},{} )[0]['FL'])
