# -*- coding: utf-8 -*-
"""验证离缝点是不是"真表面点"：面参数域采样可能落在面的内孔里（不是产品表面）。

判据：点在产品上表面，则其正下方 0.5mm 处应落在产品实体内部。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import cadquery as cq
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepLProp import BRepLProp_SLProps
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.gp import gp_Pnt

from mold_fix import MoldFixer
from product_surface_classifier import classify_product_surfaces

CASES = [
    ('YA-1131', os.path.join(ROOT, 'YA-1131-505_MIRROR_xOy.stp'),
     os.path.join(ROOT, '_m2_debug', 'out_utf8', 'YA-1131', 'punch.stp')),
    ('YA-1238', os.path.join(ROOT, 'YA-1238-01_MIRROR_xOy.stp'),
     os.path.join(ROOT, '_m2_debug', 'out_utf8', 'YA-1238', 'punch.stp')),
]
N = 8
TOL = 0.5


def load(path):
    r = cq.importers.importStep(path)
    vals = r.vals() if hasattr(r, 'vals') else [r]
    sh = [v.wrapped if hasattr(v, 'wrapped') else v for v in vals]
    return MoldFixer._make_compound(sh) if len(sh) > 1 else sh[0]


def pts_of(face, n=N):
    out = []
    try:
        a = BRepAdaptor_Surface(TopoDS.Face_s(face))
        u1, u2 = a.FirstUParameter(), a.LastUParameter()
        v1, v2 = a.FirstVParameter(), a.LastVParameter()
        INF = 1e8
        if abs(u1) > INF or abs(u2) > INF: u1, u2 = -1.0, 1.0
        if abs(v1) > INF or abs(v2) > INF: v1, v2 = -1.0, 1.0
        if abs(u2 - u1) < 1e-12: u2 = u1 + 1.0
        if abs(v2 - v1) < 1e-12: v2 = v1 + 1.0
        for i in range(n):
            for j in range(n):
                pr = BRepLProp_SLProps(a, u1 + (u2 - u1) * (i + .5) / n,
                                       v1 + (v2 - v1) * (j + .5) / n, 1, 1e-7)
                if pr.IsNormalDefined():
                    out.append(pr.Value())
    except Exception:
        pass
    return out


def dist(shape, p):
    try:
        d = BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(p).Vertex(), shape)
        d.Perform()
        return d.Value() if d.IsDone() else None
    except Exception:
        return None


for name, pp, up in CASES:
    print(f"\n{'=' * 74}\n{name}")
    product, punch = load(pp), load(up)
    cls = MoldFixer._make_classifiers(product)
    c = classify_product_surfaces(product)
    tot = ok = real_gap = artifact = 0
    worst = []
    for idx, f in c['upper_faces']:
        for p in pts_of(f):
            d = dist(punch, p)
            if d is None:
                continue
            tot += 1
            inside_below = (MoldFixer._classifier_inside(
                cls, gp_Pnt(p.X(), p.Y(), p.Z() - 0.5)) or
                MoldFixer._classifier_inside(cls, gp_Pnt(p.X(), p.Y(), p.Z() - 1.5)))
            if d <= TOL:
                ok += 1
            elif inside_below:
                real_gap += 1
                worst.append((round(d, 3), idx, round(p.X(), 1), round(p.Y(), 1), round(p.Z(), 1)))
            else:
                artifact += 1
    print(f"  采样 {tot}  贴合(≤{TOL}) {ok} ({100*ok/max(tot,1):.1f}%)  "
          f"真实离缝 {real_gap} ({100*real_gap/max(tot,1):.1f}%)  "
          f"假点(面内孔/非表面) {artifact}")
    worst.sort(reverse=True)
    print("  真实离缝最严重 10 点:")
    for w in worst[:10]:
        print(f"    距={w[0]:>8.3f} 面#{w[1]:>3} ({w[2]:>8.1f},{w[3]:>9.1f},{w[4]:>9.1f})")
