# -*- coding: utf-8 -*-
"""统一校验。

每种方法都产出同样的指标，才能横向比较、才能自动推荐：

  · 结构   ：实体数 / 壳数 / 面数 / BRepCheck 是否有效
  · 尺寸   ：体积、包围盒
  · 干涉   ：上模 ∩ 下模 的公共体积（必须 ≈ 0）
  · 贴合度 ：模具工作面的实际体积 vs 由形变面网格积分出来的"应有体积"

★贴合度怎么算才靠谱★
  上模（竖向拉伸法）= 形变面以上、到高度面 P 之间的料。
  对形变面的三角网格做积分 Σ (P - z_质心) × 三角形在 XY 上的投影面积，
  就得到"应有体积"。实测 2ROM：应有 396,000、实际 393,300 → 贴合度 99.3%。
  这个量比"目测有没有缺口"客观得多。
"""
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRep import BRep_Tool
from OCP.TopLoc import TopLoc_Location

from . import geo


# ---------------------------------------------------------------------------
# 单件
# ---------------------------------------------------------------------------
def check_solid(shape, tag="solid"):
    c = geo.counts(shape)
    valid = geo.is_valid(shape)
    return {
        "tag": tag,
        "solids": c["solids"],
        "shells": c["shells"],
        "faces": c["faces"],
        "valid": valid,
        "volume_mm3": geo.volume(shape),
        "bbox": list(geo.bbox(shape)),
        # ★合格 = 至少 1 个实体 + BRepCheck 有效★
        #   不再强制"恰好 1 个"：布尔确实可能把一个模体切成几块
        #   （m4 实测 1 主块 + 6 块分型面附近的薄板）。分成多块要【报出来】
        #   而不是【丢掉】，所以另外给 single_body 标记。
        "single_body": (c["solids"] == 1),
        "ok": (c["solids"] >= 1 and valid),
    }


def check_pair(punch, cavity, tag="pair"):
    inter = geo.common(punch, cavity)
    v = geo.volume(inter) if inter is not None else None
    return {
        "tag": tag,
        "interference_mm3": v,
        "ok": (v is not None and v <= 1.0),
    }


# ---------------------------------------------------------------------------
# 贴合度
# ---------------------------------------------------------------------------
def _mesh_tris(shape, tol=0.1):
    BRepMesh_IncrementalMesh(shape, tol, False, 0.3, True)
    tris = []
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        f = TopoDS.Face_s(ex.Current())
        loc = TopLoc_Location()
        h = BRep_Tool.Triangulation_s(f, loc)
        if h is not None:
            trsf = loc.Transformation()
            for i in range(1, h.NbTriangles() + 1):
                a, b, c = h.Triangle(i).Get()
                ps = [h.Node(n).Transformed(trsf) for n in (a, b, c)]
                tris.append(tuple((p.X(), p.Y(), p.Z()) for p in ps))
        ex.Next()
    return tris


def volume_from_surface_to_plane(surface, z_plane):
    """形变面到 z_plane 之间（形变面在下方）的体积。

    对每个三角面片用它在 XY 上的投影面积 × (z_plane − 面片质心 z) 累加。
    形变面是高度场时结果精确；有倒挂时会有偏差。
    """
    total = 0.0
    for (p0, p1, p2) in _mesh_tris(surface):
        # XY 投影的有向面积
        ax = p1[0] - p0[0]; ay = p1[1] - p0[1]
        bx = p2[0] - p0[0]; by = p2[1] - p0[1]
        area_xy = abs(ax * by - ay * bx) * 0.5
        zc = (p0[2] + p1[2] + p2[2]) / 3.0
        total += area_xy * (z_plane - zc)
    return abs(total)


def conformity(mold_shape, surface, z_plane, clip_bbox=None):
    """上模/下模 与"由形变面推出的应有体积"的吻合度。

    返回 dict：expected / actual / diff / ratio(0~1)
    """
    expected = volume_from_surface_to_plane(surface, z_plane)
    actual = geo.volume(mold_shape)
    if expected <= 0:
        return {"expected_mm3": expected, "actual_mm3": actual,
                "diff_mm3": None, "ratio": None}
    diff = actual - expected
    ratio = 1.0 - abs(diff) / expected
    return {"expected_mm3": expected, "actual_mm3": actual,
            "diff_mm3": diff, "ratio": ratio}


# ---------------------------------------------------------------------------
# 产品贴合（给"按产品工作"的方法用：m2b / m4 / m1）
# ---------------------------------------------------------------------------
def check_product_fit(punch, cavity, product, tol_mm3=1.0):
    """产品与上下模的贴合检查。

    ★为什么需要★：贴合度那套公式要用【形变面】；而 m2b/m4 是拿【产品实体】
    工作的，没有形变面可积分。对这类方法，"合格"的等价判据是：
        上模 ∩ 产品 = 0  且  下模 ∩ 产品 = 0
      即产品能原封不动放进模腔里、不被任何一半侵入。
    顺便给出产品体积与两半的"侵占比"，便于横向比较。
    """
    pv = geo.volume(product)
    ip = geo.common(punch, product) if punch is not None else None
    ic = geo.common(cavity, product) if cavity is not None else None
    vip = geo.volume(ip) if ip is not None else None
    vic = geo.volume(ic) if ic is not None else None
    vals = [v for v in (vip, vic) if v is not None]
    worst = max(vals) if vals else None
    return {
        "product_mm3": pv,
        "punch_interference_mm3": vip,
        "cavity_interference_mm3": vic,
        "worst_mm3": worst,
        "ok": (worst is not None and worst <= tol_mm3),
    }


# ---------------------------------------------------------------------------
# 汇总报告
# ---------------------------------------------------------------------------
def full_report(punch=None, cavity=None, upper=None, lower=None,
                P=None, z_top=None, product=None, tag=""):
    rep = {"tag": tag, "punch": None, "cavity": None,
           "pair": None, "punch_conformity": None, "cavity_conformity": None,
           "product_fit": None}
    if punch is not None:
        rep["punch"] = check_solid(punch, "punch")
    if cavity is not None:
        rep["cavity"] = check_solid(cavity, "cavity")
    if punch is not None and cavity is not None:
        rep["pair"] = check_pair(punch, cavity)
    if punch is not None and upper is not None and P is not None:
        rep["punch_conformity"] = conformity(punch, upper, P)
    if cavity is not None and lower is not None and z_top is not None:
        # 下模的"应有"= 基座 − 下形变面到 z_top 之间的料
        cav_expected = volume_from_surface_to_plane(lower, z_top)
        rep["cavity_conformity"] = {"expected_removed_mm3": cav_expected}
    if product is not None and (punch is not None or cavity is not None):
        rep["product_fit"] = check_product_fit(punch, cavity, product)
    return rep


def print_report(rep, log=print):
    log("=" * 78)
    if rep.get("tag"):
        log("方法: %s" % rep["tag"])
    for k in ("punch", "cavity"):
        c = rep.get(k)
        if not c:
            continue
        log("  %-6s 实体%d 壳%d 面%3d 体积=%12.1f mm^3 有效=%-5s bbox z[%.3f,%.3f]%s" % (
            k, c["solids"], c["shells"], c["faces"], c["volume_mm3"],
            c["valid"], c["bbox"][2], c["bbox"][5],
            "" if c.get("single_body", True) else "   ⚠分成 %d 块" % c["solids"]))
    p = rep.get("pair")
    if p:
        v = p["interference_mm3"]
        log("  干涉   %.4f mm^3   %s" % (v if v is not None else -1,
                                         "OK" if p["ok"] else "!! 有干涉"))
    for k, label in (("punch_conformity", "上模贴合度"), ("cavity_conformity", "下模")):
        c = rep.get(k)
        if not c:
            continue
        if c.get("ratio") is not None:
            log("  %s %.1f%%  （应有 %.0f / 实际 %.0f，差 %+.0f mm^3）" % (
                label, c["ratio"] * 100, c["expected_mm3"], c["actual_mm3"],
                c["diff_mm3"]))
    f = rep.get("product_fit")
    if f:
        log("  产品贴合 上模∩产品=%.4f  下模∩产品=%.4f mm^3  （产品 %.0f mm^3）  %s" % (
            f["punch_interference_mm3"] if f["punch_interference_mm3"] is not None else -1,
            f["cavity_interference_mm3"] if f["cavity_interference_mm3"] is not None else -1,
            f["product_mm3"], "OK" if f["ok"] else "!! 产品被侵入"))
    log("=" * 78)
    return rep
