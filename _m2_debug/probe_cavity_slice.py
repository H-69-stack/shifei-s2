# -*- coding: utf-8 -*-
"""用水平切片面积判定下模到底有没有空腔（比点分类器可靠）。

基座横截面 = base_lx × base_ly。若某 z 处截面积明显小于基座 → 那里有空腔。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from OCP.gp import gp_Pnt
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from mold_fix import MoldFixer
from mold_method2_generator import MoldMethod2Generator, _bbox_accurate

PATH = os.path.join(ROOT, 'web_box_generator', 'uploads',
                    'f4adfe3f6156_YA-1131-505_MIRROR_xOy.stp')
T = 0.5   # 切片厚度 mm


def vol(s):
    p = GProp_GProps()
    BRepGProp.VolumeProperties_s(s, p)
    return p.Mass()


r = MoldMethod2Generator().generate_from_product(PATH)
cav = r['cavity']['_shape']
prod = MoldFixer._make_compound([s for _, s in MoldFixer._extract_solids(PATH)])
pb = _bbox_accurate(prod)
cb = _bbox_accurate(cav)
base_area = (cb[3] - cb[0]) * (cb[4] - cb[1])
print("产品 z[%.2f,%.2f]" % (pb[2], pb[5]))
print("下模 z[%.2f,%.2f]  基座截面积=%.0f mm²" % (cb[2], cb[5], base_area))

print("\n  z          截面积      占基座    结论")
n = 12
for i in range(n):
    z = cb[2] + (cb[5] - cb[2]) * (i + 0.5) / n
    slab = BRepPrimAPI_MakeBox(
        gp_Pnt(cb[0] - 1, cb[1] - 1, z - T / 2),
        (cb[3] - cb[0]) + 2, (cb[4] - cb[1]) + 2, T).Shape()
    cm = BRepAlgoAPI_Common(cav, slab)
    cm.SetFuzzyValue(1e-4)
    cm.Build()
    a = vol(cm.Shape()) / T if cm.IsDone() else 0.0
    frac = a / base_area if base_area else 0
    tag = "有空腔" if frac < 0.99 else "实心"
    print("  %8.2f   %9.0f   %6.1f%%   %s" % (z, a, frac * 100, tag))
