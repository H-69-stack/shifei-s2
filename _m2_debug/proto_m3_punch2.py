# -*- coding: utf-8 -*-
"""法三上模：用「拉伸 + 成形到实体」拿到形变面以下的实体，再用体级布尔作出上模。

punch = Cut(box[0,P], below)   其中 below = 足迹柱向上拉伸、终止于上形变面曲面体
两个操作数都是【实体】→ 绕开调不动的 InsertCutSurface。
"""
import sys, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pythoncom
import win32com.client as win32
from win32com.client import VARIANT

NULL = VARIANT(pythoncom.VT_DISPATCH, None)
SEWN = r"C:\Temp\m3test\upper_sewn.stp"
MARGIN = 0.005
SW_BODY_OP_CUT = 15902
sw = win32.Dispatch("SldWorks.Application")
time.sleep(2)


def bodies(model, only_solid=False):
    bs = model.GetBodies2(0 if only_solid else -1, True)
    if bs is None:
        return []
    return list(bs) if isinstance(bs, (tuple, list)) else [bs]


def zrange(b):
    v = list(b.GetBodyBox())
    return (v[2], v[5])


def allbb(bs):
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
surf = bodies(model)
b = allbb(surf)
zmin, zmax = b[2], b[5]
P = zmin + (zmax - zmin)
print("[1] 曲面体 bbox:", ["%.4f" % v for v in b], " P=%.4f" % P)

fm = model.FeatureManager
x1, y1, x2, y2 = b[0] - MARGIN, b[1] - MARGIN, b[3] + MARGIN, b[4] + MARGIN


def sketch_rect():
    model.ClearSelection2(True)
    model.Extension.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, NULL, 0)
    model.SketchManager.InsertSketch(True)
    model.SketchManager.CreateCornerRectangle(x1, y1, 0, x2, y2, 0)
    model.SketchManager.InsertSketch(True)


print("\n[2] below = 足迹柱向上拉伸，终止=成形到实体(上形变面)")
below = None
for mark in (0, 1, 2, 4):
    try:
        sketch_rect()
        model.ClearSelection2(True)
        for s in [x for x in bodies(model) if x not in bodies(model, True)]:
            s.Select2(True, mark)
        # 重新选回草图？SW 用最后激活的草图；这里先选中曲面体做终止参考
        feat = fm.FeatureExtrusion3(True, False, False, 7, 0, 0.0, 0.0,
                                    False, False, False, False, 0.0, 0.0,
                                    False, False, False, False, True,
                                    True, True, 0, 0.0, False)
        sol = bodies(model, True)
        print("   mark=%d -> 特征=%s 实体数=%d %s" % (
            mark, "OK" if feat else "None", len(sol),
            ["%.4f~%.4f" % zrange(x) for x in sol]))
        if feat and sol:
            below = sol[-1]
            break
    except Exception as e:
        print("   mark=%d 异常 %r" % (mark, e))

if below is None:
    print("   ! 未得到 below，退出")
    sys.exit(1)

print("\n[3] box = 拉伸盒体 [0, P]（不合并，独立实体）")
sketch_rect()
fm.FeatureExtrusion3(False, False, False, 0, 0, P, 0.0,
                     False, False, False, False, 0.0, 0.0,
                     False, False, False, False, False,   # Merge=False → 独立实体
                     True, True, 0, 0.0, False)
sol = bodies(model, True)
print("   实体数=%d %s" % (len(sol), ["%.4f~%.4f" % zrange(x) for x in sol]))

# 找出 box（z 范围含 P 且最矮的那个）与 below
box = None
for x in sol:
    z0, z1 = zrange(x)
    if abs(z1 - P) < 1e-4 and z0 <= 1e-5:
        box = x
print("   box 定位:", "OK" if box is not None else "失败")

print("\n[4] punch = Operations2(box, below, CUT)")
if box is not None:
    try:
        err = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        res = box.Operations2(SW_BODY_OP_CUT, below, err)
        print("   返回=%s err=%s" % ("Body" if res else "None", err.value))
        sol2 = bodies(model, True)
        print("   实体数=%d %s" % (len(sol2), ["%.4f~%.4f" % zrange(x) for x in sol2]))
    except Exception as e:
        print("   异常 %r" % e)
