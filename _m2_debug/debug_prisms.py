# -*- coding: utf-8 -*-
"""调试：新拉伸法的棱柱为什么不连通。"""
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
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse, BRepAlgoAPI_Cut

from mold_fix import MoldFixer
from product_surface_classifier import classify_product_surfaces
from mold_method2_generator import _fuse, _cut, _bbox

P_PATH = os.path.join(ROOT, 'web_box_generator', 'uploads',
                      '543e0fdcfa2e_2ROM31-6061 ---(1).stp')


def n_of(s, k):
    n, e = 0, TopExp_Explorer(s, k)
    while e.More():
        n += 1
        e.Next()
    return n


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
print(f"面总数={len(faces)}  分类数={len(diag)}")
from collections import Counter
print("分类:", Counter(d['cls'] for d in diag))

prisms, skip = [], 0
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
            skip += 1
            continue
    fb = MoldFixer._bbox_tuple(f)
    h = (P - fb[5]) + 2.0
    if h <= 0.5:
        continue
    pr = BRepPrimAPI_MakePrism(f, gp_Vec(0, 0, h)).Shape()
    if MoldFixer._solid_volume(pr) <= 1e-6:
        continue
    prisms.append((i, kind, MoldFixer._bbox_tuple(pr), pr))
print(f"棱柱数={len(prisms)}  跳过竖直面={skip}")
for i, k, b, pr in prisms:
    print(f"  #{i:>3} {k:<6} bbox x[{b[0]:8.2f},{b[3]:8.2f}] "
          f"y[{b[1]:7.2f},{b[4]:7.2f}] z[{b[2]:8.2f},{b[5]:8.2f}] "
          f"vol={MoldFixer._solid_volume(pr):>12.0f}")

body = prisms[0][3]
for i, k, b, pr in prisms[1:]:
    ok, m = _fuse(body, pr, "f", fuzz=1e-3)
    if ok and m is not None:
        body = m
print(f"\n逐个 Fuse 后: solids={n_of(body, TopAbs_SOLID)} "
      f"vol={MoldFixer._solid_volume(body):.0f}")
ok, cut = _cut(body, product, "cut", fuzz=1e-4)
if ok and cut is not None:
    body2 = cut
    print(f"Cut(产品) 后: solids={n_of(body2, TopAbs_SOLID)} "
          f"vol={MoldFixer._solid_volume(body2):.0f} bbox={_bbox(body2)}")
