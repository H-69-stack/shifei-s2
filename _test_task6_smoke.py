# -*- coding: utf-8 -*-
"""Task#6 冒烟测试：fit_checker / assembly_builder 的 B6 + P9 改造。

验证点：
  P9 等价性：_SolidDistIndex 加速后，_dist_point_solid(index=) 与旧实现
     （循环内每次构造 vertex、无预筛）结果完全一致（fit_checker & assembly_builder）
  B6：_common_volume 正常输入返回数值（0.0 表示真无重叠）；异常输入返回 None
  端到端：FitChecker.check() 与 build_assembly() 在真实几何上无崩溃、
     检查项齐全、index 代码路径实际执行
"""
import os
import sys
import math
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import cadquery as cq
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID, TopAbs_FACE
from OCP.gp import gp_Pnt
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Ax2, gp_Dir

import fit_checker as FC
import assembly_builder as AB
from mold_generator import MoldGenerator

PASS = True
def check(name, cond, detail=""):
    global PASS
    tag = "PASS" if cond else "FAIL"
    if not cond:
        PASS = False
    print(f"  [{tag}] {name} {detail}")


def old_dist_point_solid(pnt, solid):
    """旧实现（改造前）：循环内重复构造 vertex，无 bbox 预筛、无内含短路。"""
    best = None
    try:
        exp = TopExp_Explorer(solid, TopAbs_SOLID)
        if not exp.More():
            return None
        while exp.More():
            from OCP.TopoDS import TopoDS
            v = TopoDS.Vertex_s(cq.occ_impl.shapes.MakeVertex(pnt).wrapped) \
                if False else None
            # 直接用 OCP 构造 vertex（与旧代码一致）
            from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
            v = TopoDS.Vertex_s(BRepBuilderAPI_MakeVertex(pnt).Vertex())
            d = FC_BRepExtrema(exp.Current(), v)
            d.Perform()
            if d.IsDone():
                val = d.Value()
                best = val if best is None else min(best, val)
            exp.Next()
    except Exception:
        pass
    return best

def FC_BRepExtrema(a, b):
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    return BRepExtrema_DistShapeShape(a, b)


def sample_test_points(shape, n_face=40, n_rand=20):
    """产品面采样点 + bbox 内随机点（覆盖 0 距离/近距离/远距离三种情形）。"""
    pts = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    fi = 0
    while exp.More():
        for p in FC._sample_face_points(exp.Current(), cap=12):
            pts.append(p)
        fi += 1
        exp.Next()
    b = AB._bbox(shape)
    x1, y1, z1, x2, y2, z2 = b
    import random
    rnd = random.Random(42)
    for _ in range(n_rand):
        pts.append(gp_Pnt(rnd.uniform(x1 - 5, x2 + 5),
                          rnd.uniform(y1 - 5, y2 + 5),
                          rnd.uniform(z1 - 5, z2 + 5)))
    return pts


print("=" * 66)
print("Task#6 冒烟：B6(_common_volume None 语义) + P9(距离索引等价/加速)")
print("=" * 66)

# ---------------------------------------------------------------- 生成真实模具
src = os.path.join(ROOT, "YA-1131-505_MIRROR_xOy.stp")
out_dir = os.path.join(tempfile.gettempdir(), "task6_smoke_out")
os.makedirs(out_dir, exist_ok=True)
gen = MoldGenerator()
r = gen.generate(filepath=src, side_margin=20.0, base_thickness=100.0,
                 output_dir=out_dir)
punch_file, cavity_file = r['punch'], r['cavity']
print(f"\n[生成] punch={punch_file}")
print(f"[生成] cavity={cavity_file}")
check("mold_generator 产出 punch/cavity", bool(punch_file and cavity_file))

# ---------------------------------------------------------------- P9 等价性
print("\n--- P9 等价性：新旧 _dist_point_solid ---")
for label, path in (("punch", punch_file), ("cavity", cavity_file)):
    solids = AB._extract_solids(path)
    shape = AB._make_compound([s for _, s in solids])
    pts = sample_test_points(shape)
    idx = FC._SolidDistIndex(shape)
    idx_ab = AB._SolidDistIndex(shape)
    nz = 0
    bad = 0
    for p in pts:
        d_old = old_dist_point_solid(p, shape)
        d_new = FC._dist_point_solid(p, shape, index=idx)
        d_ab = AB._dist_point_solid(p, shape, index=idx_ab)
        if d_old is None and d_new is None:
            continue
        if d_old is None or d_new is None or abs(d_old - d_new) > 1e-9 \
           or abs(d_old - d_ab) > 1e-9:
            bad += 1
        elif d_new == 0.0:
            nz += 1
    check(f"{label}: {len(pts)} 点新旧距离一致", bad == 0,
          f"(不一致 {bad} 处; 命中内含/表面 0 距离 {nz} 处)")

# 兼容调用（不传 index）路径
solids = AB._extract_solids(cavity_file)
shape = AB._make_compound([s for _, s in solids])
p0 = FC._sample_face_points(
    TopExp_Explorer(shape, TopAbs_FACE).Current(), cap=3)[0]
d_compat = FC._dist_point_solid(p0, shape)
check("fit_checker._dist_point_solid 兼容调用(无 index)", d_compat is not None,
      f"d={d_compat}")
d_compat2 = AB._dist_point_solid(p0, shape)
check("assembly_builder._dist_point_solid 兼容调用(无 index)", d_compat2 is not None,
      f"d={d_compat2}")
check("两模块距离语义一致", abs(d_compat - d_compat2) < 1e-9,
      f"{d_compat} vs {d_compat2}")

# ---------------------------------------------------------------- B6 语义
print("\n--- B6：_common_volume 数值/None 语义 ---")
prod = None
for v, s in AB._extract_solids(cavity_file):
    if abs(v - min(vv for vv, _ in AB._extract_solids(cavity_file))) < 1e-6:
        prod = s  # 体积最小者 = 产品
check("提取到产品实体", prod is not None)
if prod is not None:
    v_full = FC._common_volume(prod, prod)
    vp = FC._solid_volume(prod)
    check("fit_checker: Common(产品,产品)≈产品体积", v_full is not None
          and abs(v_full - vp) / max(vp, 1e-9) < 0.01, f"{v_full} vs {vp}")
    v_full2 = AB._common_volume(prod, prod)
    check("assembly_builder: Common(产品,产品)≈产品体积", v_full2 is not None
          and abs(v_full2 - vp) / max(vp, 1e-9) < 0.01, f"{v_full2} vs {vp}")
    # 远离的盒子 → 真 0.0（不是 None，不被误判为未知）
    far = BRepPrimAPI_MakeBox(gp_Ax2(gp_Pnt(1e5, 1e5, 1e5), gp_Dir(0, 0, 1)),
                              10, 10, 10).Shape()
    v_far = FC._common_volume(prod, far)
    check("fit_checker: Common(产品,远盒)=0.0（数值，非 None）",
          v_far is not None and v_far == 0.0, f"v={v_far}")
    v_far2 = AB._common_volume(prod, far)
    check("assembly_builder: Common(产品,远盒)=0.0（数值，非 None）",
          v_far2 is not None and v_far2 == 0.0, f"v={v_far2}")
    # 空 compound → None（无法判定，不再伪装 0.0）
    empty = AB._make_compound([])
    v_empty = FC._common_volume(prod, empty)
    check("fit_checker: Common(产品,空)=None", v_empty is None, f"v={v_empty}")
    v_empty2 = AB._common_volume(prod, empty)
    check("assembly_builder: Common(产品,空)=None", v_empty2 is None, f"v={v_empty2}")

# ---------------------------------------------------------------- fit_checker 端到端
print("\n--- fit_checker.FitChecker.check() 端到端（3实体凸模/2实体凹模）---")
# mold_generator 产出 2 实体凸模；fit_checker 期望 3 实体（基座/凸起/产品）。
# 合成第三实体「凸起」= 产品顶部的小盒（位于分模面上方），满足结构约定。
solids_p = AB._extract_solids(punch_file)
solids_c = AB._extract_solids(cavity_file)
punch_block = solids_p[0][1]      # 体积大者 = 带补腔方块
product_p = solids_p[-1][1]       # 体积小者 = 产品
cavity_blk = solids_c[0][1]
product_c = solids_c[-1][1]
bp = AB._bbox(product_p)
protrusion = BRepPrimAPI_MakeBox(
    gp_Ax2(gp_Pnt(bp[0], bp[1], bp[5]), gp_Dir(0, 0, 1)),
    bp[3] - bp[0], bp[4] - bp[1], 5.0).Shape()
punch3 = FC._make_compound_export = None  # noqa（占位避免误用）
from OCP.TopoDS import TopoDS_Compound
from OCP.BRep import BRep_Builder
def make_comp(shps):
    c = TopoDS_Compound(); b = BRep_Builder(); b.MakeCompound(c)
    for s in shps:
        if s is not None and not s.IsNull():
            b.Add(c, s)
    return c
punch3 = make_comp([punch_block, protrusion, product_p])
cavity2 = make_comp([cavity_blk, product_c])
p3_path = os.path.join(out_dir, "smoke_PUNCH3.stp")
c2_path = os.path.join(out_dir, "smoke_CAVITY2.stp")
AB._export(punch3, p3_path, as_compound=True)
AB._export(cavity2, c2_path, as_compound=True)
checker = FC.FitChecker(gap_tol=0.1, interfere_tol=1.0)
rep = checker.check(p3_path, c2_path)
check("fit_checker 返回完整报告",
      rep is not None and 'checks' in rep and 'issues' in rep)
check("fit_checker 间隙采样执行（P9 index 路径）",
      rep['checks'].get('sampled_points', 0) > 0,
      f"sampled={rep['checks'].get('sampled_points')}")
check("fit_checker 干涉检查项存在（B6 分支）",
      any(k.startswith('interfere_') for k in rep['checks']),
      f"keys={[k for k in rep['checks'] if k.startswith('interfere')]}")
check("fit_checker 一致性检查项存在",
      'product_consistency' in rep['checks'])
print(f"  result={rep['result']} elapsed={rep['elapsed_s']}s "
      f"issues={len(rep['issues'])}")

# ---------------------------------------------------------------- B2 早退
print("\n--- B2：结构体不足提前返回 ---")
p1_path = os.path.join(out_dir, "smoke_PUNCH1.stp")
AB._export(punch_block, p1_path, as_compound=False)
rep2 = checker.check(p1_path, c2_path)
check("B2: 1实体凸模 → 提前返回不崩溃",
      rep2['result'] == 'FAIL'
      and any(i['check'] == 'structure' for i in rep2['issues']),
      f"issues={[i['msg'] for i in rep2['issues']]}")

# ---------------------------------------------------------------- assembly_builder 端到端
print("\n--- assembly_builder.build_assembly() 端到端 ---")
asm_dir = os.path.join(out_dir, "assembly_out")
rep3 = AB.build_assembly(punch_path=punch_file, cavity_path=cavity_file,
                         product_src_path=cavity_file,
                         output_dir=asm_dir, output_name="smoke",
                         fuse=False, gap_tol=0.5)
check("assembly_builder 返回报告",
      isinstance(rep3, dict) and 'checks' in rep3)
check("assembly_builder 干涉项存在（B6 分支）",
      any(k.startswith('interfere_') for k in rep3['checks']),
      f"keys={[k for k in rep3['checks'] if k.startswith('interfere')]}")
check("assembly_builder 间隙采样执行（P9 index 路径）",
      rep3['checks'].get('sampled_points', 0) > 0,
      f"sampled={rep3['checks'].get('sampled_points')}")
check("assembly_builder 输出 STEP",
      rep3.get('assembly') and os.path.exists(rep3['assembly']),
      f"{rep3.get('assembly')}")
print(f"  ok={rep3.get('ok')} elapsed={rep3.get('elapsed_s')}s")

print("\n" + "=" * 66)
print("冒烟测试总体: " + ("ALL PASS ✅" if PASS else "存在 FAIL ❌"))
print("=" * 66)
sys.exit(0 if PASS else 1)
