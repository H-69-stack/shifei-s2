# -*- coding: utf-8 -*-
"""修正 Splitter 用法：SetArguments/SetTools 要 TopTools_ListOfShape。"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.TopTools import TopTools_ListOfShape
from OCP.gp import gp_Pnt
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

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


solids = MoldFixer._extract_solids(PRODUCT)
product = MoldFixer._make_compound([s for _, s in solids])
pb = bb(product)

ext = MoldContactSurfaceExtractor()
res = ext.extract_punch(PRODUCT)
sew = BRepBuilderAPI_Sewing(1e-4)
sew.Add(ext.result_faces_shape(res))
sew.Perform()
shell = sew.SewedShape()
sb = bb(shell)
zmin, zmax = sb[2], sb[5]
P = zmin + (zmax - zmin)
z_lo = zmin - MARGIN

box = BRepPrimAPI_MakeBox(gp_Pnt(pb[0] - MARGIN, pb[1] - MARGIN, z_lo),
                          (pb[3] - pb[0]) + 2 * MARGIN,
                          (pb[4] - pb[1]) + 2 * MARGIN, P - z_lo).Shape()
print("盒体 vol=%.0f z[%.2f,%.2f]" % (vol(box), z_lo, P))
print("壳 SHELL=%d FACE=%d" % (n_of(shell, TopAbs_SHELL), n_of(shell, TopAbs_FACE)))

print("\n=== Splitter(box, shell) ===")
t0 = time.time()
args = TopTools_ListOfShape(); args.Append(box)
tools = TopTools_ListOfShape(); tools.Append(shell)
sp = BRepAlgoAPI_Splitter()
sp.SetArguments(args)
sp.SetTools(tools)
sp.SetFuzzyValue(1e-4)
sp.Build()
print("  IsDone =", sp.IsDone(), " 用时 %.1fs" % (time.time() - t0))
if sp.IsDone() and not sp.Shape().IsNull():
    s = sp.Shape()
    print("  solids=%d vol=%.0f valid=%s" % (
        n_of(s, TopAbs_SOLID), vol(s), BRepCheck_Analyzer(s).IsValid()))
    e = TopExp_Explorer(s, TopAbs_SOLID)
    k = 0
    while e.More() and k < 10:
        cur = e.Current()
        b2 = bb(cur)
        print("    solid[%d] vol=%12.0f  z[%8.2f,%8.2f]  xy[%7.1f,%7.1f]" % (
            k, vol(cur), b2[2], b2[5], b2[0], b2[3]))
        k += 1
        e.Next()
else:
    print("  !! 失败")
