# -*- coding: utf-8 -*-
"""方法注册表：列出 / 选择 / 推荐。"""
from .methods import ALL


def method_classes():
    return list(ALL)


def all_methods():
    return [c() for c in ALL]


def names():
    return [c.name for c in ALL]


def get(name):
    """按 name 取方法实例；支持模糊匹配（m3 / 方法三 / upto）。"""
    n = (name or "").strip().lower()
    for c in ALL:
        if c.name == n:
            return c()
    for c in ALL:
        if n and (n in c.name.lower() or n in c.label.lower()):
            return c()
    raise KeyError("没有方法 %r；可用: %s" % (name, ", ".join(names())))


def list_info(only_available=None):
    out = []
    for c in all_methods():
        d = c.info()
        if only_available is not None:
            ok, why = c.available()
            d["available"] = ok
            d["unavailable_reason"] = why
            if only_available and not ok:
                continue
        out.append(d)
    return out


def recommend(ctx):
    """按输入自动推荐方法。

    规则（都是实测出来的）：
      · 只有产品实体        → 二B（最稳最快）
      · 有上下形变面 + SW 可用 → 三（几何由 SW 内核保证）
      · 有上下形变面 + SW 不可用 → 二A
      · 产品"厚实"（厚径比大）  → 四 也合适
    """
    cands = []
    for m in all_methods():
        ok, why = m.available(ctx)
        if ok:
            cands.append(m)
    if not cands:
        return None, [], "没有可用方法（检查输入与 SolidWorks 连接）"

    # 打分
    def score(m):
        s = 0
        if m.name == "m3":
            s = 100
        elif m.name == "m2b":
            s = 95
        elif m.name == "m2a":
            s = 70
        elif m.name == "m4":
            s = 60
        return s

    cands.sort(key=score, reverse=True)
    best = cands[0]
    notes = []
    if ctx is not None and ctx.product:
        try:
            from . import geo
            b = geo.bbox(geo.read_step(ctx.product))
            dx, dy, dz = b[3] - b[0], b[4] - b[1], b[5] - b[2]
            thin = min(dx, dy, dz)
            if dz > 0 and thin / max(dx, dy, dz) < 0.1:
                notes.append("产品是薄壁件（最小尺寸 %.1fmm / 最大 %.1fmm）："
                             "方法四用水平分型面会把料切开，建议方法三/五" % (thin, max(dx, dy, dz)))
        except Exception:
            pass
    return best, cands, "；".join(notes)
