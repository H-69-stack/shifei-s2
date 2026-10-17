# -*- coding: utf-8 -*-
"""
mold_refix.py —— 模具"再修理"模块（在第一次修复 PUNCH_FILLED / CAVITY_HOLLOWED 基础上二次修复）
================================================================================================

【模块定位】
  本模块在 mold_fix.py（第一次修复）输出之上做"再修理"，输入为两个 STEP 文件：
      · 修理后的上模（凸模） =  compound [基座, 凸起台, 产品]   （PUNCH_FILLED）
      · 修理后的下模（凹模） =  compound [型腔, 产品]           （CAVITY_HOLLOWED）
  完成两项再修复并输出新的 STEP：

  需求 1：去除产品结构体
      —— 把上下模 compound 内的"产品"结构体完全剔除，只保留模具实体本身。
         上模 → [基座, 凸起台]；下模 → [型腔]。

  需求 2：上模角落尖角自动补齐（延伸求交）
      —— 上模凸起台的左右两端（下缘角落）存在因布尔/平接残留下来的"微小空缺"：
         凸起台由两个弯曲侧曲面组成，靠近底部角落处这两条曲边未延伸相交，
         被默认圆角/平接截断成若干薄小面。本模块自动识别角落区域的两条弯曲
         边界边，沿各自切线方向向外延伸，求交得到尖角顶点，构造楔形补片并与
         凸起台布尔并集（Fuse），最终缝合（Sew / ShapeFix）成单一连续封闭实体，
         形成平齐锋利的"尖角"。

   需求 3：下模型腔角落凸起切除（默认执行）
       —— 下模型腔是上模凸起台的互补：凸起台同位置的"空缺"在型腔里是"凸起"
          （多出来的薄片/薄翼，非正常尖角）。本模块复用需求2 的"延伸求交"
          解析（检测细面/弯曲侧壁 → 两条曲边切线延长求交点 apex → 构造尖角楔形），
          但改为用 BRepAlgoAPI_Cut 把该楔块从型腔中切除，得到平齐锋利的正常尖角。

【技术底座】CadQuery 2.7.0 + OCP 7.8.1.1.post1 原生接口：
    - BRepBuilderAPI_MakeWire / MakeFace / MakePolygon    构造补片线/面
    - BRepBuilderAPI_MakeSolid / BRepPrimAPI_MakePrism    补片→实体
    - GeomAPI_ExtremaCurveCurve                          两条曲线求交
    - BRepAdaptor_Curve / GeomAbs_*                       曲边切线方向提取
    - BRepAlgoAPI_Fuse / BRepAlgoAPI_Cut / BRepAlgoAPI_Common  布尔运算
    - BRepBuilderAPI_Sewing / ShapeFix_Shape / BRepCheck  缝合与有效性校验

【输出文件命名约定】
    {输入名}_REFIXED.stp   （上下模各一个，放 --output-dir 下）
    以及 {输入名}_REFIX_report.txt（含体积/结构体数/角落检测报告）

【CLI 示例】
    venv\\Scripts\\python mold_refix.py \
        --punch fix_output\\YA-1238-01_MIRROR_xOy_PUNCH_FILLED.stp \
        --cavity fix_output\\YA-1238-01_MIRROR_xOy_CAVITY_HOLLOWED.stp \
        --output-dir refix_output

    # 只做需求1（去除产品）；需求2 角落补齐默认关闭，需 --corner-fill 显式启用；
    # 需求3 下模型腔角落凸起切除默认开启（--no-cavity-trim 关闭）
    ...
"""

import os
import sys
import math
import json
import argparse
import datetime

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception as _e:
    print("[Refix] 操作失败(已继续): %r" % (_e,))


def _force_utf8():
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception as _e:
            print("[Refix] 操作失败(已继续): %r" % (_e,))
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8', errors='replace')
            except Exception as _e:
                print("[Refix] 操作失败(已继续): %r" % (_e,))


_force_utf8()

import cadquery as cq

from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse, BRepAlgoAPI_Cut, BRepAlgoAPI_Common
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing, BRepBuilderAPI_Transform,
)
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeBox
from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from OCP.BRepLProp import BRepLProp_CLProps
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Tool, BRep_Builder
from OCP.TopoDS import TopoDS, TopoDS_Compound
from OCP.TopLoc import TopLoc_Location
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopAbs import (
    TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX,
    TopAbs_WIRE, TopAbs_IN,
)
from OCP.TopExp import TopExp_Explorer, TopExp
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape, TopTools_IndexedMapOfShape
from OCP.gp import gp_Pnt, gp_Vec, gp_Dir, gp_Ax2, gp_Trsf, gp_Pln
from OCP.GeomAbs import (
    GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere,
    GeomAbs_Torus, GeomAbs_BSplineSurface, GeomAbs_BezierSurface,
    GeomAbs_OtherSurface, GeomAbs_Line, GeomAbs_Circle,
    GeomAbs_BSplineCurve, GeomAbs_BezierCurve, GeomAbs_OtherCurve,
)
from OCP.GeomAPI import GeomAPI_ExtremaCurveCurve
from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs, STEPControl_ManifoldSolidBrep
from OCP.IFSelect import IFSelect_RetDone
from OCP.ShapeFix import ShapeFix_Shape
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepClass3d import BRepClass3d_SolidClassifier


SURF_NAME = {
    GeomAbs_Plane: 'Plane', GeomAbs_Cylinder: 'Cylinder', GeomAbs_Cone: 'Cone',
    GeomAbs_Sphere: 'Sphere', GeomAbs_Torus: 'Torus', GeomAbs_BSplineSurface: 'BSpline',
    GeomAbs_BezierSurface: 'Bezier', GeomAbs_OtherSurface: 'Other',
}
CURV_NAME = {
    GeomAbs_Line: 'Line', GeomAbs_Circle: 'Circle', GeomAbs_BSplineCurve: 'BSpline',
    GeomAbs_BezierCurve: 'Bezier', GeomAbs_OtherCurve: 'Other',
}


# =====================================================================
# 基础几何工具
# =====================================================================
def _bbox(shape):
    if shape is None or shape.IsNull():
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    try:
        b = Bnd_Box()
        BRepBndLib.Add_s(shape, b)
        return b.Get() if not b.IsVoid() else (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    except Exception:
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _volume(shape):
    try:
        p = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, p)
        return p.Mass()
    except Exception:
        x1, y1, z1, x2, y2, z2 = _bbox(shape)
        return (x2 - x1) * (y2 - y1) * (z2 - z1) if (x2 > x1 and y2 > y1 and z2 > z1) else 0.0


def _area(shape):
    try:
        p = GProp_GProps()
        BRepGProp.SurfaceProperties_s(shape, p)
        return p.Mass()
    except Exception:
        return 0.0


def _solid_count(shape):
    n = 0
    e = TopExp_Explorer(shape, TopAbs_SOLID)
    while e.More():
        n += 1
        e.Next()
    return n


def _count_faces(shape):
    n = 0
    e = TopExp_Explorer(shape, TopAbs_FACE)
    while e.More():
        n += 1
        e.Next()
    return n


def _has_geom(shape):
    if shape is None or shape.IsNull():
        return False
    return TopExp_Explorer(shape, TopAbs_SOLID).More() or TopExp_Explorer(shape, TopAbs_SHELL).More()


def _surface_type(face):
    try:
        return BRepAdaptor_Surface(TopoDS.Face_s(face)).GetType()
    except Exception:
        return None


def _curve_type(edge):
    try:
        return BRepAdaptor_Curve(TopoDS.Edge_s(edge)).GetType()
    except Exception:
        return None


def _edge_length(edge):
    ad = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
    try:
        u1, u2 = ad.FirstParameter(), ad.LastParameter()
        if u2 - u1 < 1e-9:
            return 0.0
        n = 24
        pts = [ad.Value(u1 + (u2 - u1) * k / (n - 1)) for k in range(n)]
        return sum(a.Distance(b) for a, b in zip(pts, pts[1:]))
    except Exception:
        return 0.0


def _edge_min_z(edge):
    ad = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
    try:
        u1, u2 = ad.FirstParameter(), ad.LastParameter()
        if u2 - u1 < 1e-9:
            return ad.Value(u1).Z()
        n = 8
        return min(ad.Value(u1 + (u2 - u1) * k / (n - 1)).Z() for k in range(n))
    except Exception:
        return 1e18


def _edge_points(edge, n=8):
    """采样边上的点，返回 [gp_Pnt, ...]（含两端点）。"""
    ad = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
    u1, u2 = ad.FirstParameter(), ad.LastParameter()
    if u2 - u1 < 1e-9:
        return [ad.Value(u1)]
    return [ad.Value(u1 + (u2 - u1) * k / (n - 1)) for k in range(n)]


def _edge_endpoints(edge):
    pts = []
    e = TopExp_Explorer(TopoDS.Edge_s(edge), TopAbs_VERTEX)
    while e.More():
        pts.append(BRep_Tool.Pnt_s(TopoDS.Vertex_s(e.Current())))
        e.Next()
    return pts


def _edge_mid(edge):
    ad = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
    u1, u2 = ad.FirstParameter(), ad.LastParameter()
    return ad.Value(0.5 * (u1 + u2))


def _tangent_at(edge, param):
    """边在参数 param 处的单位切线。返回 gp_Dir 或 None。"""
    try:
        ad = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
        cp = BRepLProp_CLProps(ad, 2, 1e-7)
        cp.SetParameter(param)
        if cp.IsTangentDefined():
            d = gp_Dir(1.0, 0.0, 0.0)
            cp.Tangent(d)
            return d
    except Exception as _e:
        print("[Refix] 操作失败(已继续): %r" % (_e,))
    return None


def _edge_params(edge):
    ad = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
    return ad.FirstParameter(), ad.LastParameter()


def _make_compound(shapes):
    comp = TopoDS_Compound()
    b = BRep_Builder()
    b.MakeCompound(comp)
    for s in shapes:
        if s is not None and not s.IsNull():
            b.Add(comp, s)
    return comp


def _export(shape, path, as_compound=False):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    w = STEPControl_Writer()
    mode = STEPControl_AsIs if as_compound else STEPControl_ManifoldSolidBrep
    w.Transfer(shape, mode)
    if w.Write(path) != IFSelect_RetDone:
        raise RuntimeError("STEP 导出失败: %s" % path)
    return path


def _point_inside(shape, pnt, tol=1e-6):
    any_solid = False
    e = TopExp_Explorer(shape, TopAbs_SOLID)
    while e.More():
        any_solid = True
        try:
            cl = BRepClass3d_SolidClassifier(e.Current())
            cl.Perform(pnt, tol)
            if cl.State() == TopAbs_IN:
                return True
        except Exception as _e:
            print("[Refix] 操作失败(已继续): %r" % (_e,))
        e.Next()
    if not any_solid:
        x1, y1, z1, x2, y2, z2 = _bbox(shape)
        return x1 < pnt.X() < x2 and y1 < pnt.Y() < y2 and z1 < pnt.Z() < z2
    return False


def _is_valid(shape):
    try:
        return BRepCheck_Analyzer(shape).IsValid()
    except Exception:
        return False


def _fuse(a, b, label="Fuse", fuzz=1e-4):
    f = BRepAlgoAPI_Fuse(a, b)
    f.SetRunParallel(False)
    f.SetFuzzyValue(fuzz)
    f.Build()
    if not f.IsDone() or not _has_geom(f.Shape()):
        raise RuntimeError("%s 布尔融合失败" % label)
    return f.Shape()


def _cut(a, b, label="Cut", fuzz=1e-4):
    c = BRepAlgoAPI_Cut(a, b)
    c.SetRunParallel(False)
    c.SetFuzzyValue(fuzz)
    c.Build()
    if not c.IsDone() or not _has_geom(c.Shape()):
        raise RuntimeError("%s 布尔切除失败" % label)
    return c.Shape()


def _common(a, b, label="Common", fuzz=1e-4):
    c = BRepAlgoAPI_Common(a, b)
    c.SetRunParallel(False)
    c.SetFuzzyValue(fuzz)
    c.Build()
    if not c.IsDone() or not _has_geom(c.Shape()):
        raise RuntimeError("%s 布尔求交失败" % label)
    return c.Shape()


def _translate(shape, dx, dy, dz):
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(dx, dy, dz))
    r = BRepBuilderAPI_Transform(shape, t, True)
    r.Build()
    return r.Shape()


def _sew_clean(shape, tol=1e-4):
    try:
        sew = BRepBuilderAPI_Sewing(tol)
        sew.Add(shape)
        sew.Perform()
        out = sew.SewedShape()
        if _has_geom(out):
            return out
    except Exception as _e:
        print("[Refix] 操作失败(已继续): %r" % (_e,))
    return shape


def _shapefix(shape, precision=1e-4):
    try:
        sf = ShapeFix_Shape(shape)
        sf.SetPrecision(precision)
        sf.Perform()
        out = sf.Shape()
        if _has_geom(out):
            return out
    except Exception as _e:
        print("[Refix] 操作失败(已继续): %r" % (_e,))
    return shape


def _cleanup_shape(shape, lin_tol=0.2, ang_tol=0.2, min_area=0.02):
    """安全清理：把角落/布尔产生的杂乱碎片整理为尽量少的连续整体面，不改整体形状。

    1) ShapeUpgrade_UnifySameDomain：把相邻共面/相切以及连续 BSpline 的面片
       合并为单一整体面（还原\"被布尔切成一块一块\"的曲面）；
    2) 丢弃面积过小的退化微面/碎面（角落毛刺凸起），并缝合修复为合法实体；
    3) 校验体积/实体数不因清理而改变，否则回退原形状，绝不引入过切/漏切。
    """
    orig_vol = _volume(shape)
    orig_sol = _solid_count(shape)
    out = shape
    try:
        u = ShapeUpgrade_UnifySameDomain(shape, True, True, True)
        u.SetLinearTolerance(lin_tol)
        u.SetAngularTolerance(ang_tol)
        u.Build()
        cand = u.Shape()
        if _has_geom(cand) and _solid_count(cand) == orig_sol \
                and abs(_volume(cand) - orig_vol) <= 0.01 * max(1.0, orig_vol):
            out = cand
    except Exception as _e:
        print("[Refix] 操作失败(已继续): %r" % (_e,))
    try:
        out = _shapefix(out, precision=1e-3)
    except Exception as _e:
        print("[Refix] 操作失败(已继续): %r" % (_e,))
    return (out, _count_faces(out), _count_faces(shape))


# =====================================================================
# 读取 STEP 并分解独立实体
# =====================================================================
def _extract_solids(filepath):
    if not os.path.exists(filepath):
        raise FileNotFoundError("文件不存在: %s" % filepath)
    result = cq.importers.importStep(filepath)
    shapes = result.vals() if hasattr(result, 'vals') else []
    solids = []
    for shp in shapes:
        s = shp.wrapped
        e = TopExp_Explorer(s, TopAbs_SOLID)
        while e.More():
            solids.append((_volume(e.Current()), e.Current()))
            e.Next()
        if not TopExp_Explorer(s, TopAbs_SOLID).More():
            e2 = TopExp_Explorer(s, TopAbs_SHELL)
            while e2.More():
                solids.append((_volume(e2.Current()), e2.Current()))
                e2.Next()
    if not solids:
        raise ValueError("未在 %s 中找到 Solid/Shell" % filepath)
    solids.sort(key=lambda x: x[0], reverse=True)
    return solids


# =====================================================================
# 需求 1：去除产品结构体
# =====================================================================
def remove_product(filepath, product_role=1):
    """从模具 compound 中剔除"产品"结构体。

    参数
    ----
    filepath : str     输入的模具 STEP（compound）
    product_role : int 产品的排序下标（按体积降序）。上模=[基座,凸起,产品]→产品=下标2；
                      下模=[型腔,产品]→产品=下标1。传入 None 时自动识别。

    返回
    ----
    (mold_solids, product_solid, removed_idx)
        mold_solids : list    剔除产品后保留的实体（list of TopoDS_Shape）
        product_solid : shape 被剔除的产品实体（None 表示未识别）
        removed_idx : int     被剔除实体的下标（-1 表示未识别）
    """
    solids = _extract_solids(filepath)
    idx = product_role
    if idx is None or idx < 0 or idx >= len(solids):
        idx = _auto_product_index(solids)
    kept = []
    product = None
    removed = -1
    for i, (v, s) in enumerate(solids):
        if i == idx:
            product = s
            removed = i
        else:
            kept.append(s)
    print("[Refix/需求1] %s" % os.path.basename(filepath))
    for i, (v, s) in enumerate(solids):
        tag = "  ← 产品(剔除)" if i == removed else ""
        print("   solid[%d] 体积=%.1f mm³ 面数=%d%s"
              % (i, v, _count_faces(s), tag))
    return kept, product, removed


def _auto_product_index(solids):
    """自动识别产品实体：体积显著小于模具主体，且其 bbox 的 Z 下限最低。

    产品通常是 compound 里体积较小、且在垂直方向占位最低的实体
    （上模产品底、下模产品底都低于分模面）。
    """
    best = -1
    best_score = -1.0
    for i, (v, s) in enumerate(solids):
        if i == 0:
            continue
        x1, y1, z1, x2, y2, z2 = _bbox(s)
        # 得分：体积越小越像产品；Zmin 越低越像产品（产品伸到分模面之下）
        score = (1.0 / (v + 1.0)) - z1 * 1e-5
        if score > best_score:
            best_score = score
            best = i
    return best if best >= 0 else 0


# =====================================================================
# 需求 2：上模角落尖角自动补齐（延伸求交）
# =====================================================================
def _vec_pq(p, q):
    return gp_Vec(q.X() - p.X(), q.Y() - p.Y(), q.Z() - p.Z())


def _normalize_vec(v):
    """把 gp_Vec 归一化后转为 gp_Dir。"""
    n = gp_Vec(v.X(), v.Y(), v.Z())
    n.Normalize()
    return gp_Dir(n.X(), n.Y(), n.Z())


def _dot(a, b):
    """gp_Vec 或 gp_Dir 之间的点积。"""
    return gp_Vec(a.X(), a.Y(), a.Z()).Dot(gp_Vec(b.X(), b.Y(), b.Z()))


def _line_line_closest(p1, d1, p2, d2):
    """两射线 p1+t*d1 与 p2+t*d2 最近点（返回 line1 上的点 gp_Pnt 或 None）。"""
    try:
        a = _dot(d1, d1)
        b = _dot(d1, d2)
        c = _dot(d2, d2)
        w0 = _vec_pq(p2, p1)   # p1 - p2（_vec_pq(a,b)=b-a）
        d = _dot(d1, w0)
        e = _dot(d2, w0)
        det = a * c - b * b
        if abs(det) < 1e-12:
            return None
        s = (b * e - c * d) / det
        apex = gp_Pnt(p1.X() + s * d1.X(), p1.Y() + s * d1.Y(), p1.Z() + s * d1.Z())
        if s < 0:
            return None
        return apex
    except Exception:
        return None


def _build_edge_face_map(shape):
    mm = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, mm)
    return mm


def _build_face_index(faces):
    """face 列表 → TopTools_IndexedMapOfShape（shape→序号 O(1) 查找）。

    替代在原 faces 列表里逐个 IsSame 线性扫（O(边数×面数) 次 pybind 调用）。
    """
    fidx = TopTools_IndexedMapOfShape()
    for f in faces:
        fidx.Add(f)
    return fidx


def _edge_faces(mm, edge, faces, fidx=None):
    """edge 的相邻面在 faces 中的序号列表。传 fidx 走 O(1) 索引查找。"""
    lst = mm.FindFromKey(edge)
    out = []
    while not lst.IsEmpty():
        sh = lst.First()
        if fidx is not None:
            j = fidx.FindIndex(sh)
            if j > 0:
                out.append(j - 1)
        else:
            for j, ff in enumerate(faces):
                if ff.IsSame(sh):
                    out.append(j)
                    break
        lst.RemoveFirst()
    return out


def _face_centroid(f):
    try:
        p = GProp_GProps()
        BRepGProp.SurfaceProperties_s(f, p)
        pt = p.CentreOfMass()
        return gp_Pnt(pt.X(), pt.Y(), pt.Z())
    except Exception:
        bb = _bbox(f)
        return gp_Pnt((bb[0] + bb[3]) / 2, (bb[1] + bb[4]) / 2, (bb[2] + bb[5]) / 2)


def _cluster_centers(pts, dist=12.0):
    """按距离把点聚成若干簇，返回每簇的中心点与成员。"""
    clusters = []
    for p in pts:
        placed = False
        for c in clusters:
            if p.Distance(c['center']) < dist:
                c['members'].append(p)
                cx = sum(q.X() for q in c['members']) / len(c['members'])
                cy = sum(q.Y() for q in c['members']) / len(c['members'])
                cz = sum(q.Z() for q in c['members']) / len(c['members'])
                c['center'] = gp_Pnt(cx, cy, cz)
                placed = True
                break
        if not placed:
            clusters.append({'center': p, 'members': [p]})
    return clusters


def _solid_center(shape):
    try:
        p = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, p)
        pt = p.CentreOfMass()
        return gp_Pnt(pt.X(), pt.Y(), pt.Z())
    except Exception:
        bb = _bbox(shape)
        return gp_Pnt((bb[0] + bb[3]) / 2, (bb[1] + bb[4]) / 2, (bb[2] + bb[5]) / 2)


def _face_outward_normal(face, prot_center):
    """面在面中心处的朝外单位法向（指向远离凸起中心）。返回 gp_Dir 或 None。"""
    try:
        tface = TopoDS.Face_s(face)
        adapt = BRepAdaptor_Surface(tface)
        u1, u2 = adapt.FirstUParameter(), adapt.LastUParameter()
        v1, v2 = adapt.FirstVParameter(), adapt.LastVParameter()
        INF = 1e8
        if abs(u1) > INF: u1 = -1.0
        if abs(u2) > INF: u2 = 1.0
        if abs(v1) > INF: v1 = -1.0
        if abs(v2) > INF: v2 = 1.0
        if u2 - u1 < 1e-12: u2 = u1 + 1.0
        if v2 - v1 < 1e-12: v2 = v1 + 1.0
        from OCP.BRepLProp import BRepLProp_SLProps
        props = BRepLProp_SLProps(adapt, 2, 1e-7)
        props.SetParameters(0.5 * (u1 + u2), 0.5 * (v1 + v2))
        if not props.IsNormalDefined():
            return None
        d = props.Normal()          # gp_Dir，已归一化
        c = _face_centroid(face)
        out = _vec_pq(prot_center, c)
        if _dot(out, d) < 0:
            d = gp_Dir(-d.X(), -d.Y(), -d.Z())
        return d
    except Exception:
        return None


def _min_endpoint_gap(faces, mm, ia, ib):
    """两面之间，边界边端点两两最近的水平距离（共享边≈0 会被跳过）。"""
    def ends(fi):
        out = []
        ee = TopExp_Explorer(faces[fi], TopAbs_EDGE)
        while ee.More():
            ed = ee.Current()
            ad = BRepAdaptor_Curve(TopoDS.Edge_s(ed))
            u1, u2 = ad.FirstParameter(), ad.LastParameter()
            out.append(ad.Value(u1))
            out.append(ad.Value(u2))
            ee.Next()
        return out
    pa = ends(ia)
    pb = ends(ib)
    best = 1e18
    for a in pa:
        for b in pb:
            g = _xy_dist(a, b)
            if g > 1e-6 and g < best:
                best = g
    return best


def _pick_wall_pair(candidates, faces, mm, prot_center,
                    area_frac=0.15, max_gap=25.0, max_dot=0.2):
    """从候选墙面中选出一对构成角落的两侧曲面：两曲面朝外法向对向（V 形两侧），
    且二者边界边端点彼此最接近（即两条曲边在角落处几乎相交但有缺口）。

    max_dot 控制"对向"判定上界：对严格 V 形壁默认 0.2；对圆弧薄翼的两侧
    （近似平行薄片，法向反向）可放宽到 0.6，让其也能被识别为侧壁对。
    """
    if not candidates:
        return []
    max_area = max(c['area'] for c in candidates)
    thr = max(area_frac * max_area, 1e-6)
    big = [c for c in candidates if c['area'] >= thr]
    if len(big) < 2:
        big = candidates
    best = []
    best_gap = 1e18
    n = len(big)
    for a in range(n):
        for b in range(a + 1, n):
            na = _face_outward_normal(big[a]['face'], prot_center)
            nb = _face_outward_normal(big[b]['face'], prot_center)
            if na is None or nb is None:
                continue
            if na.Dot(nb) >= max_dot:   # 需对向
                continue
            gap = _min_endpoint_gap(faces, mm, big[a]['idx'], big[b]['idx'])
            if 1e-6 < gap < max_gap and gap < best_gap:
                best_gap = gap
                best = [big[a]['idx'], big[b]['idx']]
    return best


def _pick_opposed_walls(candidates, prot_center, area_frac=0.15):
    """从候选曲面里选出朝外法向最"对向"的两个大曲面（构成 V 形角落的两侧）。

    先按面积筛掉明显的小面（< area_frac*最大面积），再在剩余大曲面上选法向最对向的一对。
    返回 [i, j] 或 []。
    """
    if not candidates:
        return []
    max_area = max(c['area'] for c in candidates)
    thr = max(area_frac * max_area, 1e-6)
    big = [c for c in candidates if c['area'] >= thr]
    if len(big) < 2:
        big = candidates
    best = []
    best_dot = 1.0
    n = len(big)
    for a in range(n):
        for b in range(a + 1, n):
            na = _face_outward_normal(big[a]['face'], prot_center)
            nb = _face_outward_normal(big[b]['face'], prot_center)
            if na is None or nb is None:
                continue
            dot = na.Dot(nb)
            if dot < best_dot:
                best_dot = dot
                best = [big[a]['idx'], big[b]['idx']]
    return best


def _xy_dist(p, q):
    dx = p.X() - q.X()
    dy = p.Y() - q.Y()
    return math.sqrt(dx * dx + dy * dy)


def _protrusion_xy_center(protrusion):
    bb = _bbox(protrusion)
    return gp_Pnt((bb[0] + bb[3]) / 2.0, (bb[1] + bb[4]) / 2.0, (bb[2] + bb[5]) / 2.0)


def _max_xy_radius(protrusion, center):
    mx = 0.0
    e = TopExp_Explorer(protrusion, TopAbs_VERTEX)
    while e.More():
        p = BRep_Tool.Pnt_s(TopoDS.Vertex_s(e.Current()))
        mx = max(mx, _xy_dist(p, center))
        e.Next()
    return mx if mx > 1e-9 else 1.0


def detect_corners(protrusion, sliver_area=40.0, wall_area=80.0,
                   cluster_dist=12.0, z_cap_frac=0.65, max_corners=4,
                   radial_frac=0.7):
    """在凸起台上自动识别需要"延伸求交"的角落。

    判据
    ----
    · 细小面（面积 < sliver_area）位于凸起台 XY 外缘附近（到 XY 中心的距离 >
      radial_frac * 最大半径）——布尔/平接残留的"微小空缺"都在外缘角落处；
    · 这些细面彼此聚集（距离 < cluster_dist）成簇；
    · 每簇附近的"弯曲大面"（非平面、面积 > wall_area）即该角落的两个弯曲侧曲面；
    · 与细面簇最靠近的弯曲大面边界边（优选最低、最长的曲边），即待延伸的两条曲边。

    返回
    ----
    list[dict]：每项 = {center, bbox, slivers, walls, ...}
    """
    faces = []
    e = TopExp_Explorer(protrusion, TopAbs_FACE)
    while e.More():
        faces.append(e.Current())
        e.Next()
    mm = _build_edge_face_map(protrusion)
    fidx = _build_face_index(faces)

    xy_center = _protrusion_xy_center(protrusion)
    max_rad = _max_xy_radius(protrusion, xy_center)
    radial_thr = radial_frac * max_rad
    prot_center = _solid_center(protrusion)

    # 1) 细面（外缘 + 面积小）
    slivers = []
    for i, f in enumerate(faces):
        a = _area(f)
        if a >= sliver_area:
            continue
        c = _face_centroid(f)
        if _xy_dist(c, xy_center) < radial_thr:
            continue
        slivers.append({'idx': i, 'face': f, 'bbox': _bbox(f),
                        'center': c, 'area': a})
    if not slivers:
        return []

    # 2) 聚类
    clusters = _cluster_centers([s['center'] for s in slivers], dist=cluster_dist)
    # ★max_corners 上限★：按簇大小降序取前 N（大簇=主拐角；小簇多为噪声/次特征）。
    # 此前参数被接受但从未使用，超过上限的角落也会全量处理。
    if max_corners and len(clusters) > max_corners:
        clusters = sorted(clusters,
                          key=lambda c: -len(c.get('members', [])))[:max_corners]
    corners = []
    for cl in clusters:
        members = [s for s in slivers if any(s['center'].Distance(q) < cluster_dist
                                             for q in cl['members'])]
        xs = [s['bbox'][0] for s in members] + [s['bbox'][3] for s in members]
        ys = [s['bbox'][1] for s in members] + [s['bbox'][4] for s in members]
        zs = [s['bbox'][2] for s in members] + [s['bbox'][5] for s in members]
        cb = (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))
        ccenter = gp_Pnt((cb[0] + cb[3]) / 2, (cb[1] + cb[4]) / 2, (cb[2] + cb[5]) / 2)

        # 3) 邻接的弯曲大面（墙壁）——取朝外法向最对向的两个（V 形两侧）
        wall_cand = {}
        for s in members:
            fe = TopExp_Explorer(s['face'], TopAbs_EDGE)
            while fe.More():
                for nf in _edge_faces(mm, fe.Current(), faces, fidx):
                    t = _surface_type(faces[nf])
                    if t == GeomAbs_Plane or t is None:
                        continue
                    a = _area(faces[nf])
                    if a >= wall_area:
                        wall_cand[nf] = a
                fe.Next()
        cand_list = [{'idx': i, 'face': faces[i], 'area': wall_cand[i]}
                     for i in wall_cand]
        walls = _pick_wall_pair(cand_list, faces, mm, prot_center)
        if len(walls) < 2:
            continue
        W1, W2 = walls[0], walls[1]
        corners.append({'ok': True, 'walls_hint': [W1, W2], 'center': ccenter,
                        'slivers': [s['idx'] for s in members], 'bbox': cb,
                        'log': '待定'})
    return corners


def _corner_geometry(protrusion, corner, max_extend=200.0):
    """解析一个角落：从 walls_hint/slivers 中确定两条待延伸曲边、端点、切线与交点。

    返回 dict {E1,E2,p1,t1,p2,t2,apex} 或 None（无法解析）。
    """
    faces = []
    e = TopExp_Explorer(protrusion, TopAbs_FACE)
    while e.More():
        faces.append(e.Current())
        e.Next()
    mm = _build_edge_face_map(protrusion)
    fidx = _build_face_index(faces)
    prot_center = _solid_center(protrusion)
    sliver_set = set(corner.get('slivers', []))
    ccenter = corner['center']
    walls = corner.get('walls_hint', [])
    cb = corner.get('bbox', (0, 0, 0, 0, 0, 0))
    dist_thresh = max(12.0, (cb[3] - cb[0]) * 1.5, (cb[4] - cb[1]) * 1.5)

    # 若没有 wall hint（之前未成功），这里重新找（取朝外法向最对向的两个大面）
    if len(walls) < 2:
        wall_cand = {}
        for si in sliver_set:
            fe = TopExp_Explorer(faces[si], TopAbs_EDGE)
            while fe.More():
                for nf in _edge_faces(mm, fe.Current(), faces, fidx):
                    t = _surface_type(faces[nf])
                    if t == GeomAbs_Plane or t is None:
                        continue
                    a = _area(faces[nf])
                    if a >= 80.0:
                        wall_cand[nf] = a
                fe.Next()
        cand_list = [{'idx': i, 'face': faces[i], 'area': wall_cand[i]}
                     for i in wall_cand]
        walls = _pick_wall_pair(cand_list, faces, mm, prot_center)
    if len(walls) < 2:
        return None
    W1, W2 = walls[0], walls[1]

    def corner_edge_of(wall_i):
        # 取"贴角落且外缘端点离凸起中心最远、尽量长"的曲边（即外缘最高点所在的边）
        fe = TopExp_Explorer(faces[wall_i], TopAbs_EDGE)
        cand = []
        while fe.More():
            ed = fe.Current()
            ad = BRepAdaptor_Curve(TopoDS.Edge_s(ed))
            u1, u2 = ad.FirstParameter(), ad.LastParameter()
            pA, pB = ad.Value(u1), ad.Value(u2)
            dxy = min(_xy_dist(pA, ccenter), _xy_dist(pB, ccenter))
            if dxy < dist_thresh:
                cand.append(ed)
            fe.Next()
        if not cand:
            return None
        cand.sort(key=lambda ed: _edge_length(ed), reverse=True)
        return cand[0]

    E1 = corner_edge_of(W1)
    E2 = corner_edge_of(W2)
    if E1 is None or E2 is None:
        return None

    def end_pt_and_tangent(ed):
        ad = BRepAdaptor_Curve(TopoDS.Edge_s(ed))
        u1, u2 = ad.FirstParameter(), ad.LastParameter()
        pA, pB = ad.Value(u1), ad.Value(u2)
        # 取"靠角落的一端"（该端即外缘尖点），用水平距离判断，避免长曲边另一端跑远
        if _xy_dist(pA, ccenter) <= _xy_dist(pB, ccenter):
            p, pU = pA, u1
        else:
            p, pU = pB, u2
        t = _tangent_at(ed, pU)
        if t is not None:
            out = _vec_pq(prot_center, p)
            if _dot(out, t) < 0:
                t = gp_Dir(-t.X(), -t.Y(), -t.Z())
        return p, t

    p1, t1 = end_pt_and_tangent(E1)
    p2, t2 = end_pt_and_tangent(E2)
    if t1 is None or t2 is None:
        return None
    if abs(_dot(t1, t2) - 1.0) < 1e-3:
        return None
    apex = _line_line_closest(p1, t1, p2, t2)
    if apex is None or p1.Distance(apex) > max_extend or p2.Distance(apex) > max_extend:
        return None
    # 楔块 z 区间：下界=两壁底部(≈板厚/缘)，上界=凸起顶面。角落缺口贯穿
    # 环面带(torus, ~z1.6-5.6)+侧壁带(cylinder, z5.6-顶) ，需覆盖到顶面。
    zb1 = _bbox(faces[W1])
    zb2 = _bbox(faces[W2])
    zlo = min(zb1[2], zb2[2])
    zhi = _bbox(protrusion)[5]
    return {'E1': E1, 'E2': E2, 'p1': p1, 't1': t1,
            'p2': p2, 't2': t2, 'apex': apex, 'W1': W1, 'W2': W2,
            'zlo': zlo, 'zhi': zhi}


def _triangle_normal(p1, p2, p3):
    v1 = _vec_pq(p1, p2)
    v2 = _vec_pq(p1, p3)
    n = v1.Crossed(v2)
    m = n.Magnitude()
    if m < 1e-12:
        return None
    return _normalize_vec(n)


def _make_bbox_box(shape):
    """以 shape 的包围盒构造一个长方体（用于把楔块裁剪回模具空间范围）。"""
    x1, y1, z1, x2, y2, z2 = _bbox(shape)
    if x2 - x1 < 1e-9 or y2 - y1 < 1e-9 or z2 - z1 < 1e-9:
        return None
    p = gp_Pnt(x1, y1, z1)
    return BRepPrimAPI_MakeBox(p, x2 - x1, y2 - y1, z2 - z1).Shape()


def _point_along_edge(edge, tip, dist):
    """沿边从端点 tip 向另一端延伸 dist 处的点（用于让楔块压进墙体、保证融合）。"""
    ad = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
    u1, u2 = ad.FirstParameter(), ad.LastParameter()
    pA, pB = ad.Value(u1), ad.Value(u2)
    total = max(_edge_length(edge), 1e-6)
    if tip.Distance(pA) <= tip.Distance(pB):
        u_start, sign, p0 = u1, 1.0, pA
    else:
        u_start, sign, p0 = u2, -1.0, pB
    # 采样步长 ~0.5mm，保证取到距离 dist 的位置而非按大跨步跳
    n = max(16, int(total / 0.5))
    prev = p0
    acc = 0.0
    for k in range(1, n + 1):
        u = u_start + sign * (u2 - u1) * k / n
        p = ad.Value(u)
        acc += prev.Distance(p)
        prev = p
        if acc >= dist:
            return p
    return prev


def _fuse_corner(current, wedge, wv, label="角落补齐"):
    """把角落楔块稳定融合进 current，并对结果做严格校验（体积/实体数/合法体）。
    布尔在某些角落镜像方向会失稳（丢失实体或体积爆涨），这里用界限+重试+ShapeFix 兜底。"""
    def plausible(f):
        if _solid_count(f) != 1:
            return False
        added = _volume(f) - _volume(current)
        if added <= 1e-6 or added > max(50.0, wv * 8.0):
            return False
        return True

    if not _is_valid(wedge):
        wedge = _shapefix(wedge)
    for a, b in [(current, wedge), (wedge, current)]:
        try:
            f = _fuse(a, b, label=label, fuzz=1e-4)
            if not plausible(f):
                continue
            if BRepCheck_Analyzer(f).IsValid():
                return f
            fs = _shapefix(f)
            if not plausible(fs) or not BRepCheck_Analyzer(fs).IsValid():
                continue
            # ShapeFix 可能把结果坍缩/爆涨，体积必须与融合前一致才可信
            if abs(_volume(fs) - _volume(f)) > 0.5 * _volume(f):
                continue
            return fs
        except Exception:
            continue
    raise RuntimeError("角落楔块融合未通过体积/合法体校验")


def _cut_corner(current, wedge, wv, label="角落去凸"):
    """把角落凸起从实体中稳定切除（与 _fuse_corner 对称），并校验体积/实体数/合法体。
    下模型腔是上模凸起台的互补：同位置的"楔块"在凸起台是空缺、在型腔是凸起，
    使用时用 BRepAlgoAPI_Cut 把该楔块从型腔切除，得到平齐锋利的正常尖角。

    说明：切后 BRepCheck_Analyzer 可能报 invalid（棱柱侧面与弯曲壁面近乎相切/重合，
    留下退化薄面）。实测该 invalid 结果体积正确、实体数=1，且经 STEP 写出再读回会被
    OCCT 归一化为 valid（体积不变）。因此只要"实体数=1 且切除量合理"，即便 analyzer
    报 invalid 也接受，交由最终 STEP 导出归一化。"""
    def plausible(f):
        if _solid_count(f) != 1:
            return False
        removed = _volume(current) - _volume(f)
        if removed <= 1e-6 or removed > max(50.0, wv * 8.0):
            return False
        return True

    if not _is_valid(wedge):
        wedge = _shapefix(wedge)
    candidates = []
    for a, b in [(current, wedge), (wedge, current)]:
        try:
            f = _cut(a, b, label=label, fuzz=1e-4)
            if not plausible(f):
                continue
            if BRepCheck_Analyzer(f).IsValid():
                return f
            fs = _shapefix(f)
            if plausible(fs) and BRepCheck_Analyzer(fs).IsValid() \
                    and abs(_volume(fs) - _volume(f)) <= 0.5 * _volume(f):
                return fs
            # invalid 但体积/实体数正确：接受，STEP 导出会归一化
            candidates.append(f)
        except Exception:
            continue
    for f in candidates:
        return f
    raise RuntimeError("角落凸起切除未通过体积/实体数校验")


def _build_wedge_vertical(q1, q2, apex, zlo, zhi):
    """构造竖直楔块：以 (q1,q2,apex) 为底（三者投影到同一 z 形成水平三角形），
    沿 z 向拉伸到 [zlo,zhi]，得到两曲面顶/底缘之间、外缘为尖角的竖直三棱柱。
    注意：q 点可能 z 略有差异（如底缘 1.5/1.6），必须投影到同一 z，否则棱柱扭曲/非法。"""
    try:
        try:
            ztri = (q1.Z() + q2.Z() + apex.Z()) / 3.0
        except Exception:
            ztri = q1.Z()
        a = gp_Pnt(q1.X(), q1.Y(), ztri)
        b = gp_Pnt(q2.X(), q2.Y(), ztri)
        c = gp_Pnt(apex.X(), apex.Y(), ztri)
        poly = BRepBuilderAPI_MakePolygon(a, b, c)
        poly.Close()
        w = poly.Wire()
        pln = gp_Pln(a, gp_Dir(0.0, 0.0, 1.0))
        face = BRepBuilderAPI_MakeFace(pln, w, True)
        if not face.IsDone():
            return None
        f = face.Face()
        up = max(0.0, zhi - ztri)
        down = max(0.0, ztri - zlo)
        shapes = []
        if up > 1e-6:
            shapes.append(BRepPrimAPI_MakePrism(f, gp_Vec(0, 0, up)).Shape())
        if down > 1e-6:
            shapes.append(BRepPrimAPI_MakePrism(f, gp_Vec(0, 0, -down)).Shape())
        if not shapes:
            return None
        res = shapes[0]
        for s in shapes[1:]:
            res = _fuse(res, s, label="楔块")
        if not BRepCheck_Analyzer(res).IsValid():
            res = _shapefix(res)
        return res
    except Exception:
        return None


def _build_miter_loft(protrusion, geo, corner, overlap=4.0):
    """构造"随高度放样"的尖角斜接实体（需求2 平滑版）。

    每个 z 截面 = (底墙尖点, 前墙尖点, 尖角顶点)，用 ThruSections 放样成实体。
    相比竖直三棱柱，放样实体的侧面跟随两墙的倾斜(torus 带 z1.6-5.6 外扩、
    之上竖直带 z5.6-顶)，与墙面平滑衔接，且外缘是位于轮廓极值点的竖直尖角边，
    不向外鼓出(无台阶)。返回实体；失败返回 None。
    """
    try:
        faces = []
        e = TopExp_Explorer(protrusion, TopAbs_FACE)
        while e.More():
            faces.append(e.Current()); e.Next()
        ccenter = corner['center']; cb = corner.get('bbox', (0, 0, 0, 0, 0, 0))
        dt = max(12.0, (cb[3] - cb[0]) * 1.5, (cb[4] - cb[1]) * 1.5)
        apex = geo['apex']; zhi = geo['zhi']
        pc = _solid_center(protrusion)

        def tip_edge(wi):
            cand = []
            fe = TopExp_Explorer(faces[wi], TopAbs_EDGE)
            while fe.More():
                ed = fe.Current()
                ad = BRepAdaptor_Curve(TopoDS.Edge_s(ed))
                u1, u2 = ad.FirstParameter(), ad.LastParameter()
                pA, pB = ad.Value(u1), ad.Value(u2)
                dxy = min(_xy_dist(pA, ccenter), _xy_dist(pB, ccenter))
                if dxy < dt:
                    L = _edge_length(ed)
                    if L > 3.0:
                        cand.append((L, ed))
                fe.Next()
            if not cand:
                return None
            cand.sort(key=lambda t: t[0])
            return cand[0][1]

        def sample(ed, n=8):
            ad = BRepAdaptor_Curve(TopoDS.Edge_s(ed))
            u1, u2 = ad.FirstParameter(), ad.LastParameter()
            pts = [ad.Value(u1 + (u2 - u1) * k / n) for k in range(n + 1)]
            pts.sort(key=lambda p: p.Z())
            return pts

        t1 = tip_edge(geo['W1']); t2 = tip_edge(geo['W2'])
        if t1 is None or t2 is None:
            return None
        f1 = sample(t1); f2 = sample(t2)
        # 上带：torus 顶沿竖直向上到壁顶，xy=顶端点（上带墙面是竖直的）
        for zz in range(7, int(zhi) + 1):
            top = f1[-1]
            if zz > top.Z():
                f1.append(gp_Pnt(top.X(), top.Y(), zz))
            top2 = f2[-1]
            if zz > top2.Z():
                f2.append(gp_Pnt(top2.X(), top2.Y(), zz))

        # 外表面 = 两墙尖角边连成的桥面(即轮廓)，向内加厚 thick 得到填充带。
        # 这样角落与轮廓齐平，不再向轮廓外鼓出。
        n = min(len(f1), len(f2))
        thick = max(6.0, overlap * 3.0)
        def inward(p, d):
            v = _vec_pq(pc, p); v = _normalize_vec(v)
            return gp_Pnt(p.X() - v.X() * d, p.Y() - v.Y() * d, p.Z())
        wires = []
        stepped = max(1, n // 10)
        for i in range(0, n, stepped):
            a = f1[i]; b = f2[i]; z = max(a.Z(), b.Z())
            a = gp_Pnt(a.X(), a.Y(), z); b = gp_Pnt(b.X(), b.Y(), z)
            ain = inward(a, thick); binp = inward(b, thick)
            poly = BRepBuilderAPI_MakePolygon(a, b, binp, ain)
            poly.Close()
            wires.append(BRepBuilderAPI_MakeWire(poly.Wire()).Wire())
        if len(wires) < 2:
            return None
        tb = BRepOffsetAPI_ThruSections(True)
        for w in wires:
            tb.AddWire(w)
        tb.SetSmoothing(False)
        tb.Build()
        if not tb.IsDone() or not _has_geom(tb.Shape()):
            return None
        out = tb.Shape()
        if not BRepCheck_Analyzer(out).IsValid():
            out = _shapefix(out)
        return out if _solid_count(out) == 1 else None
    except Exception:
        return None


def _build_wedge_prism(p1, p2, apex, depth):
    """以 尖角顶点apex + 两条切线端点p1/p2 为底，沿平面法向双向拉伸成三棱柱实心楔块。

    楔块的两个侧棱边即尖角，方向 = 原两条曲边端点处的切线方向，
    因此贴合处沿切线自然延伸，形成平齐锋利的尖角。
    返回 (solid, 拉伸厚度)；失败返回 None。
    """
    try:
        poly = BRepBuilderAPI_MakePolygon(p1, p2, apex)
        poly.Close()
        w = poly.Wire()
        n = _triangle_normal(p1, p2, apex)
        if n is None:
            return None
        pln = gp_Pln(p1, n)
        face = BRepBuilderAPI_MakeFace(pln, w, True)
        if not face.IsDone():
            return None
        f = face.Face()
        v1 = gp_Vec(n.X() * depth, n.Y() * depth, n.Z() * depth)
        v2 = gp_Vec(-n.X() * depth, -n.Y() * depth, -n.Z() * depth)
        p1s = BRepPrimAPI_MakePrism(f, v1)
        p2s = BRepPrimAPI_MakePrism(f, v2)
        if not p1s.IsDone() or not p2s.IsDone():
            return None
        return _fuse(p1s.Shape(), p2s.Shape(), label="楔块", fuzz=1e-4)
    except Exception:
        return None


def fill_corners(protrusion, sliver_area=40.0, wall_area=80.0,
                 cluster_dist=12.0, z_cap_frac=0.65, max_corners=4,
                 wedge_depth=None, radial_frac=0.7):
    """对上模凸起台执行"延伸求交"角落补齐。

    解析两条曲边 → 切线延长求交得尖角顶点 → 构造楔块 → Fuse 融合。
    返回 (新凸起台, 报告列表)。
    """
    report = []
    current = protrusion
    corners = detect_corners(protrusion, sliver_area=sliver_area,
                             wall_area=wall_area, cluster_dist=cluster_dist,
                             z_cap_frac=z_cap_frac, max_corners=max_corners,
                             radial_frac=radial_frac)
    if not corners:
        print("[Refix/需求2] 未检测到可补齐的角落")
        return current, report

    # 每个角落只处理一次：先对"原始凸起"解析几何，再依次融合（左右角落彼此独立），
    # 避免"填充后重新检测"导致同一角落被反复补料。
    pending = []
    for corner in corners:
        cb = corner.get('bbox', (0, 0, 0, 0, 0, 0))
        notch = max(cb[3] - cb[0], cb[4] - cb[1], cb[5] - cb[2])
        depth = wedge_depth if wedge_depth else max(6.0, notch * 2.0, 12.0)
        geo = _corner_geometry(protrusion, corner)
        if geo is None:
            report.append({'index': len(pending), 'ok': False, 'log': '角落几何解析失败'})
            continue
        pending.append((corner, geo, notch, depth))

    # 按尖角 X 降序（先右后左）。实测先左后右时，第二个角落的布尔融合会对镜像侧失稳
    # （丢实体/体积异常），先处理后处理的镜像侧反而稳定。
    pending.sort(key=lambda t: t[1]['apex'].X(), reverse=True)
    corner_seq = 0
    for (corner, geo, notch, depth) in pending:
        apex = geo['apex']
        # 优先"随高度放样斜接"(平滑、贴合墙倾斜、外缘尖角边不外鼓)；失败再试竖直三棱柱。
        # 两种楔块依次尝试融合，取第一个通过体积/合法体校验的结果。
        candidates = []
        lo = _build_miter_loft(protrusion, geo, corner, overlap=8.0)
        if lo is not None:
            candidates.append(('loft', lo))
        overlap = max(4.0, min(9.0, notch * 0.55))
        q1 = _point_along_edge(geo['E1'], geo['p1'], overlap)
        q2 = _point_along_edge(geo['E2'], geo['p2'], overlap)
        pr = _build_wedge_vertical(q1, q2, apex, geo['zlo'], geo['zhi'])
        if pr is not None:
            candidates.append(('prism', pr))
        if not candidates:
            report.append({'index': corner_seq, 'ok': False, 'log': '楔块构造失败'})
            corner_seq += 1
            continue
        okd = False
        for (kind, wedge) in candidates:
            wv = _volume(wedge)
            # 健壮性：_build_miter_loft 曾沿上千 mm 周界弧采样，生成约 1e11 mm³ 的
            # 爆炸性巨块（尺寸/位置失真导致后续布尔过切/漏切）。角落里正常楔块仅为
            # 数百~数千 mm³，此处加体积上下限，越界直接跳过并回退到竖直三棱柱方案。
            if wv <= 0.5 or wv > max(2.0e6, 0.05 * _volume(protrusion)):
                print("[Refix/需求2] 角落%d(%s): 楔块体积越界(%.2f mm³)，跳过"
                      % (corner_seq, kind, wv))
                continue
            try:
                fused = _fuse_corner(current, wedge, wv, label="角落补齐")
                added = _volume(fused) - _volume(current)
                current = fused
                report.append({'index': corner_seq, 'ok': True, 'log': 'OK',
                               'mode': kind,
                               'apex': (apex.X(), apex.Y(), apex.Z()),
                               'wedge_vol': wv, 'added_vol': added,
                               'edges': [geo['E1'], geo['E2']]})
                print("[Refix/需求2] 角落%d(%s): 补齐成功，尖角=(%.2f,%.2f,%.2f) "
                      "楔块%.1f 实际+%.1f mm³"
                      % (corner_seq, kind, apex.X(), apex.Y(), apex.Z(), wv, added))
                okd = True
                break
            except Exception as e:
                print("[Refix/需求2] 角落%d(%s): 融合失败: %s" % (corner_seq, kind, e))
                continue
        if not okd:
            report.append({'index': corner_seq, 'ok': False, 'log': '融合失败'})
        corner_seq += 1
    # ★必须返回★：正常路径早期只有 `if not corners` 分支有 return，
    # 漏掉这里会让 refix() 的 `prot_fixed, corner_report = fill_corners(...)`
    # 在检测到角落时解包 None 直接崩（TypeError）。
    return current, report


def _hull_2d(pts):
    """2D 凸包（扫描线，返回逆时针顶点列表 [(x,y),...]）。输入为点列。"""
    pts = sorted(set((round(x, 2), round(y, 2)) for x, y in pts))
    if len(pts) <= 1:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _mesh_nodes(shape, deflect=0.3):
    """网格化 shape 并返回全部节点坐标 [(x,y,z),...]。

    ★性能★：BRepMesh_IncrementalMesh 对大型腔要几百 ms~数秒，
    而角落切除要在 (z_hi × radius) 组合扫描里反复查询同一 shape 的节点。
    这里把"剖分+取节点"与"按条件过滤"拆开，节点只算一次、到处复用。
    """
    try:
        BRepMesh_IncrementalMesh(shape, deflect)
    except Exception as e:
        print("[Refix] 网格化失败（继续尝试用已有剖分）: %r" % (e,))
    nodes = []
    e = TopExp_Explorer(shape, TopAbs_FACE)
    while e.More():
        f = TopoDS.Face_s(e.Current())
        loc = TopLoc_Location()
        try:
            tri = BRep_Tool.Triangulation_s(f, loc)
        except Exception:
            tri = None
        if tri is not None:
            trsf = loc.Transformation()
            for i in range(1, tri.NbNodes() + 1):
                p = tri.Node(i).Transformed(trsf)
                nodes.append((p.X(), p.Y(), p.Z()))
        e.Next()
    return nodes


def _corner_footprint_pts(shape, apex, zlo, zhi, radius, nodes=None):
    """在 shape 表面采样角落附近(z∈[zlo,zhi]、XY 距 apex ≤ radius)的网格节点，
    投影到 XY 平面，用于构造角落凸起的真实轮廓底面。

    nodes 可传入 _mesh_nodes() 的缓存结果，避免反复重新剖分同一 shape。
    """
    if nodes is None:
        nodes = _mesh_nodes(shape)
    pts = []
    ax, ay = apex.X(), apex.Y()
    r2 = radius * radius
    for (x, y, z) in nodes:
        if zlo <= z <= zhi and (x - ax) ** 2 + (y - ay) ** 2 <= r2:
            pts.append((x, y))
    return pts


def _build_hull_footprint_prism(shape, apex, zlo, zhi, radius, pad=0.0, nodes=None):
    """按角落凸起的真实 XY 足迹构造竖直棱柱切除体。

    旧实现用 (q1,q2,apex) 三点构造的窄三角形只贴着尖角附近，而实际凸起是一条
    外凸的弧形\"扇面\"薄壁（z≈基座顶带），远超该三角形范围，导致切除后基座顶面
    仍残留一小块扇形。这里改为：采样角落带内实体表面节点 → 取 XY 凸包 → 拉伸成
    棱柱，覆盖整条扇面薄壁而不误切外侧柱列壁。
    """
    pts = _corner_footprint_pts(shape, apex, zlo, zhi, radius, nodes=nodes)
    if len(pts) < 3:
        return None
    hull = _hull_2d(pts)
    if len(hull) < 3:
        return None
    try:
        mp = BRepBuilderAPI_MakePolygon()
        for (x, y) in hull:
            mp.Add(gp_Pnt(x + pad, y + pad, zlo))
        mp.Close()
        fl = BRepBuilderAPI_MakeFace(mp.Wire(), True)
        if not fl.IsDone():
            return None
        prim = BRepPrimAPI_MakePrism(fl.Face(), gp_Vec(0.0, 0.0, zhi - zlo))
        if not prim.IsDone():
            return None
        return prim.Shape()
    except Exception:
        return None


def trim_cavity_corners(cavity, punch_cut_solids=None, sliver_area=40.0, wall_area=80.0,
                        cluster_dist=12.0, z_cap_frac=0.65, max_corners=4,
                        wedge_depth=None, radial_frac=0.7):
    """下模(型腔)角落凸起切除（需求3，默认执行）。

    下模型腔是上模凸起台的互补：上模凸起台角落是"空缺"（需求2 用延伸求交补齐），
    下模型腔在同一位置是"凸起"（多出来的薄片/薄翼，非正常尖角）。
    本模块复用需求2 的"延伸求交"解析，但把"上模补齐的新增材料"反过来
    用 BRepAlgoAPI_Cut 从型腔中切除，得到平齐锋利的正常尖角。

    ★root cause（弧角落柱体残留的根因）★
    型腔角落的多余材料是一条"外凸弧形薄壁柱体"，从型腔底面(z≈-1.41)一路延伸到
    该角落簇 bbox 顶(z≈31.59)，柱体带之上才是正常直壁槽。旧实现把切除体的 z 上限
    封顶到"低带"顶(≈11.59)，只削掉了 z<11.59 的柱脚下半部，导致 z∈[11.59,31.59]
    的柱体上段（圆柱直壁面）残留；若改取整段壁顶(geo['zhi']≈34.59)又会误切柱体
    上方正常直壁（-3497mm³ 过切），且布尔在恰好落在柱体顶面时退化成多实体/负体积。
    本函数改为：识别拐角簇后取该簇 bbox 顶作为柱体全高上限，再按小余量逐级尝试 z_hi，
    取首个通过体积/实体数/合法体校验（_cut_corner）者切除；并按尖角 X 每侧去重
    (保留 bbox 顶最高、覆盖柱体全高的簇)，避免左右重复与镜像布尔失稳。

    punch_cut_solids : list[{solid,apex}]|None  保留参数(兼容)；本实现不再使用，
        指定了也会被忽略，统一走"柱体足迹棱柱"路径。

    返回 (新型腔, 报告列表)。切除失败不会中断整体流程，逐一记录到报告。
    """
    report = []
    current = cavity

    # ---- 需求3 凸起柱体切除：型腔自身几何 + 柱体带封顶的竖直三棱柱 ----
    corners = detect_corners(cavity, sliver_area=sliver_area,
                             wall_area=wall_area, cluster_dist=cluster_dist,
                             z_cap_frac=z_cap_frac, max_corners=max_corners,
                             radial_frac=radial_frac)
    if not corners:
        print("[Refix/需求3] 未检测到可切除的角落凸起")
        return current, report

    # 柱体带（"低带拐角簇"）封顶的旧逻辑已废弃：实测柱体从型腔底面一直延伸到该
    # 角落簇 bbox 顶（z≈31.59），而非低带顶（z≈11.59）。用低带顶封顶会留下柱体上段
    # 圆柱直壁，正是残留"弧角落柱体"的根因。故这里不再预计算 band_top，z_hi 直接
    # 按各角落自身 bbox 顶确定（见下方逐角处理）。

    parsed = []
    for corner in corners:
        geo = _corner_geometry(cavity, corner)
        if geo is None:
            report.append({'index': len(parsed), 'ok': False, 'log': '角落几何解析失败'})
            continue
        parsed.append((corner, geo))

    # 每侧保留最能代表柱体的拐角簇：柱体一直延伸到该簇 bbox 顶（实测 z≈31.59），
    # 故应取 bbox 顶最高者以覆盖柱体全高，而非旧实现的 bbox 顶最低者（会漏切柱体上段）。
    by_side = {}
    for corner, geo in parsed:
        side = 0 if geo['apex'].X() >= 0.0 else 1
        ahead = by_side.get(side)
        if ahead is None or corner['bbox'][5] > ahead[0]['bbox'][5]:
            by_side[side] = (corner, geo)

    pending = sorted(by_side.values(), key=lambda t: t[1]['apex'].X(), reverse=True)

    corner_seq = 0
    for (corner, geo) in pending:
        apex = geo['apex']
        cb = corner['bbox']
        notch = max(cb[3] - cb[0], cb[4] - cb[1], cb[5] - cb[2])
        overlap = max(4.0, min(9.0, notch * 0.55))
        q1 = _point_along_edge(geo['E1'], geo['p1'], overlap)
        q2 = _point_along_edge(geo['E2'], geo['p2'], overlap)
        # ★残留柱体根因★：柱体是一条外凸弧形薄壁，从型腔底面一路延伸到该角落簇
        # bbox 顶（实测 z≈31.59），并非只到"低带"顶（≈11.59）。旧实现把 z_hi 封顶到
        # band_top≈11.59，导致 z∈[11.59,31.59] 的柱体上段（圆柱直壁面）残留。
        # 故 z_hi 改为：几何上取该角落簇 bbox 顶（覆盖柱体全高），再留小余量；布尔在
        # 恰好落在柱体顶面时会退化成多实体/负体积，故按余量值逐级尝试，取首个通过
        # 体积/实体数/合法体校验者（由 _cut_corner 把关）。
        col_top = cb[5]
        # 柱脚段：贴合左右曲壁的竖直尖角三棱柱下探到型腔底面，补住 z<geo['zlo'] 的柱脚。
        z_lo = min(geo['zlo'], cb[2])
        margins = (0.5,)
        radii = (9.0, 15.0, 20.0, 26.0)
        # ★残留小扇形片根因★: 角落凸起是一条外凸的弧形薄壁，一直延伸到型腔顶面；
        # 旧实现把切除体顶 z_hi 封顶在 col_top - margin（顶面下方），于是顶带
        # z∈[col_top-margin, col_top] 处那片\"与上顶面相连的小扇形片\"切不到。
        # 这里把 z_hi 候选扩展到顶面之上(col_top+eps)，让切除体盖住整条薄壁
        # （含上顶面扇片），再取切除量最大且通过体积/实体数校验的结果。
        top_eps = (0.15, 0.5, 0.9)
        z_hi_list = sorted(
            {round(min(geo['zhi'], col_top - m), 4) for m in margins}
            | {round(col_top + e, 4) for e in top_eps})

        def _try(pr, z_hi):
            if pr is None or _volume(pr) <= 1e-6:
                return None
            try:
                cut = _cut_corner(current, pr, _volume(pr), label="corner-cut")
                removed = _volume(current) - _volume(cut)
                if removed > 1e-6:
                    return (removed, cut, _volume(pr), z_hi)
            except Exception as _e:
                print("[Refix] 操作失败(已继续): %r" % (_e,))
            return None

        # 单一健壮路径：把\"延伸求交楔块\"与\"凸包足迹棱柱\"融合，覆盖整条弧形
        # 扇面薄壁（含顶带小扇形片），对 z_hi（含顶面之上）与半径全扫描，取切除量
        # 最大且通过校验的结果。切除体顶超过顶面后，薄壁（含与上顶面相连的扇片）
        # 被完整切除，得到平齐锋利的正常尖角。
        # ★节点只剖分一次★：下面 (z_hi × radius) 全扫描都查同一 current 的节点，
        # 旧实现每次调用都重新 BRepMesh_IncrementalMesh 整个型腔（16 次/角落）。
        nodes = _mesh_nodes(current)
        cands = []
        for z_hi in z_hi_list:
            if z_hi <= geo['zlo'] + 1e-6:
                continue
            wv0 = _build_wedge_vertical(q1, q2, apex, z_lo, z_hi)
            for radius in radii:
                probe = _build_hull_footprint_prism(current, apex, z_lo, z_hi,
                                                    radius=radius, nodes=nodes)
                pr = probe
                if pr is not None and _volume(pr) > 1e-6:
                    if wv0 is not None and _volume(wv0) > 1e-6:
                        u = _fuse(wv0, pr, label="wedge+hull", fuzz=1e-4)
                        if u is not None and _volume(u) > 1e-6:
                            pr = u
                else:
                    pr = wv0
                r = _try(pr, z_hi)
                if r is not None:
                    cands.append((r[0], r[1], r[2], r[3], radius))
        chosen = max(cands, key=lambda t: t[0]) if cands else None
        done = chosen is not None

        if not done:
            print("[Refix/需求3] 角落%d(hull-prism): 所有 z_hi 切除均未通过校验" % corner_seq)
            report.append({'index': corner_seq, 'ok': False, 'log': '切除未通过体积/实体数校验'})
            corner_seq += 1
            continue
        removed, cut, wv, z_hi, radius = chosen
        current = cut
        report.append({'index': corner_seq, 'ok': True, 'log': 'OK',
                       'mode': 'hull-prism',
                       'apex': (apex.X(), apex.Y(), apex.Z()),
                       'wedge_vol': wv, 'removed_vol': removed,
                       'z_hi': z_hi, 'radius': radius, 'edges': [geo['E1'], geo['E2']]})
        print("[Refix/需求3] 角落%d(hull-prism): 切除凸起柱体成功，尖角=(%.2f,%.2f,%.2f) "
              "切除体%.1f 实际-%.1f mm³ (z≤%.2f)"
              % (corner_seq, apex.X(), apex.Y(), apex.Z(), wv, removed, z_hi))
        corner_seq += 1
    return current, report


def _edge_pts(edge, n=120, deflect=1.0):
    """采样一条边上的点坐标列表 [(x,y,z),...]。"""
    try:
        c = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
        d = GCPnts_QuasiUniformDeflection(c, deflect)
        if not d.IsDone():
            return []
        return [(d.Value(i).X(), d.Value(i).Y(), d.Value(i).Z()) for i in range(1, d.NbPoints() + 1)]
    except Exception:
        return []


def _make_axis_box(xlo, xhi, ylo, yhi, zlo, zhi):
    """由两个对角点构造包围盒实体(布尔/剖分工具体)。"""
    try:
        return BRepPrimAPI_MakeBox(gp_Pnt(xlo, ylo, zlo), gp_Pnt(xhi, yhi, zhi)).Shape()
    except Exception:
        return None


def split_cavity_bands(cavity, band_width=6.0, band_height=None):
    """把下模型腔沿"前缘弧环带"切分为 [型腔主体, 左带, 右带] 三个结构体。

    "带"已经存在于型腔材料里（前缘顶部一环带），本函数只把它当作独立结构体剖分出来，
    **不新增任何几何**（带复用已有材料；左右两带即为该环带以 x=0 为界的两半）。

    做法：
      1) 识别前壁大竖直圆柱面（轴心位于前半 y<0、且延伸至上半圈的最大 Cylinder），
         得到其轴心 (cx,cy) 与半径 R；
      2) 取顶面 (z≈z_top) 的前缘弧，据其相对 (cx,cy) 的角向范围确定环带扫过的角度；
      3) 以 (cx,cy) 为轴心构造同轴环形扇区（半径 R → R-band_width、高 z_bot→z_top），
         沿 z 拉伸成工具体 —— 严格贴合弯曲的前壁；
      4) 工具体按 x=0 分成左右，与型腔 Common 得左/右带，Cut 得型腔主体；
      5) 体积守恒校验：若 左带+右带+主体 与原始型腔体积差 >1%，则判定剖分失败，
         返回 None（绝不引入过切/漏切）。

    参数
    ----
    cavity      : TopoDS_Shape  去产品后的下模型腔实体
    band_width  : float         环带沿前壁法向进入材料的厚度（mm）；实测默认约 6
    band_height : float|None    环带自上而下的高度（mm）；None 时自动取 顶面-壁面分界(≈7)

    返回
    ----
    (main, bandL, bandR) 或 None
        main   : 切掉左右带后的型腔主体
        bandL  : x<0 侧的带
        bandR  : x>0 侧的带
    """
    try:
        bnd = Bnd_Box(); BRepBndLib.Add_s(cavity, bnd)
        x1, y1, z1, x2, y2, z2 = bnd.Get()
        z_top = z2

        # 1) 前壁大竖直圆柱
        best = None
        fe = TopExp_Explorer(cavity, TopAbs_FACE)
        while fe.More():
            f = fe.Current(); fe.Next()
            su = BRepAdaptor_Surface(TopoDS.Face_s(f))
            if su.GetType() != GeomAbs_Cylinder:
                continue
            fb = Bnd_Box(); BRepBndLib.Add_s(f, fb)
            fx1, fy1, fz1, fx2, fy2, fz2 = fb.Get()
            if fz1 < z_top - 11.0:          # 须延伸到上半圈
                continue
            axc = su.Cylinder().Axis().Location()
            if axc.Y() < 0:                 # 轴心在前半侧
                a = _area(f)
                if best is None or a > best[0]:
                    best = (a, su.Cylinder())
        if best is None:
            return None
        cyl = best[1]
        R = cyl.Radius()
        ax = cyl.Axis()
        cx, cy = ax.Location().X(), ax.Location().Y()
        if band_height is None:
            band_height = max(1.0, z_top - 5.52)
        Rin = R - band_width
        if Rin <= 0:
            return None
        z_bot = z_top - band_height
        if z_bot >= z_top:
            return None
        return _split_band_finish(cavity, best, (cx, cy, R), band_width, band_height,
                                  x1, y1, z1, x2, y2, z2, z_top)
    except Exception:
        return None


def _split_band_finish(cavity, best, axis, band_width, band_height,
                       x1, y1, z1, x2, y2, z2, z_top):
    """split_cavity_bands 后半段：取前缘弧 → 同轴扇区 → 剖分 → 守恒校验。"""
    try:
        cx, cy, R = axis
        Rin = R - band_width
        z_bot = z_top - band_height
        # 2) 顶面前缘弧，定角向范围
        top_face = None
        fe2 = TopExp_Explorer(cavity, TopAbs_FACE)
        while fe2.More():
            f = fe2.Current(); fe2.Next()
            su = BRepAdaptor_Surface(TopoDS.Face_s(f))
            if su.GetType() != GeomAbs_Plane:
                continue
            if abs(su.Plane().Axis().Direction().Z()) < 0.99:
                continue
            fb = Bnd_Box(); BRepBndLib.Add_s(f, fb)
            fx1, fy1, fz1, fx2, fy2, fz2 = fb.Get()
            if abs(fz2 - z_top) < 0.1 and _area(f) > 50000:
                top_face = f
                break
        if top_face is None:
            return None
        arc = None
        ee = TopExp_Explorer(top_face, TopAbs_EDGE)
        while ee.More():
            ed = ee.Current(); ee.Next()
            p = _edge_pts(ed)
            if len(p) < 3:
                continue
            xs = [q[0] for q in p]
            if (max(xs) - min(xs)) < (x2 - x1) * 0.5:   # 仅取横贯前缘的大弧
                continue
            cy0 = sum(q[1] for q in p) / len(p)
            if arc is None or cy0 < arc[0]:
                arc = (cy0, p)
        if arc is None:
            return None
        ref = -math.pi / 2.0      # 正前方向
        angs = []
        for q in arc[1]:
            a = math.atan2(q[1] - cy, q[0] - cx)
            angs.append(((a - ref + math.pi) % (2 * math.pi)) - math.pi + ref)
        a0, a1 = min(angs), max(angs)
        return _split_band_cut(cavity, (cx, cy, R, Rin, a0, a1), band_height,
                               x1, y1, z1, x2, y2, z2, z_top, z_bot)
    except Exception:
        return None


def _split_band_cut(cavity, seg, band_height, x1, y1, z1, x2, y2, z2, z_top, z_bot):
    """构造同轴环形扇区工具体 → 按 x=0 分左右 → Common/Cut → 体积守恒校验。"""
    try:
        cx, cy, R, Rin, a0, a1 = seg
        NA = 40
        outer, inner = [], []
        for k in range(NA + 1):
            ang = a0 + (a1 - a0) * k / NA
            ca, sa = math.cos(ang), math.sin(ang)
            outer.append((cx + R * ca, cy + R * sa))
            inner.append((cx + Rin * ca, cy + Rin * sa))
        loop_pts = outer + inner[::-1]
        poly = BRepBuilderAPI_MakePolygon()
        for P in loop_pts:
            poly.Add(gp_Pnt(P[0], P[1], z_bot))
        poly.Close()
        wire = BRepBuilderAPI_MakeWire(poly.Wire()).Wire()
        face = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(0, 0, z_bot), gp_Dir(0, 0, 1)), wire, True)
        if not face.IsDone():
            return None
        prism = BRepPrimAPI_MakePrism(face.Face(), gp_Vec(0, 0, z_top - z_bot))
        prism.Build()
        if not prism.IsDone():
            return None
        tool = prism.Shape()

        boxR = _make_axis_box(0.0, x2 + 500, y1 - 500, y2 + 500, z1 - 500, z2 + 500)
        boxL = _make_axis_box(x1 - 500, 0.0, y1 - 500, y2 + 500, z1 - 500, z2 + 500)
        if boxR is None or boxL is None:
            return None
        toolR = _common(tool, boxR, label="带右工具")
        toolL = _common(tool, boxL, label="带左工具")
        bandR = _common(cavity, toolR, label="右带")
        bandL = _common(cavity, toolL, label="左带")
        main = _cut(cavity, tool, label="型腔去带")

        if _solid_count(bandL) < 1 or _solid_count(bandR) < 1 or _solid_count(main) < 1:
            return None
        tot = _volume(bandL) + _volume(bandR) + _volume(main)
        if abs(tot - _volume(cavity)) > 0.01 * max(1.0, _volume(cavity)):
            return None
        return (main, bandL, bandR)
    except Exception:
        return None


def pick_protrusion(mold_solids, protrusion_index=None):
    """从剔除产品后的上模实体中挑出"凸起台"（非基座）。"""
    if protrusion_index is not None and 0 <= protrusion_index < len(mold_solids):
        return mold_solids[protrusion_index]
    # 默认：面数最多的非 6 面盒实体即凸起台
    best, best_faces = None, -1
    for s in mold_solids:
        nf = _count_faces(s)
        if nf > best_faces:
            best_faces = nf
            best = s
    return best


# =====================================================================
# 主流程编排
# =====================================================================
def refix(punch_path, cavity_path, output_dir="refix_output",
          punch_product_role=2, cavity_product_role=1,
          protrusion_index=None, corner_fill=False,
          cavity_corner_trim=True,
          sliver_area=40.0, wall_area=80.0, cluster_dist=12.0,
          z_cap_frac=0.65, max_corners=4, wedge_depth=None,
          radial_frac=0.7,
          split_bands=False, band_width=6.0, band_height=None):
    """再修理总入口。

    需求1：上下模都去掉产品结构体；
    需求2：上模凸起台左右角落"延伸求交"补齐（可选，默认关闭）；
    需求3：下模型腔左右角落"凸起"切除（默认开启），得到正常尖角。
    返回 报告 dict（含 punch/cavity 无产品输出与 punch_output/cavity_output 最终输出）。
    """
    os.makedirs(output_dir, exist_ok=True)
    report = {'punch': None, 'cavity': None,
              'punch_output': None, 'cavity_output': None,
              'corner_report': [], 'cavity_corner_report': []}

    # ---- 需求1：上下模去产品 ----
    print("=" * 68)
    print("[Refix] 需求1：去除产品结构体")
    print("=" * 68)
    punch_kept, punch_prod, punch_idx = remove_product(punch_path, punch_product_role)
    cavity_kept, cavity_prod, cavity_idx = remove_product(cavity_path, cavity_product_role)

    if punch_prod is not None and cavity_prod is not None:
        d = abs(_volume(punch_prod) - _volume(cavity_prod))
        print("[Refix] 上/下模产品体积差: %.3f mm³（应≈0）" % d)

    # 需求1输出（上模去产品后的 compound [基座, 凸起]）
    punch_no_prod = _make_compound(punch_kept)
    punch_out1 = os.path.join(output_dir, "PUNCH_NO_PRODUCT.stp")
    _export(punch_no_prod, punch_out1, as_compound=True)
    report['punch'] = punch_out1

    cavity_no_prod = _make_compound(cavity_kept)
    cavity_out1 = os.path.join(output_dir, "CAVITY_NO_PRODUCT.stp")
    _export(cavity_no_prod, cavity_out1, as_compound=True)
    report['cavity'] = cavity_out1

    # ---- 需求3 前置：识别上模凸起台（仅用于报告；角落切除体由需求3 对型腔自身生成） ----
    # 旧实现在此用"上模 loft 楔块 − 凸起台本体"构造互补切除体：但 _build_miter_loft 会沿
    # 长达上千 mm 的周界弧采样，生成约 1e11 mm³ 的爆炸性巨块，既拖慢流程（每次布尔数分钟）
    # 又因尺寸/位置失真导致后续切除过切或漏切。故移除该前置（punch_cut_solids 不再使用，
    # trim_cavity_corners 已统一走"柱体带封顶"的竖直三棱柱路径）。
    prot = pick_protrusion(punch_kept, protrusion_index)
    punch_cut_solids = []
    if prot is not None:
        print("[Refix] 凸起台: 体积=%.1f mm³ 面数=%d" % (_volume(prot), _count_faces(prot)))


    # ---- 需求4（可选）：把型腔沿前缘弧环带切分为 [主体, 左带, 右带] 三个结构体 ----
    # 提前于需求3 执行：需求3 的角落切除点(±304,z=5.52)正是环带两端，若先切会破坏前壁检测。
    bands_split = False
    band_info = None
    if split_bands:
        print("=" * 68)
        print("[Refix] 需求4：型腔前缘弧环带切分为独立结构体（左带/右带，不新增几何）")
        print("=" * 68)
        spl = split_cavity_bands(cavity_kept[0], band_width=band_width, band_height=band_height)
        if spl is None:
            print("[Refix/需求4] 环带切分失败（体积守恒未通过/几何异常），保持单体输出")
        else:
            main_c, bandL_c, bandR_c = spl
            print("[Refix/需求4] 切分成功: 主体=%.1f mm³  左带=%.1f mm³  右带=%.1f mm³"
                  % (_volume(main_c), _volume(bandL_c), _volume(bandR_c)))
            cavity_kept = [main_c, bandL_c, bandR_c]
            bands_split = True
            band_info = {'band_width': band_width,
                         'band_height': band_height,
                         'main_vol': _volume(main_c),
                         'bandL_vol': _volume(bandL_c),
                         'bandR_vol': _volume(bandR_c)}
    report['band_info'] = band_info

    # ---- 需求3：下模型腔角落凸起切除（默认开启）。
    # 需求4 成功时也执行：在切分后的主实体 cavity_kept[0] 上做角落切除，
    # 消除带端角落残留的"薄鳍"凸起（hull-prism 全覆盖鳍全高）。
    if cavity_corner_trim:
        print("=" * 68)
        print("[Refix] 需求3：下模型腔角落凸起切除（尖角化）")
        print("=" * 68)
        if not cavity_kept:
            print("[Refix] 下模无实体，跳过需求3")
        else:
            cav = cavity_kept[0]
            cav_fixed, cav_rep = trim_cavity_corners(
                cav, punch_cut_solids=punch_cut_solids,
                sliver_area=sliver_area, wall_area=wall_area,
                cluster_dist=cluster_dist, z_cap_frac=z_cap_frac,
                max_corners=max_corners, wedge_depth=wedge_depth,
                radial_frac=radial_frac)
            report['cavity_corner_report'] = cav_rep
            if cav_rep and any(c.get('ok') for c in cav_rep):
                cavity_kept = [cav_fixed] + cavity_kept[1:]
                print("[Refix/需求3] 型腔已切除 %d 处角落凸起"
                      % sum(1 for c in cav_rep if c.get('ok')))

    # ---- 需求2：上模凸起角落补齐 ----
    upper_kept = punch_kept
    if corner_fill:
        print("=" * 68)
        print("[Refix] 需求2：上模凸起台角落尖角自动补齐（延伸求交）")
        print("=" * 68)
        if prot is None:
            print("[Refix] 未找到凸起台，跳过需求2")
        else:
            prot_fixed, corner_report = fill_corners(
                prot, sliver_area=sliver_area, wall_area=wall_area,
                cluster_dist=cluster_dist, z_cap_frac=z_cap_frac,
                max_corners=max_corners, wedge_depth=wedge_depth,
                radial_frac=radial_frac)
            report['corner_report'] = corner_report
            upper_kept = [prot_fixed if (s is prot) else s for s in upper_kept]

    # 上模最终输出 = compound [基座, 修复后凸起]
    final_punch = _make_compound(upper_kept)
    punch_out2 = os.path.join(output_dir,
                              os.path.splitext(os.path.basename(punch_path))[0]
                              + "_REFIXED.stp")
    print("[Refix] 清理上模碎片面...")
    fc_before = _count_faces(final_punch)
    try:
        for i, s in enumerate(upper_kept):
            ns = _cleanup_shape(s)[0]
            if _solid_count(ns) == _solid_count(s) and abs(_volume(ns) - _volume(s)) <= 0.01 * max(1.0, _volume(s)):
                upper_kept[i] = ns
        final_punch = _make_compound(upper_kept)
    except Exception as e:
        print("[Refix] 上模清理跳过: %s" % e)
    print("[Refix] 上模面数 %d → %d" % (fc_before, _count_faces(final_punch)))
    _export(final_punch, punch_out2, as_compound=True)
    report['punch_output'] = punch_out2

    # 下模最终输出 = compound [型腔主体, 左带, 右带]（未启用需求4时即 [型腔]）
    final_cavity = _make_compound(cavity_kept)
    cavity_out2 = os.path.join(output_dir,
                               os.path.splitext(os.path.basename(cavity_path))[0]
                               + "_REFIXED.stp")
    print("[Refix] 清理下模碎片面...")
    fc_before = _count_faces(final_cavity)
    try:
        for i, s in enumerate(cavity_kept):
            ns = _cleanup_shape(s)[0]
            if _solid_count(ns) == _solid_count(s) and abs(_volume(ns) - _volume(s)) <= 0.01 * max(1.0, _volume(s)):
                cavity_kept[i] = ns
        final_cavity = _make_compound(cavity_kept)
    except Exception as e:
        print("[Refix] 下模清理跳过: %s" % e)
    print("[Refix] 下模面数 %d → %d" % (fc_before, _count_faces(final_cavity)))
    _export(final_cavity, cavity_out2, as_compound=True)
    report['cavity_output'] = cavity_out2

    print("=" * 68)
    print("[Refix] 完成。")
    print("  上模(需求1去产品): %s" % punch_out1)
    print("  下模(需求1去产品): %s" % cavity_out1)
    print("  上模(最终): %s" % punch_out2)
    print("  下模(最终): %s" % cavity_out2)
    print("  上模 solid 数=%d 下模 solid 数=%d"
          % (_solid_count(final_punch), _solid_count(final_cavity)))
    if band_info:
        print("  需求4: 型腔主体=%.1f  左带=%.1f  右带=%.1f mm³（带厚%.1f 高%s）"
              % (band_info['main_vol'], band_info['bandL_vol'], band_info['bandR_vol'],
                 band_info['band_width'], ("自动" if band_info['band_height'] is None
                                           else "%.1f" % band_info['band_height'])))

    _write_report(output_dir, report)
    return report


def _write_report(output_dir, report):
    lines = []
    lines.append("=" * 68)
    lines.append(" 模具再修理报告 (mold_refix)")
    lines.append("=" * 68)
    lines.append("时间: %s" % datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    lines.append("")
    lines.append("[需求1] 去除产品结构体")
    lines.append("  上模(去产品): %s" % report.get('punch'))
    lines.append("  下模(去产品): %s" % report.get('cavity'))
    lines.append("")
    lines.append("[需求2] 上模角落尖角补齐")
    cr = report.get('corner_report') or []
    if cr:
        for c in cr:
            if c.get('ok'):
                ap = c.get('apex')
                lines.append("  角落%d: OK  尖角=(%.2f,%.2f,%.2f) 楔块+%.1f mm³"
                             % (c['index'], ap[0], ap[1], ap[2], c.get('wedge_vol', 0)))
            else:
                lines.append("  角落%d: 失败 (%s)" % (c['index'], c.get('log', '')))
    else:
        lines.append("  （未检测到可补齐角落 / 需求2关闭）")
    lines.append("")
    lines.append("[需求3] 下模型腔角落凸起切除")
    cr3 = report.get('cavity_corner_report') or []
    if cr3:
        for c in cr3:
            if c.get('ok'):
                ap = c.get('apex')
                lines.append("  角落%d: OK  尖角=(%.2f,%.2f,%.2f) 切除-%.1f mm³"
                             % (c['index'], ap[0], ap[1], ap[2], c.get('removed_vol', 0)))
            else:
                lines.append("  角落%d: 失败 (%s)" % (c['index'], c.get('log', '')))
    else:
        lines.append("  （未检测到角落凸起）")
    lines.append("")
    lines.append("[输出]")
    lines.append("  上模: %s" % report.get('punch_output'))
    lines.append("  下模: %s" % report.get('cavity_output'))
    path = os.path.join(output_dir, "REFIX_report.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    jpath = os.path.join(output_dir, "REFIX_report.json")
    try:
        with open(jpath, "w", encoding="utf-8") as fh:
            json.dump({'corner_report': cr,
                       'cavity_corner_report': cr3,
                       'punch_output': report.get('punch_output'),
                       'cavity_output': report.get('cavity_output')},
                      fh, ensure_ascii=False, indent=2, default=str)
    except Exception as _e:
        print("[Refix] 操作失败(已继续): %r" % (_e,))
    return path


# =====================================================================
# 命令行入口
# =====================================================================
def _main(argv=None):
    p = argparse.ArgumentParser(
        prog='mold_refix.py',
        description='模具"再修理"：需求1 去除产品结构体；需求2 上模凸起角落尖角自动补齐（延伸求交）')
    p.add_argument('--punch', required=True, help='修理后的上模(凸模) STEP 文件')
    p.add_argument('--cavity', required=True, help='修理后的下模(凹模) STEP 文件')
    p.add_argument('--output-dir', default='refix_output', help='输出目录（默认 refix_output/）')
    p.add_argument('--punch-product-role', type=int, default=2,
                   help='上模中产品实体的排序下标（体积降序，默认 2=[基座,凸起,产品]）')
    p.add_argument('--cavity-product-role', type=int, default=1,
                   help='下模中产品实体的排序下标（体积降序，默认 1=[型腔,产品]）')
    p.add_argument('--protrusion-index', type=int, default=None,
                   help='上模凸起台实体的排序下标（默认自动：面数最多者）')
    p.add_argument('--corner-fill', action='store_true', help='启用需求2 上模角落尖角补齐（默认关闭，只做需求1去产品）')
    p.add_argument('--no-cavity-trim', action='store_true', help='关闭需求3 下模型腔角落凸起切除（默认开启）')
    p.add_argument('--sliver-area', type=float, default=40.0, help='细面面积阈值 mm²（默认40）')
    p.add_argument('--wall-area', type=float, default=80.0, help='弯曲大面面积阈值 mm²（默认80）')
    p.add_argument('--cluster-dist', type=float, default=12.0, help='角落细面聚类距离 mm（默认12）')
    p.add_argument('--z-cap-frac', type=float, default=0.65,
                   help='只处理 z<底+frac*高度 的细面（默认0.65）')
    p.add_argument('--max-corners', type=int, default=4, help='最多修复角落数（默认4）')
    p.add_argument('--wedge-depth', type=float, default=None, help='楔块拉伸厚度 mm（默认自动）')
    p.add_argument('--radial-frac', type=float, default=0.7,
                   help='外缘判定阈值：细面到 XY 中心距离 > frac*最大半径 才视为角落空缺（默认0.7）')
    p.add_argument('--split-bands', action='store_true',
                   help='需求4：把下模型腔沿前缘弧环带切分为 [主体,左带,右带] 三个结构体（默认关闭）')
    p.add_argument('--band-width', type=float, default=6.0,
                   help='环带厚度 mm（沿前壁法向进入材料，默认6）')
    p.add_argument('--band-height', type=float, default=None,
                   help='环带高度 mm（自顶面向下；默认自动取 顶面-壁面分界≈7）')
    args = p.parse_args(argv)

    refix(
        args.punch, args.cavity,
        output_dir=args.output_dir,
        punch_product_role=args.punch_product_role,
        cavity_product_role=args.cavity_product_role,
        protrusion_index=args.protrusion_index,
        corner_fill=args.corner_fill,
        cavity_corner_trim=not args.no_cavity_trim,
        sliver_area=args.sliver_area,
        wall_area=args.wall_area,
        cluster_dist=args.cluster_dist,
        z_cap_frac=args.z_cap_frac,
        max_corners=args.max_corners,
        wedge_depth=args.wedge_depth,
        radial_frac=args.radial_frac,
        split_bands=args.split_bands,
        band_width=args.band_width,
        band_height=args.band_height,
    )
    return 0


if __name__ == '__main__':
    sys.exit(_main())








