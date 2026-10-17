# -*- coding: utf-8 -*-
"""moldkit —— 模具自动生成工具包。

设计目标：把原来散落在各处的模具生成办法，统一成
    「统一输入 → 选一个方法 → 统一输出与校验」
并且让 SolidWorks 侧的几何能力可以自由组合。

模块：
    swjobs    —— SolidWorks 作业驱动层（操作脚本 DSL + 宏安装/执行）
    surfaces  —— 形变面预处理（缝合 / 补孔 / 投影轮廓）
    post      —— 产物后处理（壳→实体归一化 / 去干涉 / 留间隙）
    verify    —— 统一校验（实体数·壳数·BRepCheck·干涉·贴合度）
    methods   —— 各生成方法，一个方法一个文件
    registry  —— 方法注册表（列出 / 选择 / 推荐）
    pipeline  —— 端到端流水线
"""

__version__ = "0.1.0"

__all__ = ["swjobs", "surfaces", "post", "verify", "methods", "registry", "pipeline"]
