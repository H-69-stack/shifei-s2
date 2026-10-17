# -*- coding: utf-8 -*-
"""定位法二B 上模与产品上表面的偏差区域（哪几片面上有间隙、面积多大）。"""
import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

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
    ('2ROM31-6061',
     os.path.join(ROOT, 'web_box_generator', 'uploads',
                  '543e0fdcfa2e_2ROM31-6061 ---(1).stp'),
     os.path.join(ROOT, '_m2_debug', 'out_utf8', '2ROM31-6061', 'punch.stp')),
    ('YA-1131',
     os.path.join(ROOT, 'YA-1131-505_MIRROR_xOy.stp'),
     os.path.join(ROOT, '_m2_debug', 'out_utf8', 'YA-1131', 'punch.stp')),
    ('YA-1238',
     os.path.join(ROOT, 'YA-1238-01_MIRROR_xOy.stp'),
     os.path.join(ROOT, '_m2_debug', 'out_utf8', 'YA-1238', 'punch.stp')),
]
TOL = 0.2


def load_shape(path):
    r = cq.importers.importStep(path)
    vals = r.vals() if hasattr(r, 'vals') else [r]
    shapes = [v.wrapped if hasattr(v, 'wrapped') else v for v in vals]
    return MoldFixer._make_compound(shapes) if len(shapes) > 1 else shapes[0]


def pts_of(face, n=3):
    out = []
    try:
        a = BRepAdaptor_Surface(TopoDS.Face_s(face))
        u1, u2, v1, v2 = a.FirstUParameter(), a.LastUParameter(), a.FirstVParameter(), a.LastVParameter()
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
                    out.append(gp_Pnt(p.X(), p.Y(), p.Z()))
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


def main():
    report = []
    for name, ppath, punchpath in CASES:
        print(f"\n{'=' * 76}\n{name}")
        product = load_shape(ppath)
        punch = load_shape(punchpath)
        pb, ub = MoldFixer._bbox_tuple(product), MoldFixer._bbox_tuple(punch)
        print(f"  产品 bbox x[{pb[0]:.2f},{pb[3]:.2f}] y[{pb[1]:.2f},{pb[4]:.2f}] z[{pb[2]:.2f},{pb[5]:.2f}]")
        print(f"  上模 bbox x[{ub[0]:.2f},{ub[3]:.2f}] y[{ub[1]:.2f},{ub[4]:.2f}] z[{ub[2]:.2f},{ub[5]:.2f}]")
        cov = {'x': round((ub[3] - ub[0]) / max(pb[3] - pb[0], 1e-9), 3),
               'y': round((ub[4] - ub[1]) / max(pb[4] - pb[1], 1e-9), 3)}
        print(f"  上模对产品 XY 覆盖率: {cov}")

        cls = classify_product_surfaces(product)
        bad, good_area, bad_area, maxd = [], 0.0, 0.0, 0.0
        for idx, f in cls['upper_faces']:
            area = MoldFixer._face_area(f)
            ds = [d for d in (dist(punch, p) for p in pts_of(f)) if d is not None]
            if not ds:
                continue
            mx = max(ds)
            maxd = max(maxd, mx)
            if mx > TOL:
                fb = MoldFixer._bbox_tuple(f)
                bad.append({'face': idx, 'area': round(area, 1), 'max_dist': round(mx, 3),
                            'bbox': [round(v, 1) for v in fb]})
                bad_area += area
            else:
                good_area += area
        tot = good_area + bad_area
        print(f"  上表面总采样面面积={tot:.1f}  贴合={good_area:.1f} "
              f"({100 * good_area / max(tot, 1e-9):.1f}%)  离缝={bad_area:.1f} "
              f"({100 * bad_area / max(tot, 1e-9):.1f}%)  全局最大距离={maxd:.3f}mm")
        print(f"  离缝面 {len(bad)} 片（前 12）:")
        for b in sorted(bad, key=lambda x: -x['area'])[:12]:
            print(f"    #{b['face']:>3} 面积={b['area']:>9.1f} 最大距={b['max_dist']:>8.3f} "
                  f"bbox={b['bbox']}")
        report.append({'name': name, 'coverage': cov, 'good_area': round(good_area, 1),
                       'bad_area': round(bad_area, 1), 'max_dist': round(maxd, 3),
                       'bad_faces': bad})
    with open(os.path.join(ROOT, '_m2_debug', 'fit_analysis.json'), 'w',
              encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
