# -*- coding: utf-8 -*-
"""验证纯实体布尔方案：
   above = ∪ Prism(上形变面各片, +Z)      ← 形变面"上方空间"
   punch = Common(box[z_lo, P], above)     ← 实体∩实体，避开开放壳布尔

先确认 Prism(face) 出来的是 SOLID 还是 SHELL。
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
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse, BRepAlgoAPI_Common
from OCP.BRepBuilderAPI import (BRepBuilderAPI_Sewing, BRepBuilderAPI_MakeSolid,
                                BRepBuilderAPI_MakeVertex)
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


def to_solid(shape):
    """把实体/壳统一成实体：已经是实体就原样返回；是壳则缝合+MakeSolid。"""
    if n_of(shape, TopAbs_SOLID) >= 1:
        return shape, "已是实体"
    try:
        sew = BRepBuilderAPI_Sewing(1e-4)
        sew.Add(shape)
        sew.Perform()
        sewn = sew.SewedShape()
        mk = BRepBuilderAPI_MakeSolid(TopoDS.Shell_s(sewn))
        if mk.IsDone():
            return mk.Solid(), "壳→缝合+MakeSolid"
    except Exception as e:
        return shape, "转实体失败 %r" % e
    return shape, "未转换"


t0 = time.time()
solids = MoldFixer._extract_solids(PRODUCT)
product = MoldFixer._make_compound([s for _, s in solids])
pb = bb(product)

ext = MoldContactSurfaceExtractor()
res = ext.extract_punch(PRODUCT)
faces_shape = ext.result_faces_shape(res)
ufaces = [e.Current() for e in iter(())]

# 取出上形变面的各片
fl = []
exp = TopExp_Explorer(TopoDS.Face_s(faces_shape) if faces_shape.ShapeType() == 4 else faces_shape,
                      TopAbs_FACE)
while exp.More():
    fl.append(exp.Current())
    exp.Next()
print("上形变面片数 =", len(fl))

fb = bb(faces_shape)
zmin, zmax = fb[2], fb[5]
P = zmin + (zmax - zmin)
z_lo = zmin - MARGIN
print("形变面 z[%.2f,%.2f]  P=%.2f  box z[%.2f,%.2f]" % (zmin, zmax, P, z_lo, P))

# ---- ① 逐面向上拉伸，看是实体还是壳 ----
print("\n=== ① Prism(单片面, +Z) 的类型 ===")
pr0 = BRepPrimAPI_MakePrism(TopoDS.Face_s(fl[0]), gp_Vec(0, 0, (P - bb(fl[0])[5]) + 10.0)).Shape()
print("  单面拉伸: SOLID=%d SHELL=%d vol=%.3f" % (
    n_of(pr0, TopAbs_SOLID), n_of(pr0, TopAbs_SHELL), vol(pr0)))

# ---- ② 全部面向上拉并并集 ----
print("\n=== ② ∪ Prism(各片, +Z) ===")
acc = None
for i, f in enumerate(fl):
    h = (P - bb(f)[5]) + 10.0
    if h <= 0.1:
        continue
    try:
        pr = BRepPrimAPI_MakePrism(TopoDS.Face_s(f), gp_Vec(0, 0, h)).Shape()
    except Exception:
        continue
    pr, how = to_solid(pr)
    if acc is None:
        acc = pr
    else:
        fu = BRepAlgoAPI_Fuse(acc, pr)
        fu.SetFuzzyValue(1e-3)
        fu.Build()
        if fu.IsDone() and not fu.Shape().IsNull():
            acc = fu.Shape()
acc, how = to_solid(acc)
print("  above: SOLID=%d SHELL=%d vol=%.0f valid=%s (%s)" % (
    n_of(acc, TopAbs_SOLID), n_of(acc, TopAbs_SHELL), vol(acc),
    BRepCheck_Analyzer(acc).IsValid(), how))
print("  above bbox z[%.2f,%.2f]" % (bb(acc)[2], bb(acc)[5]))

# ---- ③ 盒体 ∩ above ----
print("\n=== ③ punch = Common(box[z_lo,P], above) ===")
box = BRepPrimAPI_MakeBox(gp_Pnt(pb[0] - MARGIN, pb[1] - MARGIN, z_lo),
                          (pb[3] - pb[0]) + 2 * MARGIN,
                          (pb[4] - pb[1]) + 2 * MARGIN, P - z_lo).Shape()
cm = BRepAlgoAPI_Common(box, acc)
cm.SetFuzzyValue(1e-4)
cm.Build()
print("  IsDone =", cm.IsDone())
if cm.IsDone() and not cm.Shape().IsNull():
    punch = cm.Shape()
    pb2 = bb(punch)
    print("  punch: SOLID=%d SHELL=%d vol=%.0f valid=%s" % (
        n_of(punch, TopAbs_SOLID), n_of(punch, TopAbs_SHELL), vol(punch),
        BRepCheck_Analyzer(punch).IsValid()))
    print("  punch bbox x[%.2f,%.2f] y[%.2f,%.2f] z[%.2f,%.2f]" % (
        pb2[0], pb2[3], pb2[1], pb2[4], pb2[2], pb2[5]))
    print("  ★ 顶面 = P ? %s (差 %.4f)" % (abs(pb2[5] - P) < 0.05, pb2[5] - P))

    # 贴合检查：产品上表面采样点到 punch 的距离
    exp = TopExp_Explorer(product, TopAbs_FACE)
    tot = ok = 0
    mx = 0.0
    while exp.More():
        f = exp.Current()
        try:
            a = BRepAdaptor_Surface(TopoDS.Face_s(f))
            for i in range(3):
                for j in range(3):
                    pr = BRepLProp_SLProps(a, a.FirstUParameter() + (a.LastUParameter() - a.FirstUParameter()) * (i + .5) / 3,
                                           a.FirstVParameter() + (a.LastVParameter() - a.FirstVParameter()) * (j + .5) / 3, 1, 1e-7)
                    if pr.IsNormalDefined() and pr.Normal().Z() > 0.1:
                        tot += 1
                        d = BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(pr.Value()).Vertex(), punch)
                        d.Perform()
                        dv = d.Value() if d.IsDone() else 99
                        if dv <= 0.5:
                            ok += 1
                        mx = max(mx, dv)
        except Exception:
            pass
        exp.Next()
    print("  上表面采样 %d，贴合(<=0.5mm) %d (%.1f%%) 最大距 %.3fmm" % (
        tot, ok, 100.0 * ok / max(tot, 1), mx))
print("\n用时 %.1fs" % (time.time() - t0))
