# -*- coding: utf-8 -*-
"""探测3：GetImportFileData + LoadFile4；并检查 3D Interconnect 状态。"""
import sys, os, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pythoncom
import win32com.client as win32
from win32com.client import VARIANT

P = r"C:\Users\何雨卓\Desktop\SF\S2\web_box_generator\uploads\543e0fdcfa2e_2ROM31-6061 ---(1).stp"
sw = win32.Dispatch("SldWorks.Application")


def titles():
    try:
        d = sw.GetDocuments
        return [x.GetTitle for x in d] if d else []
    except Exception as e:
        return [f"<{e!r}>"]


print("SW =", sw.RevisionNumber)
print("初始文档:", titles())

print("\n[A] GetImportFileData")
idata = None
try:
    idata = sw.GetImportFileData(P)
    print("  返回 =", idata, type(idata).__name__)
except Exception as e:
    print("  失败:", repr(e))

if idata is not None:
    for prop, val in (("ImportAsReference", False), ("MapConfigurationData", False)):
        try:
            setattr(idata, prop, val)
            print(f"  set {prop}={val} OK")
        except Exception as e:
            print(f"  set {prop} 失败: {type(e).__name__}")
    print("\n[B] LoadFile4(path, 'r', idata)")
    try:
        m = sw.LoadFile4(P, "r", idata)
        print("  返回 =", m, type(m).__name__ if m else "")
        if m is not None:
            print("  title =", m.GetTitle)
    except Exception as e:
        print("  失败:", repr(e))
    time.sleep(1.0)
    print("  文档:", titles())

print("\n[C] OpenDoc6 后立即看 ActiveDoc（确认是否真导入了）")
err = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
warn = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
try:
    sw.OpenDoc6(P, 1, 0, "", err, warn)
except Exception as e:
    print("  异常:", repr(e))
print("  errors =", err.value, "warnings =", warn.value)
time.sleep(1.0)
print("  文档:", titles())
try:
    ad = sw.ActiveDoc
    print("  ActiveDoc =", ad.GetTitle if ad else None)
    if ad:
        try:
            print("  ActiveDoc 体数 =", len(ad.GetBodies2(-1, True) or []))
        except Exception as e:
            print("  GetBodies2:", repr(e))
except Exception as e:
    print("  ActiveDoc 失败:", repr(e))
