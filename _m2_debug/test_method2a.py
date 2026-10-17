# -*- coding: utf-8 -*-
"""验证法二A（形变面路径）修复：上模顶面必须平齐 P、下模不得超出基座顶面。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from mold_fix import MoldFixer
from mold_method2_generator import MoldMethod2Generator, _bbox, _bbox_accurate

U = os.path.join(ROOT, 'web_box_generator', 'uploads')
CASES = {
    '2ROM31-6061': (os.path.join(U, '925e79889839_2ROM31-6061 ---(1)_PUNCH_CONTACT_FACES.stp'),
                    os.path.join(U, 'b852a7a025e8_2ROM31-6061 ---(1)_CAVITY_CONTACT_FACES.stp')),
    'YA-1131': (os.path.join(U, '204cfaba3f3f_YA-1131-505_MIRROR_xOy_PUNCH_CONTACT_FACES.stp'),
                os.path.join(U, 'b721182a27e0_YA-1131-505_MIRROR_xOy_CAVITY_CONTACT_FACES.stp')),
    'YA-1238': (os.path.join(U, '656d07b0bbcb_YA-1238-01_MIRROR_xOy_PUNCH_CONTACT_FACES.stp'),
                os.path.join(U, 'd007a86fd7cc_YA-1238-01_MIRROR_xOy_CAVITY_CONTACT_FACES.stp')),
}

g = MoldMethod2Generator()
for name, (up, lo) in CASES.items():
    print(f"\n{'=' * 72}\n{name}")
    for tag, path, fn in (('上模', up, g.generate_punch), ('下模', lo, g.generate_cavity)):
        if not os.path.exists(path):
            print(f"  {tag}: 文件不存在，跳过")
            continue
        res = fn(path)
        if not res.get('success'):
            print(f"  {tag}: 失败 -> {res.get('error')}")
            continue
        d = res['details']
        bb = _bbox_accurate(res['_shape'])
        exp = d['P'] if tag == '上模' else d['z_top']
        over = bb[5] - exp
        ok = abs(over) < 0.05
        print(f"  {tag}: solids={MoldFixer._solid_count(res['_shape'])} "
              f"shells={MoldFixer._shell_count(res['_shape'])} "
              f"valid={d.get('valid')} 期望顶面={exp:.3f} 实际={bb[5]:.3f} "
              f"超出={over:+.3f}  {'OK' if ok else '<== 仍不对'}")
