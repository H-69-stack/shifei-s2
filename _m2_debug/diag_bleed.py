# -*- coding: utf-8 -*-
"""排查 2.63mm 外溢：先确认盒体自身 bbox，再试不同布尔参数。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Pnt, gp_Vec
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeBox
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse, BRepAlgoAPI_Common
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from mold_fix import MoldFixer
from mold_contact_surface_extractor import MoldContactSurfaceExtractor

PRODUCT = os.path.join(ROOT, "web_box_generator", "uploads",
                       "543e0fdcfa2e_2ROM31-6061 ---(1).stp")


def n_of(s, k):
    n, e = 0, TopExp_Explorer(s, k)
    while e.More():
        n += 1
        e.Next()
    return n


def bb(s):
    return MoldFixer._bbox_tuple(s)


def bb_opt(s):
    """带最优包围盒（不含容差扩张）"""
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(s, b, False, False)
    return b.Get()


def vol(s):
    p = GProp_GProps()
    BRepGProp.VolumeProperties_s(s, p)
    return p.Mass()


solids = MoldFixer._extract_solids(PRODUCT)
product = MoldFixer._make_compound([s for _, s in solids])
pb = bb(product)

ext = MoldContactSurfaceExtractor()
res = ext.extract_punch(PRODUCT)
fs = ext.result_faces_shape(res)
fl = []
e = TopExp_Explorer(fs, TopAbs_FACE)
while e.More():
    fl.append(TopoDS.Face_s(e.Current()))
    e.Next()

fb = bb(fs)
zmin, zmax = fb[2], fb[5]
P = zmin + (zmax - zmin)
z_lo = zmin - 5.0
print("P=%.4f z_lo=%.4f" % (P, z_lo))

# 盒体：两种造法对比
box1 = BRepPrimAPI_MakeBox(gp_Pnt(pb[0] - 5, pb[1] - 5, z_lo),
                           (pb[3] - pb[0]) + 10, (pb[4] - pb[1]) + 10, P - z_lo).Shape()
print("\n盒体1 BRepPrimAPI_MakeBox(Pnt,dx,dy,dz):")
print("  bbox     z[%.4f,%.4f]  vol=%.1f" % (bb(box1)[2], bb(box1)[5], vol(box1)))
print("  bbox_opt z[%.4f,%.4f]" % (bb_opt(box1)[2], bb_opt(box1)[5]))

# above
acc = None
for f in fl:
    h = (P - bb(f)[5]) + 10.0
    if h <= 0.1:
        continue
    pr = BRepPrimAPI_MakePrism(f, gp_Vec(0, 0, h)).Shape()
    if acc is None:
        acc = pr
    else:
        fu = BRepAlgoAPI_Fuse(acc, pr)
        fu.SetFuzzyValue(1e-3)
        fu.Build()
        if fu.IsDone():
            acc = fu.Shape()
print("\nabove: z[%.4f,%.4f] solids=%d" % (bb(acc)[2], bb(acc)[5], n_of(acc, TopAbs_SOLID)))

print("\n=== Common 各种参数 ===")
for tag, fuzz in (("默认(无fuzz)", None), ("fuzz=1e-4", 1e-4), ("fuzz=1e-3", 1e-3)):
    cm = BRepAlgoAPI_Common(box1, acc)
    if fuzz is not None:
        cm.SetFuzzyValue(fuzz)
    cm.Build()
    if cm.IsDone() and not cm.Shape().IsNull():
        s = cm.Shape()
        b1, b2 = bb(s), bb_opt(s)
        print("  %-14s solids=%d valid=%-5s bbox z[%.4f,%.4f]  opt z[%.4f,%.4f]" % (
            tag, n_of(s, TopAbs_SOLID), BRepCheck_Analyzer(s).IsValid(),
            b1[2], b1[5], b2[2], b2[5]))
    else:
        print("  %-14s 失败" % tag)

print("\n=== 换个思路：先 Cut(box, below) 而不是 Common(box, above) ===")
# below = ∪ Prism(面, -Z, 到 z_lo 以下)
dl = None
for f in fl:
    d = (bb(f)[2] - (z_lo - 10.0))
    if d <= 0.1:
        continue
    pr = BRepPrimAPI_MakePrism(f, gp_Vec(0, 0, -d)).Shape()
    if dl is None:
        dl = pr
    else:
        fu = BRepAlgoAPI_Fuse(dl, pr)
        fu.SetFuzzyValue(1e-3)
        fu.Build()
        if fu.IsDone():
            dl = fu.Shape()
print("below: z[%.4f,%.4f] solids=%d" % (bb(dl)[2], bb(dl)[5], n_of(dl, TopAbs_SOLID)))
cu = BRepAlgoAPI_Cut(box1, dl)
cu.SetFuzzyValue(1e-4)
cu.Build()
if cu.IsDone() and not cu.Shape().IsNull():
    s = cu.Shape()
    b1, b2 = bb(s), bb_opt(s)
    print("  Cut 结果 solids=%d valid=%s bbox z[%.4f,%.4f] opt z[%.4f,%.4f] vol=%.0f" % (
        n_of(s, TopAbs_SOLID), BRepCheck_Analyzer(s).IsValid(),
        b1[2], b1[5], b2[2], b2[5], vol(s)))
else:
    print("  Cut 失败")
