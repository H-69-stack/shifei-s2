# -*- coding: utf-8 -*-
"""第0步验证：Python 能否通过 COM 驱动本机 SolidWorks 2024。"""
import sys
import time

try:
    import win32com.client as win32
    print("[1] import win32com.client  OK")
except Exception as e:
    print("[1] import 失败:", e)
    sys.exit(1)

t0 = time.time()
try:
    sw = win32.Dispatch("SldWorks.Application")
    print(f"[2] Dispatch('SldWorks.Application') OK  ({time.time()-t0:.1f}s)")
except Exception as e:
    print("[2] Dispatch 失败:", repr(e))
    sys.exit(2)

try:
    print("[3] RevisionNumber =", sw.RevisionNumber())
except Exception as e:
    print("[3] RevisionNumber 失败:", repr(e))

for attr in ("Visible",):
    try:
        print(f"[4] {attr} =", getattr(sw, attr))
    except Exception as e:
        print(f"[4] {attr} 读取失败:", repr(e))

# 关键 API 是否可调用（方法3 要用）
for name in ("LoadFile4", "OpenDoc6", "NewDocument", "ActiveDoc", "GetDocuments",
             "Modeler", "SetUserPreferenceToggle"):
    print(f"[5] sw.{name:24s} ->", "有" if hasattr(sw, name) else "无")

try:
    docs = sw.GetDocuments()
    n = 0 if docs is None else len(docs)
    print(f"[6] 当前打开文档数 = {n}")
except Exception as e:
    print("[6] GetDocuments 失败:", repr(e))

print(f"[7] 总耗时 {time.time()-t0:.1f}s  —— 结论：COM 通道 {'可用' if True else '不可用'}")
