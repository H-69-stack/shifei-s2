# -*- coding: utf-8 -*-
"""P10 合成拓扑等价性测试：直接边折弯检测路径（真实文件不覆盖）。

构造含各类过滤分支的合成 topology，对比新旧 BendDetector.detect()：
  - 正常 direct_edge 折弯（共面同侧、非厚度对、双方参与圆角网络）
  - 规则1/2/3/4 拒绝分支
  - 圆角桥接索引拒绝分支（P10 新增 O(1) 查表）
  - 原点缺失分支（B14：None vs 旧版 (0,0,0)，预期差异）
"""
import importlib.util
import sys

sys.path.insert(0, "C:/Users/何雨卓/Desktop/SF/S2")

import bend_detector as new_mod

spec = importlib.util.spec_from_file_location(
    "bend_detector_old", "C:/Users/何雨卓/AppData/Local/Temp/bend_detector_old.py")
old_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old_mod)


def plane(fi, normal, origin, edges, adj):
    return {
        'face_idx': fi, 'surface_type': 'Plane', 'area': 100.0,
        'surface_params': {'normal': normal, 'origin': origin},
        'bounding_edges': edges, 'adjacent_faces': adj,
    }


def cyl(fi, adj_planes, axis_dir=None, adj=()):
    # 与 brep_topology 产出的 fillet_faces 条目结构一致：
    # face_idx/surface_type/bend_radius/adjacent_planes/axis_origin/axis_direction 均在顶层
    return {
        'face_idx': fi, 'surface_type': 'Cylinder',
        'bend_radius': 1.0,
        'adjacent_planes': list(adj_planes),
        'axis_origin': (0.0, 0.0, 0.0),
        'axis_direction': axis_dir,
        'area': 10.0,
        'surface_params': {'axis_origin': (0.0, 0.0, 0.0),
                           'axis_direction': axis_dir, 'radius': 1.0},
        'bounding_edges': [], 'adjacent_faces': list(adj),
    }


def edge(ei, length, direction, faces, p1=(0, 0, 0), p2=(0, 0, 0)):
    return {
        'edge_idx': ei, 'curve_type': 'Line',
        'curve_params': {'length': length, 'direction': direction},
        'start_vertex': p1, 'end_vertex': p2,
        'adjacent_faces': list(faces),
    }


def build_topo():
    # 注意：与 brep_topology 真实产出一致，face_idx / edge_idx 均等于
    # 各自列表的位置（BendDetector 内部 self.faces[fi] / self.edges[ei]
    # 均按位置索引）
    faces = [
        plane(0, (0, 0, 1), (0, 0, 0), [0, 1], [2]),          # 正常折弯面 A
        plane(1, (1, 0, 0), (10, 0, 0), [0, 2], [3]),         # 正常折弯面 B
        cyl(2, [0, 17], adj=(0, 17)),                          # 圆角网络：只连 0
        cyl(3, [1, 2], adj=(1, 2)),                            # 圆角网络：只连 1
        plane(4, (0, 1, 0), (0, 0, 0), [3], [5]),             # 规则3 拒绝：不在圆角网络
        plane(5, (0, 0, 1), (0, 0, 0), [3], [4]),
        plane(6, (0, 1, 0), (0, 0, 0), [4], [7]),             # 规则2 拒绝：厚度对
        plane(7, (0, -1, 0), (0, 2, 0), [4], [6]),
        plane(8, (0, 1, 0), (0, 0, 0), [5], [9]),             # 规则4 拒绝：边太短
        plane(9, (0, 0, 1), (0, 0, 0), [5], [8]),
        plane(10, (0, 0, 1), (0, 0, 0), [6], [12]),           # 桥接拒绝：圆角面桥接但方法1未覆盖
        plane(11, (1, 0, 0), (0, 0, 0), [6], [12]),
        cyl(12, [10, 11], axis_dir=(0, 0, 1), adj=(10, 11)),   # 边缘面过滤 → 方法1 不产出
        plane(13, (0, 1, 0), (0, 0, 0), [7], [15]),           # 正常折弯，但共享边无顶点
        plane(14, (0, 0, 1), (0, 0, 0), [7], [16]),
        cyl(15, [13, 17], adj=(13, 17)),
        cyl(16, [14, 15], adj=(14, 15)),
        cyl(17, [], adj=()),                                    # 非平面占位
    ]
    edges = [
        edge(0, 20.0, (0, 1, 0), [0, 1], (5, 0, 0), (5, 20, 0)),
        edge(1, 20.0, (0, 1, 0), [0, 2], (0, 0, 0), (0, 20, 0)),
        edge(2, 20.0, (0, 1, 0), [1, 3], (10, 0, 0), (10, 20, 0)),
        edge(3, 20.0, (0, 1, 0), [4, 5]),
        edge(4, 20.0, (0, 1, 0), [6, 7]),
        edge(5, 3.0, (0, 1, 0), [8, 9]),
        edge(6, 20.0, (0, 1, 0), [10, 11]),
        edge(7, 20.0, (1, 0, 0), [13, 14], None, None),      # 无顶点 → 原点 None
    ]
    fillet_faces = [f for f in faces if f['surface_type'] == 'Cylinder' and f.get('adjacent_planes')]
    return {
        'faces': faces,
        'edges': edges,
        'vertices': [],
        'thickness': 2.0,
        'thickness_pairs': [(6, 7)],
        'base_face_idx': None,
        'classification': {
            'planar_faces': [0, 1, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14],
            'fillet_faces': fillet_faces,
        },
    }


def bend_key(b):
    return (b['plane_a'], b['plane_b'], b['fillet_face'],
            b['bend_angle_deg'], b['bend_radius'],
            tuple(sorted(b.get('shared_edges_between_planes', []))),
            b['detection_method'])


def main():
    topo = build_topo()
    ro = old_mod.BendDetector(topo).detect()
    rn = new_mod.BendDetector(topo).detect()

    bo = sorted(map(bend_key, ro['bends']))
    bn = sorted(map(bend_key, rn['bends']))
    print("旧版 direct/全部 折弯:")
    for k in bo:
        print(f"  {k}")
    print("新版 折弯:")
    for k in bn:
        print(f"  {k}")

    ok = bo == bn
    print(f"\n折弯集合一致: {ok}")

    # 原点对比（B14 预期差异：新版 None，旧版 (0,0,0)）
    for b in rn['bends']:
        print(f"  新版折弯 ({b['plane_a']},{b['plane_b']}) origin = {b['bend_axis_origin']}")
    for b in ro['bends']:
        print(f"  旧版折弯 ({b['plane_a']},{b['plane_b']}) origin = {b['bend_axis_origin']}")

    fo = sorted((f['edge_idx'], f['reason']) for f in ro['free_edges'])
    fn = sorted((f['edge_idx'], f['reason']) for f in rn['free_edges'])
    print(f"\n自由边一致: {fo == fn}  ({len(fo)} vs {len(fn)})")
    if fo != fn:
        print(f"  旧: {fo}")
        print(f"  新: {fn}")

    nd_o = sum(1 for b in ro['bends'] if b['detection_method'] == 'direct_edge')
    nd_n = sum(1 for b in rn['bends'] if b['detection_method'] == 'direct_edge')
    print(f"direct_edge 折弯数: 旧 {nd_o} vs 新 {nd_n}")

    print("\n" + ("P10 合成等价性: 通过 ✓" if ok and fo == fn and nd_n == 2 else "P10 合成等价性: 失败 ✗"))


if __name__ == "__main__":
    main()
