# -*- coding: utf-8 -*-
import brep_topology as bt
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS
from OCP.TopAbs import TopAbs_FORWARD

ex = bt.BrepTopologyExtractor('mold_out1131/Y1131B_upper_filled.stp')
ex.extract()
es = ex._edge_shapes
a, b = es[107], es[111]
print('IsSame(107,111):', a.IsSame(b), ' IsEqual:', a.IsEqual(b))
print('orientations:', a.Orientation(), b.Orientation())


def fwd(sh):
    s = TopoDS.Edge_s(sh)
    s.Orientation(TopAbs_FORWARD)
    return s


m1 = TopTools_IndexedMapOfShape()
m1.Add(a)
m1.Add(b)
print('raw map extent after adding both:', m1.Extent())

m2 = TopTools_IndexedMapOfShape()
m2.Add(fwd(a))
m2.Add(fwd(b))
print('canon map extent:', m2.Extent(),
      'FindIndex(a_fwd)=', m2.FindIndex(fwd(a)),
      'FindIndex(b_fwd)=', m2.FindIndex(fwd(b)))
print('FindIndex(a raw) in canon map:', m2.FindIndex(a))
