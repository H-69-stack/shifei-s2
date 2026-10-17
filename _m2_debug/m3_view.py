# -*- coding: utf-8 -*-
"""把形变面画出来：俯视图 + 轴测图 + 侧视剖切。
用来看清楚它到底是"浅碟形（高度场）"还是"深盆形（有竖直壁）"，
这直接决定 SW 的『成形到曲面 / 曲面切除』能不能用。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

from OCP.STEPControl import STEPControl_Reader
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.BRep import BRep_Tool
from OCP.TopLoc import TopLoc_Location
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box


def load(path):
    r = STEPControl_Reader()
    r.ReadFile(path)
    r.TransferRoots()
    return r.OneShape()


def triangulate(sh, tol=0.6):
    BRepMesh_IncrementalMesh(sh, tol, False, 0.5, True)
    tris = []
    ex = TopExp_Explorer(sh, TopAbs_FACE)
    while ex.More():
        f = TopoDS.Face_s(ex.Current())
        loc = TopLoc_Location()
        h = BRep_Tool.Triangulation_s(f, loc)
        if h is not None:
            trsf = loc.Transformation()
            for i in range(1, h.NbTriangles() + 1):
                t = h.Triangle(i)
                a, b, c = t.Get()
                pts = []
                for n in (a, b, c):
                    p = h.Node(n).Transformed(trsf)
                    pts.append((p.X(), p.Y(), p.Z()))
                tris.append(pts)
        ex.Next()
    return tris


def stats(sh, faces, tris):
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(sh, b, False, False)
    x1, y1, z1, x2, y2, z2 = b.Get()
    print("bbox  x[%.1f,%.1f] y[%.1f,%.1f] z[%.1f,%.1f]  dx=%.1f dy=%.1f dz=%.1f" % (
        x1, x2, y1, y2, z1, z2, x2 - x1, y2 - y1, z2 - z1))
    print("面数 =", len(faces))

    # 每个面：是不是平面？法向偏多少度？
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_SurfaceType
    kinds = {}
    nvert = 0
    for f in faces:
        a = BRepAdaptor_Surface(f)
        t = a.GetType()
        kinds[t] = kinds.get(t, 0) + 1
    for k, v in kinds.items():
        print("   面类型 %-24s x%d" % (str(k).split('.')[-1], v))

    # 竖直性检测：面的包围盒 dz vs 面面积投影
    return (x1, y1, z1, x2, y2, z2)


def plot(tris, out, title):
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    fig = plt.figure(figsize=(16, 10))
    ax1 = fig.add_subplot(2, 2, 1, projection="3d")
    pc = Poly3DCollection(tris, alpha=0.85)
    pc.set_facecolor("steelblue")
    pc.set_edgecolor("none")
    ax1.add_collection3d(pc)
    allp = [p for t in tris for p in t]
    xs = [p[0] for p in allp]; ys = [p[1] for p in allp]; zs = [p[2] for p in allp]
    ax1.set_xlim(min(xs), max(xs)); ax1.set_ylim(min(ys), max(ys)); ax1.set_zlim(min(zs), max(zs))
    try:
        ax1.set_box_aspect((max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)))
    except Exception:
        pass
    ax1.set_title("axonometric")

    # 俯视（XY 投影）
    ax2 = fig.add_subplot(2, 2, 2)
    for t in tris:
        ax2.fill([p[0] for p in t], [p[1] for p in t], facecolor="steelblue",
                 edgecolor="none", alpha=0.9)
    ax2.set_aspect("equal")
    ax2.set_title("top view (XY projection)")
    ax2.grid(True, lw=0.3)

    # 前视（XZ）—— 看有没有竖直壁/倒钩
    ax3 = fig.add_subplot(2, 2, 3)
    for t in tris:
        ax3.fill([p[0] for p in t], [p[2] for p in t], facecolor="indianred",
                 edgecolor="none", alpha=0.9)
    ax3.set_title("front view (XZ)")
    ax3.grid(True, lw=0.3)

    # 侧视（YZ）
    ax4 = fig.add_subplot(2, 2, 4)
    for t in tris:
        ax4.fill([p[1] for p in t], [p[2] for p in t], facecolor="seagreen",
                 edgecolor="none", alpha=0.9)
    ax4.set_title("side view (YZ)")
    ax4.grid(True, lw=0.3)

    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(out, dpi=90)
    print("图 ->", out)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    items = [
        (r"C:\Temp\m3test\upper_sewn2rom.stp", "2ROM upper"),
        (os.path.join(root, r"web_box_generator\uploads\contact_output\a5737faebbf7\543e0fdcfa2e_2ROM31-6061 ---(1)_CAVITY_CONTACT_FACES.stp"), "2ROM cavity(lower)"),
        (os.path.join(root, r"web_box_generator\uploads\contact_output\c37ff7241a19\f4adfe3f6156_YA-1131-505_MIRROR_xOy_PUNCH_CONTACT_FACES.stp"), "1131 upper"),
        (os.path.join(root, r"web_box_generator\uploads\contact_output\c37ff7241a19\f4adfe3f6156_YA-1131-505_MIRROR_xOy_CAVITY_CONTACT_FACES.stp"), "1131 lower"),
    ]
    outdir = os.path.join(root, "_m2_debug", "m3_figs")
    os.makedirs(outdir, exist_ok=True)
    for path, tag in items:
        if not os.path.exists(path):
            print("缺失", path); continue
        print("=" * 70)
        print(tag, path)
        sh = load(path)
        faces = []
        ex = TopExp_Explorer(sh, TopAbs_FACE)
        while ex.More():
            faces.append(TopoDS.Face_s(ex.Current())); ex.Next()
        tris = triangulate(sh)
        stats(sh, faces, tris)
        print("三角面片 =", len(tris))
        out = os.path.join(outdir, tag.replace(" ", "_").replace("(", "").replace(")", "") + ".png")
        plot(tris, out, tag)


if __name__ == "__main__":
    main()
