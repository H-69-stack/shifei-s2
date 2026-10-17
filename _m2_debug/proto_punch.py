# -*- coding: utf-8 -*-
"""原型：通用上模 = Cut(产品沿 +Z 扫掠体 , 产品) ∩ {z <= P}。

原理：V = { p : 存在 s∈产品 在 p 正下方且 p.z <= P }（产品向上扫掠）。
      punch = V − 产品 —— 即"从正上方压下时能到达的、产品以外的空间"。
      自动正确处理：平板 / 深槽（冲头伸入槽内）/ 凹坑（填平）/ 贯穿孔（不填）。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from OCP.gp import gp_Vec
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeBox
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Common
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID, TopAbs_SHELL

from mold_fix import MoldFixer

CASES = [
    ('2ROM31-6061', os.path.join(ROOT, 'web_box_generator', 'uploads',
                                 '543e0fdcfa2e_2ROM31-6061 ---(1).stp')),
    ('YA-1131', os.path.join(ROOT, 'YA-1131-505_MIRROR_xOy.stp')),
]


def n_of(s, k):
    n, e = 0, TopExp_Explorer(s, k)
    while e.More():
        n += 1
        e.Next()
    return n


for name, path in CASES:
    print(f"\n{'=' * 74}\n{name}")
    t0 = time.time()
    solids = MoldFixer._extract_solids(path)
    product = MoldFixer._make_compound([s for _, s in solids])
    pb = MoldFixer._bbox_tuple(product)
    Z_A, Z_B = pb[5], pb[2]
    dt = Z_A - Z_B
    P = Z_A + dt
    H = P - Z_B
    print(f"  产品 z[{Z_B:.2f},{Z_A:.2f}] dt={dt:.2f}  P={P:.2f}  扫掠高 H={H:.2f}")
    try:
        V = BRepPrimAPI_MakePrism(product, gp_Vec(0, 0, H)).Shape()
    except Exception as e:
        print(f"  !! MakePrism(实体) 失败: {e}")
        continue
    print(f"  扫掠体: solids={n_of(V, TopAbs_SOLID)} shells={n_of(V, TopAbs_SHELL)} "
          f"valid={BRepCheck_Analyzer(V).IsValid()} vol={MoldFixer._solid_volume(V):.0f}")
    punch = BRepAlgoAPI_Cut(V, product)
    punch.SetFuzzyValue(1e-4)
    punch.Build()
    if not punch.IsDone():
        print("  !! Cut 失败")
        continue
    ps = punch.Shape()
    cut_box = BRepPrimAPI_MakeBox(
        [pb[0] - 50, pb[1] - 50, P], (pb[3] - pb[0]) + 100, (pb[4] - pb[1]) + 100, H + 50).Shape()
    c = BRepAlgoAPI_Cut(ps, cut_box)
    c.SetFuzzyValue(1e-4)
    c.Build()
    ps = c.Shape() if c.IsDone() else ps
    bb = MoldFixer._bbox_tuple(ps)
    print(f"  上模: solids={n_of(ps, TopAbs_SOLID)} shells={n_of(ps, TopAbs_SHELL)} "
          f"valid={BRepCheck_Analyzer(ps).IsValid()} vol={MoldFixer._solid_volume(ps):.0f}")
    print(f"  上模 bbox x[{bb[0]:.2f},{bb[3]:.2f}] y[{bb[1]:.2f},{bb[4]:.2f}] z[{bb[2]:.2f},{bb[5]:.2f}]")
    print(f"  顶面=P? {abs(bb[5] - P) < 0.05}   用时 {time.time() - t0:.1f}s")
