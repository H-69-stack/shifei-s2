# -*- coding: utf-8 -*-
"""P6 perf: old IsSame scan vs new IndexedMap on the biggest model."""
import time
from collections import defaultdict

import brep_topology as bt
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_WIRE, TopAbs_EDGE
from OCP.TopoDS import TopoDS
from OCP.BRepTools import BRepTools

f = '1clb1070-71a.stp'
ex = bt.BrepTopologyExtractor(f)
ex.extract()
fs, es = ex._face_shapes, ex._edge_shapes

t0 = time.time()
edge_to_faces = defaultdict(list)
for fid, face in fs.items():
    outer_wire = BRepTools.OuterWire_s(face)
    we = TopExp_Explorer(face, TopAbs_WIRE)
    while we.More():
        wire = we.Current()
        ee = TopExp_Explorer(wire, TopAbs_EDGE)
        while ee.More():
            edge = TopoDS.Edge_s(ee.Current())
            for eid, known in es.items():
                if known.IsSame(edge):
                    if fid not in edge_to_faces[eid]:
                        edge_to_faces[eid].append(fid)
                    break
            ee.Next()
        we.Next()
t_old = time.time() - t0

t0 = time.time()
ex2 = bt.BrepTopologyExtractor(f)
ex2.extract()
t_new = time.time() - t0

print('old O(E^2) edge-face loop: %.2fs' % t_old)
print('new full extract() (read+all): %.2fs' % t_new)
print('edges=%d faces=%d' % (len(es), len(fs)))
