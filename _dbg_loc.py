# -*- coding: utf-8 -*-
import brep_topology as bt
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_WIRE, TopAbs_EDGE
from OCP.TopoDS import TopoDS
from OCP.BRepTools import BRepTools

ex = bt.BrepTopologyExtractor('mold_out1131/Y1131B_upper_filled.stp')
ex.extract()
es = ex._edge_shapes
fs = ex._face_shapes

# find the occ edge that old-matched 111
face = fs[3]
we = TopExp_Explorer(face, TopAbs_WIRE)
occ = None
while we.More():
    ee = TopExp_Explorer(we.Current(), TopAbs_EDGE)
    while ee.More():
        c = TopoDS.Edge_s(ee.Current())
        for eid, known in es.items():
            if eid == 111 and known.IsSame(c):
                occ = c
        ee.Next()
    we.Next()

if occ is not None:
    for eid in (107, 111, 258, 250):
        k = es[eid]
        print('es[%d]: TShape same as occ=%s | Location.IsIdentity=%s occ.Location.IsIdentity=%s | loc_same=%s' % (
            eid,
            k.TShape() is occ.TShape() or k.TShape() == occ.TShape(),
            k.Location().IsIdentity(),
            occ.Location().IsIdentity(),
            k.Location() == occ.Location(),
        ))
else:
    print('occ not found')
