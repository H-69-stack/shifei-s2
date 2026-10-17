# -*- coding: utf-8 -*-
"""试分块并集 / 过滤碎棱柱，解决 49 片一次性并集卡死。"""
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
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from mold_method2_generator import _load_faces, _mix, _bbox, _fuse_many, _fuse

U = os.path.join(ROOT, "web_box_generator", "uploads")
P = os.path.join(U, "b721182a27e0_YA-1131-505_MIRROR_xOy_CAVITY_CONTACT_FACES.stp")


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


faces = _load_faces(P)
bb = _bbox(_mix(faces), "cav")
zmin, zmax = bb[2], bb[5]
dt = max(zmax - zmin, 1e-6)
z_top = zmin + dt
z_end = z_top + max(dt * 1.5 + 20.0, 20.0)

prisms, vols = [], []
for f in faces:
    fb = _bbox(f)
    big = max((z_end - fb[5]) + 2.0, 1.0)
    pr = BRepPrimAPI_MakePrism(f, gp_Vec(0, 0, big)).Shape()
    prisms.append(pr)
    vols.append(vol(pr))
vols_sorted = sorted(vols)
print("棱柱 %d 个；体积 min=%.1f  中位=%.1f  max=%.1f" % (
    len(prisms), vols_sorted[0], vols_sorted[len(vols_sorted) // 2], vols_sorted[-1]))
print("小于 1.0 的棱柱数 =", sum(1 for v in vols if v < 1.0))
print("小于 100 的棱柱数 =", sum(1 for v in vols if v < 100.0))

print("\n===== 策略1：过滤掉体积 < 100 的碎棱柱后，一次性并集 =====", flush=True)
keep = [p for p, v in zip(prisms, vols) if v >= 100.0]
print("  保留 %d 个，开始...（限时观察）" % len(keep), flush=True)
t = time.time()
acc = _fuse_many(keep, "filtered")
print("  -> %.1fs  SOLID=%s" % (time.time() - t,
                                n_of(acc, TopAbs_SOLID) if acc is not None else "None"),
      flush=True)
