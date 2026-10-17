# -*- coding: utf-8 -*-
"""P10/B14 等价性测试：新 bend_detector vs 原版 (git HEAD)。

对比 detect() 全量结果：折弯集合（多重集）、自由边集合。
"""
import importlib.util
import sys

sys.path.insert(0, "C:/Users/何雨卓/Desktop/SF/S2")

import bend_detector as new_mod
from brep_topology import BrepTopologyExtractor

spec = importlib.util.spec_from_file_location(
    "bend_detector_old", "C:/Users/何雨卓/AppData/Local/Temp/bend_detector_old.py")
old_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old_mod)

FILES = [
    "24TK_1324-1.stp",
    "24TK_1442-1.stp",
    "1clb1070-71a.stp",
    "YA-1131-505_MIRROR_xOy.stp",
]


def bend_key(b):
    return (
        b['plane_a'], b['plane_b'], b['fillet_face'],
        b['bend_angle_deg'], b['bend_radius'],
        tuple(sorted(b.get('shared_edges_between_planes', []))),
        b['detection_method'],
    )


def free_key(f):
    return (f['edge_idx'], f['reason'], tuple(f.get('adjacent_faces', [])))


def main():
    all_ok = True
    for f in FILES:
        print(f"\n===== {f} =====")
        try:
            topo = BrepTopologyExtractor(f).extract()
        except Exception as e:
            print(f"  抽取失败: {e}")
            continue
        nf = len(topo['faces'])
        ne = len(topo['edges'])
        print(f"  面 {nf} 边 {ne} 厚度 {topo.get('thickness')}")

        import time
        t0 = time.perf_counter()
        ro = old_mod.BendDetector(topo).detect()
        t_old = time.perf_counter() - t0
        t0 = time.perf_counter()
        rn = new_mod.BendDetector(topo).detect()
        t_new = time.perf_counter() - t0

        bo = sorted(map(bend_key, ro['bends']))
        bn = sorted(map(bend_key, rn['bends']))
        ok = bo == bn
        all_ok = all_ok and ok
        print(f"  [{'OK ' if ok else 'DIFF'}] detect  old={t_old*1000:7.1f}ms new={t_new*1000:7.1f}ms "
              f"({t_old/max(t_new,1e-9):.1f}x)  bends {len(bo)} vs {len(bn)}")

        if not ok:
            so, sn = set(bo), set(bn)
            for k in sorted(so - sn):
                print(f"    仅旧版有: {k}")
            for k in sorted(sn - so):
                print(f"    仅新版有: {k}")

        fo = sorted(map(free_key, ro['free_edges']))
        fn = sorted(map(free_key, rn['free_edges']))
        ok2 = fo == fn
        all_ok = all_ok and ok2
        print(f"  [{'OK ' if ok2 else 'DIFF'}] free_edges {len(fo)} vs {len(fn)}")
        if not ok2:
            so, sn = set(fo), set(fn)
            for k in sorted(so - sn)[:10]:
                print(f"    仅旧版有: {k}")
            for k in sorted(sn - so)[:10]:
                print(f"    仅新版有: {k}")

        # 统计 direct_edge 方法折弯数（P10 主要改动路径）
        nd_o = sum(1 for b in ro['bends'] if b['detection_method'] == 'direct_edge')
        nd_n = sum(1 for b in rn['bends'] if b['detection_method'] == 'direct_edge')
        print(f"  direct_edge 折弯: 旧 {nd_o} vs 新 {nd_n}")

    print("\n" + "=" * 60)
    print("P10/B14 等价性: " + ("全部一致 ✓" if all_ok else "存在差异 ✗"))
    print("=" * 60)


if __name__ == "__main__":
    main()
