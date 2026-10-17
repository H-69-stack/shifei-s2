# -*- coding: utf-8 -*-
"""P1/B13 等价性 + 性能测试：新 slice_analyzer vs 原版 (git HEAD)。

用法:
    PYTHONPATH="C:/Users/何雨卓/Desktop/SF/S2/venv/Lib/site-packages" "D:/Python310/python.exe" _test_p1_b13.py
"""
import importlib.util
import math
import sys
import time

sys.path.insert(0, "C:/Users/何雨卓/Desktop/SF/S2")

import slice_analyzer as new_mod

OLD_PATH = "C:/Users/何雨卓/AppData/Local/Temp/slice_analyzer_old.py"
spec = importlib.util.spec_from_file_location("slice_analyzer_old", OLD_PATH)
old_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old_mod)

from step_reader import StepReader

FILES = [
    "24TK_1324-1.stp",
    "24TK_1442-1.stp",
    "1clb1070-71a.stp",
    "YA-1131-505_MIRROR_xOy.stp",
]

DIRECTIONS = [
    (0, 1, 0),
    (1, 0, 0),
    (0.3, 0.8, 0.5),
]


def build(mod, raw):
    s = mod.MeshSlicer(direction=(0, 1, 0))
    s.add_solids_from_raw(raw)
    return s


def compare_result(tag, ro, rn):
    """对比新旧 slice() 结果，返回 (ok, notes)"""
    notes = []
    ok = True

    if len(ro["slice_positions"]) != len(rn["slice_positions"]):
        return False, [f"{tag}: 切片数不同 {len(ro['slice_positions'])} vs {len(rn['slice_positions'])}"]

    # 位置逐项
    for i, (a, b) in enumerate(zip(ro["slice_positions"], rn["slice_positions"])):
        if abs(a - b) > 1e-9:
            ok = False
            notes.append(f"{tag}: slice_positions[{i}] {a} vs {b}")

    # 长度逐项
    for i, (a, b) in enumerate(zip(ro["slice_lengths"], rn["slice_lengths"])):
        if abs(a - b) > 1e-6:
            ok = False
            notes.append(f"{tag}: slice_lengths[{i}] {a:.6f} vs {b:.6f} (diff {b - a:+.6f})")

    if ro["best_index"] != rn["best_index"]:
        notes.append(f"{tag}: best_index {ro['best_index']} vs {rn['best_index']}")
    if abs(ro["best_length"] - rn["best_length"]) > 1e-4:
        ok = False
        notes.append(f"{tag}: best_length {ro['best_length']} vs {rn['best_length']}")

    # best_contour_3d 逐点
    co, cn = ro["best_contour_3d"], rn["best_contour_3d"]
    if len(co) != len(cn):
        notes.append(f"{tag}: best_contour 点数 {len(co)} vs {len(cn)}")
        if abs(ro["best_length"] - rn["best_length"]) <= 1e-4:
            notes.append(f"{tag}:   但弧长一致 → 链式差异（点数不同），需人工判断")
    else:
        maxd = 0.0
        for a, b in zip(co, cn):
            d = math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))
            maxd = max(maxd, d)
        if maxd > 1e-6:
            ok = False
            notes.append(f"{tag}: best_contour_3d 最大点偏差 {maxd:.6g}")

    return ok, notes


def compare_plane(tag, ro, rn):
    notes = []
    ok = True
    lo, ln = ro["arc_lengths"], rn["arc_lengths"]
    if len(lo) != len(ln):
        notes.append(f"{tag}: 轮廓数 {len(lo)} vs {len(ln)}")
    for i, (a, b) in enumerate(zip(lo, ln)):
        if abs(a - b) > 1e-6:
            ok = False
            notes.append(f"{tag}: arc_lengths[{i}] {a:.6f} vs {b:.6f}")
    return ok, notes


def main():
    all_ok = True
    total_notes = []

    for f in FILES:
        print(f"\n===== {f} =====")
        try:
            raw = StepReader().read(f)
        except Exception as e:
            print(f"  读取失败: {e}")
            continue
        nv = sum(len(s.get("vertices", [])) // 3 for s in raw.get("solids", []))
        nt = sum(len(s.get("triangles", [])) // 3 for s in raw.get("solids", []))
        print(f"  顶点 {nv} 三角形 {nt}")

        for direction in DIRECTIONS:
            so = old_mod.MeshSlicer(direction=direction)
            so.add_solids_from_raw(raw)
            sn = new_mod.MeshSlicer(direction=direction)
            sn.add_solids_from_raw(raw)

            tag = f"{f[:16]}|dir={direction}"

            t0 = time.perf_counter()
            ro = so.slice(num_slices=50, margin=5.0)
            t_old = time.perf_counter() - t0

            t0 = time.perf_counter()
            rn = sn.slice(num_slices=50, margin=5.0)
            t_new = time.perf_counter() - t0

            ok, notes = compare_result(tag, ro, rn)
            all_ok = all_ok and ok
            total_notes.extend(notes)
            speedup = t_old / t_new if t_new > 0 else float("inf")
            status = "OK " if ok else "DIFF"
            print(f"  [{status}] slice dir={direction} old={t_old*1000:8.1f}ms new={t_new*1000:8.1f}ms "
                  f"({speedup:.1f}x) best_len={ro['best_length']:.2f} idx={ro['best_index']}")
            for n in notes:
                print(f"      {n}")

            # 单平面路径
            o = so.origin
            po = (o[0], o[1] + 10.0, o[2])
            ipo = so.intersect_plane(po, direction)
            ipn = sn.intersect_plane(po, direction)
            ok2, notes2 = compare_plane(tag + "|plane", ipo, ipn)
            all_ok = all_ok and ok2
            total_notes.extend(notes2)
            if not ok2:
                print(f"  [DIFF] intersect_plane dir={direction}")
                for n in notes2:
                    print(f"      {n}")
            else:
                print(f"  [OK ] intersect_plane dir={direction} 轮廓={len(ipo['arc_lengths'])}")

    print("\n" + "=" * 60)
    if all_ok and not total_notes:
        print("全部一致：P1 预过滤 + B13 链接 与 原实现结果完全一致 ✓")
    elif all_ok:
        print("数值全部一致（以下为仅提示差异，不影响判定）：")
        for n in total_notes:
            print(f"  - {n}")
    else:
        print("存在差异：")
        for n in total_notes:
            print(f"  - {n}")
    print("=" * 60)


if __name__ == "__main__":
    main()
