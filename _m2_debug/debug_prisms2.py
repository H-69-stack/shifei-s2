# -*- coding: utf-8 -*-
"""调试：新拉伸法在 1131/1238 上残留的独立实体是什么（体积/包围盒）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.gp import gp_Vec, gp_Pnt
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from mold_fix import MoldFixer
from product_surface_classifier import classify_product_surfaces
from mold_method2_generator import _fuse, _cut, _bbox

WHICH = sys.argv[1] if len(sys.argv) > 1 else '1131'
if WHICH == '1131':
    P_PATH = os.path.join(ROOT, 'YA-1131-505_MIRROR_xOy.stp')
else:
    P_PATH = os.path.join(ROOT, 'YA-1238-01_MIRROR_xOy.stp')


def solids_of(s):
    out, e = [], TopExp_Explorer(s, TopAbs_SOLID)
    while e.More():
        out.append(e.Current())
        e.Next()
    return out


solids = MoldFixer._extract_solids(P_PATH)
product = MoldFixer._make_compound([s for _, s in solids])
pb = MoldFixer._bbox_tuple(product)
Z_A, Z_B = pb[5], pb[2]
P = Z_A + (Z_A - Z_B)

cls = classify_product_surfaces(product)
diag = cls['faces']
faces = []
e = TopExp_Explorer(product, TopAbs_FACE)
while e.More():
    faces.append(e.Current())
    e.Next()
prisms, skipped = [], []
for i, f in enumerate(faces):
    kind = diag[i]['cls']
    if kind == 'lower':
        continue
    if kind == 'side':
        fb = MoldFixer._bbox_tuple(f)
        cx, cy = (fb[0] + fb[3]) / 2.0, (fb[1] + fb[4]) / 2.0
        hb = False
        for zz in (fb[2] - 0.5, fb[2] - 3.0, pb[2] + 1.0):
            if zz <= pb[2] - 0.5:
                continue
            if MoldFixer._point_inside(product, gp_Pnt(cx, cy, zz)):
                hb = True
                break
        if not hb:
            skipped.append(i)
            continue
    fb = MoldFixer._bbox_tuple(f)
    h = (P - fb[5]) + 2.0
    if h <= 0.5:
        continue
    pr = BRepPrimAPI_MakePrism(f, gp_Vec(0, 0, h)).Shape()
    if MoldFixer._solid_volume(pr) <= 1e-6:
        continue
    prisms.append((i, kind, pr))
print(f"面={len(faces)}  upper={sum(1 for d in diag if d['cls']=='upper')} "
      f"side={sum(1 for d in diag if d['cls']=='side')} 棱柱={len(prisms)} "
      f"跳过竖直面={len(skipped)}")

body = prisms[0][2]
for i, k, pr in prisms[1:]:
    ok, m = _fuse(body, pr, "f", fuzz=1e-3)
    if ok and m is not None:
        body = m
ss = solids_of(body)
print(f"Fuse 后 {len(ss)} 实体:")
for s in sorted(ss, key=lambda x: -MoldFixer._solid_volume(x)):
    b = MoldFixer._bbox_tuple(s)
    print(f"   vol={MoldFixer._solid_volume(s):>12.0f}  "
          f"x[{b[0]:8.2f},{b[3]:8.2f}] y[{b[1]:9.2f},{b[4]:9.2f}] z[{b[2]:9.2f},{b[5]:9.2f}]")
