# -*- coding: utf-8 -*-
"""形变面预处理。

形变面（mold_contact_surface_extractor 的产物）是"一堆散面"，
直接喂给 SolidWorks / OCC 都会出问题。这里做三件事：

  1. sew()            —— 散面缝成单一 SHELL（否则 SW 会把每片面拆成独立文档）
  2. fill_holes()     —— 补掉形变面上的孔（★「成形到曲面」要求曲面盖住草图轮廓，
                          孔的位置没有曲面可终止 → 拉伸必定失败。实测确认过★）
  3. projection_outline() —— 算 XY 上的【最外投影轮廓】多边形。
                          形变面投影只有包围盒的 76~92%（1131 是机头罩壳，半椭圆），
                          用包围盒矩形当轮廓必然失败。内缩一点保证严格落在投影内部。
"""
import math
import os
import numpy as np

from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_WIRE
from OCP.TopoDS import TopoDS
from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing, BRepBuilderAPI_MakeFace
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeFilling
from OCP.GeomAbs import GeomAbs_Shape
from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
from OCP.TopTools import TopTools_HSequenceOfShape, TopTools_IndexedDataMapOfShapeListOfShape
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRep import BRep_Tool
from OCP.TopLoc import TopLoc_Location

from . import geo


# ---------------------------------------------------------------------------
# 缝合
# ---------------------------------------------------------------------------
def sew(shape, tol=1e-3):
    faces = []
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        faces.append(TopoDS.Face_s(ex.Current()))
        ex.Next()
    if len(faces) <= 1:
        return shape
    s = BRepBuilderAPI_Sewing(tol)
    for f in faces:
        s.Add(f)
    s.Perform()
    res = s.SewedShape()
    return res if res is not None and not res.IsNull() else shape


def free_edges(shape):
    """只有 1 个相邻面的边 = 自由边（壳的边界）。"""
    m = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, m)
    out = []
    for i in range(1, m.Extent() + 1):
        if m.FindFromIndex(i).Extent() == 1:
            out.append(m.FindKey(i))
    return out


def free_wires(shape, tol=1e-3):
    edges = free_edges(shape)
    if not edges:
        return []
    seq = TopTools_HSequenceOfShape()
    for e in edges:
        seq.Append(e)
    wires = TopTools_HSequenceOfShape()
    try:
        ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(seq, tol, False, wires)
    except TypeError:
        return []
    return [TopoDS.Wire_s(wires.Value(i)) for i in range(1, wires.Length() + 1)]


def _wire_span(w):
    b = Bnd_Box()
    BRepBndLib.Add_s(w, b, False)
    x1, y1, z1, x2, y2, z2 = b.Get()
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2)


# ---------------------------------------------------------------------------
# 补孔
# ---------------------------------------------------------------------------
def _fill_one_wire(w, log=print):
    # ① 平面补片（板料圆孔基本都落在平面上）
    try:
        mf = BRepBuilderAPI_MakeFace(w, False)
        if mf.IsDone():
            f = mf.Face()
            if f is not None and not f.IsNull():
                return f
    except Exception as e:
        log("      MakeFace 异常: %s" % e)
    # ② 曲面补片：G0 相接的自由曲面
    mk = BRepOffsetAPI_MakeFilling(3, 15, 2, False, 1e-5, 1e-4, 1e-2, 0.1, 8, 9)
    n = 0
    ex = TopExp_Explorer(w, TopAbs_EDGE)
    while ex.More():
        mk.Add(TopoDS.Edge_s(ex.Current()), GeomAbs_Shape.GeomAbs_C0)
        n += 1
        ex.Next()
    if n < 3:
        return None
    mk.Build()
    if not mk.IsDone() or mk.Shape().IsNull():
        return None
    return mk.Shape()


def fill_holes(shape, tol=1e-3, min_hole_span=2.0, log=print):
    """把形变面上的孔补平。

    返回 (新形状, 报告 dict)。没有孔时原样返回。
    """
    rep = {"wires": 0, "holes": 0, "filled": 0, "failed": 0}
    wires = free_wires(shape, tol)
    rep["wires"] = len(wires)
    if len(wires) < 2:
        log("  没有孔（自由边环数=%d）" % len(wires))
        return shape, rep

    spans = sorted(((_wire_span(w), w) for w in wires), key=lambda t: -t[0])
    holes = spans[1:]
    rep["holes"] = len(holes)
    log("  外边界跨距=%.1fmm，孔候选=%d 个" % (spans[0][0], len(holes)))

    faces = []
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        faces.append(TopoDS.Face_s(ex.Current()))
        ex.Next()

    patches = []
    for span, w in holes:
        if span < min_hole_span:
            log("    跳过太小的环 (跨距 %.2f)" % span)
            continue
        f = _fill_one_wire(w, log)
        if f is None:
            rep["failed"] += 1
            log("    !! 补孔失败 (跨距 %.1f)" % span)
        else:
            patches.append(f)
            rep["filled"] += 1
    if not patches:
        log("  没有补上任何孔 → 原样返回")
        return shape, rep

    s = BRepBuilderAPI_Sewing(tol)
    for f in faces:
        s.Add(f)
    for p in patches:
        s.Add(p)
    s.Perform()
    res = s.SewedShape()
    # B15：缝合可能返回空 shape，直接访问 ShapeType() 会崩；与上文 sew() 一致加守卫
    if res is None or res.IsNull():
        log("  补孔后缝合失败（返回空），退回原 shape")
        return shape, rep
    log("  补孔后缝合：类型=%s 自由边=%d 多重边=%d" %
        (res.ShapeType(), s.NbFreeEdges(), s.NbMultipleEdges()))
    return res, rep


# ---------------------------------------------------------------------------
# 投影轮廓
# ---------------------------------------------------------------------------
CELL = 0.1          # mm / 栅格（仅栅格兜底路线用）
RASTER_MAX_CELLS = 30_000_000   # P15：栅格单元数上限（bool 掩码 ~30MB，
                                # 腐蚀还要 int32 累加缓冲）。原实现无上限，
                                # 1000mm 零件配 cell=0.1 即 10⁸ 格，有 OOM 风险。
INSET = 0.0         # mm 内缩。★默认 0★：这个量就是上模四周比形变面"少"多少、
                    # 也是下模型腔四周残留立墙的厚度（实测 0.5mm 时残料 7588 mm³）。
                    # 0 时走 HLR 精确轮廓；>0 时退回栅格路线（HLR 的 2D 内缩暂未做）。
RDP_TOL = 0.25      # mm 简化容差
MESH_TOL = 0.08     # mm 网格弦高容差


def _mesh_xy(shape, tol=MESH_TOL):
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
                tris.append((ps[0].X(), ps[0].Y(), ps[1].X(), ps[1].Y(),
                             ps[2].X(), ps[2].Y()))
        ex.Next()
    return tris


def _rasterize(tris, x1, y1, x2, y2, cell):
    """投影三角形按【单元中心】落格。★全程用毫米，绝不混用栅格索引★

    P15：W*H 超过 RASTER_MAX_CELLS 时自动放大 cell（兜底路线宁粗勿崩），
    并告警。原实现无上限：1000mm 零件配 cell=0.1 即 10⁸ 格，掩码加腐蚀
    缓冲轻松破 GB，有 OOM 风险。
    """
    span = max(x2 - x1, y2 - y1)
    if span > 0:
        need = span / math.sqrt(RASTER_MAX_CELLS)
        if cell < need:
            cell = need
            print("  [P15] 栅格单元数超上限 %d，cell 自动放大到 %.3fmm"
                  "（投影跨度 %.0fmm）" % (RASTER_MAX_CELLS, cell, span))
    W = int(math.ceil((x2 - x1) / cell)) + 2
    H = int(math.ceil((y2 - y1) / cell)) + 2
    mask = np.zeros((H, W), dtype=bool)
    for (ax, ay, bx, by, cx, cy) in tris:
        v = sorted([(ay, ax), (by, bx), (cy, cx)])
        j0 = max(0, int(math.ceil((v[0][0] - y1) / cell - 0.5)))
        j1 = min(H - 1, int(math.floor((v[2][0] - y1) / cell - 0.5)))
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
                lo = max(0, int(math.ceil((min(xs) - x1) / cell - 0.5)))
                hi = min(W - 1, int(math.floor((max(xs) - x1) / cell - 0.5)))
                if hi >= lo:
                    mask[j, lo:hi + 1] = True
    return mask, W, H


def _erode(mask, k):
    if k <= 0:
        return mask
    H, W = mask.shape
    m = mask.astype(np.int32)
    mp = np.pad(m, k, constant_values=0)
    ii = np.cumsum(np.cumsum(mp, axis=0), axis=1)
    ii = np.pad(ii, ((1, 0), (1, 0)))
    d = 2 * k + 1
    S = ii[d:, d:] - ii[:-d, d:] - ii[d:, :-d] + ii[:-d, :-d]
    return S == d * d


def _rdp(pts, tol):
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
            d = math.hypot(px - x1, py - y1) if L < 1e-12 else \
                abs(dy * (px - x1) - dx * (py - y1)) / L
            if d > best:
                best, bi = d, k
        if best > tol:
            keep[bi] = True
            stack.append((i, bi))
            stack.append((bi, j))
    return [pts[k] for k in range(len(pts)) if keep[k]]


def _clean_polygon(pts, collinear_tol=0.05):
    """去掉重合点与"共线折返"点。

    ★为什么必须做★：轮廓是从【栅格掩膜】提出来的，边界天然带台阶状小折返 ——
    这些点在几何上几乎共线，但会让草图出现"往回走"的短线段，
    SolidWorks 会判定轮廓无效 → 拉伸返回 Nothing。
    实测 YA-1131：81 点轮廓（无自相交、无零长段）照样被拒，
    而同一位置的矩形就能拉伸成功 —— 差别就在这些台阶点。

    做法：反复删除"到相邻两点连线距离 < collinear_tol"的点，直到稳定。
    """
    if len(pts) < 4:
        return pts
    # 去掉重合点
    out = [pts[0]]
    for p in pts[1:]:
        if math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > 1e-9:
            out.append(p)
    pts = out
    changed = True
    while changed and len(pts) > 4:
        changed = False
        out = []
        n = len(pts)
        for i in range(n):
            ax, ay = pts[(i - 1) % n]
            bx, by = pts[i]
            cx, cy = pts[(i + 1) % n]
            L = math.hypot(cx - ax, cy - ay)
            if L < 1e-12:
                d = math.hypot(bx - ax, by - ay)
            else:
                d = abs((cx - ax) * (by - ay) - (cy - ay) * (bx - ax)) / L
            if d < collinear_tol:
                changed = True          # b 几乎是直线上的点，丢掉
            else:
                out.append(pts[i])
        if len(out) >= 4:
            pts = out
        else:
            break
    return pts


def _draw_outline_png(tris, polys, out_png, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9, 6))
    for t in tris:
        ax.fill([t[0], t[2], t[4]], [t[1], t[3], t[5]],
                facecolor="lightsteelblue", edgecolor="none", alpha=0.8)
    for i, poly in enumerate(polys):
        cx = [p[0] for p in poly] + [poly[0][0]]
        cy = [p[1] for p in poly] + [poly[0][1]]
        ax.plot(cx, cy, "r-" if i == 0 else "m-", lw=1.2)
    ax.set_aspect("equal")
    ax.set_title(title)
    ax.grid(lw=0.3)
    fig.tight_layout()
    fig.savefig(out_png, dpi=85)
    plt.close(fig)


def _hlr_outline(shape, rdp_tol=RDP_TOL, defl=0.2, log=print):
    """用 OCC 的消隐（HLR）算【精确】XY 投影轮廓。

    ★为什么换掉栅格法★
    栅格法是"三角形投影 -> 栅格化 -> matplotlib 提环"，边界天然带台阶状折返；
    实测 YA-1131 的 81 点栅格轮廓（无自相交、无零长段、面积正确）**仍被
    SolidWorks 拒绝当作拉伸轮廓**，而 HLR 给出的解析轮廓被接受。
    """
    from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
    from OCP.HLRAlgo import HLRAlgo_Projector
    from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection

    algo = HLRBRep_Algo()
    algo.Add(shape)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0))))
    algo.Update()
    algo.Hide()
    hlr = HLRBRep_HLRToShape(algo)

    def sample(s):
        out = []
        ex = TopExp_Explorer(s, TopAbs_EDGE)
        while ex.More():
            e = TopoDS.Edge_s(ex.Current())
            try:
                c = BRepAdaptor_Curve(e)
                d = GCPnts_QuasiUniformDeflection(c, defl)
                if d.IsDone() and d.NbPoints() >= 2:
                    out.append([(d.Value(i).X(), d.Value(i).Y())
                                for i in range(1, d.NbPoints() + 1)])
            except Exception:
                pass
            ex.Next()
        return out

    def chain(pieces):
        """把边链接成闭合环。

        P15：原实现对每个待接片段都全表扫描所有 pieces（外层还套扩展
        循环），最坏 O(P³)。改为端点空间哈希（量化格 + 3×3 邻域）后
        候选查找 O(1) 均摊。择优语义保持不变：片段序号最小者优先，
        同片段起点匹配优先于终点匹配。
        """
        def close(a, b, tol=0.05):
            return math.hypot(a[0] - b[0], a[1] - b[1]) <= tol
        n = len(pieces)
        used = [False] * n
        # 量化格尺寸 = tol：容差内两端点的索引格相差至多 1，3×3 邻域必覆盖
        q = 0.05
        grid = {}
        for j, pc in enumerate(pieces):
            for pt in (pc[0], pc[-1]):
                key = (int(math.floor(pt[0] / q)), int(math.floor(pt[1] / q)))
                grid.setdefault(key, []).append(j)

        def find_next(pt):
            """返回 (j, 是否反向)：未用片段中序号最小、且端点与 pt 接得上者。"""
            kx = int(math.floor(pt[0] / q))
            ky = int(math.floor(pt[1] / q))
            best_j, best_rev = -1, False
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j in grid.get((kx + dx, ky + dy), ()):
                        if used[j] or (best_j >= 0 and j >= best_j):
                            continue
                        pc = pieces[j]
                        if close(pt, pc[0]):
                            best_j, best_rev = j, False
                        elif close(pt, pc[-1]):
                            best_j, best_rev = j, True
            return best_j, best_rev

        loops = []
        for i in range(n):
            if used[i]:
                continue
            used[i] = True
            cur = list(pieces[i])
            for _ in range(n * 2):
                if len(cur) > 2 and close(cur[-1], cur[0]):
                    break
                hit, rev = find_next(cur[-1])
                if hit < 0:
                    break
                used[hit] = True
                nxt = list(reversed(pieces[hit])) if rev else pieces[hit]
                cur.extend(nxt[1:])
            if len(cur) >= 4 and close(cur[-1], cur[0], 0.1):
                loops.append(cur)
        return loops

    def to_polys(loops):
        polys = []
        for lp in loops:
            simp = _rdp(lp, rdp_tol)
            if len(simp) >= 2:
                dx = simp[-1][0] - simp[0][0]
                dy = simp[-1][1] - simp[0][1]
                if math.hypot(dx, dy) < 1e-6:
                    simp = simp[:-1]
            if len(simp) >= 3:
                simp = _clean_polygon(simp)
            if len(simp) >= 3:
                polys.append(simp)
        polys.sort(key=lambda q: -geo.polyline_area(q))
        return polys

    # ★逐层级单独提环，取最好的那个★
    #   不能把几层的边混在一起链接：Rg1Line 是"面与面之间的接缝"，
    #   混进去会让贪心链接在岔口断掉，最外环反而只剩一小块
    #   （实测 1131：混起来最外环只有 5554 mm²，单独用 VCompound 是 139437）。
    best = None
    for name in ("OutLineVCompound", "VCompound", "Rg1LineVCompound"):
        try:
            s = getattr(hlr, name)()
        except Exception:
            continue
        if s is None or s.IsNull():
            continue
        pieces = sample(s)
        if not pieces:
            continue
        pieces = [q for q in pieces if len(q) >= 2]
        polys = to_polys(chain(pieces))
        area = geo.polyline_area(polys[0]) if polys else 0.0
        log("  HLR %-18s %3d 条边 -> %2d 环，最外环 %s 面积=%.0f"
            % (name, len(pieces), len(polys), len(polys[0]) if polys else 0, area))
        if polys and (best is None or area > best[0]):
            best = (area, polys, name, len(pieces))
    if best is None:
        return None, {"hlr_edges": 0, "hlr_loops": 0}
    area, polys, name, nedge = best
    log("  HLR 采用 %s：最外环 %d 点 面积=%.0f" % (name, len(polys[0]), area))
    return polys, {"hlr_edges": nedge, "hlr_loops": len(polys),
                   "hlr_outer_points": len(polys[0]), "hlr_area": area,
                   "hlr_level": name}


def projection_outline(shape, out_png=None, cell=CELL, inset=INSET,
                       rdp_tol=RDP_TOL, log=print):
    """算 XY 最外投影轮廓。

    返回 (polygons, info)；polygons[0] 是最外环，其后是内环（洞）。
    ★点表里不含重复的收尾点★（否则 VBA 会画一条零长度线段，草图直接失效）。

    P15：HLR 成功（默认路线）时不再无条件跑全分辨率纯 Python 栅格化——
    面积校核只用粗栅格估计（≤1024 格/边，像素化误差 <1%，校核容差 15%），
    全分辨率栅格 + matplotlib 仅在真正退回栅格路线时才做。
    """
    x1, y1, z1, x2, y2, z2 = geo.bbox(shape)
    tris = _mesh_xy(shape)
    # 粗栅格面积估计：保持"掩膜并集"语义（投影重叠不重复计数），
    # 但把格数压到 ≤1024²，HLR 成功时这就是唯一的栅格开销。
    span = max(x2 - x1, y2 - y1)
    chk_cell = max(cell, span / 1024.0) if span > 0 else cell
    mask, W, H = _rasterize(tris, x1, y1, x2, y2, chk_cell)
    proj_area = float(mask.sum()) * chk_cell * chk_cell

    # ★首选 HLR 精确轮廓★
    #   栅格轮廓带台阶折返，会被 SolidWorks 拒绝当拉伸轮廓（实测 YA-1131）。
    #   inset>0 时 HLR 还要做 2D 内缩（先不做），所以只在 inset=0（默认）时启用。
    if inset <= 1e-9:
        hlr_polys, hlr_info = None, {}
        try:
            hlr_polys, hlr_info = _hlr_outline(shape, rdp_tol=rdp_tol, log=log)
        except Exception as e:
            log("  HLR 轮廓失败: %s" % e)
        if hlr_polys:
            a = geo.polyline_area(hlr_polys[0])
            ratio = (a / proj_area) if proj_area else 0.0
            log("  HLR: %d 边 -> %d 环，最外环 %d 点 面积=%.0f；"
                "栅格投影面积=%.0f  比值=%.3f"
                % (hlr_info.get("hlr_edges", 0), hlr_info.get("hlr_loops", 0),
                   len(hlr_polys[0]), a, proj_area, ratio))
            if 0.85 <= ratio <= 1.15:
                info = {"bbox": [x1, y1, z1, x2, y2, z2],
                        "projection_area": proj_area,
                        "outline_area": a,
                        "outer_points": len(hlr_polys[0]),
                        "loops": len(hlr_polys),
                        "cell": cell, "inset": inset,
                        "method": "hlr"}
                info.update(hlr_info)
                if out_png:
                    _draw_outline_png(tris, hlr_polys, out_png,
                                      "HLR outline (%d loops, %d pts)"
                                      % (len(hlr_polys), len(hlr_polys[0])))
                return hlr_polys, info
            log("  ⚠ HLR 面积与投影面积差 %d%%，弃用 HLR，退回栅格法"
                % int(abs(1 - ratio) * 100))
        else:
            log("  HLR 没提出闭合环，退回栅格法")

    # ---- 栅格兜底路线（HLR 失败/被弃用，或 inset>0）----
    # P15：此时才 import matplotlib + 全分辨率栅格化——HLR 成功时这两样
    # （matplotlib 冷 import ~0.5s + 纯 Python 栅格化）纯属白做。
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    mask, W, H = _rasterize(tris, x1, y1, x2, y2, cell)
    proj_area = float(mask.sum()) * cell * cell

    # ★inset 必须能取 0★
    #   早期为了"让 SW 的成形到曲面盖得住轮廓"写死了 max(1, ...)，
    #   结果内缩永远 ≥1 个栅格。切除用内缩轮廓会在型腔四周留一圈薄墙
    #   （实测 2ROM：0.3mm 厚 × 周长 580mm × 高 44mm ≈ 7588 mm³，
    #   就是用户看到的"挖空没到位"）。
    k = int(round(inset / cell)) if inset > 0 else 0
    er = _erode(mask, k) if k > 0 else mask
    if not er.any():
        raise RuntimeError("内缩 %.2fmm 后投影为空，请调小 inset" % inset)

    xs = x1 + (np.arange(W) + 0.5) * cell
    ys = y1 + (np.arange(H) + 0.5) * cell
    # ★必须给掩膜留白边再提轮廓★
    #   掩膜是贴着包围盒生成的，前几格就落在数组边界上。
    #   matplotlib 的 contour 遇到"图形挨着数组边缘"会画出【不闭合】的碎环 ——
    #   实测 2ROM：投影面积 12000，轮廓却只有 5992、3 个点（正好一半）。
    #   以前 inset≥1 格时图形被腐蚀离开了边界，所以没暴露。
    pad = 3
    erp = np.pad(er, pad, constant_values=False)
    xsp = x1 + (np.arange(W + 2 * pad) + 0.5 - pad) * cell
    ysp = y1 + (np.arange(H + 2 * pad) + 0.5 - pad) * cell
    cs = plt.contour(xsp, ysp, erp.astype(float), levels=[0.5])
    loops = [l for l in (cs.allsegs[0] if cs.allsegs else []) if len(l) >= 4]
    loops.sort(key=lambda l: -geo.polyline_area([(p[0], p[1]) for p in l]))

    polys = []
    for l in loops:
        pts = [(float(p[0]), float(p[1])) for p in l]
        simp = _rdp(pts, rdp_tol)
        if len(simp) >= 2:
            dx = simp[-1][0] - simp[0][0]
            dy = simp[-1][1] - simp[0][1]
            if math.hypot(dx, dy) < 1e-6:
                simp = simp[:-1]
        if len(simp) >= 3:
            simp = _clean_polygon(simp)
        if len(simp) >= 3:
            polys.append(simp)
    if not polys:
        raise RuntimeError("没提出投影轮廓")

    # ★自检★：轮廓面积必须和掩膜面积对得上，否则一定是提环出了问题
    mask_area = float(er.sum()) * cell * cell
    out_area = geo.polyline_area(polys[0])
    if mask_area > 0 and out_area < 0.6 * mask_area:
        raise RuntimeError(
            "投影轮廓明显提错了：掩膜面积 %.0f mm^2，但最外环只有 %.0f mm^2（%d 点）。"
            "通常是轮廓贴到了数组边界，检查 padding。"
            % (mask_area, out_area, len(polys[0])))

    info = {
        "bbox": [x1, y1, z1, x2, y2, z2],
        "projection_area": proj_area,
        "outline_area": geo.polyline_area(polys[0]),
        "outer_points": len(polys[0]),
        "loops": len(polys),
        "cell": cell, "inset": inset,
    }
    log("  投影面积=%.0f mm^2  轮廓环数=%d  最外环 %d 点 面积=%.0f mm^2  内缩=%.2fmm" %
        (proj_area, len(polys), len(polys[0]), info["outline_area"], inset))

    if out_png:
        fig, ax = plt.subplots(figsize=(9, 6))
        for t in tris:
            ax.fill([t[0], t[2], t[4]], [t[1], t[3], t[5]],
                    facecolor="lightsteelblue", edgecolor="none", alpha=0.8)
        for i, poly in enumerate(polys):
            cx = [p[0] for p in poly] + [poly[0][0]]
            cy = [p[1] for p in poly] + [poly[0][1]]
            ax.plot(cx, cy, "r-" if i == 0 else "m-", lw=1.2)
        ax.set_aspect("equal")
        ax.set_title("projection outline (%d loops, inset %.2fmm)" % (len(polys), inset))
        ax.grid(lw=0.3)
        fig.tight_layout()
        fig.savefig(out_png, dpi=85)
        plt.close(fig)

    return polys, info


def write_poly(polys, path):
    """写点表给 SolidWorks 引擎读。

    格式（纯 ASCII，用 | 以外的分隔都可以，引擎用 Input #）：
        第1行 = 点数 N
        其后 N 行 "x y"
    ★只写最外环★（内环=孔，已经用补孔解决，不该再当轮廓）
    """
    poly = polys[0]
    with open(path, "w", encoding="ascii") as f:
        f.write("%d\n" % len(poly))
        for (x, y) in poly:
            f.write("%.4f %.4f\n" % (x, y))
    return path
