# -*- coding: utf-8 -*-
"""测量形变面在 XY 平面上的投影覆盖率。

目的：判断 SolidWorks 的「成形到曲面」拉伸能否用【包围盒矩形】当草图轮廓。
     —— SW 要求终止曲面必须完全覆盖草图轮廓，否则重建报错 / 静默失败。

做法：把每个面按 UV 网格采样 → 投到 XY → 栅格化成掩膜 →
      统计 (a) 包围盒矩形  (b) 内缩 INSET 后的矩形  的覆盖率。
"""
import os
import sys
import glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.gp import gp_Pnt


def load_faces(path):
    r = STEPControl_Reader()
    if r.ReadFile(path) != 1:
        raise RuntimeError("读不了 " + path)
    r.TransferRoots()
    sh = r.OneShape()
    faces = []
    ex = TopExp_Explorer(sh, TopAbs_FACE)
    while ex.More():
        faces.append(TopoDS.Face_s(ex.Current()))
        ex.Next()
    return sh, faces


def bbox(sh):
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(sh, b, False, False)
    return b.Get()


def sample_xy(faces, n=44):
    """每个面采 n x n 个 UV 点，返回 [(x,y), ...]。"""
    pts = []
    for f in faces:
        try:
            a = BRepAdaptor_Surface(f)
            u1, u2 = a.FirstUParameter(), a.LastUParameter()
            v1, v2 = a.FirstVParameter(), a.LastVParameter()
            INF = 1e9
            if not (-INF < u1 < INF and -INF < u2 < INF):
                u1, u2 = 0.0, 1.0
            if not (-INF < v1 < INF and -INF < v2 < INF):
                v1, v2 = 0.0, 1.0
            if abs(u2 - u1) < 1e-12:
                u2 = u1 + 1.0
            if abs(v2 - v1) < 1e-12:
                v2 = v1 + 1.0
            for i in range(n):
                u = u1 + (u2 - u1) * i / (n - 1.0)
                for j in range(n):
                    v = v1 + (v2 - v1) * j / (n - 1.0)
                    p = a.Value(u, v)
                    pts.append((p.X(), p.Y()))
        except Exception:
            continue
    return pts


def coverage(pts, x1, y1, x2, y2, grid=160):
    """把点栅格化，返回掩膜与覆盖率查询函数。"""
    mask = bytearray(grid * grid)
    dx = (x2 - x1) / grid
    dy = (y2 - y1) / grid
    for (x, y) in pts:
        ix = int((x - x1) / dx)
        iy = int((y - y1) / dy)
        if 0 <= ix < grid and 0 <= iy < grid:
            mask[iy * grid + ix] = 1
    # 3x3 膨胀，补偿采样间隙
    out = bytearray(mask)
    for iy in range(grid):
        for ix in range(grid):
            if mask[iy * grid + ix]:
                for a in (-1, 0, 1):
                    for b in (-1, 0, 1):
                        jy, jx = iy + a, ix + b
                        if 0 <= jy < grid and 0 <= jx < grid:
                            out[jy * grid + jx] = 1
    return out, dx, dy


def rect_cov(mask, grid, x1, y1, dx, dy, ix1, iy1, ix2, iy2):
    tot = 0
    hit = 0
    for iy in range(max(0, iy1), min(grid, iy2)):
        for ix in range(max(0, ix1), min(grid, ix2)):
            tot += 1
            if mask[iy * grid + ix]:
                hit += 1
    return (hit / tot if tot else 0.0), tot - hit


def main():
    files = sys.argv[1:]
    if not files:
        files = [
            r"C:\Temp\m3test\upper_sewn2rom.stp",
            r"web_box_generator\uploads\contact_output\a5737faebbf7\543e0fdcfa2e_2ROM31-6061 ---(1)_PUNCH_CONTACT_FACES.stp",
            r"web_box_generator\uploads\contact_output\a5737faebbf7\543e0fdcfa2e_2ROM31-6061 ---(1)_CAVITY_CONTACT_FACES.stp",
            r"web_box_generator\uploads\contact_output\c37ff7241a19\f4adfe3f6156_YA-1131-505_MIRROR_xOy_PUNCH_CONTACT_FACES.stp",
            r"web_box_generator\uploads\contact_output\c37ff7241a19\f4adfe3f6156_YA-1131-505_MIRROR_xOy_CAVITY_CONTACT_FACES.stp",
            r"web_box_generator\uploads\contact_output\c547455f385a\3ac64e94d72b_YA-1238-01_MIRROR_xOy_PUNCH_CONTACT_FACES.stp",
            r"web_box_generator\uploads\contact_output\c547455f385a\3ac64e94d72b_YA-1238-01_MIRROR_xOy_CAVITY_CONTACT_FACES.stp",
        ]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print("%-52s %5s %24s %10s %10s %10s" % (
        "文件", "面数", "bbox z(mm)", "全覆盖", "内缩0.1", "内缩1.0"))
    print("-" * 120)
    for rel in files:
        p = rel if os.path.isabs(rel) else os.path.join(root, rel)
        if not os.path.exists(p):
            print("%-52s  缺失" % os.path.basename(rel))
            continue
        try:
            sh, faces = load_faces(p)
            bb = bbox(sh)
            x1, y1, z1, x2, y2, z2 = bb
            pts = sample_xy(faces)
            grid = 160
            mask, dx, dy = coverage(pts, x1, y1, x2, y2, grid)
            c_full, miss_full = rect_cov(mask, grid, x1, y1, dx, dy, 0, 0, grid, grid)

            def inset_cov(mm):
                d = mm
                ix1 = int(d / dx)
                iy1 = int(d / dy)
                ix2 = grid - ix1
                iy2 = grid - iy1
                return rect_cov(mask, grid, x1, y1, dx, dy, ix1, iy1, ix2, iy2)

            c01, m01 = inset_cov(0.1)
            c10, m10 = inset_cov(1.0)
            name = os.path.basename(p)
            if len(name) > 50:
                name = name[:47] + "..."
            print("%-52s %5d [%8.1f,%8.1f] %9.1f%% %9.1f%% %9.1f%%  (缺口格 %d)" % (
                name, len(faces), z1, z2, c_full * 100, c01 * 100, c10 * 100, miss_full))
        except Exception as e:
            print("%-52s  错误: %s" % (os.path.basename(rel), e))


if __name__ == "__main__":
    main()
