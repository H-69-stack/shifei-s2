# -*- coding: utf-8 -*-
import os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID, TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
import fit_checker as FC
import assembly_builder as AB

CAV = r"C:/Users/何雨卓/AppData/Local/Temp/task6_smoke_out/YA-1131-505_MIRROR_xOy_CAVITY.stp"
print("loading...", flush=True)
solids = AB._extract_solids(CAV)
shape = AB._make_compound([s for _, s in solids])
idx = FC._SolidDistIndex(shape)
print("cavity solids:", len(solids), flush=True)

def old_dist(pnt, solid):
    best = None
    exp = TopExp_Explorer(solid, TopAbs_SOLID)
    while exp.More():
        v = TopoDS.Vertex_s(BRepBuilderAPI_MakeVertex(pnt).Vertex())
        d = BRepExtrema_DistShapeShape(exp.Current(), v)
        d.Perform()
        if d.IsDone():
            val = d.Value()
            best = val if best is None else min(best, val)
        exp.Next()
    return best

pts = []
exp = TopExp_Explorer(shape, TopAbs_FACE)
while exp.More():
    for p in FC._sample_face_points(exp.Current(), cap=6):
        pts.append(p)
    exp.Next()
print("face pts:", len(pts), flush=True)

bad = []
for i, p in enumerate(pts):
    d_old = old_dist(p, shape)
    d_new = FC._dist_point_solid(p, shape, index=idx)
    if (d_old is None) != (d_new is None):
        bad.append((i, d_old, d_new, "None 不匹配"))
    elif d_old is not None and d_new is not None and abs(d_old - d_new) > 1e-6:
        bad.append((i, d_old, d_new, abs(d_old-d_new)))
    if (i+1) % 200 == 0:
        print("  ...", i+1, "checked", flush=True)

print("总采样:", len(pts), " 大差异(>1e-6):", len(bad), flush=True)
for i, o, n, delta in bad[:20]:
    print(f"  #{i}: old={o!r} new={n!r} delta={delta}", flush=True)
print("最大 delta(如有):", max((x[3] for x in bad if isinstance(x[3], float)), default=0.0), flush=True)
