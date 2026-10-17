# -*- coding: utf-8 -*-
"""方法一（legacy）：mold_fix.py 的 v30→v42 修复管线。

★注意输入不一样★：方法一吃的【不是产品、也不是形变面】，而是已经生成好的
「凸模 / 凹模毛坯 compound 文件」（由更早的 mold_generator / BoxGenerator 产出）。
所以它只在"你手上已经有毛坯"时可用，界面上会因此显示为不可用。

    fill()   修复凸模：输出 compound [基座, 凸起, 产品]
    hollow() 修复凹模：输出 compound [型腔, 产品]

这条管线迭代了 13 个版本（v30→v42），每版都针对一个具体缺陷，
是项目里历史最久的一条。留着它主要是为了对照与兜底。
"""
import os

from ..method import MoldMethod, MoldOutput, Param, Step
from .. import geo, verify


class Method1Legacy(MoldMethod):
    name = "m1"
    label = "方法一（legacy）：mold_fix v42 修复管线"
    engine = "ocp"
    needs = ()
    desc = ("项目最早的一套完整修复管线（mold_fix.py，v30→v42）。"
            "输入是凸模/凹模【毛坯 compound 文件】，不是产品或形变面。")
    pros = "历史最久、针对 13 类具体缺陷逐版打磨过；带几何状态缓存"
    cons = ("输入格式与其它方法不同（要毛坯文件）；"
            "参数多、逻辑重（189KB），不适合作为新流程的主路径")
    # ★界面上会为这两项渲染文件输入口★
    extra_inputs = [
        {"key": "punch_mold", "label": "凸模毛坯 compound（走 fill）", "accept": ".stp,.step"},
        {"key": "cavity_mold", "label": "凹模毛坯 compound（走 hollow）", "accept": ".stp,.step"},
    ]
    params = [
        Param("punch_mold", "凸模毛坯 compound 文件路径", "", kind="str",
              help="走 fill()；留空则跳过上模"),
        Param("cavity_mold", "凹模毛坯 compound 文件路径", "", kind="str",
              help="走 hollow()；留空则跳过下模"),
        Param("product", "产品实体 STEP 路径", "", kind="str",
              help="毛坯里若不内嵌产品，需要在这里给"),
        Param("trim_edge", "外边缘切齐", False, kind="bool"),
        Param("remove_pillars", "去除残留柱子", False, kind="bool"),
    ]

    def available(self, ctx=None):
        if ctx is None:
            return True, ""
        p = (ctx.p("punch_mold", self.name) or "").strip()
        c = (ctx.p("cavity_mold", self.name) or "").strip()
        if not p and not c:
            return False, ("方法一需要【凸模/凹模毛坯 compound 文件】"
                           "（参数里填 punch_mold / cavity_mold）")
        for k, v in (("punch_mold", p), ("cavity_mold", c)):
            if v and not os.path.exists(v):
                return False, "%s 不存在: %s" % (k, v)
        return True, ""

    def generate(self, ctx):
        out = MoldOutput(self.name)
        log = ctx.log
        try:
            import sys
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if root not in sys.path:
                sys.path.insert(0, root)
            from mold_fix import MoldFixer

            fixer = MoldFixer()
            od = ctx.path("m1")
            os.makedirs(od, exist_ok=True)
            product = (ctx.p("product", self.name) or ctx.product or "").strip() or None

            p = (ctx.p("punch_mold", self.name) or "").strip()
            if p:
                with Step(log, "方法一 fill()：修复凸模"):
                    try:
                        r = fixer.fill(p, product_filepath=product,
                                       output_dir=od, output_name="%s_m1_PUNCH" % ctx.part_name)
                        log("  fill 返回: %r" % (r,))
                    except Exception as e:
                        log("  !! fill 失败: %s" % e)
                        return out.fail("fill 失败: %s" % e)

            c = (ctx.p("cavity_mold", self.name) or "").strip()
            if c:
                with Step(log, "方法一 hollow()：修复凹模"):
                    try:
                        r = fixer.hollow(c, product_filepath=product,
                                         output_dir=od, output_name="%s_m1_CAVITY" % ctx.part_name,
                                         trim_edge=bool(ctx.p("trim_edge", self.name)),
                                         remove_pillars=bool(ctx.p("remove_pillars", self.name)))
                        log("  hollow 返回: %r" % (r,))
                    except Exception as e:
                        log("  !! hollow 失败: %s" % e)
                        return out.fail("hollow 失败: %s" % e)

            # 收集产物
            made = []
            for f in sorted(os.listdir(od)):
                if f.lower().endswith((".stp", ".step")):
                    made.append(os.path.join(od, f))
            if not made:
                return out.fail("方法一跑完了但没找到输出 STEP")
            out.artifacts = {"m1_outputs": made}
            out.metrics["outputs"] = made
            for f in made:
                log("  产物 %s" % f)

            # ★把 compound 拆成"模体"和"产品"，给出和别的方法同口径的校验★
            #   legacy 的输出是 compound [基座, 凸起, 产品] / [型腔, 产品]，
            #   产品是其中一块，不能当成模具本体来算结构。
            #   ★产品贴合一律用 compound 里【内嵌的那块】做参考★
            #   毛坯的坐标系不一定是世界系（实测毛坯 z[-36,44]、外部产品 z[1,71]，
            #   差 35mm）。拿外部文件去比会得出完全错误的"侵入 18.3%"。
            prod_shape = None
            if product:
                try:
                    prod_shape = geo.read_step(product)
                    log("  外部产品 %s（体积 %.0f）—— 仅用于识别内嵌产品块" %
                        (os.path.basename(product), geo.volume(prod_shape)))
                except Exception as e:
                    log("  读产品失败（改用体积最小的块）：%s" % e)
            prod_vol = geo.volume(prod_shape) if prod_shape is not None else None
            prod_ref = None

            for f in made:
                try:
                    sh = geo.read_step(f)
                    sols = geo.split_solids(sh)
                    tag = "上模" if "PUNCH" in f.upper() else "下模"
                    log("  %s %s：compound 里有 %d 个实体，体积 %s" % (
                        tag, os.path.basename(f), len(sols),
                        " / ".join("%.0f" % geo.volume(s) for s in sols)))
                    if not sols:
                        continue
                    if len(sols) >= 2:
                        if prod_vol:
                            pi = min(range(len(sols)),
                                     key=lambda i: abs(geo.volume(sols[i]) - prod_vol))
                        else:
                            pi = len(sols) - 1
                        prod_member = sols[pi]
                        mold_sols = [s for i, s in enumerate(sols) if i != pi]
                        log("    内嵌产品块 = 第 %d 块（体积 %.0f），模体由其余 %d 块组成" %
                            (pi, geo.volume(prod_member), len(mold_sols)))
                        if prod_ref is None:
                            prod_ref = prod_member
                            log("    → 产品贴合的参考物用这块内嵌产品（保证同坐标系）")
                    else:
                        mold_sols = sols
                    mold = geo.fuse_many(mold_sols) if len(mold_sols) > 1 else mold_sols[0]
                    c2 = geo.counts(mold)
                    log("    %s 模体：实体%d 壳%d 面%d 体积=%.0f mm^3 有效=%s" %
                        (tag, c2["solids"], c2["shells"], c2["faces"],
                         geo.volume(mold), geo.is_valid(mold)))
                    if tag == "上模":
                        out.punch_shape, out.punch_path = mold, f
                    else:
                        out.cavity_shape, out.cavity_path = mold, f
                except Exception as e:
                    log("  读取 %s 失败: %s" % (f, e))

            with Step(log, "校验"):
                rep = verify.full_report(out.punch_shape, out.cavity_shape,
                                         product=prod_ref, tag=self.label)
                out.metrics["report"] = rep
                verify.print_report(rep, log=log)
            out.ok = True
        except Exception as e:
            return out.fail("异常: %s" % e, e)
        return out
