# -*- coding: utf-8 -*-
"""方法3 关键试验：把形变面曲面体变成【实体】，之后就能全用体级布尔。

试两条路：
  A) FeatureExtruRefSurface3 —— 拉伸曲面 + CapEnd + KnitResult（封端缝合）
  B) FeatureBossThicken2     —— 加厚 + FillVolume
成功判据：出现 SOLID 体。
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pythoncom
import win32com.client as win32
from win32com.client import VARIANT

NULL = VARIANT(pythoncom.VT_DISPATCH, None)
ROOT = r"C:\Users\何雨卓\Desktop\SF\S2"
SEWN = r"C:\Temp\m3test\upper_sewn2rom.stp"

# ---- 用 OCP 生成 2ROM 的缝合上形变面 ----
if not os.path.exists(SEWN):
    sys.path.insert(0, ROOT)
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
    from mold_contact_surface_extractor import MoldContactSurfaceExtractor
    ext = MoldContactSurfaceExtractor()
    res = ext.extract_punch(os.path.join(ROOT, 'web_box_generator', 'uploads',
                                         '543e0fdcfa2e_2ROM31-6061 ---(1).stp'))
    sew = BRepBuilderAPI_Sewing(1e-4)
    sew.Add(ext.result_faces_shape(res))
    sew.Perform()
    os.makedirs(os.path.dirname(SEWN), exist_ok=True)
    w = STEPControl_Writer(); w.Transfer(sew.SewedShape(), STEPControl_AsIs); w.Write(SEWN)
    print("生成缝合形变面:", SEWN)

sw = win32.Dispatch("SldWorks.Application")
time.sleep(15)          # ★等 SW 就绪，否则崩


def bodies(m, only_solid=False):
    bs = m.GetBodies2(0 if only_solid else -1, True)
    if bs is None:
        return []
    return list(bs) if isinstance(bs, (tuple, list)) else [bs]


def bb(bs):
    out = []
    for b in bs:
        try:
            out.append([float(x) for x in list(b.GetBodyBox())[:6]])
        except Exception:
            pass
    if not out:
        return None
    return (min(b[0] for b in out), min(b[1] for b in out), min(b[2] for b in out),
            max(b[3] for b in out), max(b[4] for b in out), max(b[5] for b in out))


print("\n[1] 导入形变面")
model = sw.LoadFile4(SEWN, "", sw.GetImportFileData(SEWN),
                     VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0))
time.sleep(2)
sb0 = bodies(model)
print("   体数=%d  实体=%d" % (len(sb0), len(bodies(model, True))))
sname = str(sb0[0].Name)
b = bb(sb0)
print("   曲面名=%r  bbox z[%.4f,%.4f]" % (sname, b[2], b[5]))

ZMIN, ZMAX = b[2], b[5]
H = (ZMAX - ZMIN) + 0.06          # 往下拉足量（米）

print("\n[2] A) FeatureExtruRefSurface3（拉伸曲面+封端+缝合）")
model.ClearSelection2(True)
ok = model.Extension.SelectByID2(sname, "SURFACEBODY", 0, 0, 0, False, 0, NULL, 0)
print("   选中曲面:", ok)
fm = model.FeatureManager
for tag, args in (
    ("Sd=T,Dir=T,T1=0,Cap1=Cap2=T,Knit=T",
     (True, True, 0, 0.0, 0, 0, H, 0.0, False, False, False, False, 0.0, 0.0,
      False, False, False, False, True, True, False, True)),
    ("Sd=T,Dir=F,T1=0,Cap1=Cap2=T,Knit=T",
     (True, False, 0, 0.0, 0, 0, H, 0.0, False, False, False, False, 0.0, 0.0,
      False, False, False, False, True, True, False, True)),
):
    try:
        f = fm.FeatureExtruRefSurface3(*args)
        n = len(bodies(model, True))
        print("   %-38s -> %-5s 实体=%d %s" % (
            tag, "OK" if f else "None", n,
            ["%.4f~%.4f" % (bb(bodies(model, True))[2], bb(bodies(model, True))[5])] if n else ""))
        if f:
            break
    except Exception as e:
        print("   %-38s 异常 %s" % (tag, str(e)[:70]))

if not bodies(model, True):
    print("\n[3] B) FeatureBossThicken2（加厚+FillVolume）")
    for tag, args in (("thick=H, Dir=T, Fill=True", (H, True, 0, True)),
                      ("thick=H, Dir=F, Fill=True", (H, False, 0, True)),
                      ("thick=H, Dir=T, Fill=False", (H, True, 0, False))):
        model.ClearSelection2(True)
        model.Extension.SelectByID2(sname, "SURFACEBODY", 0, 0, 0, False, 0, NULL, 0)
        try:
            f = fm.FeatureBossThicken2(*args)
            n = len(bodies(model, True))
            print("   %-30s -> %-5s 实体=%d" % (tag, "OK" if f else "None", n))
            if f and n:
                break
        except Exception as e:
            print("   %-30s 异常 %s" % (tag, str(e)[:70]))

nb = bodies(model, True)
print("\n[4] 结果：实体数 =", len(nb))
for x in nb:
    v = list(x.GetBodyBox())
    print("   x[%.4f,%.4f] y[%.4f,%.4f] z[%.4f,%.4f]" % (v[0], v[3], v[1], v[4], v[2], v[5]))
