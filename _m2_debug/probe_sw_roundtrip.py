# -*- coding: utf-8 -*-
"""判别：SolidWorks 自己建的实体 导出 STEP 再导入 —— 能通说明是 OCP 写的 STEP 有问题；
不能通说明这台 SW 的中性格式翻译器整体不可用。

顺带验证方法3 需要的：草图 + 拉伸凸台 + 体布尔。
注意：SW API 内部长度单位是【米】。
"""
import sys, os, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pythoncom
import win32com.client as win32
from win32com.client import VARIANT

TPL = r"C:\ProgramData\SolidWorks\SOLIDWORKS 2024\templates\gb_part.prtdot"
OUT = r"C:\Temp\m3test\swmade.stp"
os.makedirs(os.path.dirname(OUT), exist_ok=True)
if os.path.exists(OUT):
    os.remove(OUT)

sw = win32.Dispatch("SldWorks.Application")
print("SW", sw.RevisionNumber)

# 关闭上次遗留的测试文档（不碰用户的）
for t in [x.GetTitle for x in (sw.GetDocuments or [])]:
    if t.startswith("零件") and t != "零件1":
        try:
            sw.CloseDoc(t)
        except Exception:
            pass

print("\n[1] 新建零件")
model = sw.NewDocument(TPL, 0, 0, 0)
print("   ->", model)
if model is None:
    print("   NewDocument 失败，终止"); sys.exit(1)

print("\n[2] 选基准面 + 画矩形草图")
ok_sel = False
for pname in ("前视基准面", "Front Plane", "上视基准面"):
    try:
        if model.Extension.SelectByID2(pname, "PLANE", 0, 0, 0, False, 0, None, 0):
            print("   选中基准面:", pname); ok_sel = True; break
    except Exception as e:
        print("   SelectByID2 异常:", repr(e))
if not ok_sel:
    print("   ! 选不中基准面")

sm = model.SketchManager
model.SketchManager.InsertSketch(True)
sm.CreateCornerRectangle(0, 0, 0, 0.10, 0.06, 0)   # 100mm x 60mm
model.SketchManager.InsertSketch(True)
print("   草图完成")

print("\n[3] 拉伸凸台 50mm")
fm = model.FeatureManager
feat = None
# FeatureExtrusion3(Sd,Flip,Dir,T1,T2,D1,D2,Dchk1,Dchk2,Ddir1,Ddir2,Dang1,Dang2,
#   OffsetReverse1,OffsetReverse2,TranslateSurface1,TranslateSurface2,Merge,
#   UseFeatScope,UseAutoSelect,T0,StartOffset,FlipStartOffset,OptimizeGeometry)
try:
    feat = fm.FeatureExtrusion3(True, False, False, 0, 0, 0.05, 0.0,
                                False, False, False, False, 0.0, 0.0,
                                False, False, False, False, True,
                                True, True, 0, 0.0, False, True)
    print("   FeatureExtrusion3 ->", feat)
except Exception as e:
    print("   FeatureExtrusion3 异常:", repr(e))

print("\n[4] 体数量")
try:
    bs = model.GetBodies2(-1, True)
    print("   =>", len(bs) if bs else 0)
except Exception as e:
    print("   GetBodies2 异常:", repr(e))

print("\n[5] 另存为 STEP")
try:
    r = model.Extension.SaveAs(OUT, 0, 1, None, None, 0, 0)
    print("   SaveAs ->", r, " 文件存在:", os.path.exists(OUT),
          os.path.getsize(OUT) if os.path.exists(OUT) else "")
except Exception as e:
    print("   SaveAs 异常:", repr(e))

title = model.GetTitle
try:
    sw.CloseDoc(title)
    print("   已关闭", title)
except Exception as e:
    print("   关闭失败:", repr(e))

print("\n[6] 重新导入 SW 自己导出的 STEP")
if os.path.exists(OUT):
    err = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warn = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    try:
        m2 = sw.OpenDoc6(OUT, 1, 1, "", err, warn)
        print("   errors =", err.value, " 返回 =", "ModelDoc" if m2 else "None")
    except Exception as e:
        print("   异常:", repr(e))
    time.sleep(1.0)
    print("   文档:", [x.GetTitle for x in (sw.GetDocuments or [])])
else:
    print("   STEP 未生成，无法测试")
