# -*- coding: utf-8 -*-
"""法三成果验证：干涉检查 + 剖面 + 与法二A/形变面的一致性 + 出图。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from OCP.STEPControl import STEPControl_Reader
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID, TopAbs_SHELL
from OCP.TopoDS import TopoDS
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRep import BRep_Tool
from OCP.TopLoc import TopLoc_Location
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepCheck import BRepCheck_Analyzer

OUT = None


def load(p):
    r = STEPControl_Reader()
    if r.ReadFile(p) != 1:
        raise RuntimeError("读不了 " + p)
    r.TransferRoots()
    return r.OneShape()


def vol(sh):
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(sh, g)
    return abs(g.Mass())


def bb(sh):
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(sh, b, False, False)
    return b.Get()


def count(sh, kind):
    n = 0
    ex = TopExp_Explorer(sh, kind)
    while ex.More():
        n += 1; ex.Next()
    return n


def mesh(sh, tol=0.4):
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


def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "m3_out")
    files = {
        "punch": os.path.join(out, "2ROM_PUNCH_M3C.stp"),
        "cavity": os.path.join(out, "2ROM_CAVITY_M3.stp"),
        "upper": r"C:\Temp\m3test\upper_filled2rom.stp",
        "lower": r"C:\Temp\m3test\lower_filled2rom.stp",
    }
    sh = {}
    print("=" * 78)
    for k, p in files.items():
        if not os.path.exists(p):
            print("missing", k, p); return
        sh[k] = load(p)
        v = vol(sh[k])
        x1, y1, z1, x2, y2, z2 = bb(sh[k])
        print("%-7s solid=%d shell=%d face=%2d  vol=%12.1f mm^3  bbox x[%.1f,%.1f] y[%.1f,%.1f] z[%.1f,%.1f]" % (
            k, count(sh[k], TopAbs_SOLID), count(sh[k], TopAbs_SHELL), count(sh[k], TopAbs_FACE),
            v, x1, x2, y1, y2, z1, z2))
        print("        BRepCheck valid =", BRepCheck_Analyzer(sh[k]).IsValid())

    print("=" * 78)
    print("[INTERFERENCE] punch n cavity")
    cut = BRepAlgoAPI_Common(sh["punch"], sh["cavity"])
    cut.SetRunParallel(False); cut.Build()
    if cut.IsDone():
        ic = vol(cut.Shape())
        print("  common volume = %.4f mm^3   %s" % (ic, "OK no interference" if ic < 1.0 else "!! INTERFERENCE"))
    else:
        print("  Common failed")

    print("\n[MEASURES]")
    print("  punch  height = %.2f mm (bottom=upper surface min, top=P)" % (bb(sh["punch"])[5] - bb(sh["punch"])[2]))
    print("  cavity height = %.2f mm" % (bb(sh["cavity"])[5] - bb(sh["cavity"])[2]))

    # 3 views: punch + cavity + the two deformation surfaces
    fig = plt.figure(figsize=(16, 9))
    views = [("TOP (XY)", 0, 1), ("FRONT (XZ)", 0, 2), ("SIDE (YZ)", 1, 2)]
    for idx, (title, ia, ib) in enumerate(views):
        ax = fig.add_subplot(2, 2, idx + 1)
        for key, col, al in (("cavity", "seagreen", 0.45), ("punch", "indianred", 0.55)):
            for t in mesh(sh[key]):
                ax.fill([p[ia] for p in t], [p[ib] for p in t], facecolor=col,
                        edgecolor="none", alpha=al)
        for key, col in (("upper", "blue"), ("lower", "orange")):
            for t in mesh(sh[key]):
                ax.plot([p[ia] for p in t] + [t[0][ia]], [p[ib] for p in t] + [t[0][ib]],
                        color=col, lw=0.25, alpha=0.5)
        ax.set_title("%s  red=PUNCH green=CAVITY blue=upperSurf orange=lowerSurf" % title)
        ax.set_aspect("equal")
        ax.grid(lw=0.3)
    ax = fig.add_subplot(2, 2, 4, projection="3d")
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    for key, col in (("punch", "indianred"), ("cavity", "seagreen")):
        pc = Poly3DCollection(mesh(sh[key], tol=1.0), alpha=0.35)
        pc.set_facecolor(col); pc.set_edgecolor("none")
        ax.add_collection3d(pc)
    x1, y1, z1, x2, y2, z2 = bb(sh["cavity"])
    ax.set_xlim(x1, x2); ax.set_ylim(y1, y2); ax.set_zlim(z1, z2)
    ax.set_title("3D  punch(red) + cavity(green)")
    png = os.path.join(out, "2ROM_M3_verify.png")
    fig.tight_layout(); fig.savefig(png, dpi=85); plt.close(fig)
    print("\nfigure ->", png)


if __name__ == "__main__":
    main()
