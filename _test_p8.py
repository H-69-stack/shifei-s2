# -*- coding: utf-8 -*-
"""P8/B12 等价性测试：新 corner_close._detect_corners vs 原版 (git HEAD)。

对比同一 (ext, topo, det_result) 下候选角落集合；
B12 为有意的行为修正（gap_pos=None 时不再合并不同候选），单独核对。
"""
import importlib.util
import sys
import time

sys.path.insert(0, "C:/Users/何雨卓/Desktop/SF/S2")

import corner_close as new_mod
from brep_topology import BrepTopologyExtractor
from bend_detector import BendDetector

spec = importlib.util.spec_from_file_location(
    "corner_close_old", "C:/Users/何雨卓/AppData/Local/Temp/corner_close_old.py")
old_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old_mod)

FILES = [
    "24TK_1324-1.stp",
    "24TK_1442-1.stp",
    "1clb1070-71a.stp",
    "YA-1131-505_MIRROR_xOy.stp",
]


def cand_key(c):
    return (
        tuple(c['faces']), c['angle_deg'], c['gap_mm'], tuple(c['edge_pair']),
        c['closable'], c['normals'], c['origins'],
        None if c['gap_pos'] is None else tuple(round(v, 3) for v in c['gap_pos']),
    )


def main():
    all_ok = True
    for f in FILES:
        print(f"\n===== {f} =====")
        try:
            ext = BrepTopologyExtractor(f)
            topo = ext.extract()
            det_result = BendDetector(topo).detect()
        except Exception as e:
            print(f"  加载失败: {e}")
            continue

        ro = old_mod.CornerCloseRepair()
        rn = new_mod.CornerCloseRepair()

        t0 = time.perf_counter()
        co = ro._detect_corners(ext, topo, det_result)
        t_old = time.perf_counter() - t0
        t0 = time.perf_counter()
        cn = rn._detect_corners(ext, topo, det_result)
        t_new = time.perf_counter() - t0

        ko = sorted(map(cand_key, co))
        kn = sorted(map(cand_key, cn))
        ok = ko == kn
        all_ok = all_ok and ok
        print(f"  [{'OK ' if ok else 'DIFF'}] _detect_corners  old={t_old*1000:8.1f}ms "
              f"new={t_new*1000:8.1f}ms ({t_old/max(t_new,1e-9):.1f}x)  候选 {len(ko)} vs {len(kn)}")
        if not ok:
            so, sn = set(ko), set(kn)
            for k in sorted(so - sn):
                print(f"    仅旧版有: {k}")
            for k in sorted(sn - so):
                print(f"    仅新版有: {k}")
        else:
            n_none = sum(1 for c in cn if c['gap_pos'] is None)
            print(f"    候选细节一致（其中 gap_pos=None 的候选 {n_none} 个）")

    print("\n" + "=" * 60)
    print("P8 等价性: " + ("候选集合全部一致 ✓" if all_ok else "存在差异 ✗"))
    print("（B12 为有意行为修正：gap_pos=None 时旧版把不同候选合并为一个，")
    print(" 新版按 (边对, 面对) 唯一键保留——若上面出现'仅新版有'且")
    print(" gap_pos=None，即 B12 生效的正常差异）")
    print("=" * 60)


if __name__ == "__main__":
    main()
