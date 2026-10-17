# -*- coding: utf-8 -*-
"""方法三：SolidWorks「成形到曲面」（二次开发，★推荐★）。

原理（这一版的思路和"用曲面去切"完全不同）：
    上模 = 在 z=P 的基准面上画形变面的【投影轮廓】→ 向下「成形到曲面」拉到上形变面
    下模 = 先拉基座块 → 在基座顶面画投影轮廓 → 向下「成形到曲面」切除到下形变面

    这两式正好等价于法二A，但**全程没有布尔、没有曲面切除**，
    只有 SolidWorks 最标准的两个拉伸特征，终止位置由 SW 自己算。

为什么旧版"使用曲面切除"必然失败：
    SW 要求曲面把实体【切断】。盒体四周都比形变面大，形变面边界整圈悬在实体内部，
    实体能从边界外侧绕过去 → 静默不切（InsertCutSurface 返回 err=0 但体积一位不差）。

三个必须做的预处理（都在这里做掉）：
    1. 散面缝合（否则 SW 会把每片面拆成独立文档）
    2. 补掉形变面上的孔（★孔处没有曲面可终止，拉伸必定失败★ 已实测确认）
    3. 算投影轮廓（★形变面投影只有包围盒的 76~92%，矩形轮廓必然失败★）

★★关于轮廓内缩 INSET（2026-09-20 修正，这是"拉伸/挖空没到位"的根因）★★
    早期为了让 SW 盖得住轮廓，默认内缩 0.5mm。实测这是错的：
      · 上模：轮廓比形变面小 0.5mm。2ROM 的形变面边界是【竖直壁】，SW 会把凸台
        自动外扩到壁面，所以看不出来；但 1131 那种【穹顶曲面】边界 SW 不会外扩
        → 上模四周实打实缺 0.5mm（就是用户报的"外缘缺口 / 拉伸没到位"）。
      · 下模：切除用内缩轮廓，会在型腔四周留下一圈没被挖掉的立墙。
        实测 2ROM：0.3mm 厚 × 周长 580mm × 高 44mm ≈ 7588 mm³
        （就是"挖空没到位"）。
    现在默认 INSET=0（精确投影轮廓），实测 SW 能正常终止；
    并且加了【失败自动降级】：万一轮廓正好相切导致失败，自动退回 0.4mm / 1.0mm 重试。

实测（2ROM31-6061）：上模 ≈ 2.6s、下模 ≈ 2.7s；上模贴合度 99.9%。
"""
import os

from ..method import MoldMethod, MoldOutput, Param, Step
from .. import geo, surfaces, post, verify, swjobs


def _prep_surface(src, dst, log, min_hole=2.0):
    """缝合 → 补孔 → 导出。返回 (path, shape, report)。"""
    sh = geo.read_step(src)
    sh = surfaces.sew(sh)
    sh, rep = surfaces.fill_holes(sh, min_hole_span=min_hole, log=log)
    geo.write_step(sh, dst)
    log("  预处理完成 -> %s" % dst)
    return dst, sh, rep


class Method3SwUpto(MoldMethod):
    name = "m3"
    label = "方法三：SolidWorks「成形到曲面」（推荐）"
    engine = "solidworks"
    needs = ("upper", "lower")
    desc = ("用 SolidWorks 的「成形到曲面」拉伸/切除直接生成上下模实体。"
            "几何等价于法二A，但由 SW 的几何内核完成，秒级且稳定。")
    pros = ("秒级完成；几何由 SolidWorks 内核保证；"
            "没有布尔、没有曲面切除，不依赖 OCC 处理开放壳")
    cons = ("需要 SolidWorks 与已装好的宏；"
            "形变面必须先补孔、轮廓必须用投影轮廓（软件已自动处理）")
    params = [
        Param("h1", "上模：高度面 P = 形变面最低点 + h1", 0.0, "mm",
              help="0 = 自动（dz + ppad）"),
        Param("ppad", "P 相对形变面最高点的余量", 1.0, "mm",
              help="法二A 用 0（尖边）；SW 里建议留 1mm 避免零厚度"),
        Param("h_cavity", "下模空腔高（z_top = 最低点 + 该值）", 0.0, "mm",
              help="0 = 自动（dz + cpad）"),
        Param("cpad", "下模基座顶面余量", 2.0, "mm"),
        Param("margin", "下模基座每边外扩", 5.0, "mm"),
        Param("cutlift", "切除基准面相对基座顶面的抬高量", 0.5, "mm"),
        Param("inset", "投影轮廓内缩量", 0.0, "mm",
              help="★默认 0（精确轮廓）★。这个量就是上模四周比形变面'少'多少、"
                   "也是下模型腔四周残留立墙的厚度。失败时软件会自动退回 0.4mm 重试"),
        Param("cell", "轮廓栅格分辨率", 0.1, "mm", help="越小轮廓越准，0.1 已足够"),
        Param("clearance", "是否自动去干涉（上模 − 下模）", True, kind="bool"),
    ]

    def available(self, ctx=None):
        ok, why = super().available(ctx)
        if not ok:
            return ok, why
        return swjobs.availability()

    # ------------------------------------------------------------------
    def _build(self, ctx, out, log, eng, up_f, up_sh, lo_f, lo_sh,
               inset, cell, margin, P, z_top, z_lo, cutlift):
        """按给定内缩量完整做一遍（补孔后的面已经准备好）。"""
        ub, lb = geo.bbox(up_sh), geo.bbox(lo_sh)

        with Step(log, "算投影轮廓（inset=%.2fmm）" % inset):
            up_poly = ctx.path("%s_upper_poly.txt" % ctx.part_name)
            lo_poly = ctx.path("%s_lower_poly.txt" % ctx.part_name)
            pu, iu = surfaces.projection_outline(
                up_sh, out_png=ctx.path("%s_upper_outline.png" % ctx.part_name),
                cell=cell, inset=inset, log=log)
            surfaces.write_poly(pu, up_poly)
            pl, il = surfaces.projection_outline(
                lo_sh, out_png=ctx.path("%s_lower_outline.png" % ctx.part_name),
                cell=cell, inset=inset, log=log)
            surfaces.write_poly(pl, lo_poly)
            out.metrics["outline"] = {"upper": iu, "lower": il}

        outp = ctx.path("%s_m3_PUNCH_vba.stp" % ctx.part_name)
        outc = ctx.path("%s_m3_CAVITY_vba.stp" % ctx.part_name)

        with Step(log, "SolidWorks：上模（成形到曲面·凸台）"):
            jp = (swjobs.Job()
                  .import_step(up_f, "srf")
                  .plane_z(P, "ptop")
                  .sketch_poly("ptop", up_poly, "skp")
                  .boss_upto("skp", "srf", "ptop", "DOWN", "punch")
                  .delete_surfaces()
                  .check()
                  .export(outp))
            rp = eng.run_job(jp, quiet=True)
            log(rp.log)
            if not rp.ok:
                return False, "上模生成失败: %s" % rp.error

        with Step(log, "SolidWorks：下模（基座 + 成形到曲面·切除）"):
            jc = (swjobs.Job()
                  .import_step(lo_f, "srf")
                  .plane_auto("sysz")
                  .sketch_rect("sysz", lb[0] - margin, lb[1] - margin,
                               lb[3] + margin, lb[4] + margin, "skbase")
                  .boss_blind("skbase", z_top, -z_lo, "base")
                  .plane_z(z_top + cutlift, "pcut")
                  .sketch_poly("pcut", lo_poly, "skcut")
                  .cut_upto("skcut", "srf", "pcut", "DOWN", "cav")
                  .delete_surfaces()
                  .check()
                  .export(outc))
            rc = eng.run_job(jc, quiet=True)
            log(rc.log)
            if not rc.ok:
                return False, "下模生成失败: %s" % rc.error

        with Step(log, "后处理：壳→实体归一化"):
            pp = ctx.path("%s_m3_PUNCH.stp" % ctx.part_name)
            cp = ctx.path("%s_m3_CAVITY.stp" % ctx.part_name)
            ok1, _ = post.normalize(outp, pp, log=log)
            ok2, _ = post.normalize(outc, cp, log=log)
            if not (ok1 and ok2):
                return False, "后处理失败：SolidWorks 导出里没有闭合壳"
            out.punch_shape = geo.read_step(pp)
            out.cavity_shape = geo.read_step(cp)
            out.punch_path, out.cavity_path = pp, cp

        if ctx.p("clearance", self.name):
            with Step(log, "去干涉（上模 − 下模）"):
                pc = ctx.path("%s_m3_PUNCH_CLEAR.stp" % ctx.part_name)
                newp, info = post.clearance(out.punch_shape, out.cavity_shape,
                                            out=pc, log=log)
                out.punch_shape = newp
                out.punch_path = pc
                out.metrics["clearance"] = info

        with Step(log, "校验"):
            rep = verify.full_report(out.punch_shape, out.cavity_shape,
                                     upper=up_sh, P=P, tag=self.label)
            out.metrics["report"] = rep
            verify.print_report(rep, log=log)
        return True, None

    # ------------------------------------------------------------------
    def _build_by_surface_cut(self, ctx, out, log, eng, up_f, up_sh, lo_f, lo_sh,
                              inset, cell, margin, P, z_top, z_lo):
        """降级路线：投影轮廓棱柱 + 使用曲面切除（不依赖"高度场"）。

        ★为什么需要★「成形到曲面」要求竖直线上只打中曲面一次。带内凹加强筋的
        穹顶（实测 YA-1131：41 片，含球面/环面/样条）做不到 → 直接返回 Nothing。
        这时改成：
            上模 = 棱柱(投影轮廓, z_lo→P) 用上形变面切开，留【上面】
            下模 = 基座 − (棱柱(z_lo→z_top) 用下形变面切开留【上面】)
        棱柱的 XY 正好等于曲面的投影轮廓，所以曲面的边界环落在棱柱侧壁上，
        曲面能把棱柱【切断】—— 这是"使用曲面切除"能生效的前提。
        """
        ub, lb = geo.bbox(up_sh), geo.bbox(lo_sh)

        with Step(log, "算投影轮廓（降级路线，inset=%.2fmm）" % inset):
            up_poly = ctx.path("%s_upper_poly.txt" % ctx.part_name)
            lo_poly = ctx.path("%s_lower_poly.txt" % ctx.part_name)
            pu, iu = surfaces.projection_outline(
                up_sh, out_png=ctx.path("%s_upper_outline.png" % ctx.part_name),
                cell=cell, inset=inset, log=log)
            surfaces.write_poly(pu, up_poly)
            pl, il = surfaces.projection_outline(
                lo_sh, out_png=ctx.path("%s_lower_outline.png" % ctx.part_name),
                cell=cell, inset=inset, log=log)
            surfaces.write_poly(pl, lo_poly)
            out.metrics["outline"] = {"upper": iu, "lower": il}

        outp = ctx.path("%s_m3_PUNCH_vba.stp" % ctx.part_name)
        outc = ctx.path("%s_m3_CAVITY_vba.stp" % ctx.part_name)

        with Step(log, "SolidWorks：上模（棱柱 + 使用曲面切除）"):
            jp = (swjobs.Job()
                  .import_step(up_f, "srf")
                  .plane_z(z_lo, "pb")
                  .sketch_poly("pb", up_poly, "skp")
                  .boss_blind("skp", P - z_lo, 0.0, "prism", merge=False)
                  .raw("CUT_SURFACE", surf="srf", target="@prism", keep="ABOVE",
                       name="punch")
                  .delete_surfaces().check().export(outp))
            rp = eng.run_job(jp, quiet=True)
            log(rp.log)
            if not rp.ok:
                return False, "上模(降级路线)失败: %s" % rp.error

        with Step(log, "SolidWorks：下模（基座 − 表面以上的料）"):
            jc = (swjobs.Job()
                  .import_step(lo_f, "srf")
                  .plane_z(z_lo, "pb")
                  .sketch_poly("pb", lo_poly, "skp")
                  .boss_blind("skp", z_top - z_lo, 0.0, "above", merge=False)
                  .raw("CUT_SURFACE", surf="srf", target="@above", keep="ABOVE",
                       name="abv")
                  .plane_z(z_lo, "pb2")
                  .sketch_rect("pb2", lb[0] - margin, lb[1] - margin,
                               lb[3] + margin, lb[4] + margin, "skbase")
                  .boss_blind("skbase", z_top - z_lo, 0.0, "base", merge=False)
                  .combine("CUT", "@base", "@above", "cav")
                  .delete_surfaces().check().export(outc))
            rc = eng.run_job(jc, quiet=True)
            log(rc.log)
            if not rc.ok:
                return False, "下模(降级路线)失败: %s" % rc.error

        with Step(log, "后处理：壳→实体归一化"):
            pp = ctx.path("%s_m3_PUNCH.stp" % ctx.part_name)
            cp = ctx.path("%s_m3_CAVITY.stp" % ctx.part_name)
            ok1, _ = post.normalize(outp, pp, log=log)
            ok2, _ = post.normalize(outc, cp, log=log)
            if not (ok1 and ok2):
                return False, "后处理失败：SolidWorks 导出里没有闭合壳"
            out.punch_shape = geo.read_step(pp)
            out.cavity_shape = geo.read_step(cp)
            out.punch_path, out.cavity_path = pp, cp

        if ctx.p("clearance", self.name):
            with Step(log, "去干涉（上模 − 下模）"):
                pc = ctx.path("%s_m3_PUNCH_CLEAR.stp" % ctx.part_name)
                newp, info = post.clearance(out.punch_shape, out.cavity_shape,
                                            out=pc, log=log)
                out.punch_shape = newp
                out.punch_path = pc
                out.metrics["clearance"] = info

        with Step(log, "校验"):
            rep = verify.full_report(out.punch_shape, out.cavity_shape,
                                     upper=up_sh, P=P, tag=self.label)
            out.metrics["report"] = rep
            verify.print_report(rep, log=log)
        return True, None

    # ------------------------------------------------------------------
    def generate(self, ctx):
        out = MoldOutput(self.name)
        log = ctx.log
        eng = swjobs.engine()
        try:
            inset = float(ctx.p("inset", self.name) or 0.0)
            cell = float(ctx.p("cell", self.name) or 0.1)
            margin = float(ctx.p("margin", self.name) or 5.0)

            # ---------- 1. 预处理 ----------
            with Step(log, "预处理：缝合 + 补孔"):
                up_f, up_sh, up_rep = _prep_surface(
                    ctx.upper, ctx.path("%s_upper_filled.stp" % ctx.part_name), log)
                lo_f, lo_sh, lo_rep = _prep_surface(
                    ctx.lower, ctx.path("%s_lower_filled.stp" % ctx.part_name), log)
                out.metrics["fill"] = {"upper": up_rep, "lower": lo_rep}

            ub = geo.bbox(up_sh)
            lb = geo.bbox(lo_sh)
            dz_u = ub[5] - ub[2]
            dz_l = lb[5] - lb[2]

            # ---------- 2. 参数 ----------
            h1 = float(ctx.p("h1", self.name) or 0.0)
            if h1 <= 0:
                h1 = dz_u + float(ctx.p("ppad", self.name) or 1.0)
            P = ub[2] + h1
            hc = float(ctx.p("h_cavity", self.name) or 0.0)
            if hc <= 0:
                hc = dz_l + float(ctx.p("cpad", self.name) or 2.0)
            z_top = lb[2] + hc
            z_lo = z_top - (hc + dz_l)
            cutlift = float(ctx.p("cutlift", self.name) or 0.5)
            out.metrics["params"] = {"P": P, "z_top": z_top, "z_lo": z_lo,
                                     "h1": h1, "h_cavity": hc}
            log("  形变面 z: 上[%.2f,%.2f] 下[%.2f,%.2f]" % (ub[2], ub[5], lb[2], lb[5]))
            log("  P=%.2f  z_top=%.2f  z_lo=%.2f" % (P, z_top, z_lo))

            # ---------- 3. 按内缩量试；失败自动降级 ----------
            #   每个内缩量先试「成形到曲面」（快、几何最准），
            #   失败再试「棱柱 + 使用曲面切除」（不依赖高度场）。
            tries = [inset]
            for extra in (0.4, 1.0):
                v = round(inset + extra, 3)
                if v not in tries:
                    tries.append(v)
            last = None
            for i, ins in enumerate(tries):
                if i:
                    log("")
                    log("⚠ 内缩 %.2fmm 的两种路线都失败，退到 %.2fmm 再试"
                        % (tries[i - 1], ins))
                log("")
                log("── 路线A：成形到曲面（inset=%.2fmm）──" % ins)
                ok, err = self._build(ctx, out, log, eng, up_f, up_sh, lo_f, lo_sh,
                                      ins, cell, margin, P, z_top, z_lo, cutlift)
                if ok:
                    out.metrics.update({"inset_used": ins, "route": "upto"})
                    if ins != inset:
                        log("  （最终内缩 %.2fmm —— 精确轮廓在该零件上无法终止）" % ins)
                    out.ok = True
                    return out
                last = err
                log("")
                log("── 路线B：棱柱 + 使用曲面切除（inset=%.2fmm）──" % ins)
                log("  （路线A失败原因：%s）" % err)
                ok2, err2 = self._build_by_surface_cut(
                    ctx, out, log, eng, up_f, up_sh, lo_f, lo_sh,
                    ins, cell, margin, P, z_top, z_lo)
                if ok2:
                    out.metrics.update({"inset_used": ins, "route": "surface-cut"})
                    log("  ★ 降级路线成功（该零件的形变面不是高度场，"
                        "「成形到曲面」不适用）")
                    out.ok = True
                    return out
                last = err2
            return out.fail(last or "生成失败")
        except Exception as e:
            return out.fail("异常: %s" % e, e)
