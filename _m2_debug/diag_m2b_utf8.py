# -*- coding: utf-8 -*-
"""法二B 上模质量诊断：UTF-8 修复前后对比 + 上模是否贴合产品上表面。

用法：
  python _m2_debug/diag_m2b_utf8.py [--no-utf8]
"""
import os
import sys
import json
import time
import argparse
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

if '--no-utf8' not in sys.argv and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepLProp import BRepLProp_SLProps
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.gp import gp_Pnt

from mold_fix import MoldFixer
from mold_method2_generator import MoldMethod2Generator
from product_surface_classifier import classify_product_surfaces

PRODUCTS = [
    ('2ROM31-6061', os.path.join(ROOT, 'web_box_generator', 'uploads',
                                 '543e0fdcfa2e_2ROM31-6061 ---(1).stp')),
    ('YA-1131', os.path.join(ROOT, 'YA-1131-505_MIRROR_xOy.stp')),
    ('YA-1238', os.path.join(ROOT, 'YA-1238-01_MIRROR_xOy.stp')),
]


def n_of(shape, kind):
    n = 0
    e = TopExp_Explorer(shape, kind)
    while e.More():
        n += 1
        e.Next()
    return n


def sample_face_pts(face, shape, n=3, eps=0.1):
    """面上 n×n 采样点（仅取朝上面）。"""
    pts = []
    try:
        adapt = BRepAdaptor_Surface(TopoDS.Face_s(face))
        u1, u2 = adapt.FirstUParameter(), adapt.LastUParameter()
        v1, v2 = adapt.FirstVParameter(), adapt.LastVParameter()
        INF = 1e8
        if abs(u1) > INF or abs(u2) > INF:
            u1, u2 = -1.0, 1.0
        if abs(v1) > INF or abs(v2) > INF:
            v1, v2 = -1.0, 1.0
        if abs(u2 - u1) < 1e-12: u2 = u1 + 1.0
        if abs(v2 - v1) < 1e-12: v2 = v1 + 1.0
        for i in range(n):
            for j in range(n):
                uu = u1 + (u2 - u1) * (i + 0.5) / n
                vv = v1 + (v2 - v1) * (j + 0.5) / n
                pr = BRepLProp_SLProps(adapt, uu, vv, 1, 1e-7)
                if pr.IsNormalDefined():
                    p = pr.Value()
                    pts.append(gp_Pnt(p.X(), p.Y(), p.Z()))
    except Exception:
        pass
    return pts


def dist_to(shape, pnt):
    try:
        d = BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(pnt).Vertex(), shape)
        d.Perform()
        if d.IsDone():
            return d.Value()
    except Exception:
        pass
    return None


def punch_fit_check(product, punch, max_faces=60):
    """上模下表面是否贴合产品上表面：产品上表面采样点到上模最近距离。"""
    cls = classify_product_surfaces(product)
    uppers = cls['upper_faces'][:max_faces]
    dists = []
    for idx, f in uppers:
        for p in sample_face_pts(f, product, 3):
            d = dist_to(punch, p)
            if d is not None:
                dists.append(d)
    if not dists:
        return {'n': 0}
    dists.sort()
    return {
        'n': len(dists),
        'max': round(dists[-1], 4),
        'p95': round(dists[int(0.95 * (len(dists) - 1))], 4),
        'mean': round(sum(dists) / len(dists), 4),
        'upper_faces_total': len(cls['upper_faces']),
        'lower_faces_total': len(cls['lower_faces']),
    }


def run(name, path):
    print(f"\n{'=' * 78}\n[法二B·UTF8={UTF8}] {name}")
    t0 = time.time()
    solids = MoldFixer._extract_solids(path)
    product = MoldFixer._make_compound([s for _, s in solids])
    pb = MoldFixer._bbox_tuple(product)
    Z_A, Z_B = pb[5], pb[2]

    g = MoldMethod2Generator()
    try:
        r = g.generate_from_product(path)
    except Exception:
        traceback.print_exc()
        return {'name': name, 'error': 'exception'}
    if not r.get('success'):
        print(f"  失败: {r.get('error')}")
        return {'name': name, 'success': False, 'error': r.get('error')}

    punch = r['punch']['_shape']
    cavity = r['cavity']['_shape']
    src = r['punch']['details'].get('src')
    pbb = MoldFixer._bbox_tuple(punch)
    print(f"  产品: Z_A={Z_A:.3f} Z_B={Z_B:.3f} 高={Z_A - Z_B:.3f}")
    print(f"  上模: src={src} solids={n_of(punch, TopAbs_SOLID)} "
          f"shells={n_of(punch, TopAbs_SHELL)} vol={MoldFixer._solid_volume(punch):.1f}")
    print(f"  上模 bbox=[{pbb[0]:.2f},{pbb[1]:.2f},{pbb[2]:.3f} .. "
          f"{pbb[3]:.2f},{pbb[4]:.2f},{pbb[5]:.3f}]")
    fit = punch_fit_check(product, punch)
    print(f"  上模←产品上表面距离: {fit}")
    ic = None
    try:
        c = BRepAlgoAPI_Common(punch, product)
        c.Build()
        ic = MoldFixer._solid_volume(c.Shape()) if c.IsDone() else None
    except Exception:
        pass
    print(f"  Common(上模,产品)={ic}")
    secs = round(time.time() - t0, 1)
    print(f"  用时 {secs}s")

    out = os.path.join(ROOT, '_m2_debug', 'out_utf8' if UTF8 else 'out_gbk', name)
    os.makedirs(out, exist_ok=True)
    g.export_step(r['punch'], os.path.join(out, 'punch.stp'))
    g.export_step(r['cavity'], os.path.join(out, 'cavity.stp'))
    return {'name': name, 'success': True, 'utf8': UTF8, 'src': src,
            'punch_shells': n_of(punch, TopAbs_SHELL),
            'punch_bbox': pbb, 'fit': fit, 'interfere': ic, 'secs': secs}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-utf8', action='store_true')
    a = ap.parse_args()
    UTF8 = not a.no_utf8
    recs = []
    for nm, p in PRODUCTS:
        if os.path.exists(p):
            recs.append(run(nm, p))
    tag = 'utf8' if UTF8 else 'gbk'
    with open(os.path.join(ROOT, '_m2_debug', f'diag_b_{tag}.json'), 'w',
              encoding='utf-8') as f:
        json.dump(recs, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n写入 _m2_debug/diag_b_{tag}.json")
