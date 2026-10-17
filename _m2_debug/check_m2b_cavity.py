# -*- coding: utf-8 -*-
"""法二B 质量核查：下模到底有没有挖出型腔？

判据：
  · 空盒体积 = base_lx × base_ly × base_hz
  · 下模实际体积应 ≈ 空盒体积 − 产品体积
  · Common(下模, 产品) 应为 0（无干涉）
  · 上模底面应贴合产品上表面
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID, TopAbs_SHELL
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common

from mold_fix import MoldFixer
from mold_method2_generator import MoldMethod2Generator, _bbox_accurate, _bbox

CASES = [
    ('2ROM31-6061', os.path.join(ROOT, 'web_box_generator', 'uploads',
                                 '543e0fdcfa2e_2ROM31-6061 ---(1).stp')),
    ('YA-1131', os.path.join(ROOT, 'web_box_generator', 'uploads',
                             'f4adfe3f6156_YA-1131-505_MIRROR_xOy.stp')),
]


def vol(s):
    p = GProp_GProps()
    BRepGProp.VolumeProperties_s(s, p)
    return p.Mass()


def n_of(s, k):
    n, e = 0, TopExp_Explorer(s, k)
    while e.More():
        n += 1
        e.Next()
    return n


g = MoldMethod2Generator()
for name, path in CASES:
    if not os.path.exists(path):
        print(f"\n{name}: 文件不存在 {path}")
        continue
    print(f"\n{'=' * 74}\n{name}")
    t = time.time()
    r = g.generate_from_product(path)
    print("  用时 %.1fs  success=%s" % (time.time() - t, r.get('success')))
    if not r.get('success'):
        print("  失败:", r.get('error'))
        continue

    solids = MoldFixer._extract_solids(path)
    product = MoldFixer._make_compound([s for _, s in solids])
    pv = vol(product)
    pb = _bbox_accurate(product)
    print("  产品: vol=%.0f  z[%.2f,%.2f]" % (pv, pb[2], pb[5]))

    for kind in ('punch', 'cavity'):
        res = r[kind]
        shp = res['_shape']
        d = res['details']
        bb = _bbox_accurate(shp)
        v = vol(shp)
        cm = BRepAlgoAPI_Common(shp, product)
        cm.SetFuzzyValue(1e-3)
        cm.Build()
        iv = vol(cm.Shape()) if cm.IsDone() else -1
        print(f"  -- {kind}: solids={n_of(shp, TopAbs_SOLID)} shells={n_of(shp, TopAbs_SHELL)} "
              f"valid={d.get('valid')} vol={v:.0f}")
        print(f"     bbox x[{bb[0]:.1f},{bb[3]:.1f}] y[{bb[1]:.1f},{bb[4]:.1f}] z[{bb[2]:.1f},{bb[5]:.1f}]")
        print(f"     Common({kind},产品) = {iv:.3f}")

    # 下模是否真的挖了腔
    cav = r['cavity']
    cb = _bbox_accurate(cav['_shape'])
    box_vol = (cb[3] - cb[0]) * (cb[4] - cb[1]) * (cb[5] - cb[2])
    cv = vol(cav['_shape'])
    print("  ★ 下模包围盒体积=%.0f  实际体积=%.0f  差值=%.0f  产品体积=%.0f" % (
        box_vol, cv, box_vol - cv, pv))
    print("  ★ 挖腔是否合理（差值应≈产品体积）: %s" % (
        "是" if abs((box_vol - cv) - pv) < 0.15 * max(pv, 1) else "⚠ 否！型腔没挖出来"))
