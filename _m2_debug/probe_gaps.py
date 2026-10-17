# -*- coding: utf-8 -*-
"""定位 1131/1238 法二B 上模的局部离缝：列出距离 > 0.5mm 的采样点并聚类。"""
import os
import sys
import json

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
TOL = 0.5
N = 8


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
                    p = pr.Value()
                    out.append((gp_Pnt(p.X(), p.Y(), p.Z()), pr.Normal().Z()))
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
    cls = classify_product_surfaces(product)
    bad = []
    ntot = nok = 0
    for idx, f in cls['upper_faces']:
        for p, nz in pts_of(f):
            d = dist(punch, p)
            if d is None:
                continue
            ntot += 1
            if d <= TOL:
                nok += 1
            else:
                bad.append((round(d, 3), idx, round(p.X(), 1), round(p.Y(), 1),
                            round(p.Z(), 1), round(nz, 2)))
    print(f"  采样点 {ntot}，贴合(≤{TOL}mm) {nok} ({100 * nok / max(ntot,1):.1f}%)，"
          f"离缝 {len(bad)}")
    bad.sort(reverse=True)
    print("  离缝最严重 20 点 (距离, 面, x, y, z, 面法向nz):")
    for b in bad[:20]:
        print(f"    距={b[0]:>8.3f}  面#{b[1]:>3}  ({b[2]:>8.1f},{b[3]:>8.1f},{b[4]:>8.1f})  nz={b[5]:+.2f}")
