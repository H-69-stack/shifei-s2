# -*- coding: utf-8 -*-
"""驱动用户已建好的 VBA 宏：写参数 → RunMacro2 → 读结果。

宏文件：C:\\Users\\何雨卓\\Desktop\\SF\\S2\\mold_method3.swp
模块名：mold_method31（SW 按文件名自动命名）
参数：  %TEMP%\\mold3_cfg.txt      结果：%TEMP%\\mold3_result.txt
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pythoncom
import win32com.client as win32
from win32com.client import VARIANT

SWP = r"C:\Users\何雨卓\Desktop\SF\S2\mold_method3.swp"
OUTDIR = r"C:\Users\何雨卓\Desktop\SF\S2\_m2_debug\m3_out"
CFG = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "mold3_cfg.txt")
RES = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "mold3_result.txt")
os.makedirs(OUTDIR, exist_ok=True)

sw = win32.Dispatch("SldWorks.Application")
print("SW =", sw.RevisionNumber)

# --- 1) 列出宏里所有方法，找到入口过程名 ---
# ★filter 语义（实测）★  1/5 -> 只列入口(Public 无参)过程
#                        2/6 -> 只列内部过程
#                        3/7 -> 两者都列
# 之前用 filter=2 选不到 RunM3，误调带参的 DoPunch -> errors=6。
print("\n[1] 宏内方法（filter=3 全量）")
entry = None
try:
    ms = sw.GetMacroMethods(SWP, 3)
    ms = list(ms) if ms is not None and isinstance(ms, (tuple, list)) else ([ms] if ms else [])
    print("    共 %d 个:" % len(ms))
    for m in ms:
        print("      ", m)
    for pref in ("runm3", "mold3run", "main"):
        for m in ms:
            if m.lower().endswith("." + pref):
                entry = m
                break
        if entry:
            break
    if entry is None and ms:
        entry = ms[0]
except Exception as e:
    print("   失败:", repr(e))
print("    入口过程 =", entry)
if not entry:
    sys.exit(1)
module = entry.rsplit(".", 1)[0] if "." in entry else "mold_method31"
proc = entry.rsplit(".", 1)[1] if "." in entry else entry

# --- 2) 写参数文件（PUNCH，用缝合好的 2ROM 上形变面）---
cfg = {
    "MODE": "PUNCH",
    "SRC": r"C:\Temp\m3test\upper_sewn2rom.stp",
    # ★输出路径必须纯 ASCII★：VBA 的 Open For Input 按【系统 ANSI(GBK)】读文件，
    #   中文路径会被毁（实测 "何雨卓" → "浣曢鍦"）。所以先输出到 C:\Temp，再由
    #   Python 拷回项目目录。
    "OUT": r"C:\Temp\m3test\2ROM_PUNCH_VBA.stp",
    "MARGIN": "5",
    "H1": "0",
    "KEEP": "ABOVE",
}
print("\n[2] 写参数文件(GBK/ANSI):", CFG)
if os.path.exists(RES):
    os.remove(RES)
with open(CFG, "w", encoding="mbcs", errors="replace") as f:
    for k, v in cfg.items():
        f.write("%s=%s\n" % (k, v))
print(open(CFG, encoding="mbcs", errors="replace").read())

# --- 3) 运行宏 ---
print("[3] RunMacro2(%s, %s, %s)" % (os.path.basename(SWP), module, proc))
t = time.time()
err = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
try:
    ok = sw.RunMacro2(SWP, module, proc, 0, err)
    print("    返回 =", ok, " errors =", err.value, " 用时 %.1fs" % (time.time() - t))
except Exception as e:
    print("    异常:", repr(e))

# --- 4) 读结果 ---
time.sleep(1)
print("\n[4] 结果文件:")
if os.path.exists(RES):
    print("   ", open(RES, encoding="utf-8", errors="replace").read().strip())
else:
    print("    未生成（宏可能没跑到写结果那步）")

out = cfg["OUT"]
print("\n[5] 输出 STEP:", out, "存在 =", os.path.exists(out),
      os.path.getsize(out) if os.path.exists(out) else "")
if os.path.exists(out):
    import shutil
    dst = os.path.join(OUTDIR, "2ROM_PUNCH_VBA.stp")
    shutil.copy2(out, dst)
    print("    已拷回:", dst, os.path.getsize(dst), "bytes")
