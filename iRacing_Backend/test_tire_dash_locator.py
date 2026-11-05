import unittest

import cv2
import numpy as np

from tire_reader.dash_locator import BmwDashLocator, corners


class LocatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.locator=BmwDashLocator()

    def scene(self, scale=.8, x=200, y=150, tilt=0):
        reference=self.locator.reference
        h,w=reference.shape[:2]
        H=np.array([[scale,tilt,x],[0,scale,y],[0.00002,0,1]],dtype=float)
        return cv2.warpPerspective(reference,H,(1600,1000)),H

    def test_translation_scale_and_perspective_map_wheels_in_order(self):
        for scale,x,y,tilt in ((.6,350,300,0),(.95,650,100,.08),(.8,30,500,-.03)):
            with self.subTest(scale=scale,x=x):
                self.locator.previous_bounds=None
                image,H=self.scene(scale,x,y,tilt)
                found=self.locator.locate(image)
                self.assertEqual(found['status'],'located',found)
                for wheel,box in self.locator.boxes.items():
                    expected=cv2.perspectiveTransform(corners(box).reshape(-1,1,2),H).reshape(-1,2)
                    observed=np.array(found['location']['quadrilaterals'][wheel])
                    self.assertLess(np.max(np.linalg.norm(expected-observed,axis=1)),4)

    def test_blank_wrong_page_occlusion_and_cutoff_return_no_location(self):
        image,_=self.scene()
        wrong_page=image.copy();wrong_page[145:300,190:375]=0
        cutoff,_=self.scene(y=900)
        for candidate in (np.zeros_like(image),wrong_page,cutoff):
            self.locator.previous_bounds=None
            found=self.locator.locate(candidate)
            self.assertIsNone(found['location'],found)

    def test_tracking_follows_shift_reacquires_and_never_reuses_failed_frame(self):
        self.locator.previous_bounds=None
        first,_=self.scene()
        self.assertEqual(self.locator.locate(first)['status'],'located')
        shifted,_=self.scene(x=230,y=160)
        result=self.locator.locate(shifted)
        self.assertEqual(result['status'],'located')
        self.assertEqual(result['metrics']['search'],'local')
        far,_=self.scene(x=1000,y=550)
        self.assertEqual(self.locator.locate(far)['status'],'located')
        self.assertIsNone(self.locator.locate(np.zeros((100,200,3),dtype=np.uint8))['location'])
        self.assertIsNone(self.locator.locate(np.zeros_like(first))['location'])
        self.assertIsNone(self.locator.previous_bounds)

    def test_mirrored_and_very_small_dashboard_are_rejected(self):
        image,_=self.scene(scale=.25)
        self.locator.previous_bounds=None
        self.assertIsNone(self.locator.locate(image)['location'])
        image,_=self.scene()
        self.assertIsNone(self.locator.locate(image[:,::-1].copy())['location'])

    def test_weak_hypothesis_only_guides_fresh_verified_matching(self):
        image,H=self.scene()
        approximate=H.copy();approximate[0,2]+=4;approximate[1,2]-=3
        hint=dict(inliers=10,inlier_ratio=.2)
        refined,reason,metrics=self.locator._refine_candidate(image,approximate,hint)
        self.assertEqual(reason,'located')
        self.assertGreaterEqual(metrics['inliers'],16)
        self.assertGreaterEqual(metrics['inlier_ratio'],.3)
        location,status=self.locator._validate(image,refined)
        self.assertEqual(status,'located')
        for wheel,box in self.locator.boxes.items():
            expected=cv2.perspectiveTransform(corners(box).reshape(-1,1,2),H).reshape(-1,2)
            observed=np.asarray(location['quadrilaterals'][wheel])
            self.assertLess(np.max(np.linalg.norm(expected-observed,axis=1)),4)
        # The same good-looking hypothesis cannot produce a location when the
        # new pixels contain no dashboard, nor recurse into another hypothesis.
        for frame,proposal in [(np.zeros_like(image),approximate),
                (image,np.array([[1,0,5000],[0,1,5000],[0,0,1]],float)),
                (image,np.array([[-1,0,800],[0,1,150],[0,0,1]],float))]:
            with self.subTest(proposal=proposal.tolist()):
                result,_,_=self.locator._refine_candidate(frame,proposal,hint)
                self.assertIsNone(result)

    def test_half_reference_keeps_original_wheel_coordinates_and_validation(self):
        image,H=self.scene(scale=.5)
        # A cover over the lower right of the panel leaves pressure cells visible.
        image[245:340,355:520]=10
        refined,reason,metrics=self.locator._find(image,downscale=False,half_reference=True)
        self.assertEqual(reason,'located')
        self.assertGreaterEqual(metrics['inliers'],16)
        self.assertGreaterEqual(metrics['inlier_ratio'],.3)
        location,status=self.locator._validate(image,refined)
        self.assertEqual(status,'located')
        for wheel,box in self.locator.boxes.items():
            expected=cv2.perspectiveTransform(corners(box).reshape(-1,1,2),H).reshape(-1,2)
            observed=np.asarray(location['quadrilaterals'][wheel])
            self.assertLess(np.max(np.linalg.norm(expected-observed,axis=1)),4)


if __name__=='__main__': unittest.main()
