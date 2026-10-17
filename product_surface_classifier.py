# -*- coding: utf-8 -*-
"""
product_surface_classifier.py —— 按【投影法】识别钣金产品上/下表面的独立逻辑模块
====================================================================================

【定义】（用户 2026-09 修正）
  不再用"单点法向 Z>0/<=0"的粗糙判据，而是按【Z 轴投影】判定上下表面：

  · 设一个比产品顶部更高、平行于 xOy 的【较高面 A】（z = Zmax + margin）；
  · 设一个比产品底部更低、平行于 xOy 的【较低面 B】（z = Zmin - margin）；
  · 【上表面】= 产品面沿 +Z 向面 A 投影，能在面 A 上【投影出面积】的面；
  · 【下表面】= 产品面沿 -Z 向面 B 投影，能在面 B 上【投影出面积】的面。

【数学实现】
  一个面沿 ±Z 正交投影到某平行 xOy 平面上的面积 = 面面积 × |该面外法向的 Z 分量|：
    · 朝上分量 proj_top    = area × max( nz, 0 )   → 落在面 A 上的投影面积；
    · 朝下分量 proj_bottom = area × max(-nz, 0 )   → 落在面 B 上的投影面积。
  其中 nz 为该面【朝外单位法向】的 Z 分量，用参数域多点采样取平均（稳健，
  避免 BSpline/圆环面单点奇异导致的误判）。
  判定规则（nz_eps 默认 0.1）：
    · nz >  +nz_eps  → 上表面（朝上，能向上投影出面积）；
    · nz <  -nz_eps  → 下表面（朝下，能向下投影出面积）；
    · 其余          → 竖直侧壁（沿 Z 投影成线/环，投影面积为 0，归为 side，忽略）。

【独立性与复用】
  本模块只依赖 mold_fix.MoldFixer 的 _point_inside / _face_area / _bbox_tuple /
  _extract_solids / _make_compound，供 mold_contact_surface_extractor 调用。

【API】
  classify_product_surfaces(shape, margin=None, nz_eps=0.1, n_samples=3) -> dict
    返回 { plane_A_z, plane_B_z, margin, upper_faces, lower_faces,
           face_count, faces=[每面诊断] }
    其中 upper_faces / lower_faces 为 [(idx, TopoDS_Face)]。

【CLI 自测】
  python product_surface_classifier.py YA-1131-505_MIRROR_xOy.stp
    打印面A/面B 高度、上/下表面计数与分类明细。
"""

import os
import sys

import cadquery as cq  # noqa: F401

from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepLProp import BRepLProp_SLProps
from OCP.gp import gp_Pnt

from mold_fix import MoldFixer

DEFAULT_NZ_EPS = 0.1       # |nz| 阈值：低于此视为近竖直侧壁（Z 向投影近 0，剔除微斜边缘小面）
DEFAULT_MARGIN = None      # 面A/面B 相对 Zmax/Zmin 的外扩；None=自动


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


# ---------------------------------------------------------------------
# 朝外单位法向 Z 分量（多参数采样平均）
# ---------------------------------------------------------------------

def _adapt_u_v(adapt):
    """取参数域 [u1,u2]x[v1,v2]（无限参数用 [-1,1] 截断）。"""
    INF = 1e8
    u1, u2 = adapt.FirstUParameter(), adapt.LastUParameter()
    v1, v2 = adapt.FirstVParameter(), adapt.LastVParameter()
    if abs(u1) > INF: u1 = -1.0
    if abs(u2) > INF: u2 = 1.0
    if abs(v1) > INF: v1 = -1.0
    if abs(v2) > INF: v2 = 1.0
    if abs(u2 - u1) < 1e-12: u2 = u1 + 1.0
    if abs(v2 - v1) < 1e-12: v2 = v1 + 1.0
    return u1, u2, v1, v2
def _outer_nz_at(face, shape, um, vm):
    """返回面在参数点 (um,vm) 处的【朝外单位法向 Z 分量】，失败返回 None。

    用 BRepAdaptor_Surface + BRepLProp_SLProps 求面上点的法向，再借助
    SolidClassifier（MoldFixer._point_inside）把法向修正为朝外。
    """
    try:
        adapt = BRepAdaptor_Surface(TopoDS.Face_s(face))
        props = BRepLProp_SLProps(adapt, um, vm, 2, 1e-7)
        if not props.IsNormalDefined():
            return None
        pnt = props.Value()
        n = props.Normal()
        nx, ny, nz = n.X(), n.Y(), n.Z()
        mag = (nx * nx + ny * ny + nz * nz) ** 0.5
        if mag < 1e-12:
            return None
        nx, ny, nz = nx / mag, ny / mag, nz / mag
        eps = 0.1
        # 沿法向偏移一点，若落在实体内则说明该法向指向内 → 反转
        if MoldFixer._point_inside(
                shape, gp_Pnt(pnt.X() + nx * eps, pnt.Y() + ny * eps,
                              pnt.Z() + nz * eps), tol=1e-6):
            return -nz
        return nz
    except Exception:
        return None


def _face_avg_nz(face, shape, n_samples=3):
    """对一副面在参数域 n×n 采样，返回朝外单位法向 Z 分量平均值。

    返回 (nz_avg, n_valid)。全部失败时回退单点中心采样。
    """
    nz_vals = []
    try:
        adapt = BRepAdaptor_Surface(TopoDS.Face_s(face))
        u1, u2, v1, v2 = _adapt_u_v(adapt)
        for i in range(n_samples):
            uu = u1 + (u2 - u1) * (i + 0.5) / n_samples
            for j in range(n_samples):
                vv = v1 + (v2 - v1) * (j + 0.5) / n_samples
                nz = _outer_nz_at(face, shape, uu, vv)
                if nz is not None:
                    nz_vals.append(nz)
    except Exception:
        pass
    if not nz_vals:
        for (uu, vv) in [(0.5, 0.5), (0.25, 0.5), (0.75, 0.5)]:
            try:
                adapt = BRepAdaptor_Surface(TopoDS.Face_s(face))
                u1, u2, v1, v2 = _adapt_u_v(adapt)
                nz = _outer_nz_at(face, shape, u1 + (u2 - u1) * uu,
                                  v1 + (v2 - v1) * vv)
                if nz is not None:
                    nz_vals.append(nz)
                    break
            except Exception:
                continue
    if not nz_vals:
        return 0.0, 0
    return sum(nz_vals) / len(nz_vals), len(nz_vals)



def classify_product_surfaces(shape, margin=None, nz_eps=DEFAULT_NZ_EPS,
                              n_samples=3):
    """按【Z 轴投影】识别产品上/下表面。

    shape: 产品 shape（solid / compound）。
    margin: 面A/面B 相对 bbox.Zmax/Zmin 的外扩高度；None=自动（bboxZ范围 ×0.1，至少 1mm）。
    nz_eps: |nz| 阈值（默认 0.1），低于此视为近竖直侧壁（投影近 0，剔除边缘微斜小片）。
    n_samples: 每面参数域采样数（n×n，默认 3×3=9 点）。

    返回：
      {
        'plane_A_z': float,          # 较高面 A 高度（z = Zmax + margin）
        'plane_B_z': float,          # 较低面 B 高度（z = Zmin - margin）
        'margin': float,
        'face_count': int,
        'upper_faces': [(idx, TopoDS_Face)],
        'lower_faces': [(idx, TopoDS_Face)],
        'faces': [                   # 每面诊断
            {'index','area','nz','n_valid','proj_top_area','proj_bottom_area','cls'}
        ],
      }
    """
    # 产品包围盒 → 面A / 面B（平行 xOy 的假设面）
    xmin, ymin, zmin, xmax, ymax, zmax = MoldFixer._bbox_tuple(shape, "product")
    z_range = max(zmax - zmin, 1.0)
    if margin is None:
        margin = max(0.1 * z_range, 1.0)
    plane_A_z = zmax + margin
    plane_B_z = zmin - margin

    faces = []
    upper = []
    lower = []
    unknown = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    idx = 0
    while exp.More():
        face = exp.Current()
        area = MoldFixer._face_area(face)
        nz, n_valid = _face_avg_nz(face, shape, n_samples)
        # 沿 ±Z 正交投影到面A / 面B 的面积（= 面面积 × |Z向单位法向分量|）
        proj_top = area * max(nz, 0.0)
        proj_bottom = area * max(-nz, 0.0)
        # B15：采样失败（n_valid==0）与"真竖直侧壁"必须区分——前者是
        # 法向算不出来，不能静默当成 side 丢弃（真实上/下表面可能因此漏判）。
        if n_valid == 0:
            cls = 'unknown'        # 无法判定，暴露给调用方，不混入 upper/lower
        elif nz > nz_eps:
            cls = 'upper'
        elif nz < -nz_eps:
            cls = 'lower'
        else:
            cls = 'side'
        faces.append({
            'index': idx,
            'area': round(area, 4),
            'nz': round(nz, 6),
            'n_valid': n_valid,
            'proj_top_area': round(proj_top, 4),
            'proj_bottom_area': round(proj_bottom, 4),
            'cls': cls,
        })
        if cls == 'upper':
            upper.append((idx, TopoDS.Face_s(face)))
        elif cls == 'lower':
            lower.append((idx, TopoDS.Face_s(face)))
        elif cls == 'unknown':
            unknown.append((idx, TopoDS.Face_s(face)))
        idx += 1
        exp.Next()

    return {
        'plane_A_z': round(plane_A_z, 4),
        'plane_B_z': round(plane_B_z, 4),
        'margin': round(margin, 4),
        'face_count': idx,
        'upper_faces': upper,
        'lower_faces': lower,
        'unknown_faces': unknown,
        'faces': faces,
    }


# ---------------------------------------------------------------------
# CLI 自测
# ---------------------------------------------------------------------

def main(argv=None):
    _force_utf8()
    if not argv:
        argv = sys.argv[1:]
    if not argv:
        print("用法：python product_surface_classifier.py <产品.stp> [nz_eps] [margin]")
        return 2
    filepath = argv[0]
    if not os.path.exists(filepath):
        print(f"错误：文件不存在: {filepath}")
        return 2
    nz_eps = float(argv[1]) if len(argv) > 1 else DEFAULT_NZ_EPS
    margin = float(argv[2]) if len(argv) > 2 else DEFAULT_MARGIN

    solids = MoldFixer._extract_solids(filepath)
    shapes = [s for _, s in solids]
    shape = MoldFixer._make_compound(shapes) if len(shapes) > 1 else shapes[0]

    res = classify_product_surfaces(shape, margin=margin, nz_eps=nz_eps)
    print(f"产品面总数 : {res['face_count']}")
    print(f"面 A(z)    : {res['plane_A_z']}   (Zmax + {res['margin']})")
    print(f"面 B(z)    : {res['plane_B_z']}   (Zmin - {res['margin']})")
    print(f"上表面 U   : {len(res['upper_faces'])} 片（沿+Z投影面A有面积）")
    print(f"下表面 L   : {len(res['lower_faces'])} 片（沿-Z投影面B有面积）")
    print(f"未判定 ?   : {len(res.get('unknown_faces', []))} 片（法向采样失败，需人工确认）")
    print("-" * 64)
    label_map = {'upper': '上', 'lower': '下', 'side': '侧壁', 'unknown': '未知'}
    for f in res['faces']:
        print(f"  #{f['index']:>3}  {label_map[f['cls']]}  面积={f['area']:.4f}  "
              f"nz={f['nz']:+.3f}  面A投影={f['proj_top_area']:.3f}  "
              f"面B投影={f['proj_bottom_area']:.3f}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
