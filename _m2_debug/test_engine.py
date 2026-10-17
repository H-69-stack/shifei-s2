# -*- coding: utf-8 -*-
"""验证 SolidWorks 操作解释器：装引擎 + 用作业脚本重跑方法三。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from moldkit.swjobs import SwEngine, Job

TMP = r"C:\Temp\m3test"


def main():
    do_install = "--install" in sys.argv
    eng = SwEngine()
    print("=" * 74)
    print("引擎源码 :", eng.engine_src)
    print("宏文件   :", eng.swp)
    print("=" * 74)

    if do_install:
        if not eng.install():
            print("!! 装引擎失败")
            return
        print("已装。现在的方法列表：")
        for m in eng.list_methods():
            print("   ", m)

    print("\n入口存在 =", eng.has_entry())
    if not eng.has_entry():
        print("!! 引擎没装好")
        return

    # ---- 方法三（上模）：与之前验证过的结果对比 ----
    out = os.path.join(TMP, "ENGINE_PUNCH.stp")
    if os.path.exists(out):
        os.remove(out)
    job = (Job()
           .import_step(os.path.join(TMP, "2ROM_upper_filled.stp"), "srf")
           .plane_z(72.0, "ptop")
           .sketch_poly("ptop", os.path.join(TMP, "2ROM_upper_poly.txt"), "skp")
           .boss_upto("skp", "srf", "ptop", "DOWN", "punch")
           .check()
           .export(out))
    print("\n" + "=" * 74)
    print("作业脚本：")
    print(job.text())
    r = eng.run_job(job)
    print("-" * 74)
    print(r.log)
    print("-" * 74)
    print("ok =", r.ok, " 结果 =", r.result)
    for s in r.solids():
        print("  实体%d 体积=%.1f mm^3 bbox=%s" % (s["idx"], s["volume_m3"] * 1e9, s["bbox"]))
    print("输出存在 =", os.path.exists(out),
          os.path.getsize(out) if os.path.exists(out) else "")


if __name__ == "__main__":
    main()
