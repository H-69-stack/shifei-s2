# -*- coding: utf-8 -*-
"""重试早期绑定：类型库已用 makepy 生成，EnsureDispatch 这次可能就能成。

若成功，带对象参数的方法（FeatureBossThicken2 / ThickenSheet / InsertCutSurface）
应当可以正常调用。
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pythoncom
import win32com.client as win32
import win32com.client.gencache as gencache
from win32com.client import VARIANT

SEWN = r"C:\Temp\m3test\upper_sewn2rom.stp"
NULL = VARIANT(pythoncom.VT_DISPATCH, None)

print("=== 1) EnsureDispatch（类型库已存在）===")
sw = None
try:
    sw = gencache.EnsureDispatch("SldWorks.Application")
    print("   OK  type =", type(sw).__name__)
except Exception as e:
    print("   失败:", repr(e))
    sw = win32.Dispatch("SldWorks.Application")
    print("   回退 Dispatch  type =", type(sw).__name__)
time.sleep(12)
print("   RevisionNumber =", sw.RevisionNumber)


def bodies(m, only_solid=False):
    bs = m.GetBodies2(0 if only_solid else -1, True)
    if bs is None:
        return []
    return list(bs) if isinstance(bs, (tuple, list)) else [bs]


print("\n=== 2) 导入形变面 ===")
err = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
model = sw.LoadFile4(SEWN, "", sw.GetImportFileData(SEWN), err)
time.sleep(2)
sb = bodies(model)
sname = str(sb[0].Name)
v = list(sb[0].GetBodyBox())
H = (v[5] - v[2]) + 0.06
print("   曲面 %r z[%.4f,%.4f]" % (sname, v[2], v[5]))

print("\n=== 3) 检查对象类型（是否早期绑定）===")
fm = model.FeatureManager
print("   model        type =", type(model).__name__)
print("   FeatureManager type =", type(fm).__name__)
print("   曲面体        type =", type(sb[0]).__name__)

print("\n=== 4) 试带对象参数的方法 ===")
model.ClearSelection2(True)
print("   选中曲面:", model.Extension.SelectByID2(sname, "SURFACEBODY", 0, 0, 0, False, 0, NULL, 0))

for tag, fn in (
    ("FeatureBossThicken2(H,True,0,True)", lambda: fm.FeatureBossThicken2(H, True, 0, True)),
    ("ThickenSheet(body,H,True)",          lambda: fm.ThickenSheet(sb[0], H, True)),
):
    try:
        r = fn()
        n = len(bodies(model, True))
        print("   %-38s -> %-5s 实体=%d" % (tag, "OK" if r else "None", n))
        if r and n:
            b2 = list(bodies(model, True)[0].GetBodyBox())
            print("      ★ 实体 bbox z[%.4f,%.4f]" % (b2[2], b2[5]))
            break
    except Exception as e:
        print("   %-38s 异常 %s" % (tag, str(e)[:80]))
