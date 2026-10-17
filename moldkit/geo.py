# -*- coding: utf-8 -*-
"""几何基础工具（OpenCASCADE 封装）。

这里只放"到处都要用"的东西：读 STEP、量包围盒/体积、布尔、壳→实体。
所有长度单位统一【毫米】。
"""
import math

from OCP.STEPControl import STEPControl_Reader, STEPControl_Writer, STEPControl_AsIs
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopAbs import (TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID, TopAbs_EDGE,
                        TopAbs_WIRE, TopAbs_COMPOUND)
from OCP.TopoDS import TopoDS
from OCP.BRep import BRep_Tool
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepAlgoAPI import (BRepAlgoAPI_Common, BRepAlgoAPI_Cut,
                             BRepAlgoAPI_Fuse)
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing,
                                BRepBuilderAPI_Transform)
from OCP.gp import gp_Trsf, gp_Vec


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------
def read_step(path, heal=True):
    r = STEPControl_Reader()
    if r.ReadFile(path) != 1:
        raise RuntimeError("读不了 STEP: %s" % path)
    r.TransferRoots()
    sh = r.OneShape()
    if heal:
        try:
            from OCP.ShapeFix import ShapeFix_Shape
            fix = ShapeFix_Shape(sh)
            fix.Perform()
            sh = fix.Shape()
        except Exception:
            pass
    return sh


def write_step(shape, path):
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    w.Write(path)
    return path


# ---------------------------------------------------------------------------
# 量
# ---------------------------------------------------------------------------
def bbox(shape, accurate=True):
    """返回 (x1,y1,z1,x2,y2,z2)，毫米。

    ★一定要用 AddOptimal★：Bnd_Box 默认会带容差扩张（实测虚高可达 2.6mm），
    用它做"顶面是否平齐"这类校验会误报。
    """
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, b, False, False)
    return b.Get()


def volume(shape):
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return abs(g.Mass())


def area(shape):
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, g)
    return g.Mass()


def is_valid(shape):
    try:
        return BRepCheck_Analyzer(shape).IsValid()
    except Exception:
        return False


def count(shape, kind):
    n = 0
    ex = TopExp_Explorer(shape, kind)
    while ex.More():
        n += 1
        ex.Next()
    return n


def split_solids(shape):
    """把 compound 拆成实体列表（按体积从大到小）。"""
    out = []
    ex = TopExp_Explorer(shape, TopAbs_SOLID)
    while ex.More():
        out.append(ex.Current())
        ex.Next()
    out.sort(key=lambda s: -volume(s))
    return out


def compound(shapes):
    """把若干形状合成一个 compound。"""
    from OCP.TopoDS import TopoDS_Compound
    from OCP.BRep import BRep_Builder
    c = TopoDS_Compound()
    b = BRep_Builder()
    b.MakeCompound(c)
    for s in shapes:
        b.Add(c, s)
    return c


def fuse_many(shapes, fuzz=1e-3):
    """逐个融合（★不要用一次性多输入融合，OCC 会卡死★）。"""
    if not shapes:
        return None
    if len(shapes) == 1:
        return shapes[0]
    acc = shapes[0]
    for s in shapes[1:]:
        r = fuse(acc, s, fuzz)
        if r is not None:
            acc = r
    return acc


def counts(shape):
    return {
        "solids": count(shape, TopAbs_SOLID),
        "shells": count(shape, TopAbs_SHELL),
        "faces": count(shape, TopAbs_FACE),
        "edges": count(shape, TopAbs_EDGE),
    }


# ---------------------------------------------------------------------------
# 布尔（OCC 对开放壳不可靠，这里统一关掉并行、并做有效性兜底）
# ---------------------------------------------------------------------------
def _boolop(cls, a, b, fuzz=1e-3):
    op = cls(a, b)
    try:
        op.SetFuzzyValue(fuzz)
    except Exception:
        pass
    op.SetRunParallel(False)
    op.Build()
    if not op.IsDone():
        return None
    s = op.Shape()
    return None if s.IsNull() else s


def cut(a, b, fuzz=1e-3):
    return _boolop(BRepAlgoAPI_Cut, a, b, fuzz)


def fuse(a, b, fuzz=1e-3):
    return _boolop(BRepAlgoAPI_Fuse, a, b, fuzz)


def common(a, b, fuzz=1e-3):
    return _boolop(BRepAlgoAPI_Common, a, b, fuzz)


def intersects(a, b, tol_mm3=1.0):
    """两个体是否真的互相侵入（公共体积 > tol）。"""
    c = common(a, b)
    if c is None:
        return None
    return volume(c) > tol_mm3


# ---------------------------------------------------------------------------
# 壳 → 实体
# ---------------------------------------------------------------------------
def shells_of(shape):
    out = []
    ex = TopExp_Explorer(shape, TopAbs_SHELL)
    while ex.More():
        out.append(TopoDS.Shell_s(ex.Current()))
        ex.Next()
    return out


def make_solid_from_shell(shell):
    ms = BRepBuilderAPI_MakeSolid(shell)
    if ms.IsDone():
        return ms.Solid()
    sew = BRepBuilderAPI_Sewing(1e-4)
    ex = TopExp_Explorer(shell, TopAbs_FACE)
    while ex.More():
        sew.Add(ex.Current())
        ex.Next()
    sew.Perform()
    ms = BRepBuilderAPI_MakeSolid(TopoDS.Shell_s(sew.SewedShape()))
    return ms.Solid() if ms.IsDone() else None


def to_solid(shape, keep_all_closed=True):
    """把（可能被 SW 导成曲面的）形状归一化成真实体。

    ★SolidWorks 的 STEP 导出经常是 SHELL_BASED_SURFACE_MODEL★：
      闭合壳（模具本体）+ 开放壳（没删掉的输入曲面）混在一起，
      别的软件读回来就是 0 个实体。这里**取全部闭合壳**自己造成实体，
      只丢掉开放壳。

    ★为什么必须"全部"而不是"最大的那个"★
      布尔运算完全可能把一个模体切成好几块（实测 m4：主块 4,193,289 mm³
      之外还有 6 块 12~37 cm³ 的板，都在分型面附近）。
      只取最大的那块会**静默丢掉约 145,000 mm³ 材料**，而日志一路"成功"。

    返回 (solid_or_compound, info) ；失败返回 (None, info)。
    """
    info = {"shells": [], "picked": None, "closed_count": 0}
    if count(shape, TopAbs_SOLID) > 0:
        sols = []
        ex = TopExp_Explorer(shape, TopAbs_SOLID)
        while ex.More():
            sols.append(ex.Current())
            ex.Next()
        sols.sort(key=lambda s: -volume(s))
        info["picked"] = "already-solid"
        info["solid_count"] = len(sols)
        if len(sols) == 1 or not keep_all_closed:
            return sols[0], info
        # ★多实体也要全留★（否则同样会静默丢材料）
        from OCP.TopoDS import TopoDS_Compound
        from OCP.BRep import BRep_Builder
        comp = TopoDS_Compound()
        bld = BRep_Builder()
        bld.MakeCompound(comp)
        for s in sols:
            bld.Add(comp, s)
        info["picked"] = "already-solid-multi"
        return comp, info

    closed = []
    for i, s in enumerate(shells_of(shape)):
        isc = bool(BRep_Tool.IsClosed_s(s))
        nf = count(s, TopAbs_FACE)
        v = volume(s)
        info["shells"].append({"idx": i, "faces": nf, "closed": isc, "volume": v})
        if isc:
            closed.append((v, s))
    info["closed_count"] = len(closed)
    if not closed:
        return None, info

    closed.sort(key=lambda t: -t[0])
    solids = []
    for v, sh in closed:
        s = make_solid_from_shell(sh)
        if s is not None:
            solids.append(s)
    if not solids:
        return None, info
    if len(solids) == 1 or not keep_all_closed:
        info["picked"] = "closed-shell"
        info["solid_count"] = 1
        return solids[0], info

    # 多块 → 合并成一个 compound（一个文件里保留全部材料）
    from OCP.TopoDS import TopoDS_Compound
    from OCP.BRep import BRep_Builder
    comp = TopoDS_Compound()
    bld = BRep_Builder()
    bld.MakeCompound(comp)
    for s in solids:
        bld.Add(comp, s)
    info["picked"] = "all-closed-shells"
    info["solid_count"] = len(solids)
    return comp, info


# ---------------------------------------------------------------------------
# 变换
# ---------------------------------------------------------------------------
def translate(shape, dx=0.0, dy=0.0, dz=0.0):
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(dx, dy, dz))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def mirror_z(shape, z0=0.0):
    """关于 z=z0 平面镜像（产品镜像用）。"""
    from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
    t = gp_Trsf()
    t.SetMirror(gp_Ax2(gp_Pnt(0, 0, z0), gp_Dir(0, 0, 1)))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def box(x1, y1, z1, x2, y2, z2):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    b = BRepPrimAPI_MakeBox(max(x2 - x1, 1e-6), max(y2 - y1, 1e-6),
                            max(z2 - z1, 1e-6)).Shape()
    return translate(b, x1, y1, z1)


def polyline_area(pts):
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5
