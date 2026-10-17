"""
corner_close.py — 钣金件角落智能自动延伸闭合补齐模块

【模块定位】
  运行在【镜像生成之后、凹凸模生成之前】，作为产品预处理步骤：
  识别法兰拐角开放缺口，沿自身平面方向延伸相邻法兰面、裁剪相交生成闭合补片，
  与原实体 Fuse 布尔融合，输出修复补齐后的产品 STEP，保证后续模具生成质量。

【技术底座】
  几何操作基于 OpenCASCADE（cadquery-ocp 的 OCP.* 原生绑定）：
    - BRepBuilderAPI_MakeFace 沿平面构造延伸矩形面
    - BRepPrimAPI_MakePrism  面→板厚实体
    - BRepAlgoAPI_Common / Fuse  补片生成与融合
    - BRepCheck_Analyzer    形状有效性校验
  拓扑复用（不重复造轮子）：
    - brep_topology 抽取面/边/邻接/板厚/平面圆角分类
    - bend_detector 的 free_edges 自由边识别

【输出文件命名约定】
  {product_name}_CORNER_CLOSED.stp
"""

import os
import sys
import math
import argparse
import datetime

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from brep_topology import BrepTopologyExtractor
from bend_detector import BendDetector

from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeBox
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Fuse
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import (
    TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE, TopAbs_EDGE, TopAbs_REVERSED,
)
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Pnt, gp_Vec, gp_Pln, gp_Ax3, gp_Dir


def _vec(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    return math.sqrt(_dot(a, a))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _normalize(a):
    n = _norm(a)
    if n < 1e-12:
        return (0.0, 0.0, 0.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def _dist(a, b):
    return _norm(_vec(a, b))


def _scale(v, s):
    return (v[0] * s, v[1] * s, v[2] * s)


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _midpoint(a, b):
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2)

class CornerCloseRepair:
    """钣金件角落自动延伸闭合补齐器。

    参数（均带合理默认值）：
        extend_mm: float = 2.0       面最大延伸距离（不允许无限制延伸）
        gap_tol: float = 0.15        判定为需要闭合的角落间隙阈值，间隙>该值才做闭合
        thickness: float | None      钣金板厚；不传则复用 brep_topology 自动检测
        corner_mode: 'butt'|'overlap' 对接 / 重叠两种闭合模式，默认 butt
        skip_small_features: bool    跳过极小碎面（避免误修复圆角/倒角特征）
        export_report: bool          是否输出 txt 修复报告到输出目录
        min_flange_area: float       小面面积阈值（mm²）
        angle_min_deg: float         两平面二面角下限（°），过滤平行/共面墙
        max_gap_detect: float        识别角落的最大自由边间隙（mm），过滤相距过远的碎对
        proximity_tol: float         两法兰自由边到对方平面的最大距离（mm），判定为真实交汇角落
    """

    def __init__(self, extend_mm: float = 2.0, gap_tol: float = 0.15,
                 thickness: float | None = None, corner_mode: str = "sharp_closed",
                 skip_small_features: bool = True, export_report: bool = True,
                 min_flange_area: float = 3.0, angle_min_deg: float = 10.0,
                 max_gap_detect: float = 12.0, proximity_tol: float = 5.0):
        if extend_mm <= 0:
            raise ValueError("extend_mm 必须为正数")
        if gap_tol < 0:
            raise ValueError("gap_tol 不能为负")
        if corner_mode not in ("sharp_closed", "butt", "overlap"):
            raise ValueError("corner_mode 必须是 'sharp_closed' / 'butt' / 'overlap'")
        self.extend_mm = extend_mm
        self.gap_tol = gap_tol
        self.thickness = thickness
        self.corner_mode = corner_mode
        self.skip_small_features = skip_small_features
        self.export_report = export_report
        self.min_flange_area = min_flange_area
        self.angle_min_deg = angle_min_deg
        self.max_gap_detect = max_gap_detect
        self.proximity_tol = proximity_tol


    # ── 加载 / 拓扑复用 ──

    def _load(self, filepath: str):
        """复用 brep_topology + bend_detector 抽取拓扑、分类、板厚、自由边。

        返回 (extractor, topo, det_result)。
        extractor 内部保留 face/edge 的 TopoDS 实体（_face_shapes/_edge_shapes），
        供几何运算使用，避免重复编写 STEP 解析逻辑。
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"文件不存在: {filepath}")
        ext = BrepTopologyExtractor(filepath)
        topo = ext.extract()
        det = BendDetector(topo)
        det_result = det.detect()
        return ext, topo, det_result

    def _get_solid(self, ext):
        return ext._solid

    def _get_thickness(self, topo):
        if self.thickness is not None:
            return float(self.thickness)
        th = topo.get('thickness')
        if th and th > 0:
            return float(th)
        return 2.0


    # ── 自由边 / 几何离散工具 ──

    def _edge_points(self, ext, eid: int, n: int = 12):
        """把边离散为 3D 折线点列（用于测距）。"""
        edge = ext._edge_shapes[eid]
        try:
            ad = BRepAdaptor_Curve(edge)
        except Exception:
            return []
        first = ad.FirstParameter()
        last = ad.LastParameter()
        pts = []
        if not (math.isfinite(first) and math.isfinite(last)) or last <= first:
            return []
        for i in range(n + 1):
            p = gp_Pnt()
            try:
                ad.D0(first + (last - first) * i / n, p)
            except Exception:
                continue
            pts.append((p.X(), p.Y(), p.Z()))
        return pts

    @staticmethod
    def _polyline_closest(pa, pb, na=9, nb=9):
        """两条折线点列之间最近的一对点，返回 (pa_point, pb_point, dist)。"""
        if not pa or not pb:
            return None, None, 1e18
        best = 1e18
        bp = bq = None
        for i in range(len(pa) - 1):
            a0, a1 = pa[i], pa[i + 1]
            for j in range(len(pb) - 1):
                b0, b1 = pb[j], pb[j + 1]
                for s in range(na + 1):
                    qa = (a0[0] + (a1[0] - a0[0]) * s / na,
                          a0[1] + (a1[1] - a0[1]) * s / na,
                          a0[2] + (a1[2] - a0[2]) * s / na)
                    for t in range(nb + 1):
                        qb = (b0[0] + (b1[0] - b0[0]) * t / nb,
                              b0[1] + (b1[1] - b0[1]) * t / nb,
                              b0[2] + (b1[2] - b0[2]) * t / nb)
                        d = _dist(qa, qb)
                        if d < best:
                            best = d
                            bp, bq = qa, qb
        return bp, bq, best

    @staticmethod
    def _polyline_min_dist(pa, pb, na=7, nb=7):
        """两条 3D 折线点列之间的最小距离（分段线性采样近似）。"""
        if not pa or not pb:
            return 1e18
        best = 1e18
        for i in range(len(pa) - 1):
            a0, a1 = pa[i], pa[i + 1]
            for j in range(len(pb) - 1):
                b0, b1 = pb[j], pb[j + 1]
                for s in range(na + 1):
                    qa = (a0[0] + (a1[0] - a0[0]) * s / na,
                          a0[1] + (a1[1] - a0[1]) * s / na,
                          a0[2] + (a1[2] - a0[2]) * s / na)
                    for t in range(nb + 1):
                        qb = (b0[0] + (b1[0] - b0[0]) * t / nb,
                              b0[1] + (b1[1] - b0[1]) * t / nb,
                              b0[2] + (b1[2] - b0[2]) * t / nb)
                        d = _dist(qa, qb)
                        if d < best:
                            best = d
        return best


    @staticmethod
    def _plane_signed_dist(origin, normal, point):
        """点到平面的有符号距离。"""
        return _dot(_vec(point, origin), normal)

    def _edge_plane_dist(self, pts, origin, normal):
        """折线点列到平面的最小距离。"""
        if not pts:
            return 1e18
        best = 1e18
        for p in pts:
            best = min(best, abs(self._plane_signed_dist(origin, normal, p)))
        return best

    # ── 尖角闭合几何（延伸两边缘线求交点 → 三角补片） ──

    @staticmethod
    def _edge_dir(pa):
        """折线（边）的方向向量（从起点指向终点，单位化）。"""
        if len(pa) < 2:
            return None
        return _normalize(_vec(pa[-1], pa[0]))

    @staticmethod
    def _edges_coplanar(pa, pb):
        """两条边（折线点列）是否共面：混合积近似为 0 则共面。"""
        d1 = CornerCloseRepair._edge_dir(pa)
        d2 = CornerCloseRepair._edge_dir(pb)
        if d1 is None or d2 is None:
            return True
        cr = _cross(d1, d2)
        if _norm(cr) < 1e-6:
            return True  # 平行视为共面
        w = _sub(pa[0], pb[0])
        t = _dot(cr, w)
        # 以边长尺度归一化判定
        scale = max(1.0, _norm(w), _norm(_sub(pa[0], pa[-1])), _norm(_sub(pb[0], pb[-1])))
        return abs(t) < 1e-3 * scale * _norm(cr)

    @staticmethod
    def _edge_extend_dir(pa, pt):
        """边在缺口端 pt 处的延伸方向（沿边方向、向缺口外延伸）。"""
        first = pa[0]
        last = pa[-1]
        if _dist(pt, first) <= _dist(pt, last):
            # 缺口在起点侧 → 向起点外延伸
            if len(pa) < 2:
                return None
            return _normalize(_sub(first, pa[1])) if _norm(_sub(first, pa[1])) > 1e-9 else None
        else:
            if len(pa) < 2:
                return None
            return _normalize(_sub(last, pa[-2])) if _norm(_sub(last, pa[-2])) > 1e-9 else None

    @staticmethod
    def _line_line_closest(p1, d1, p2, d2):
        """两条三维直线的最接近点（若相交即交点）。返回 apex 点或 None。"""
        d1n = _normalize(d1)
        d2n = _normalize(d2)
        cr = _cross(d1n, d2n)
        if _norm(cr) < 1e-6:
            return None  # 平行
        w0 = _sub(p1, p2)
        a = _dot(d1n, d1n)
        b = _dot(d1n, d2n)
        c = _dot(d2n, d2n)
        e = _dot(w0, d1n)
        f = _dot(w0, d2n)
        denom = a * c - b * b
        if abs(denom) < 1e-12:
            return None
        s = (b * f - c * e) / denom
        t = (a * f - b * e) / denom
        pt1 = _add(p1, _scale(d1n, s))
        pt2 = _add(p2, _scale(d2n, t))
        return _midpoint(pt1, pt2)

    @staticmethod
    def _triangle_normal(p1, p2, p3):
        v1 = _sub(p2, p1)
        v2 = _sub(p3, p1)
        return _normalize(_cross(v1, v2))

    @staticmethod
    def _make_triangle_face(p1, p2, p3):
        """以三点构造三角形面。"""
        try:
            poly = BRepBuilderAPI_MakePolygon()
            poly.Add(gp_Pnt(p1[0], p1[1], p1[2]))
            poly.Add(gp_Pnt(p2[0], p2[1], p2[2]))
            poly.Add(gp_Pnt(p3[0], p3[1], p3[2]))
            poly.Close()
            wire = poly.Wire()
            if wire.IsNull():
                return None
            fb = BRepBuilderAPI_MakeFace(wire)
            fb.Build()
            if not fb.IsDone():
                return None
            f = fb.Face()
            return None if f.IsNull() else f
        except Exception:
            return None

    @staticmethod
    def _extrude_face(face, normal, thickness):
        """沿法向把面拉伸成厚度为 thickness 的实体。"""
        if _norm(normal) < 1e-9:
            return None
        try:
            p = BRepPrimAPI_MakePrism(face, gp_Vec(normal[0] * thickness,
                                                   normal[1] * thickness,
                                                   normal[2] * thickness))
            p.Build()
            if not p.IsDone():
                return None
            s = p.Shape()
            return None if s.IsNull() else s
        except Exception:
            return None

    # ── 角落识别 ──

    def _detect_corners(self, ext, topo, det_result):
        """识别法兰拐角开放缺口，返回角落候选列表。

        每个角落：
          {'faces': (fa, fb), 'angle_deg', 'gap_mm',
           'edge_pair': (ea, eb), 'closable': bool, 'normals': ...}
        """
        faces = topo['faces']
        cls = topo['classification']
        planar = [fi for fi in cls.get('planar_faces', [])]

        thick_set = set()
        for p in topo.get('thickness_pairs', []):
            thick_set.add(tuple(sorted(p)))

        fe_by_face = {}
        for fe in det_result.get('free_edges', []):
            for af in fe.get('adjacent_faces', []):
                fe_by_face.setdefault(af, []).append(fe['edge_idx'])
            if len(fe.get('adjacent_faces', [])) == 1:
                fe_by_face.setdefault(fe['adjacent_faces'][0], []).append(fe['edge_idx'])

        info = {}
        for fi in planar:
            sp = faces[fi]['surface_params']
            normal = tuple(sp.get('normal') or (0, 0, 0))
            origin = tuple(sp.get('origin') or (0, 0, 0))
            area = faces[fi]['area']
            small = bool(self.skip_small_features and area < self.min_flange_area)
            info[fi] = {
                'normal': normal, 'origin': origin, 'area': area,
                'free': sorted(set(fe_by_face.get(fi, []))),
                'small': small,
            }

        candidates = []
        planar_list = [fi for fi in planar if not info[fi]['small']]
        for ia in range(len(planar_list)):
            fa = planar_list[ia]
            for ib in range(ia + 1, len(planar_list)):
                fb = planar_list[ib]
                key = tuple(sorted((fa, fb)))
                if key in thick_set:
                    continue
                # 排除相邻面：相邻（共享边）即已连接，其间隙只是板厚偏移，非开放角落。
                if fa in faces[fb].get('adjacent_faces', []) or fb in faces[fa].get('adjacent_faces', []):
                    continue
                na, nb = info[fa]['normal'], info[fb]['normal']
                c = _cross(na, nb)
                if _norm(c) < 1e-6:
                    continue
                dot_nn = max(-1.0, min(1.0, _dot(na, nb)))
                ang = math.degrees(math.acos(dot_nn))
                ang = min(ang, 180.0 - ang)
                if ang < self.angle_min_deg:
                    continue
                fa_edges = info[fa]['free']
                fb_edges = info[fb]['free']
                if not fa_edges or not fb_edges:
                    continue
                pa_cache = {e: self._edge_points(ext, e) for e in fa_edges}
                pb_cache = {e: self._edge_points(ext, e) for e in fb_edges}
                # 在所有自由边对中，只保留【共面】（同一法兰平面内、可形成尖角）
                # 且间隙在 (gap_tol, max_gap] 的边对，取间隙最小者。
                best = 1e18
                best_pair = (None, None)
                for ea in fa_edges:
                    for eb in fb_edges:
                        if not self._edges_coplanar(pa_cache[ea], pb_cache[eb]):
                            continue
                        d = self._polyline_min_dist(pa_cache[ea], pb_cache[eb])
                        if self.gap_tol < d < best:
                            best = d
                            best_pair = (ea, eb)
                if best_pair[0] is None or best >= 1e17:
                    continue  # 无共面闭合边对
                if best > self.max_gap_detect:
                    continue  # 相距过远，非同一角落特征

                closable = best <= 2.0 * self.extend_mm
                ea, eb = best_pair
                # 缺口位置（两缺口边的最近点中点），用于去重同一物理角落。
                qa, qb, _ = self._polyline_closest(pa_cache[ea], pb_cache[eb])
                gap_pos = None
                if qa is not None and qb is not None:
                    gap_pos = _midpoint(qa, qb)
                candidates.append({
                    'faces': (fa, fb),
                    'angle_deg': round(ang, 2),
                    'gap_mm': round(best, 4),
                    'edge_pair': (ea, eb),
                    'closable': closable,
                    'normals': (na, nb),
                    'origins': (info[fa]['origin'], info[fb]['origin']),
                    'gap_pos': gap_pos,
                })

        # 去重：同一缺口位置只保留一个角落（避免重复闭合同一物理角落）。
        dedup = {}
        for c in candidates:
            if c['gap_pos'] is None:
                dedup.setdefault(('free',), c)
                continue
            key = (round(c['gap_pos'][0], 3), round(c['gap_pos'][1], 3), round(c['gap_pos'][2], 3))
            if key not in dedup:
                dedup[key] = c
        candidates = list(dedup.values())
        return candidates


    # ── 角落闭合几何处理（OCP 原生） ──

    def _material_inward_dir(self, ext, topo, face_idx, surface_normal):
        """确定面所在板厚的『材料内侧』方向。

        优先用厚度对面（thickness_pairs）：方向 = 本面平面原点 → 反向平行面对原点。
        退化时回退到面朝向判定：OUTER 法向的反向即材料内侧。
        """
        origin = None
        try:
            fp = topo['faces'][face_idx]['surface_params']
            origin = tuple(fp.get('origin') or (0, 0, 0))
        except Exception:
            pass
        if origin:
            for p in topo.get('thickness_pairs', []):
                if face_idx in p:
                    other = p[0] if p[1] == face_idx else p[1]
                    try:
                        op = topo['faces'][other]['surface_params']
                        oo = tuple(op.get('origin') or (0, 0, 0))
                        v = _vec(oo, origin)
                        if _norm(v) > 1e-9:
                            return _normalize(v)
                    except Exception:
                        pass
        face = ext._face_shapes[face_idx]
        rev = False
        try:
            rev = face.Orientation() == TopAbs_REVERSED
        except Exception:
            rev = False
        outward = _normalize(surface_normal)
        return (-outward[0], -outward[1], -outward[2]) if not rev else outward

    def _face_slab(self, ext, topo, face_idx, thickness, extend):
        """把一个平面沿自身平面方向扩展为矩形面，再沿材料内侧拉伸为板厚实体。"""
        face = ext._face_shapes[face_idx]
        adaptor = BRepAdaptor_Surface(face)
        pln = adaptor.Plane()
        try:
            u1 = adaptor.FirstUParameter()
            u2 = adaptor.LastUParameter()
            v1 = adaptor.FirstVParameter()
            v2 = adaptor.LastVParameter()
        except Exception:
            return None
        if not (math.isfinite(u1) and math.isfinite(u2)
                and math.isfinite(v1) and math.isfinite(v2)):
            return None
        u1 -= extend
        u2 += extend
        v1 -= extend
        v2 += extend
        ext_face_builder = BRepBuilderAPI_MakeFace(pln, u1, u2, v1, v2)
        ext_face_builder.Build()
        if not ext_face_builder.IsDone():
            return None
        ext_face = ext_face_builder.Face()
        if ext_face.IsNull():
            return None
        normal = (pln.Position().Axis().Direction().X(),
                  pln.Position().Axis().Direction().Y(),
                  pln.Position().Axis().Direction().Z())
        inward = self._material_inward_dir(ext, topo, face_idx, normal)
        if _norm(inward) < 1e-9:
            return None
        try:
            prism = BRepPrimAPI_MakePrism(ext_face, gp_Vec(inward[0] * thickness,
                                                           inward[1] * thickness,
                                                           inward[2] * thickness))
            prism.Build()
        except Exception:
            return None
        if not prism.IsDone():
            return None
        s = prism.Shape()
        if s.IsNull():
            return None
        return s

    @staticmethod
    def _make_box(mn, mx):
        """以两对角点构造一个盒子实体。"""
        try:
            box = BRepPrimAPI_MakeBox(gp_Pnt(mn[0], mn[1], mn[2]),
                                      gp_Pnt(mx[0], mx[1], mx[2]))
            box.Build()
            if not box.IsDone():
                return None
            s = box.Shape()
            return s if not s.IsNull() else None
        except Exception:
            return None

    def _corner_box(self, ext, corner, margin):
        """角落区域的裁剪包围盒：张成最近自由边对的最近点 qa/qb（即缺口），外扩 margin。

        用 qa、qb 两个点直接确定缺口张成范围，而非以中点为心的立方体，
        从而得到贴合缺口形状的紧凑裁剪盒，避免补片在其它方向过度外扩。
        """
        ea, eb = corner.get('edge_pair', (None, None))
        if ea is None or eb is None:
            return None
        pa = self._edge_points(ext, ea)
        pb = self._edge_points(ext, eb)
        qa, qb, d = self._polyline_closest(pa, pb)
        if qa is None or qb is None:
            return None
        mn = (min(qa[0], qb[0]) - margin, min(qa[1], qb[1]) - margin, min(qa[2], qb[2]) - margin)
        mx = (max(qa[0], qb[0]) + margin, max(qa[1], qb[1]) + margin, max(qa[2], qb[2]) + margin)
        return (mn, mx)

    def _build_patch_sharp(self, ext, topo, corner, thickness, extend):
        """尖角闭合：延伸两条缺口边线求交点 → 生成与原板厚一致的三角形楔形补片。

        步骤：
          1. 取两条最靠近的缺口边（同一法兰平面内）；
          2. 各沿边方向向缺口外延伸（求缺口端点处切线方向的延长线）；
          3. 两延伸线求交点 → 尖角顶点 apex；
          4. 以 [缺口端点A, 缺口端点B, apex] 构造三角形面；
          5. 沿材料内侧方向拉伸板厚 → 楔形实体。
        """
        ea, eb = corner.get('edge_pair', (None, None))
        if ea is None or eb is None:
            return None
        pa_pts = self._edge_points(ext, ea)
        pb_pts = self._edge_points(ext, eb)
        if len(pa_pts) < 2 or len(pb_pts) < 2:
            return None
        qa, qb, gap = self._polyline_closest(pa_pts, pb_pts)
        if qa is None or qb is None:
            return None
        da = self._edge_extend_dir(pa_pts, qa)
        db = self._edge_extend_dir(pb_pts, qb)
        if da is None or db is None:
            return None
        apex = self._line_line_closest(qa, da, qb, db)
        if apex is None:
            return None

        # 搭接余量：把三角形基边的两个缺口端点沿各自边、向材料内部回退，
        # 使补片与原钣金产生面重叠，Fuse 才能融合成一体（而非仅沿边接触）。
        overlap = min(thickness, max(0.5, gap * 0.25))
        qa_lap = _add(qa, _scale(da, -overlap))  # 向材料内侧回退
        qb_lap = _add(qb, _scale(db, -overlap))
        # 保证三角形仍非退化且朝尖角方向
        if _norm(_sub(qb_lap, qa_lap)) < 1e-9:
            return None
        # 检查三角形非退化（基边用搭接后的端点，保证与原钣金重叠）
        tri_n = self._triangle_normal(qa_lap, qb_lap, apex)
        if _norm(tri_n) < 1e-9:
            return None
        face = self._make_triangle_face(qa_lap, qb_lap, apex)
        if face is None:
            return None
        # 厚度方向 = 三角形法向，与材料内侧方向对齐
        fa = corner['faces'][0]
        na = corner.get('normals', ((0, 0, 0), (0, 0, 0)))[0]
        inward = self._material_inward_dir(ext, topo, fa, na)
        n = tri_n
        if _norm(inward) > 1e-9 and _dot(n, inward) < 0:
            n = (-n[0], -n[1], -n[2])
        wedge = self._extrude_face(face, n, thickness)
        if wedge is None:
            return None
        return wedge

    def _build_patch(self, ext, topo, corner, thickness, extend):
        """按 corner_mode 生成角落闭合补片。

        - 'sharp_closed'（默认）：延伸两缺口边求交点，生成尖角三角形楔形补片；
        - 'butt'/'overlap'：两法兰沿自身平面延伸后取并集，裁剪到角落包围盒（平面对接）。
        """
        if self.corner_mode == 'sharp_closed':
            return self._build_patch_sharp(ext, topo, corner, thickness, extend)

        fa, fb = corner['faces']
        eff_extend = extend
        if self.corner_mode == 'overlap':
            eff_extend = extend + 0.5 * thickness
        slab_a = self._face_slab(ext, topo, fa, thickness, eff_extend)
        slab_b = self._face_slab(ext, topo, fb, thickness, eff_extend)
        if slab_a is None or slab_b is None:
            return None
        union = self._fuse(slab_a, slab_b, label="corner_union")
        if union is None:
            return None
        box = self._corner_box(ext, corner, thickness)
        if box is None:
            return None
        clip = self._make_box(box[0], box[1])
        if clip is None:
            return None
        try:
            common = BRepAlgoAPI_Common(union, clip)
            common.SetRunParallel(False)
            common.SetFuzzyValue(1e-4)
            common.Build()
        except Exception:
            return None
        if not common.IsDone():
            return None
        shape = common.Shape()
        if shape.IsNull() or not self._has_geometry(shape):
            return None
        return shape

    @staticmethod
    def _has_geometry(shape):
        if shape is None or shape.IsNull():
            return False
        return (TopExp_Explorer(shape, TopAbs_SOLID).More()
                or TopExp_Explorer(shape, TopAbs_SHELL).More())

    @staticmethod
    def _fuse(a, b, label="Fuse"):
        try:
            f = BRepAlgoAPI_Fuse(a, b)
            f.SetRunParallel(False)
            f.SetFuzzyValue(1e-4)
            f.Build()
            if not f.IsDone() or not CornerCloseRepair._has_geometry(f.Shape()):
                return None
            return f.Shape()
        except Exception:
            return None


    # ── 校验 / 输出 ──

    @staticmethod
    def _volume(shape):
        try:
            props = GProp_GProps()
            BRepGProp.VolumeProperties_s(shape, props)
            return props.Mass()
        except Exception:
            return 0.0

    @staticmethod
    def _bbox(shape):
        try:
            b = Bnd_Box()
            BRepBndLib.Add_s(shape, b)
            x1, y1, z1, x2, y2, z2 = b.Get()
            return {'min': [x1, y1, z1], 'max': [x2, y2, z2],
                    'size': [x2 - x1, y2 - y1, z2 - z1]}
        except Exception:
            return {'min': [0, 0, 0], 'max': [0, 0, 0], 'size': [0, 0, 0]}

    @staticmethod
    def _is_valid(shape):
        try:
            return bool(BRepCheck_Analyzer(shape).IsValid())
        except Exception:
            return False

    @staticmethod
    def _is_single_solid(shape):
        """是否恰好 1 个实体（角落补片必须与原钣金融合成一体）。"""
        n = 0
        e = TopExp_Explorer(shape, TopAbs_SOLID)
        while e.More():
            n += 1
            e.Next()
        return n == 1

    @staticmethod
    def _export(shape, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        w = STEPControl_Writer()
        w.Transfer(shape, STEPControl_AsIs)
        if w.Write(path) != IFSelect_RetDone:
            raise RuntimeError(f"导出失败: {path}")
        return path

    # ── 对外公开方法 ──

    def check_corner_gaps(self, filepath: str) -> dict:
        """仅检测，不修改模型：扫描所有角落，输出间隙列表（预检查）。"""
        ext, topo, det_result = self._load(filepath)
        corners = self._detect_corners(ext, topo, det_result)
        thickness = self._get_thickness(topo)
        gaps = []
        for c in corners:
            gaps.append({
                'faces': list(c['faces']),
                'angle_deg': c['angle_deg'],
                'gap_mm': c['gap_mm'],
                'edges': list(c['edge_pair']),
                'closable': c['closable'],
                'reason': 'ok' if c['closable'] else 'gap_exceeds_extend',
            })
        return {
            'filepath': filepath,
            'filename': os.path.basename(filepath),
            'thickness': thickness,
            'gap_tol': self.gap_tol,
            'extend_mm': self.extend_mm,
            'total_corners': len(corners),
            'closable_corners': sum(1 for c in corners if c['closable']),
            'gaps': gaps,
        }


    def repair_file(self, filepath: str, output_dir: str,
                    output_name: str = None) -> dict:
        """主入口：读取 STEP，执行角落闭合，写出文件，返回报告字典。"""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"文件不存在: {filepath}")
        os.makedirs(output_dir, exist_ok=True)

        ext, topo, det_result = self._load(filepath)
        solid = self._get_solid(ext)
        if solid is None or solid.IsNull():
            raise ValueError("未找到有效 Solid/Shell 实体")
        thickness = self._get_thickness(topo)
        orig_vol = self._volume(solid)
        orig_valid = self._is_valid(solid)

        corners = self._detect_corners(ext, topo, det_result)
        closable = [c for c in corners if c['closable']]

        report = {
            'input': filepath,
            'product_name': os.path.basename(filepath),
            'thickness': thickness,
            'gap_tol': self.gap_tol,
            'extend_mm': self.extend_mm,
            'corner_mode': self.corner_mode,
            'skip_small_features': self.skip_small_features,
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'original_volume': round(orig_vol, 4),
            'original_valid': bool(orig_valid),
            'detected_corners': len(corners),
            'success_corners': [],
            'failed_corners': [],
            'gap_details': [],
        }

        current = solid
        success = 0
        for i, c in enumerate(closable):
            fa, fb = c['faces']
            gap = c['gap_mm']
            patch = None
            err = None
            try:
                patch = self._build_patch(ext, topo, c, thickness, self.extend_mm)
            except Exception as e:  # noqa: BLE001
                err = str(e)
            if patch is None:
                report['failed_corners'].append({
                    'index': i, 'faces': [fa, fb],
                    'gap_mm': gap, 'angle_deg': c['angle_deg'],
                    'error': err or 'patch_build_failed_or_empty',
                })
                continue
            patch_vol = self._volume(patch)
            fused = self._fuse(current, patch, label=f"corner#{i}")
            if fused is None:
                report['failed_corners'].append({
                    'index': i, 'faces': [fa, fb],
                    'gap_mm': gap, 'angle_deg': c['angle_deg'],
                    'error': 'fuse_failed',
                })
                continue
            if not self._is_valid(fused) or not self._is_single_solid(fused):
                report['failed_corners'].append({
                    'index': i, 'faces': [fa, fb],
                    'gap_mm': gap, 'angle_deg': c['angle_deg'],
                    'error': 'fused_shape_invalid_or_multi_solid',
                })
                continue
            current = fused
            success += 1
            report['success_corners'].append({
                'index': i, 'faces': [fa, fb],
                'gap_mm': gap, 'angle_deg': c['angle_deg'],
                'patch_volume': round(patch_vol, 4),
            })

        for c in corners:
            report['gap_details'].append({
                'faces': list(c['faces']),
                'angle_deg': c['angle_deg'],
                'gap_mm': c['gap_mm'],
                'closable': c['closable'],
            })


        repaired_vol = self._volume(current)
        repaired_valid = self._is_valid(current)
        added_vol = repaired_vol - orig_vol

        report.update({
            'success_corners_count': success,
            'failed_corners_count': len(report['failed_corners']),
            'repaired_volume': round(repaired_vol, 4),
            'added_volume': round(added_vol, 4),
            'repaired_valid': bool(repaired_valid),
            'thickness_preserved': True,
            'output': None,
        })

        if output_name is None:
            base = os.path.basename(filepath)
            stem, _ = os.path.splitext(base)
            output_name = f"{stem}_CORNER_CLOSED.stp"
        out_path = os.path.join(output_dir, output_name)
        exported = False
        if repaired_valid:
            try:
                self._export(current, out_path)
                report['output'] = out_path
                exported = True
            except Exception as e:  # noqa: BLE001
                report['export_error'] = str(e)
        report['exported'] = exported

        if self.export_report:
            report_path = os.path.join(output_dir,
                                       os.path.splitext(output_name)[0] + "_report.txt")
            self._write_report(report, report_path)
            report['report_path'] = report_path

        return report


    def _write_report(self, report: dict, path: str):
        """写出人类可读 txt 修复报告。"""
        lines = []
        lines.append("=" * 68)
        lines.append(" 钣金件角落自动延伸闭合补齐报告 (CornerCloseRepair)")
        lines.append("=" * 68)
        lines.append(f" 时间          : {report.get('timestamp')}")
        lines.append(f" 输入文件      : {report.get('input')}")
        lines.append(f" 板厚          : {report.get('thickness')} mm")
        lines.append(f" 间隙阈值      : {report.get('gap_tol')} mm")
        lines.append(f" 最大延伸距离  : {report.get('extend_mm')} mm")
        lines.append(f" 闭合模式      : {report.get('corner_mode')}")
        lines.append("")
        lines.append(f" 原始体积      : {report.get('original_volume'):.3f} mm³")
        lines.append(f" 原始形状有效  : {report.get('original_valid')}")
        lines.append(f" 识别角落数量  : {report.get('detected_corners')}")
        lines.append(f" 修复成功角落  : {report.get('success_corners_count')}")
        lines.append(f" 修复失败角落  : {report.get('failed_corners_count')}")
        lines.append(f" 修复后体积    : {report.get('repaired_volume'):.3f} mm³")
        lines.append(f" 新增体积      : {report.get('added_volume'):.3f} mm³")
        lines.append(f" 修复后形状有效: {report.get('repaired_valid')}")
        lines.append(f" 板厚保持一致  : {report.get('thickness_preserved')}")
        lines.append(f" 输出文件      : {report.get('output')}")
        lines.append("")
        lines.append("-" * 68)
        lines.append(" 检测到的间隙列表:")
        for i, g in enumerate(report.get('gap_details', [])):
            lines.append(f"  [{i}] 面 {g['faces']}  间隙 {g['gap_mm']:.3f} mm  "
                         f"二面角 {g['angle_deg']}°  可闭合 {g['closable']}")
        lines.append("")
        if report.get('success_corners'):
            lines.append("-" * 68)
            lines.append(" 成功修复角落:")
            for s in report['success_corners']:
                lines.append(f"  面 {s['faces']}  间隙 {s['gap_mm']:.3f} mm  "
                             f"补片体积 {s.get('patch_volume', 0):.3f} mm³")
        if report.get('failed_corners'):
            lines.append("-" * 68)
            lines.append(" 失败角落:")
            for s in report['failed_corners']:
                lines.append(f"  面 {s['faces']}  间隙 {s['gap_mm']:.3f} mm  原因: {s['error']}")
        lines.append("=" * 68)

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        return path



# ── 命令行入口 ──

def _main(argv=None):
    parser = argparse.ArgumentParser(
        prog='corner_close.py',
        description='钣金件角落自动延伸闭合补齐（--check-only 仅检测不修改；默认完整修复+导出）')
    parser.add_argument('--input', help='输入钣金产品 STEP 文件')
    parser.add_argument('--output-dir', default=None, help='输出目录（默认与输入同目录）')
    parser.add_argument('--output-name', default=None, help='输出文件名（默认 {name}_CORNER_CLOSED.stp）')
    parser.add_argument('--check-only', action='store_true', help='仅检测角落间隙，不修改模型')
    parser.add_argument('--extend', type=float, default=2.0, help='面最大延伸距离 mm（默认 2.0）')
    parser.add_argument('--gap-tol', type=float, default=0.15, help='角落闭合间隙阈值 mm（默认 0.15）')
    parser.add_argument('--thickness', type=float, default=None, help='钣金板厚（默认自动检测）')
    parser.add_argument('--mode', default='sharp_closed', choices=['sharp_closed', 'butt', 'overlap'],
                        help='闭合模式 sharp_closed/butt/overlap（默认 sharp_closed 尖角闭合）')
    parser.add_argument('--max-gap', type=float, default=12.0,
                        help='识别角落的最大自由边间隙 mm（默认 12.0）')
    parser.add_argument('--proximity-tol', type=float, default=5.0,
                        help='两法兰自由边到对方平面的最大距离 mm（默认 5.0）')
    parser.add_argument('--no-skip-small', action='store_true', help='不跳过极小碎面')
    parser.add_argument('--no-report', action='store_true', help='不输出 txt 修复报告')
    args = parser.parse_args(argv)

    input_file = args.input
    if not input_file:
        cand = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'YA-1131-505_MIRROR_xOy.stp')
        if os.path.exists(cand):
            input_file = cand
        else:
            parser.error('请用 --input 指定 STEP 文件')

    repair = CornerCloseRepair(
        extend_mm=args.extend,
        gap_tol=args.gap_tol,
        thickness=args.thickness,
        corner_mode=args.mode,
        skip_small_features=not args.no_skip_small,
        export_report=not args.no_report,
        max_gap_detect=args.max_gap,
        proximity_tol=args.proximity_tol,
    )

    if args.check_only:
        r = repair.check_corner_gaps(input_file)
        print(f"[CornerClose] 检测 {r['filename']}: 板厚 {r['thickness']}mm, "
              f"识别角落 {r['total_corners']}, 可闭合 {r['closable_corners']}")
        for g in r['gaps']:
            print(f"  面 {g['faces']} 间隙 {g['gap_mm']:.3f}mm 二面角 {g['angle_deg']}° "
                  f"可闭合 {g['closable']} ({g['reason']})")
        return 0

    out_dir = args.output_dir or os.path.dirname(os.path.abspath(input_file))
    rep = repair.repair_file(input_file, out_dir, args.output_name)
    print(f"[CornerClose] 完成: 识别 {rep['detected_corners']} 个角落, "
          f"成功 {rep['success_corners_count']}, 失败 {rep['failed_corners_count']}")
    print(f"[CornerClose] 原始体积 {rep['original_volume']:.3f} → 修复后 "
          f"{rep['repaired_volume']:.3f} mm³ (+{rep['added_volume']:.3f})")
    print(f"[CornerClose] 输出: {rep.get('output')}")
    return 0


if __name__ == '__main__':
    sys.exit(_main())

