# -*- coding: utf-8 -*-
"""方法二B：产品实体布尔（OpenCASCADE）—— 项目里最稳的一条。

原理：
    直接把【产品实体】(镜像后的板料) 用盒体布尔掉：
    上模 = 盒 − 产品（上方），下模 = 盒 − 产品（下方）
    再做二次切除，避免出现 1~2% 的薄片残料。

特点：
    · 秒级完成、结果稳定（1 实体 1 壳、BRepCheck 有效）
    · 需要产品实体（不是形变面）
    · 历史实测贴合度：2ROM 98.3%、1131 98.9%、1238 98.6%
"""
import os

from ..method import MoldMethod, MoldOutput, Param, Step
from .. import geo, post, verify


class Method2B(MoldMethod):
    name = "m2b"
    label = "方法二B：产品实体布尔（OpenCASCADE）"
    engine = "ocp"
    needs = ("product",)
    desc = ("用完整产品实体做盒体布尔，稳定产出上下模。"
            "目标几何与方法二A相同，但只需秒级、质量更好。")
    pros = "最稳、最快；1 实体 1 壳、BRepCheck 有效；贴合度 98%+"
    cons = "需要产品实体（而不是形变面）；上下模同样是竖向分割，仍要留间隙"
    params = [
        Param("h1", "上模：高度面 P = 产品最低点 + h1", 0.0, "mm", help="0 = 自动"),
        Param("h_cavity", "下模空腔高", 0.0, "mm", help="0 = 自动"),
        Param("margin", "基座每边外扩", 5.0, "mm"),
        Param("clearance", "是否自动去干涉", True, kind="bool"),
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
            with Step(log, "方法二B：产品实体布尔"):
                r = g.generate_from_product(ctx.product,
                                            h1=(h1 if h1 > 0 else None),
                                            h_cavity=(hc if hc > 0 else None))
                if not r.get("success"):
                    return out.fail("生成失败: %s" % r.get("error"))
                out.punch_shape = r["punch"]["_shape"]
                out.cavity_shape = r["cavity"]["_shape"]

            with Step(log, "导出"):
                pp = ctx.path("%s_m2b_PUNCH.stp" % ctx.part_name)
                cp = ctx.path("%s_m2b_CAVITY.stp" % ctx.part_name)
                geo.write_step(out.punch_shape, pp)
                geo.write_step(out.cavity_shape, cp)
                out.punch_path, out.cavity_path = pp, cp
                log("  %s" % pp)
                log("  %s" % cp)

            if ctx.p("clearance", self.name):
                with Step(log, "去干涉"):
                    pc = ctx.path("%s_m2b_PUNCH_CLEAR.stp" % ctx.part_name)
                    newp, info = post.clearance(out.punch_shape, out.cavity_shape,
                                                out=pc, log=log)
                    out.punch_shape = newp
                    out.punch_path = pc
                    out.metrics["clearance"] = info

            out.metrics["report"] = verify.full_report(
                out.punch_shape, out.cavity_shape,
                product=geo.read_step(ctx.product), tag=self.label)
            verify.print_report(out.metrics["report"], log=log)
            out.ok = True
        except Exception as e:
            return out.fail("异常: %s" % e, e)
        return out
