# -*- coding: utf-8 -*-
"""验证：即使 stdout 是 GBK（模拟 CLI/库调用），法二B 上模也不再退化为矩形块。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 强制 stdout 为 GBK（不 reconfig 成 utf-8），复现真实命令行环境
try:
    sys.stdout.reconfigure(encoding='gbk', errors='strict')
except Exception as e:
    print('cannot set gbk:', e)

from mold_fix import MoldFixer
from mold_method2_generator import MoldMethod2Generator

P = os.path.join(ROOT, 'web_box_generator', 'uploads',
                 '543e0fdcfa2e_2ROM31-6061 ---(1).stp')

sys.stderr.write('[test] stdout encoding = %r\n' % sys.stdout.encoding)
g = MoldMethod2Generator()
r = g.generate_from_product(P)
assert r.get('success'), r.get('error')
src = r['punch']['details'].get('src')
bb = r['punch']['bbox']
sys.stderr.write('[test] punch src = %s\n' % src)
sys.stderr.write('[test] punch bbox = %s\n' % bb)
sys.stderr.write('[test] RESULT: %s\n' % ('PASS 未退化' if src == 'protrusion' else 'FAIL 仍是 block'))
