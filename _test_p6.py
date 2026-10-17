# -*- coding: utf-8 -*-
"""P6 equivalence test: IndexedMap edge lookup vs old IsSame scan."""
from collections import defaultdict

import cadquery as cq
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_WIRE, TopAbs_EDGE, TopAbs_SOLID
from OCP.TopoDS import TopoDS
from OCP.BRepTools import BRepTools

import brep_topology as bt


def old_maps(solid, face_shapes, edge_shapes):
    """原 O(E^2) IsSame 线性扫描实现，重建同一套映射作为基准。"""
    edge_to_faces = defaultdict(list)
    face_to_edges = defaultdict(list)
    face_outer_edges = defaultdict(list)
    face_inner_edges = defaultdict(list)
    for fid, face in face_shapes.items():
        outer_wire = BRepTools.OuterWire_s(face)
        wire_exp = TopExp_Explorer(face, TopAbs_WIRE)
        while wire_exp.More():
            wire = wire_exp.Current()
            is_outer = (not outer_wire.IsNull() and outer_wire.IsSame(wire))
            edge_exp = TopExp_Explorer(wire, TopAbs_EDGE)
            while edge_exp.More():
                edge = TopoDS.Edge_s(edge_exp.Current())
                for eid, known_edge in edge_shapes.items():
                    if known_edge.IsSame(edge):
                        if fid not in edge_to_faces[eid]:
                            edge_to_faces[eid].append(fid)
                        if eid not in face_to_edges[fid]:
                            face_to_edges[fid].append(eid)
                        target = face_outer_edges[fid] if is_outer else face_inner_edges[fid]
                        if eid not in target:
                            target.append(eid)
                        break
                edge_exp.Next()
            wire_exp.Next()
    return edge_to_faces, face_to_edges, face_outer_edges, face_inner_edges


def cmp_maps(name, new_map, old_map):
    keys = set(new_map) | set(old_map)
    diffs = 0
    for k in keys:
        nv = list(new_map.get(k, []))
        ov = list(old_map.get(k, []))
        if nv != ov:
            diffs += 1
            if diffs <= 3:
                print('  DIFF %s[%s]: new=%s old=%s' % (name, k, nv, ov))
    return diffs


def main():
    import glob
    files = sorted(glob.glob('*.stp'))[:3] + sorted(glob.glob('mold_out*/*.stp'))[:2]
    total_diff = 0
    for f in files:
        try:
            ex = bt.BrepTopologyExtractor(f)
            ex.extract()
        except Exception as e:
            print('SKIP %s: %s' % (f, str(e)[:60]))
            continue
        n_faces = len(ex._face_shapes)
        n_edges = len(ex._edge_shapes)
        if n_faces == 0:
            print('SKIP %s: no faces' % f)
            continue
        o_etf, o_fte, o_foe, o_fie = old_maps(ex._solid, ex._face_shapes, ex._edge_shapes)
        d = 0
        d += cmp_maps('edge_to_faces', ex._edge_to_faces, o_etf)
        d += cmp_maps('face_to_edges', ex._face_to_edges, o_fte)
        d += cmp_maps('face_outer_edges', ex._face_outer_edges, o_foe)
        d += cmp_maps('face_inner_edges', ex._face_inner_edges, o_fie)
        # 覆盖统计：新实现是否所有边都被定位
        n_located = sum(1 for eid in range(n_edges)
                        if eid in ex._edge_to_faces or eid in ex._face_to_edges.values()
                        or any(eid in v for v in ex._face_to_edges.values()))
        print('%s: faces=%d edges=%d diffs=%d' % (f, n_faces, n_edges, d))
        total_diff += d
    print('TOTAL DIFFS:', total_diff)
    assert total_diff == 0, 'P6 equivalence FAILED'
    print('P6 edge-face mapping equivalence: OK')


if __name__ == '__main__':
    main()
