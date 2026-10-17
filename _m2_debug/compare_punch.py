# -*- coding: utf-8 -*-
"""决定性对比：旧凸起内核 vs 新拉伸法 —— 用"只数真表面点"的校验口径。

对每个产品：产品只导入一次，分别构造 旧(mold_fix 内核) / 新(拉伸法) 上模，
用同一套采样（面参数域 N×N，剔除落在面内孔里的假点）比较贴合率与最大离缝。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import cadquery as cq
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID, TopAbs_SHELL
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepLProp import BRepLProp_SLProps
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.gp import gp_Pnt

from mold_fix import MoldFixer
from product_surface_classifier import classify_product_surfaces
from mold_method2_generator import MoldMethod2Generator

CASES = [
    ('2ROM31-6061', os.path.join(ROOT, 'web_box_generator', 'uploads',
                                 '543e0fdcfa2e_2ROM31-6061 ---(1).stp')),
    ('YA-1131', os.path.join(ROOT, 'YA-1131-505_MIRROR_xOy.stp')),
    ('YA-1238', os.path.join(ROOT, 'YA-1238-01_MIRROR_xOy.stp')),
]
N = 6
TOL = 0.5


def n_of(s, k):
    n, e = 0, TopExp_Explorer(s, k)
    while e.More():
        n += 1
        e.Next()
    return n


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


def evaluate(punch, upper_faces, cls):
    tot = ok = real = art = 0
    maxd = 0.0
    for idx, f in upper_faces:
        for p in pts_of(f):
            d = dist(punch, p)
            if d is None:
                continue
            tot += 1
            below = (MoldFixer._classifier_inside(cls, gp_Pnt(p.X(), p.Y(), p.Z() - 0.5))
                     or MoldFixer._classifier_inside(cls, gp_Pnt(p.X(), p.Y(), p.Z() - 1.5)))
            if d <= TOL:
                ok += 1
            elif below:
                real += 1
                maxd = max(maxd, d)
            else:
                art += 1
    return {'tot': tot, 'ok': ok, 'real': real, 'art': art, 'max': round(maxd, 3)}


for name, path in CASES:
    print(f"\n{'=' * 78}\n{name}")
    solids = MoldFixer._extract_solids(path)
    product = MoldFixer._make_compound([s for _, s in solids])
    pb = MoldFixer._bbox_tuple(product)
    Z_A, Z_B = pb[5], pb[2]
    P = Z_A + (Z_A - Z_B)
    c = classify_product_surfaces(product)
    cls = MoldFixer._make_classifiers(product)

    g = MoldMethod2Generator()
    results = {}

    # 旧：mold_fix 凸起内核
    try:
        leg, _mz = MoldFixer()._build_full_protrusion(product, P)
        results['legacy'] = leg
    except Exception as e:
        print(f"  legacy 失败: {e}")
        results['legacy'] = None

    # 新：拉伸法
    try:
        new, n_used, n_skip = g.build_punch_by_extrusion(product, P)
        print(f"  新法: 棱柱={n_used} 跳过竖直面={n_skip}")
        results['extrusion'] = new
    except Exception as e:
        print(f"  extrusion 失败: {e}")
        results['extrusion'] = None

    for tag, shape in results.items():
        if shape is None:
            print(f"  [{tag}] None")
            continue
        bb = MoldFixer._bbox_tuple(shape)
        covx = (bb[3] - bb[0]) / max(pb[3] - pb[0], 1e-9)
        covy = (bb[4] - bb[1]) / max(pb[4] - pb[1], 1e-9)
        ev = evaluate(shape, c['upper_faces'], cls)
        print(f"  [{tag}] solids={n_of(shape, TopAbs_SOLID)} shells={n_of(shape, TopAbs_SHELL)} "
              f"valid={BRepCheck_Analyzer(shape).IsValid()} vol={MoldFixer._solid_volume(shape):.0f}")
        print(f"      XY覆盖 x={covx:.3f} y={covy:.3f} | 采样{ev['tot']} "
              f"贴合{ev['ok']}({100*ev['ok']/max(ev['tot'],1):.1f}%) "
              f"真实离缝{ev['real']}({100*ev['real']/max(ev['tot'],1):.1f}%) "
              f"假点{ev['art']} 最大真实离缝={ev['max']}mm")
