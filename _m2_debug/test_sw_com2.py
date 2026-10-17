# -*- coding: utf-8 -*-
"""第0步(续)：用 EnsureDispatch 拿到早期绑定类型库，并确认方法3 需要的关键 API。"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import win32com.client as win32

t0 = time.time()
try:
    sw = win32com.client.gencache.EnsureDispatch("SldWorks.Application")
    print(f"[1] EnsureDispatch OK  ({time.time()-t0:.1f}s)  type={type(sw)}")
except Exception as e:
    print("[1] EnsureDispatch 失败:", repr(e))
    sw = win32.Dispatch("SldWorks.Application")
    print("    回退到 Dispatch (late binding)")

for attr in ("RevisionNumber", "Visible"):
    try:
        v = getattr(sw, attr)
        print(f"[2] sw.{attr} = {v!r}")
    except Exception as e:
        print(f"[2] sw.{attr} 失败: {e!r}")

print("\n[3] 方法3 必需 API 可用性：")
for name, kind in (("OpenDoc6", "method"), ("LoadFile4", "method"),
                   ("NewDocument", "method"), ("ActiveDoc", "prop"),
                   ("GetDocuments", "prop"), ("Modeler", "prop"),
                   ("CloseDoc", "method"), ("GetUserPreferenceToggle", "method"),
                   ("SetUserPreferenceToggle", "method")):
    try:
        v = getattr(sw, name)
        callable_ = callable(v)
        print(f"    sw.{name:26s} 取到  callable={callable_}  ({type(v).__name__})")
    except Exception as e:
        print(f"    sw.{name:26s} 取不到 -> {e!r}")

print("\n[4] 当前文档：")
try:
    doc = sw.ActiveDoc
    print("    ActiveDoc =", doc.GetTitle() if doc else None)
except Exception as e:
    print("    ActiveDoc 失败:", repr(e))
print(f"\n[5] 总耗时 {time.time()-t0:.1f}s")
