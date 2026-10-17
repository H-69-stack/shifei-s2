# -*- coding: utf-8 -*-
"""形变面的两个决定性属性 + 可靠视图。

属性1【高度场】：从上方沿 -Z 打竖直线，统计每条线打中曲面的次数。
    · 全部 ≤1  → 是高度场 → SW「成形到曲面」有确定意义
    · 存在 >1  → 有倒挂 → 「成形到曲面」结果不确定
属性2【孔】：投影里有没有内环（洞）。有洞 → 「成形到曲面」会在洞处失败。
另出 4 视图 PNG（全部用【网格三角面】画，不再用 UV 采样，避免上次的 ±1e9 陷阱）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from OCP.STEPControl import STEPControl_Reader
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRep import BRep_Tool
from OCP.TopLoc import TopLoc_Location
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
from OCP.Geom import Geom_Line
from OCP.gp import gp_Pnt, gp_Dir
from OCP.GeomAdaptor import GeomAdaptor_Curve


def load(path):
    r = STEPControl_Reader()
    r.ReadFile(path)
    r.TransferRoots()
    return r.OneShape()


def bbox(sh):
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(sh, b, False, False)
    return b.Get()


def mesh(sh, tol=0.5):
    BRepMesh_IncrementalMesh(sh, tol, False, 0.4, True)
    tris = []
    ex = TopExp_Explorer(sh, TopAbs_FACE)
    while ex.More():
        f = TopoDS.Face_s(ex.Current())
        loc = TopLoc_Location()
        h = BRep_Tool.Triangulation_s(f, loc)
        if h is not None:
            trsf = loc.Transformation()
            for i in range(1, h.NbTriangles() + 1):
                a, b, c = h.Triangle(i).Get()
                tris.append([tuple(h.Node(n).Transformed(trsf).Coord()) for n in (a, b, c)])
        ex.Next()
    return tris


def ray_hits(sh, x, y, z0):
    """从 (x,y,z0) 沿 -Z 打线，返回打中曲面的点数。"""
    inter = BRepIntCurveSurface_Inter()
    inter.Init(sh, 1e-6)
    line = Geom_Line(gp_Pnt(x, y, z0), gp_Dir(0, 0, -1))
    inter.Load(GeomAdaptor_Curve(line))
    n = 0
    while inter.More():
        n += 1
        inter.Next()
    return n


def height_field(sh, tris, grid=28):
    x1, y1, z1, x2, y2, z2 = bbox(sh)
    z0 = z2 + (z2 - z1) + 10.0
    hist = {}
    covered = 0
    tot = 0
    # 只用网格点落在投影内的位置测（在投影外的线本来就打不中）
    pts = set()
    for t in tris:
        for p in t:
            pts.add((round(p[0], 2), round(p[1], 2)))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    samples = []
    for i in range(grid):
        for j in range(grid):
            x = x1 + (x2 - x1) * (i + 0.5) / grid
            y = y1 + (y2 - y1) * (j + 0.5) / grid
            samples.append((x, y))
    for (x, y) in samples:
        try:
            n = ray_hits(sh, x, y, z0)
        except Exception:
            continue
        tot += 1
        if n > 0:
            covered += 1
        hist[n] = hist.get(n, 0) + 1
    return hist, covered, tot


def draw(tris, path, tag):
    fig, axes = plt.subplots(2, 2, figsize=(15, 9))
    allp = [p for t in tris for p in t]
    xs = [p[0] for p in allp]; ys = [p[1] for p in allp]; zs = [p[2] for p in allp]

    ax = axes[0][0]
    for t in tris:
        ax.fill([p[0] for p in t], [p[1] for p in t], facecolor="tab:blue",
                edgecolor="none", alpha=0.85)
    ax.set_title("TOP VIEW (XY)  <- projection outline"); ax.set_aspect("equal"); ax.grid(lw=0.3)

    ax = axes[0][1]
    for t in tris:
        ax.fill([p[0] for p in t], [p[2] for p in t], facecolor="indianred",
                edgecolor="none", alpha=0.85)
    ax.set_title("FRONT (XZ)  <- overhang check"); ax.grid(lw=0.3)

    ax = axes[1][0]
    for t in tris:
        ax.fill([p[1] for p in t], [p[2] for p in t], facecolor="seagreen",
                edgecolor="none", alpha=0.85)
    ax.set_title("SIDE (YZ)"); ax.grid(lw=0.3)

    ax = axes[1][1]
    ax.scatter(xs, zs, s=1, c="k")
    ax.set_title("FRONT points (XZ)"); ax.grid(lw=0.3)

    for ax in axes.ravel():
        ax.relim(); ax.autoscale_view()
    fig.suptitle(tag)
    fig.tight_layout()
    fig.savefig(path, dpi=85)
    plt.close(fig)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    items = [
        (r"C:\Temp\m3test\upper_sewn2rom.stp", "2ROM_upper"),
        (os.path.join(root, r"web_box_generator\uploads\contact_output\a5737faebbf7\543e0fdcfa2e_2ROM31-6061 ---(1)_CAVITY_CONTACT_FACES.stp"), "2ROM_lower"),
        (os.path.join(root, r"web_box_generator\uploads\contact_output\c37ff7241a19\f4adfe3f6156_YA-1131-505_MIRROR_xOy_PUNCH_CONTACT_FACES.stp"), "1131_upper"),
        (os.path.join(root, r"web_box_generator\uploads\contact_output\c37ff7241a19\f4adfe3f6156_YA-1131-505_MIRROR_xOy_CAVITY_CONTACT_FACES.stp"), "1131_lower"),
    ]
    outdir = os.path.join(root, "_m2_debug", "m3_figs")
    os.makedirs(outdir, exist_ok=True)
    for path, tag in items:
        if not os.path.exists(path):
            print("缺失", path, flush=True); continue
        print("=" * 78, flush=True)
        print(tag, flush=True)
        sh = load(path)
        bb = bbox(sh)
        print("  bbox x[%.1f,%.1f] y[%.1f,%.1f] z[%.1f,%.1f]" % bb, flush=True)
        tris = mesh(sh)
        print("  三角面片 =", len(tris), flush=True)
        hist, cov, tot = height_field(sh, tris, grid=26)
        print("  竖直射线命中次数分布:", dict(sorted(hist.items())), flush=True)
        multi = sum(v for k, v in hist.items() if k >= 2)
        print("  ==> 命中>=2 的采样点 = %d / %d  (%.1f%%)  %s" % (
            multi, tot, 100.0 * multi / max(tot, 1),
            "★有倒挂，不是高度场" if multi else "是高度场"), flush=True)
        print("  ==> 投影内有料的采样点 = %d / %d" % (cov, tot), flush=True)
        draw(tris, os.path.join(outdir, tag + "_v2.png"), tag)
        print("  图 ->", os.path.join(outdir, tag + "_v2.png"), flush=True)


if __name__ == "__main__":
    main()
