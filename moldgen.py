#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""moldgen —— 模具自动生成命令行入口。

用法：
    python moldgen.py list                                 列出所有方法与参数
    python moldgen.py recommend [输入...]                  按输入推荐方法
    python moldgen.py run  -m m3  [输入...] [-o 输出目录]  跑一个方法
    python moldgen.py compare       [输入...] [-o 输出目录] 把所有方法都跑一遍对比
    python moldgen.py install-sw                           把操作解释器装进 SolidWorks 的 .swp

输入：
    --product <产品实体.stp>        （方法二B / 四 需要）
    --upper   <上形变面.stp>        （方法二A / 三 需要）
    --lower   <下形变面.stp>
参数覆盖：
    --set h1=2 --set inset=0.3 ...   （也可写 --set m3.inset=0.3 只对该方法生效）
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from moldkit import pipeline, registry, swjobs


def _log_factory(quiet=False, tee=None):
    fh = open(tee, "w", encoding="utf-8") if tee else None

    def log(s=""):
        if not quiet:
            print(s, flush=True)
        if fh:
            fh.write(str(s) + "\n")
            fh.flush()
    return log, fh


def _parse_set(pairs):
    d = {}
    for p in pairs or []:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        d[k.strip()] = v.strip()
    return d


def cmd_list(args):
    print("可用方法：\n")
    for info in registry.list_info():
        print("  %-5s %s   [%s]" % (info["name"], info["label"], info["engine"]))
        print("        需要: %s" % (", ".join(info["needs"]) or "-"))
        if info["desc"]:
            print("        %s" % info["desc"])
        if info["pros"]:
            print("        优点: %s" % info["pros"])
        if info["cons"]:
            print("        限制: %s" % info["cons"])
        if info["params"]:
            print("        参数:")
            for p in info["params"]:
                print("            %-12s %-34s 默认=%s%s %s" % (
                    p["name"], p["label"], p["default"], p["unit"],
                    ("[" + p["kind"] + "]") if p["kind"] != "float" else ""))
        print()


def cmd_recommend(args):
    ctx = pipeline.make_context(args.out or os.path.join(HERE, "mold_out"),
                                product=args.product, upper=args.upper,
                                lower=args.lower, log=lambda s="": None)
    best, cands, note = registry.recommend(ctx)
    print("输入: product=%s upper=%s lower=%s" % (args.product, args.upper, args.lower))
    print()
    for m in cands:
        tag = "  ← 推荐" if best is not None and m.name == best.name else ""
        print("  %-5s %s%s" % (m.name, m.label, tag))
    if not cands:
        print("  没有可用方法")
    if note:
        print("\n注意: %s" % note)


def cmd_install_sw(args):
    eng = swjobs.engine()
    print("引擎源码:", eng.engine_src)
    print("宏文件  :", eng.swp)
    if not eng.install():
        print("\n!! 装失败。请先在 SolidWorks 里按 Alt+F11 打开 VBA 编辑器（工程需已加载）")
        return 1
    print("\n方法列表:")
    for m in eng.list_methods():
        print("   ", m)
    print("\n入口 %s 存在 = %s" % (eng.entry, eng.has_entry()))
    return 0


def _run(args, methods, log):
    params = _parse_set(args.set)
    # 额外输入（方法一的毛坯）用专门选项时写进参数
    for flag, key in (("punch_mold", "punch_mold"), ("cavity_mold", "cavity_mold")):
        v = getattr(args, flag, None)
        if v:
            params.setdefault(key, v)
    ctx = pipeline.make_context(args.out or os.path.join(HERE, "mold_out"),
                                product=args.product, upper=args.upper,
                                lower=args.lower, params=params,
                                part_name=args.name, log=log)
    log("工作目录: %s" % ctx.workdir)
    log("零件名  : %s" % ctx.part_name)
    results = {}
    if len(methods) == 1:
        results[methods[0]] = pipeline.run(methods[0], ctx, log=log)
    else:
        results = pipeline.run_all(ctx, only=methods, log=log)
    pipeline.summarize(results, log=log)
    rp = os.path.join(ctx.workdir, "%s_report.json" % ctx.part_name)
    pipeline.save_report(results, rp)
    log("\n报告: %s" % rp)
    log("\n产物:")
    for f in sorted(os.listdir(ctx.workdir)):
        p = os.path.join(ctx.workdir, f)
        if os.path.isfile(p):
            log("  %-44s %9d bytes" % (f, os.path.getsize(p)))
    bad = [n for n, o in results.items() if not o.ok]
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description="模具自动生成", add_help=True,
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd")

    def add_inputs(p):
        p.add_argument("--product", help="产品实体 STEP")
        p.add_argument("--upper", help="上形变面 STEP")
        p.add_argument("--lower", help="下形变面 STEP")
        p.add_argument("--punch-mold", help="凸模毛坯 compound（方法一）")
        p.add_argument("--cavity-mold", help="凹模毛坯 compound（方法一）")
        p.add_argument("-o", "--out", help="输出目录")
        p.add_argument("--name", help="零件名（用于输出文件前缀）")
        p.add_argument("--set", action="append", help="覆盖参数 key=value，可多次")
        p.add_argument("-q", "--quiet", action="store_true")
        p.add_argument("--tee", help="日志同时写到文件")

    ap.add_argument("--list", action="store_true", help="列出方法")
    p = sub.add_parser("list", help="列出所有方法")
    p = sub.add_parser("recommend", help="推荐方法")
    add_inputs(p)
    p = sub.add_parser("run", help="跑一个方法")
    add_inputs(p)
    p.add_argument("-m", "--method", required=True)
    p = sub.add_parser("compare", help="所有方法对比")
    add_inputs(p)
    p.add_argument("-m", "--methods", action="append", help="只跑这些，可多次")
    p = sub.add_parser("install-sw", help="把操作解释器装进 SolidWorks")

    args = ap.parse_args()
    if args.list or args.cmd == "list":
        return cmd_list(args) or 0
    if args.cmd == "recommend":
        return cmd_recommend(args) or 0
    if args.cmd == "install-sw":
        return cmd_install_sw(args)
    if args.cmd == "run":
        log, fh = _log_factory(args.quiet, args.tee)
        try:
            return _run(args, [args.method], log)
        finally:
            if fh:
                fh.close()
    if args.cmd == "compare":
        log, fh = _log_factory(args.quiet, args.tee)
        try:
            ms = args.methods or registry.names()
            return _run(args, ms, log)
        finally:
            if fh:
                fh.close()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
