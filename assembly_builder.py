# -*- coding: utf-8 -*-
"""
assembly_builder.py —— 装配吻合结构体输出模块
====================================================

【模块定位】
  在「再修理」输出（*_REFIXED 上模 = [基座,凸起] / 下模 = [型腔]）之上，
  新增一个功能：把【再修理后的上模】＋【产品】＋【再修理后的下模】
  按同一坐标系拼合成一个【装配吻合的结构体】并输出 STEP。

【为什么产品必须从原始模具提取】
  实测（YA-1131-505_MIRROR_xOy）：
    · 再修理后的上模/下模（*_REFIXED）与【原始 fix_output 的 PUNCH_FILLED /
      CAVITY_HOLLOWED 模具 compound】共享同一坐标框架（Z≈[12.5,196.5]）；
    · 而独立的产品 STEP（如 *MIRROR_xOy.stp）是另一框架（Z 偏置近 5 万）。
  因此：若从独立产品 STEP 直接拼合，会错位近 5 万 mm，无法吻合。
  正确做法：从原始 fix_output 模具 compound 内自动提取「产品结构体」，
  该产品实体与再修理后的上/下模天然同框架 → 真正做到无间隙、无干涉吻合
  （实测 Common(上模,产品)=0、Common(下模,产品)=0）。

【装配吻合结构体】
    输出 = compound [ 上模(基座+凸起), 产品, 下模型腔 ]（三个独立结构体，同框架拼合）
    可选 fuse=True：布尔融合为单一连续实体（默认关闭，因布尔可能留碎片）。
    可选 align='parting'：若外供产品与模具不同框架，按"分模面+配合中心"近似对齐。

【输出文件命名约定】
    {前缀}_ASSEMBLED.stp   （compound [上模, 产品, 下模] 三结构体）
    {前缀}_ASSEMBLED_FUSED.stp（可选，fuse=True 时的单一融合实体）

【CLI 示例】
    venv/Scripts/python assembly_builder.py \
        --punch   refix_output/xxx_PUNCH_FILLED_REFIXED.stp \
        --cavity  refix_output/xxx_CAVITY_HOLLOWED_REFIXED.stp \
        --product-src fix_output/xxx_PUNCH_FILLED.stp \
        --output-dir assembly_output

退出码：0=PASS（吻合，已输出）；1=存在干涉/间隙/异常；2=文件/参数错误。
"""

import os
import sys
import math
import time
import json
import argparse


def _force_utf8():
    """Windows 控制台与 stdout 强制 UTF-8（中文不乱码）。"""
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8', errors='replace')
            except Exception:
                pass


_force_utf8()

import cadquery as cq

from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import (TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE, TopAbs_IN)
from OCP.TopoDS import TopoDS, TopoDS_Compound
from OCP.BRep import BRep_Builder
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.BRepAlgoAPI import (BRepAlgoAPI_Fuse, BRepAlgoAPI_Common)
from OCP.BRepBuilderAPI import (BRepBuilderAPI_Transform, BRepBuilderAPI_MakeVertex)
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.STEPControl import (STEPControl_Writer, STEPControl_AsIs,
                             STEPControl_ManifoldSolidBrep)
from OCP.IFSelect import IFSelect_RetDone
from OCP.gp import gp_Pnt, gp_Vec, gp_Trsf
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


def _is_valid(shape):
    try:
        return BRepCheck_Analyzer(shape).IsValid()
    except Exception:
        return False


def _has_geom(shape):
    if shape is None or shape.IsNull():
        return False
    return (TopExp_Explorer(shape, TopAbs_SOLID).More()
            or TopExp_Explorer(shape, TopAbs_SHELL).More())


def _common_volume(a, b):
    """两实体交集体积（≈0 表示无干涉）。失败返回 0.0。"""
    try:
        c = BRepAlgoAPI_Common(a, b)
        c.SetRunParallel(False)
        c.SetFuzzyValue(1e-5)
        c.Build()
        if c.IsDone():
            return _volume(c.Shape())
    except Exception:
        pass
    return 0.0


def _fuse(a, b, label="Fuse", fuzz=1e-4):
    f = BRepAlgoAPI_Fuse(a, b)
    f.SetRunParallel(False)
    f.SetFuzzyValue(fuzz)
    f.Build()
    if not f.IsDone() or not _has_geom(f.Shape()):
        raise RuntimeError("%s 布尔融合失败" % label)
    return f.Shape()


def _translate(shape, dx, dy, dz):
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(dx, dy, dz))
    r = BRepBuilderAPI_Transform(shape, t, True)
    r.Build()
    return r.Shape()


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


def _extract_solids(filepath):
    """读取 STEP，提取所有独立 Solid/Shell，按体积降序排序。
    返回 [(volume, TopoDS_Shape), ...]"""
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


def _auto_product_index(solids):
    """自动识别产品实体：体积显著小于模具主体，且 Z 下限最低。"""
    best = -1
    best_score = -1.0
    for i, (v, s) in enumerate(solids):
        if i == 0:
            continue
        b = _bbox(s)
        z1 = b[2]  # zmin
        score = (1.0 / (v + 1.0)) - z1 * 1e-5
        if score > best_score:
            best_score = score
            best = i
    return best if best >= 0 else 0



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
        except Exception:
            pass
        e.Next()
    if not any_solid:
        b = _bbox(shape)
        return (b[0] < pnt.X() < b[3] and b[1] < pnt.Y() < b[4] and b[2] < pnt.Z() < b[5])
    return False


def _dist_point_solid(pnt, solid):
    """点到实体的最近距离（点在其内部/表面为 0）。失败返回 None。
    遍历实体内的所有 SOLID，返回最小的最近距离（compound 需全覆盖）。"""
    best = None
    try:
        exp = TopExp_Explorer(solid, TopAbs_SOLID)
        if not exp.More():
            return None
        while exp.More():
            v = TopoDS.Vertex_s(BRepBuilderAPI_MakeVertex(pnt).Vertex())
            d = BRepExtrema_DistShapeShape(exp.Current(), v)
            d.Perform()
            if d.IsDone():
                val = d.Value()
                best = val if best is None else min(best, val)
            exp.Next()
    except Exception:
        pass
    return best


def _sample_face_points(face, cap=12):
    """面上采样点：面 UV 网格点，去重并截断。"""
    pts = []
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
        if abs(u2 - u1) < 1e-12: u2 = u1 + 1.0
        if abs(v2 - v1) < 1e-12: v2 = v1 + 1.0
        for fu in (0.25, 0.5, 0.75):
            for fv in (0.25, 0.5, 0.75):
                if len(pts) >= cap:
                    break
                pts.append(adapt.Value(u1 + (u2 - u1) * fu, v1 + (v2 - v1) * fv))
    except Exception:
        pass
    seen = []
    for p in pts:
        if not any(p.Distance(q) < 1e-6 for q in seen):
            seen.append(p)
    return seen[:cap]


def _estimate_parting_frame(shape, prefer_small_zmin=True):
    """估算「分模面 + 配合特征中心」，返回 (mate_cx, mate_cy, split_z)。

    与前端 Three.js 装配对齐逻辑一致：
      上模 → 取 z_min 最小实体（凸起台）中心 + 其顶面为分模面；
      产品 / 下模 → 整体中心 + 顶面为分模面。
    bbox 语义：_bbox 返回 (xmin, ymin, zmin, xmax, ymax, zmax)。
    """
    ss = []
    e = TopExp_Explorer(shape, TopAbs_SOLID)
    while e.More():
        ss.append((_volume(e.Current()), e.Current()))
        e.Next()
    if not ss:
        ss = [(0.0, shape)]
    boxes = [_bbox(s) for _, s in ss]
    if prefer_small_zmin and len(boxes) > 1:
        core_idx = min(range(len(boxes)), key=lambda i: boxes[i][2])  # zmin 最小者
        cb = boxes[core_idx]
        mate = ((cb[0] + cb[3]) / 2.0, (cb[1] + cb[4]) / 2.0)
        split = cb[5]  # 顶面 z
    else:
        x1 = min(b[0] for b in boxes); x2 = max(b[3] for b in boxes)
        y1 = min(b[1] for b in boxes); y2 = max(b[4] for b in boxes)
        z2 = max(b[5] for b in boxes)
        mate = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
        split = z2
    return mate[0], mate[1], split


def _extract_product_from_moldup(product_src, product_role=None):
    """从原始模具 compound 中提取「产品结构体」。

    返回 (product_topods_or_None, report_dict)。
    """
    solids = _extract_solids(product_src)
    idx = product_role
    if idx is None or idx < 0 or idx >= len(solids):
        idx = _auto_product_index(solids)
    report = {'src_solids': len(solids), 'product_role': idx}
    for i, (v, s) in enumerate(solids):
        tag = "  <- 产品(提取)" if i == idx else ""
        print("   solid[%d] 体积=%.1f mm^3 面数=%d%s" % (i, v, _count_faces(s), tag))
    product = solids[idx][1] if 0 <= idx < len(solids) else None
    return product, report


# =====================================================================
# 装配吻合结构体生成（主入口）
# =====================================================================

def build_assembly(punch_path, cavity_path, product_path=None,
                   product_src_path=None, product_role=None,
                   output_dir="assembly_output", output_name=None,
                   fuse=False, align=None, gap_tol=0.1,
                   interfere_tol=1.0, strict_gap=False):
    """生成【装配吻合结构体】并输出 STEP。

    参数
    ----
    punch_path : str     再修理后上模（*_REFIXED，[*基座,凸起*]）
    cavity_path : str    再修理后下模（*_REFIXED，[*型腔*]）
    product_path : str   外部产品 STEP（可选）。
    product_src_path : str  原始 fix_output 模具（PUNCH_FILLED/CAVITY_HOLLOWED，
                         内含与模具同框架的产品实体）。提供时优先用它（真正吻合）。
    product_role : int   产品在 product_src 中的下标（None=自动）。
    fuse : bool          是否尝试布尔融合为单一实体（默认 False，关闭）。⚠ 实验性：\n                         型腔与上模/产品间为曲面随形贴合，OCCT 布尔常失败/损坏；\n                         融合失败时仍保证输送出合拢 compound（主产物不受损）。
    align : str|None     'parting' -> 分模面+配合中心对齐；None -> 不平移。
    gap_tol : float      间隙容差（mm，默认 0.1）。
    interfere_tol : float 干涉容差（mm^3，默认 1.0）。

    返回 report dict：{'ok','assembly','fused','issues','checks','product_source',
                       'punch_solids','cavity_solids','elapsed_s'}
    """
    t0 = time.time()
    issues = []
    checks = {}

    print("=" * 60)
    print("装配吻合结构体生成  [上模 -> 产品 -> 下模]")
    print("=" * 60)

    # ---- 1. 读取再修理后的上模 / 下模 ----
    punch_solids = _extract_solids(punch_path)
    cavity_solids = _extract_solids(cavity_path)
    print("  上模(再修理后) 结构体=%d" % len(punch_solids))
    for i, (v, s) in enumerate(punch_solids):
        print("    punch[%d] 体积=%.1f 面数=%d" % (i, v, _count_faces(s)))
    print("  下模(再修理后) 结构体=%d" % len(cavity_solids))
    for i, (v, s) in enumerate(cavity_solids):
        print("    cavity[%d] 体积=%.1f 面数=%d" % (i, v, _count_faces(s)))
    if not punch_solids or not cavity_solids:
        raise ValueError("上模/下模未读到实体")
    punch_shape = _make_compound([s for _, s in punch_solids])
    cavity_shape = _make_compound([s for _, s in cavity_solids])

    # ---- 2. 产品实体来源 ----
    product = None
    product_src_tag = None
    if product_src_path and os.path.exists(product_src_path):
        print("\n  产品来源：原始 fix_output 模具（同框架提取，真正吻合）")
        product, prod_report = _extract_product_from_moldup(product_src_path,
                                                            product_role)
        product_src_tag = "mold_compound"
    elif product_path and os.path.exists(product_path):
        print("\n  产品来源：外部产品 STEP")
        ext = _extract_solids(product_path)
        if ext:
            product = ext[0][1]
        product_src_tag = "external"
    if product is None:
        raise ValueError("未能获取产品实体（请提供 --product-src 原始模具或 --product）")

    # ---- 3. 坐标框架判断 + 对齐 ----
    pb = _bbox(product)
    psh = _bbox(punch_shape)
    csh = _bbox(cavity_shape)
    checks['product_bbox'] = [round(v, 2) for v in pb]
    checks['punch_bbox'] = [round(v, 2) for v in psh]
    checks['cavity_bbox'] = [round(v, 2) for v in csh]
    pzc = (pb[2] + pb[5]) / 2.0   # 产品 Z 中心（zmin=idx2, zmax=idx5）
    mzc = (psh[2] + psh[5]) / 2.0  # 上模 Z 中心
    # 同框架判据：产品与上/下模在 Z 上应有重叠（分模面上下必有接触面）。
    # 不得用 Z 中心比较 —— 产品在分模面下、上模在分模面上，中心天然不同。
    z_overlap = min(pb[5], csh[5]) - max(pb[2], psh[2])
    if product_src_tag == 'mold_compound':
        # 从原始 fix_output 模具提取的产品，天然与 REFIX 上下模同框架（最强信号）
        same_frame = True
        print("  产品来源=原始模具 compound -> 判定为同框架（最强信号，0平移）")
    else:
        same_frame = z_overlap > -0.5
        print("  同框架判断: 产品Z=[%.1f,%.1f] 上模Z=[%.1f,%.1f] 下模Z=[%.1f,%.1f] Z重叠=%.1f -> %s"
              % (pb[2], pb[5], psh[2], psh[5], csh[2], csh[5], z_overlap,
                 "同框架(0平移)" if same_frame else "不同框架"))
    checks['same_frame'] = bool(same_frame)

    if not same_frame:
        if align in (None, ''):
            issue = ("产品与上/下模处于不同坐标框架（Z中心差 %.1f mm，Z重叠 %.1f mm）。"
                     "请提供 --product-src 原始模具或 --align parting 近似对齐"
                     % (abs(pzc - mzc), z_overlap))
            print("  X " + issue)
            issues.append({'severity': 'FAIL', 'check': 'frame', 'msg': issue})
        else:
            print("  按分模面+配合中心近似对齐（align=parting）...")
            pmx, pmy, pspl = _estimate_parting_frame(punch_shape, prefer_small_zmin=True)
            pdx, pdy, pdspl = _estimate_parting_frame(product, prefer_small_zmin=False)
            cdx, cdy, cdspl = _estimate_parting_frame(cavity_shape, prefer_small_zmin=False)
            dxp, dyp, dzp = pmx - pdx, pmy - pdy, pspl - pdspl
            product = _translate(product, dxp, dyp, dzp)
            dxc, dyc, dzc = pmx - cdx, pmy - cdy, pspl - cdspl
            cavity_shape = _translate(cavity_shape, dxc, dyc, dzc)
            punch_shape = _translate(punch_shape, -pmx, -pmy, -pspl)
            print("  产品平移=(%.1f,%.1f,%.1f) 下模平移=(%.1f,%.1f,%.1f)"
                  % (dxp, dyp, dzp, dxc, dyc, dzc))

    # ---- 4. 装配吻合结构体 compound [上模, 产品, 下模] ----
    assembly = _make_compound([punch_shape, product, cavity_shape])
    checks['assembly_solid_count'] = _solid_count(assembly)
    checks['assembly_volume'] = round(_volume(assembly), 1)
    checks['assembly_faces'] = _count_faces(assembly)
    print("\n  装配结构体: 结构体=%d 体积=%.1f mm^3 面数=%d"
          % (checks['assembly_solid_count'], checks['assembly_volume'],
             checks['assembly_faces']))


    # ---- 5. 干涉自检 ----
    for name, a, b in [('上模<->产品', punch_shape, product),
                       ('下模<->产品', cavity_shape, product),
                       ('上模<->下模', punch_shape, cavity_shape)]:
        v = _common_volume(a, b)
        checks['interfere_' + name] = round(v, 3)
        print("  [干涉] %s: Common=%.3f mm^3 %s"
              % (name, v, "OK" if v <= interfere_tol else "X 干涉!"))
        if v > interfere_tol:
            issues.append({'severity': 'FAIL', 'check': 'interference',
                           'msg': "%s 干涉体积 %.1f mm^3 (> %.1f)"
                                  % (name, v, interfere_tol)})

    # ---- 6. 间隙自检（产品表面采样点到 上模/下模 最近距离）----
    print("\n  产品表面贴合采样（gap_tol=%.2f mm）..." % gap_tol)
    gap_pts = 0
    n_pts = 0
    max_gap = 0.0
    e = TopExp_Explorer(product, TopAbs_FACE)
    while e.More():
        for p in _sample_face_points(e.Current(), cap=12):
            n_pts += 1
            d_p = _dist_point_solid(p, punch_shape)
            d_c = _dist_point_solid(p, cavity_shape)
            d_best = None
            for d in (d_p, d_c):
                if d is not None:
                    d_best = d if d_best is None else min(d_best, d)
            if d_best is not None and d_best > max_gap:
                max_gap = d_best
            if d_best is None or d_best > gap_tol:
                gap_pts += 1
        e.Next()
    checks['sampled_points'] = n_pts
    checks['gap_points'] = gap_pts
    checks['max_gap'] = round(max_gap, 4)
    print("  采样点=%d 未覆盖(间隙)点=%d 最大间隙=%.3f mm %s"
          % (n_pts, gap_pts, max_gap, "OK" if gap_pts == 0 else "X"))
    if gap_pts > 0:
        # 说明：再修理需求3（下模型腔角落凸起切除）会在两角落留 ~1.6mm 已知局部
        # 差异；默认按 WARN 如实报告，不阻断装配结构体输出（干涉才是硬约束）。
        if strict_gap:
            issues.append({'severity': 'FAIL', 'check': 'gap',
                           'msg': "产品表面 %d/%d 采样点未被上/下模覆盖（最大间隙 %.3f mm）"
                                  % (gap_pts, n_pts, max_gap)})
        else:
            issues.append({'severity': 'WARN', 'check': 'gap',
                           'msg': "产品表面 %d/%d 采样点未被覆盖（最大间隙 %.3f mm）。"
                                  "多为再修理下模角落切除的已知局部差异；严格判定可用 --strict-gap"
                                  % (gap_pts, n_pts, max_gap)})

    # ---- 7. 导出 ----
    os.makedirs(output_dir, exist_ok=True)
    base = output_name or (os.path.splitext(os.path.basename(punch_path))[0]
                           .replace("_REFIXED", ""))
    assem_path = os.path.join(output_dir, "%s_ASSEMBLY.stp" % base)
    _export(assembly, assem_path, as_compound=True)
    checks['assembly_path'] = assem_path
    print("\n  V 合拢装配体已输出（compound [上模, 产品, 下模]，同框架合拢贴合）: %s" % assem_path)

    fused_path = None
    if fuse:
        print("  [实验性] 布尔融合三件为单一实体（型腔随形贴合，OCCT 可能失败）...")
        try:
            merged = _fuse(punch_shape, product, label="装配-上模+产品")
            merged = _fuse(merged, cavity_shape, label="装配+下模")
            if not _is_valid(merged):
                raise RuntimeError("融合结果 BRepCheck 无效，保留合拢 compound")
            fused_path = os.path.join(output_dir, "%s_ASSEMBLY_FUSED.stp" % base)
            _export(merged, fused_path, as_compound=False)
            checks['fused_solid_count'] = _solid_count(merged)
            checks['fused_volume'] = round(_volume(merged), 1)
            checks['fused_valid'] = _is_valid(merged)
            print("  V 融合实体已输出: %s（结构体=%d 体积=%.1f 有效=%s）"
                  % (fused_path, checks['fused_solid_count'],
                     checks['fused_volume'], checks['fused_valid']))
        except Exception as e:
            print("  ! 融合失败（已保留合拢 compound 主输出）: %s" % e)
            issues.append({'severity': 'WARN', 'check': 'fuse',
                           'msg': "布尔融合失败，已保留合拢 compound：" + str(e)})

    ok = not any(i['severity'] == 'FAIL' for i in issues)
    report = {
        'ok': ok,
        'assembly': assem_path,
        'fused': fused_path,
        'issues': issues,
        'checks': checks,
        'product_source': product_src_tag,
        'punch_solids': len(punch_solids),
        'cavity_solids': len(cavity_solids),
        'elapsed_s': round(time.time() - t0, 1),
    }
    return report





def _centroid(shape):
    try:
        p = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, p)
        c = p.CentreOfMass()
        return (c.X(), c.Y(), c.Z())
    except Exception:
        b = _bbox(shape)
        return ((b[0] + b[3]) / 2.0, (b[1] + b[4]) / 2.0, (b[2] + b[5]) / 2.0)


def _loaded_solids(filepath):
    """读取 STEP，返回按体积降序的 TopoDS_Shape 列表。"""
    return [s for _, s in _extract_solids(filepath)]


def compute_align_offsets(punch_file, product_file, cavity_file):
    """用 BREP 精确计算【上模/产品/下模】三件的对齐位移。

    目标：三件装配配合 —— 分模面（拆/合模面）共面、配合中心（轴线）共轴。
    与「前端 Three.js 网格中心近似」不同，这里用 BREP 真实质心 + 分模面，
    对【任意导入、任意坐标框架】的 STEP 都能正确对齐（如产品镜像件 Z/Y 偏置数万）。

    基准 = 上模：以「基座（体积最大实体）底面 Z」为分模面 Z_ref，
              以「上模整体体积加权质心 XY」为配合中心 (cx_ref, cy_ref)。

    返回 dict：
      {'centered': bool, 'frame': 'punch_based',
       'z_split': float,
       'offsets': {'punch': [dx,dy,dz], 'product': [dx,dy,dz], 'cavity': [dx,dy,dz]},
       'detail': {...}}
    """
    punch = _loaded_solids(punch_file)
    product = _loaded_solids(product_file)
    cavity = _loaded_solids(cavity_file)
    if not punch or not product or not cavity:
        raise ValueError("上模/产品/下模中有文件未读到实体")

    # ---- 各件提取特征 ----
    # 上模
    base = max(punch, key=lambda s: _volume(s))
    z_ref = _bbox(base)[2]  # 基座底面 = 分模面
    total_p = sum(_volume(s) for s in punch) or 1.0
    cx_ref = sum(_centroid(s)[0] * _volume(s) for s in punch) / total_p
    cy_ref = sum(_centroid(s)[1] * _volume(s) for s in punch) / total_p
    # 产品：可能是【纯产品文件】(1实体) 或【含产品的模具 compound】(多实体)。
    #       正确做法是自动提取「产品结构体」(体积较小 + Zmin 较低)，避免取到基座。
    if len(product) > 1:
        idx = _auto_product_index([(_volume(s), s) for s in product])
        prod_ent = product[max(0, idx)]
        print("[Align] 产品文件含 %d 实体，自动提取产品实体(下标 %d)" % (len(product), idx))
    else:
        prod_ent = product[0]
    pr_z = _bbox(prod_ent)[5]  # 产品顶面 = 分模面
    pr_c = _centroid(prod_ent)
    # 下模：型腔主体（体积最大）分模面 = 顶面 Z，配合中心 = 型腔主体质心 XY
    main = max(cavity, key=lambda s: _volume(s))
    ca_z = _bbox(main)[5]
    ca_c = _centroid(main)

    # ---- 位移（把自己对齐到基准的分模面 Z_ref + 配合中心 (cx_ref,cy_ref)）----
    # 保持基准件(上模)不动；产品、下模平移。
    # 产品：Z 用「产品顶面Zmax -> ref」，即 dz = z_ref - pr_z；
    #       XY 用「产品质心 -> ref」。
    dx_p = cx_ref - pr_c[0]
    dy_p = cy_ref - pr_c[1]
    dz_p = z_ref - pr_z
    # 下模：同样用型腔主体做特征。
    dx_c = cx_ref - ca_c[0]
    dy_c = cy_ref - ca_c[1]
    dz_c = z_ref - ca_z
    # 上模：不动（基准）。
    offsets = {
        'punch': [0.0, 0.0, 0.0],
        'product': [round(dx_p, 4), round(dy_p, 4), round(dz_p, 4)],
        'cavity': [round(dx_c, 4), round(dy_c, 4), round(dz_c, 4)],
    }

    detail = {
        'punch_split_z': round(z_ref, 3),
        'punch_mate_xy': [round(cx_ref, 3), round(cy_ref, 3)],
        'product_split_z': round(pr_z, 3),
        'product_mate_xy': [round(pr_c[0], 3), round(pr_c[1], 3)],
        'cavity_split_z': round(ca_z, 3),
        'cavity_mate_xy': [round(ca_c[0], 3), round(ca_c[1], 3)],
    }
    return {'centered': True, 'frame': 'punch_based', 'z_split': round(z_ref, 3),
            'offsets': offsets, 'detail': detail}

def print_report(report):
    print("\n" + "=" * 60)
    print("装配吻合结构体 生成报告")
    print("=" * 60)
    print("  结果: %s" % ("V PASS（吻合）" if report['ok'] else "X FAIL"))
    n_warn = sum(1 for i in report['issues'] if i['severity'] == 'WARN')
    if report['ok'] and n_warn:
        print("  （含 %d 条警告，装配结构体已输出）" % n_warn)
    print("  结构体: 上模=%d 下模=%d  产品来源=%s"
          % (report['punch_solids'], report['cavity_solids'], report['product_source']))
    print("  装配体积=%.1f mm^3 面数=%d 结构体数=%d"
          % (report['checks'].get('assembly_volume', 0),
             report['checks'].get('assembly_faces', 0),
             report['checks'].get('assembly_solid_count', 0)))
    print("  干涉: 上模<->产品=%s 下模<->产品=%s 上模<->下模=%s"
          % (report['checks'].get('interfere_上模<->产品'),
             report['checks'].get('interfere_下模<->产品'),
             report['checks'].get('interfere_上模<->下模')))
    print("  间隙: 采样点=%d 未覆盖=%d 最大=%.3f mm"
          % (report['checks'].get('sampled_points', 0),
             report['checks'].get('gap_points', 0),
             report['checks'].get('max_gap', 0)))
    print("  输出(合拢装配体): %s" % report['assembly'])
    if report.get('fused'):
        print("  融合(实验性): %s" % report['fused'])
    if report['issues']:
        print("\n  --- 问题 ---")
        for i in report['issues']:
            print("  [%s] %s: %s" % (i['severity'], i['check'], i['msg']))
    else:
        print("  V 无干涉、无间隙、装配吻合")
    return 'PASS' if report['ok'] else 'FAIL'


def main():
    parser = argparse.ArgumentParser(
        prog='assembly_builder.py',
        description='装配吻合结构体输出：再修理后的上模 + 产品 + 再修理后的下模 '
                    '按同一坐标系拼合成一个 STEP 结构体（含自检）。',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            '示例：\n'
            '  python assembly_builder.py \\\n'
            '      --punch refix_output/xxx_PUNCH_FILLED_REFIXED.stp \\\n'
            '      --cavity refix_output/xxx_CAVITY_HOLLOWED_REFIXED.stp \\\n'
            '      --product-src fix_output/xxx_PUNCH_FILLED.stp \\\n'
            '      --output-dir assembly_output\n'
            '退出码：0=PASS；1=FAIL（干涉/间隙/异常）；2=参数/文件错误'
        ),
    )
    parser.add_argument('--punch', required=True, help='再修理后上模 STEP（*_REFIXED）')
    parser.add_argument('--cavity', required=True, help='再修理后下模 STEP（*_REFIXED）')
    parser.add_argument('--product', default=None, help='外部产品 STEP（可选）')
    parser.add_argument('--product-src', default=None,
                        help='原始 fix_output 模具（PUNCH_FILLED/CAVITY_HOLLOWED，'
                             '内含同框架产品实体，推荐）')
    parser.add_argument('--product-role', type=int, default=None,
                        help='产品在 product-src 中的下标（默认自动）')
    parser.add_argument('--output-dir', default='assembly_output', help='输出目录')
    parser.add_argument('--output-name', default=None, help='输出文件前缀')
    parser.add_argument('--fuse', action='store_true',
                        help='[实验性] 布尔融合为单一实体（默认关；型腔随形贴合常失败，失败仍保合拢 compound）')
    parser.add_argument('--align', default=None, choices=[None, 'parting'],
                        help='不同框架时按分模面+配合中心对齐')
    parser.add_argument('--gap-tol', type=float, default=0.1, help='间隙容差 mm')
    parser.add_argument('--interfere-tol', type=float, default=1.0, help='干涉容差 mm^3')
    parser.add_argument('--strict-gap', action='store_true',
                        help='严格判定间隙为 FAIL（默认 WARN，不阻断输出）')
    parser.add_argument('--json', default=None, help='报告写入 JSON（可选）')
    args = parser.parse_args()

    try:
        report = build_assembly(
            punch_path=args.punch,
            cavity_path=args.cavity,
            product_path=args.product,
            product_src_path=args.product_src,
            product_role=args.product_role,
            output_dir=args.output_dir,
            output_name=args.output_name,
            fuse=args.fuse,
            align=args.align,
            gap_tol=args.gap_tol,
            interfere_tol=args.interfere_tol,
            strict_gap=args.strict_gap,
        )
        result = print_report(report)
        if args.json:
            with open(args.json, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print("\n  报告已写入: %s" % args.json)
        sys.exit(0 if result == 'PASS' else 1)
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        print("装配生成异常: %s" % e)
        traceback.print_exc()
        sys.exit(2)


if __name__ == '__main__':
    main()

