# -*- coding: utf-8 -*-
"""B12 针对性测试：gap_pos=None 时去重键行为（旧版合并 vs 新版保留）。"""
import importlib.util
import sys

sys.path.insert(0, "C:/Users/何雨卓/Desktop/SF/S2")

import corner_close as new_mod

spec = importlib.util.spec_from_file_location(
    "corner_close_old", "C:/Users/何雨卓/AppData/Local/Temp/corner_close_old.py")
old_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old_mod)


class FakeExt:
    _face_shapes = {}
    _edge_shapes = {}


def build():
    """4 个平面面 → 6 个面对，全部满足候选条件且 gap_pos=None。"""
    faces = []
    for i in range(4):
        faces.append({
            'face_idx': i, 'surface_type': 'Plane', 'area': 100.0,
            'surface_params': {'normal': (0, 0, 1), 'origin': (float(i), 0, 0)},
            'bounding_edges': [], 'adjacent_faces': [],
        })
    # 法向两两不正交都行：_cross(na,na)=0 会被跳过硬要不同法向
    faces[1]['surface_params']['normal'] = (1, 0, 0)
    faces[2]['surface_params']['normal'] = (0, 1, 0)
    faces[3]['surface_params']['normal'] = (0, 0, 1)
    topo = {
        'faces': faces,
        'thickness': 2.0,
        'thickness_pairs': [],
        'classification': {'planar_faces': [0, 1, 2, 3], 'fillet_faces': []},
    }
    det = {'free_edges': [
        {'edge_idx': i * 2, 'adjacent_faces': [i]} for i in range(4)
    ] + [
        {'edge_idx': i * 2 + 1, 'adjacent_faces': [i]} for i in range(4)
    ]}
    return FakeExt(), topo, det


def run(mod, label):
    ext, topo, det = build()
    r = mod.CornerCloseRepair()
    # 强制所有边对通过共面/距离检查，且 _polyline_closest 失败 → gap_pos=None
    r._edge_points = lambda ext, eid, n=12: []
    r._polyline_min_dist = lambda pa, pb, na=7, nb=7: 1.0
    r._polyline_closest = lambda pa, pb, na=9, nb=9: (None, None, 1.0)
    cands = r._detect_corners(ext, topo, det)
    keys = [(tuple(c['faces']), tuple(c['edge_pair'])) for c in cands]
    print(f"{label}: {len(cands)} 个候选")
    for k in sorted(keys):
        print(f"   {k}")
    return keys


def main():
    ko = run(old_mod, "旧版")
    kn = run(new_mod, "新版")
    print()
    print(f"旧版保留 {len(ko)} 个，新版保留 {len(kn)} 个")
    ok = len(kn) == 5 and len(ko) == 1
    print("B12: " + ("通过 ✓（不同候选不再被静默合并）" if ok else "失败 ✗"))


if __name__ == "__main__":
    main()
