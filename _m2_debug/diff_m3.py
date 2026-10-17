# -*- coding: utf-8 -*-
"""量化方法三/四「拉伸与挖空没到位」到底差在哪。

思路：拿【方法二A】当参照物 —— 它就是"形变面沿 +Z 竖向拉伸"的直接定义，
      把两边的参数对齐（同一个高度面 P、同一个空腔顶 z_top、同一个边距），
      再做差集：
          missing = 参照 − 产物   ← 产物比参照【少】的料 = "没到位"的部分
          extra   = 产物 − 参照   ← 产物比参照【多】的料
      missing 的体积和包围盒直接告诉我们：差多少、差在哪儿。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from moldkit import geo
from mold_method2_generator import MoldMethod2Generator


def rep(tag, sh):
    if sh is None:
        print("  %-10s (空)" % tag)
        return
    try:
        b = geo.bbox(sh)
    except Exception:
        print("  %-10s (空/invalid bbox)  体积=%.1f" % (tag, geo.volume(sh)))
        return
    if geo.volume(sh) < 0.01:
        print("  %-10s (无实体，体积≈0)" % tag)
        return
    c = geo.counts(sh)
    print("  %-10s 实体%d 壳%d 面%3d 体积=%12.1f mm^3  有效=%-5s  bbox x[%8.2f,%8.2f] y[%8.2f,%8.2f] z[%8.2f,%8.2f]"
          % (tag, c["solids"], c["shells"], c["faces"], geo.volume(sh),
             geo.is_valid(sh), b[0], b[3], b[1], b[4], b[2], b[5]))


def diff(tag, product_sh, ref_sh):
    """product_sh = 产物, ref_sh = 参照"""
    print("\n" + "-" * 100)
    print("【%s】产物 vs 参照（方法二A）" % tag)
    rep("产物", product_sh)
    rep("参照", ref_sh)
    missing = geo.cut(ref_sh, product_sh)
    extra = geo.cut(product_sh, ref_sh)
    rep("missing", missing)
    rep("extra", extra)
    vm = geo.volume(missing) if missing is not None else -1
    ve = geo.volume(extra) if extra is not None else -1
    vr = geo.volume(ref_sh)
    print("  → 少料 %.1f mm^3（占参照 %.2f%%）   多料 %.1f mm^3（占参照 %.2f%%）"
          % (vm, 100 * vm / vr, ve, 100 * ve / vr))
    return vm, ve


def main():
    # ★参照物也必须用【补孔后】的形变面★
    #   不然 m2a 的参照上模中间带个洞，和 m3 比就是"多料 19688 mm³"的假差异。
    upper = os.path.join(ROOT, "mold_out", "2ROM_upper_filled.stp")
    lower = os.path.join(ROOT, "mold_out", "2ROM_lower_filled.stp")
    if not os.path.exists(upper):
        upper = r"C:\Temp\m3test\upper_sewn2rom.stp"
    if not os.path.exists(lower):
        lower = r"C:\Temp\m3test\lower_sewn2rom.stp"
    out = os.path.join(ROOT, "mold_out")

    print("=" * 100)
    print("输入: %s / %s" % (os.path.basename(upper), os.path.basename(lower)))

    ub = geo.bbox(geo.read_step(upper))
    lb = geo.bbox(geo.read_step(lower))
    dz_u, dz_l = ub[5] - ub[2], lb[5] - lb[2]
    print("上形变面 z[%.2f,%.2f] dz=%.2f   下形变面 z[%.2f,%.2f] dz=%.2f"
          % (ub[2], ub[5], dz_u, lb[2], lb[5], dz_l))

    g = MoldMethod2Generator(margin=5.0)

    # ---- 上模：对齐 P ----
    # m3 默认 H1 = dz + ppad(1) → P = zmin + dz + 1；m2a 传 h1 = dz + 1
    h1 = dz_u + 1.0
    print("\n对齐参数：上模 h1=%.2f（P=%.2f）；下模 h_cavity=%.2f（z_top=%.2f）"
          % (h1, ub[2] + h1, dz_l + 2.0, lb[2] + dz_l + 2.0))
    rp = g.generate_punch(upper, h1=h1)
    ref_punch = rp.get("_shape") if rp.get("success") else None
    rc = g.generate_cavity(lower, h_cavity=dz_l + 2.0)
    ref_cav = rc.get("_shape") if rc.get("success") else None

    p3 = os.path.join(out, "2ROM_m3_PUNCH.stp")
    c3 = os.path.join(out, "2ROM_m3_CAVITY.stp")
    if os.path.exists(p3) and ref_punch is not None:
        diff("m3 上模", geo.read_step(p3), ref_punch)
    if os.path.exists(c3) and ref_cav is not None:
        diff("m3 下模", geo.read_step(c3), ref_cav)

    # ---- 轮廓覆盖：产物的 XY footprint 是否盖住形变面投影 ----
    print("\n" + "=" * 100)
    print("【XY 覆盖检查】产物在 XY 上是否盖住形变面投影")
    from moldkit import surfaces
    for tag, srf, mold_p in (("上模", upper, p3), ("下模", lower, c3)):
        if not os.path.exists(mold_p):
            continue
        sh_s = geo.read_step(srf)
        sh_m = geo.read_step(mold_p)
        polys, info = surfaces.projection_outline(sh_s, log=lambda s="": None)
        # 形变面投影轮廓的包围盒 vs 产物包围盒
        xs = [p[0] for p in polys[0]]; ys = [p[1] for p in polys[0]]
        bm = geo.bbox(sh_m)
        print("  %s: 形变面投影轮廓 x[%.2f,%.2f] y[%.2f,%.2f] 面积=%.0f mm^2"
              % (tag, min(xs), max(xs), min(ys), max(ys), info["outline_area"]))
        # 用轮廓在 z 中高处切一刀，看产物截面是否包含轮廓
        zmid = (bm[2] + bm[5]) / 2.0
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
        from OCP.gp import gp_Pln, gp_Pnt, gp_Dir
        sec = BRepAlgoAPI_Section(sh_m, gp_Pln(gp_Pnt(0, 0, zmid), gp_Dir(0, 0, 1)))
        sec.SetRunParallel(False); sec.Build()
        if sec.IsDone() and not sec.Shape().IsNull():
            bs = geo.bbox(sec.Shape())
            print("      产物在 z=%.2f 的截面 bbox x[%.2f,%.2f] y[%.2f,%.2f]"
                  % (zmid, bs[0], bs[3], bs[1], bs[4]))
            covered = (bs[0] <= min(xs) + 0.05 and bs[3] >= max(xs) - 0.05
                       and bs[1] <= min(ys) + 0.05 and bs[4] >= max(ys) - 0.05)
            print("      截面比轮廓%s" % ("大/相等 ✓" if covered else "小 ✗ ← 这就是'没到位'"))


if __name__ == "__main__":
    main()
