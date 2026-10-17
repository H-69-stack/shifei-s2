# -*- coding: utf-8 -*-
"""端到端流水线：统一输入 → 选方法 → 生成 → 校验 → 出报告。"""
import json
import os
import re
import time

from . import registry
from .method import MoldContext


_HEX_PREFIX = re.compile(r'^[0-9a-fA-F]{8,}[_\-]')
_EXT = re.compile(r'\.(stp|step|STL|stl)$', re.I)
_UNSAFE = re.compile(r'[^0-9A-Za-z\u4e00-\u9fff.\-]+')


def clean_part_name(name, maxlen=40):
    """把上传文件名洗成干净的零件名。

    ★要解决的问题★：界面上的文件是用 `<8位16进制>_原文件名` 存的，
    直接拿来当零件名，产物就会变成
        `092b28911a59_YA-1131-505_MIRROR_xOy_m3_PUNCH.stp`
    —— 又长又带一串 16 进制。这里把它剥掉。
    """
    n = os.path.basename(name or '')
    n = _EXT.sub('', n)
    prev = None
    while prev != n:                      # 可能有多层前缀，反复剥
        prev = n
        n = _HEX_PREFIX.sub('', n)
    n = _UNSAFE.sub('_', n).strip('_')
    if len(n) > maxlen:
        n = n[:maxlen].rstrip('_')
    return n or 'part'


def make_context(workdir, product=None, upper=None, lower=None, params=None,
                 part_name="part", log=print):
    for k, v in (("product", product), ("upper", upper), ("lower", lower)):
        if v and not os.path.exists(v):
            raise FileNotFoundError("%s 不存在: %s" % (k, v))
    if not part_name or part_name == "part":
        src = product or upper or lower or "part"
        part_name = clean_part_name(src)
    else:
        part_name = clean_part_name(part_name)
    return MoldContext(workdir, product=product, upper=upper, lower=lower,
                       params=params, log=log, part_name=part_name)


def audit(out, tol_pair_mm3=1.0, tol_prod_ratio=0.01):
    """★通用闸门★：从校验报告里挑出硬问题。

    为什么需要：几何流水线最怕"静默产出错的东西还报成功"。
    实测踩过 —— GetBodies2 顺序不稳定导致组合选错体，上下模体积从 4,193,289
    变成 91,906，日志里一路"成功"。有了这道闸门，这类结果会被判为失败。

    判定标准：
      · 上下模干涉 > 1 mm³                    → 不合格
      · 模具侵入产品 > 产品的 1%              → 不合格
      · 任一半模不是「1 实体 1 壳且 BRepCheck 有效」→ 不合格
    """
    rep = (out.metrics or {}).get("report") or {}
    probs = []

    p = rep.get("pair")
    if p and p.get("interference_mm3") is not None:
        if p["interference_mm3"] > tol_pair_mm3:
            probs.append("上下模干涉 %.3f mm^3 > %.3f" % (p["interference_mm3"], tol_pair_mm3))

    f = rep.get("product_fit")
    if f and not f.get("ok"):
        v = f.get("worst_mm3") or 0.0
        ratio = (v / f["product_mm3"]) if f.get("product_mm3") else 0.0
        if ratio > tol_prod_ratio:
            probs.append("模具侵入产品 %.0f mm^3（占产品 %.1f%%）" % (v, ratio * 100))

    for k, cn in (("punch", "上模"), ("cavity", "下模")):
        c = rep.get(k)
        if c and not c.get("ok"):
            probs.append("%s 不合格：实体%d 壳%d 有效=%s" %
                         (cn, c["solids"], c["shells"], c["valid"]))
    return probs


def run(method_name, ctx, log=None):
    log = log or ctx.log
    m = registry.get(method_name)
    ok, why = m.available(ctx)
    log("")
    log("#" * 78)
    log("# 方法: %s  (%s)" % (m.label, m.name))
    log("# 引擎: %s   输入: %s" % (m.engine, ", ".join(m.needs)))
    if not ok:
        log("# !! 不可用: %s" % why)
        log("#" * 78)
        from .method import MoldOutput
        return MoldOutput(m.name).fail("不可用: %s" % why)
    log("#" * 78)
    # ★先把方法自己声明的参数默认值灌进去★
    #   否则 ctx.p("clearance") 取不到值（None → 假），默认行为不会执行。
    for k, v in m.default_params().items():
        ctx.params.setdefault(k, v)
    out = m.generate(ctx)
    out.elapsed = ctx.elapsed

    # ★统一闸门★
    if out.ok:
        probs = audit(out)
        if probs:
            out.metrics["problems"] = probs
            for s in probs:
                log("  !! 校验不合格: %s" % s)
            out.ok = False
            out.error = "校验不合格: " + "；".join(probs)

    log("")
    log(">>> %s  →  %s%s" % (m.name, "成功" if out.ok else "失败",
                             "" if out.ok else ("（%s）" % out.error)))
    return out


def run_all(ctx, only=None, log=None):
    log = log or ctx.log
    res = {}
    for m in registry.all_methods():
        if only and m.name not in only:
            continue
        try:
            res[m.name] = run(m.name, ctx, log=log)
        except Exception as e:
            from .method import MoldOutput
            res[m.name] = MoldOutput(m.name).fail("未捕获异常: %s" % e, e)
    return res


def summarize(results, log=print):
    log("")
    log("=" * 90)
    log("%-6s %-34s %6s %8s %12s %12s %10s" %
        ("方法", "名称", "结果", "用时", "上模体积", "下模体积", "干涉"))
    log("-" * 90)
    for name, o in results.items():
        pv = cv = iv = "-"
        rep = (o.metrics or {}).get("report") or {}
        if rep.get("punch"):
            pv = "%.0f" % rep["punch"]["volume_mm3"]
        if rep.get("cavity"):
            cv = "%.0f" % rep["cavity"]["volume_mm3"]
        if rep.get("pair") and rep["pair"]["interference_mm3"] is not None:
            iv = "%.2f" % rep["pair"]["interference_mm3"]
        log("%-6s %-34s %6s %7.1fs %12s %12s %10s" %
            (name, o.method and registry.get(name).label[:32] if False else name,
             "OK" if o.ok else "FAIL", o.elapsed, pv, cv, iv))
    log("=" * 90)
    return results


def save_report(results, path):
    d = {name: o.to_dict() for name, o in results.items()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return path
