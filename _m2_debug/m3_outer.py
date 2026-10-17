# -*- coding: utf-8 -*-
"""算出形变面在 XY 上的【最外投影轮廓多边形】，内缩一点点，输出点表给 VBA 用。

为什么不用 SW 自己的「转换实体引用」：
    实测对曲面体返回 True 但 0 个实体；逐个选【边】能产出 50 个实体，
    但那 50 条里混着【面与面之间的内部接缝】和【孔洞环】——
    内部接缝让轮廓不干净，孔洞环让「成形到曲面」在洞的位置找不到终止面。
    而「成形到曲面」要求终止曲面【完整盖住草图轮廓】，所以轮廓必须是
    【只含最外环、且严格落在投影内部】的干净多边形。

做法：
    三角网格 → 投影到 XY → 栅格化成掩膜 → 腐蚀（内缩）→
    marching squares 取面积最大的闭合环 → Douglas-Peucker 简化 → 写点表
    单元 0.25mm，内缩 1.5mm，保证严格在投影内部。
"""
import os
import sys
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
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

CELL = 0.2          # mm / 栅格
INSET = 0.5         # mm 内缩（★不能大：这个量就是上模四周比形变面【少】多少★）
RDP_TOL = 0.25      # mm 简化容差
MESH_TOL = 0.08     # mm 网格弦高容差（越小投影越准）


def load(path):
    r = STEPControl_Reader()
    if r.ReadFile(path) != 1:
        raise RuntimeError("读不了 " + path)
    r.TransferRoots()
    return r.OneShape()


def bbox(sh):
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(sh, b, False, False)
    return b.Get()


def mesh_xy(sh, tol=MESH_TOL):
    """返回投影到 XY 的三角形列表 [(x0,y0,x1,y1,x2,y2), ...]（mm）。"""
    BRepMesh_IncrementalMesh(sh, tol, False, 0.3, True)
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
                ps = [h.Node(n).Transformed(trsf) for n in (a, b, c)]
                tris.append((ps[0].X(), ps[0].Y(), ps[1].X(), ps[1].Y(), ps[2].X(), ps[2].Y()))
        ex.Next()
    return tris


def rasterize(tris, x1, y1, x2, y2, cell):
    """把投影三角形按【单元中心】落格成掩膜。

    单元 i 覆盖 x∈[x1+i*cell, x1+(i+1)*cell)，中心 x1+(i+0.5)*cell。
    ★全程用毫米，绝不混用栅格索引★（上一版就是混用导致面积只剩一半）。
    """
    W = int(math.ceil((x2 - x1) / cell)) + 2
    H = int(math.ceil((y2 - y1) / cell)) + 2
    mask = np.zeros((H, W), dtype=bool)

    for (ax, ay, bx, by, cx, cy) in tris:
        v = sorted([(ay, ax), (by, bx), (cy, cx)])
        j0 = int(math.ceil((v[0][0] - y1) / cell - 0.5))
        j1 = int(math.floor((v[2][0] - y1) / cell - 0.5))
        j0 = max(0, j0)
        j1 = min(H - 1, j1)
        for j in range(j0, j1 + 1):
            yc = y1 + (j + 0.5) * cell
            xs = []
            for (p, q) in ((v[0], v[1]), (v[1], v[2]), (v[0], v[2])):
                ya, xa = p
                yb, xb = q
                if ya == yb:
                    if abs(yc - ya) < 1e-9:
                        xs.append(xa)
                        xs.append(xb)
                    continue
                if (ya - 1e-9) <= yc <= (yb + 1e-9):
                    t = (yc - ya) / (yb - ya)
                    xs.append(xa + t * (xb - xa))
            if xs:
                lo = int(math.ceil((min(xs) - x1) / cell - 0.5))
                hi = int(math.floor((max(xs) - x1) / cell - 0.5))
                lo = max(0, lo)
                hi = min(W - 1, hi)
                if hi >= lo:
                    mask[j, lo:hi + 1] = True
    return mask, W, H


def erode(mask, k):
    """k 单元腐蚀（积分图向量化，无 Python 循环）。"""
    if k <= 0:
        return mask
    H, W = mask.shape
    m = mask.astype(np.int32)
    mp = np.pad(m, k, constant_values=0)
    ii = np.cumsum(np.cumsum(mp, axis=0), axis=1)
    ii = np.pad(ii, ((1, 0), (1, 0)))
    d = 2 * k + 1
    S = (ii[d:, d:] - ii[:-d, d:] - ii[d:, :-d] + ii[:-d, :-d])
    return S == d * d


def shoelace(p):
    s = 0.0
    n = len(p)
    for i in range(n):
        x1, y1 = p[i]
        x2, y2 = p[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


def rdp(pts, tol):
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        (x1, y1), (x2, y2) = pts[i], pts[j]
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        best, bi = -1.0, -1
        for k in range(i + 1, j):
            px, py = pts[k]
            if L < 1e-12:
                d = math.hypot(px - x1, py - y1)
            else:
                d = abs(dy * (px - x1) - dx * (py - y1)) / L
            if d > best:
                best, bi = d, k
        if best > tol:
            keep[bi] = True
            stack.append((i, bi))
            stack.append((bi, j))
    return [pts[k] for k in range(len(pts)) if keep[k]]


def process(path, tag, outdir):
    sh = load(path)
    x1, y1, z1, x2, y2, z2 = bbox(sh)
    tris = mesh_xy(sh)
    print("  bbox x[%.1f,%.1f] y[%.1f,%.1f]  三角= %d" % (x1, x2, y1, y2, len(tris)))
    mask, W, H = rasterize(tris, x1, y1, x2, y2, CELL)
    cov = mask.sum() * CELL * CELL
    print("  投影面积 = %.0f mm^2   掩膜 %dx%d" % (cov, W, H))

    k = int(round(INSET / CELL))
    er = erode(mask, k)
    if er.sum() == 0:
        print("  !! 腐蚀后为空，减小 INSET")
        return None

    xs = x1 + (np.arange(W) + 0.5) * CELL
    ys = y1 + (np.arange(H) + 0.5) * CELL
    cs = plt.contour(xs, ys, er.astype(float), levels=[0.5])
    loops = cs.allsegs[0] if cs.allsegs else []
    if not loops:
        print("  !! 没提到轮廓")
        return None
    loops = [l for l in loops if len(l) >= 4]
    loops.sort(key=lambda l: -shoelace(l))
    outer = loops[0]
    pts = [(float(p[0]), float(p[1])) for p in outer]
    simp = rdp(pts, RDP_TOL)
    # ★必须去掉重复的收尾点★
    #   matplotlib 的轮廓数组首尾点重合，如果原样写出去，VBA 会画出
    #   "点n -> 点1" 这条零长度线段 → 草图轮廓无效 → 拉伸返回 Nothing。
    if len(simp) >= 2:
        dx0 = simp[-1][0] - simp[0][0]
        dy0 = simp[-1][1] - simp[0][1]
        if math.hypot(dx0, dy0) < 1e-6:
            simp = simp[:-1]
    print("  轮廓点 %d -> 简化后 %d   面积=%.0f mm^2 (原投影 %.0f)" % (
        len(pts), len(simp), shoelace(simp), cov))

    dst = os.path.join(outdir, tag + "_poly.txt")
    with open(dst, "w", encoding="ascii") as f:
        f.write("%d\n" % len(simp))
        for (px, py) in simp:
            f.write("%.4f %.4f\n" % (px, py))
    print("  ->", dst, os.path.getsize(dst), "bytes")

    # 出图核对
    fig, ax = plt.subplots(figsize=(9, 6))
    for t in tris:
        ax.fill([t[0], t[2], t[4]], [t[1], t[3], t[5]], facecolor="lightsteelblue",
                edgecolor="none", alpha=0.8)
    cxs = [p[0] for p in simp] + [simp[0][0]]
    cys = [p[1] for p in simp] + [simp[0][1]]
    ax.plot(cxs, cys, "r-", lw=1.2)
    ax.set_aspect("equal")
    ax.set_title("%s  outer outline (%d pts, inset %.1fmm)" % (tag, len(simp), INSET))
    ax.grid(lw=0.3)
    png = os.path.join(outdir, tag + "_outline.png")
    fig.tight_layout()
    fig.savefig(png, dpi=85)
    plt.close(fig)
    print("  ->", png)
    return dst


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    outdir = r"C:\Temp\m3test"
    os.makedirs(outdir, exist_ok=True)
    items = [
        (r"C:\Temp\m3test\upper_sewn2rom.stp", "2ROM_upper"),
        (r"C:\Temp\m3test\lower_sewn2rom.stp", "2ROM_lower"),
        (os.path.join(root, r"web_box_generator\uploads\contact_output\c37ff7241a19\f4adfe3f6156_YA-1131-505_MIRROR_xOy_PUNCH_CONTACT_FACES.stp"),
         "1131_upper"),
        (os.path.join(root, r"web_box_generator\uploads\contact_output\c37ff7241a19\f4adfe3f6156_YA-1131-505_MIRROR_xOy_CAVITY_CONTACT_FACES.stp"),
         "1131_lower"),
    ]
    for path, tag in items:
        if not os.path.exists(path):
            print("缺失", path)
            continue
        print("=" * 70)
        print(tag)
        try:
            process(path, tag, outdir)
        except Exception as e:
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
