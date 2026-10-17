# -*- coding: utf-8 -*-
"""验证上模最后一个关键步骤：InsertCutSurface（用形变面切除盒体，保留曲面上方）。

用已缝合好的形变面 C:\\Temp\\m3test\\upper_sewn.stp（z 0.003~0.071 m）。
"""
import sys, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pythoncom
import win32com.client as win32
from win32com.client import VARIANT

NULL = VARIANT(pythoncom.VT_DISPATCH, None)
SEWN = r"C:\Temp\m3test\upper_sewn.stp"
MARGIN = 0.005          # 米

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
print("[1] 导入:", model.GetTitle)
srf = bodies(model)
b = bb(srf)
print("   曲面 bbox:", ["%.4f" % v for v in b])

zmin, zmax = b[2], b[5]
dz = zmax - zmin
P = zmin + dz
z_lo = zmin - MARGIN
print("   zmin=%.4f zmax=%.4f  P=%.4f  z_lo=%.4f" % (zmin, zmax, P, z_lo))

print("\n[2] 在上视基准面画足迹矩形，双向盲拉成盒体 [-margin, P]")
model.ClearSelection2(True)
ok_pl = None
for p in ("上视基准面", "Top Plane"):
    try:
        if model.Extension.SelectByID2(p, "PLANE", 0, 0, 0, False, 0, NULL, 0):
            ok_pl = p; break
    except Exception:
        pass
print("   基准面:", ok_pl)
model.SketchManager.InsertSketch(True)
model.SketchManager.CreateCornerRectangle(b[0] - MARGIN, b[1] - MARGIN, 0,
                                          b[3] + MARGIN, b[4] + MARGIN, 0)
model.SketchManager.InsertSketch(True)
fm = model.FeatureManager
feat = fm.FeatureExtrusion3(False, False, False, 0, 0, P, MARGIN,
                            False, False, False, False, 0.0, 0.0,
                            False, False, False, False, True,
                            True, True, 0, 0.0, False)
print("   拉伸特征:", "OK" if feat else "失败")
sb = bodies(model, True)
print("   实体数:", len(sb), " bbox:", ["%.4f" % v for v in bb(sb)])

print("\n[3] InsertCutSurface（保留曲面上方）")
done = False
for args, tag in (((False, 0), "2参(Flip=False,Keep=0)"),
                  ((False, 1), "2参(Flip=False,Keep=1)"),
                  ((True, 0), "2参(Flip=True,Keep=0)"),
                  ((True, 1), "2参(Flip=True,Keep=1)")):
    try:
        model.ClearSelection2(True)
        srf_now = [x for x in bodies(model) if x not in bodies(model, True)]
        for s in srf_now:
            s.Select2(False, 4)
        r = fm.InsertCutSurface(*args)
        newsb = bodies(model, True)
        print("   %-26s -> %s  实体数=%d bbox=%s" % (
            tag, "OK" if r else "None", len(newsb),
            ["%.4f" % v for v in bb(newsb)] if newsb else "-"))
        if r:
            done = True
            break
    except Exception as e:
        print("   %-26s 异常 %r" % (tag, e))
print("\n   结果:", "成功" if done else "全部失败")
