# -*- coding: utf-8 -*-
"""法三几何原型：上模 = 足迹盒体（定到 [z_lo, P]） ∩ 形变面以上。

验证链：导入形变面 → 草图拉伸盒体 → Body2.ApplyTransform 精确定位
        → InsertCutSurface(保留曲面上方) → 导出
"""
import sys, os, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pythoncom
import win32com.client as win32
from win32com.client import VARIANT

NULL = VARIANT(pythoncom.VT_DISPATCH, None)
TPL = r"C:\ProgramData\SolidWorks\SOLIDWORKS 2024\templates\gb_part.prtdot"
UPPER = r"C:\Users\何雨卓\Desktop\SF\S2\web_box_generator\uploads\925e79889839_2ROM31-6061 ---(1)_PUNCH_CONTACT_FACES.stp"
MARGIN = 5.0
H1 = None

sw = win32.Dispatch("SldWorks.Application")
print("SW", sw.RevisionNumber)
time.sleep(2)


def bodies(model, only_solid=False):
    bs = model.GetBodies2(0 if only_solid else -1, True)
    if bs is None:
        return []
    return list(bs) if isinstance(bs, (tuple, list)) else [bs]


def bbox(bs):
    boxes = []
    for b in bs:
        try:
            v = list(b.GetBodyBox())
            if len(v) >= 6:
                boxes.append([float(x) for x in v[:6]])
        except Exception:
            pass
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), min(b[2] for b in boxes),
            max(b[3] for b in boxes), max(b[4] for b in boxes), max(b[5] for b in boxes))


def sel_plane(model, candidates):
    for p in candidates:
        try:
            if model.Extension.SelectByID2(p, "PLANE", 0, 0, 0, False, 0, NULL, 0):
                return p
        except Exception:
            pass
    return None


def make_box_body(model, x1, y1, x2, y2, up_m, down_m=None):
    """在上视基准面画矩形并拉伸成盒体（长度单位=米）。"""
    model.ClearSelection2(True)
    pl = sel_plane(model, ("上视基准面", "Top Plane"))
    print("     基准面:", pl)
    model.SketchManager.InsertSketch(True)
    model.SketchManager.CreateCornerRectangle(x1, y1, 0, x2, y2, 0)
    model.SketchManager.InsertSketch(True)
    fm = model.FeatureManager
    if down_m is None:
        feat = fm.FeatureExtrusion3(True, False, False, 0, 0, up_m, 0.0,
                                    False, False, False, False, 0.0, 0.0,
                                    False, False, False, False, True,
                                    True, True, 0, 0.0, False)
    else:
        # 双向盲拉：T1=0 向上 up_m，T2=0 向下 down_m
        feat = fm.FeatureExtrusion3(False, False, False, 0, 0, up_m, down_m,
                                    False, False, False, False, 0.0, 0.0,
                                    False, False, False, False, True,
                                    True, True, 0, 0.0, False)
    return feat


print("\n[1] 导入上形变面")
model = sw.LoadFile4(UPPER, "", sw.GetImportFileData(UPPER),
                     VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0))
print("   ->", model.GetTitle)
time.sleep(0.8)
allb = bodies(model)
print("   体数:", len(allb), " 实体数:", len(bodies(model, True)))
bb = bbox(allb)
print("   包围盒 x[%.2f,%.2f] y[%.2f,%.2f] z[%.2f,%.2f]" % (bb[0], bb[3], bb[1], bb[4], bb[2], bb[5]))

zmin, zmax = bb[2], bb[5]
dz = zmax - zmin
h1 = dz if H1 is None else H1
P = zmin + h1
z_lo = zmin - MARGIN
print("   dz=%.3f  h1=%.3f  P=%.3f  z_lo=%.3f" % (dz, h1, P, z_lo))

print("\n[2] 画足迹盒体（从 z=0 向上拉，再平移到 [z_lo, P]）")
H = (P - z_lo) / 1000.0        # 米
x1, y1, x2, y2 = (bb[0] - MARGIN) / 1000.0, (bb[1] - MARGIN) / 1000.0, \
                 (bb[3] + MARGIN) / 1000.0, (bb[4] + MARGIN) / 1000.0
feat = make_box_body(model, x1, y1, x2, y2, H)
print("   拉伸特征:", "OK" if feat else "失败")

solids = bodies(model, True)
print("   拉伸后实体数:", len(solids))
box_body = solids[0] if solids else None
if box_body is None:
    sys.exit(1)
print("   盒体 z 范围:", list(box_body.GetBodyBox())[2:6:3])

print("\n[3] ApplyTransform 把盒体移到 z_lo")
try:
    mu = sw.GetMathUtility()
    dz_m = z_lo / 1000.0
    data = (1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, dz_m, 1.0)
    xf = mu.CreateTransform(data)
    box_body.ApplyTransform(xf)
    print("   新 z 范围:", list(box_body.GetBodyBox())[2:6:3])
except Exception as e:
    print("   ApplyTransform 失败:", repr(e))

print("\n[4] 用形变面切除（保留曲面上方）")
fm = model.FeatureManager
for keep in (0, 1):
    try:
        model.ClearSelection2(True)
        surf = [b for b in bodies(model) if b not in bodies(model, True)]
        print("   曲面体数:", len(surf))
        for s in surf:
            s.Select2(False, 1)
        r = fm.InsertCutSurface(False, keep, True, True, NULL,
                                VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0))
        print("   InsertCutSurface(keep=%d) ->" % keep, "OK" if r else "None")
        if r:
            break
    except Exception as e:
        print("   InsertCutSurface(keep=%d) 异常: %r" % (keep, e))

print("\n[5] 结果")
bs = bodies(model, True)
print("   实体数:", len(bs))
for b in bs:
    try:
        print("   z 范围:", list(b.GetBodyBox())[2:6:3], " 体积=", b.GetMassProperties(0)[3] if False else '')
    except Exception as e:
        print("   读取失败", repr(e))
