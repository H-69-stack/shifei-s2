# -*- coding: utf-8 -*-
"""用 OpenCASCADE 的 HLR（消隐）算出形变面在 XY 上的【精确投影轮廓】，
输出成多边形点列，供 SolidWorks VBA 直接用 CreateLine 画出来。

为什么需要它：
    SW 的「成形到曲面」要求终止曲面【盖住整个草图轮廓】。形变面的投影只有
    包围盒的 76~92%（1131 是个机头罩壳，投影是半椭圆），所以矩形轮廓必然失败。
    而 SW 里「转换实体引用」对曲面体不产出实体（实测 True 但 0 个实体），
    所以轮廓改由 OCC 算好、以点表形式喂给 VBA。

输出：C:\\Temp\\m3test\\<tag>_outline.txt
    第 1 行 = 外环点数
    其后每行 = "x y"（单位 mm，世界 XY）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from OCP.STEPControl import STEPControl_Reader
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_WIRE
from OCP.TopoDS import TopoDS
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GCPnts import GCPnts_QuasiUniformDeflection


def load(path):
    r = STEPControl_Reader()
    if r.ReadFile(path) != 1:
        raise RuntimeError("读不了 " + path)
    r.TransferRoots()
    return r.OneShape()


def outline(shape):
    """返回可见外轮廓的边（已投影到 XY 平面）。"""
    algo = HLRBRep_Algo()
    algo.Add(shape)
    ax = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0))
    algo.Projector(HLRAlgo_Projector(ax))
    algo.Update()
    algo.Hide()
    hlr = HLRBRep_HLRToShape(algo)
    parts = []
    for name in ("OutLineVCompound", "VCompound", "Rg1LineVCompound"):
        try:
            s = getattr(hlr, name)()
            if s is not None and not s.IsNull():
                parts.append((name, s))
        except Exception as e:
            print("   %s 失败: %s" % (name, e))
    return parts


def sample_edges(shape, defl=0.2, maxpts=1200):
    """把 shape 里所有边离散成点，返回 [[(u,v),...], ...]（每条边一串）。"""
    chains = []
    ex = TopExp_Explorer(shape, TopAbs_EDGE)
    while ex.More():
        e = TopoDS.Edge_s(ex.Current())
        try:
            c = BRepAdaptor_Curve(e)
            d = GCPnts_QuasiUniformDeflection(c, defl)
            if d.IsDone() and d.NbPoints() >= 2:
                pts = []
                for i in range(1, d.NbPoints() + 1):
                    p = d.Value(i)
                    pts.append((p.X(), p.Y()))
                if len(pts) > maxpts:
                    step = max(1, len(pts) // maxpts)
                    pts = pts[::step]
                chains.append(pts)
        except Exception:
            pass
        ex.Next()
    return chains


def total_len(chains):
    import math
    t = 0.0
    for c in chains:
        for i in range(1, len(c)):
            t += math.dist(c[i - 1], c[i])
    return t


def main():
    items = [
        (r"C:\Temp\m3test\upper_sewn2rom.stp", "2ROM_upper"),
        (r"C:\Temp\m3test\lower_sewn2rom.stp", "2ROM_lower"),
        (os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      r"web_box_generator\uploads\contact_output\c37ff7241a19\f4adfe3f6156_YA-1131-505_MIRROR_xOy_PUNCH_CONTACT_FACES.stp"),
         "1131_upper"),
    ]
    for path, tag in items:
        if not os.path.exists(path):
            print("缺失", path)
            continue
        print("=" * 70)
        print(tag)
        sh = load(path)
        parts = outline(sh)
        for name, s in parts:
            chains = sample_edges(s)
            print("  %-20s 边数=%d  总长(mm)=%.1f" % (name, len(chains), total_len(chains)))
        # 取点数最多的那一层当外轮廓
        best = None
        for name, s in parts:
            chains = sample_edges(s)
            if not chains:
                continue
            if best is None or len(chains) > len(best[1]):
                best = (name, chains)
        if best is None:
            print("  !! 没算出轮廓")
            continue
        name, chains = best
        print("  -> 采用 %s，%d 条边" % (name, len(chains)))
        dst = r"C:\Temp\m3test\%s_outline.txt" % tag
        with open(dst, "w", encoding="ascii", errors="replace") as f:
            f.write("%d\n" % len(chains))
            for c in chains:
                f.write("%d\n" % len(c))
                for (u, v) in c:
                    f.write("%.6f %.6f\n" % (u, v))
        print("  -> 写出", dst, os.path.getsize(dst), "bytes")


if __name__ == "__main__":
    main()
