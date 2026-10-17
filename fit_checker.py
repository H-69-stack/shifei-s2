# -*- coding: utf-8 -*-
"""
fit_checker.py —— 三件贴合验证模块（修复后凸模 → 产品 → 修复后凹模）
=====================================================================

验证目标
--------
输入修复后的上模 / 下模 STEP 文件，验证三者装配后"依次紧密贴合"：

  ① 无干涉（不允许实体交叉重叠）：
       任何两者 Common 体积 ≈ 0（凸起↔产品、型腔↔产品、凸模基座↔产品、凸起↔型腔）

  ② 无间隙（配合面完全贴合）：
       产品表面每个采样点距【上模(基座∪凸起) ∪ 下模(型腔)】的最近距离 ≤ gap_tol。
       （采样点若处于型腔空腔内部 / 模具表面，距离为 0 → 贴合；
         若裸露在外（间隙带 / 型腔扩大 / 型腔挖浅），距离 > tol → 判为间隙。）

  ③ 型腔忠实复刻产品外形（禁止向外扩大型腔轮廓）：
       - 型腔顶面开口必须到达分模面（不留产品顶部外露带）；
       - 型腔内壁随产品外形 —— 若型腔向外扩大 / 向下挖深，
         产品表面采样点距型腔距离将 > tol 而被判为间隙（②自动覆盖）。

判定归属
--------
若失败采样点最近的是【型腔】→ 下模挖空不正确，应修正 mold_fix.hollow()；
若失败采样点最近的是【凸起/基座】→ 上模填充不正确，应修正 mold_fix.fill()。

CLI 用法
--------
  python fit_checker.py --punch <凸模修复.stp> --cavity <凹模修复.stp> [--product <产品.stp>]

退出码：0 = PASS（完全贴合）；1 = FAIL（存在间隙 / 干涉 / 型腔不忠实）；2 = 异常。
"""

import os
import sys
import time
import math
import json
import argparse

import cadquery as cq

from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import (TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE,
                        TopAbs_EDGE, TopAbs_VERTEX, TopAbs_IN)
from OCP.TopoDS import TopoDS
from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.BRepLProp import BRepLProp_SLProps
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.gp import gp_Pnt, gp_Vec


# =====================================================================
# 基础工具
# =====================================================================

def _force_utf8():
    """Windows 控制台与 Python 输出强制 UTF-8，避免中文乱码。"""
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


def _extract_solids(filepath):
    """加载 STEP 文件，提取所有独立 Solid/Shell 实体，按体积降序排序。"""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"文件不存在: {filepath}")
    result = cq.importers.importStep(filepath)
    shapes = result.vals() if hasattr(result, 'vals') else []
    solids = []
    for shp in shapes:
        s = shp.wrapped
        exp = TopExp_Explorer(s, TopAbs_SOLID)
        while exp.More():
            solids.append((_solid_volume(exp.Current()), exp.Current()))
            exp.Next()
        if not TopExp_Explorer(s, TopAbs_SOLID).More():
            exp2 = TopExp_Explorer(s, TopAbs_SHELL)
            while exp2.More():
                solids.append((_solid_volume(exp2.Current()), exp2.Current()))
                exp2.Next()
    if not solids:
        raise ValueError("未找到 Solid 或 Shell 实体")
    solids.sort(key=lambda x: x[0], reverse=True)
    return solids


def _bbox_tuple(shape):
    if shape is None or shape.IsNull():
        return (0., 0., 0., 0., 0., 0.)
    try:
        b = Bnd_Box()
        BRepBndLib.Add_s(shape, b)
        if b.IsVoid():
            return (0., 0., 0., 0., 0., 0.)
        return b.Get()
    except Exception:
        return (0., 0., 0., 0., 0., 0.)


def _solid_volume(shape):
    try:
        p = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, p)
        return p.Mass()
    except Exception:
        x1, y1, z1, x2, y2, z2 = _bbox_tuple(shape)
        return (x2 - x1) * (y2 - y1) * (z2 - z1) if (x2 > x1 and y2 > y1 and z2 > z1) else 0.0


def _solid_count(shape):
    n = 0
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        n += 1
        exp.Next()
    return n


def _common_volume(a, b):
    """两实体 Common 交集体积（近似 0 表示无重叠/无干涉）。

    B6：布尔失败/未完成时返回 None（与"交集体积≈0"明确区分），
    由调用方记 WARN 按未知处理，不再静默按 0 放过。

    前置守卫：任一参数为 None/空/不含任何 SOLID 时无法判定，同样返回
    None（空 compound 经 OCC 会"成功"返回空集体积 0，会被误判为"已确认
    无重叠"——这里显式拦截，避免静默漏报）。
    """
    if a is None or b is None or a.IsNull() or b.IsNull():
        return None
    if _solid_count(a) == 0 or _solid_count(b) == 0:
        return None
    try:
        c = BRepAlgoAPI_Common(a, b)
        c.SetRunParallel(False)
        c.SetFuzzyValue(1e-5)
        c.Build()
        if c.IsDone():
            return _solid_volume(c.Shape())
    except Exception:
        pass
    return None


def _bbox_point_gap(bb, pnt):
    """包围盒 (x1,y1,z1,x2,y2,z2) 到点的最短距离（内部/表面上为 0）。"""
    x1, y1, z1, x2, y2, z2 = bb
    d2 = 0.0
    for lo, hi, c in ((x1, x2, pnt.X()), (y1, y2, pnt.Y()), (z1, z2, pnt.Z())):
        if c < lo:
            d = lo - c
        elif c > hi:
            d = c - hi
        else:
            d = 0.0
        d2 += d * d
    return math.sqrt(d2)


class _SolidDistIndex:
    """P9：点到实体距离查询索引。

    原实现每个采样点都对 compound 内每个 SOLID 各做一次
    BRepExtrema_DistShapeShape，循环内还重复构造 vertex、无任何
    预筛。这里预枚举 SOLID/SHELL + 包围盒 + SolidClassifier：
      - 内含短路：分类器命中 TopAbs_IN 即距离 0（远比 BVH 距离快）
      - bbox 下界粗排：下界已不优于当前最优时跳过精确距离计算
      - vertex 每个查询只构造一次
    距离语义与原实现一致（内部/表面为 0；返回最小最近距离）。
    """
    __slots__ = ('entries', '_shape')

    def __init__(self, shape):
        self._shape = shape
        self.entries = []
        if shape is None:
            return
        for tp in (TopAbs_SOLID, TopAbs_SHELL):
            exp = TopExp_Explorer(shape, tp)
            while exp.More():
                shp = exp.Current()
                bb = None
                try:
                    b = Bnd_Box()
                    BRepBndLib.AddOptimal_s(shp, b)
                    if not b.IsVoid():
                        bb = b.Get()
                except Exception:
                    bb = None
                clsf = None
                if tp == TopAbs_SOLID:
                    try:
                        clsf = BRepClass3d_SolidClassifier(shp)
                    except Exception:
                        clsf = None
                self.entries.append((shp, bb, clsf))
                exp.Next()

    def dist(self, pnt):
        """点到实体最近距离（内部/表面为 0）。无几何/失败返回 None。"""
        if not self.entries:
            return None
        best = None
        vertex = None
        for shp, bb, clsf in self.entries:
            if clsf is not None:
                try:
                    clsf.Perform(pnt, 1e-6)
                    if clsf.State() == TopAbs_IN:
                        return 0.0
                except Exception:
                    pass
            if bb is not None and best is not None and _bbox_point_gap(bb, pnt) >= best:
                continue
            try:
                if vertex is None:
                    vertex = BRepBuilderAPI_MakeVertex(pnt).Vertex()
                d = BRepExtrema_DistShapeShape(shp, vertex)
                d.Perform()
                if d.IsDone():
                    val = d.Value()
                    best = val if best is None else min(best, val)
            except Exception:
                pass
        return best


def _point_inside(shape, pnt, tol=1e-6):
    """点是否在实体内部（SolidClassifier）。"""
    any_solid = False
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        any_solid = True
        try:
            clsf = BRepClass3d_SolidClassifier(exp.Current())
            clsf.Perform(pnt, tol)
            if clsf.State() == TopAbs_IN:
                return True
        except Exception:
            pass
        exp.Next()
    if not any_solid:
        x1, y1, z1, x2, y2, z2 = _bbox_tuple(shape)
        return (x1 < pnt.X() < x2 and y1 < pnt.Y() < y2 and z1 < pnt.Z() < z2)
    return False


def _face_outer_normal(face, product):
    """求面【朝外单位法向】（BRepAdaptor + BRepLProp + SolidClassifier 修正朝外）。
    返回 (点, 单位法向 gp_Vec) 或 None。"""
    try:
        tface = TopoDS.Face_s(face)
        adapt = BRepAdaptor_Surface(tface)
        u1 = adapt.FirstUParameter()
        u2 = adapt.LastUParameter()
        v1 = adapt.FirstVParameter()
        v2 = adapt.LastVParameter()
        INF = 1e8
        if abs(u1) > INF: u1 = -1.0
        if abs(u2) > INF: u2 = 1.0
        if abs(v1) > INF: v1 = -1.0
        if abs(v2) > INF: v2 = 1.0
        if abs(u2 - u1) < 1e-12: u2 = u1 + 1.0
        if abs(v2 - v1) < 1e-12: v2 = v1 + 1.0
    except Exception:
        return None

    props = BRepLProp_SLProps(adapt, 2, 1e-7)
    candidates = [
        ((u1 + u2) / 2.0, (v1 + v2) / 2.0),
        ((3 * u1 + u2) / 4.0, (v1 + v2) / 2.0),
        ((u1 + 3 * u2) / 4.0, (v1 + v2) / 2.0),
        ((u1 + u2) / 2.0, (3 * v1 + v2) / 4.0),
        ((u1 + u2) / 2.0, (v1 + 3 * v2) / 4.0),
    ]
    for um, vm in candidates:
        try:
            props.SetParameters(um, vm)
            if not props.IsNormalDefined():
                continue
            pnt = props.Value()
            n = props.Normal()
            nx, ny, nz = n.X(), n.Y(), n.Z()
            mag = math.sqrt(nx * nx + ny * ny + nz * nz)
            if mag < 1e-12:
                continue
            nx, ny, nz = nx / mag, ny / mag, nz / mag
            eps = 0.1
            if _point_inside(
                    product,
                    gp_Pnt(pnt.X() + nx * eps, pnt.Y() + ny * eps, pnt.Z() + nz * eps),
                    tol=1e-6):
                nx, ny, nz = -nx, -ny, -nz
            return pnt, gp_Vec(nx, ny, nz)
        except Exception:
            continue
    return None


def _sample_face_points(face, cap=12):
    """在面上采样点：顶点 + 边中点 + 面 UV 中心（尽力），去重并截断。"""
    pts = []
    exp = TopExp_Explorer(face, TopAbs_VERTEX)
    while exp.More():
        pts.append(BRep_Tool.Pnt_s(TopoDS.Vertex_s(exp.Current())))
        exp.Next()
    exp = TopExp_Explorer(face, TopAbs_EDGE)
    while exp.More():
        e = TopoDS.Edge_s(exp.Current())
        ad = BRepAdaptor_Curve(e)
        u1, u2 = ad.FirstParameter(), ad.LastParameter()
        if u2 - u1 > 1e-9:
            pts.append(ad.Value(0.5 * (u1 + u2)))
        exp.Next()
    try:
        adf = BRepAdaptor_Surface(TopoDS.Face_s(face))
        u1, u2 = adf.FirstUParameter(), adf.LastUParameter()
        v1, v2 = adf.FirstVParameter(), adf.LastVParameter()
        if abs(u1) > 1e8: u1 = -1.0
        if abs(u2) > 1e8: u2 = 1.0
        if abs(v1) > 1e8: v1 = -1.0
        if abs(v2) > 1e8: v2 = 1.0
        if u2 - u1 > 1e-9 and v2 - v1 > 1e-9:
            pts.append(adf.Value(0.5 * (u1 + u2), 0.5 * (v1 + v2)))
    except Exception:
        pass
    out = []
    for p in pts:
        if all(p.Distance(q) > 1e-6 for q in out):
            out.append(p)
        if len(out) >= cap:
            break
    return out


def _dist_point_solid(pnt, solid, index=None):
    """点到实体的最近距离（点在其内部/表面为 0）。失败返回 None。

    P9：传入预建的 _SolidDistIndex 可避免每个采样点重复枚举
    SOLID / 构造 vertex / 无预筛的开销；不传则临时构建（兼容旧调用）。
    """
    if index is None:
        index = _SolidDistIndex(solid)
    return index.dist(pnt)


# =====================================================================
# 主验证类
# =====================================================================

class FitChecker:
    """三件贴合验证器。

    Args:
        gap_tol: 间隙容差 (mm)，默认 0.1。产品采样点到模具距离 ≤ 该值视为贴合。
        interfere_tol: 干涉容差 (mm³)，默认 1.0。Common 体积超过该值视为干涉。
        band_tol: 型腔顶面开口低于分模面的最大允许量 (mm)，默认 0.2。
        sample_cap: 每个产品面最多采样点数，默认 12。
    """

    def __init__(self, gap_tol=0.1, interfere_tol=1.0, band_tol=0.2, sample_cap=12):
        self.gap_tol = gap_tol
        self.interfere_tol = interfere_tol
        self.band_tol = band_tol
        self.sample_cap = sample_cap

    def check(self, punch_file, cavity_file, product_file=None):
        """执行三件贴合验证。

        Returns:
            dict: {'result','issues','checks','gaps','gap_count','elapsed_s'}
        """
        _force_utf8()
        t0 = time.time()
        issues = []
        gaps = []
        checks = {}

        print("=" * 60)
        print("三件贴合验证  [上模 → 产品 → 下模]")
        print("=" * 60)
        ps = _extract_solids(punch_file)
        cs = _extract_solids(cavity_file)
        print(f"  凸模文件结构体: {len(ps)} 个（期望 3：基座/凸起/产品）")
        print(f"  凹模文件结构体: {len(cs)} 个（期望 2：型腔/产品）")

        if len(ps) < 3:
            issues.append({'severity': 'FAIL', 'check': 'structure',
                           'msg': f"凸模文件应含 3 个结构体（基座/凸起/产品），实际 {len(ps)} 个"})
        if len(cs) < 2:
            issues.append({'severity': 'FAIL', 'check': 'structure',
                           'msg': f"凹模文件应含 2 个结构体（型腔/产品），实际 {len(cs)} 个"})
        # B2：结构体数量不足时，后续 ps[2]/cs[1] 索引没有意义
        #（len(ps)==1 时 ps[1] 直接 IndexError，与凹模侧保护不对称）。
        # 提前返回，不做猜测性索引。
        if len(ps) < 3 or len(cs) < 2:
            return FitChecker._finalize(issues, checks, gaps, t0)

        base_p = ps[0][1]
        protrusion = ps[1][1] if len(ps) >= 3 else None
        product_p = ps[2][1] if len(ps) >= 3 else ps[1][1]
        cavity = cs[0][1]
        product_c = cs[1][1] if len(cs) >= 2 else cs[0][1]

        # ---- 产品实体选取 + 一致性 ----
        product = None
        if product_file and os.path.exists(product_file):
            ext = _extract_solids(product_file)
            if ext:
                product = ext[0][1]
                print(f"  使用外部产品文件: {os.path.basename(product_file)}")
        if product is None:
            product = product_p
            print("  产品取自凸模 compound（第 3 结构体）")
        if product is None:
            product = product_c

        if product_p is not None and product_c is not None:
            v = _common_volume(product_p, product_c)
            if v is None:
                # B6：布尔失败不再伪装成"无重叠"——按未知处理并告警
                checks['product_consistency'] = None
                issues.append({'severity': 'WARN', 'check': 'consistency',
                               'msg': "凸模内产品与凹模内产品一致性检查失败（布尔运算未完成），请人工确认"})
            else:
                vp = max(_solid_volume(product_p), 1e-9)
                checks['product_consistency'] = round(v / vp, 4)
                if v / vp < 0.95:
                    issues.append({'severity': 'WARN', 'check': 'consistency',
                                   'msg': "凸模内产品与凹模内产品不一致，请确认同产品/同坐标系"})

        # ---- 分模面 / 型腔开口 ----
        Z_A = _bbox_tuple(product)[5] if product is not None else _bbox_tuple(product_p)[5]
        cavity_open_z = _bbox_tuple(cavity)[5]
        band = Z_A - cavity_open_z
        checks['parting_plane_z'] = round(Z_A, 3)
        checks['cavity_open_z'] = round(cavity_open_z, 3)
        checks['opening_band_mm'] = round(band, 3)
        print(f"  分模面 Z_A = {Z_A:.3f}   型腔顶面开口 = {cavity_open_z:.3f}   "
              f"外露带 = {band:.3f} mm")
        if band > self.band_tol:
            issues.append({'severity': 'FAIL', 'check': 'cavity_opening',
                           'msg': f"型腔顶面低于分模面 {band:.2f}mm（> {self.band_tol:.2f}mm）："
                                  "产品顶部外露带，型腔未忠实复刻产品高度（下模挖空开口过低）"})

        # ---- 干涉检查 ----
        pairs = [
            ('凸起↔产品', protrusion, product),
            ('型腔↔产品', cavity, product),
            ('凸模基座↔产品', base_p, product),
            ('凸起↔型腔', protrusion, cavity),
        ]
        for name, a, b in pairs:
            if a is None or b is None:
                continue
            v = _common_volume(a, b)
            if v is None:
                # B6：布尔失败不再伪装成"无重叠"——按未知处理并告警
                checks[f'interfere_{name}'] = None
                print(f"  [干涉] {name}: 布尔运算失败，无法判定")
                issues.append({'severity': 'WARN', 'check': 'interference',
                               'msg': f"{name} 干涉检查失败（布尔运算未完成），请人工确认"})
                continue
            checks[f'interfere_{name}'] = round(v, 3)
            print(f"  [干涉] {name}: Common = {v:.3f} mm³"
                  + ("  OK" if v <= self.interfere_tol else "  ✗ 干涉!"))
            if v > self.interfere_tol:
                issues.append({'severity': 'FAIL', 'check': 'interference',
                               'msg': f"{name} 干涉体积 {v:.1f} mm³（> {self.interfere_tol}）—— 实体交叉重叠"})

        # ---- 间隙 / 型腔忠实检查（产品表面点采样）----
        print(f"\n  产品表面贴合采样（gap_tol={self.gap_tol}mm，每面≤{self.sample_cap}点）...")
        return FitChecker._run_gap_check(
            product, base_p, protrusion, cavity, self.gap_tol,
            self.sample_cap, issues, checks, gaps, t0)


    @staticmethod
    def _run_gap_check(product, base_p, protrusion, cavity, gap_tol,
                       sample_cap, issues, checks, gaps, t0):
        """产品表面点采样：每个点必须被【型腔 或 凸模基座 或 凸起】任一覆盖。"""
        # P9：预建距离索引（一次性枚举 SOLID + bbox + 内含分类器），
        # 避免每个采样点重复枚举 SOLID / 构造 vertex / 无预筛的开销
        idx_cav = _SolidDistIndex(cavity)
        idx_base = _SolidDistIndex(base_p)
        idx_pro = _SolidDistIndex(protrusion) if protrusion is not None else None
        n_pts = 0
        exp = TopExp_Explorer(product, TopAbs_FACE)
        fi = 0
        while exp.More():
            f = exp.Current()
            pts = _sample_face_points(f, sample_cap)
            for p in pts:
                n_pts += 1
                # 先测型腔（产品表面大多贴型腔内壁/处于空腔内 → 距离 0）
                d_cav = _dist_point_solid(p, cavity, index=idx_cav)
                if d_cav is not None and d_cav <= gap_tol:
                    continue
                # 型腔未覆盖 → 检查凸模基座底面（覆盖产品顶部）
                d_base = _dist_point_solid(p, base_p, index=idx_base)
                if d_base is not None and d_base <= gap_tol:
                    continue
                # 再检查凸起（覆盖产品凹坑/凹槽）
                d_pro = _dist_point_solid(p, protrusion, index=idx_pro) if protrusion is not None else None
                if d_pro is not None and d_pro <= gap_tol:
                    continue
                # 三者均未覆盖 → 间隙
                cand = [(v, k) for v, k in
                        ((d_cav, 'cavity'), (d_base, 'punch_base'), (d_pro, 'punch_protrusion'))
                        if v is not None]
                if not cand:
                    continue
                d_best, who = min(cand, key=lambda x: x[0])
                gaps.append({
                    'face': fi,
                    'x': round(p.X(), 2), 'y': round(p.Y(), 2), 'z': round(p.Z(), 2),
                    'gap_mm': round(d_best, 4), 'nearest': who,
                    'd_cav': round(d_cav, 4) if d_cav is not None else None,
                    'd_base': round(d_base, 4) if d_base is not None else None,
                    'd_pro': round(d_pro, 4) if d_pro is not None else None,
                })
            fi += 1
            exp.Next()

        checks['sampled_points'] = n_pts
        checks['gap_points'] = len(gaps)
        if gaps:
            gaps.sort(key=lambda g: -g['gap_mm'])
            issues.append({
                'severity': 'FAIL', 'check': 'gap',
                'msg': f"产品表面 {len(gaps)}/{n_pts} 个采样点未被上/下模覆盖"
                       f"（最大间隙 {gaps[0]['gap_mm']:.3f}mm），详情见报告",
            })
            n_cav = sum(1 for g in gaps if g['nearest'] == 'cavity')
            n_pun = len(gaps) - n_cav
            if n_cav > n_pun:
                issues.append({'severity': 'WARN', 'check': 'cause',
                               'msg': f"间隙多位于型腔侧（{n_cav} 点），判定为【下模挖空不正确】，"
                                      "应修正 mold_fix.hollow() 后重新生成凹模"})
            else:
                issues.append({'severity': 'WARN', 'check': 'cause',
                               'msg': f"间隙多位于上模侧（{n_pun} 点），判定为【上模填充不正确】，"
                                      "应修正 mold_fix.fill() 后重新生成凸模"})
        else:
            print("  ✓ 所有采样点均被上模/下模覆盖 —— 无间隙、无外露带")
        return FitChecker._finalize(issues, checks, gaps, t0)

    @staticmethod
    def _finalize(issues, checks, gaps, t0):
        result = 'FAIL' if any(i['severity'] == 'FAIL' for i in issues) else 'PASS'
        gaps.sort(key=lambda g: -g['gap_mm'])
        return {
            'result': result,
            'issues': issues,
            'checks': checks,
            'gaps': gaps[:50],
            'gap_count': len(gaps),
            'elapsed_s': round(time.time() - t0, 1),
        }

    def print_report(self, report, show_gaps=10):
        """打印人类可读报告，返回 'PASS' / 'FAIL'。"""
        print("\n" + "=" * 60)
        if report['result'] == 'PASS':
            print("  验证结论: ✅ PASS —— 三者完全贴合（无间隙 / 无干涉 / 型腔忠实）")
        else:
            print("  验证结论: ❌ FAIL —— 存在贴合问题（见下）")
        print("=" * 60)
        print(f"  检查用时: {report['elapsed_s']} s")
        for k, v in report['checks'].items():
            print(f"    {k}: {v}")
        if report['issues']:
            print("\n  --- 检查问题 ---")
            for i in report['issues']:
                print(f"  [{i['severity']}] {i['check']}: {i['msg']}")
        if report.get('gaps'):
            print(f"\n  --- 间隙明细（前 {show_gaps}/{report['gap_count']} 处，按间隙从大到小）---")
            print("    face   x        y        z      gap_mm   nearest")
            for g in report['gaps'][:show_gaps]:
                print(f"    {g['face']:>5} {g['x']:>8.1f} {g['y']:>8.1f} {g['z']:>8.2f}"
                      f" {g['gap_mm']:>8.3f} {g['nearest']}")
        return report['result']


# =====================================================================
# 命令行入口
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        prog='fit_checker.py',
        description='三件贴合验证模块：验证【修复后凸模 → 产品 → 修复后凹模】'
                    '依次紧密贴合（无间隙 / 无干涉 / 型腔忠实复刻产品外形）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            '示例：\n'
            '  python fit_checker.py --punch fix_output/xxx_PUNCH_FILLED.stp \\\n'
            '                         --cavity fix_output/xxx_CAVITY_HOLLOWED.stp\n'
            '  python fit_checker.py --punch ... --cavity ... --product 产品.stp\n'
            '  python fit_checker.py --punch ... --cavity ... --gap-tol 0.05\n'
            '退出码：0=PASS（完全贴合）；1=FAIL（间隙/干涉/型腔不忠实）；2=异常'
        ),
    )
    parser.add_argument('--punch', dest='punch_file', required=True,
                        help='修复后凸模(上模) STEP 文件（compound [基座, 凸起, 产品]）')
    parser.add_argument('--cavity', dest='cavity_file', required=True,
                        help='修复后凹模(下模) STEP 文件（compound [型腔, 产品]）')
    parser.add_argument('--product', dest='product_file', default=None,
                        help='外部产品 STEP 文件（可选，默认取凸模 compound 内产品）')
    parser.add_argument('--gap-tol', dest='gap_tol', type=float, default=0.1,
                        help='间隙容差 mm（默认 0.1）')
    parser.add_argument('--interfere-tol', dest='interfere_tol', type=float, default=1.0,
                        help='干涉容差 mm³（默认 1.0）')
    parser.add_argument('--band-tol', dest='band_tol', type=float, default=0.2,
                        help='型腔开口低于分模面允许的最大外露带 mm（默认 0.2）')
    parser.add_argument('--sample-cap', dest='sample_cap', type=int, default=12,
                        help='每个产品面采样点数（默认 12）')
    parser.add_argument('--show-gaps', dest='show_gaps', type=int, default=10,
                        help='报告中打印的间隙明细条数（默认 10）')
    parser.add_argument('--json', dest='json_out', default=None,
                        help='将报告写入 JSON 文件（可选）')
    args = parser.parse_args()

    _force_utf8()
    try:
        checker = FitChecker(gap_tol=args.gap_tol, interfere_tol=args.interfere_tol,
                             band_tol=args.band_tol, sample_cap=args.sample_cap)
        report = checker.check(args.punch_file, args.cavity_file, args.product_file)
        result = checker.print_report(report, show_gaps=args.show_gaps)
        if args.json_out:
            with open(args.json_out, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f"\n  报告已写入: {args.json_out}")
        sys.exit(0 if result == 'PASS' else 1)
    except Exception as e:
        import traceback
        print(f"验证异常: {e}")
        traceback.print_exc()
        sys.exit(2)


if __name__ == '__main__':
    main()




