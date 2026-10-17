# -*- coding: utf-8 -*-
"""
mold_contact_surface_extractor.py —— 提取【凸模/凹模 形变配合面】独立模块
====================================================================================

【模块定位】（用户 2026-09 修正逻辑）
  本模块不再从【生成的模具】中提取配合面，而是直接【根据产品】提取形变面：
  钣金产品的表面形变区域本身就是模具型腔/凸起台曲面的直接来源，因此只需读
  产品 STEP，从其自身表面提取形变配合面即可，无需先生成并修复模具。

【输入】产品 STEP 文件（如 YA-1131-505_MIRROR_xOy.stp / 原始产品 .stp）。

【几何定义】
  · 凸模 contact_punch_faces  = 产品【上表面】(upper)  —— 成型时与凸模凸起台完全贴合；
  · 凹模 contact_cavity_faces = 产品【下表面】(lower)  —— 成型时与凹模型腔完全贴合。

【上下表面识别 = 投影法（独立模块 product_surface_classifier.py）】
  · 上表面 = 产品面沿 +Z 向【较高面 A】(z=Zmax+margin) 投影出面积者；
  · 下表面 = 产品面沿 -Z 向【较低面 B】(z=Zmin-margin) 投影出面积者；
  · 竖直/近竖直侧壁 |nz|≤0.1（Z 向投影近 0；剔除边缘近竖直微斜小片）归 side 排除。

【复用】
  · product_surface_classifier.classify_product_surfaces（投影法分上/下表面）；
  · mold_fix.MoldFixer._extract_solids / _bbox_tuple / _face_area / _count_faces /
    _make_compound / _export。

【输出】
  ① 内存数据结构：faces 列表（face index / 面积 / 曲面类型 / 包围盒 / 覆盖率）；
  ② 导出 STEP：{stem}_PUNCH_CONTACT_FACES.stp / {stem}_CAVITY_CONTACT_FACES.stp
     （Compound，只含提取到的产品形变面，非实体；--no-export-step 跳过）；
  ③ 导出 JSON 报告：{stem}_PUNCH_CONTACT_FACES.json / {stem}_CAVITY_CONTACT_FACES.json
     以及汇总 contact_surface_report.json + --show-summary 控制台报告。

【CLI 示例】
  # 同时提取凸凹模配合形变面（同一产品文件分别取上/下表面）
  python mold_contact_surface_extractor.py --punch YA-1131-505_MIRROR_xOy.stp \
      --cavity YA-1238-01_MIRROR_xOy.stp --output-dir contact_surface_out
  # 只提取凸模（产品上表面）
  python mold_contact_surface_extractor.py --punch YA-1131-505_MIRROR_xOy.stp \
      --output-dir contact_surface_out
  # 只提取凹模（产品下表面）
  python mold_contact_surface_extractor.py --cavity YA-1131-505_MIRROR_xOy.stp \
      --output-dir contact_surface_out
  # 参数可选：--no-export-step 只输出json不保存step；--show-summary 打印控制台报告

退出码：0=成功；2=参数/文件错误；1=运行异常。
"""

import os
import sys
import json
import argparse

import cadquery as cq  # noqa: F401  (与项目其他模块保持一致，导入以启用 OCP 环境)

from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.GeomAbs import (
    GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere,
    GeomAbs_Torus, GeomAbs_BezierSurface, GeomAbs_BSplineSurface,
    GeomAbs_SurfaceOfRevolution, GeomAbs_SurfaceOfExtrusion,
    GeomAbs_OffsetSurface, GeomAbs_OtherSurface,
)

# 复用 mold_fix.py 的既有工具（模块带 __main__ 保护，导入安全）
from mold_fix import MoldFixer, install_safe_print

# 本模块 print 含 '²' 等 GBK 不可编码字符 —— 打印抛异常会中断几何流程。
install_safe_print(globals())

DEFAULT_TOL = 0.1
DEFAULT_RATIO = 0.6


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
# 面几何信息工具
# ---------------------------------------------------------------------

def _surface_type_str(face):
    """返回面的 OCC 曲面类型字符串（plane/cylinder/...）。"""
    try:
        adapt = BRepAdaptor_Surface(TopoDS.Face_s(face))
        t = adapt.GetType()
    except Exception:
        return "unknown"
    table = {
        GeomAbs_Plane: "plane",
        GeomAbs_Cylinder: "cylinder",
        GeomAbs_Cone: "cone",
        GeomAbs_Sphere: "sphere",
        GeomAbs_Torus: "torus",
        GeomAbs_BezierSurface: "bezier",
        GeomAbs_BSplineSurface: "bspline",
        GeomAbs_SurfaceOfRevolution: "revolution",
        GeomAbs_SurfaceOfExtrusion: "extrusion",
        GeomAbs_OffsetSurface: "offset",
        GeomAbs_OtherSurface: "other",
    }
    for key, name in table.items():
        try:
            if t == key:
                return name
        except Exception:
            continue
    return "other"


def _face_list(shape):
    """枚举拓扑形状的所有 Face，返回 ([(idx, TopoDS_Face)], 总数)。"""
    faces = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    idx = 0
    while exp.More():
        faces.append((idx, TopoDS.Face_s(exp.Current())))
        idx += 1
        exp.Next()
    return faces, idx


def _bbox_overlap_any(a, b, tol):
    """两包围盒 (xmin,ymin,zmin,xmax,ymax,zmax) 外扩 tol 后是否三轴都重叠。"""
    return (a[0] - tol < b[3] and b[0] - tol < a[3] and
            a[1] - tol < b[4] and b[1] - tol < a[4] and
            a[2] - tol < b[5] and b[2] - tol < a[5])


def _face_min_dist(face_a, face_b):
    """两片之间最小距离（BRepExtrema_DistShapeShape）。失败返回 None。"""
    try:
        dss = BRepExtrema_DistShapeShape(face_a, face_b)
        dss.Perform()
        if dss.IsDone():
            return float(dss.Value())
    except Exception:
        pass
    return None


def _common_area(face_a, face_b, fuzz=1e-5):
    """两片布尔求交区域的【面积】。失败/空返回 0.0。"""
    try:
        op = BRepAlgoAPI_Common(face_a, face_b)
        op.SetRunParallel(False)
        op.SetFuzzyValue(fuzz)
        op.Build()
        if not op.IsDone():
            return 0.0
        shape = op.Shape()
        if shape is None or shape.IsNull():
            return 0.0
        total = 0.0
        exp = TopExp_Explorer(shape, TopAbs_FACE)
        while exp.More():
            total += MoldFixer._face_area(exp.Current())
            exp.Next()
        return total
    except Exception:
        return 0.0


# ---------------------------------------------------------------------
# 主提取器
# ---------------------------------------------------------------------

class MoldContactSurfaceExtractor:
    """凸模/凹模形变配合面提取器（【根据产品】提取）。

    用法：
        ex = MoldContactSurfaceExtractor()
        res = ex.extract_punch("YA-1131-505_MIRROR_xOy.stp")   # 产品上表面
        ex.export_step(res, "out/xxx_PUNCH_CONTACT_FACES.stp")
        ex.write_json(res, "out/xxx_PUNCH_CONTACT_FACES.json")
    """

    def __init__(self, tol=DEFAULT_TOL, ratio=DEFAULT_RATIO, fuzz=1e-5):
        self.tol = float(tol)
        self.ratio = float(ratio)
        self.fuzz = float(fuzz)

    # ---------- 实体识别 ----------

    def _load_product(self, filepath):
        """读取产品 STEP，返回 (product_shape, solid_infos, face_count)。

        product_shape 为全部独立 Solid/Shell 合成的 compound，可整体做上下表面分类。
        （用户修正：直接【根据产品】提取形变面，不再读取生成的模具。）
        """
        solids = MoldFixer._extract_solids(filepath)
        solid_infos = self._solid_infos(solids)
        shapes = [s for _, s in solids]
        product = MoldFixer._make_compound(shapes) if len(shapes) > 1 else shapes[0]
        face_count = MoldFixer._count_faces(product)
        return product, solid_infos, face_count

    def _classify(self, product):
        """用【投影法】分离产品上/下表面（独立模块 product_surface_classifier）。

        上表面 = 沿 +Z 向「较高面 A」（z=Zmax+margin）能投影出面积的面；
        下表面 = 沿 -Z 向「较低面 B」（z=Zmin-margin）能投影出面积的面。
        返回带索引的 [(idx, TopoDS_Face)] 与分类结果字典。
        """
        from product_surface_classifier import classify_product_surfaces
        res = classify_product_surfaces(product)
        print(f"[上下表面·投影法] 面A(z)={res['plane_A_z']} / 面B(z)={res['plane_B_z']}，"
              f"上表面 U={len(res['upper_faces'])} / 下表面 L={len(res['lower_faces'])}")
        upper = [(i, TopoDS.Face_s(f)) for i, f in res['upper_faces']]
        lower = [(i, TopoDS.Face_s(f)) for i, f in res['lower_faces']]
        return upper, lower, res

    def _faces_result(self, faces):
        """直接把产品目标面集（上/下表面）构造成 face 诊断列表。

        由于面就取自产品本身（形变面即模具配合曲面的来源），每片都是 100%
        贴合判定（is_contact=True、coverage=1.0），无需再与模具布尔求交。
        faces: [(idx, TopoDS_Face)]
        """
        results = []
        for fidx, f in faces:
            area = MoldFixer._face_area(f)
            fb = MoldFixer._bbox_tuple(f, f"face-{fidx}")
            results.append({
                'index': fidx,
                'area': round(area, 4),
                'surface_type': _surface_type_str(f),
                'bbox': [round(v, 4) for v in fb],
                'covered': round(area, 4),
                'ratio': 1.0,
                'is_contact': True,
                'matched_product_faces': [{'index': fidx,
                                           'overlap_area': round(area, 4),
                                           'dist': 0.0}],
            })
        return results

    # ---------- 组装结果 ----------

    def _solid_infos(self, solids):
        infos = []
        for i, (vol, s) in enumerate(solids):
            infos.append({
                'index': i,
                'volume': round(vol, 4),
                'bbox': [round(v, 4) for v in MoldFixer._bbox_tuple(s, f"solid-{i}")],
            })
        return infos

    def _build_result(self, kind, filepath, solid_infos, face_count,
                      upper_count, lower_count, face_results, product_faces,
                      classifier=None):
        contact_faces = [r for r in face_results if r['is_contact']]
        res = {
            'kind': kind,
            'input': filepath,
            'tol': self.tol,
            'ratio': self.ratio,
            'solids': solid_infos,
            'product_index': 0,
            'mold_indices': [],
            'product_face_count': face_count,
            'product_upper_count': upper_count,
            'product_lower_count': lower_count,
            'face_count': len(face_results),
            'contact_faces': contact_faces,
            'contact_face_count': len(contact_faces),
            'total_contact_area': round(sum(r['area'] for r in contact_faces), 4),
            'all_faces': face_results,
            '_product_faces': [TopoDS.Face_s(f) for f in product_faces],  # 仅供 STEP 导出，勿 JSON
        }
        if classifier is not None:
            res['surface_classifier'] = {
                'plane_A_z': classifier['plane_A_z'],
                'plane_B_z': classifier['plane_B_z'],
                'margin': classifier['margin'],
                'face_count': classifier['face_count'],
                'upper_count': len(classifier['upper_faces']),
                'lower_count': len(classifier['lower_faces']),
            }
        return res

    # ---------- 提取主入口 ----------

    def extract_punch(self, filepath):
        """根据产品提取凸模形变配合面 = 产品【上表面】(upper)。

        产品上表面即成型时与凸模凸起台完全贴合的形变面，直接取自产品本身。
        """
        product, solid_infos, face_count = self._load_product(filepath)
        upper, lower, _cls = self._classify(product)
        print(f"[提取凸模] 产品面分类：上表面 U={len(upper)} / 下表面 L={len(lower)}")
        faces = self._faces_result(upper)
        return self._build_result('punch', filepath, solid_infos, face_count,
                                  len(upper), len(lower), faces, [f for _, f in upper],
                                  _cls)

    def extract_cavity(self, filepath):
        """根据产品提取凹模形变配合面 = 产品【下表面】(lower)。

        产品下表面即成型时与凹模型腔完全贴合的形变面，直接取自产品本身。
        """
        product, solid_infos, face_count = self._load_product(filepath)
        upper, lower, _cls = self._classify(product)
        print(f"[提取凹模] 产品面分类：上表面 U={len(upper)} / 下表面 L={len(lower)}")
        faces = self._faces_result(lower)
        return self._build_result('cavity', filepath, solid_infos, face_count,
                                  len(upper), len(lower), faces, [f for _, f in lower],
                                  _cls)

    # ---------- 提取 contact face 的 TopoDS（供导出 STEP） ----------

    def result_faces_shape(self, result):
        """从结果取 contact_faces 的 TopoDS_Face，组合成 Compound（仅面，非实体）。"""
        faces = list(result.get('_product_faces', []))
        if not faces:
            return None
        return MoldFixer._make_compound(faces)

    # ---------- 导出 ----------

    def export_step(self, result, path):
        """把结果中的配合面保存为 STEP（Compound，仅面，非实体）。"""
        shape = self.result_faces_shape(result)
        if shape is None:
            print(f"  {result['kind']} 无配合面可导出，跳过: {path}")
            return None
        MoldFixer._export(shape, path, as_compound=True)
        print(f"  导出配合面 STEP: {path}")
        return path

    def write_json(self, result, path):
        """保存单目标（凸模/凹模）JSON 报告。"""
        payload = {
            'schema': 1,
            'kind': result['kind'],
            'input': result['input'],
            'tolerance_mm': result['tol'],
            'coverage_ratio_threshold': result['ratio'],
            'solids': result['solids'],
            'product_index': result['product_index'],
            'mold_indices': result['mold_indices'],
            'product_upper_count': result['product_upper_count'],
            'product_lower_count': result['product_lower_count'],
            'contact_face_count': result['contact_face_count'],
            'total_contact_area_mm2': result['total_contact_area'],
            'faces': result['contact_faces'],
        }
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"  导出 JSON 报告: {path}")
        return path

    def write_combined_report(self, results, path):
        """保存凸/凹模合并汇总报告。"""
        summary = {
            'schema': 1,
            'tolerance_mm': self.tol,
            'coverage_ratio_threshold': self.ratio,
            'items': [
                {
                    'kind': r['kind'],
                    'input': r['input'],
                    'contact_face_count': r['contact_face_count'],
                    'total_contact_area_mm2': r['total_contact_area'],
                    'product_upper_count': r['product_upper_count'],
                    'product_lower_count': r['product_lower_count'],
                }
                for r in results
            ],
        }
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"  导出汇总 JSON: {path}")
        return path

    def print_summary(self, result):
        """控制台打印结论。"""
        print("\n" + "=" * 66)
        kind = '凸模' if result['kind'] == 'punch' else '凹模'
        print(f"  {kind} 形变配合面提取结果  [tol={self.tol}mm, ratio={self.ratio}]")
        print("=" * 66)
        print(f"  输入        : {os.path.basename(result['input'])}")
        print(f"  配合面数量  : {result['contact_face_count']}")
        print(f"  总贴合面积  : {result['total_contact_area']:.4f} mm²")
        print(f"  产品面上表面: {result['product_upper_count']} 片 / "
              f"下表面: {result['product_lower_count']} 片")
        print("-" * 66)
        for r in result['contact_faces']:
            print(f"    #{r['index']:>3}  {r['surface_type']:<12} "
                  f"面积={r['area']:.4f} 覆盖率={r['ratio']*100:6.2f}%  "
                  f"bbox={tuple(r['bbox'])}")
        print("=" * 66)


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def _stem_from(filename, suffix):
    """去掉模板后缀（大小写不敏感），返回业务标识前缀 xxx。

    例如 'abc_PUNCH_FILLED.stp' + '_PUNCH_FILLED' -> 'abc'。
    """
    base = os.path.splitext(filename)[0]
    if base.upper().endswith(suffix.upper()):
        return base[:len(base) - len(suffix)]
    return base


def _output_paths(input_file, kind, output_dir, no_export_step):
    """计算 STEP / JSON 输出路径。返回 (step_path, json_path)。

    输入现在为【产品】文件，_stem_from 不会匹配多余后缀，直接取文件名前缀。
    """
    os.makedirs(output_dir, exist_ok=True)
    if kind == 'punch':
        stem = _stem_from(os.path.basename(input_file), '_PUNCH_FILLED')
        step_name = f"{stem}_PUNCH_CONTACT_FACES.stp"
        json_name = f"{stem}_PUNCH_CONTACT_FACES.json"
    else:
        stem = _stem_from(os.path.basename(input_file), '_CAVITY_HOLLOWED')
        step_name = f"{stem}_CAVITY_CONTACT_FACES.stp"
        json_name = f"{stem}_CAVITY_CONTACT_FACES.json"
    step_path = None if no_export_step else os.path.join(output_dir, step_name)
    json_path = os.path.join(output_dir, json_name)
    return step_path, json_path


def main(argv=None):
    _force_utf8()
    parser = argparse.ArgumentParser(
        description="【根据产品】提取形变配合面：凸模=产品上表面 / 凹模=产品下表面。")
    parser.add_argument('--punch', default=None, help='【产品】STP/STEP（提取其上表面=凸模配合面）')
    parser.add_argument('--cavity', default=None, help='【产品】STP/STEP（提取其下表面=凹模配合面）')
    parser.add_argument('--output-dir', default='contact_surface_out', help='输出目录')
    parser.add_argument('--tol', type=float, default=DEFAULT_TOL,
                        help='保留参数（默认 0.1，新逻辑恒全额贴合，不再使用）')
    parser.add_argument('--ratio', type=float, default=DEFAULT_RATIO,
                        help='保留参数（默认 0.6，新逻辑恒 100%，不再使用）')
    parser.add_argument('--no-export-step', action='store_true',
                        help='只输出 json，不保存 STEP')
    parser.add_argument('--show-summary', action='store_true',
                        help='控制台打印每个目标的结论报告')
    args = parser.parse_args(argv)

    if not args.punch and not args.cavity:
        print("错误：必须提供 --punch 和/或 --cavity（至少一个产品文件）。")
        return 2
    try:
        os.makedirs(args.output_dir, exist_ok=True)
    except OSError as e:
        print(f"错误：无法创建输出目录 {args.output_dir}: {e}")
        return 2

    ex = MoldContactSurfaceExtractor(tol=args.tol, ratio=args.ratio)
    results = []
    try:
        if args.punch:
            if not os.path.exists(args.punch):
                print(f"错误：产品文件不存在: {args.punch}")
                return 2
            print(f"[1/2] 提取凸模配合面（产品上表面）: {args.punch}")
            res = ex.extract_punch(args.punch)
            step_path, json_path = _output_paths(args.punch, 'punch', args.output_dir,
                                                 args.no_export_step)
            if step_path:
                ex.export_step(res, step_path)
            ex.write_json(res, json_path)
            results.append(res)

        if args.cavity:
            if not os.path.exists(args.cavity):
                print(f"错误：产品文件不存在: {args.cavity}")
                return 2
            print(f"[2/2] 提取凹模配合面（产品下表面）: {args.cavity}")
            res = ex.extract_cavity(args.cavity)
            step_path, json_path = _output_paths(args.cavity, 'cavity', args.output_dir,
                                                 args.no_export_step)
            if step_path:
                ex.export_step(res, step_path)
            ex.write_json(res, json_path)
            results.append(res)

        if results:
            ex.write_combined_report(results, os.path.join(args.output_dir,
                                                          "contact_surface_report.json"))
        if args.show_summary:
            for res in results:
                ex.print_summary(res)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"运行异常：{e}")
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())





