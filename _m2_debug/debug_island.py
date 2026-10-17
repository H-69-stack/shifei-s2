# -*- coding: utf-8 -*-
"""定位 1131 上模残留孤岛：区域内每个面的分类与拉伸决策。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE
from OCP.gp import gp_Pnt
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import (GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone,
                         GeomAbs_Sphere, GeomAbs_Torus, GeomAbs_BSplineSurface,
                         GeomAbs_BezierSurface)
from OCP.TopoDS import TopoDS

from mold_fix import MoldFixer
from product_surface_classifier import classify_product_surfaces
from mold_method2_generator import _face_mid_point, _bbox

ISL = (115.45, -1095.48, 177.45, -1033.02)

solids = MoldFixer._extract_solids(os.path.join(ROOT, 'YA-1131-505_MIRROR_xOy.stp'))
product = MoldFixer._make_compound([s for _, s in solids])
pb = MoldFixer._bbox_tuple(product)
Z_A, Z_B = pb[5], pb[2]

cls = classify_product_surfaces(product)
diag = cls['faces']
faces = []
e = TopExp_Explorer(product, TopAbs_FACE)
while e.More():
    faces.append(e.Current())
    e.Next()

TYPES = {GeomAbs_Plane: 'Plane', GeomAbs_Cylinder: 'Cyl', GeomAbs_Cone: 'Cone',
         GeomAbs_Sphere: 'Sph', GeomAbs_Torus: 'Torus',
         GeomAbs_BSplineSurface: 'BSpline', GeomAbs_BezierSurface: 'Bezier'}


def overlap(a, b):
    return (a[0] < ISL[2] and ISL[0] < a[3] and a[1] < ISL[3] and ISL[1] < a[4])


print(f"孤岛 XY 区域 x[{ISL[0]},{ISL[2]}] y[{ISL[1]},{ISL[3]}]")
for i, f in enumerate(faces):
    fb = MoldFixer._bbox_tuple(f)
    if not overlap(fb, ISL):
        continue
    d = diag[i]
    try:
        st = TYPES.get(BRepAdaptor_Surface(TopoDS.Face_s(f)).GetType(), '?')
    except Exception:
        st = '?'
    mp = _face_mid_point(f)
    mps = f"({mp.X():.1f},{mp.Y():.1f},{mp.Z():.1f})" if mp else "None"
    # 决策复算
    if d['cls'] == 'lower':
        dec = 'skip(lower)'
    elif d['cls'] == 'side':
        hb = False
        for zz in (fb[2] - 0.5, fb[2] - 3.0, pb[2] + 1.0):
            if zz <= pb[2] - 0.5:
                continue
            if mp and MoldFixer._point_inside(product, gp_Pnt(mp.X(), mp.Y(), zz)):
                hb = True
                break
        dec = 'PRISM' if hb else 'skip(no material below)'
    else:
        dec = 'PRISM'
    print(f"  #{i:>3} {d['cls']:<6} {st:<8} area={d['area']:>9.1f} nz={d['nz']:+.3f} "
          f"z[{fb[2]:9.2f},{fb[5]:9.2f}] mid={mps:<24} → {dec}")
