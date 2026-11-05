"""Data-driven dashboard localization, revalidated on every captured frame."""
import math,time
import cv2,numpy as np
from .dash_locator import BmwDashLocator,corners
from .pressure_ocr import load_image
from .vehicle_dash import ROOT,VEHICLES,canonical_car


class VehicleDashLocator(BmwDashLocator):
    def __init__(self,car_path):
        self.car_path=canonical_car(car_path)
        if self.car_path is None:raise ValueError('지원하지 않는 차량입니다.')
        self.spec=VEHICLES[self.car_path]
        image,self.reference_sha256=load_image(ROOT/'samples/gt3_dash'/self.spec['image'])
        x0,y0,x1,y1=self.spec['crop']
        self.reference=image[y0:y1,x0:x1]
        self.gray=cv2.cvtColor(self.reference,cv2.COLOR_BGR2GRAY)
        self.sift=cv2.SIFT_create(nfeatures=2500)
        self.keypoints,self.descriptors=self.sift.detectAndCompute(self.gray,None)
        relative=lambda box:[box[0]-x0,box[1]-y0,box[2]-x0,box[3]-y0]
        self.boxes={w:relative(b) for w,b in self.spec['pressure'].items()}
        self.temperature_boxes={w:relative(b) for w,b in self.spec['temperature'].items()}
        self.anchor=relative(self.spec['anchor'])
        self.temperature_anchor=relative(self.spec['temperature_anchor']) if self.spec.get('temperature_anchor') else None
        self.previous_bounds=None

    def locate(self,image):
        started=time.perf_counter()
        found=super().locate(image)
        if self.car_path!='ferrari296gt3' or found['location']:
            return found
        # Low sunlight contrast removes SIFT features before OCR even runs.
        # Recheck the original geometry and heading at the same thresholds.
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        if int(gray.max())-int(gray.min())<8:
            return found
        for mode in ('exposure','local_contrast'):
            prepared=(cv2.normalize(gray,None,0,255,cv2.NORM_MINMAX) if mode=='exposure'
                else cv2.createCLAHE(clipLimit=3,tileGridSize=(8,8)).apply(gray))
            candidate=super().locate(cv2.cvtColor(prepared,cv2.COLOR_GRAY2BGR))
            if candidate['location']:
                location,reason=self._validate(image,np.asarray(candidate['location']['homography']))
                if location:
                    candidate.update(location=location,status=reason)
                    candidate['metrics']['exposure_retry']=mode
                    found=candidate
                    break
        if not found['location']:self.previous_bounds=None
        found['elapsed_ms']=round((time.perf_counter()-started)*1000,3)
        return found

    def _validate(self,image,H):
        height,width=image.shape[:2];rh,rw=self.gray.shape
        quad=cv2.perspectiveTransform(corners([0,0,rw,rh]).reshape(-1,1,2),H).reshape(-1,2)
        if not np.isfinite(quad).all() or not cv2.isContourConvex(quad):return None,'invalid_panel_geometry'
        if cv2.contourArea(quad,oriented=True)<=0:return None,'flipped_panel'
        edge=np.linalg.norm(np.roll(quad,-1,axis=0)-quad,axis=1)
        if min(edge)<=0 or max(edge[0],edge[2])/min(edge[0],edge[2])>2 or max(edge[1],edge[3])/min(edge[1],edge[3])>2:
            return None,'extreme_perspective'
        rectified=cv2.warpPerspective(image,np.linalg.inv(H),(rw,rh))
        def match(box,threshold=.43):
            a,b,c,d=box
            reference=self.gray[b:d,a:c]
            patch=cv2.cvtColor(rectified[b:d,a:c],cv2.COLOR_BGR2GRAY)
            score=float(cv2.matchTemplate(patch,reference,cv2.TM_CCOEFF_NORMED)[0,0])
            return math.isfinite(score) and score>=threshold,score
        valid,score=match(self.anchor)
        if not valid:return None,'pressure_page_unverified'
        def project(source):
            boxes={};quads={}
            for wheel,box in source.items():
                points=cv2.perspectiveTransform(corners(box).reshape(-1,1,2),H).reshape(-1,2)
                if (not np.isfinite(points).all() or np.any(points<2) or
                        np.any(points[:,0]>width-3) or np.any(points[:,1]>height-3)):continue
                if min(np.linalg.norm(points[3]-points[0]),np.linalg.norm(points[2]-points[1]))<12:continue
                # Source regions already include margins. Expanding each one
                # independently crosses adjacent pressure/temperature rows.
                boxes[wheel]=[math.floor(points[:,0].min()),math.floor(points[:,1].min()),
                              math.ceil(points[:,0].max()),math.ceil(points[:,1].max())]
                quads[wheel]=points.tolist()
            return boxes,quads
        boxes,quadrilaterals=project(self.boxes)
        temperatures,_=project(self.temperature_boxes)
        if self.temperature_anchor and not match(self.temperature_anchor,.70)[0]:temperatures={}
        if not boxes and not temperatures:return None,'pressure_cut_off'
        return dict(boxes=boxes,temperature_boxes=temperatures,temperature_kind=self.spec['temperature_kind'],
            quadrilaterals=quadrilaterals,panel=quad.tolist(),homography=H.tolist(),heading_score=score,
            ocr_size=[rw,rh],ocr_homography=np.linalg.inv(H).tolist(),
            ocr_boxes={w:self.boxes[w] for w in boxes},
            ocr_temperature_boxes={w:self.temperature_boxes[w] for w in temperatures},
            missing_wheels=[w for w in self.boxes if w not in boxes],car_path=self.car_path), 'located'


class MultiCarDashLocator:
    """Caches features, not pixels. Live collection always sets the SDK car first."""
    def __init__(self,car_path=None):
        self.car_path=canonical_car(car_path)
        self.cache={}

    def for_car(self,car_path):
        car=canonical_car(car_path)
        if car is None:raise ValueError('지원하지 않는 차량입니다.')
        if car not in self.cache:
            self.cache[car]=BmwDashLocator() if car=='bmwm4gt3' else VehicleDashLocator(car)
        return self.cache[car]

    @property
    def reference(self):return self.for_car(self.car_path or 'bmwm4gt3').reference

    @property
    def previous_bounds(self):return None

    @previous_bounds.setter
    def previous_bounds(self,value):
        for locator in self.cache.values():locator.previous_bounds=value

    def seed_profile(self,profile):
        car=canonical_car(profile.get('car_path'))
        if car:self.for_car(car).seed_profile(profile)

    def set_car(self,car_path):self.car_path=canonical_car(car_path)

    def locate(self,image):
        started=time.perf_counter()
        cars=[self.car_path] if self.car_path else list(VEHICLES)
        best=None
        for car in cars:
            found=self.for_car(car).locate(image)
            found['car_path']=car
            if found['location']:
                found['location']['car_path']=car
                found['elapsed_ms']=round((time.perf_counter()-started)*1000,3)
                return found
            best=found
        best['elapsed_ms']=round((time.perf_counter()-started)*1000,3)
        return best


def ocr_frame(image,location):
    """Read upright dashboard regions; screen-space bounds remain for visibility.

    Adjacent tilted quadrilaterals have overlapping bounding rectangles. Warping
    the verified panel removes that overlap and avoids reading a neighbour row.
    Only regions whose full quadrilateral is visible are included.
    """
    # Ferrari's small blue LCD glyphs lose strokes when a perspective warp is
    # followed by another OCR resize (5 can become 6). Its separate cells have
    # enough spacing to read the original, fully visible bounding rectangles.
    if location.get('car_path')=='ferrari296gt3' or 'ocr_homography' not in location:
        return image,location['boxes'],location.get('temperature_boxes',{})
    H=np.asarray(location['homography'])
    scale=math.sqrt(abs(np.linalg.det(H[:2,:2])))
    # Keep native pixel density; magnifying a small dash before OCR needlessly
    # interpolates the same glyph twice and can erase its decimal point.
    size=np.maximum(1,np.round(np.asarray(location['ocr_size'])*scale).astype(int))
    rectified=cv2.warpPerspective(image,np.diag([scale,scale,1])@np.asarray(location['ocr_homography']),tuple(size))
    scaled=lambda boxes:{w:[round(v*scale) for v in b] for w,b in boxes.items()}
    return rectified,scaled(location['ocr_boxes']),scaled(location['ocr_temperature_boxes'])
