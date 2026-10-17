# -*- coding: utf-8 -*-
import brep_topology as bt
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_WIRE, TopAbs_EDGE
from OCP.TopoDS import TopoDS
from OCP.BRepTools import BRepTools
from OCP.TopTools import TopTools_IndexedMapOfShape

ex = bt.BrepTopologyExtractor('mold_out1131/Y1131B_upper_filled.stp')
ex.extract()
es = ex._edge_shapes
fs = ex._face_shapes

m = TopTools_IndexedMapOfShape()
for eid, sh in es.items():
    m.Add(sh)
print('edges=%d map=%d' % (len(es), m.Extent()))

face = fs[3]
outer = BRepTools.OuterWire_s(face)
we = TopExp_Explorer(face, TopAbs_WIRE)
while we.More():
    wire = we.Current()
    is_outer = (not outer.IsNull() and outer.IsSame(wire))
    ee = TopExp_Explorer(wire, TopAbs_EDGE)
    while ee.More():
        occ = TopoDS.Edge_s(ee.Current())
        # old: first IsSame match
        old_eid = None
        for eid, known in es.items():
            if known.IsSame(occ):
                old_eid = eid
                break
        new_idx = m.FindIndex(occ)
        new_eid = new_idx - 1 if new_idx > 0 else None
        if old_eid != new_eid:
            print('MISMATCH outer=%s old=%s new=%s' % (is_outer, old_eid, new_eid))
            for eid, known in es.items():
                if known.IsSame(occ) or (new_eid is not None and known.IsSame(es[new_eid])):
                    print('   es[%d]: IsSame(occ)=%s IsEqual(occ)=%s orient=%s' % (
                        eid, known.IsSame(occ), known.IsEqual(occ), known.Orientation()))
            print('   occ orient=%s' % occ.Orientation())
        ee.Next()
    we.Next()
