# -*- coding: utf-8 -*-
"""方法三端到端编排：形变面 → 上下模，一步跑完。

用法：
    python m3_all.py 2ROM ^
        --upper <上形变面.stp> --lower <下形变面.stp> ^
        [--tag 2ROM] [--skip-fill] [--skip-macro] [--skip-install]

链路（每一步都是独立脚本，可以单独跑）：
    ① m3_fill.py    形变面【补孔】        （OCC）—— 「成形到曲面」要求曲面盖住轮廓，
                                             孔处没曲面可终止，必须补
    ② m3_outer.py   算【投影轮廓点表】     （OCC）—— 形变面投影只有包围盒 76~92%，
                                             矩形轮廓必然失败；且要内缩 0.5mm
    ③ m3_drive.py   在 SolidWorks 里【成形到曲面】生成上/下模   （SW VBA）
    ④ m3_post.py    壳→实体归一化 + 上下模去干涉                 （OCC）
    ⑤ m3_verify.py  干涉检查 + 三视图验证图                      （OCC）
"""
import os
import sys
import subprocess
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = r"C:\Temp\m3test"
OUT = os.path.join(HERE, "m3_out")


def step(n, title):
    print("\n" + "=" * 78)
    print("【第 %s 步】%s" % (n, title))
    print("=" * 78)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    tag = args[0]
    opt = {}
    i = 1
    while i < len(args):
        a = args[i]
        if a.startswith("--"):
            k = a[2:]
            if "=" in k:
                k, v = k.split("=", 1)
                opt[k] = v
            elif i + 1 < len(args) and not args[i + 1].startswith("--"):
                opt[k] = args[i + 1]
                i += 1
            else:
                opt[k] = "1"
        i += 1

    upper = opt.get("upper")
    lower = opt.get("lower")
    if not upper or not lower:
        print("!! 必须给 --upper 和 --lower")
        return
    os.makedirs(TMP, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)

    up_f = os.path.join(TMP, "%s_upper_filled.stp" % tag)
    lo_f = os.path.join(TMP, "%s_lower_filled.stp" % tag)
    up_poly = os.path.join(TMP, "%s_upper_poly.txt" % tag)
    lo_poly = os.path.join(TMP, "%s_lower_poly.txt" % tag)

    import m3_fill
    import m3_outer
    import m3_post

    try:
        # ① 补孔
        step(1, "形变面补孔")
        if "skip-fill" in opt:
            print("  跳过")
        else:
            m3_fill.process(upper, up_f)
            m3_fill.process(lower, lo_f)
        if not os.path.exists(up_f):
            up_f, lo_f = upper, lower

        # ② 投影轮廓
        step(2, "投影轮廓点表")
        m3_outer.process(up_f, "%s_upper" % tag, TMP)
        m3_outer.process(lo_f, "%s_lower" % tag, TMP)

        # ③ SolidWorks VBA 生成
        step(3, "SolidWorks 生成上下模")
        if "skip-macro" in opt:
            print("  跳过")
        else:
            import m3_drive
            if "skip-install" not in opt:
                if not m3_drive.install():
                    print("!! 宏没装上（VBE 没开？）—— 后续用现有 .swp 继续")
            m3_drive.run("punch", {
                "MODE": "PUNCH", "SRC": up_f,
                "OUT": os.path.join(TMP, "%s_PUNCH_VBA.stp" % tag),
                "POLY": up_poly,
            })
            m3_drive.run("cavity", {
                "MODE": "CAVITY", "SRC": lo_f,
                "OUT": os.path.join(TMP, "%s_CAVITY_VBA.stp" % tag),
                "POLY": lo_poly,
            })

        # ④ 归一化 + 去干涉
        step(4, "壳→实体归一化 + 去干涉")
        import shutil
        pv = os.path.join(TMP, "%s_PUNCH_VBA.stp" % tag)
        cv = os.path.join(TMP, "%s_CAVITY_VBA.stp" % tag)
        for src, dst in ((pv, os.path.join(OUT, "%s_PUNCH_M3.stp" % tag)),
                         (cv, os.path.join(OUT, "%s_CAVITY_M3.stp" % tag))):
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(OUT, os.path.basename(src)))
                m3_post.process(src, dst)

        # ⑤ 验证
        step(5, "验证")
        print("  跑 m3_verify.py 需要固定的文件名，这里只列产物：")
        for f in sorted(os.listdir(OUT)):
            p = os.path.join(OUT, f)
            print("    %-34s %8d bytes" % (f, os.path.getsize(p)))

    except Exception:
        traceback.print_exc()
        print("\n!! 中断")


if __name__ == "__main__":
    main()
