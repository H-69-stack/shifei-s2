# -*- coding: utf-8 -*-
"""各种模具生成方法。

每个方法一个文件，都继承 MoldMethod。新增方法 = 新增一个文件 + 在下面登记。
"""
from .m1_legacy import Method1Legacy
from .m2a_surface_stretch import Method2A
from .m2b_product_boolean import Method2B
from .m3_sw_uptosurface import Method3SwUpto
from .m4_sw_product_combine import Method4SwCombine
from .m5_sw_moldtools import Method5SwMoldTools

ALL = [Method1Legacy, Method2A, Method2B,
       Method3SwUpto, Method4SwCombine, Method5SwMoldTools]

__all__ = ["Method1Legacy", "Method2A", "Method2B", "Method3SwUpto",
           "Method4SwCombine", "Method5SwMoldTools", "ALL"]
