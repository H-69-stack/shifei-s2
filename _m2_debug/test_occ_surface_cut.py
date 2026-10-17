# -*- coding: utf-8 -*-
"""关键验证：OCC 能不能"用曲面切实体"。

对比两条路：
  A) BRepAlgoAPI_Cut(实体盒, 缝好的上形变面壳)
  B) BRepAlgoAPI_Splitter(实体盒, 缝好的上形变面壳) → 按位置挑出"曲面上方"那块
若可行 → 法二A 在 OCP 里就能修好，不需要 SolidWorks。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Pnt, gp_Vec
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeBox
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Splitter, BRepAlgoAPI_Common
from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing, BRepBuilderAPI_MakeVertex
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepLProp import BRepLProp_SLProps

from mold_fix import MoldFixer
from mold_contact_surface_extractor import MoldContactSurfaceExtractor

PRODUCT = os.path.join(ROOT, "web_box_generator", "uploads",
                       "543e0fdcfa2e_2ROM31-6061 ---(1).stp")
MARGIN = 5.0


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


def bb(s):
    return MoldFixer._bbox_tuple(s)


t0 = time.time()
print("产品:", os.path.basename(PRODUCT))

# 产品实体
solids = MoldFixer._extract_solids(PRODUCT)
product = MoldFixer._make_compound([s for _, s in solids])
pb = bb(product)
print("产品 z[%.2f,%.2f]  xy[%.2f,%.2f .. %.2f,%.2f]" % (pb[2], pb[5], pb[0], pb[1], pb[3], pb[4]))

# 上形变面 → 缝合壳
ext = MoldContactSurfaceExtractor()
res = ext.extract_punch(PRODUCT)
faces_shape = ext.result_faces_shape(res)
sew = BRepBuilderAPI_Sewing(1e-4)
sew.Add(faces_shape)
sew.Perform()
shell = sew.SewedShape()
print("缝合成: SHELL=%d FACE=%d" % (n_of(shell, TopAbs_SHELL), n_of(shell, TopAbs_FACE)))

sb = bb(shell)
zmin, zmax = sb[2], sb[5]
P = zmin + (zmax - zmin)
z_lo = zmin - MARGIN
print("形变面 z[%.2f,%.2f]  P=%.2f  box z[%.2f,%.2f]" % (zmin, zmax, P, z_lo, P))

# 足迹盒体
box = BRepPrimAPI_MakeBox(
    gp_Pnt(pb[0] - MARGIN, pb[1] - MARGIN, z_lo),
    (pb[3] - pb[0]) + 2 * MARGIN, (pb[4] - pb[1]) + 2 * MARGIN, P - z_lo).Shape()
print("盒体: vol=%.0f  solids=%d" % (vol(box), n_of(box, TopAbs_SOLID)))

# ---- A) Cut(实体, 壳) ----
print("\n=== A) BRepAlgoAPI_Cut(box, shell) ===")
try:
    c = BRepAlgoAPI_Cut(box, shell)
    c.SetFuzzyValue(1e-4)
    c.Build()
    if c.IsDone() and not c.Shape().IsNull():
        s = c.Shape()
        print("  Done  solids=%d shells=%d vol=%.0f valid=%s" % (
            n_of(s, TopAbs_SOLID), n_of(s, TopAbs_SHELL), vol(s),
            BRepCheck_Analyzer(s).IsValid()))
        print("  bbox z[%.2f,%.2f]" % (bb(s)[2], bb(s)[5]))
        for i, so in enumerate([e.Current() for e in iter(())]):
            pass
        e = TopExp_Explorer(s, TopAbs_SOLID)
        k = 0
        while e.More() and k < 6:
            b2 = bb(e.Current())
            print("    solid[%d] vol=%.0f z[%.2f,%.2f]" % (k, vol(e.Current()), b2[2], b2[5]))
            k += 1
            e.Next()
    else:
        print("  !! 未完成")
except Exception as ex:
    print("  异常:", repr(ex))

# ---- B) Splitter ----
print("\n=== B) BRepAlgoAPI_Splitter(box, shell) ===")
try:
    sp = BRepAlgoAPI_Splitter()
    sp.SetArguments([box])
    sp.SetTools([shell])
    sp.SetFuzzyValue(1e-4)
    sp.Build()
    if sp.IsDone() and not sp.Shape().IsNull():
        s = sp.Shape()
        print("  Done  solids=%d vol=%.0f valid=%s" % (
            n_of(s, TopAbs_SOLID), vol(s), BRepCheck_Analyzer(s).IsValid()))
        e = TopExp_Explorer(s, TopAbs_SOLID)
        k = 0
        while e.More() and k < 10:
            b2 = bb(e.Current())
            print("    solid[%d] vol=%.0f z[%.2f,%.2f] xy[%.1f,%.1f]" % (
                k, vol(e.Current()), b2[2], b2[5], b2[0], b2[3]))
            k += 1
            e.Next()
    else:
        print("  !! 未完成")
except Exception as ex:
    print("  异常:", repr(ex))

print("\n用时 %.1fs" % (time.time() - t0))
