# -*- coding: utf-8 -*-
"""探测4：找 3D Interconnect 开关 → 关掉 → 多文件导入对比 + 错误码解码。"""
import sys, os, re, glob, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pythoncom
import win32com.client as win32
from win32com.client import VARIANT

ROOT = r"C:\Users\何雨卓\Desktop\SF\S2"

# ---- 从生成的类型库 .py 里解析 name=value（常量不是模块属性，必须解析文件）----
CONST = {}
for f in glob.glob(os.path.join(os.environ["TEMP"], "gen_py", "3.10", "*.py")):
    for line in open(f, encoding="utf-8", errors="replace"):
        m = re.match(r"\s*(sw\w+)\s*=\s*(-?\d+)\s*(#.*)?$", line)
        if m:
            CONST.setdefault(m.group(1), int(m.group(2)))

print("=== 与 3D Interconnect / 导入 相关的开关常量 ===")
for k in sorted(CONST):
    if re.search(r"interconnect|multicad|importstep|enable3d", k, re.I):
        print(f"  {k:52s} = {CONST[k]}")

ERR = {v: k for k, v in CONST.items() if k.startswith("swFileLoad") or
       k in ("swGenericError", "swFileRequiresRepairError")}


def decode(code):
    if not code:
        return "0 (无错误)"
    names = [n for v, n in ERR.items() if v == code]
    if code == 2097152:
        return "2097152 = swFileRequiresRepairError（文件需修复）"
    return f"{code} = " + (", ".join(names) if names else "未知")


sw = win32.Dispatch("SldWorks.Application")
print("\nSW =", sw.RevisionNumber)

TOGGLE = CONST.get("swMultiCAD_Enable3DInterconnect")
print(f"swMultiCAD_Enable3DInterconnect = {TOGGLE}")
if TOGGLE is not None:
    try:
        sw.SetUserPreferenceToggle(TOGGLE, False)
        print("  已关闭 3D Interconnect -> False")
    except Exception as e:
        print("  关闭失败:", repr(e))

FILES = [
    ("2ROM 产品", os.path.join(ROOT, "web_box_generator", "uploads",
                               "543e0fdcfa2e_2ROM31-6061 ---(1).stp")),
    ("2ROM 上形变面", os.path.join(ROOT, "web_box_generator", "uploads",
                                   "925e79889839_2ROM31-6061 ---(1)_PUNCH_CONTACT_FACES.stp")),
    ("法二B产物 cavity", os.path.join(ROOT, "_m2_debug", "final", "cavity.stp")),
    ("法二B产物 punch", os.path.join(ROOT, "_m2_debug", "final", "punch.stp")),
]

print("\n=== 多文件导入对比（每次 OpenDoc6，读 errors）===")
for label, path in FILES:
    if not os.path.exists(path):
        print(f"  {label:20s} 文件不存在，跳过")
        continue
    err = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warn = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    try:
        sw.OpenDoc6(path, 1, 1, "", err, warn)
    except Exception as e:
        print(f"  {label:20s} 调用异常 {e!r}")
        continue
    time.sleep(0.8)
    try:
        d = sw.GetDocuments
        titles = [x.GetTitle for x in d] if d else []
    except Exception:
        titles = ["<?>"]
    print(f"  {label:20s} errors={err.value:<10} {decode(err.value):52s} 文档={titles}")
    # 关闭刚导入的（不动用户原有文档）
    for t in titles:
        if t and t not in ("零件1",) and t.endswith(".stp"):
            try:
                sw.CloseDoc(t)
            except Exception:
                pass
