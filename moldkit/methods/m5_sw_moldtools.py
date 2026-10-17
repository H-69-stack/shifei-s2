# -*- coding: utf-8 -*-
"""方法五：SolidWorks 自带「模具工具」工作流。

走的是 SolidWorks 官方 Mold Design 的那一套特征：
    分型线 → 分型面 → 关闭曲面 → 型心/型腔（InsertMoldCoreCavitySolids）

对应的 API（都来自 IFeatureManager）：
    InsertMoldPartingLine(BFlipDir)
    InsertMoldPartingSurface(Radiate, ReverseAlignment, ReverseOffset,
                             OffsetDistance, Angle, Smooth, SmoothDistance, Knit)
    InsertMoldShutOffSurface()
    InsertMoldCoreCavitySolids(Dist1, Dist2, Setback, Angle)

★适用范围要讲清楚★
    SW 的模具工具是为【实心成型零件】设计的：它要靠拔模分析找出一条分型线。
    本项目最常见的输入是薄壁板料件（板厚 2mm），分型线可能落在板厚方向上，
    分型线/分型面有较大概率识别不出来。所以这个方法放在这里是为了
    "方法齐全 + 能验证"，不一定会成功 —— 失败时日志会明确写出卡在哪一步。
    薄壁件请优先用方法三（成形到曲面）。
"""
import os

from ..method import MoldMethod, MoldOutput, Param, Step
from .. import geo, post, verify, swjobs


class Method5SwMoldTools(MoldMethod):
    name = "m5"
    label = "方法五：SolidWorks 模具工具（分型线/分型面/型心型腔）"
    engine = "solidworks"
    needs = ("product",)
    desc = ("用 SolidWorks 自带的模具工具工作流：分型线 → 分型面 → 关闭曲面 → "
            "型心/型腔（InsertMoldCoreCavitySolids）。")
    pros = "完全走 SW 官方模具设计流程；对实心成型零件最正统"
    cons = ("要求零件能识别出分型线；薄壁板料件的分型线常落在板厚方向，"
            "有较大概率失败 —— 薄壁件请用方法三")
    params = [
        Param("d1", "型心/型腔块：上方向厚度", 0.0, "mm", help="0 = 自动（产品高度的 0.5 倍）"),
        Param("d2", "型心/型腔块：下方向厚度", 0.0, "mm", help="0 = 自动"),
        Param("setback", "后退量", 0.0, "mm"),
        Param("angle", "拔模角", 0.0, "度"),
        Param("shutoff", "是否做关闭曲面", True, kind="bool"),
        Param("clearance", "是否自动去干涉", True, kind="bool"),
    ]

    def available(self, ctx=None):
        ok, why = super().available(ctx)
        if not ok:
            return ok, why
        return swjobs.availability()

    def generate(self, ctx):
        out = MoldOutput(self.name)
        log = ctx.log
        eng = swjobs.engine()
        try:
            prod = geo.read_step(ctx.product)
            pb = geo.bbox(prod)
            h = pb[5] - pb[2]
            d1 = float(ctx.p("d1", self.name) or 0.0) or h * 0.5
            d2 = float(ctx.p("d2", self.name) or 0.0) or h * 0.5
            setback = float(ctx.p("setback", self.name) or 0.0)
            angle = float(ctx.p("angle", self.name) or 0.0)
            log("  产品 bbox z[%.2f,%.2f] 高度=%.2f  → 型心/型腔厚度 d1=%.2f d2=%.2f" %
                (pb[2], pb[5], h, d1, d2))

            raw = ctx.path("%s_m5_RAW.stp" % ctx.part_name)
            job = swjobs.Job()
            job.import_step(ctx.product, "prod")
            job.raw("DRAFT_ANALYSIS", dir="Z", angle=angle)
            job.raw("MOLD_PARTING_LINE", flip=0, name="pline")
            job.raw("MOLD_PARTING_SURFACE", name="psurf")
            if ctx.p("shutoff", self.name):
                job.raw("MOLD_SHUTOFF", name="shut")
            job.raw("MOLD_CORE_CAVITY", d1=d1, d2=d2, setback=setback,
                    angle=angle, name="corecav")
            job.check()
            job.export(raw)

            with Step(log, "SolidWorks 模具工具工作流"):
                r = eng.run_job(job, quiet=True)
                log(r.log)
                if not r.ok:
                    return out.fail("模具工具工作流失败: %s（薄壁件常见，请改用方法三）"
                                    % r.error)
                solids = r.solids()
                log("  SolidWorks 报告实体数 = %d" % len(solids))

            with Step(log, "后处理：壳→实体归一化"):
                dst = ctx.path("%s_m5_PAIR.stp" % ctx.part_name)
                okn, info = post.normalize(raw, dst, log=log)
                if not okn:
                    return out.fail("后处理失败：导出里没有闭合壳")
                sh = geo.read_step(dst)
                c = geo.counts(sh)
                log("  归一化后：实体%d 壳%d 面%d 体积=%.0f mm^3" %
                    (c["solids"], c["shells"], c["faces"], geo.volume(sh)))
                if c["solids"] >= 2:
                    # SW 会给出两个实体（型心 / 型腔），按 z 分成上下模
                    from OCP.TopExp import TopExp_Explorer
                    from OCP.TopAbs import TopAbs_SOLID
                    ex = TopExp_Explorer(sh, TopAbs_SOLID)
                    sols = []
                    while ex.More():
                        sols.append(ex.Current())
                        ex.Next()
                    sols.sort(key=lambda s: geo.bbox(s)[2])
                    out.cavity_shape, out.punch_shape = sols[0], sols[-1]
                    out.cavity_path = out.punch_path = dst
                else:
                    out.punch_shape = sh
                    out.punch_path = dst
                    log("  ! 只得到 1 个实体，没有分出上下模")

            with Step(log, "校验"):
                rep = verify.full_report(out.punch_shape, out.cavity_shape, tag=self.label)
                out.metrics["report"] = rep
                verify.print_report(rep, log=log)
            out.ok = True
        except Exception as e:
            return out.fail("异常: %s" % e, e)
        return out
