# -*- coding: utf-8 -*-
"""关键试验：不并集，直接用「一次布尔减 + 49 个工具」——
   Cut(base, [prism1..prism49])  用 BRepAlgoAPI_Cut 的 SetArguments/SetTools。

对比三种：
  A) 并集 49 个（已知卡死，只做前 8 个测单次耗时）
  B) 一次性 Cut（工具列表）
  C) 逐个 Cut 累加
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopTools import TopTools_ListOfShape
from OCP.gp import gp_Vec, gp_Pnt
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeBox
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from mold_method2_generator import _load_faces, _mix, _bbox, _cut

U = os.path.join(ROOT, "web_box_generator", "uploads")
P = os.path.join(U, "b721182a27e0_YA-1131-505_MIRROR_xOy_CAVITY_CONTACT_FACES.stp")


def n_of(s, k):
    n, e = 0, TopExp_Explorer(s, k)
    while e.More():
        n += 1
        e.Next()
    return n


def vol(s):
    p = GProp_GProps()
    BRepGProp.VolumeProperties_s(s, p)
    return p.Mass()


faces = _load_faces(P)
bb = _bbox(_mix(faces), "cav")
zmin, zmax = bb[2], bb[5]
dt = max(zmax - zmin, 1e-6)
z_top = zmin + dt
z_end = z_top + max(dt * 1.5 + 20.0, 20.0)
print("下模: zmin=%.2f ztop=%.2f zend=%.2f 面数=%d" % (zmin, z_top, z_end, len(faces)))

prisms = []
for f in faces:
    fb = _bbox(f)
    big = max((z_end - fb[5]) + 2.0, 1.0)
    prisms.append(BRepPrimAPI_MakePrism(f, gp_Vec(0, 0, big)).Shape())
print("棱柱造好 %d 个" % len(prisms))

base_bb = (bb[0] - 5, bb[1] - 5, zmin - dt - 5, bb[3] + 5, bb[4] + 5, z_top)
base = BRepPrimAPI_MakeBox(gp_Pnt(base_bb[0], base_bb[1], base_bb[2]),
                           base_bb[3] - base_bb[0], base_bb[4] - base_bb[1],
                           base_bb[5] - base_bb[2]).Shape()
print("基座 vol=%.0f" % vol(base))

print("\n===== B) 一次性 Cut（工具列表 49 个）=====", flush=True)
t = time.time()
args = TopTools_ListOfShape(); args.Append(base)
tools = TopTools_ListOfShape()
for pr in prisms:
    tools.Append(pr)
algo = BRepAlgoAPI_Cut()
algo.SetArguments(args)
algo.SetTools(tools)
algo.SetFuzzyValue(1e-3)
try:
    algo.SetRunParallel(True)
except Exception:
    pass
algo.Build()
dt_b = time.time() - t
print("  IsDone=%s  %.1fs" % (algo.IsDone(), dt_b), flush=True)
if algo.IsDone() and not algo.Shape().IsNull():
    s = algo.Shape()
    print("  solids=%d vol=%.0f  bbox z[%.2f,%.2f]" % (
        n_of(s, TopAbs_SOLID), vol(s), _bbox(s)[2], _bbox(s)[5]))
