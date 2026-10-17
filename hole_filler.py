"""
孔洞补齐模块 (hole_filler.py)
基于 OpenCASCADE (CadQuery / OCP bindings)

功能：自动识别导入模型上的所有孔洞（圆形贯穿孔/盲孔），
      自动生成与钣金两面【平齐、无超出】的补实体，Fuse 后导出。

识别原理：
  · 遍历所有【完整圆柱面】（360° 闭合圆周，GeomAbs_Cylinder 且 U 参数跨度≈2π）——
    这是【孔壁】的几何特征（折弯圆角是部分圆柱弧、外凸台虽为完整圆柱但填充后
    为无害空操作，不会改变几何）。
  · 对每个孔壁圆柱面，取其包围盒沿轴线投影，得到两端面位置 t_min/t_max
    （= 法兰面/孔底），构造一个恰好贴合两端面的圆柱补实体：
        高度 = t_max - t_min，底面/顶面落于两端平面 → 与钣金面平齐、不超出。
  · 用 BRepAlgoAPI_Fuse 将补实体融合进原实体并导出 STEP。
"""

import os
import math
import cadquery as cq
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.BRepTools import BRepTools
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.gp import gp_Pnt, gp_Dir, gp_Ax2
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.TopTools import TopTools_ListOfShape
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
from OCP.IFSelect import IFSelect_RetDone


class HoleFiller:
    """
    自动识别并补齐模型上的所有孔洞（补齐至平面、无超出）。

    使用示例：
        hf = HoleFiller()
        r = hf.fill('model.stp', output_dir='mold_output')
        # r = {'output_path', 'holes': [...], 'filled_count', 'bbox', ...}
    """

    # 完整圆周公差（U 参数跨度 ≈ 2π）
    FULL_TOL = 0.05

    def __init__(self):
        self._shape = None
        self._solid = None

    # ------------------------------------------------------------------ #
    # 加载
    # ------------------------------------------------------------------ #
    def load(self, filepath: str):
        """加载 STEP 文件，提取第一个 Solid。"""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"文件不存在: {filepath}")
        result = cq.importers.importStep(filepath)
        shapes = result.vals() if hasattr(result, 'vals') else []
        if not shapes:
            raise ValueError("STEP 文件未读取到几何体")
        solid = None
        n_solids = 0
        for shp in shapes:
            exp = TopExp_Explorer(shp.wrapped, TopAbs_SOLID)
            while exp.More():
                n_solids += 1
                if solid is None:
                    solid = TopoDS.Solid_s(exp.Current())
                exp.Next()
        if solid is None:
            raise ValueError("未找到 Solid 实体")
        # B4：多实体（焊接件/组件）时其余实体曾被静默丢失。
        # 补齐流程按单实体设计，这里显式告警而非静默继续。
        if n_solids > 1:
            print(f"[警告] 文件含 {n_solids} 个 Solid，孔洞补齐仅作用于第一个；"
                  f"多实体产品请先合并或分别处理")
        self._solid = solid
        self._shape = solid
        return solid

    # ------------------------------------------------------------------ #
    # 检测
    # ------------------------------------------------------------------ #
    @staticmethod
    def _cyl_face_info(face):
        """若 face 为圆柱面，返回 {radius, axis_dir, loc, u_span, t_min, t_max}；
        非圆柱面返回 None。"""
        try:
            adaptor = BRepAdaptor_Surface(face)
            if adaptor.GetType() != GeomAbs_Cylinder:
                return None
            cyl = adaptor.Cylinder()
            radius = cyl.Radius()
            axis = cyl.Axis()
            u1, u2, _v1, _v2 = BRepTools.UVBounds_s(face)
            u_span = u2 - u1

            b = Bnd_Box()
            BRepBndLib.Add_s(face, b)
            x1, y1, z1, x2, y2, z2 = b.Get()
            corners = [(x, y, z) for x in (x1, x2)
                       for y in (y1, y2) for z in (z1, z2)]
            d = axis.Direction()
            L = axis.Location()
            tvals = [(q[0] - L.X()) * d.X() + (q[1] - L.Y()) * d.Y() +
                     (q[2] - L.Z()) * d.Z() for q in corners]
            return {
                'radius': radius,
                'axis_dir': d.Coord(),
                'loc': (L.X(), L.Y(), L.Z()),
                'u_span': u_span,
                't_min': min(tvals),
                't_max': max(tvals),
            }
        except Exception:
            return None

    def detect_holes(self):
        """识别所有孔洞。返回候选列表，每项为孔壁面信息（已去重）。"""
        if self._shape is None:
            raise RuntimeError("尚未加载模型，请先调用 load()")
        raw = []
        exp = TopExp_Explorer(self._shape, TopAbs_FACE)
        while exp.More():
            face = TopoDS.Face_s(exp.Current())
            info = self._cyl_face_info(face)
            if info:
                raw.append(info)
            exp.Next()

        # 只保留"完整圆周"圆柱面（孔壁特征：U 跨度 ≈ 2π）
        candidates = [c for c in raw if c['u_span'] >= (2 * math.pi - self.FULL_TOL)]

        # 去重：同一孔壁（同轴线、同半径、t 区间重叠）只保留一个
        # B3：原实现只比较 radius/t_min/t_max/axis_dir，不比较轴线【位置】，
        # 两块板上同轴、等半径、t 区间相同的平行孔阵被判为重复，只保留
        # 第一个孔 → 孔阵系统性漏填。加入轴线间距判据（两轴线间距
        # < 1e-3 才算同一孔）。
        holes = []
        for c in sorted(candidates, key=lambda x: (-x['radius'], x['t_min'])):
            dup = False
            for h in holes:
                if (abs(h['radius'] - c['radius']) < 1e-3 and
                        abs(h['t_min'] - c['t_min']) < 1e-3 and
                        abs(h['t_max'] - c['t_max']) < 1e-3 and
                        _dir_equal(h['axis_dir'], c['axis_dir']) and
                        _axis_gap(h['loc'], c['loc'], c['axis_dir']) < 1e-3):
                    dup = True
                    break
            if not dup:
                holes.append(c)
        return holes

    # ------------------------------------------------------------------ #
    # 补齐
    # ------------------------------------------------------------------ #
    @staticmethod
    def _make_plug(info):
        """由孔壁信息构造贴合两端面的圆柱补实体（平齐、无超出）。"""
        axis_dir = gp_Dir(*info['axis_dir'])
        loc = gp_Pnt(*info['loc'])
        base = gp_Pnt(loc.X() + axis_dir.X() * info['t_min'],
                      loc.Y() + axis_dir.Y() * info['t_min'],
                      loc.Z() + axis_dir.Z() * info['t_min'])
        height = info['t_max'] - info['t_min']
        if height <= 1e-8:
            return None
        ax2 = gp_Ax2(base, axis_dir)
        return BRepPrimAPI_MakeCylinder(ax2, info['radius'], height).Shape()

    def _fuse(self, a, b):
        try:
            f = BRepAlgoAPI_Fuse(a, b)
            f.SetRunParallel(False)
            f.Build()
            if not f.IsDone():
                return None
            return f.Shape()
        except Exception:
            return None

    def _fuse_many(self, base, plugs):
        """P13：base 与整批 plug 一次性布尔融合。

        原实现每孔都对持续增大的实体做一次完整布尔，1000 孔场景
        平方级增长。plug 互不重叠（同轴孔已去重），可批量融合；
        单批失败由调用方退化为批内逐孔 fuse。
        """
        try:
            op = BRepAlgoAPI_Fuse()
            args = TopTools_ListOfShape()
            args.Append(base)
            op.SetArguments(args)
            tools = TopTools_ListOfShape()
            for p in plugs:
                tools.Append(p)
            op.SetTools(tools)
            op.SetRunParallel(False)
            op.SetFuzzyValue(1e-5)
            op.Build()
            if not op.IsDone():
                return None
            return op.Shape()
        except Exception:
            return None

    @staticmethod
    def _bbox(shape):
        b = Bnd_Box()
        BRepBndLib.Add_s(shape, b)
        x1, y1, z1, x2, y2, z2 = b.Get()
        return {
            'min': [x1, y1, z1], 'max': [x2, y2, z2],
            'size': [x2 - x1, y2 - y1, z2 - z1],
        }

    @staticmethod
    def _volume(shape):
        try:
            p = GProp_GProps()
            BRepGProp.VolumeProperties_s(shape, p)
            return p.Mass()
        except Exception:
            return None

    @staticmethod
    def _is_valid(shape):
        try:
            return BRepCheck_Analyzer(shape).IsValid()
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #
    def fill(self, filepath: str, output_dir: str = None,
             product_name: str = None, max_holes: int = 1000):
        """
        加载 STEP 文件并补齐所有孔洞，导出为新的 STEP 文件。

        Args:
            filepath: 输入 STEP 文件路径
            output_dir: 输出目录（默认与源文件同目录）
            product_name: 输出文件名基础名（不含扩展名）；默认取文件基本名
            max_holes: 单次最多补齐孔数（防异常模型卡死）

        Returns:
            {
                'output_path': str,        # 补齐后 STEP 文件路径
                'holes': [ {...}, ... ],   # 每个孔 {radius, diameter, axis, t_min, t_max}
                'hole_count': int,         # 识别到的孔数
                'filled_count': int,       # 成功补齐数
                'skipped_count': int,      # 因布尔失败跳过的孔数
                'volume_before': float,
                'volume_after': float,
                'bbox': {...},             # 补齐后包围盒
                'valid': bool,             # BRepCheck 有效
            }
        """
        self.load(filepath)

        if product_name:
            name = product_name
        else:
            name = os.path.splitext(os.path.basename(filepath))[0]
        if not output_dir:
            output_dir = os.path.dirname(os.path.abspath(filepath))
        os.makedirs(output_dir, exist_ok=True)

        vol_before = self._volume(self._shape)
        holes = self.detect_holes()[:max_holes]

        result_shape = self._shape
        filled = 0
        skipped = 0
        # P13：先构造全部 plug，再分批批量融合（每批一次布尔），
        # 避免"每孔对持续增大的实体做完整布尔"的平方级开销。
        plugs = []
        for info in holes:
            plug = self._make_plug(info)
            if plug is not None:
                plugs.append(plug)

        BATCH = 64
        i = 0
        while i < len(plugs):
            batch = plugs[i:i + BATCH]
            i += BATCH
            if len(batch) == 1:
                merged = self._fuse(result_shape, batch[0])
                if merged is not None and self._is_valid(merged):
                    result_shape = merged
                    filled += 1
                else:
                    skipped += 1
                continue
            merged = self._fuse_many(result_shape, batch)
            if merged is not None and self._is_valid(merged):
                result_shape = merged
                filled += len(batch)
            else:
                # 批量失败 → 该批内退化为逐孔 fuse（保持原容错语义）
                for plug in batch:
                    m = self._fuse(result_shape, plug)
                    if m is not None and self._is_valid(m):
                        result_shape = m
                        filled += 1
                    else:
                        skipped += 1

        out_path = os.path.join(output_dir, f"{name}_HOLE_FILLED.stp")
        w = STEPControl_Writer()
        w.Transfer(result_shape, STEPControl_AsIs)
        if w.Write(out_path) != IFSelect_RetDone:
            raise RuntimeError(f"导出失败: {out_path}")

        hole_report = [{
            'radius': round(h['radius'], 3),
            'diameter': round(h['radius'] * 2, 3),
            'axis': [round(v, 3) for v in h['axis_dir']],
            't_min': round(h['t_min'], 3),
            't_max': round(h['t_max'], 3),
        } for h in holes]

        return {
            'output_path': out_path,
            'holes': hole_report,
            'hole_count': len(holes),
            'filled_count': filled,
            'skipped_count': len(holes) - filled,
            'volume_before': vol_before,
            'volume_after': self._volume(result_shape),
            'bbox': self._bbox(result_shape),
            'valid': self._is_valid(result_shape),
        }


def _dir_equal(a, b, tol=1e-3):
    """判断两个方向向量在 ± 方向意义上等价。"""
    dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
    return abs(abs(dot) - 1.0) < tol


def _axis_gap(loc_a, loc_b, axis_dir):
    """两平行轴线之间的最短距离（loc_a/loc_b 为轴线上点）。

    B3 配套：向量差投影到轴线方向后剩余分量即轴间距。
    """
    dx, dy, dz = (loc_a[0] - loc_b[0], loc_a[1] - loc_b[1], loc_a[2] - loc_b[2])
    d = axis_dir
    t = dx * d[0] + dy * d[1] + dz * d[2]
    px, py, pz = dx - t * d[0], dy - t * d[1], dz - t * d[2]
    return math.sqrt(px * px + py * py + pz * pz)


def _force_utf8():
    try:
        import sys
        if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


if __name__ == '__main__':
    _force_utf8()
    test_file = "YA-1238-01_MIRROR_xOy.stp"
    if not os.path.exists(test_file):
        test_file = os.path.join(os.path.dirname(__file__), test_file)
    if not os.path.exists(test_file):
        print("未找到测试文件 YA-1238-01_MIRROR_xOy.stp")
        raise SystemExit(1)

    out_dir = os.path.join(os.path.dirname(__file__), "mold_output")
    os.makedirs(out_dir, exist_ok=True)

    hf = HoleFiller()
    r = hf.fill(test_file, output_dir=out_dir)
    # B15：_volume 失败返回 None 时原 f"{None:.0f}" 直接崩，这里显式处理
    vb = r['volume_before']
    va = r['volume_after']
    print(f"\n[结果] 孔洞补齐: {r['output_path']}")
    print(f"  识别孔数: {r['hole_count']}  成功补齐: {r['filled_count']}"
          f"  跳过: {r['skipped_count']}")
    print(f"  体积: {vb:.0f} -> {va:.0f} mm³" if (vb is not None and va is not None)
          else f"  体积: {vb} -> {va} mm³（体积计算失败）")
    print(f"  有效: {r['valid']}  包围盒: {r['bbox']['size']}")
    for h in r['holes']:
        print(f"    Φ{h['diameter']}mm  轴={h['axis']}  t=[{h['t_min']},{h['t_max']}]")
