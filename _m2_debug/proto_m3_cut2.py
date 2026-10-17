# -*- coding: utf-8 -*-
"""修两处：① 用 Z 法向基准面（本模板 前视基准面 才是 Z 法向）；
        ② InsertCutSurface 用 6 参 + 正确的 VARIANT 封送。"""
import sys, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pythoncom
import win32com.client as win32
from win32com.client import VARIANT

NULL = VARIANT(pythoncom.VT_DISPATCH, None)
SEWN = r"C:\Temp\m3test\upper_sewn.stp"
MARGIN = 0.005

sw = win32.Dispatch("SldWorks.Application")
time.sleep(2)


def bodies(model, only_solid=False):
    bs = model.GetBodies2(0 if only_solid else -1, True)
    if bs is None:
        return []
    return list(bs) if isinstance(bs, (tuple, list)) else [bs]


def bb(bs):
    boxes = []
    for b in bs:
        try:
            boxes.append([float(x) for x in list(b.GetBodyBox())[:6]])
        except Exception:
            pass
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), min(b[2] for b in boxes),
            max(b[3] for b in boxes), max(b[4] for b in boxes), max(b[5] for b in boxes))


model = sw.LoadFile4(SEWN, "", sw.GetImportFileData(SEWN),
                     VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0))
time.sleep(1.5)
b = bb(bodies(model))
print("[1] 曲面 bbox:", ["%.4f" % v for v in b])
zmin, zmax = b[2], b[5]
P = zmin + (zmax - zmin)
print("   zmin=%.4f P=%.4f" % (zmin, P))

print("\n[2] 用【前视基准面】(Z 法向) 画矩形并双向盲拉")
model.ClearSelection2(True)
print("   选中:", model.Extension.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, NULL, 0))
model.SketchManager.InsertSketch(True)
model.SketchManager.CreateCornerRectangle(b[0] - MARGIN, b[1] - MARGIN, 0,
                                          b[3] + MARGIN, b[4] + MARGIN, 0)
model.SketchManager.InsertSketch(True)
fm = model.FeatureManager
fm.FeatureExtrusion3(False, False, False, 0, 0, P, MARGIN,
                     False, False, False, False, 0.0, 0.0,
                     False, False, False, False, True, True, True, 0, 0.0, False)
sb = bodies(model, True)
print("   盒体 bbox:", ["%.4f" % v for v in bb(sb)])

print("\n[3] InsertCutSurface 6 参尝试")
arr0 = VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, ())
variants = [
    ("Bodies=(),Err=byref", (False, 0, True, True, arr0,
                             VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0))),
    ("Bodies=(),Err=0",     (False, 0, True, True, arr0, 0)),
    ("Bodies=NULL,Err=0",   (False, 0, True, True, NULL, 0)),
    ("Keep=1,Bodies=()",    (False, 1, True, True, arr0, 0)),
    ("Flip=True,Keep=0",    (True, 0, True, True, arr0, 0)),
]
for tag, args in variants:
    try:
        model.ClearSelection2(True)
        for s in [x for x in bodies(model) if x not in bodies(model, True)]:
            s.Select2(False, 4)
        r = fm.InsertCutSurface(*args)
        ns = bodies(model, True)
        print("   %-22s -> %-5s 实体=%d bbox=%s" % (
            tag, "OK" if r else "None", len(ns),
            ["%.4f" % v for v in bb(ns)] if ns else "-"))
        if r:
            print("\n   ★ 成功！上模已生成")
            break
    except Exception as e:
        print("   %-22s 异常 %r" % (tag, e))
