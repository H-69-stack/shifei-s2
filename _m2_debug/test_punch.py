# -*- coding: utf-8 -*-
"""用真实 build_punch_by_extrusion 测单件连通性（快速验证探针修复）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID, TopAbs_SHELL
from OCP.BRepCheck import BRepCheck_Analyzer

from mold_fix import MoldFixer
from mold_method2_generator import MoldMethod2Generator

name = sys.argv[1] if len(sys.argv) > 1 else '1131'
paths = {
    '2ROM': os.path.join(ROOT, 'web_box_generator', 'uploads',
                         '543e0fdcfa2e_2ROM31-6061 ---(1).stp'),
    '1131': os.path.join(ROOT, 'YA-1131-505_MIRROR_xOy.stp'),
    '1238': os.path.join(ROOT, 'YA-1238-01_MIRROR_xOy.stp'),
}
p = paths[name]
solids = MoldFixer._extract_solids(p)
product = MoldFixer._make_compound([s for _, s in solids])
bb = MoldFixer._bbox_tuple(product)
Z_A, Z_B = bb[5], bb[2]
P = Z_A + (Z_A - Z_B)

g = MoldMethod2Generator()
shape, n_used, n_skip = g.build_punch_by_extrusion(product, P)


def n_of(s, k):
    n, e = 0, TopExp_Explorer(s, k)
    while e.More():
        n += 1
        e.Next()
    return n


print(f"\n[{name}] 棱柱={n_used} 跳过竖直面={n_skip}")
if shape is None:
    print("  → None")
else:
    print(f"  solids={n_of(shape, TopAbs_SOLID)} shells={n_of(shape, TopAbs_SHELL)} "
          f"vol={MoldFixer._solid_volume(shape):.0f} "
          f"valid={BRepCheck_Analyzer(shape).IsValid()} bbox={MoldFixer._bbox_tuple(shape)}")
