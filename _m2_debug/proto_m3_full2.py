# -*- coding: utf-8 -*-
"""方法3 原型 v2：
  ① OCP：产品 ∪ 凸起 = 单一实体（"产品下表面以上空间"）→ 导出
  ② SW ：导入(1体) → 草图拉伸建基座盒 → Operations2 一次布尔 → 导出型腔
  ③ 产出的型腔用水平切片面积验证（应出现明显开腔）
"""
import os
import sys
import time

ROOT = r"C:\Users\何雨卓\Desktop\SF\S2"
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pythoncom
import win32com.client as win32
from win32com.client import VARIANT

NULL = VARIANT(pythoncom.VT_DISPATCH, None)
PRODUCT = os.path.join(ROOT, "web_box_generator", "uploads",
                       "543e0fdcfa2e_2ROM31-6061 ---(1).stp")
PREP = r"C:\Temp\m3test\m3_union.stp"
OUTDIR = os.path.join(ROOT, "_m2_debug", "m3_out")
SW_BODY_OP_CUT = 15902
os.makedirs(OUTDIR, exist_ok=True)

print("=== ① OCP：产品 ∪ 凸起 ===")
from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
from OCP.IFSelect import IFSelect_RetDone
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID
from mold_fix import MoldFixer
from mold_method2_generator import MoldMethod2Generator, _bbox_accurate

solids = MoldFixer._extract_solids(PRODUCT)
product = MoldFixer._make_compound([s for _, s in solids])
pb = _bbox_accurate(product)
Z_B, Z_A = pb[2], pb[5]
dt = Z_A - Z_B

g = MoldMethod2Generator()
punch, n_used, n_skip = g.build_punch_by_extrusion(product, Z_A + dt)
fu = BRepAlgoAPI_Fuse(product, punch)
fu.SetFuzzyValue(1e-3)
fu.Build()
union = fu.Shape() if fu.IsDone() else None
ns = 0
e = TopExp_Explorer(union, TopAbs_SOLID)
while e.More():
    ns += 1
    e.Next()
print("  产品 z[%.2f,%.2f]  凸起棱柱=%d  并集 solids=%d  z[%.2f,%.2f]" % (
    Z_B, Z_A, n_used, ns, _bbox_accurate(union)[2], _bbox_accurate(union)[5]))

w = STEPControl_Writer(); w.Transfer(union, STEPControl_AsIs)
print("  导出:", w.Write(PREP) == IFSelect_RetDone, PREP)

print("\n=== ② SolidWorks ===")
sw = win32.Dispatch("SldWorks.Application")
time.sleep(15)


def bodies(m, only_solid=False):
    bs = m.GetBodies2(0 if only_solid else -1, True)
    if bs is None:
        return []
    return list(bs) if isinstance(bs, (tuple, list)) else [bs]


def zr(b):
    v = list(b.GetBodyBox())
    return v[2], v[5]


err = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
mdoc = sw.LoadFile4(PREP, "", sw.GetImportFileData(PREP), err)
time.sleep(2)
sol = bodies(mdoc, True)
print("  导入 errors=%s 实体数=%d  z[%.2f,%.2f]" % (err.value, len(sol), *zr(sol[0])))

# 建基座盒
x1, y1 = (pb[0] - 5) / 1000.0, (pb[1] - 5) / 1000.0
x2, y2 = (pb[3] + 5) / 1000.0, (pb[4] + 5) / 1000.0
up = Z_A / 1000.0
dn = (dt - Z_B) / 1000.0
mdoc.ClearSelection2(True)
mdoc.Extension.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, NULL, 0)
mdoc.SketchManager.InsertSketch(True)
mdoc.SketchManager.CreateCornerRectangle(x1, y1, 0, x2, y2, 0)
mdoc.SketchManager.InsertSketch(True)
r = mdoc.FeatureManager.FeatureExtrusion3(
    False, False, False, 0, 0, up, dn, False, False, False, False, 0.0, 0.0,
    False, False, False, False, False, True, True, 0, 0.0, False)
print("  基座盒拉伸:", "OK" if r else "None")

sol = bodies(mdoc, True)
print("  实体数=%d" % len(sol))
box = union_body = None
for x in sol:
    z0, z1 = zr(x)
    if z0 < Z_B - 1:
        box = x
    else:
        union_body = x
print("  基座盒=%s  并集体=%s" % ("OK" if box else "X", "OK" if union_body else "X"))
if not (box and union_body):
    for x in sol:
        print("     %s z[%.2f,%.2f]" % (x.Name, *zr(x)))
    sys.exit(1)

print("\n  布尔：盒 − (产品∪凸起) = 型腔")
res = box.Operations2(SW_BODY_OP_CUT, union_body, 0)
print("     返回 =", "Body" if res else "None")
time.sleep(0.5)

# 导出
out_cav = os.path.join(OUTDIR, "2ROM_CAVITY_M3.stp")
if os.path.exists(out_cav):
    os.remove(out_cav)
verr = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
vwarn = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
mdoc.Extension.SaveAs(out_cav, 0, 1, NULL, verr, vwarn)
print("  导出型腔:", os.path.exists(out_cav),
      os.path.getsize(out_cav) if os.path.exists(out_cav) else "")

# 另外把凸起单独导出（SW 里再导入一次不方便，直接用 OCP 的）
w2 = STEPControl_Writer(); w2.Transfer(punch, STEPControl_AsIs)
out_p = os.path.join(OUTDIR, "2ROM_PUNCH_M3.stp")
w2.Write(out_p)
print("  导出上模:", os.path.exists(out_p), os.path.getsize(out_p) if os.path.exists(out_p) else "")

print("\n=== ③ 验证型腔（水平切片面积）===")
from OCP.gp import gp_Pnt
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
cav_solids = MoldFixer._extract_solids(out_cav)
cav = MoldFixer._make_compound([s for _, s in cav_solids])
cb = _bbox_accurate(cav)
A = (cb[3] - cb[0]) * (cb[4] - cb[1])
T = 0.5


def vol(s):
    p = GProp_GProps(); BRepGProp.VolumeProperties_s(s, p); return p.Mass()


print("  基座截面=%.0f mm2  型腔 z[%.2f,%.2f]" % (A, cb[2], cb[5]))
for i in range(8):
    z = cb[2] + (cb[5] - cb[2]) * (i + 0.5) / 8
    slab = BRepPrimAPI_MakeBox(gp_Pnt(cb[0]-1, cb[1]-1, z-T/2),
                               (cb[3]-cb[0])+2, (cb[4]-cb[1])+2, T).Shape()
    cm = BRepAlgoAPI_Common(cav, slab); cm.SetFuzzyValue(1e-4); cm.Build()
    a = vol(cm.Shape())/T if cm.IsDone() else 0
    print("    z=%8.2f  %8.0f  %5.1f%%  %s" % (z, a, 100*a/A, "有空腔" if a < 0.99*A else "实心"))
