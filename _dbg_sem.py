# -*- coding: utf-8 -*-
import brep_topology as bt
from OCP.TopTools import TopTools_IndexedMapOfShape

ex = bt.BrepTopologyExtractor('mold_out1131/Y1131B_upper_filled.stp')
ex.extract()
es = ex._edge_shapes

a = es[111]   # FORWARD, IsSame with occ
b = es[258]   # REVERSED, same TShape+Location as a (IsSame True)
print('a.IsSame(b):', a.IsSame(b), 'a.IsEqual(b):', a.IsEqual(b))

m = TopTools_IndexedMapOfShape()
r1 = m.Add(a)
r2 = m.Add(b)
print('Add(a)=%d Add(b)=%d extent=%d  -> orientation %s' % (
    r1, r2, m.Extent(),
    'INSENSITIVE (IsSame)' if m.Extent() == 1 else 'SENSITIVE (IsEqual)'))

# does FindIndex find a by b and vice versa?
print('FindIndex(a)=%d FindIndex(b)=%d' % (m.FindIndex(a), m.FindIndex(b)))
