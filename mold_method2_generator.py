# -*- coding: utf-8 -*-
"""
mold_method2_generator.py —— ★模具生成法二★（独立新功能）
====================================================================================

【方向设定】（用户 2026-09）
  上方 = +Z；下方 = -Z；水平面方向 = 垂直于 Z 轴的方向（xOy 平面）。

【输入】上/下【形变面】STEP（由 mold_contact_surface_extractor 基于产品提取：
  {产品}_PUNCH_CONTACT_FACES.stp = 产品上表面；{产品}_CAVITY_CONTACT_FACES.stp = 产品下表面。
  均为 Compound，仅含面，非实体）。

【输出】
  上模 PUNCH_M2：导入上形变面 → 每点沿 +Z 拉伸填充至同一高度面 P（平面，平行 xOy；
                 P = 上形变面最低点 z + h1，h1 可设/自动推荐=上形变面高度）→ 顶面=平面 P、
                 底面=上形变面、侧面竖直 → 导出。下模 CAVITY_M2：见下。

【几何实现】
  上模：把上形变面各面沿 +Z 拉伸到远高于 P，再 Cut 掉 z>P 上半空间（顶面平齐 P）。
  下模：Cut(基座长方体, 挖空体)。
        挖空体 = Common(下形变面沿 +Z 拉伸空间 V_up, box[z_bottom..z_top])，
        其中 V_up=轮廓内、下形变面之上的空间；z_top=基座顶面=下形变面 zmin+h_cavity，
        z_bottom=下形变面 zmin（最低点恰好接触空腔底）。基座 xy 中心与下形变面中心对齐。

【CLI 示例】
  python mold_method2_generator.py --upper <产品>_PUNCH_CONTACT_FACES.stp \\
      --lower <产品>_CAVITY_CONTACT_FACES.stp --output-dir mold_method2
  # 可选 --h1 30 / --h-cavity 20 / --base-lx/-ly/-hz / --margin 5 / --no-export-step

退出码：0=成功；2=参数/文件错误；1=运行异常。
"""

import os
import sys
import json
import argparse

import cadquery as cq  # noqa: F401

from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Vec, gp_Pnt
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeBox
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse, BRepAlgoAPI_Cut, BRepAlgoAPI_Common
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepLProp import BRepLProp_SLProps
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.TopTools import TopTools_ListOfShape

from mold_fix import MoldFixer, install_safe_print

# 本模块 print 含 '−' / '³' 等 GBK 不可编码字符 —— 打印抛异常会中断几何流程。
# 安装 mold_fix 的安全 print，保证任何调用方式（CLI / 库 / Web）都不会因打印失败。
install_safe_print(globals())

DEFAULT_MARGIN = 5.0   # 基座至少超出产品/形变面 xy 边距（mm）

# 法二A 的形变面片数上限。
# ★实测（2026-09-11）★ 法二A 需要把每片形变面沿 Z 拉伸，再把结果当布尔操作数
# 使用。片数一多（1131 上表面 41 片 / 下表面 49 片），这些棱柱彼此**不相连**，
# 融合后是【多实体 compound】；OCC 在"多实体 + 共面重叠"的输入上会卡死
# （实测 Cut(盒,49个工具) / Common(多实体,盒) / 一次性并集 全部 >7~10 分钟，
#  顺序雕刻同样卡死）。因此超过该上限时**直接快速失败**，避免把 Web 服务卡住。
MAX_FACES_METHOD2A = 20


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
# 几何工具
# ---------------------------------------------------------------------

def _load_faces(filepath):
    """读取 STEP，返回该 shape 的所有 TopoDS_Face（形变面 Compound 仅含面）。"""
    result = cq.importers.importStep(filepath)
    vals = result.vals() if hasattr(result, 'vals') else [result]
    faces = []
    for v in vals:
        shp = v.wrapped if hasattr(v, 'wrapped') else v
        exp = TopExp_Explorer(shp, TopAbs_FACE)
        while exp.More():
            faces.append(TopoDS.Face_s(exp.Current()))
            exp.Next()
    if not faces:
        raise ValueError(f"未找到任何 Face（请用形变面 STEP：*_CONTACT_FACES.stp）：{filepath}")
    return faces

def _mix(faces):
    """多面合成 compound（供 bbox 用，不融合）。"""
    return MoldFixer._make_compound(faces)


def _bbox(shape, name="shape"):
    """包围盒 (xmin,ymin,zmin,xmax,ymax,zmax)。"""
    return MoldFixer._bbox_tuple(shape, name)


def _bbox_accurate(shape):
    """★真实★包围盒（不含 Bnd_Box 的容差扩张）。

    ★实测（2026-09-11）★ `MoldFixer._bbox_tuple` 走 `Bnd_Box` + `BRepBndLib.Add_s`，
    返回值会被形状容差扩张：一个真实顶面恰在 z=71.0009 的实体被报成 73.6275
    （虚高 **2.63mm**）。布尔结果的容差往往不小，用它做"顶面是否平齐 P"这类
    精确校验会**误判正确结果为失败**（方法二A 的 punch 就因此被错杀了很久）。
    精确校验一律用 `BRepBndLib.AddOptimal`。
    """
    try:
        b = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, b, False, False)
        if b.IsVoid():
            return _bbox(shape)
        return b.Get()
    except Exception:
        return _bbox(shape)


def _fuse(a, b, label="Fuse", fuzz=1e-3):
    """稳健融合，返回 (result_success, shape)。"""
    try:
        f = BRepAlgoAPI_Fuse(a, b)
        f.SetRunParallel(False)
        f.SetFuzzyValue(fuzz)
        f.Build()
        if f.IsDone() and not f.Shape().IsNull():
            return True, f.Shape()
    except Exception:
        pass
    return False, None


def _fuse_many(shapes, label="fuse-many", fuzz=1e-3):
    """把多个实体交给同一个 BOPAlgo 做并集（单次布尔）。

    ⚠⚠ 本机实测【不要用它做形变面棱柱的并集】⚠⚠（2026-09-11）
      面数多、且棱柱彼此大量共面重叠时（1131 下形变面 49 片），
      **BOPAlgo 会卡死 >7 分钟**；同样输入改为逐个 Fuse 只需约 50 秒。
      （上模 41 片时它反而只要 ~10s —— 行为不稳定，不可依赖。）
      形变面拉伸请用 `_stretch_up` 里的【逐个融合】路径。
    """
    shapes = [s for s in shapes if s is not None and not s.IsNull()]
    if not shapes:
        return None
    if len(shapes) == 1:
        return shapes[0]
    args = TopTools_ListOfShape()
    tools = TopTools_ListOfShape()
    args.Append(shapes[0])
    for s in shapes[1:]:
        tools.Append(s)
    try:
        algo = BRepAlgoAPI_Fuse()
        algo.SetArguments(args)
        algo.SetTools(tools)
        algo.SetFuzzyValue(fuzz)
        try:
            algo.SetRunParallel(True)
        except Exception:
            pass
        algo.Build()
        if algo.IsDone() and not algo.Shape().IsNull():
            return algo.Shape()
    except Exception:
        pass
    # 兜底：退回逐个 Fuse
    acc = shapes[0]
    for s in shapes[1:]:
        ok, merged = _fuse(acc, s, label, fuzz)
        if ok and merged is not None:
            acc = merged
    return acc


def _cut(a, b, label="Cut", fuzz=1e-3):
    """稳健布尔减，返回 (result_success, shape)。

    ⚠ 用 SetRunParallel(False)：并行布尔偶发产生错误结果，本项目一律串行。
    """
    try:
        c = BRepAlgoAPI_Cut(a, b)
        c.SetRunParallel(False)
        c.SetFuzzyValue(fuzz)
        c.Build()
        if c.IsDone() and not c.Shape().IsNull():
            return True, c.Shape()
    except Exception:
        pass
    return False, None


def _common(a, b, label="Common", fuzz=1e-3):
    """稳健布尔交，返回 (result_success, shape)。"""
    try:
        c = BRepAlgoAPI_Common(a, b)
        c.SetRunParallel(False)
        c.SetFuzzyValue(fuzz)
        c.Build()
        if c.IsDone() and not c.Shape().IsNull():
            return True, c.Shape()
    except Exception:
        pass
    return False, None


def _valid(shape):
    """BRepCheck 校验形状是否合法。"""
    try:
        if shape is None or shape.IsNull():
            return False
        return BRepCheck_Analyzer(shape).IsValid()
    except Exception:
        return False


def _make_box(lx, ly, cx, cy, z_lo, z_hi):
    """生成长方体：x∈[cx-lx/2,cx+lx/2]，y∈[cy-ly/2,cy+ly/2]，z∈[z_lo,z_hi]。"""
    box = BRepPrimAPI_MakeBox(lx, ly, max(z_hi - z_lo, 1e-6)).Shape()
    from OCP.gp import gp_Trsf
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(cx - lx / 2.0, cy - ly / 2.0, z_lo))
    return BRepBuilderAPI_Transform(box, t).Shape()


def _stretch_up(faces, z_end):
    """把若干面各自沿 +Z 拉伸到高度 z_end（顶面恰好在 z_end 附近）并融合。

    返回 (成功?, 拉伸融合体)。用于构造"形变面上方空间"（V_up）。

    ★两个关键修复★
    ① 2026-09-11：拉伸高度必须让【每个面拉伸体的顶】刚好落在 z_end，
       不能再叠加面自身高度。旧式 `big = (z_end-bb[2]) + |bb[5]-bb[2]| + 5`
       会让高处的面拉伸到远超 z_end 的位置（实测 2ROM 拉到 z=319，而裁顶盒
       只到 196）→ 裁顶盒根本盖不住，残留 248mm 的柱体。**这才是法二A慢的
       主因**：旧棱柱巨大，布尔自然慢。
    ② 2026-09-11：**必须用【逐个 Fuse】**，不能用 `_fuse_many` 一次性并集 ——
       实测 1131 下形变面 49 片的棱柱，一次性并集会卡死 >7 分钟，逐个 Fuse
       约 50 秒完成（每步 0.03~5s）。先造好全部棱柱再逐个融合，累加器不预先
       增长；实测每步耗时稳定，不会越滚越慢。
    """
    prisms = []
    for i, fc in enumerate(faces):
        bb = _bbox(fc, f"face-{i}")
        big = max((z_end - bb[5]) + 2.0, 1.0)   # 顶面 = bb[5] + big = z_end + 2
        try:
            prism = BRepPrimAPI_MakePrism(fc, gp_Vec(0.0, 0.0, big)).Shape()
        except Exception:
            continue
        if prism is not None and not prism.IsNull():
            prisms.append(prism)
    if not prisms:
        return False, None
    acc = prisms[0]
    for k, pr in enumerate(prisms[1:], 1):
        ok, merged = _fuse(acc, pr, f"stretch-{k}", fuzz=1e-3)
        if ok and merged is not None:
            acc = merged
    return True, acc


def _face_mid_point(face):
    """取面参数域中点上的一个真实点（失败返回 None）。

    用于判断竖直面下方是否有料：必须用【面上】的点，不能用包围盒中心
    —— 圆筒面（凸台外壁/孔壁）的包围盒中心落在轴线上，会误判。
    """
    try:
        adapt = BRepAdaptor_Surface(TopoDS.Face_s(face))
        u1, u2 = adapt.FirstUParameter(), adapt.LastUParameter()
        v1, v2 = adapt.FirstVParameter(), adapt.LastVParameter()
        INF = 1e8
        if abs(u1) > INF or abs(u2) > INF: u1, u2 = -1.0, 1.0
        if abs(v1) > INF or abs(v2) > INF: v1, v2 = -1.0, 1.0
        if abs(u2 - u1) < 1e-12: u2 = u1 + 1.0
        if abs(v2 - v1) < 1e-12: v2 = v1 + 1.0
        props = BRepLProp_SLProps(adapt, (u1 + u2) / 2.0, (v1 + v2) / 2.0, 0, 1e-7)
        return props.Value()
    except Exception:
        return None


def _clamp_to_box(shape, x1, y1, z1, x2, y2, z2, label="clamp"):
    """把 shape 裁进给定长方体（切掉盒外一切材料）。

    布尔（尤其对开放壳/薄壁）偶尔会把结果甩到包围盒之外（实测法二A 下模
    顶面超出基座顶面 +6mm）。本函数作为最后一道保险：Cut(shape, 盒外体)。
    失败/无变化时原样返回。
    """
    try:
        pad = max(x2 - x1, y2 - y1, z2 - z1) + 50.0
        outer = BRepPrimAPI_MakeBox(pad + (x2 - x1), pad + (y2 - y1),
                                    (z2 - z1) + 2 * pad).Shape()
        from OCP.gp import gp_Trsf, gp_Vec as _V
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        t = gp_Trsf()
        t.SetTranslation(_V(x1 - pad, y1 - pad, z1 - 2 * pad))
        outer = BRepBuilderAPI_Transform(outer, t).Shape()
        inner = _make_box(x2 - x1, y2 - y1, (x1 + x2) / 2.0, (y1 + y2) / 2.0, z1, z2)
        outside = BRepAlgoAPI_Cut(outer, inner)
        outside.SetRunParallel(False)
        outside.Build()
        if not outside.IsDone():
            return shape
        ok, clipped = _cut(shape, outside.Shape(), label, fuzz=1e-4)
        if ok and clipped is not None and not clipped.IsNull():
            return clipped
    except Exception:
        pass
    return shape


class MoldMethod2Generator:
    """模具生成法二：基于上/下形变面生成 上模/下模（竖直拉伸填充）。

    用法：
        g = MoldMethod2Generator(margin=5.0)
        res = g.generate_punch("xxx_PUNCH_CONTACT_FACES.stp", h1=None)
        g.export_step(res, "out/xxx_PUNCH_M2.stp")
    """

    def __init__(self, margin=DEFAULT_MARGIN):
        self.margin = float(margin)

    # ---------------- 上模 ----------------
    def generate_punch(self, upper_filepath, h1=None, base_margin=None):
        """上模：上形变面每点沿 +Z 拉伸填充至高度面 P。

        h1: P - 上形变面最低点z；None=自动推荐=上形变面高度 dz（P=最高点）。
        返回结果 dict（含 shape 于 _shape，供导出）。失败返回 {'success':False, ...}。
        """
        faces = _load_faces(upper_filepath)
        if len(faces) > MAX_FACES_METHOD2A:
            return {'success': False,
                    'error': (f'法二A 上形变面有 {len(faces)} 片，超过上限 '
                              f'{MAX_FACES_METHOD2A}：OCC 在这种多实体布尔上会卡死。'
                              f'请改用【法二B：把产品实体填到「产品」输入框】——'
                              f'目标几何完全相同（上模底面=产品上表面、顶面=平面P），'
                              f'但只需秒级完成，质量也更好。')}
        comp = _mix(faces)
        bb = _bbox(comp, "upper")
        # 形变面自身最高点 zmax、最低点 zmin
        zmin, zmax = bb[2], bb[5]
        dz = max(zmax - zmin, 1e-6)
        if h1 is None or h1 <= 0:
            h1 = dz
        P = zmin + h1                 # 高度面 P
        span_lx = (bb[3] - bb[0]) + 2 * self.margin
        span_ly = (bb[4] - bb[1]) + 2 * self.margin
        cx = (bb[0] + bb[3]) / 2.0
        cy = (bb[1] + bb[4]) / 2.0

        # 形变面沿 +Z 拉伸成"面上方空间"，再用盒体裁到 [.., P]
        z_end = P + max(dz * 1.5 + 20.0, 20.0)
        ok, stretched = _stretch_up(faces, z_end)
        if not ok or stretched is None:
            return {'success': False, 'error': '上形变面拉伸失败'}
        upper_box = _make_box(span_lx, span_ly, cx, cy, P, z_end + 20.0)
        ok, punch = _cut(stretched, upper_box, "punch-cut")
        if not ok or punch is None:
            return {'success': False, 'error': '上模顶面裁剪失败'}
        # 事后校验：顶面必须平齐 P（用精确包围盒）。
        # ★不要再加 _clamp_to_box「保险裁剪」★：它与目标盒共面，额外的 OCC 布尔
        #   极慢（法二A 曾因此卡到分钟级）；而且原来那个"顶面超出"其实是
        #   Bnd_Box 容差造成的测量假象（详见 _bbox_accurate 注释）。
        ptop = _bbox_accurate(punch)[5]
        if abs(ptop - P) > 0.05:
            return {'success': False,
                    'error': f'上模顶面未平齐高度面 P（期望 {P:.3f}，实际 {ptop:.3f}）'}
        print(f"[法二·上模] 上形变面 {len(faces)} 面, "
              f"z[{zmin:.2f},{zmax:.2f}], h1={h1:.2f}, 高度面P={P:.2f}")
        return self._result('punch', upper_filepath, punch,
                            {'h1': h1, 'P': P, 'zmin': zmin, 'zmax': zmax,
                             'face_count': len(faces), 'valid': _valid(punch)})


    # ---------------- 下模 ----------------
    def generate_cavity(self, lower_filepath, base_lx=None, base_ly=None, base_hz=None,
                        h_cavity=None):
        """下模：基座 + 挖柱体空腔（底面=下形变面投影轮廓）+ 填充下形变面以下空腔。

        base_lx/ly/hz: 基座长宽高；None=自动推荐（长宽≥下形变面 xy 尺寸+2*margin，
                       高=空腔高度+底部厚度）。
        h_cavity: 空腔（柱体）高度；None=自动推荐=下形变面高度 dt。
        返回结果 dict（含 shape 于 _shape，供导出）。
        """
        faces = _load_faces(lower_filepath)
        if len(faces) > MAX_FACES_METHOD2A:
            return {'success': False,
                    'error': (f'法二A 下形变面有 {len(faces)} 片，超过上限 '
                              f'{MAX_FACES_METHOD2A}：OCC 在这种多实体布尔上会卡死'
                              f'（下模必须做"盒 − 多实体"，实测 >10 分钟无响应）。'
                              f'请改用【法二B：把产品实体填到「产品」输入框】——'
                              f'型腔底面同样是产品下表面，但只需秒级完成。')}
        comp = _mix(faces)
        bb = _bbox(comp, "lower")
        zmin, zmax = bb[2], bb[5]
        dt = max(zmax - zmin, 1e-6)          # 下形变面高度
        if h_cavity is None or h_cavity <= 0:
            h_cavity = dt
        # 基座与下形变面 xOy 中心对齐（均在 Z 轴上）
        xmin, ymin = bb[0], bb[1]
        xmax, ymax = bb[3], bb[4]
        cx = (xmin + xmax) / 2.0
        cy = (ymin + ymax) / 2.0
        if base_lx is None or base_lx <= 0:
            base_lx = (xmax - xmin) + 2 * self.margin
        if base_ly is None or base_ly <= 0:
            base_ly = (ymax - ymin) + 2 * self.margin
        # 基座顶面 z_top = 下形变面 zmin + h_cavity（下形变面最低点恰好接触空腔底）
        z_top = zmin + h_cavity
        if base_hz is None or base_hz <= 0:
            base_hz = h_cavity + dt            # 顶部空腔 + 底部垫厚
        z_lo = z_top - base_hz
        base = _make_box(base_lx, base_ly, cx, cy, z_lo, z_top)

        # ★去掉 Common★ cavity = Cut(基座, vup)。
        # 旧写法 `Common(vup, cavity_zone)` 里 vup 是【多实体 compound】（下形变面
        # 各片棱柱彼此不相连，实测可达 10 个实体），多实体输入 + 共面重叠会让
        # OCC 布尔卡死 >10 分钟 —— 这是下模"一直卡在生成中"的真正原因。
        # `base` 本身已经把结果限定在基座内，cavity_zone 是多余的。
        z_end = z_top + max(dt * 1.5 + 20.0, 20.0)
        ok, vup = _stretch_up(faces, z_end)
        if not ok or vup is None:
            return {'success': False, 'error': '下形变面拉伸失败'}
        ok, cavity = _cut(base, vup, "cavity-cut")
        if not ok or cavity is None:
            return {'success': False, 'error': '下模挖腔失败'}
        # 事后校验（精确包围盒，不用 Bnd_Box）：下模必须在基座范围内。
        # 原来那个"下模顶面超出 z_top +3~6mm"同样是 Bnd_Box 容差造成的假象。
        cb = _bbox_accurate(cavity)
        if cb[5] - z_top > 0.05 or cb[2] < z_lo - 0.05:
            return {'success': False,
                    'error': (f'下模超出基座范围（实际 z[{cb[2]:.3f},{cb[5]:.3f}] '
                              f'vs 基座 z[{z_lo:.3f},{z_top:.3f}]）——'
                              f'OCP 布尔对开放形变面壳不可靠；'
                              f'请改用【法二B：直接传产品实体 product_filepath】')}

        print(f"[法二·下模] 下形变面 {len(faces)} 面, z[{zmin:.2f},{zmax:.2f}], "
              f"h_cavity={h_cavity:.2f}, z_top={z_top:.2f}, "
              f"基座 {base_lx:.2f}x{base_ly:.2f}x{base_hz:.2f}")
        return self._result('cavity', lower_filepath, cavity,
                            {'h_cavity': h_cavity, 'z_top': z_top, 'zmin': zmin,
                             'zmax': zmax, 'face_count': len(faces),
                             'base_lx': base_lx, 'base_ly': base_ly, 'base_hz': base_hz,
                             'valid': _valid(cavity)})


    # ---------------- 方案B：基于完整产品实体的稳健内核（稳定 1实体1壳 + BRepCheck 有效） ----------------
    def generate_from_product(self, product_filepath, h1=None, h_cavity=None,
                              base_lx=None, base_ly=None, base_hz=None):
        """用【完整产品实体】做稳定布尔，产出 上模/下模（均 1实体1壳、BRepCheck 有效）。

        由于"上表面曲面之上"需开放曲面界定、逐面拉伸会多壳，这里改为稳定型腔式：
          · 下模 CAVITY_M2 = Cut( 盒[Z_B-base .. Z_A], 产品实体 )  → 型腔底面贴合产品下表面；
          · 上模 PUNCH_M2 = 分模面 Z_A 以上到 P 的实体块（稳定单壳）。
        断言：产物必须 1实体1壳 且 BRepCheck IsValid，否则返回失败（不交付坏件）。
        返回 {'punch': result_dict, 'cavity': result_dict}。
        """
        solids = MoldFixer._extract_solids(product_filepath)
        product = MoldFixer._make_compound([s for _, s in solids])
        bb = _bbox(product, "product")
        xmin, ymin, zmin, xmax, ymax, zmax = bb
        Z_A, Z_B = zmax, zmin
        dt = max(Z_A - Z_B, 1e-6)
        cx, cy = (xmin + xmax) / 2.0, (ymin + ymax) / 2.0
        if h1 is None or h1 <= 0:
            h1 = dt                       # 上模顶面 P = Z_A + h1
        if h_cavity is None or h_cavity <= 0:
            base_thk = dt                 # 下模底部垫厚
        else:
            base_thk = h_cavity
        if base_lx is None or base_lx <= 0:
            base_lx = (xmax - xmin) + 2 * self.margin
        if base_ly is None or base_ly <= 0:
            base_ly = (ymax - ymin) + 2 * self.margin
        if base_hz is None or base_hz <= 0:
            base_hz = base_thk + dt        # 下模总高 = 底部垫厚 + 型腔高度

        print(f"[法二B·产品] 产品 bbox Z[{Z_B:.2f},{Z_A:.2f}] 高={dt:.2f}, "
              f"h1={h1:.2f}(上模), 下模基座 {base_lx:.2f}x{base_ly:.2f}x{base_hz:.2f}")

        # 下模：盒[Z_B-base_z .. Z_A] − 产品
        z_lo_c = Z_B - base_thk
        box_c = _make_box(base_lx, base_ly, cx, cy, z_lo_c, Z_A)
        ok, cavity = _cut(box_c, product, "m2b-cavity")
        if not ok or cavity is None:
            return {'success': False, 'error': '下模(盒−产品)布尔失败'}
        cav_res = self._result('cavity', product_filepath, cavity, {
            'Z_A': Z_A, 'Z_B': Z_B, 'product_height': dt,
            'base_lx': base_lx, 'base_ly': base_ly, 'base_hz': base_hz,
            'valid': _valid(cavity)})
        # 校验单壳
        if self._shells(cavity) != 1 or not _valid(cavity):
            return {'success': False,
                    'error': f"下模不是1实体1壳/无效：壳={self._shells(cavity)} valid={_valid(cavity)}"}

        # 上模构造：两种内核，按【XY 足迹覆盖率】择优（2026-09-11 实测结论）
        #   · mold_fix 凸起内核（按最大主板面拉柱 + 补边缘 + 补凹坑）：
        #     平板/曲面件表现最好 —— YA-1131 贴合 98.9%、YA-1238 贴合 98.6%
        #     （最大真实离缝 1.2mm / 0）；但它是"单一主板面"假设，深槽/多台阶件
        #     会崩：2ROM31-6061 只覆盖 X 向 22%、85% 上表面离缝，最大 183mm。
        #   · 拉伸法（全部朝上表面沿 +Z 拉伸到 P 的并集）：深槽件正确 ——
        #     2ROM 覆盖 100%、贴合 98.3%、离缝 0；但对平板件略逊（1131 93.5%、
        #     1238 85.3%）。
        # 故：先跑成熟内核；足迹覆盖率 ≥0.95 就用它，否则改用拉伸法。
        P_top = Z_A + h1
        punch = None
        punch_src = None

        def _xy_cov(shape):
            b = _bbox(shape, "punch")
            w = max(pbb3 - pbb0, 1e-9)
            h = max(pbb4 - pbb1, 1e-9)
            cw = min(b[3], pbb3) - max(b[0], pbb0)
            ch = min(b[4], pbb4) - max(b[1], pbb1)
            return max(cw, 0.0) * max(ch, 0.0) / (w * h)

        pbb0, pbb1, pbb3, pbb4 = xmin, ymin, xmax, ymax

        legacy = None
        try:
            legacy, _mz = MoldFixer()._build_full_protrusion(product, P_top)
            if not (legacy is not None and not legacy.IsNull()
                    and MoldFixer._solid_count(legacy) >= 1
                    and self._shells(legacy) == 1 and _valid(legacy)):
                legacy = None
        except Exception as e:
            print(f"  上模凸起内核构造失败({e})，改用拉伸法")
            legacy = None

        if legacy is not None:
            lc = _xy_cov(legacy)
            print(f"  上模候选[凸起内核]: XY足迹覆盖率={lc:.3f} 体积="
                  f"{MoldFixer._solid_volume(legacy):.0f}")
            if lc >= 0.95:
                punch, punch_src = legacy, 'protrusion'

        if punch is None:
            try:
                ex, n_used, n_skip = self.build_punch_by_extrusion(product, P_top)
                if (ex is not None and not ex.IsNull()
                        and MoldFixer._solid_count(ex) >= 1
                        and self._shells(ex) == 1 and _valid(ex)):
                    ec = _xy_cov(ex)
                    print(f"  上模候选[拉伸法]: {n_used} 面"
                          f"{'（跳过贯穿孔壁 %d）' % n_skip if n_skip else ''} "
                          f"XY足迹覆盖率={ec:.3f} 体积={MoldFixer._solid_volume(ex):.0f}")
                    if legacy is None or ec >= _xy_cov(legacy):
                        punch, punch_src = ex, 'extrusion'
                    else:
                        print("  拉伸法覆盖率不优于凸起内核 → 采用凸起内核")
                        punch, punch_src = legacy, 'protrusion'
                else:
                    print("  上模拉伸法结果不合格（非1实体1壳或无效）")
            except Exception as e:
                print(f"  上模拉伸法异常({e})")
            if punch is None and legacy is not None:
                punch, punch_src = legacy, 'protrusion'
        if punch is None:
            punch_src = 'block'
            punch = _make_box(base_lx, base_ly, cx, cy, Z_A, P_top)
            print("  上模回退稳定实体块（两种内核均不可用）")
        punch_res = self._result('punch', product_filepath, punch, {
            'h1': h1, 'P': P_top, 'Z_A': Z_A, 'Z_B': Z_B,
            'base_lx': base_lx, 'base_ly': base_ly, 'src': punch_src,
            'valid': _valid(punch)})
        if self._shells(punch) != 1 or not _valid(punch):
            return {'success': False,
                    'error': f"上模不是1实体1壳/无效：壳={self._shells(punch)} valid={_valid(punch)}"}

        # ── ★下模第二步：再挖掉"产品上表面以上的空间"★（2026-09-11 修复）──
        # 只做 `盒 − 产品` 是【不够的】：那只挖掉产品这层薄板自身的体积。当产品是
        # 弯曲/异形板、其上表面在分模面 Z_A 以下时，盒子会**留住"产品上方"的材料**，
        # 结果型腔退化成一条 1~2% 面积的细缝（实测 1131：切片面积仅 97.9~99.0%，
        # 用户看到的就是"顶面只有一条线、没挖出型面"）。
        #
        # 正确的型腔 = 盒 −（产品 ∪ 产品上表面以上空间）= 盒 − 产品 − 凸起。
        # 而"产品上表面以上空间"**就是上模凸起**（底面=产品上表面），直接复用，
        # 不需要任何新的曲面运算。
        try:
            ok2, cavity2 = _cut(cavity, punch, "m2b-cavity-above", fuzz=1e-3)
            if ok2 and cavity2 is not None and self._shells(cavity2) == 1 \
                    and _valid(cavity2):
                removed = MoldFixer._solid_volume(cavity) - MoldFixer._solid_volume(cavity2)
                cavity = cavity2
                cav_res = self._result('cavity', product_filepath, cavity, {
                    'Z_A': Z_A, 'Z_B': Z_B, 'product_height': dt,
                    'base_lx': base_lx, 'base_ly': base_ly, 'base_hz': base_hz,
                    'above_removed': round(removed, 1),
                    'valid': _valid(cavity)})
                print(f"  下模: 再挖「产品上表面以上」{removed:.0f} mm³"
                      f"（型腔底面=产品下表面、向上开口）")
            else:
                print("  下模: 挖「产品上表面以上」失败，保留 盒−产品 结果")
        except Exception as e:
            print(f"  下模: 挖「产品上表面以上」异常({e})，保留 盒−产品 结果")

        return {'success': True, 'punch': punch_res, 'cavity': cav_res}

    @staticmethod
    def _shells(shape):
        """数 shape 的 SHELL 数量。"""
        from OCP.TopAbs import TopAbs_SHELL
        e = TopExp_Explorer(shape, TopAbs_SHELL); n = 0
        while e.More(): n += 1; e.Next()
        return n

    # ---------------- 上模：全表面拉伸法（A+B 修复，2026-09-11） ----------------
    def build_punch_by_extrusion(self, product, P, nz_eps=0.1):
        """上模 = 产品所有【朝上表面】+【非贯穿孔的竖直/陡立面】沿 +Z 拉伸到高度面 P
        的并集，再减去产品本体、裁掉 z>P。

        这是"法二"的原始定义（每点沿 +Z 拉伸填充至面 P）的忠实实现，相对旧的
        `_build_full_protrusion`（只按"最大主板面"轮廓拉一根柱体）的差别：

          · 足迹 = 全部朝上表面，而不是单一主板面 → 深槽件/多台阶件不再只覆盖一角
            （旧法实测 2ROM31-6061 上模 X 向只覆盖 22%）；
          · 全部拉伸块一次性并集，不做"只留最大主体、其余 Fuse 不成即丢弃"的整合
            → 不再把外缘合法的凸起块当"布尔残片"丢掉（旧法实测 YA-1131 丢 6 块、
            YA-1238 丢 8 块，外缘缺料最大 79mm / 161mm）；
          · 相邻朝上表面共享棱边 → 各自拉伸体沿该棱边的竖直面贴合 → 并集天然连通
            为单一实体，无需额外补桥。

        【竖直面为什么要一起拉】两段不同高度的朝上表面之间由一道竖直壁连接时，
        两侧拉伸体之间会留下"壁厚 × 高度"的竖向沟槽；把该竖直壁本身也沿 +Z 拉伸，
        再被产品减掉后，恰好只剩下壁顶以上的部分，正好填平沟槽并把两侧连成一体。

        【贯穿孔为什么不拉】贯穿孔壁的下方没有产品材料；若也拉伸，孔会被填死
        （与既有约定"贯穿孔不下降填充"冲突）。因此对竖直面先做"下方是否有料"
        探测：有料（台阶壁/盲孔壁）→ 拉伸；无料（贯穿孔壁）→ 跳过。

        返回 (punch_shape, n_used, n_skipped)；无可用面时返回 (None, 0, 0)。
        """
        from product_surface_classifier import classify_product_surfaces

        cls = classify_product_surfaces(product, nz_eps=nz_eps)
        diag = cls['faces']
        # 按 TopExp_Explorer 的原始顺序重新取面，与 classify 的下标一一对应
        faces = []
        e = TopExp_Explorer(product, TopAbs_FACE)
        while e.More():
            faces.append(e.Current())
            e.Next()

        pbb = MoldFixer._bbox_tuple(product, "product")
        prisms = []
        n_skip = 0
        for i, f in enumerate(faces):
            if i >= len(diag):
                break
            kind = diag[i]['cls']
            if kind == 'lower':
                continue
            if kind == 'side':
                # 竖直/陡立面：仅当其【外侧下方】存在产品材料（台阶壁/凸台壁/
                # 盲孔壁）才拉伸填平；贯穿孔壁下方无料 → 跳过（孔保持不填）。
                mp = _face_mid_point(f)
                fb = MoldFixer._bbox_tuple(f)
                if mp is None:
                    n_skip += 1
                    continue
                has_below = False
                for zz in (fb[2] - 0.5, fb[2] - 3.0, pbb[2] + 1.0):
                    if zz <= pbb[2] - 0.5:
                        continue
                    if MoldFixer._point_inside(product, gp_Pnt(mp.X(), mp.Y(), zz)):
                        has_below = True
                        break
                if not has_below:
                    n_skip += 1
                    continue
            fb = MoldFixer._bbox_tuple(f)
            h = (P - fb[5]) + 2.0          # 顶面拉到 P 以上，随后统一裁掉
            if h <= 0.5:
                continue
            try:
                pr = BRepPrimAPI_MakePrism(f, gp_Vec(0.0, 0.0, h)).Shape()
            except Exception:
                continue
            if MoldFixer._solid_volume(pr) <= 1e-6:
                continue
            prisms.append(pr)
        if not prisms:
            return None, 0, n_skip

        # 逐个并集（★必须逐个★：把其余棱柱打包成 compound 一次性 Fuse 会
        # 返回"多个实体并列"的结果，互不融合 → 上模碎裂成十几块。
        # 实测 2ROM：compound 一次 Fuse → 12 实体；逐个 Fuse → 1 实体、覆盖全 X。
        body = prisms[0]
        if len(prisms) > 1:
            for pr in prisms[1:]:
                ok, merged = _fuse(body, pr, "上模-棱柱", fuzz=1e-3)
                if ok and merged is not None:
                    body = merged

        # 注：曾尝试把 mold_fix 的凸起内核并进来补凹坑细节，但并集会封出
        # 封闭内腔（实测 1131 壳数=2、1238 壳数=4，且 _fill_internal_voids
        # 填不掉），破坏"1实体1壳"契约，且对 2ROM 毫无增益（体积不变）→ 放弃。

        # 并集/封顶后若出现【封闭内腔】→ 多壳，尝试用 mold_fix 填腔函数填实。
        try:
            if MoldFixer._shell_count(body) > 1:
                filled = MoldFixer._fill_internal_voids(body)
                if filled is not None and not filled.IsNull():
                    body = filled
        except Exception as e:
            print(f"  上模: 内部空腔填实跳过（{e}）")

        # 若仍有互不相连的块（被贯穿口/窄槽隔开——分模面以上本该是实心），
        # 在【分模面 Z_A 以上】按产品 XY 足迹补一块封顶体把它们连成一体。
        # 物理上正确：Z_A 以上产品处处不存在，冲头该处本来就是实心。
        if MoldFixer._solid_count(body) > 1:
            cap = None
            try:
                fp = MoldFixer()._build_product_footprint_face(product)
                if fp is not None and not fp.IsNull():
                    fb = MoldFixer._bbox_tuple(fp)
                    cap = BRepPrimAPI_MakePrism(
                        MoldFixer._translate(fp, 0.0, 0.0, pbb[5] - fb[5]),
                        gp_Vec(0.0, 0.0, P - pbb[5])).Shape()
            except Exception:
                cap = None
            if cap is None or MoldFixer._solid_volume(cap) <= 1.0:
                cap = _make_box(pbb[3] - pbb[0], pbb[4] - pbb[1],
                                (pbb[0] + pbb[3]) / 2.0, (pbb[1] + pbb[4]) / 2.0,
                                pbb[5], P)
            ok, merged = _fuse(body, cap, "上模-封顶", fuzz=1e-3)
            if ok and merged is not None:
                body = merged
        # 减去产品本体（贴合产品上表面）
        ok, cut = _cut(body, product, "上模-去产品", fuzz=1e-4)
        if ok and cut is not None:
            body = cut
        # 裁掉 z>P：顶面平齐高度面 P
        span = max(pbb[3] - pbb[0], pbb[4] - pbb[1]) + 2 * self.margin + 20.0
        cx, cy = (pbb[0] + pbb[3]) / 2.0, (pbb[1] + pbb[4]) / 2.0
        top_box = _make_box(span, span, cx, cy, P, P + span + 50.0)
        ok, cut = _cut(body, top_box, "上模-裁顶", fuzz=1e-4)
        if ok and cut is not None:
            body = cut
        return body, len(prisms), n_skip

    # ---------------- 结果 / 导出 ----------------
    def _result(self, kind, input_file, shape, details):
        """组装结果 dict（_shape 为导出用 TopoDS shape，勿写 JSON）。"""
        bb = _bbox(shape, f"{kind}-result")
        return {
            'kind': kind,
            'input': input_file,
            'success': True,
            'details': details,
            'bbox': [round(v, 4) for v in bb],
            'volume': round(MoldFixer._solid_volume(shape), 4),
            '_shape': shape,
        }

    def export_step(self, result, path):
        """把结果 shape 导出为 STEP。"""
        if not result.get('success'):
            print(f"  跳过导出（失败）: {path}")
            return None
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        MoldFixer._export(result['_shape'], path, as_compound=False)
        print(f"  导出 {result['kind']} STEP: {path}")
        return path

    def write_json(self, result, path):
        """保存结果 JSON（剔除 _shape）。"""
        payload = {k: v for k, v in result.items() if k != '_shape'}
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"  导出 JSON 报告: {path}")
        return path


# ---------------------------------------------------------------------
# 命名 / CLI
# ---------------------------------------------------------------------

def _stem(filename):
    return os.path.splitext(os.path.basename(filename))[0]


def main(argv=None):
    _force_utf8()
    parser = argparse.ArgumentParser(
        description="模具生成法二：由 上/下形变面 生成上模(竖直拉伸至面P)/下模(基座挖腔+下形变面垫底)。")
    parser.add_argument('--upper', default=None, help='上形变面 STEP（*_PUNCH_CONTACT_FACES.stp）')
    parser.add_argument('--lower', default=None, help='下形变面 STEP（*_CAVITY_CONTACT_FACES.stp）')
    parser.add_argument('--product', default=None,
                        help='【推荐·法二B】完整产品/形变面具实体 STEP：用产品实体做稳定单壳有效的上/下模')
    parser.add_argument('--output-dir', default='mold_method2', help='输出目录')
    parser.add_argument('--h1', type=float, default=None,
                        help='上模填充高度 面P=形变面最低点+h1（默认=上形变面高度）')
    parser.add_argument('--h-cavity', type=float, default=None,
                        help='下模空腔(柱体)高度（默认=下形变面高度）')
    parser.add_argument('--base-lx', type=float, default=None, help='下模基座长 X（默认自动≥产品）')
    parser.add_argument('--base-ly', type=float, default=None, help='下模基座宽 Y（默认自动≥产品）')
    parser.add_argument('--base-hz', type=float, default=None, help='下模基座高 Z（默认自动）')
    parser.add_argument('--margin', type=float, default=DEFAULT_MARGIN,
                        help='基座超出产品 xy 边距（默认 5mm）')
    parser.add_argument('--no-export-step', action='store_true',
                        help='只输出 JSON，不保存 STEP')
    parser.add_argument('--show-summary', action='store_true', help='打印结论')
    args = parser.parse_args(argv)

    if not args.upper and not args.lower and not args.product:
        print("错误：必须提供 --product 和/或 --upper / --lower。")
        return 2
    try:
        os.makedirs(args.output_dir, exist_ok=True)
    except OSError as e:
        print(f"错误：无法创建输出目录 {args.output_dir}: {e}")
        return 2

    g = MoldMethod2Generator(margin=args.margin)
    results = []
    try:
        if args.upper:
            if not os.path.exists(args.upper):
                print(f"错误：上形变面文件不存在: {args.upper}")
                return 2
            stem = _stem(args.upper)
            res = g.generate_punch(args.upper, h1=args.h1)
            if not res['success']:
                print(f"上模生成失败：{res.get('error')}")
                return 1
            step_path = (os.path.join(args.output_dir, f"{stem}_PUNCH_M2.stp")
                         if not args.no_export_step else None)
            if step_path:
                g.export_step(res, step_path)
            g.write_json(res, os.path.join(args.output_dir, f"{stem}_PUNCH_M2.json"))
            results.append(res)

        if args.lower:
            if not os.path.exists(args.lower):
                print(f"错误：下形变面文件不存在: {args.lower}")
                return 2
            stem = _stem(args.lower)
            res = g.generate_cavity(args.lower, base_lx=args.base_lx, base_ly=args.base_ly,
                                    base_hz=args.base_hz, h_cavity=args.h_cavity)
            if not res['success']:
                print(f"下模生成失败：{res.get('error')}")
                return 1
            step_path = (os.path.join(args.output_dir, f"{stem}_CAVITY_M2.stp")
                         if not args.no_export_step else None)
            if step_path:
                g.export_step(res, step_path)
            g.write_json(res, os.path.join(args.output_dir, f"{stem}_CAVITY_M2.json"))
            results.append(res)

        # ── 法二B：基于完整产品实体的稳健内核（1实体1壳 + 有效）──
        if args.product:
            if not os.path.exists(args.product):
                print(f"错误：产品文件不存在: {args.product}")
                return 2
            stem = _stem(args.product)
            r = g.generate_from_product(args.product, h1=args.h1, h_cavity=args.h_cavity,
                                        base_lx=args.base_lx, base_ly=args.base_ly,
                                        base_hz=args.base_hz)
            if not r.get('success'):
                print(f"法二B(产品实体)生成失败：{r.get('error')}")
                return 1
            for kind in ('punch', 'cavity'):
                res = r[kind]
                sfx = 'PUNCH' if kind == 'punch' else 'CAVITY'
                step_path = (os.path.join(args.output_dir, f"{stem}_{sfx}_M2.stp")
                             if not args.no_export_step else None)
                if step_path:
                    g.export_step(res, step_path)
                g.write_json(res, os.path.join(args.output_dir, f"{stem}_{sfx}_M2.json"))
                results.append(res)

        if args.show_summary:
            for res in results:
                kind = '上模' if res['kind'] == 'punch' else '下模'
                d = res['details']
                print(f"\n=== {kind}（法二）===")
                print(f"  输入: {os.path.basename(res['input'])}")
                for k, v in d.items():
                    if k != 'valid':
                        print(f"  {k:>12}: {v}")
                print(f"  体积: {res['volume']:.2f} mm³")
        return 0
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"运行异常：{e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())

