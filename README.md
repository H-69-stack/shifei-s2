# S2 · 钣金液压成型凹凸模自动生成与修复系统

输入一个钣金件 STEP 模型，自动完成 **镜像件生成 → 凹凸模粗生成 → 模具修复 → 三件贴合自检 → STEP 交付**，
几何运算全部基于 OpenCASCADE（`OCP` 绑定），无第三方 CAD 依赖；可选走 SolidWorks COM 通道。

> 详细架构、模块职责、CLI 与 Web API 见 **[SYSTEM_README.md](SYSTEM_README.md)**（约 1080 行，改代码前请先读它）。

## 处理链

```
产品 STEP
   │
   ├─ step_reader.py ──────── 读取 / 网格化 / 特征提取（圆柱面·孔·圆角·倒角）
   ├─ brep_topology.py ────── BREP 拓扑抽取（面/边/顶点/厚度/分类）
   ├─ bend_detector.py ────── 折弯识别（二面角 + 圆角双判定）
   ├─ corner_close.py ─────── 钣金角落延伸闭合（产品预处理）
   ├─ hole_filler.py ──────── 孔洞补齐至平面
   ├─ mirror_generator.py ─── 镜像件 / 旋转件
   │
   ├─ mold_generator.py ───── 凹凸模粗生成（方案 3）
   ├─ mold_method2_generator.py  方案二：由上下形变面生成
   ├─ mold_method3_solidworks.py 方案三：SolidWorks COM 驱动
   │
   ├─ mold_fix.py ─────────── ★核心：凸模填腔 / 凹模挖腔 + 检测 + 缓存
   ├─ mold_refix.py ───────── 再修理：去产品结构体 + 凸起角落尖角补齐
   ├─ fit_checker.py ──────── 三件贴合验证（间隙 / 干涉 / 型腔忠实度）
   └─ assembly_builder.py ─── 装配吻合结构体输出（含自检）
```

两条入口：CLI（`mold_fix.py`、`moldgen.py`）与 Web 控制台（`web_box_generator/app.py`，Flask + Three.js 三维查看/测量）。

## 环境

- Windows + Python 3.10.8（项目内 `venv`）
- `cadquery 2.7.0` · `cadquery-ocp 7.8.1.1.post1` · `Flask 3.1.3` · `numpy 2.2.6`
- 可选：`pywin32` + SOLIDWORKS 2024（仅方案三）

```powershell
venv\Scripts\activate
python web_box_generator\app.py        # Web 控制台
python mold_fix.py --help              # 模具修复 CLI
python moldkit\webapp.py               # moldkit 控制台（127.0.0.1:5002）
```

## 目录

| 路径 | 说明 |
|---|---|
| `moldkit/` | 模具自动生成软件包：六个方法一方法一文件（m1/m2a/m2b/m3/m4/m5）+ 校验 + 流水线 |
| `web_box_generator/` | Flask 后端 + 前端（Three.js 查看/测量/生成/下载） |
| `MOLDKIT.md` | 模具自动生成软件使用手册（方法总览 · 架构 · 踩坑记录） |
| `SYSTEM_README.md` | 系统说明文档（架构 · 模块 · 数据流 · CLI · API） |
| `CODE_REVIEW_2026-10-17.md` | 全量代码审查（约 2.1 万行）：安全 9 项 / 性能 17 项 / 正确性 18 项 |
| `_m2_debug/` | 调试验证脚本与一次性复现用例 |

## 仓库不含几何数据

`*.stp` 等几何文件默认不入库（单件 1–3 MB、全量约 185 MB，且多为客户件）。
需要纳入某件时 `git add -f <文件>`；需要全量纳入时改用 Git LFS。

## 推送到 GitHub

本机 `hosts` 把 `github.com` 钉到了一个只读加速器（clone/ls-remote 正常，`git push` 返回 **403**）。
本仓库提供绕过脚本，直连真实 IP 推送：

```powershell
powershell -ExecutionPolicy Bypass -File tools\push-github.ps1
```

凭据取自 Windows 凭据管理器（与 git 相同），不落盘、不改系统 hosts。根治办法是在加速器里关掉 `github.com` 那条记录。

## 当前状态

- 已有：完整生成/修复/校验链路，两条渲染与两套 Web 控制台，CLI 与软件包双形态。
- 待办：按 `CODE_REVIEW_2026-10-17.md` 分四批修复（安全 → 性能 → 正确性加固 → 清理）；
  补 pytest 回归（现有对拍脚本见 `_test_p6.py` 的等价性测试写法）。
