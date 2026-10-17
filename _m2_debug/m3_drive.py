# -*- coding: utf-8 -*-
"""法三 VBA 全自动驱动：把宏贴进 VBE → 保存 .swp → RunMacro2 → 回收结果。

用法：
    python m3_drive.py install                只装宏（不运行）
    python m3_drive.py punch                  装宏 + 跑上模
    python m3_drive.py cavity                 装宏 + 跑下模
    python m3_drive.py run punch              只运行（宏已在 .swp 里）
    python m3_drive.py install --src X --out Y ...

为什么要 SendKeys 贴代码：RunMacro2 只能跑 .swp 里的宏，
而 .swp 是 OLE 复合文档，没法从外部直接写 VBA 源码。
"""
import os
import sys
import time
import shutil

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pythoncom
import win32clipboard
import win32com.client as win32
from win32com.client import VARIANT

ROOT = r"C:\Users\何雨卓\Desktop\SF\S2"
MACRO = os.path.join(ROOT, "mold_method3_macro.txt")
SWP = os.path.join(ROOT, "mold_method3.swp")
OUTDIR = os.path.join(ROOT, "_m2_debug", "m3_out")
TMP = os.environ.get("TEMP", r"C:\Windows\Temp")
CFG = os.path.join(TMP, "mold3_cfg.txt")
RES = os.path.join(TMP, "mold3_result.txt")
LOG = os.path.join(TMP, "mold3_log.txt")
VBE_TITLE = "Microsoft Visual Basic for Applications - mold_method3"


def find_vbe_title():
    """动态找 VBA 编辑器窗口标题。

    ★不能写死标题★：VBE 的标题会变
        "Microsoft Visual Basic for Applications"                ← 刚打开、没选中工程
        "Microsoft Visual Basic for Applications - mold_method3"  ← 选中了工程
        "Microsoft Visual Basic for Applications - mold_method3 [中断]"  ← 处在中断态
    写死就会 AppActivate 返回 False，白白以为"VBE 没开"。
    """
    try:
        import win32gui
    except Exception:
        return None
    found = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h):
            t = win32gui.GetWindowText(h)
            if "Visual Basic for Applications" in t:
                found.append((h, t))
        return True

    win32gui.EnumWindows(cb, None)
    if not found:
        return None
    print("  找到 VBE 窗口:", [t for _, t in found])
    # 优先带工程名的（说明代码窗口有内容）
    for h, t in found:
        if " - " in t:
            return t
    return found[0][1]

DEFAULTS = {
    "punch": {
        "MODE": "PUNCH",
        "SRC": r"C:\Temp\m3test\upper_sewn2rom.stp",
        "OUT": r"C:\Temp\m3test\2ROM_PUNCH_VBA.stp",
        # ★投影轮廓点表（由 m3_outer.py 用 OCC 算好）★
        "POLY": r"C:\Temp\m3test\2ROM_upper_poly.txt",
    },
    "cavity": {
        "MODE": "CAVITY",
        "SRC": r"C:\Temp\m3test\lower_sewn2rom.stp",
        "OUT": r"C:\Temp\m3test\2ROM_CAVITY_VBA.stp",
        "POLY": r"C:\Temp\m3test\2ROM_lower_poly.txt",
    },
}


def set_clipboard(text):
    pythoncom.CoInitialize()
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def install():
    src = open(MACRO, encoding="utf-8").read()
    print("[install] 宏文件 %s  %d 字符" % (MACRO, len(src)))
    for i, ln in enumerate(src.splitlines(), 1):
        s = ln.strip()
        if s.lower().startswith("attribute vb_name"):
            print("  !! 第 %d 行是真正的 Attribute VB_Name 语句 —— 会导致编译错误" % i)
            return False
    if "Attribute VB_Name" in src:
        print("  (注释里提到 Attribute VB_Name，无害)")
    set_clipboard(src)
    shell = win32.Dispatch("WScript.Shell")
    title = find_vbe_title()
    if not title:
        print("  !! 没找到 VBA 编辑器窗口；请先按 Alt+F11 打开")
        return False
    ok = shell.AppActivate(title)
    print("  AppActivate(%r) = %s" % (title, ok))
    if not ok:
        print("  !! AppActivate 失败")
        return False
    time.sleep(0.8)
    shell.SendKeys("{F7}")          # 确保焦点在代码窗口（视图→代码）
    time.sleep(0.8)
    print("  F7 后标题 =", find_vbe_title())
    shell.SendKeys("^a")            # 全选该模块
    time.sleep(0.5)
    shell.SendKeys("^v")            # 粘贴
    time.sleep(2.5)
    shell.SendKeys("^s")            # 保存 .swp
    time.sleep(2.5)
    print("  已粘贴并保存，.swp 时间戳 =",
          time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(SWP))))
    return True


def show_methods(sw):
    print("[methods] GetMacroMethods(filter=3)")
    entry = None
    try:
        ms = sw.GetMacroMethods(SWP, 3)
        ms = list(ms) if ms is not None and isinstance(ms, (tuple, list)) else ([ms] if ms else [])
        print("  共 %d 个：" % len(ms))
        for m in ms:
            print("    ", m)
        for m in ms:
            if m.lower().endswith(".runm3"):
                entry = m
    except Exception as e:
        print("  失败:", repr(e))
    print("  入口 =", entry)
    return entry


def connect(timeout=180):
    """连 SW；没启动就启动。★冷启动后必须等它稳下来★
    实测：Dispatch 之后立刻调用 API 会把 SW 打崩（远程过程调用失败）。
    """
    t0 = time.time()
    launched = False
    while time.time() - t0 < timeout:
        try:
            sw = win32.Dispatch("SldWorks.Application")
            rev = sw.RevisionNumber
            if not launched:
                launched = True
                # 首次 Dispatch 会拉起进程，多给点时间
                print("[sw] 首次连接（可能刚启动），等 20s 让它稳下来 …")
                time.sleep(20)
            sw2 = win32.Dispatch("SldWorks.Application")
            print("[sw] revision =", sw2.RevisionNumber)
            return sw2
        except Exception as e:
            print("  等待 SW 就绪 … %r" % (e,))
            time.sleep(5)
    return None


def run(kind, extra=None):
    cfg = dict(DEFAULTS.get(kind, {}))
    if extra:
        cfg.update(extra)
    if not os.path.exists(cfg["SRC"]):
        print("!! 输入不存在:", cfg["SRC"])
    print("[cfg] 写", CFG)
    if os.path.exists(RES):
        os.remove(RES)
    if os.path.exists(LOG):
        os.remove(LOG)
    with open(CFG, "w", encoding="mbcs", errors="replace") as f:
        for k, v in cfg.items():
            f.write("%s=%s\n" % (k, v))
    print("   ", cfg)

    sw = connect()
    if sw is None:
        print("!! 连不上 SolidWorks")
        return False
    entry = show_methods(sw)
    if not entry:
        print("!! 找不到 RunM3 入口 —— 宏没装好")
        return False
    module, proc = entry.rsplit(".", 1)

    print("[run] RunMacro2(%s, %s, %s)" % (os.path.basename(SWP), module, proc))
    t = time.time()
    err = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    try:
        ok = sw.RunMacro2(SWP, module, proc, 0, err)
        print("  返回 =", ok, " errors =", err.value, " 用时 %.1fs" % (time.time() - t))
    except Exception as e:
        print("  异常:", repr(e))

    time.sleep(1)
    print("[result]")
    if os.path.exists(RES):
        print("   ", open(RES, encoding="utf-8", errors="replace").read().strip())
    else:
        print("    未生成")
    print("[log] " + LOG)
    if os.path.exists(LOG):
        print(open(LOG, encoding="mbcs", errors="replace").read())
    out = cfg["OUT"]
    print("[out]", out, "存在 =", os.path.exists(out),
          os.path.getsize(out) if os.path.exists(out) else "")
    if os.path.exists(out):
        os.makedirs(OUTDIR, exist_ok=True)
        dst = os.path.join(OUTDIR, os.path.basename(out))
        shutil.copy2(out, dst)
        print("    已拷回:", dst)
    return True


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0].lower()
    kind = args[1].lower() if len(args) > 1 and not args[1].startswith("-") else "punch"
    # 支持 KEY=VALUE 覆盖配置
    extra = {}
    for a in args[1:]:
        if "=" in a:
            k, v = a.split("=", 1)
            extra[k.upper()] = v
    if cmd == "install":
        install()
    elif cmd == "run":
        run(kind, extra)
    elif cmd in ("punch", "cavity"):
        if install():
            time.sleep(1)
            run(cmd, extra)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
