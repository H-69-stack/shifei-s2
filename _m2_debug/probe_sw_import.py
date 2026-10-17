# -*- coding: utf-8 -*-
"""探测：Python COM 导入 STEP 到底哪种写法能用。"""
import sys
import os

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pythoncom
import win32com.client as win32
from win32com.client import VARIANT

P = r"C:\Users\何雨卓\Desktop\SF\S2\web_box_generator\uploads\543e0fdcfa2e_2ROM31-6061 ---(1).stp"
print("文件存在:", os.path.exists(P))
print("文件大小:", os.path.getsize(P))

sw = win32.Dispatch("SldWorks.Application")
print("SW =", sw.RevisionNumber)


def byref_i4(v=0):
    return VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, v)


def try_call(label, fn):
    try:
        r = fn()
        ok = r is not None
        title = ""
        if ok:
            try:
                title = r.GetTitle
            except Exception:
                title = "<no title>"
        print(f"  {label:52s} -> {'OK' if ok else 'None'}  {title}")
        return r
    except Exception as e:
        print(f"  {label:52s} -> FAIL {e!r}")
        return None


m = None
m = m or try_call("OpenDoc6(path,1,1,'',byref,byref)",
                  lambda: sw.OpenDoc6(P, 1, 1, "", byref_i4(), byref_i4()))
m = m or try_call("OpenDoc6(path,1,0,'',byref,byref)",
                  lambda: sw.OpenDoc6(P, 1, 0, "", byref_i4(), byref_i4()))
m = m or try_call("LoadFile4(path,'',None)",
                  lambda: sw.LoadFile4(P, "", None))
m = m or try_call("LoadFile4(path,'r',None)",
                  lambda: sw.LoadFile4(P, "r", None))
m = m or try_call("OpenDoc(path,1)",
                  lambda: sw.OpenDoc(P, 1))
m = m or try_call("LoadFile(path,'')",
                  lambda: sw.LoadFile(P, ""))

if m is not None:
    try:
        print("\n体数量:", len(m.GetBodies2(-1, True) or []))
    except Exception as e:
        print("GetBodies2 失败:", repr(e))
    try:
        print("包围盒:", m.GetBox(0))
    except Exception as e:
        print("GetBox 失败:", repr(e))
else:
    print("\n全部写法都失败")
