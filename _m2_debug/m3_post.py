# -*- coding: utf-8 -*-
"""法三导出收尾：把 SolidWorks 导出的 STEP 归一化成【真实体】。

为什么需要：
    SW 的 SaveAs 把零件写成了 SHELL_BASED_SURFACE_MODEL，里面包着两个壳：
        CLOSED_SHELL = 模具实体本身的边界（面数正确）
        OPEN_SHELL   = 残留的输入形变面（宏里的 EditDelete 没真正删掉它）
    只要有非实体面存在，SW 就整体按【曲面】导出，别的软件读回来就是 0 实体，
    后续装配/干涉检查全部失效。

做法：
    读 STEP → 找出所有壳 → 取闭合的那个 → BRepBuilderAPI_MakeSolid → 导出。
    顺带做 BRepCheck 校验 + 体积/包围盒核对。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from OCP.STEPControl import STEPControl_Reader, STEPControl_Writer, STEPControl_AsIs
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SHELL, TopAbs_SOLID, TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib


def shells_of(sh):
    out = []
    ex = TopExp_Explorer(sh, TopAbs_SHELL)
    while ex.More():
        out.append(TopoDS.Shell_s(ex.Current()))
        ex.Next()
    return out


def nfaces(s):
    n = 0
    ex = TopExp_Explorer(s, TopAbs_FACE)
    while ex.More():
        n += 1
        ex.Next()
    return n


def vol(sh):
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(sh, g)
    return g.Mass() / 1000.0     # mm^3


def bbox(sh):
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(sh, b, False, False)
    return b.Get()


def process(src, dst, expect_zmin=None):
    print("=" * 70)
    print(os.path.basename(src))
    r = STEPControl_Reader()
    if r.ReadFile(src) != 1:
        print("  读失败"); return None
    r.TransferRoots()
    sh = r.OneShape()
    shs = shells_of(sh)
    print("  壳数 = %d" % len(shs))
    closed = []
    for i, s in enumerate(shs):
        isc = BRep_Tool.IsClosed_s(s)
        nf = nfaces(s)
        v = vol(s)
        print("    壳%d 面数=%2d 闭合=%s 体积=%.1f mm^3" % (i, nf, isc, v))
        if isc:
            closed.append((nf, v, s))
    if not closed:
        print("  !! 没有闭合壳"); return None
    closed.sort(key=lambda t: -t[1])
    nf, v, shell = closed[0]
    print("  -> 取闭合壳：面数=%d 体积=%.1f mm^3" % (nf, v))

    ms = BRepBuilderAPI_MakeSolid(shell)
    if not ms.IsDone():
        print("  !! MakeSolid 失败，改为缝合后再试")
        sew = BRepBuilderAPI_Sewing(1e-4)
        ex = TopExp_Explorer(shell, TopAbs_FACE)
        while ex.More():
            sew.Add(ex.Current()); ex.Next()
        sew.Perform()
        ms = BRepBuilderAPI_MakeSolid(TopoDS.Shell_s(sew.SewedShape()))
        if not ms.IsDone():
            print("  !! 仍然失败"); return None
    solid = ms.Solid()
    print("  实体有效 =", BRepCheck_Analyzer(solid).IsValid())
    print("  体积 = %.1f mm^3" % vol(solid))
    x1, y1, z1, x2, y2, z2 = bbox(solid)
    print("  bbox x[%.3f,%.3f] y[%.3f,%.3f] z[%.3f,%.3f] mm" % (x1, x2, y1, y2, z1, z2))

    w = STEPControl_Writer()
    w.Transfer(solid, STEPControl_AsIs)
    ok = w.Write(dst)
    print("  Write =", ok, dst, os.path.getsize(dst))

    r2 = STEPControl_Reader(); r2.ReadFile(dst); r2.TransferRoots()
    n = 0
    ex = TopExp_Explorer(r2.OneShape(), TopAbs_SOLID)
    while ex.More(): n += 1; ex.Next()
    print("  回读确认：实体数 =", n)
    return dst


def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "m3_out")
    jobs = [
        (os.path.join(out, "2ROM_PUNCH_VBA.stp"), os.path.join(out, "2ROM_PUNCH_M3.stp")),
        (os.path.join(out, "2ROM_CAVITY_VBA.stp"), os.path.join(out, "2ROM_CAVITY_M3.stp")),
    ]
    made = {}
    for src, dst in jobs:
        if not os.path.exists(src):
            print("缺失", src); continue
        r = process(src, dst)
        if r:
            made["punch" if "PUNCH" in dst else "cavity"] = dst

    # ---- 消除上下模干涉 ----
    # 上模 = 上形变面的竖直投影，下模 = 基座 − 下形变面的竖直投影。
    # 两个形变面是沿【板料法向】偏 2mm（板厚）的，在近竖直壁处，
    # 上表面的投影会低于下表面的投影 → 上下模咬入。实测咬入量
    # ≈ 2mm × 壁高 × 壁长 ≈ 6863 mm³（= 板厚 × 壁面积），是"法二A"的固有特性。
    # 真实模具不能干涉，所以从上模里减掉下模实体，做出应有的间隙。
    if "punch" in made and "cavity" in made:
        print("=" * 70)
        print("【消除干涉】上模 − 下模")
        rp = STEPControl_Reader(); rp.ReadFile(made["punch"]); rp.TransferRoots()
        rc = STEPControl_Reader(); rc.ReadFile(made["cavity"]); rc.TransferRoots()
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Common
        cut = BRepAlgoAPI_Cut(rp.OneShape(), rc.OneShape())
        cut.SetRunParallel(False)
        cut.Build()
        if not cut.IsDone():
            print("  !! Cut 失败")
            return
        res = cut.Shape()
        print("  去干涉后上模 有效 =", BRepCheck_Analyzer(res).IsValid())
        print("  去干涉后上模 体积 = %.1f  (原 %.1f)" % (vol(res), vol(rp.OneShape())))
        n = 0
        ex = TopExp_Explorer(res, TopAbs_SOLID)
        while ex.More(): n += 1; ex.Next()
        print("  实体数 =", n)
        chk = BRepAlgoAPI_Common(res, rc.OneShape())
        chk.SetRunParallel(False); chk.Build()
        print("  复核干涉 = %.4f mm^3" % (vol(chk.Shape()) if chk.IsDone() else -1))

        dst = os.path.join(out, "2ROM_PUNCH_M3C.stp")
        w = STEPControl_Writer()
        w.Transfer(res, STEPControl_AsIs)
        w.Write(dst)
        print("  ->", dst, os.path.getsize(dst))
        r2 = STEPControl_Reader(); r2.ReadFile(dst); r2.TransferRoots()
        n = 0
        ex = TopExp_Explorer(r2.OneShape(), TopAbs_SOLID)
        while ex.More(): n += 1; ex.Next()
        print("  回读实体数 =", n)


if __name__ == "__main__":
    main()
