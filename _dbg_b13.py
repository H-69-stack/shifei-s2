# -*- coding: utf-8 -*-
"""诊断：隔离 P1 预过滤 vs B13 各子改动，定位 slice_lengths 大偏差来源。"""
import importlib.util
import sys

sys.path.insert(0, "C:/Users/何雨卓/Desktop/SF/S2")

import slice_analyzer as new_mod
from step_reader import StepReader

spec = importlib.util.spec_from_file_location("slice_analyzer_old", "C:/Users/何雨卓/AppData/Local/Temp/slice_analyzer_old.py")
old_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old_mod)

FILE = "24TK_1324-1.stp"
DIR = (0, 1, 0)
SI = 9   # old 1514.55 vs new 755.24 的切片

raw = StepReader().read(FILE)


def collect_segments(slicer, t, direction):
    """复刻 slice() 主循环取第 si 片的 segments（新 P1 过滤版）。"""
    projs = [new_mod._vec_dot(v, slicer.direction) for v in slicer._vertices]
    t_min, t_max = min(projs), max(projs)
    t_range = t_max - t_min
    if t_range < 0.01:
        t_min -= 1.0; t_max += 1.0; t_range = t_max - t_min
    t_min -= 5.0; t_max += 5.0; t_range = t_max - t_min
    step = t_range / 49
    tt = t_min + SI * step
    plane_origin = new_mod._vec_add(slicer.origin, new_mod._vec_scale(slicer.direction, tt - new_mod._vec_dot(slicer.origin, slicer.direction)))
    segs_new = []
    for i0, i1, i2 in slicer._triangles:
        seg = slicer._intersect_triangle_plane(
            (slicer._vertices[i0], slicer._vertices[i1], slicer._vertices[i2]),
            plane_origin, slicer.direction)
        if seg is not None:
            segs_new.append(seg)
    return tt, segs_new


# 1) 用新 slicer 的数据，但【全量】收集 segments（无 P1 过滤）——验证 P1 等价性
sn = new_mod.MeshSlicer(direction=DIR)
sn.add_solids_from_raw(raw)
so = old_mod.MeshSlicer(direction=DIR)
so.add_solids_from_raw(raw)

tt, segs_full = collect_segments(sn, None, DIR)
print(f"切片 t = {tt:.6f}  全量线段数 = {len(segs_full)}")

# 旧版同位置 segments（从旧 slice() 内部逻辑复刻：全量）
def old_slice_segments(slicer, si):
    projs = [old_mod._vec_dot(v, slicer.direction) for v in slicer._vertices]
    t_min, t_max = min(projs), max(projs)
    t_range = t_max - t_min
    if t_range < 0.01:
        t_min -= 1.0; t_max += 1.0; t_range = t_max - t_min
    t_min -= 5.0; t_max += 5.0
    step = (t_max - t_min) / 49
    t = t_min + si * step
    plane_origin = old_mod._vec_add(slicer.origin, old_mod._vec_scale(slicer.direction, t - old_mod._vec_dot(slicer.origin, slicer.direction)))
    segs = []
    for i0, i1, i2 in slicer._triangles:
        seg = slicer._intersect_triangle_plane(
            (slicer._vertices[i0], slicer._vertices[i1], slicer._vertices[i2]),
            plane_origin, slicer.direction)
        if seg is not None:
            segs.append(seg)
    return t, segs

t_old, segs_old = old_slice_segments(so, SI)
print(f"旧版切片 t = {t_old:.6f}  线段数 = {len(segs_old)}")
print(f"t 一致: {abs(tt - t_old) < 1e-12}")

# 逐段比较（含顺序）
same = len(segs_full) == len(segs_old) and all(
    a == b for a, b in zip(segs_full, segs_old))
print(f"线段列表完全一致（含顺序）: {same}")
if not same and len(segs_full) == len(segs_old):
    for i, (a, b) in enumerate(zip(segs_full, segs_old)):
        if a != b:
            print(f"  首个差异段 #{i}: new={a} old={b}")
            break

# 2) 新旧 _chain_segments 对同一 segments 的结果
c_old = old_mod.MeshSlicer._chain_segments(segs_old, tolerance=0.05)
c_new = new_mod.MeshSlicer._chain_segments(segs_old, tolerance=0.05)
print(f"\n旧链式: {len(c_old)} 条轮廓, 长度 = {[round(old_mod.MeshSlicer._arc_length(c), 3) for c in c_old]}")
print(f"新链式: {len(c_new)} 条轮廓, 长度 = {[round(new_mod.MeshSlicer._arc_length(c), 3) for c in c_new]}")
