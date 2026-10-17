# -*- coding: utf-8 -*-
"""验证 1clb1070 最大偏差案例：新旧轮廓分组差异的性质。"""
import importlib.util
import sys

sys.path.insert(0, "C:/Users/何雨卓/Desktop/SF/S2")
import slice_analyzer as M

spec = importlib.util.spec_from_file_location("slice_analyzer_old", "C:/Users/何雨卓/AppData/Local/Temp/slice_analyzer_old.py")
old_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old_mod)
from step_reader import StepReader

FILE = "1clb1070-71a.stp"
DIR = (0, 1, 0)
SI = 9

raw = StepReader().read(FILE)
sn = M.MeshSlicer(direction=DIR); sn.add_solids_from_raw(raw)
so = old_mod.MeshSlicer(direction=DIR); so.add_solids_from_raw(raw)

def segs_at(slicer, mod, si):
    projs = [mod._vec_dot(v, slicer.direction) for v in slicer._vertices]
    t_min, t_max = min(projs), max(projs)
    tr = t_max - t_min
    if tr < 0.01:
        t_min -= 1.0; t_max += 1.0; tr = t_max - t_min
    t_min -= 5.0; t_max += 5.0
    step = (t_max - t_min) / 49
    t = t_min + si * step
    po = mod._vec_add(slicer.origin, mod._vec_scale(slicer.direction, t - mod._vec_dot(slicer.origin, slicer.direction)))
    out = []
    for i0, i1, i2 in slicer._triangles:
        s = slicer._intersect_triangle_plane((slicer._vertices[i0], slicer._vertices[i1], slicer._vertices[i2]), po, slicer.direction)
        if s is not None:
            out.append(s)
    return out

segs = segs_at(sn, M, SI)
print(f"线段数 {len(segs)}")
co = old_mod.MeshSlicer._chain_segments(segs, 0.05)
cn = M.MeshSlicer._chain_segments(segs, 0.05)
used_o = sum(len(c) - 1 for c in co)
used_n = sum(len(c) - 1 for c in cn)
print(f"旧: {len(co)} 条, 用段 {used_o}/{len(segs)}, 长度 {[round(old_mod.MeshSlicer._arc_length(c),3) for c in co]}")
print(f"新: {len(cn)} 条, 用段 {used_n}/{len(segs)}, 长度 {[round(M.MeshSlicer._arc_length(c),3) for c in cn]}")
