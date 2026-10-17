# -*- coding: utf-8 -*-
"""扫 mark：选曲面体后用「成形到实体/曲面」拉伸。"""
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
time.sleep(2.0)
bs = bodies(model)
if not bs:
    print("导入为空，退出"); sys.exit(1)
bname = str(bs[0].Name)
print("曲面体 =", repr(bname))

fm = model.FeatureManager
found = None
for t1 in (7, 4):
    for mark in (0, 1, 2, 4, 8, 16):
        model.ClearSelection2(True)
        model.Extension.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, NULL, 0)
        model.SketchManager.InsertSketch(True)
        model.SketchManager.CreateCornerRectangle(-0.125, -0.03, 0, 0.125, 0.03, 0)
        model.SketchManager.InsertSketch(True)
        model.Extension.SelectByID2(bname, "SURFACEBODY", 0, 0, 0, True, mark, NULL, 0)
        feat = None
        try:
            feat = fm.FeatureExtrusion3(
                True, False, False, t1, 0, 0.0, 0.0, False, False, False, False, 0.0, 0.0,
                False, False, False, False, True, True, True, 0, 0.0, False)
        except Exception as e:
            print("  T1=%d mark=%-2d 异常 %s" % (t1, mark, str(e)[:60])); continue
        sol = bodies(model, True)
        zr = ["%.4f~%.4f" % (list(x.GetBodyBox())[2], list(x.GetBodyBox())[5]) for x in sol]
        print("  T1=%d mark=%-2d -> %-5s 实体=%d %s" % (
            t1, mark, "OK" if feat else "None", len(sol), zr))
        if feat:
            found = (t1, mark)
            break
    if found:
        break
print("\n成功组合:", found)
