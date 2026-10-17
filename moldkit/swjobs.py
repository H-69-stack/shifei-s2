# -*- coding: utf-8 -*-
"""SolidWorks 作业驱动层。

把「在 SolidWorks 里怎么生成模具」抽象成一份【操作脚本】(job)：

    Job().import_step(src, "srf") \
         .plane_z(72, "ptop") \
         .sketch_poly("ptop", poly_file, "skp") \
         .boss_upto("skp", "srf", "ptop", "DOWN", "punch") \
         .check() \
         .export(out)

Python 只负责"写脚本"，SolidWorks 侧的 `mold_sw_engine.txt` 逐行执行。
★新增一种模具做法 = 只写 Python，不用改 VBA、不用重开 VBE。★

用法：
    eng = SwEngine()
    eng.install()                      # 把引擎贴进 .swp（需要 VBE 已打开）
    ok, log = eng.run_job(job)
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

JOB = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "moldsv_job.txt")
RES = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "moldsv_result.txt")
LOG = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "moldsv_log.txt")

# ★进程级互斥★
#   1) JOB/RES/LOG 是固定路径（VBA 引擎按名读取，不能改），两个 job 同时跑
#      会互相覆盖作业/结果文件 → 结果错配。SolidWorks 本身是单实例，
#      RunMacro2 天然串行，加锁只是把"隐式串行"变成"显式正确"。
#   2) install() 要清空剪贴板 + 模拟按键，与用户正在进行的复制粘贴/输入
#      互斥，避免按键打到错误窗口。
_JOB_LOCK = threading.Lock()
_INSTALL_LOCK = threading.Lock()

DEFAULT_SWP = os.path.join(ROOT, "mold_method3.swp")
DEFAULT_MODULE = "mold_method31"
DEFAULT_ENTRY = "RunMoldSV"
ENGINE_SRC = os.path.join(ROOT, "mold_sw_engine.txt")


# ---------------------------------------------------------------------------
# 操作脚本
# ---------------------------------------------------------------------------
class Job:
    """一份 SolidWorks 操作脚本。每个方法返回一条 op。"""

    def __init__(self):
        self.ops = []

    # --- 底层 ---
    def raw(self, opname, **kw):
        # ★参数名不要叫 op★：Job.combine 要传 kwarg "op"，会撞名
        parts = ["OP=%s" % opname]
        for k, v in kw.items():
            if v is None:
                continue
            parts.append("%s=%s" % (k.upper(), v))
        self.ops.append("|".join(parts))
        return self

    def text(self):
        return "\r\n".join(self.ops) + "\r\n"

    # --- 文档 ---
    def new(self):
        return self.raw("NEW")

    def import_step(self, src, name="srf"):
        return self.raw("IMPORT", src=src, name=name)

    def use(self, name):
        return self.raw("USE", name=name)

    # --- 参考几何 ---
    def plane_z(self, z_mm, name):
        return self.raw("PLANE_Z", z=z_mm, name=name)

    def plane_auto(self, name="sysz"):
        """按法向自动找系统基准面（★本机 gb 模板里"前视基准面"才是 Z 法向★）。"""
        return self.raw("PLANE_AUTO", name=name)

    # --- 草图 ---
    def sketch_poly(self, plane, poly_file, name):
        return self.raw("SKETCH_POLY", plane=plane, file=poly_file, name=name)

    def sketch_rect(self, plane, x1, y1, x2, y2, name):
        return self.raw("SKETCH_RECT", plane=plane, x1=x1, y1=y1, x2=x2, y2=y2, name=name)

    # --- 特征 ---
    def boss_blind(self, sketch, up_mm, dn_mm, name, merge=True):
        return self.raw("BOSS_BLIND", sketch=sketch, up=up_mm, dn=dn_mm, name=name,
                        merge=(1 if merge else 0))

    def boss_upto(self, sketch, target, plane, dir_, name):
        return self.raw("BOSS_UPTO", sketch=sketch, target=target, plane=plane,
                        dir=dir_, name=name)

    def cut_upto(self, sketch, target, plane, dir_, name):
        return self.raw("CUT_UPTO", sketch=sketch, target=target, plane=plane,
                        dir=dir_, name=name)

    def combine(self, op, main, tool, name="comb"):
        # ★组合类型必须用 TYPE 这个键★：作业行本身以 OP=COMBINE 开头，
        #   如果再用 OP=CUT 就会被 KV(a,"OP") 取到 "COMBINE"。
        return self.raw("COMBINE", type=op, main=main, tool=tool, name=name)

    def delete_surfaces(self):
        return self.raw("DELETE_SURFACES")

    def keep_bodies(self, idx):
        return self.raw("KEEP_BODIES", idx=idx)

    # --- 输出 ---
    def check(self):
        return self.raw("CHECK")

    def log(self, msg):
        return self.raw("LOG", msg=msg)

    def export(self, out):
        return self.raw("EXPORT", out=out)

    def __len__(self):
        return len(self.ops)

    def __repr__(self):
        return "Job(%d ops)" % len(self.ops)


# ---------------------------------------------------------------------------
# 结果
# ---------------------------------------------------------------------------
class JobResult:
    def __init__(self, ok, result_line, log_text):
        self.ok = ok
        self.result = result_line
        self.log = log_text

    @property
    def error(self):
        if self.ok:
            return None
        parts = (self.result or "").split("|")
        return parts[-1] if parts else "unknown"

    def solids(self):
        """从日志里抽出 CHECK 报的实体信息：[(idx, name, vol_m3, bbox_mm), ...]"""
        import re
        out = []
        for m in re.finditer(
                r"实体(\d+) (\S+) 体积=([\d.eE+-]+) m\^3 bbox "
                r"x\[([-\d.]+),([-\d.]+)\] y\[([-\d.]+),([-\d.]+)\] z\[([-\d.]+),([-\d.]+)\]",
                self.log):
            g = m.groups()
            out.append({
                "idx": int(g[0]), "name": g[1], "volume_m3": float(g[2]),
                "bbox": [float(g[3]), float(g[5]), float(g[7]),
                         float(g[4]), float(g[6]), float(g[8])],
            })
        return out

    def __repr__(self):
        return "JobResult(ok=%s, %r)" % (self.ok, self.result)


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------
class SwEngine:
    """SolidWorks 侧的执行引擎（安装宏 / 跑作业 / 收日志）。"""

    def __init__(self, swp=None, module=None, entry=None, engine_src=None):
        self.swp = swp or DEFAULT_SWP
        self.module = module or DEFAULT_MODULE
        self.entry = entry or DEFAULT_ENTRY
        self.engine_src = engine_src or ENGINE_SRC
        # ★COM 连接必须【每线程一份】★
        #   Flask 的每个请求跑在各自线程里，把主线程创建的 COM 指针拿到别的线程用
        #   会失败（表现：连得上 SW 但 GetMacroMethods 报"宏没有入口"）。
        import threading
        self._local = threading.local()

    # --- 连接 ---
    def attach(self):
        """只挂到【已经在运行】的 SolidWorks，绝不把它拉起来。

        为什么必须有：Dispatch("SldWorks.Application") 在 SW 没开时会
        【把 SW 启动起来】，所以界面上会出现"我 SW 没开，方法四/五却可用"。
        GetActiveObject 只在已有实例时成功，没有就抛异常，正是要的行为。
        """
        import win32com.client as win32
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        try:
            sw = win32.GetActiveObject("SldWorks.Application")
            _ = sw.RevisionNumber
        except Exception:
            return None
        self._local.sw = sw
        return sw

    def is_running(self):
        try:
            return self.attach() is not None
        except Exception:
            return False

    def connect(self, timeout=180, settle=20):
        """连上 SolidWorks（没启动就启动）。★冷启动要等它稳下来★

        ★每个线程都要 CoInitialize★：Flask 是多线程的，工作线程里不初始化 COM，
        pywin32 会直接失败（表现为"宏入口找不到"，其实是连都没连上）。
        """
        import win32com.client as win32
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        sw0 = getattr(self._local, "sw", None)
        if sw0 is not None:
            return sw0
        t0 = time.time()
        launched = False
        while time.time() - t0 < timeout:
            try:
                sw = win32.Dispatch("SldWorks.Application")
                rev = sw.RevisionNumber
                if not launched:
                    launched = True
                    print("[SwEngine] 首次连接（可能刚启动），等 %ds 让它稳下来…" % settle)
                    time.sleep(settle)
                    sw = win32.Dispatch("SldWorks.Application")
                    rev = sw.RevisionNumber
                self._local.sw = sw
                return sw
            except Exception as e:
                print("  等待 SW 就绪 … %r" % (e,))
                time.sleep(5)
        return None

    @property
    def sw(self):
        sw = getattr(self._local, "sw", None)
        if sw is not None:
            return sw
        return self.connect()

    def available(self):
        try:
            return self.sw is not None
        except Exception:
            return False

    # --- 宏安装 ---
    def find_vbe_title(self):
        """动态找 VBA 编辑器窗口标题（标题会变，不能写死）。"""
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
        for _h, t in found:
            if " - " in t:
                return t
        return found[0][1]

    def _force_load_project(self):
        """想办法让 SolidWorks 把 .swp 工程载入 VBA 编辑器。

        ★为什么需要★：VBE 有时是"空"的（窗口标题里没有工程名），
        这时 Ctrl+A / Ctrl+V 会打到空处，Ctrl+S 也不会写文件。
        而我们手里唯一的"程序化入口"是 RunMacro2 —— 它必须先能在 .swp 里
        找到入口，才能把工程载进来。

        做法：找 .swp 里【已存在】的零参入口（优先旧宏的 RunM3），
        写一份"必然立刻失败"的配置（输入文件指向不存在的路径），
        调一次让它快速报错退出 —— 工程就留在 VBE 里了。
        """
        ms = self.list_methods()
        cand = [m for m in ms if m.lower().endswith(".runm3")]
        if not cand:
            cand = [m for m in ms if m.lower().endswith("." + self.entry.lower())]
        if not cand:
            print("  force-load: .swp 里没有可用的零参入口")
            return False
        module, proc = cand[0].rsplit(".", 1)
        try:
            cfg = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "mold3_cfg.txt")
            with open(cfg, "w", encoding="mbcs", errors="replace") as f:
                f.write("MODE=PUNCH\nSRC=C:\\__force_load_nonexistent__.stp\n"
                        "OUT=C:\\__force_load_nonexistent_out__.stp\n")
        except Exception:
            pass
        import pythoncom
        from win32com.client import VARIANT
        err = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        try:
            self.sw.RunMacro2(self.swp, module, proc, 0, err)
        except Exception as e:
            print("  force-load RunMacro2 异常: %r" % (e,))
        time.sleep(1.5)
        print("  force-load 后 VBE = %s" % self.find_vbe_title())
        return True

    def install(self, src=None, save_wait=3.0, verify=True):
        """把引擎源码贴进 .swp 的模块并保存。★需要 VBE 已加载该工程★

        ★必须验证★（血泪教训）：
        这台机器上 VBE 有时没有加载工程，此时 Ctrl+A/Ctrl+V 打到空处、
        Ctrl+S 也不写文件，但 AppActivate 仍然返回 True。
        早期版本只看 AppActivate 就 return True，于是连续几次都"安装成功"，
        而 .swp 的 mtime 一直停在几小时前 —— 内容根本没换。
        现在：粘贴后必须【mtime 变了】且【入口存在】才算成功，否则明确报失败。
        """
        import win32clipboard
        import pythoncom
        import win32com.client as win32

        src = src or self.engine_src
        if not os.path.exists(src):
            print("[install] 找不到引擎源码", src)
            return False
        text = open(src, encoding="utf-8").read()
        for i, ln in enumerate(text.splitlines(), 1):
            if ln.strip().lower().startswith("attribute vb_name"):
                print("[install] !! 第 %d 行是 Attribute VB_Name，会导致编译错误" % i)
                return False
        print("[install] %s  %d 字符" % (os.path.basename(src), len(text)))

        # ★互斥★：安装过程会清空系统剪贴板并向 VBE 窗口模拟 Ctrl+A/V/S，
        #   与用户的复制粘贴/键盘输入互斥；并发安装也会互相打断。
        with _INSTALL_LOCK:
            return self._install_locked(text, save_wait, verify)

    def _install_locked(self, text, save_wait, verify):

        # 0) 工程没加载就先把它载进 VBE
        if not self.has_entry():
            print("[install] .swp 里没有 %s，先尝试让 VBE 加载工程…" % self.entry)
            self._force_load_project()

        pythoncom.CoInitialize()
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()

        shell = win32.Dispatch("WScript.Shell")
        title = self.find_vbe_title()
        if not title:
            print("[install] !! 没找到 VBA 编辑器窗口；请先在 SolidWorks 里按 Alt+F11")
            return False
        if " - " not in title:
            print("[install] ⚠ VBE 标题 '%s' 里没有工程名 —— 工程可能没加载，粘贴会落空" % title)
        if not shell.AppActivate(title):
            print("[install] !! AppActivate 失败")
            return False
        print("[install] VBE = %s" % title)

        time.sleep(0.8)
        shell.SendKeys("{F7}")
        time.sleep(0.8)
        shell.SendKeys("^a")
        time.sleep(0.5)
        shell.SendKeys("^v")
        time.sleep(2.5)
        before = os.path.getmtime(self.swp) if os.path.exists(self.swp) else 0
        shell.SendKeys("^s")
        time.sleep(save_wait)
        after = os.path.getmtime(self.swp) if os.path.exists(self.swp) else 0
        changed = after != before
        entry_ok = self.has_entry() if verify else True
        print("[install] .swp mtime %s；入口 %s = %s" %
              ("已更新" if changed else "未变(说明没写进去)",
               self.entry, "存在" if entry_ok else "不存在"))
        if verify and not (changed and entry_ok):
            print("[install] !! 安装未生效。请在 SolidWorks 里把该宏打开"
                  "（工具 → 宏 → 编辑，选中 mold_method3.swp）后重试；"
                  "或确认 VBE 窗口标题里带 '- mold_method3'。")
            return False
        print("[install] ✅ 安装并验证通过")
        return True

    def list_methods(self):
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        sw = self.sw
        if sw is None:
            return []
        try:
            ms = sw.GetMacroMethods(self.swp, 3)
            ms = list(ms) if isinstance(ms, (tuple, list)) else ([ms] if ms else [])
            return ms
        except Exception as e:
            print("  GetMacroMethods 失败:", repr(e))
            return []

    def has_entry(self, entry=None):
        entry = entry or self.entry
        return any(m.lower().endswith("." + entry.lower()) for m in self.list_methods())

    # --- 跑作业 ---
    def run_job(self, job, quiet=False, wait=1.0):
        """跑一份作业。JOB/RES/LOG 是固定路径，全进程互斥（见文件头说明）。"""
        if isinstance(job, Job):
            job = job.text()
        with _JOB_LOCK:
            return self._run_job_locked(job, quiet, wait)

    def _run_job_locked(self, job_text, quiet, wait):
        import pythoncom
        import win32com.client as win32
        from win32com.client import VARIANT

        with open(JOB, "w", encoding="mbcs", errors="replace") as f:
            f.write(job_text)
        for p in (RES, LOG):
            if os.path.exists(p):
                os.remove(p)

        sw = self.sw
        if sw is None:
            return JobResult(False, "ERR|0|连不上 SolidWorks", "")
        if not self.has_entry():
            return JobResult(False, "ERR|0|宏里没有入口 %s（引擎没装？）" % self.entry, "")

        if not quiet:
            print("[run_job] %d 个操作 -> RunMacro2(%s, %s, %s)" %
                  (len(job_text.splitlines()), os.path.basename(self.swp), self.module, self.entry))
        t = time.time()
        err = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)

        # ★卡死 watchdog★：RunMacro2 是阻塞式 COM 调用，SW 卡死时线程会
        #   永久挂住（daemon 线程 + 任务状态永远 running）。无法安全中断
        #   COM 调用，但至少让用户看到卡了多久，而不是无声等待。
        _stuck = {"flag": False}

        def _watchdog():
            time.sleep(120)
            _stuck["flag"] = True
            print("  [run_job] ⚠ RunMacro2 已超过 120s 未返回，SolidWorks 可能卡死，"
                  "建议检查 SW 界面")

        wd = threading.Thread(target=_watchdog, daemon=True)
        wd.start()
        try:
            sw.RunMacro2(self.swp, self.module, self.entry, 0, err)
            raw = "返回=%s errors=%s 用时=%.1fs" % (True, err.value, time.time() - t)
        except Exception as e:
            if not quiet:
                print("  RunMacro2 异常:", repr(e))
            raw = "异常 %r" % (e,)

        time.sleep(wait)
        res = open(RES, encoding="mbcs", errors="replace").read().strip() if os.path.exists(RES) else ""
        log = open(LOG, encoding="mbcs", errors="replace").read() if os.path.exists(LOG) else ""
        ok = res.startswith("OK")
        if not quiet:
            print("  %s  结果=%s" % (raw, res or "(无)"))
        return JobResult(ok, res, log)


_ENGINE = None


def engine(**kw):
    """进程内共享一个引擎实例（连接 SW 很贵，别反复连）。"""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = SwEngine(**kw)
    return _ENGINE


def availability():
    """给方法用的可用性探测：(ok, 原因)。

    ★不要用 has_entry() 判可用★：那需要真去连 SW 并读宏方法表，
    在界面加载时既慢又容易因为线程 COM 没初始化而误报"不可用"。
    这里只要求宏文件在、SW 连得上；入口不对会在 run_job 里明确报出来。
    """
    eng = engine()
    if not os.path.exists(eng.swp):
        return False, "找不到宏文件 %s" % eng.swp
    if not os.path.exists(eng.engine_src):
        return False, "找不到引擎源码 %s" % eng.engine_src
    # ★不要用 eng.sw★：它在 SW 没开时会把 SW 启动起来，
    #   于是"SW 没开却显示方法可用"。这里只探测【已运行】的实例。
    if not eng.is_running():
        return False, "SolidWorks 没有运行（方法四/五需要它先打开）"
    return True, ""


def ensure_engine(auto_install=True, log=print):
    """确保 SolidWorks 侧引擎可用；缺了就自动重装。返回 (ok, 说明)。

    ★为什么要有这个★
    这台机器上 SolidWorks 的 VBE **不落盘** —— Ctrl+S、Alt+F→S、甚至人手按
    都不写 .swp（文件不是只读，全盘也找不到写到别处的副本）。
    所以引擎只活在 SW 进程内存里，**SW 一重启就没了**。
    与其要求用户记得重装，不如每次跑之前自检一次、缺了就补上。

    `install()` 会自己 force-load 工程（调用 .swp 里已有的旧入口）再粘贴并验证，
    所以即使 .swp 是旧内容也能自动恢复。
    """
    eng = engine()
    try:
        if eng.has_entry():
            return True, "引擎已就绪"
    except Exception as e:
        return False, "连不上 SolidWorks: %s" % e
    if not auto_install:
        return False, "引擎缺失（未自动安装）"
    log("[engine] 宏里没有 %s —— 自动重装引擎…" % eng.entry)
    ok = False
    try:
        ok = eng.install()
    except Exception as e:
        log("[engine] 重装抛异常: %r" % (e,))
    try:
        if eng.has_entry():
            return True, ("引擎已自动重装" if ok else
                          "引擎已重装（.swp 未落盘，仅 SolidWorks 内存有效）")
    except Exception:
        pass
    return False, ("引擎重装失败。请在 SolidWorks 里用「工具 → 宏 → 编辑」"
                   "打开 mold_method3.swp 让 VBE 加载工程后重试")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    e = SwEngine()
    print("引擎源码:", e.engine_src, os.path.exists(e.engine_src))
    print("宏文件  :", e.swp, os.path.exists(e.swp))
    if e.available():
        print("SW 已连接, 宏方法数 =", len(e.list_methods()))
        print("入口 %s 存在 = %s" % (e.entry, e.has_entry()))
