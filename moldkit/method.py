# -*- coding: utf-8 -*-
"""方法框架：任何模具生成办法都实现成一个 MoldMethod。

统一以后就能：
    · 在界面/命令行里【列出所有方法、自由选择】
    · 每个方法自己声明需要什么输入、有哪些参数
    · 产出同样的 MoldOutput，从而可以横向比较、自动推荐
"""
import os
import time
import traceback


# ---------------------------------------------------------------------------
# 参数声明
# ---------------------------------------------------------------------------
class Param:
    def __init__(self, name, label, default=None, unit="", kind="float",
                 choices=None, help=""):
        self.name = name
        self.label = label
        self.default = default
        self.unit = unit
        self.kind = kind          # float | int | bool | choice | str
        self.choices = choices or []
        self.help = help

    def coerce(self, v):
        if v is None:
            return self.default
        if self.kind == "bool":
            if isinstance(v, str):
                return v.strip().lower() in ("1", "true", "yes", "on")
            return bool(v)
        if self.kind == "int":
            return int(float(v))
        if self.kind == "float":
            return float(v)
        return v

    def to_dict(self):
        return {"name": self.name, "label": self.label, "default": self.default,
                "unit": self.unit, "kind": self.kind, "choices": self.choices,
                "help": self.help}


# ---------------------------------------------------------------------------
# 上下文
# ---------------------------------------------------------------------------
class MoldContext:
    """一次生成任务的全部输入与工作区。路径一律用绝对路径。"""

    def __init__(self, workdir, product=None, upper=None, lower=None,
                 params=None, log=print, part_name="part"):
        self.workdir = os.path.abspath(workdir)
        os.makedirs(self.workdir, exist_ok=True)
        self.product = product
        self.upper = upper
        self.lower = lower
        self.params = dict(params or {})
        self.log = log
        self.part_name = part_name
        self._t0 = time.time()
        self.artifacts = {}

    def path(self, *parts):
        return os.path.join(self.workdir, *parts)

    def p(self, name, method=None):
        """取参数（可带方法前缀覆盖，如 'm3.inset' 优先于 'inset'）。"""
        if method:
            k = "%s.%s" % (method, name)
            if k in self.params:
                return self.params[k]
        return self.params.get(name)

    @property
    def elapsed(self):
        return time.time() - self._t0


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
class MoldOutput:
    def __init__(self, method):
        self.method = method
        self.ok = False
        self.error = None
        self.punch_path = None
        self.cavity_path = None
        self.punch_shape = None
        self.cavity_shape = None
        self.metrics = {}
        self.artifacts = {}
        self.elapsed = 0.0
        self.traceback = None

    def fail(self, msg, exc=None):
        self.ok = False
        self.error = msg
        if exc is not None:
            self.traceback = "".join(traceback.format_exception(
                type(exc), exc, exc.__traceback__))
        return self

    def to_dict(self):
        d = {
            "method": self.method, "ok": self.ok, "error": self.error,
            "punch": self.punch_path, "cavity": self.cavity_path,
            "metrics": self.metrics, "artifacts": self.artifacts,
            "elapsed": round(self.elapsed, 2),
        }
        return d

    def __repr__(self):
        return "MoldOutput(%s, ok=%s, %.1fs)" % (self.method, self.ok, self.elapsed)


# ---------------------------------------------------------------------------
# 方法基类
# ---------------------------------------------------------------------------
class MoldMethod:
    name = "base"             # 稳定 id（命令行/接口用）
    label = "未命名方法"       # 中文名
    engine = "?"              # ocp | solidworks | hybrid
    needs = ()                # ("upper","lower") / ("product",)
    # ★额外输入文件★：给"输入格式和主流不同"的方法用（例如方法一要凸/凹模毛坯）。
    #   每项 {"key": 参数名, "label": 显示名, "accept": 文件后缀}
    #   界面会为它渲染一个文件输入口，上传后把服务器路径写进同名参数。
    extra_inputs = []
    desc = ""
    pros = ""
    cons = ""
    params = []

    # --- 能力/可用性 ---
    def available(self, ctx=None):
        """返回 (bool, 原因)。默认按 needs 检查输入是否给了。"""
        if ctx is None:
            missing = list(self.needs)
            if missing:
                return True, "需要输入: %s" % ", ".join(missing)
            return True, ""
        missing = [k for k in self.needs if not getattr(ctx, k, None)]
        if missing:
            return False, "缺少输入: %s" % ", ".join(missing)
        return True, ""

    # --- 生成 ---
    def generate(self, ctx):
        raise NotImplementedError

    # --- 辅助 ---
    def default_params(self):
        return {p.name: p.default for p in self.params}

    def params_dict(self):
        return [p.to_dict() for p in self.params]

    def info(self):
        return {
            "name": self.name, "label": self.label, "engine": self.engine,
            "needs": list(self.needs), "desc": self.desc,
            "pros": self.pros, "cons": self.cons,
            "params": self.params_dict(),
            "extra_inputs": [dict(x) for x in self.extra_inputs],
        }

    def __repr__(self):
        return "<%s %s>" % (type(self).__name__, self.name)


# ---------------------------------------------------------------------------
# 时间/日志辅助
# ---------------------------------------------------------------------------
class Step:
    """给日志分段计时。"""
    def __init__(self, log, title):
        self.log = log
        self.title = title

    def __enter__(self):
        self.t0 = time.time()
        self.log("")
        self.log("=" * 74)
        self.log("▶ %s" % self.title)
        self.log("=" * 74)
        return self

    def __exit__(self, *a):
        self.log("  （%s 用时 %.2fs）" % (self.title, time.time() - self.t0))
        return False
