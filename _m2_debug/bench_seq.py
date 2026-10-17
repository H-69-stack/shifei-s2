# -*- coding: utf-8 -*-
"""逐对融合计时：看是"单个棱柱病态"还是"累加器越滚越大"。"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID
from OCP.gp import gp_Vec
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

from mold_method2_generator import _load_faces, _mix, _bbox

U = os.path.join(ROOT, "web_box_generator", "uploads")
P = os.path.join(U, "b721182a27e0_YA-1131-505_MIRROR_xOy_CAVITY_CONTACT_FACES.stp")


def n_sol(s):
    n, e = 0, TopExp_Explorer(s, TopAbs_SOLID)
    while e.More():
        n += 1
        e.Next()
    return n


faces = _load_faces(P)
bb = _bbox(_mix(faces), "cav")
zmin, zmax = bb[2], bb[5]
dt = max(zmax - zmin, 1e-6)
z_end = (zmin + dt) + max(dt * 1.5 + 20.0, 20.0)

prisms = []
for f in faces:
    fb = _bbox(f)
    big = max((z_end - fb[5]) + 2.0, 1.0)
    prisms.append(BRepPrimAPI_MakePrism(f, gp_Vec(0, 0, big)).Shape())

print("棱柱 %d 个。逐个融合计时（每步打印，看清趋势）:" % len(prisms), flush=True)
acc = prisms[0]
for i in range(1, len(prisms)):
    t = time.time()
    fu = BRepAlgoAPI_Fuse(acc, prisms[i])
    fu.SetFuzzyValue(1e-3)
    fu.Build()
    el = time.time() - t
    if fu.IsDone() and not fu.Shape().IsNull():
        acc = fu.Shape()
    print("  第 %2d 次融合: %6.2fs  累计实体=%d" % (i, el, n_sol(acc)), flush=True)
    if el > 60:
        print("  !! 单步超 60s，停", flush=True)
        break
print("完成", flush=True)
