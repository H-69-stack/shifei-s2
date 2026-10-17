# -*- coding: utf-8 -*-
"""验证「Python 通过 COM 对 SolidWorks 模型做实际操作」的最后一环：体级布尔。

做一个最小闭环：新建零件 → 拉伸两个分离实体 → Operations2 布尔减 → 导出。
"""
import sys, os, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pythoncom
import win32com.client as win32
from win32com.client import VARIANT

NULL = VARIANT(pythoncom.VT_DISPATCH, None)
TPL = r"C:\ProgramData\SolidWorks\SOLIDWORKS 2024\templates\gb_part.prtdot"
SW_BODY_OP_CUT = 15902
SW_BODY_OP_ADD = 15903
OUT = r"C:\Temp\m3test\bool_test.stp"

sw = win32.Dispatch("SldWorks.Application")
time.sleep(20)                      # ★必须等它真正就绪，否则会崩


def docs():
    d = sw.GetDocuments
    return list(d) if d else []


def solids(m):
    bs = m.GetBodies2(0, True)
    if bs is None:
        return []
    return list(bs) if isinstance(bs, (tuple, list)) else [bs]


def zr(b):
    v = list(b.GetBodyBox())
    return v[2], v[5]


print("SW", sw.RevisionNumber, " 文档:", [d.GetTitle for d in docs()])

print("\n[1] 新建零件")
m = sw.NewDocument(TPL, 0, 0, 0)
if m is None:
    print("   失败"); sys.exit(1)
time.sleep(0.5)
print("   ->", m.GetTitle)


def rect_extrude(x1, y1, x2, y2, up, dn, merge):
    m.ClearSelection2(True)
    m.Extension.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, NULL, 0)
    m.SketchManager.InsertSketch(True)
    m.SketchManager.CreateCornerRectangle(x1, y1, 0, x2, y2, 0)
    m.SketchManager.InsertSketch(True)
    if dn > 0:
        return m.FeatureManager.FeatureExtrusion3(
            False, False, False, 0, 0, up, dn, False, False, False, False, 0.0, 0.0,
            False, False, False, False, merge, True, True, 0, 0.0, False)
    return m.FeatureManager.FeatureExtrusion3(
        True, False, False, 0, 0, up, 0.0, False, False, False, False, 0.0, 0.0,
        False, False, False, False, merge, True, True, 0, 0.0, False)


print("\n[2] 拉伸体A (60x60x50mm, Merge=False)")
a = rect_extrude(-0.03, -0.03, 0.03, 0.03, 0.05, 0.0, False)
print("   特征:", "OK" if a else "None")
print("\n[3] 拉伸体B (30x30x80mm 与 A 相交, Merge=False)")
b = rect_extrude(-0.015, -0.015, 0.015, 0.015, 0.08, 0.0, False)
print("   特征:", "OK" if b else "None")

bs = solids(m)
print("\n[4] 实体数 =", len(bs))
for i, x in enumerate(bs):
    print("   body[%d] z[%.4f,%.4f] name=%s" % (i, zr(x)[0], zr(x)[1], x.Name))

print("\n[5] Operations2 布尔减")
if len(bs) >= 2:
    target, tool = bs[0], bs[1]
    res = None
    for tag, args in (("Error=0(普通int)", (SW_BODY_OP_CUT, tool, 0)),
                      ("Error=VARIANT byref", (SW_BODY_OP_CUT, tool,
                                               VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0))),
                      ("Error 省略", (SW_BODY_OP_CUT, tool))):
        try:
            res = target.Operations2(*args)
            print("   %-22s -> %s" % (tag, "Body" if res else "None"))
            if res:
                break
        except Exception as e:
            print("   %-22s 异常 %s" % (tag, str(e)[:90]))
    time.sleep(0.3)
    if res:
        v = list(res.GetBodyBox())
        print("   ★ 返回结果体 bbox: x[%.4f,%.4f] y[%.4f,%.4f] z[%.4f,%.4f]" % (
            v[0], v[3], v[1], v[4], v[2], v[5]))
        try:
            print("   结果体 = %d 个 solid" % len(
                list(res.GetFaces()) or []))
        except Exception:
            pass
    bs2 = solids(m)
    print("   模型内实体数 =", len(bs2))
    for i, x in enumerate(bs2):
        v = list(x.GetBodyBox())
        print("   body[%d] x[%.4f,%.4f] y[%.4f,%.4f] z[%.4f,%.4f]" % (
            i, v[0], v[3], v[1], v[4], v[2], v[5]))
else:
    print("   ! 实体不足 2 个，跳过")

print("\n[6] 导出 STEP")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
if os.path.exists(OUT):
    os.remove(OUT)
verr = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
vwarn = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
try:
    m.Extension.SaveAs(OUT, 0, 1, NULL, verr, vwarn)
    print("   OK", os.path.exists(OUT), os.path.getsize(OUT) if os.path.exists(OUT) else "")
except Exception as e:
    print("   异常:", repr(e))

try:
    sw.CloseDoc(m.GetTitle)
    print("   已关闭", m.GetTitle)
except Exception:
    pass
