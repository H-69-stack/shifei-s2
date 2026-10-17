# -*- coding: utf-8 -*-
"""点归属测试：法二B 下模在"产品足迹内、靠近顶面"处到底是空腔还是实心？

若型腔正确：足迹内靠近顶面处应为【空】（产品已被挖掉，且型腔向上开口）。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from OCP.gp import gp_Pnt
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.TopAbs import TopAbs_IN, TopAbs_OUT, TopAbs_ON
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID

from mold_fix import MoldFixer
from mold_method2_generator import MoldMethod2Generator, _bbox_accurate

PATH = os.path.join(ROOT, 'web_box_generator', 'uploads',
                    'f4adfe3f6156_YA-1131-505_MIRROR_xOy.stp')
NAME = 'YA-1131'

r = MoldMethod2Generator().generate_from_product(PATH)
cav = r['cavity']['_shape']
prod = MoldFixer._make_compound([s for _, s in MoldFixer._extract_solids(PATH)])
pb = _bbox_accurate(prod)
cb = _bbox_accurate(cav)
print("%s  产品 z[%.2f,%.2f] xy[%.1f,%.1f .. %.1f,%.1f]" % (
    NAME, pb[2], pb[5], pb[0], pb[1], pb[3], pb[4]))
print("        下模 z[%.2f,%.2f]" % (cb[2], cb[5]))


def inside(shape, p):
    c = BRepClass3d_SolidClassifier(shape, p, 1e-6)
    return c.State() == TopAbs_IN


cx, cy = (pb[0] + pb[3]) / 2.0, (pb[1] + pb[4]) / 2.0
print("\n竖直扫描（产品足迹中心 x=%.1f y=%.1f）:" % (cx, cy))
print("   z 位置          在下模内?   说明")
zs = [cb[5] - 0.5, cb[5] - 2, cb[5] - 5, pb[2] + 1, cb[2] + 1]
for z in zs:
    p = gp_Pnt(cx, cy, z)
    s = "实心(有料)" if inside(cav, p) else "空(型腔)"
    print("   z=%10.2f    %-10s %s" % (z, "是" if inside(cav, p) else "否", s))

print("\n足迹内多点（z 取顶面下 1mm）:")
hit_void = hit_solid = 0
for fx in (0.2, 0.35, 0.5, 0.65, 0.8):
    for fy in (0.3, 0.5, 0.7):
        x = pb[0] + (pb[3] - pb[0]) * fx
        y = pb[1] + (pb[4] - pb[1]) * fy
        if inside(cav, gp_Pnt(x, y, cb[5] - 1.0)):
            hit_solid += 1
        else:
            hit_void += 1
print("   空(型腔)=%d   实心=%d   (共 15 点)" % (hit_void, hit_solid))

print("\n足迹【外】取一点（基座本体，应实心）:")
ox, oy = pb[3] + 4.0, pb[4] + 4.0
print("   (%.1f,%.1f) z=顶面下2mm -> %s" % (
    ox, oy, "实心" if inside(cav, gp_Pnt(ox, oy, cb[5] - 2)) else "空"))
