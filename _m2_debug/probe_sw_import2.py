# -*- coding: utf-8 -*-
"""探测2：OpenDoc6 之后从 ActiveDoc/GetDocuments 取文档，并打印 err/warn。"""
import sys, os, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pythoncom
import win32com.client as win32
from win32com.client import VARIANT

P = r"C:\Users\何雨卓\Desktop\SF\S2\web_box_generator\uploads\543e0fdcfa2e_2ROM31-6061 ---(1).stp"
sw = win32.Dispatch("SldWorks.Application")
print("SW =", sw.RevisionNumber, " Visible =", sw.Visible)


def docs():
    try:
        d = sw.GetDocuments
        return list(d) if d else []
    except Exception as e:
        return [f"<GetDocuments 失败 {e!r}>"]


print("打开前文档数:", len(docs()))

err = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
warn = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
print("\n调用 OpenDoc6(...)")
try:
    r = sw.OpenDoc6(P, 1, 1, "", err, warn)
    print("  返回 =", r)
except Exception as e:
    print("  异常 =", repr(e))
print("  errors =", err.value, " warnings =", warn.value)

time.sleep(1.0)
print("打开后文档数:", len(docs()))
try:
    ad = sw.ActiveDoc
    print("ActiveDoc =", (ad.GetTitle if ad else None))
except Exception as e:
    print("ActiveDoc 失败:", repr(e))

# 逐个列出文档标题
for i, d in enumerate(docs()):
    try:
        print(f"  doc[{i}] title={d.GetTitle} type={d.GetType}")
    except Exception as e:
        print(f"  doc[{i}] {e!r}")

# 备选：SetUserPreferenceToggle 关掉 3D Interconnect / 用 ImportStepData
print("\n-- 尝试 LoadFile4 正确签名（ImportData 传 VARIANT byref object）--")
try:
    idata = VARIANT(pythoncom.VT_DISPATCH, None)
    r = sw.LoadFile4(P, "", idata)
    print("  LoadFile4 ->", r)
except Exception as e:
    print("  LoadFile4 失败:", repr(e))
