# -*- coding: utf-8 -*-
"""P12/P7 smoke: array storage + streaming JSON export."""
import json
import os
import tempfile
import time

from step_reader import StepReader

f = '24TK_1324-1.stp'
reader = StepReader(linear_deflection=0.5, angular_deflection=0.3)

t0 = time.time()
data = reader.read(f)
t1 = time.time()

# 类型检查：array 存储
from array import array
s0 = data['solids'][0]
assert isinstance(s0['vertices'], array), type(s0['vertices'])
assert isinstance(s0['triangles'], array), type(s0['triangles'])
assert s0['vertices'].typecode == 'd' and s0['triangles'].typecode == 'i'
assert s0['vertex_count'] == len(s0['vertices']) // 3
assert s0['triangle_count'] == len(s0['triangles']) // 3
print('read: %.2fs  solids=%d verts=%d tris=%d' % (
    t1 - t0, data['total_solids'], s0['vertex_count'], s0['triangle_count']))
print('bbox:', data['bbox'])

# slice_analyzer 兼容性（消费 vertices/triangles）
from slice_analyzer import MeshSlicer
sa = MeshSlicer(direction=(0, 1, 0))
sa.add_solids_from_raw(data)
print('slice_analyzer loaded: verts=%d tris=%d' % (sa.vertex_count, sa.triangle_count))

# P7：复用 data 导出（只解析一次）
tmp = tempfile.mkdtemp()
out1 = reader.export_mesh_json(f, output_path=os.path.join(tmp, 'm.json'), data=data)
out2 = reader.export_features_json(f, output_path=os.path.join(tmp, 'f.json'), data=data)
print('exports:', os.path.basename(out1), os.path.basename(out2))

# JSON 有效性与内容一致性（流式写出 vs 标准 dump 语义相同）
with open(out1, encoding='utf-8') as fh:
    j = json.load(fh)
assert j['filename'] == data['filename']
assert j['bbox'] == data['bbox']
assert j['stats']['triangles'] == data['total_triangles']
assert len(j['solids']) == data['total_solids']
js0 = j['solids'][0]
assert len(js0['vertices']) == len(s0['vertices'])
assert len(js0['triangles']) == len(s0['triangles'])
# 数值逐一相等（流式 repr 与 json 浮点序列化一致）
assert js0['vertices'][:12] == list(s0['vertices'][:12])
assert js0['triangles'][:12] == list(s0['triangles'][:12])
assert js0['faces'] == s0['faces']
assert len(js0['edges']) == len(s0['edges'])
print('mesh JSON valid: %d solids, %.2f MB' % (
    len(j['solids']), os.path.getsize(out1) / 1024 / 1024))

with open(out2, encoding='utf-8') as fh:
    jf = json.load(fh)
assert jf['statistics']['triangles'] == data['total_triangles']
print('features JSON valid:', list(jf.keys()))

# 默认路径导出（不传 data，内部自解析）也要正常
out3 = reader.export_mesh_json(f, output_path=os.path.join(tmp, 'm2.json'))
with open(out3, encoding='utf-8') as fh:
    json.load(fh)
print('default-path export OK')

print('P12/P7 SMOKE OK')
