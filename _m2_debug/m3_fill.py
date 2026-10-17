# -*- coding: utf-8 -*-
"""把形变面上的【孔】补平，输出新的 STEP。

为什么必须补：
    SW 的「成形到曲面」要求终止曲面【完整盖住草图轮廓】。
    2ROM 的上形变面在中间有个圆孔（板料本身有孔），轮廓盖住孔 →
    孔的位置没有任何曲面可终止 → FeatureExtrusion3 返回 Nothing。
    （已实测确认：把轮廓换成避开孔的小矩形，立刻就成功了。）

补成什么样：
    每个孔用 BRepOffsetAPI_MakeFilling 铺一张与孔边 G0 相接的补片。
    这样上下模在孔处【相互贴合】——真实成形模在孔的位置就是上下模对压的，
    所以这比"上模留个洞 / 下模留根柱子"都更符合实际。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from OCP.STEPControl import STEPControl_Reader, STEPControl_Writer, STEPControl_AsIs
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_WIRE
from OCP.TopoDS import TopoDS
from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
from OCP.TopTools import TopTools_HSequenceOfShape
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeFilling
from OCP.GeomAbs import GeomAbs_Shape
from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib


def load(path):
    r = STEPControl_Reader()
    if r.ReadFile(path) != 1:
        raise RuntimeError("读不了 " + path)
    r.TransferRoots()
    return r.OneShape()


def free_edges(shape):
    """只有 1 个相邻面的边 = 自由边（壳的边界）。"""
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
    m = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, m)
    out = []
    for i in range(1, m.Extent() + 1):
        e = m.FindKey(i)
        lst = m.FindFromIndex(i)
        if lst.Extent() == 1:
            out.append(e)
    return out


def free_wires(shape, tol=1e-3):
    edges = free_edges(shape)
    print("  自由边数 = %d" % len(edges))
    if not edges:
        return []
    seq = TopTools_HSequenceOfShape()
    for e in edges:
        seq.Append(e)
    wires = TopTools_HSequenceOfShape()
    try:
        ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(seq, tol, False, wires)
    except TypeError as ex:
        print("  !! ConnectEdgesToWires_s 失败: %s" % ex)
        return []
    out = []
    for i in range(1, wires.Length() + 1):
        out.append(TopoDS.Wire_s(wires.Value(i)))
    return out


def wire_span(w):
    b = Bnd_Box()
    BRepBndLib.Add_s(w, b, False)
    x1, y1, z1, x2, y2, z2 = b.Get()
    return ((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2) ** 0.5


def fill_wire(w, edge_n=0):
    # ① 平面补片：孔多半落在平面上（板料圆孔），MakeFace 直接搞定
    try:
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        mf = BRepBuilderAPI_MakeFace(w, False)
        if mf.IsDone():
            s = mf.Face()
            if s is not None and not s.IsNull():
                return s, -1
        else:
            print("      MakeFace 未完成")
    except Exception as e:
        print("      MakeFace 异常: %s" % e)

    # ② 曲面补片：非平面孔边用 MakeFilling 铺一张 G0 相接的补片
    # 签名: (Degree, NbPtsOnCur, NbIter, Anisotropie, Tol2d, Tol3d, TolAng, TolCurv, MaxDeg, MaxSegments)
    # ★MaxSegments 必须是整数★（传 True 会 TypeError）
    mk = BRepOffsetAPI_MakeFilling(3, 15, 2, False, 1e-5, 1e-4, 1e-2, 0.1, 8, 9)
    n = 0
    ex = TopExp_Explorer(w, TopAbs_EDGE)
    while ex.More():
        mk.Add(TopoDS.Edge_s(ex.Current()), GeomAbs_Shape.GeomAbs_C0)
        n += 1
        ex.Next()
    if n < 3:
        print("      MakeFilling 只有 %d 条边" % n)
        return None, n
    mk.Build()
    if not mk.IsDone():
        return None, n
    s = mk.Shape()
    if s.IsNull():
        return None, n
    return s, n


def process(src, dst, tol=1e-3, min_hole=2.0):
    sh = load(src)
    wires = free_wires(sh, tol)
    print("  自由边环数 = %d" % len(wires))
    if len(wires) < 1:
        print("  没有自由边（可能已是闭合壳），直接复制")
        w = STEPControl_Writer()
        w.Transfer(sh, STEPControl_AsIs)
        w.Write(dst)
        return

    spans = sorted(((wire_span(w), w) for w in wires), key=lambda t: -t[0])
    print("  各环跨距(mm) = %s" % ", ".join("%.1f" % s for s, _ in spans))
    outer = spans[0]
    holes = spans[1:]
    print("  外边界跨距 %.1f，孔候选 %d 个" % (outer[0], len(holes)))

    # 收集原始面
    faces = []
    ex = TopExp_Explorer(sh, TopAbs_FACE)
    while ex.More():
        faces.append(TopoDS.Face_s(ex.Current()))
        ex.Next()

    patches = []
    for span, w in holes:
        if span < min_hole:
            print("    跳过太小的环 (跨距 %.2f)" % span)
            continue
        s, n = fill_wire(w)
        if s is None:
            print("    !! 补孔失败 (跨距 %.1f, %d 条边)" % (span, n))
        else:
            patches.append(s)
            print("    补孔成功 (跨距 %.1f, %d 条边)" % (span, n))

    if not patches:
        print("  没有补上任何孔 —— 原样导出")
        w = STEPControl_Writer()
        w.Transfer(sh, STEPControl_AsIs)
        w.Write(dst)
        return

    sew = BRepBuilderAPI_Sewing(tol)
    for f in faces:
        sew.Add(f)
    for p in patches:
        sew.Add(p)
    sew.Perform()
    res = sew.SewedShape()
    print("  缝合后类型 = %s  自由边=%d  多重边=%d" % (
        res.ShapeType(), sew.NbFreeEdges(), sew.NbMultipleEdges()))
    w = STEPControl_Writer()
    w.Transfer(res, STEPControl_AsIs)
    print("  Write =", w.Write(dst), dst, os.path.getsize(dst))


def main():
    jobs = [
        (r"C:\Temp\m3test\upper_sewn2rom.stp", r"C:\Temp\m3test\upper_filled2rom.stp"),
        (r"C:\Temp\m3test\lower_sewn2rom.stp", r"C:\Temp\m3test\lower_filled2rom.stp"),
    ]
    for src, dst in jobs:
        print("=" * 70)
        print(os.path.basename(src), "->", os.path.basename(dst))
        if not os.path.exists(src):
            print("  缺失")
            continue
        try:
            process(src, dst)
        except Exception:
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
