# -*- coding: utf-8 -*-
"""方法3 原型（全用已验证可行的 API）：
  ① OCP 准备实体：产品实体 + 上模凸起实体 → 合并导出成一个多实体 STEP
  ② SolidWorks：导入 → 用草图拉伸建基座盒 → Body2.Operations2 做两次布尔
                （盒 − 产品 − 凸起 = 型腔）→ 导出 STEP
所有 SW 调用都是实测通过的：LoadFile4 / NewDocument / SelectByID2 / FeatureExtrusion3 /
Operations2 / SaveAs。避开了调不动的 InsertCutSurface / FeatureBossThicken2。
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
TPL = r"C:\ProgramData\SolidWorks\SOLIDWORKS 2024\templates\gb_part.prtdot"
PRODUCT = os.path.join(ROOT, "web_box_generator", "uploads",
                       "543e0fdcfa2e_2ROM31-6061 ---(1).stp")
PREP = r"C:\Temp\m3test\m3_prep.stp"
OUT_CAV = r"C:\Users\何雨卓\Desktop\SF\S2\_m2_debug\m3_out\1131_CAVITY_M3.stp"
SW_BODY_OP_CUT = 15902

os.makedirs(os.path.dirname(OUT_CAV), exist_ok=True)

# ============ ① OCP：准备 产品 + 凸起 ============
print("=== ① OCP 准备实体 ===")
from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
from OCP.IFSelect import IFSelect_RetDone
from mold_fix import MoldFixer
from mold_method2_generator import MoldMethod2Generator, _bbox_accurate

solids = MoldFixer._extract_solids(PRODUCT)
product = MoldFixer._make_compound([s for _, s in solids])
pb = _bbox_accurate(product)
Z_B, Z_A = pb[2], pb[5]
dt = Z_A - Z_B
print("  产品 z[%.2f,%.2f] 高=%.2f  xy[%.1f,%.1f .. %.1f,%.1f]" % (
    Z_B, Z_A, dt, pb[0], pb[1], pb[3], pb[4]))

g = MoldMethod2Generator()
punch, n_used, n_skip = g.build_punch_by_extrusion(product, Z_A + dt)
print("  凸起: 棱柱=%d 跳过=%d  solids=%d" % (
    n_used, n_skip, MoldFixer._solid_count(punch)))
pbp = _bbox_accurate(punch)
print("  凸起 z[%.2f,%.2f]" % (pbp[2], pbp[5]))

comp = MoldFixer._make_compound([product, punch])
os.makedirs(os.path.dirname(PREP), exist_ok=True)
w = STEPControl_Writer()
w.Transfer(comp, STEPControl_AsIs)
print("  导出准备件:", w.Write(PREP) == IFSelect_RetDone, PREP)

# ============ ② SolidWorks ============
print("\n=== ② SolidWorks ===")
sw = win32.Dispatch("SldWorks.Application")
time.sleep(15)          # 等 SW 就绪


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
print("  导入: errors=%s  实体数=%d" % (err.value, len(sol)))
for i, x in enumerate(sol):
    z0, z1 = zr(x)
    print("     body[%d] %-16s z[%.2f,%.2f]" % (i, str(x.Name)[:16], z0, z1))
if len(sol) < 2:
    print("  ! 实体不足 2 个，无法继续（多实体 STEP 被拆开了）")
    sys.exit(1)

# 建基座盒：在 前视基准面（Z 法向）上画足迹矩形，双向盲拉覆盖 [Z_B-70, Z_A]
base_thk = dt
x1, y1 = (pb[0] - 5) / 1000.0, (pb[1] - 5) / 1000.0
x2, y2 = (pb[3] + 5) / 1000.0, (pb[4] + 5) / 1000.0
up = Z_A / 1000.0
dn = (base_thk - Z_B) / 1000.0        # 使盒底 = Z_B - base_thk
print("  基座盒: x[%.1f,%.1f] y[%.1f,%.1f]  up=%.3f dn=%.3f (m)" % (
    x1, y1, x2, y2, up, dn))
mdoc.ClearSelection2(True)
print("  选中基准面:", mdoc.Extension.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, NULL, 0))
mdoc.SketchManager.InsertSketch(True)
mdoc.SketchManager.CreateCornerRectangle(x1, y1, 0, x2, y2, 0)
mdoc.SketchManager.InsertSketch(True)
fm = mdoc.FeatureManager
f = fm.FeatureExtrusion3(False, False, False, 0, 0, up, dn,
                         False, False, False, False, 0.0, 0.0,
                         False, False, False, False, False,   # Merge=False → 独立实体
                         True, True, 0, 0.0, False)
print("  拉伸基座:", "OK" if f else "None")

sol = bodies(mdoc, True)
print("  实体数=%d" % len(sol))
box = prod = prot = None
for x in sol:
    z0, z1 = zr(x)
    nm = str(x.Name)
    if abs(z1 - Z_A) < 0.05 and z0 < Z_B - 1:
        box = x
    elif abs(z1 - (Z_A + dt)) < 0.5:
        prot = x
    else:
        prod = x
print("  定位: 基座盒=%s 产品=%s 凸起=%s" % (
    "OK" if box else "X", "OK" if prod else "X", "OK" if prot else "X"))
if not (box and prod and prot):
    print("  ! 无法区分三个实体")
    for x in sol:
        print("     %s z[%.2f,%.2f]" % (x.Name, *zr(x)))
    sys.exit(1)

print("\n  布尔1: 盒 − 产品")
r1 = box.Operations2(SW_BODY_OP_CUT, prod, 0)
print("     返回 =", "Body" if r1 else "None")
time.sleep(0.3)
print("  布尔2: (盒−产品) − 凸起")
r2 = (r1 if r1 else box).Operations2(SW_BODY_OP_CUT, prot, 0)
print("     返回 =", "Body" if r2 else "None")
time.sleep(0.3)
for x in bodies(mdoc, True):
    z0, z1 = zr(x)
    print("     %-18s z[%.2f,%.2f]  xy[%.1f,%.1f]" % (
        str(x.Name)[:18], z0, z1, list(x.GetBodyBox())[0], list(x.GetBodyBox())[3]))

print("\n  导出 STEP:", OUT_CAV)
if os.path.exists(OUT_CAV):
    os.remove(OUT_CAV)
verr = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
vwarn = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
try:
    mdoc.Extension.SaveAs(OUT_CAV, 0, 1, NULL, verr, vwarn)
    print("     OK", os.path.exists(OUT_CAV), os.path.getsize(OUT_CAV) if os.path.exists(OUT_CAV) else "")
except Exception as e:
    print("     异常:", repr(e))
