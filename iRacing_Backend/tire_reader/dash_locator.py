"""BMW dashboard geometry from image features, independent of screen coordinates."""
from __future__ import annotations

import math
from pathlib import Path
import time

import cv2
import numpy as np

from .pressure_ocr import WHEELS, load_image, validate_profile

ROOT = Path(__file__).resolve().parent
REFERENCE_CROP = (590, 124, 1224, 505)
# These are coordinates within the vehicle reference image, never monitor coordinates.
REFERENCE_BOXES = dict(FL=[605,171,684,210], FR=[698,171,780,210],
                       RL=[605,233,684,273], RR=[698,233,780,273])


def corners(box):
    x0,y0,x1,y1 = box
    return np.float32([[x0,y0], [x1,y0], [x1,y1], [x0,y1]])


class BmwDashLocator:
    def __init__(self, reference_path=None):
        image, self.reference_sha256 = load_image(reference_path or ROOT/'samples/gt3_dash/bmw_m4_p7.png')
        x0,y0,x1,y1 = REFERENCE_CROP
        self.reference = image[y0:y1,x0:x1]
        self.gray = cv2.cvtColor(self.reference, cv2.COLOR_BGR2GRAY)
        self.sift = cv2.SIFT_create(nfeatures=2500)
        self.keypoints, self.descriptors = self.sift.detectAndCompute(self.gray,None)
        self.boxes = {w:[a-x0,b-y0,c-x0,d-y0] for w,(a,b,c,d) in REFERENCE_BOXES.items()}
        self.previous_bounds = None

    def seed_profile(self, profile):
        """Search a previous location first, while revalidating every new pixel."""
        validate_profile(profile)
        width, height = profile['image_size']
        points = (profile.get('locator') or {}).get('location') or {}
        panel = np.asarray(points.get('panel', []), dtype=float)
        if panel.shape != (4,2) or not np.isfinite(panel).all():
            boxes = profile['boxes'].values()
            panel = np.asarray([[min(b[0] for b in boxes), min(b[1] for b in boxes)],
                [max(b[2] for b in boxes), max(b[3] for b in boxes)]], dtype=float)
        margin = max(60, int((panel[:,0].max()-panel[:,0].min())*.25))
        bounds = (max(0,int(panel[:,0].min())-margin), max(0,int(panel[:,1].min())-margin),
            min(width,int(panel[:,0].max())+margin), min(height,int(panel[:,1].max())+margin))
        self.previous_bounds = bounds if bounds[2]-bounds[0]>=64 and bounds[3]-bounds[1]>=64 else None

    def _refine_candidate(self, image, H, metrics):
        """A rejected hypothesis may guide a new search, never supply a reading."""
        rh,rw = self.gray.shape
        quad = cv2.perspectiveTransform(corners([0,0,rw,rh]).reshape(-1,1,2),H).reshape(-1,2)
        if (not np.isfinite(quad).all() or not cv2.isContourConvex(quad)
                or cv2.contourArea(quad,oriented=True)<=0):
            return None, 'weak_geometry', metrics
        height,width = image.shape[:2]
        a,b = np.maximum(0,np.floor(quad.min(axis=0)).astype(int))
        c,d = np.minimum([width,height],np.ceil(quad.max(axis=0)).astype(int))
        if c-a<64 or d-b<64 or (a==b==0 and c==width and d==height):
            return None, 'weak_geometry', metrics
        # Focus the feature budget on the current candidate instead of the
        # road, wheel and hands. Recompute all matches at native resolution;
        # the original inlier, coverage and anchor gates still apply.
        refined,reason,local = self._find(image[b:d,a:c],downscale=False)
        if refined is None:
            # Small LCD labels lose different strokes at different scales.
            # A half-size official reference supplies fresh descriptors while
            # keeping source coordinates and every acceptance gate unchanged.
            refined,reason,local = self._find(image[b:d,a:c],downscale=False,half_reference=True)
        if refined is None:
            return None, reason, local
        local.update(refined_search=True,initial_inliers=metrics['inliers'],
                     initial_inlier_ratio=metrics['inlier_ratio'])
        return np.array([[1,0,a],[0,1,b],[0,0,1]],dtype=float) @ refined, 'located', local

    def _find(self, image, *, downscale=True, refine=False, half_reference=False):
        metrics = {}
        reference_keypoints,reference_descriptors = self.keypoints,self.descriptors
        if half_reference:
            if not hasattr(self,'_half_features'):
                half = cv2.resize(self.gray,None,fx=.5,fy=.5,interpolation=cv2.INTER_AREA)
                keys,descriptors = self.sift.detectAndCompute(half,None)
                for key in keys:
                    key.pt = (key.pt[0]*2,key.pt[1]*2)
                self._half_features = keys,descriptors
            reference_keypoints,reference_descriptors = self._half_features
            metrics['half_reference'] = True
        ratio = min(1.0, 1600/max(image.shape[:2])) if downscale else 1.0
        search = cv2.resize(image,None,fx=ratio,fy=ratio,interpolation=cv2.INTER_AREA) if ratio<1 else image
        keypoints, descriptors = self.sift.detectAndCompute(cv2.cvtColor(search,cv2.COLOR_BGR2GRAY),None)
        metrics['scene_keypoints'] = len(keypoints)
        if descriptors is None or reference_descriptors is None or len(keypoints)<16:
            return None, 'no_features', metrics
        pairs = cv2.BFMatcher().knnMatch(reference_descriptors,descriptors,k=2)
        good = [a for pair in pairs if len(pair)==2 for a,b in [pair] if a.distance<0.72*b.distance]
        # One destination feature must not support several reference features.
        unique = {}
        for match in sorted(good,key=lambda m:m.distance):
            unique.setdefault(match.trainIdx,match)
        good = list(unique.values())
        metrics['matches'] = len(good)
        if len(good)<16:
            return None, 'too_few_matches', metrics
        source = np.float32([reference_keypoints[m.queryIdx].pt for m in good]).reshape(-1,1,2)
        target = np.float32([keypoints[m.trainIdx].pt for m in good]).reshape(-1,1,2)
        H, mask = cv2.findHomography(source,target,cv2.RANSAC,3.0)
        if H is None or mask is None or not np.isfinite(H).all():
            return None, 'no_geometry', metrics
        accepted = mask.ravel().astype(bool)
        src = source[accepted].reshape(-1,2)
        inliers = len(src)
        metrics.update(inliers=inliers,inlier_ratio=inliers/len(good))
        if inliers<16 or inliers/len(good)<0.30:
            if refine:
                return self._refine_candidate(image,np.diag([1/ratio,1/ratio,1.0]) @ H,metrics)
            return None, 'weak_geometry', metrics
        coverage = cv2.contourArea(cv2.convexHull(src))/(self.gray.shape[0]*self.gray.shape[1])
        if hasattr(self,'anchor'):
            a,b,c,d=self.anchor
            margin=max(c-a,d-b)*2
            near=sum(a-margin<x<c+margin and b-margin<y<d+margin for x,y in src)
        else:
            near = sum(x<205 and y<175 for x,y in src)
        metrics.update(reference_coverage=coverage,pressure_anchor_inliers=int(near))
        if coverage<0.08 or near<4:
            return None, 'pressure_anchor_missing', metrics
        error = np.linalg.norm(cv2.perspectiveTransform(source[accepted],H)-target[accepted],axis=2)
        metrics['median_reprojection_px'] = float(np.median(error))
        if metrics['median_reprojection_px']>2.5:
            return None, 'geometry_error', metrics
        H = np.diag([1/ratio,1/ratio,1.0]) @ H
        return H, 'located', metrics

    def _validate(self, image, H):
        height,width = image.shape[:2]
        rh,rw = self.gray.shape
        quad = cv2.perspectiveTransform(corners([0,0,rw,rh]).reshape(-1,1,2),H).reshape(-1,2)
        if not np.isfinite(quad).all() or not cv2.isContourConvex(quad):
            return None, 'invalid_panel_geometry'
        if cv2.contourArea(quad,oriented=True)<=0:
            return None, 'flipped_panel'
        edge = np.linalg.norm(np.roll(quad,-1,axis=0)-quad,axis=1)
        if min(edge)<=0 or max(edge[0],edge[2])/min(edge[0],edge[2])>2.0 or max(edge[1],edge[3])/min(edge[1],edge[3])>2.0:
            return None, 'extreme_perspective'
        projected = {}
        boxes = {}
        for wheel in WHEELS:
            points = cv2.perspectiveTransform(corners(self.boxes[wheel]).reshape(-1,1,2),H).reshape(-1,2)
            if (not np.isfinite(points).all() or np.any(points<2)
                    or np.any(points[:,0]>width-3) or np.any(points[:,1]>height-3)):
                return None, 'pressure_cut_off'
            digit_height = min(np.linalg.norm(points[3]-points[0]),np.linalg.norm(points[2]-points[1]))
            if digit_height<16:
                return None, 'pressure_too_small'
            projected[wheel] = points.tolist()
            boxes[wheel] = [int(math.floor(points[:,0].min()))-2,
                            int(math.floor(points[:,1].min()))-2,
                            int(math.ceil(points[:,0].max()))+2,
                            int(math.ceil(points[:,1].max()))+2]
        # Validate the static pressure heading, separate from changing digits.
        rectified = cv2.warpPerspective(image,np.linalg.inv(H),(rw,rh))
        heading = cv2.cvtColor(rectified[4:32,8:198],cv2.COLOR_BGR2GRAY)
        reference_heading = self.gray[4:32,8:198]
        score = float(cv2.matchTemplate(heading,reference_heading,cv2.TM_CCOEFF_NORMED)[0,0])
        if not math.isfinite(score) or score<0.45:
            return None, 'pressure_page_unverified'
        return dict(boxes=boxes,quadrilaterals=projected,panel=quad.tolist(),
                    homography=H.tolist(),heading_score=score), 'located'

    def locate(self, image):
        started = time.perf_counter()
        result = None
        reason = 'not_found'
        metrics = {}
        # Recheck geometry on every image. A failed frame never returns old boxes.
        searches = []
        if self.previous_bounds:
            x0,y0,x1,y1 = self.previous_bounds
            crop=image[y0:y1,x0:x1]
            if crop.shape[0]>=64 and crop.shape[1]>=64:
                searches.append((crop,x0,y0,'local',True))
        searches.append((image,0,0,'full',True))
        # Downsampling can remove the small heading's feature points. Retry the
        # original pixels before declaring the dashboard absent.
        if max(image.shape[:2])>1600:
            searches.append((image,0,0,'full_resolution',False))
        for search,x0,y0,mode,downscale in searches:
            H,reason,metrics = self._find(search,downscale=downscale,refine=True)
            if H is None:
                continue
            H = np.array([[1,0,x0],[0,1,y0],[0,0,1]],dtype=float) @ H
            result,reason = self._validate(image,H)
            if result:
                metrics['search'] = mode
                break
        if result:
            points = np.asarray(result['panel'])
            height,width = image.shape[:2]
            margin = max(60, int((points[:,0].max()-points[:,0].min())*0.25))
            self.previous_bounds = (max(0,int(points[:,0].min())-margin),
                max(0,int(points[:,1].min())-margin), min(width,int(points[:,0].max())+margin),
                min(height,int(points[:,1].max())+margin))
        else:
            self.previous_bounds = None
        return dict(status=reason, location=result, metrics=metrics,
                    elapsed_ms=round((time.perf_counter()-started)*1000,3),
                    reference_sha256=self.reference_sha256)
