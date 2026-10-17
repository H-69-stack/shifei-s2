# -*- coding: utf-8 -*-
"""定位下模 _stretch_up 卡点，并试「同曲面面片合并」减少布尔次数。"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID, TopAbs_SHELL
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Vec
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

from mold_method2_generator import _load_faces, _mix, _bbox, _fuse_many

U = os.path.join(ROOT, "web_box_generator", "uploads")
P = os.path.join(U, "b721182a27e0_YA-1131-505_MIRROR_xOy_CAVITY_CONTACT_FACES.stp")


def faces_of(shape):
    out, e = [], TopExp_Explorer(shape, TopAbs_FACE)
    while e.More():
        out.append(TopoDS.Face_s(e.Current()))
        e.Next()
    return out


def n_of(s, k):
    n, e = 0, TopExp_Explorer(s, k)
    while e.More():
        n += 1
        e.Next()
    return n


t = time.time()
faces = _load_faces(P)
print("原始面数 = %d   (%.2fs)" % (len(faces), time.time() - t))

# ① 缝合 + 同曲面合并
t = time.time()
sew = BRepBuilderAPI_Sewing(1e-4)
for f in faces:
    sew.Add(f)
sew.Perform()
sewn = sew.SewedShape()
print("缝合: SHELL=%d FACE=%d   (%.2fs)" % (
    n_of(sewn, TopAbs_SHELL), n_of(sewn, TopAbs_FACE), time.time() - t))

t = time.time()
try:
    uni = ShapeUpgrade_UnifySameDomain(sewn, True, True, False)
    uni.Build()
    merged = uni.Shape()
    mf = faces_of(merged)
    print("合并同曲面后: FACE=%d   (%.2fs)  ★片数 %d → %d" % (
        len(mf), time.time() - t, len(faces), len(mf)))
except Exception as e:
    mf = faces
    print("合并失败(%r)，仍用 %d 片" % (e, len(mf)))

# ② 造棱柱耗时
bb = _bbox(_mix(faces), "cav")
zmin, zmax = bb[2], bb[5]
dt = max(zmax - zmin, 1e-6)
z_top = zmin + dt
z_end = z_top + max(dt * 1.5 + 20.0, 20.0)

t = time.time()
prisms = []
for i, f in enumerate(mf):
    fb = _bbox(f)
    big = max((z_end - fb[5]) + 2.0, 1.0)
    pr = BRepPrimAPI_MakePrism(f, gp_Vec(0, 0, big)).Shape()
    prisms.append(pr)
print("造 %d 个棱柱: %.2fs" % (len(prisms), time.time() - t))

# ③ 一次性并集（这里最可能卡）
print(">>> 开始一次性并集（若卡住就是这里）...", flush=True)
t = time.time()
acc = _fuse_many(prisms, "bench")
print("并集完成: %.2fs  SOLID=%s" % (
    time.time() - t, n_of(acc, TopAbs_SOLID) if acc is not None else "None"))
