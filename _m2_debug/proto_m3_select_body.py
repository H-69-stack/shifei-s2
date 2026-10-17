# -*- coding: utf-8 -*-
"""绕开 Body2.Select2：改用 ModelDocExtension.SelectByID2(体名, 类型串) 选中曲面体，
然后做「成形到实体」拉伸。"""
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
print("曲面体 type =", type(surf).__name__)

bname = None
for attr in ("Name", "GetType"):
    try:
        bname = getattr(surf, attr)
        print("  body.%s = %r" % (attr, bname))
    except Exception as e:
        print("  body.%s 失败 %r" % (attr, e))

print("\n[1] 试各种类型串选曲面体")
TYPES = ("REFSURFACES", "SURFACEBODY", "SURFACEBODIES", "BODYFEATURE",
         "REFERENCEPLANE", "SKETCH", "FACE")
picked = None
for ts in TYPES:
    model.ClearSelection2(True)
    try:
        ok = model.Extension.SelectByID2(bname, ts, 0, 0, 0, False, 0, NULL, 0)
    except Exception as e:
        print("   %-14s 异常 %r" % (ts, e))
        continue
    print("   %-14s -> %s" % (ts, ok))
    if ok:
        picked = ts
        break
print("   选中类型:", picked)

print("\n[2] 画草图 + 成形到实体拉伸")
model.ClearSelection2(True)
model.Extension.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, NULL, 0)
model.SketchManager.InsertSketch(True)
model.SketchManager.CreateCornerRectangle(-0.125, -0.03, 0, 0.125, 0.03, 0)
model.SketchManager.InsertSketch(True)
if picked:
    model.Extension.SelectByID2(bname, picked, 0, 0, 0, True, 0, NULL, 0)
feat = None
for t1, tag in ((7, "成形到实体(7)"), (4, "成形到曲面(4)")):
    try:
        feat = model.FeatureManager.FeatureExtrusion3(
            True, False, False, t1, 0, 0.0, 0.0, False, False, False, False, 0.0, 0.0,
            False, False, False, False, True, True, True, 0, 0.0, False)
        print("   T1=%s -> %s" % (tag, "OK" if feat else "None"))
        if feat:
            break
    except Exception as e:
        print("   T1=%s 异常 %r" % (tag, e))
        model.Extension.SelectByID2(bname, picked or "REFSURFACES", 0, 0, 0, True, 0, NULL, 0)

sol = bodies(model, True)
print("\n[3] 实体数 =", len(sol))
for x in sol:
    print("   z 范围:", ["%.4f" % v for v in list(x.GetBodyBox())[2:6:3]])
srf = [x for x in bodies(model) if x not in sol]
print("   曲面体数 =", len(srf))
