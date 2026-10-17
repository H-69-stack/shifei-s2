# -*- coding: utf-8 -*-
"""产物后处理：把 SolidWorks 导出的东西变成能用的模具实体。

三件事，顺序不能变：
  1. normalize()     —— 壳→真实体。
        ★SolidWorks 的 SaveAs 写的是 SHELL_BASED_SURFACE_MODEL★：
          闭合壳（模具本体）+ 开放壳（没删掉的输入形变面，SW 会因为被特征引用
          而删不掉）两个壳混在一个文件里，别的软件读回来就是 0 个实体，
          后续装配/干涉检查全部失效。
  2. clearance()     —— 上模 − 下模，给上下模留出加工间隙。
        ★这不是 bug，是"竖向拉伸法"的固有特性★：上下形变面是沿【板料法向】
          偏一个板厚的，在近竖直壁处上表面的竖直投影会低于下表面的竖直投影，
          两半必然咬入（实测咬入量 ≈ 板厚 × 壁面积）。
  3. 导出 + 复核
"""
import os

from . import geo


def normalize(src, dst, log=print):
    """壳→实体并导出。返回 (ok, info)。"""
    sh = geo.read_step(src)
    solid, info = geo.to_solid(sh)
    info["src"] = src
    info["dst"] = dst
    if solid is None:
        log("  !! %s 里没有闭合壳，无法做成实体" % os.path.basename(src))
        info["ok"] = False
        return False, info
    info["valid"] = geo.is_valid(solid)
    info["volume_mm3"] = geo.volume(solid)
    info["bbox"] = list(geo.bbox(solid))
    info["counts"] = geo.counts(solid)
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    geo.write_step(solid, dst)
    info["ok"] = True
    log("  %s -> %s  实体%d 壳%d 面%d 体积=%.0f mm^3 有效=%s  (取壳方式: %s)" % (
        os.path.basename(src), os.path.basename(dst),
        info["counts"]["solids"], info["counts"]["shells"], info["counts"]["faces"],
        info["volume_mm3"], info["valid"], info.get("picked")))
    if info["counts"]["solids"] > 1:
        log("    ⚠ 归一化后是 %d 块（布尔把模体切开了）—— 已全部保留，没有丢材料" %
            info["counts"]["solids"])
    # 回读确认
    rb = geo.read_step(dst)
    info["readback_solids"] = geo.count(rb, geo.TopAbs_SOLID)
    log("    回读实体数 = %d" % info["readback_solids"])
    return True, info


def interference(punch, cavity, tol_mm3=1.0):
    """上模∩下模 的公共体积。"""
    c = geo.common(punch, cavity)
    if c is None:
        return None
    return geo.volume(c)


def clearance(punch, cavity, out=None, log=print):
    """从上模里减掉下模，消除干涉。返回 (新上模, info)。"""
    info = {"before_mm3": geo.volume(punch), "cavity_mm3": geo.volume(cavity)}
    inter = geo.common(punch, cavity)
    info["interference_mm3"] = geo.volume(inter) if inter is not None else 0.0
    log("  干涉量 = %.1f mm^3" % info["interference_mm3"])
    if info["interference_mm3"] <= 0.001:
        log("  无干涉，无需处理")
        info["after_mm3"] = info["before_mm3"]
        info["removed_mm3"] = 0.0
        info["valid"] = geo.is_valid(punch)
        if out:
            geo.write_step(punch, out)
        return punch, info

    res = geo.cut(punch, cavity)
    if res is None:
        log("  !! Cut 失败，保留原样")
        info["after_mm3"] = info["before_mm3"]
        info["removed_mm3"] = 0.0
        info["valid"] = geo.is_valid(punch)
        return punch, info

    info["after_mm3"] = geo.volume(res)
    info["removed_mm3"] = info["before_mm3"] - info["after_mm3"]
    info["valid"] = geo.is_valid(res)
    info["counts"] = geo.counts(res)
    chk = geo.common(res, cavity)
    info["recheck_interference_mm3"] = geo.volume(chk) if chk is not None else -1.0
    log("  去干涉后：体积 %.1f -> %.1f （减掉 %.1f）  有效=%s  复核干涉=%.4f mm^3" % (
        info["before_mm3"], info["after_mm3"], info["removed_mm3"],
        info["valid"], info["recheck_interference_mm3"]))
    if out:
        geo.write_step(res, out)
        log("  -> %s" % out)
    return res, info
