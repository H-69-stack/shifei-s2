# -*- coding: utf-8 -*-
"""方法四：SolidWorks 原生「基座 − 产品」（组合/删减）。

原理（经典的型心/型腔思路，用 SW 自己的布尔做）：
    在一个以【平整分型面 z_split】为界的分割下：
        上模 = 盒[z_split, P] − 产品
        下模 = 盒[z_lo, z_split] − 产品
    盒体用盲拉伸生成，并且 **Merge=0** 让它成为独立实体，
    再用 FeatureManager.InsertCombineFeature(CUT) 删掉产品。

特点 / 适用范围：
    · 纯 SolidWorks 特征，没有曲面运算，最"正统"
    · ★分型面是水平面★：产品越"厚实"越合适（铸件、锻件那种实心件）
    · 对薄壁板料件（板厚 2mm），水平分型面会把料切开，
      得到的上下模在分型面附近形状不理想 —— 这种情况请用方法三/五
    · 分型面高度 z_split 可调；默认取产品包围盒 z 的中点
"""
import os

from ..method import MoldMethod, MoldOutput, Param, Step
from .. import geo, post, verify, swjobs


class Method4SwCombine(MoldMethod):
    name = "m4"
    label = "方法四：SolidWorks 组合（基座 − 产品）"
    engine = "solidworks"
    needs = ("product",)
    desc = ("用 SolidWorks 的「组合·删减」把产品从上下两个基座块里扣掉，"
            "得到型心/型腔。分型面是一个水平面，高度可调。")
    pros = "纯 SW 特征、无曲面运算；对厚实产品最合适；分型面高度可调"
    cons = ("水平分型面对薄壁板料件不理想（会把板厚方向切开）；"
            "需要产品实体")
    params = [
        Param("split_z", "分型面高度 z", 0.0, "mm", help="0 = 自动（产品包围盒 z 中点）"),
        Param("pad_top", "上模在产品之上再留的厚度", 0.0, "mm", help="0 = 自动（产品高度的 0.5 倍）"),
        Param("pad_bot", "下模在产品之下再留的厚度", 0.0, "mm", help="0 = 自动（同上）"),
        Param("margin", "基座每边外扩", 5.0, "mm"),
        Param("clearance", "是否自动去干涉", True, kind="bool"),
    ]

    def available(self, ctx=None):
        ok, why = super().available(ctx)
        if not ok:
            return ok, why
        return swjobs.availability()

    def _one(self, eng, ctx, tag, src, z_lo, z_hi, x1, y1, x2, y2, out, log):
        """做一个半模：盒[z_lo, z_hi] − 产品。

        ★主实体用 @box 指代，工具体用 ALL★
          不能用序号：GetBodies2 的返回顺序不是创建顺序，而且不稳定
          （实测同一作业两次运行，产品一次在实体0、一次在实体1）。
          用序号会静默组合出错误的体 —— 体积看着"成功"其实完全不对。
          @box 是靠"这个特征创建的实体"反查，TOOL=ALL 就是"除主实体外全部"，
          两者都不依赖顺序。
        """
        job = swjobs.Job()
        job.import_step(src, "prod")
        job.plane_z(z_lo, "pbox")
        job.sketch_rect("pbox", x1, y1, x2, y2, "skbox")
        job.boss_blind("skbox", z_hi - z_lo, 0.0, "box", merge=False)
        job.check()          # 看看多实体（诊断用）
        job.combine("CUT", "@box", "ALL", "comb")
        job.check()
        job.export(out)
        r = eng.run_job(job, quiet=True)
        log(r.log)
        if not r.ok:
            raise RuntimeError("%s 失败: %s" % (tag, r.error))
        return r

    def generate(self, ctx):
        out = MoldOutput(self.name)
        log = ctx.log
        eng = swjobs.engine()
        try:
            prod = geo.read_step(ctx.product)
            pb = geo.bbox(prod)
            margin = float(ctx.p("margin", self.name) or 5.0)
            split_z = float(ctx.p("split_z", self.name) or 0.0)
            if split_z == 0.0:
                split_z = (pb[2] + pb[5]) / 2.0
            h = pb[5] - pb[2]
            pad_top = float(ctx.p("pad_top", self.name) or 0.0) or h * 0.5
            pad_bot = float(ctx.p("pad_bot", self.name) or 0.0) or h * 0.5
            P = pb[5] + pad_top
            z_lo = pb[2] - pad_bot
            x1, y1, x2, y2 = pb[0] - margin, pb[1] - margin, pb[3] + margin, pb[4] + margin
            log("  产品 bbox x[%.1f,%.1f] y[%.1f,%.1f] z[%.1f,%.1f]" %
                (pb[0], pb[3], pb[1], pb[4], pb[2], pb[5]))
            log("  分型面 z=%.2f   上模 z[%.2f,%.2f]   下模 z[%.2f,%.2f]" %
                (split_z, split_z, P, z_lo, split_z))
            out.metrics["params"] = {"split_z": split_z, "P": P, "z_lo": z_lo}

            outp = ctx.path("%s_m4_PUNCH_vba.stp" % ctx.part_name)
            outc = ctx.path("%s_m4_CAVITY_vba.stp" % ctx.part_name)

            with Step(log, "SolidWorks：上模（盒 − 产品）"):
                self._one(eng, ctx, "上模", ctx.product, split_z, P,
                          x1, y1, x2, y2, outp, log)
            with Step(log, "SolidWorks：下模（盒 − 产品）"):
                self._one(eng, ctx, "下模", ctx.product, z_lo, split_z,
                          x1, y1, x2, y2, outc, log)

            with Step(log, "后处理：壳→实体归一化"):
                pp = ctx.path("%s_m4_PUNCH.stp" % ctx.part_name)
                cp = ctx.path("%s_m4_CAVITY.stp" % ctx.part_name)
                ok1, _ = post.normalize(outp, pp, log=log)
                ok2, _ = post.normalize(outc, cp, log=log)
                if not (ok1 and ok2):
                    return out.fail("后处理失败：SolidWorks 导出里没有闭合壳")
                out.punch_shape = geo.read_step(pp)
                out.cavity_shape = geo.read_step(cp)
                out.punch_path, out.cavity_path = pp, cp

            if ctx.p("clearance", self.name):
                with Step(log, "去干涉"):
                    pc = ctx.path("%s_m4_PUNCH_CLEAR.stp" % ctx.part_name)
                    newp, info = post.clearance(out.punch_shape, out.cavity_shape,
                                                out=pc, log=log)
                    out.punch_shape = newp
                    out.punch_path = pc
                    out.metrics["clearance"] = info

            with Step(log, "校验"):
                rep = verify.full_report(out.punch_shape, out.cavity_shape,
                                         product=prod, tag=self.label)
                out.metrics["report"] = rep
                verify.print_report(rep, log=log)
            out.ok = True
        except Exception as e:
            return out.fail("异常: %s" % e, e)
        return out
