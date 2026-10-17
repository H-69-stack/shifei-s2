# -*- coding: utf-8 -*-
"""验证法三几何原语：新建零件 → 选基准面 → 草图矩形 → 拉伸凸台。

SW API 内部长度单位是【米】；对象型参数必须传 VARIANT(VT_DISPATCH, None)。
"""
import sys, os, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pythoncom
import win32com.client as win32
from win32com.client import VARIANT

NULL = VARIANT(pythoncom.VT_DISPATCH, None)
TPL = r"C:\ProgramData\SolidWorks\SOLIDWORKS 2024\templates\gb_part.prtdot"

sw = win32.Dispatch("SldWorks.Application")
print("SW", sw.RevisionNumber)
time.sleep(2)

print("\n[1] 新建零件")
model = sw.NewDocument(TPL, 0, 0, 0)
print("   ->", model)
if model is None:
    sys.exit(1)
time.sleep(0.5)

print("\n[2] 选基准面（枚举候选名）")
picked = None
for pname in ("前视基准面", "Front Plane", "上视基准面", "Top Plane", "右视基准面"):
    try:
        ok = model.Extension.SelectByID2(pname, "PLANE", 0, 0, 0, False, 0, NULL, 0)
    except Exception as e:
        print(f"   {pname}: 异常 {e!r}")
        continue
    print(f"   {pname}: {ok}")
    if ok:
        picked = pname
        break
if not picked:
    print("   ! 没选中任何基准面")
    sys.exit(1)

print(f"\n[3] 在 {picked} 上画 100x60 矩形草图")
model.SketchManager.InsertSketch(True)
model.SketchManager.CreateCornerRectangle(0, 0, 0, 0.10, 0.06, 0)
model.SketchManager.InsertSketch(True)
print("   草图完成")

print("\n[4] 拉伸凸台 50mm")
fm = model.FeatureManager
args = (True, False, False, 0, 0, 0.05, 0.0,
        False, False, False, False, 0.0, 0.0,
        False, False, False, False, True,
        True, True, 0, 0.0, False)
feat = None
try:
    feat = fm.FeatureExtrusion3(*args)
    print("   FeatureExtrusion3 ->", feat)
except Exception as e:
    print("   23 参异常:", repr(e))
    try:
        feat = fm.FeatureExtrusion3(*args[:18])
        print("   18 参 ->", feat)
    except Exception as e2:
        print("   18 参异常:", repr(e2))

print("\n[5] 结果")
try:
    bs = model.GetBodies2(-1, True)
    bs = list(bs) if bs else []
    print("   体数 =", len(bs))
    for b in bs:
        try:
            print("   GetBodyBox =", list(b.GetBodyBox()))
        except Exception as e:
            print("   GetBodyBox 失败", repr(e))
except Exception as e:
    print("   GetBodies2 失败", repr(e))

print("\n[6] 导出 STEP 验证")
out = r"C:\Temp\m3test\prim_box.stp"
os.makedirs(os.path.dirname(out), exist_ok=True)
if os.path.exists(out):
    os.remove(out)
verr = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
vwarn = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
for a in ((out, 0, 1, NULL, NULL, verr, vwarn), (out, 0, 1, NULL, verr, vwarn), (out, 0, 1, NULL, NULL)):
    try:
        model.Extension.SaveAs(*a)
        if os.path.exists(out):
            print("   导出 OK", os.path.getsize(out), "bytes")
            break
    except Exception as e:
        print("   SaveAs 尝试失败:", repr(e))

title = model.GetTitle
try:
    sw.CloseDoc(title)
    print("   已关闭", title)
except Exception as e:
    print("   关闭失败", repr(e))
