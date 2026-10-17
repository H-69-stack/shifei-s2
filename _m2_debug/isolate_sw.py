# -*- coding: utf-8 -*-
"""精确隔离：到底是哪一行抛 'Python instance can not be converted to a COM object'。"""
import sys, time, traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pythoncom
import win32com.client as win32
from win32com.client import VARIANT

NULL = VARIANT(pythoncom.VT_DISPATCH, None)
SEWN = r"C:\Temp\m3test\upper_sewn.stp"
sw = win32.Dispatch("SldWorks.Application")
time.sleep(2)


def bodies(model, only_solid=False):
    bs = model.GetBodies2(0 if only_solid else -1, True)
    if bs is None:
        return []
    return list(bs) if isinstance(bs, (tuple, list)) else [bs]


def step(tag, fn):
    try:
        r = fn()
        print("  %-42s OK   %s" % (tag, r if r is not None else ""))
        return r
    except Exception as e:
        print("  %-42s FAIL %r" % (tag, e))
        return None


model = sw.LoadFile4(SEWN, "", sw.GetImportFileData(SEWN),
                     VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0))
time.sleep(1.5)
surf = bodies(model)[0]
print("曲面体已取到:", type(surf).__name__)

print("\n--- 隔离 ---")
step("model.ClearSelection2(True)", lambda: model.ClearSelection2(True))
step("surf.Select2(True, 0)", lambda: surf.Select2(True, 0))
step("model.ClearSelection2(True) 2", lambda: model.ClearSelection2(True))
step("SelectByID2 前视基准面", lambda: model.Extension.SelectByID2(
    "前视基准面", "PLANE", 0, 0, 0, False, 0, NULL, 0))
step("SketchManager.InsertSketch(True)", lambda: model.SketchManager.InsertSketch(True))
step("CreateCornerRectangle", lambda: model.SketchManager.CreateCornerRectangle(
    -0.125, -0.03, 0, 0.125, 0.03, 0))
step("SketchManager.InsertSketch(True) 2", lambda: model.SketchManager.InsertSketch(True))

print("\n--- 纯盲拉（对照，之前成功过）---")
fm = model.FeatureManager
step("FeatureExtrusion3 盲拉 (T1=0)", lambda: fm.FeatureExtrusion3(
    True, False, False, 0, 0, 0.01, 0.0, False, False, False, False, 0.0, 0.0,
    False, False, False, False, True, True, True, 0, 0.0, False))

print("\n--- 选中曲面体后再拉伸 ---")
model.ClearSelection2(True)
for p in ("前视基准面",):
    model.Extension.SelectByID2(p, "PLANE", 0, 0, 0, False, 0, NULL, 0)
model.SketchManager.InsertSketch(True)
model.SketchManager.CreateCornerRectangle(-0.125, -0.03, 0, 0.125, 0.03, 0)
model.SketchManager.InsertSketch(True)
step("surf.Select2(True, 0)", lambda: surf.Select2(True, 0))
step("FeatureExtrusion3 成形到实体 (T1=7)", lambda: fm.FeatureExtrusion3(
    True, False, False, 7, 0, 0.0, 0.0, False, False, False, False, 0.0, 0.0,
    False, False, False, False, True, True, True, 0, 0.0, False))

print("\n--- 试试 SelectByID2 按名字选曲面体 ---")
model.ClearSelection2(True)
tgt = None
try:
    f = model.FirstFeature()
    while f:
        nm = f.Name
        tn = f.GetTypeName2()
        if 'Surface' in tn or '曲面' in tn or 'Surface' in nm:
            tgt = (nm, tn)
        f = f.GetNextFeature()
    print("  找到的曲面特征:", tgt)
except Exception as e:
    print("  遍历特征失败:", repr(e))

sb = bodies(model, True)
print("\n最终实体数:", len(sb))
for x in sb:
    print("   z:", ["%.4f" % v for v in list(x.GetBodyBox())[2:6:3]])
