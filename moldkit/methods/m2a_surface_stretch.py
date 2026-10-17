# -*- coding: utf-8 -*-
"""方法二A：形变面竖向拉伸（OpenCASCADE）。

原理（mold_method2_generator 里的原版实现）：
    上模 = 形变面各面沿 +Z 拉伸的并集，裁到 z ≤ P（P = 形变面最低点 + h1）
    下模 = 基座块 − （下形变面各面沿 +Z 拉伸的并集）

特点：
    · 只用形变面，不需要产品实体
    · 竖向拉伸法在近竖直壁处上下模会天然咬入（咬入量 ≈ 板厚 × 壁面积），
      需要后处理去干涉
    · OCC 对这种"多片开放壳"的布尔不稳，形变面片数 > 20 基本会卡死
"""
import os

from ..method import MoldMethod, MoldOutput, Param, Step
from .. import geo, post, verify


class Method2A(MoldMethod):
    name = "m2a"
    label = "方法二A：形变面竖向拉伸（OpenCASCADE）"
    engine = "ocp"
    needs = ("upper", "lower")
    desc = ("把上/下形变面各自沿 +Z 拉伸成体，上模裁到高度面 P、"
            "下模用基座块挖掉下形变面以上的料。忠实于最早的法二A定义。")
    pros = "不需要产品实体；几何定义直观；秒级完成（面数少时）"
    cons = ("上下模在近竖直壁处天然咬入（≈板厚×壁面积），必须后处理去干涉；"
            "形变面片数多（>20）时 OCC 布尔会卡死")
    params = [
        Param("h1", "上模：高度面 P = 形变面最低点 + h1", 0.0, "mm",
              help="0 = 自动（形变面自身高度 dz）"),
        Param("h_cavity", "下模：空腔高（z_top = 最低点 + 该值）", 0.0, "mm",
              help="0 = 自动（dz）"),
        Param("margin", "基座每边外扩", 5.0, "mm"),
        Param("clearance", "是否自动去干涉（上模 − 下模）", True, kind="bool"),
    ]

    def generate(self, ctx):
        out = MoldOutput(self.name)
        log = ctx.log
        try:
            import sys
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if root not in sys.path:
                sys.path.insert(0, root)
            from mold_method2_generator import MoldMethod2Generator

            margin = float(ctx.p("margin", self.name) or 5.0)
            h1 = float(ctx.p("h1", self.name) or 0.0)
            hc = float(ctx.p("h_cavity", self.name) or 0.0)

            g = MoldMethod2Generator(margin=margin)
            with Step(log, "方法二A：生成上模（形变面竖向拉伸）"):
                rp = g.generate_punch(ctx.upper, h1=(h1 if h1 > 0 else None))
                if not rp.get("success"):
                    return out.fail("上模失败: %s" % rp.get("error"))
                out.punch_shape = rp["_shape"]
                out.metrics["punch_faces"] = rp["details"].get("face_count")
            with Step(log, "方法二A：生成下模（基座 − 下形变面拉伸）"):
                rc = g.generate_cavity(ctx.lower, h_cavity=(hc if hc > 0 else None))
                if not rc.get("success"):
                    return out.fail("下模失败: %s" % rc.get("error"))
                out.cavity_shape = rc["_shape"]
                out.metrics["cavity_faces"] = rc["details"].get("face_count")

            with Step(log, "导出"):
                pp = ctx.path("%s_m2a_PUNCH.stp" % ctx.part_name)
                cp = ctx.path("%s_m2a_CAVITY.stp" % ctx.part_name)
                geo.write_step(out.punch_shape, pp)
                geo.write_step(out.cavity_shape, cp)
                out.punch_path, out.cavity_path = pp, cp
                log("  %s" % pp)
                log("  %s" % cp)

            if ctx.p("clearance", self.name):
                with Step(log, "去干涉（上模 − 下模）"):
                    pc = ctx.path("%s_m2a_PUNCH_CLEAR.stp" % ctx.part_name)
                    newp, info = post.clearance(out.punch_shape, out.cavity_shape,
                                                out=pc, log=log)
                    out.punch_shape = newp
                    out.punch_path = pc
                    out.metrics["clearance"] = info

            out.metrics["report"] = verify.full_report(
                out.punch_shape, out.cavity_shape, tag=self.label)
            verify.print_report(out.metrics["report"], log=log)
            out.ok = True
        except Exception as e:
            return out.fail("异常: %s" % e, e)
        return out
