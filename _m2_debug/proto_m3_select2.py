# -*- coding: utf-8 -*-
"""修正：bname = surf.Name（之前被 GetType 覆盖）。选曲面体 + 成形到实体。"""
import sys, time

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


model = sw.LoadFile4(SEWN, "", sw.GetImportFileData(SEWN),
                     VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0))
time.sleep(1.5)
surf = bodies(model)[0]
bname = str(surf.Name)
print("曲面体名 =", repr(bname))

print("\n[1] 试类型串")
TYPES = ("SURFACEBODY", "SURFACEBODIES", "REFSURFACES", "BODYFEATURE",
         "SOLIDBODIES", "REFERENCECURVES", "SKETCH")
picked = None
for ts in TYPES:
    model.ClearSelection2(True)
    try:
        ok = model.Extension.SelectByID2(bname, ts, 0, 0, 0, False, 0, NULL, 0)
    except Exception as e:
        print("   %-14s 异常 %r" % (ts, str(e)[:70])); continue
    print("   %-14s -> %s" % (ts, ok))
    if ok:
        picked = ts; break

print("\n[2] 草图 + 拉伸")
model.ClearSelection2(True)
model.Extension.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, NULL, 0)
model.SketchManager.InsertSketch(True)
model.SketchManager.CreateCornerRectangle(-0.125, -0.03, 0, 0.125, 0.03, 0)
model.SketchManager.InsertSketch(True)

if picked:
    model.Extension.SelectByID2(bname, picked, 0, 0, 0, True, 0, NULL, 0)

feat = None
for t1 in (7, 4):
    try:
        feat = model.FeatureManager.FeatureExtrusion3(
            True, False, False, t1, 0, 0.0, 0.0, False, False, False, False, 0.0, 0.0,
            False, False, False, False, True, True, True, 0, 0.0, False)
        print("   T1=%d -> %s" % (t1, "OK" if feat else "None"))
        if feat:
            break
    except Exception as e:
        print("   T1=%d 异常 %r" % (t1, str(e)[:80]))

sol = bodies(model, True)
print("\n[3] 实体数 =", len(sol))
for x in sol:
    print("   z:", ["%.4f" % v for v in list(x.GetBodyBox())[2:6:3]])

if not sol and picked:
    print("\n[4] 退一步：不选体，直接盲拉大盒体（对照）")
    model.ClearSelection2(True)
    model.Extension.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, NULL, 0)
    model.SketchManager.InsertSketch(True)
    model.SketchManager.CreateCornerRectangle(-0.125, -0.03, 0, 0.125, 0.03, 0)
    model.SketchManager.InsertSketch(True)
    f2 = model.FeatureManager.FeatureExtrusion3(
        False, False, False, 0, 0, 0.071, 0.005, False, False, False, False, 0.0, 0.0,
        False, False, False, False, True, True, True, 0, 0.0, False)
    print("   盒体:", "OK" if f2 else "None")
    for x in bodies(model, True):
        print("   z:", ["%.4f" % v for v in list(x.GetBodyBox())[2:6:3]])
