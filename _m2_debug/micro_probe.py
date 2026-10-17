# -*- coding: utf-8 -*-
"""微探针：在离缝点处，检查上模在 (x,y) 竖直方向到底有没有料。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import cadquery as cq
from OCP.gp import gp_Pnt
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from mold_fix import MoldFixer

CASES = [
    ('YA-1131',
     os.path.join(ROOT, '_m2_debug', 'out_utf8', 'YA-1131', 'punch.stp'),
     [(260.0, -910.8, -48982.0), (-260.0, -910.8, -48982.0),
      (185.7, -910.8, -48982.0), (260.0, -1023.3, -48982.0),
      (0.0, -1000.0, -48982.0)]),
    ('YA-1238',
     os.path.join(ROOT, '_m2_debug', 'out_utf8', 'YA-1238', 'punch.stp'),
     [(519.1, -1523.0, -5858.8), (-519.1, -1523.0, -5858.8),
      (370.8, -1523.0, -5858.8), (0.0, -1700.0, -5858.8)]),
]
E = 0.5


def load(path):
    r = cq.importers.importStep(path)
    vals = r.vals() if hasattr(r, 'vals') else [r]
    sh = [v.wrapped if hasattr(v, 'wrapped') else v for v in vals]
    return MoldFixer._make_compound(sh) if len(sh) > 1 else sh[0]


for name, path, pts in CASES:
    print(f"\n{'=' * 70}\n{name}  上模 z 范围 {MoldFixer._bbox_tuple(load(path))[2]:.2f}"
          f" .. {MoldFixer._bbox_tuple(load(path))[5]:.2f}")
    punch = load(path)
    for (x, y, z) in pts:
        p = gp_Pnt(x, y, z)
        d = BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(p).Vertex(), punch)
        d.Perform()
        supp = [d.PointOnShape2(i + 1) for i in range(d.NbSolution())]
        box = BRepPrimAPI_MakeBox(gp_Pnt(x - E, y - E, z - 200), 2 * E, 2 * E, 400).Shape()
        c = BRepAlgoAPI_Common(punch, box)
        c.Build()
        cz = MoldFixer._bbox_tuple(c.Shape()) if c.IsDone() else (0., 0., 0., 0., 0., 0.)
        has = (cz[5] - cz[2]) > 1e-6
        print(f"  点({x:8.1f},{y:8.1f},{z:9.1f})  最近距={d.Value():8.3f}  "
              f"竖直柱内有料={has}  z[{cz[2]:.2f},{cz[5]:.2f}]")
        if supp:
            s = supp[0]
            print(f"        最近点=({s.X():.1f},{s.Y():.1f},{s.Z():.1f})")
