# -*- coding: utf-8 -*-
"""再试两条：ThickenSheet(Sheet,Thickness,Direction) 与 FeatureExtruRefSurface2。"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pythoncom
import win32com.client as win32
from win32com.client import VARIANT

NULL = VARIANT(pythoncom.VT_DISPATCH, None)
SEWN = r"C:\Temp\m3test\upper_sewn2rom.stp"

sw = win32.Dispatch("SldWorks.Application")
time.sleep(15)


def bodies(m, only_solid=False):
    bs = m.GetBodies2(0 if only_solid else -1, True)
    if bs is None:
        return []
    return list(bs) if isinstance(bs, (tuple, list)) else [bs]


model = sw.LoadFile4(SEWN, "", sw.GetImportFileData(SEWN),
                     VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0))
time.sleep(2)
sb = bodies(model)
sname = str(sb[0].Name)
v = list(sb[0].GetBodyBox())
H = (v[5] - v[2]) + 0.06
print("曲面 %r  z[%.4f,%.4f]  拉伸量 H=%.4f m" % (sname, v[2], v[5], H))
fm = model.FeatureManager

print("\n[A] ThickenSheet(Sheet=曲面体, Thickness, Direction)")
for tag, args in (("T=H, Dir=T", (sb[0], H, True)),
                  ("T=H, Dir=F", (sb[0], H, False)),
                  ("T=-H, Dir=T", (sb[0], -H, True))):
    model.ClearSelection2(True)
    model.Extension.SelectByID2(sname, "SURFACEBODY", 0, 0, 0, False, 0, NULL, 0)
    try:
        f = fm.ThickenSheet(*args)
        n = len(bodies(model, True))
        print("   %-14s -> %-5s 实体=%d" % (tag, "OK" if f else "None", n))
        if f and n:
            break
    except Exception as e:
        print("   %-14s 异常 %s" % (tag, str(e)[:70]))

print("\n[B] FeatureExtruRefSurface2")
if not bodies(model, True):
    for tag, args in (
        ("Sd=T,Dir=T,T1=0,D1=H,Cap=T,Knit=T",
         (True, True, 0, 0.0, 0, 0, H, 0.0, False, False, False, False, 0.0, 0.0,
          False, False, False, False, True, True, True)),
        ("Sd=T,Dir=F,T1=0,D1=H,Cap=T,Knit=T",
         (True, False, 0, 0.0, 0, 0, H, 0.0, False, False, False, False, 0.0, 0.0,
          False, False, False, False, True, True, True)),
    ):
        model.ClearSelection2(True)
        model.Extension.SelectByID2(sname, "SURFACEBODY", 0, 0, 0, False, 0, NULL, 0)
        try:
            f = fm.FeatureExtruRefSurface2(*args)
            n = len(bodies(model, True))
            print("   %-36s -> %-5s 实体=%d" % (tag, "OK" if f else "None", n))
            if f and n:
                break
        except Exception as e:
            print("   %-36s 异常 %s" % (tag, str(e)[:70]))

nb = bodies(model, True)
print("\n结果：实体数 =", len(nb))
if nb:
    v2 = list(nb[0].GetBodyBox())
    print("   bbox z[%.4f,%.4f]" % (v2[2], v2[5]))
