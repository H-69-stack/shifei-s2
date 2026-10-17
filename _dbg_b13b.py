# -*- coding: utf-8 -*-
"""诊断2：在带桩版 _chain_segments 上复现 446 段的链接过程，看新算法在哪断链。"""
import math
import sys

sys.path.insert(0, "C:/Users/何雨卓/Desktop/SF/S2")
import slice_analyzer as M
from step_reader import StepReader

FILE = "24TK_1324-1.stp"
DIR = (0, 1, 0)
SI = 9

raw = StepReader().read(FILE)
sn = M.MeshSlicer(direction=DIR)
sn.add_solids_from_raw(raw)

projs = [M._vec_dot(v, sn.direction) for v in sn._vertices]
t_min, t_max = min(projs), max(projs)
t_range = t_max - t_min
if t_range < 0.01:
    t_min -= 1.0; t_max += 1.0; t_range = t_max - t_min
t_min -= 5.0; t_max += 5.0
step = (t_max - t_min) / 49
t = t_min + SI * step
plane_origin = M._vec_add(sn.origin, M._vec_scale(sn.direction, t - M._vec_dot(sn.origin, sn.direction)))
segments = []
for i0, i1, i2 in sn._triangles:
    seg = sn._intersect_triangle_plane(
        (sn._vertices[i0], sn._vertices[i1], sn._vertices[i2]), plane_origin, sn.direction)
    if seg is not None:
        segments.append(seg)
print(f"segments = {len(segments)}")

# ── 带桩链式（与新实现同逻辑 + 事件日志）──
def chain_traced(segments, tolerance=0.05):
    inv_tol = 1.0 / tolerance

    def _key(pt):
        return (round(pt[0] * inv_tol), round(pt[1] * inv_tol), round(pt[2] * inv_tol))

    endpoint_map = {}
    for idx, (s, e) in enumerate(segments):
        for pt, is_start in ((s, True), (e, False)):
            endpoint_map.setdefault(_key(pt), []).append((idx, is_start))

    def _find_candidates(pt):
        kx, ky, kz = _key(pt)
        for seg_idx, is_start in endpoint_map.get((kx, ky, kz), ()):
            if seg_idx not in used:
                yield seg_idx, is_start
        for dx in (0, -1, 1):
            for dy in (0, -1, 1):
                for dz in (0, -1, 1):
                    if dx == 0 and dy == 0 and dz == 0:
                        continue
                    for seg_idx, is_start in endpoint_map.get((kx + dx, ky + dy, kz + dz), ()):
                        if seg_idx in used:
                            continue
                        pt2 = segments[seg_idx][0] if is_start else segments[seg_idx][1]
                        if M._vec_dist(pt2, pt) <= tolerance:
                            yield seg_idx, is_start

    used = set()
    contours = []
    events = []
    for start_idx, (s, e) in enumerate(segments):
        if start_idx in used:
            continue
        contour = [s, e]
        used.add(start_idx)
        current_pt = e
        in_dir = M._vec_sub(e, s)
        in_len = M._vec_length(in_dir)
        in_dir = M._vec_scale(in_dir, 1.0 / in_len) if in_len > 1e-12 else None
        while True:
            best = None
            best_score = -2.0
            n_cand = 0
            for seg_idx, is_start in _find_candidates(current_pt):
                n_cand += 1
                seg = segments[seg_idx]
                if in_dir is None:
                    score = 1.0
                else:
                    d = M._vec_sub(seg[1], seg[0]) if is_start else M._vec_sub(seg[0], seg[1])
                    dl = M._vec_length(d)
                    score = -1.0 if dl <= 1e-12 else abs(M._vec_dot(M._vec_scale(d, 1.0 / dl), in_dir))
                if score > best_score:
                    best_score = score
                    best = (seg_idx, is_start)
                if best_score >= 1.0 - 1e-12 and in_dir is None:
                    break
            if best is None:
                events.append((len(contours), len(contour), "no_candidate", n_cand, current_pt))
                break
            if n_cand > 1:
                events.append((len(contours), len(contour), f"junction({n_cand} cands, score={best_score:.3f})", n_cand, current_pt))
            seg_idx, is_start = best
            used.add(seg_idx)
            seg = segments[seg_idx]
            next_pt = seg[1] if is_start else seg[0]
            contour.append(next_pt)
            current_pt = next_pt
            nd = M._vec_sub(next_pt, contour[-2])
            nl = M._vec_length(nd)
            in_dir = M._vec_scale(nd, 1.0 / nl) if nl > 1e-12 else None
            if M._vec_dist(current_pt, contour[0]) <= tolerance:
                events.append((len(contours), len(contour), "closed", n_cand, current_pt))
                break
            if len(contour) > len(segments) * 2:
                events.append((len(contours), len(contour), "safety", n_cand, current_pt))
                break
        contours.append(contour)
    return contours, events

c_new_real = M.MeshSlicer._chain_segments(segments)
print(f"\n真实新逻辑轮廓: {len(c_new_real)} 条")
for i, c in enumerate(c_new_real):
    print(f"  #{i}: {len(c)} 点, 弧长 {M.MeshSlicer._arc_length(c):.3f}")

print("\n关键事件:")
for ci, npts, kind, nc, pt in events:
    if kind != "closed" or nc > 1:
        print(f"  轮廓#{ci} @{npts}点: {kind}  cand={nc}  pt=({pt[0]:.3f},{pt[1]:.3f},{pt[2]:.3f})")

# 对照：旧逻辑
def chain_old(segments, tolerance=0.05):
    inv_tol = 1.0 / tolerance
    def _key(pt):
        return (round(pt[0] * inv_tol), round(pt[1] * inv_tol), round(pt[2] * inv_tol))
    endpoint_map = {}
    for idx, (s, e) in enumerate(segments):
        for pt, is_start in ((s, True), (e, False)):
            endpoint_map.setdefault(_key(pt), []).append((idx, is_start))
    used = set()
    contours = []
    for start_idx, (s, e) in enumerate(segments):
        if start_idx in used:
            continue
        contour = [s, e]
        used.add(start_idx)
        current_pt = e
        while True:
            k = _key(current_pt)
            candidates = endpoint_map.get(k, [])
            found = False
            for seg_idx, is_start in candidates:
                if seg_idx in used:
                    continue
                used.add(seg_idx)
                seg = segments[seg_idx]
                next_pt = seg[1] if is_start else seg[0]
                contour.append(next_pt)
                current_pt = next_pt
                found = True
                break
            if not found:
                break
            if _key(current_pt) == _key(contour[0]):
                break
            if len(contour) > len(segments) * 2:
                break
        contours.append(contour)
    return contours

c_old = chain_old(segments)
print(f"\n旧逻辑轮廓: {len(c_old)} 条")
for i, c in enumerate(c_old):
    print(f"  #{i}: {len(c)} 点, 弧长 {M.MeshSlicer._arc_length(c):.3f}")
