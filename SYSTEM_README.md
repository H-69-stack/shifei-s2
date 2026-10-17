# 系统说明文档（SYSTEM README）

> **本文件用途**：让 AI / 开发者在不逐行读取全部源码的前提下，快速理解本系统的架构、模块、数据流、CLI 与 Web API。
> **更新惯例**：每次对系统做较大改动后，请同步更新本文件（模块职责、关键函数签名、版本演进）。

---

## 1. 系统概述

本系统是 **钣金件液压成型凹凸模具自动生成与修复系统**（Windows + Python + OpenCASCADE）。

**核心业务**：输入一个钣金件 STEP 模型（如 `YA-1131-505`、`YA-1238-01`），自动完成：
1. 镜像件生成（左右对称件，用于制造镜像模具）；
2. 凹模（下模/型腔）/ 凸模（上模）粗生成；
3. 模具修复（凸模"填腔"、凹模"挖腔"），输出可直接交付 CAD 的 STEP 文件。

**技术底座**：全部几何操作基于 OpenCASCADE（通过 `cadquery` / `cadquery-ocp` 的 `OCP.*` Python 绑定），布尔运算（Cut / Fuse / Common）走原生 OCP API。

**两种使用入口**：
- **命令行（离线）**：直接运行各 `.py` 模块（尤其 `mold_fix.py`）；
- **Web 界面（在线）**：`web_box_generator/app.py` 启动 Flask 服务，浏览器上传/查看/测量/生成/修复/下载。

---

## 2. 运行环境

| 项目 | 值 |
|---|---|
| 操作系统 | Windows（注意控制台 GBK→UTF-8 问题，模块内已有 `_force_utf8()` 处理） |
| Python | 3.10.8（项目自带 `venv` 虚拟环境） |
| 关键依赖 | `cadquery 2.7.0`、`cadquery-ocp 7.8.1.1.post1`（OCP.*）、`Flask 3.1.3`、`numpy 2.2.6`、`matplotlib`、`ezdxf` |
| 可选依赖 | `pywin32 312` —— 仅方法三（SolidWorks COM）需要，已装入项目 venv |
| 外部软件 | SOLIDWORKS 2024 SP5（可选，仅方法三用；须已安装且可用许可证） |
| 虚拟环境 | `venv\Scripts\activate` 后运行 |

> 进入虚拟环境：`venv\Scripts\activate`（PowerShell）或 `venv\Scripts\activate.bat`（cmd）。

---

## 3. 目录结构

```
S1/
├── SYSTEM_README.md              ← 本说明文档
├── step_reader.py                # STEP 读取 + 网格导出 + 特征检测
├── brep_topology.py              # Step1: BREP 拓扑抽取（面/边/顶点/厚度/分类）
├── bend_detector.py              # Step2: 折弯识别（二面角 + 圆角双判定）
├── slice_analyzer.py             # 网格切片毛料计算（MeshSlicer）
├── mirror_generator.py           # 镜像与旋转模型生成（镜像+绕 x/y/z 轴旋转）
├── hole_filler.py                # ★新增★ 孔洞补齐（自动识别所有孔洞并补齐至平面、无超出）
├── mold_generator.py             # 凹凸模粗生成（方案3）
├── mold_fix.py                   # ★核心★ 模具修复（凸模填充/凹模挖腔 + 检测 + 缓存）
├── corner_close.py               # ★新增★ 钣金角落自动延伸闭合补齐（产品预处理：镜像后、模具前）
├── fit_checker.py                # ★新增★ 三件贴合验证（上模→产品→下模 无间隙/无干涉/型腔忠实）
├── mold_refix.py                 # ★新增★ 再修理：去除产品结构体 + 上模凸起角落尖角自动补齐（延伸求交）
├── assembly_builder.py           # ★新增★ 装配吻合结构体输出：再修理后的上模+产品+再修理后下模（同框架拼合+自检）
├── mold_contact_surface_extractor.py # ★新增★ 形变配合面提取（【根据产品】：凸模=产品上表面 / 凹模=产品下表面，不需生成模具）
├── product_surface_classifier.py # ★新增★ 产品上/下表面分类独立模块（【投影法】：向面A/面B投影，能投影出面积者=上/下表面）
├── mold_method2_generator.py # ★新增★ 模具生成法二（由上下形变面生成上模=竖直拉伸至面P / 下模=基座挖柱体空腔+形变面垫底）
├── mold_method3_solidworks.py # ★方法三★ SolidWorks COM 二次开发底座（COM 管线已验证，见 4.9d）
├── mold_method3_macro.txt     # 方法三旧版单用途 VBA 宏（已被 mold_sw_engine.txt 取代）
├── mold_sw_engine.txt         # ★★SolidWorks 通用操作解释器（VBA）★★ 加新方法不用改它，见 4.9e
├── moldgen.py                 # ★★模具自动生成 CLI 入口★★（list/recommend/run/compare/install-sw）
├── MOLDKIT.md                 # ★★模具自动生成软件 使用手册★★（方法总览·架构·全部踩坑）
├── moldkit/                   # ★★模具自动生成软件包★★（见 4.9e）
│   ├── swjobs.py              #   SolidWorks 作业驱动层（操作脚本 DSL + 装宏/跑宏）
│   ├── geo.py                 #   OCC 基础：读STEP/量/布尔/壳→实体/变换
│   ├── surfaces.py            #   形变面预处理：缝合 / 补孔 / 投影轮廓
│   ├── post.py                #   后处理：壳→实体归一化 / 去干涉
│   ├── verify.py              #   统一校验：结构·尺寸·干涉·贴合度
│   ├── method.py              #   方法基类 + 上下文 + 统一输出 + 参数声明
│   ├── registry.py            #   方法注册表：列出 / 选择 / 推荐
│   ├── pipeline.py            #   端到端流水线
│   ├── webapp.py              #   Web 控制台（127.0.0.1:5002）
│   └── methods/               #   六个方法，一个方法一个文件（m1/m2a/m2b/m3/m4/m5）
├── mold_fix_backup_20260815_200541.py  # mold_fix 旧版备份
├── BoxGenerator.py               # tkinter 小工具：生成长方体 STEP/STL
├── web_box_generator/
│   ├── app.py                    # Flask 后端（所有 Web API）
│   ├── templates/index.html      # 前端（Three.js 三维查看/测量/操作）
│   └── uploads/                  # 上传文件（以 {12位hex}_{原名} 存储）及输出子目录
├── mold_output/                  # MoldGenerator 输出的 CAVITY / PUNCH 模具
├── fix_output/                   # MoldFixer 修复后的模具（含 annotation/ 说明txt）
├── mold_fix_cache/               # 几何状态缓存 JSON（.geom_cache.json）
├── _repro3_fixed/                # 复现/调试用的修复结果
└── *.stp                         # 产品/模具 STEP 数据文件（YA-1131-505 / YA-1238-01）
```

**数据文件命名约定**（贯穿全系统）：
- `{产品名}_MIRROR_{平面}.stp` —— 镜像件（如 `YA-1131-505_MIRROR_xOy.stp`）
- `{产品名}_ROTATE_{轴}_{CW/CCW}_{角度}deg.stp` —— 旋转件（如 `YA-1131-505_ROTATE_x_CW_30deg.stp`）
- `{产品名}_HOLE_FILLED.stp` —— 孔洞补齐件（所有孔洞已补齐至平面、无超出）
- `{产品名}_CAVITY.stp` —— 凹模（下模）
- `{产品名}_PUNCH.stp` —— 凸模（上模）
- `{产品名}_PUNCH_FILLED.stp` / `_CAVITY_HOLLOWED.stp` —— 修复后的模具

---

## 4. 模块详解

### 4.1 `step_reader.py` —— STEP 读取 / 网格化 / 特征提取

**类 `StepReader`**（构造参数：`linear_deflection=0.1`, `angular_deflection=0.1`，单位 mm/rad，越小越精细）

| 方法 | 作用 |
|---|---|
| `read(filepath) -> dict` | 读取 STEP，返回完整模型数据（见下方结构） |
| `export_mesh_json(filepath)` | 导出三角网格 JSON（前端查看用） |
| `export_features_json(filepath)` | 导出特征 JSON（圆柱面/孔/圆角/倒角等） |

**`read()` 返回结构**：
```python
{
  'filename': str, 'filepath': str,
  'solids': [{ 'name', 'faces': [...], 'edges': [...], 'color',
              'vertices': [...扁平顶点], 'triangles': [...索引],
              'physical_properties': {'volume','surface_area','center_of_mass'},
              'face_params': [{'surface_type','area',...}],
              'features': {'cylindrical_faces','torus_faces','conical_faces','fillets','chamfers'} }],
  'total_solids','total_faces','total_edges','total_triangles',
  'bbox': {'min','max','size','center'},
  'stats': {...}
}
```
- 用 `BRepMesh_IncrementalMesh` 网格化；`BRepAdaptor` 识别曲面/曲线类型（Plane/Cylinder/Cone/Sphere/Torus/BSpline…）。
- 圆柱面→潜在孔、环面→圆角、锥面→倒角 的启发式特征检测。

**类 `StepFileScanner`**：`scan(directory)` 扫描目录下 `.stp/.step` 文件列表。

> ⚠ 模块 `__main__` 中硬编码了测试目录 `d:\Users\liyis\...`（他人电脑路径），直接运行会找不到文件——仅作开发参考。

---

### 4.2 `brep_topology.py` —— BREP 拓扑抽取（流水线 Step 1）

**类 `BrepTopologyExtractor`**：`extractor = BrepTopologyExtractor("model.stp"); topo = extractor.extract()`

`extract()` 从 STEP 原生 BREP 提取：
- 所有 Face / Edge / Vertex 及其几何参数（法向、面积、长度、曲面/曲线类型）；
- **面-边邻接关系**：每个面的 `bounding_edges`、`adjacent_faces`（哪两个面共享边）；
- **统一板厚 `thickness`**：基于"邻接平行反向面对面"检测；
- **面片分类 `classification`**：`planar_faces`（平面）/ `cylindrical_faces`（圆柱面）/ `fillet_faces`（圆角/折弯过渡面）/ `other_faces`；
- **`base_face_idx`**：面积最大的平面 = 基准底板。

**topo 字典**：
```python
{
  'faces': [{'face_idx','surface_type','area','bounding_edges','adjacent_faces',
             'surface_params': {...}, 'surface': {...}}],
  'edges': [{'edge_idx','curve_type','length','vertices', 'faces': [...]}],
  'vertices': [...], 'thickness': float, 'thickness_pairs': [(fi,fj),...],
  'classification': {...}, 'base_face_idx': int, 'bbox': {...}
}
```

---

### 4.3 `bend_detector.py` —— 折弯识别（流水线 Step 2）

**类 `BendDetector`**：`detector = BendDetector(topo); result = detector.detect()`

判定方法（双判定，纯拓扑，不受网格采样间隙干扰）：
1. 两个**平面**共享公共 Edge + 中间存在连续**圆角(Blend)面** → **直角折弯边**；
2. **圆柱柱面**两侧衔接平面法兰 → **滚弯成型弧段**。

`detect()` 返回：
```python
{
  'bends': [{ 'plane_a','plane_b','fillet_face','fillet_surface_type',
              'bend_angle_deg','bend_radius','bend_axis_direction','bend_axis_origin',
              'tangent_edges_a','tangent_edges_b','shared_edges_between_planes',
              'bend_type', 'detection_method' }],
  'free_edges': [{'reason','...' }],   # 自由边（无相邻面/无圆角 → 外轮廓切割边，已过滤）
  'bend_graph': {...},                 # 折弯邻接图（连通性检查用）
}
```
- `summarize(result)` 生成人类可读报告。可计算折弯二面角 θ、内半径 R。
- 内部把钣金面分为正面/背面两侧（`_assign_sheet_sides`）、构建厚度面对映射。

---

### 4.4 `slice_analyzer.py` —— 网格切片毛料计算

**类 `MeshSlicer`**（`direction=(dx,dy,dz)` 切片方向，`origin` 参考点）：
- `add_solid(vertices, triangles)` / `add_solids_from_raw(raw)`：加载网格（与 `StepReader.read()` 输出兼容）；
- `slice(num_slices, margin)`：沿方向生成等距平行平面与三角网格求交 → 各截面轮廓弧长；
  取**最大弧长**作为"拉直/展开长度"（毛料尺寸估计）；
- `intersect_plane(plane_origin, plane_normal)`：单平面求交，返回 `contours_3d / contours_2d / arc_lengths / best_length`；
- 静态方法 `fit_plane_from_points(points)`：最小二乘平面拟合。

> 典型用法：取钣金件包围盒最短轴为切片方向（厚度方向），计算展开尺寸。

---

### 4.5 `mirror_generator.py` —— 镜像与旋转模型生成

**类 `MirrorGenerator`（镜像 + 旋转 双能力）**：

**（A）镜像**：
- 支持平面：`xOy`（Z 镜像）、`xOz`（Y 镜像）、`yOz`（X 镜像）；
- `generate(filepath, plane='xOy', output_dir=None, center_on_centroid=True, product_name=None) -> dict`：
  - `center_on_centroid=True`：镜像面过**模型质心**（质心由体积属性算，退化时用包围盒中心）；
  - 输出 `{name}_MIRROR_{plane}.stp`；
  - 返回 `{output_path, plane, plane_label, centroid, bbox}`。
- 实现：`BRepBuilderAPI_Transform` + `gp_Trsf.SetMirror`。

**（B）旋转（新增）**：
- 支持轴：`x`（X 轴）、`y`（Y 轴）、`z`（Z 轴）；角度单位**度**；方向 `clockwise`（顺时针/逆时针）；
- `rotate(axis='z', angle=30.0, clockwise=True, center_on_centroid=True) -> TopoDS_Shape`：绕指定坐标轴旋转，轴默认过模型质心（`center_on_centroid=False` 时过原点），遵循右手定则（CCW=绕 +轴向正角，CW=负角）；
- `generate_rotation(filepath, axis='z', angle=30.0, clockwise=True, output_dir=None, center_on_centroid=True, product_name=None) -> dict`：
  - 输出 `{name}_ROTATE_{axis}_{CW/CCW}_{deg}deg.stp`；
  - 返回 `{output_path, axis, axis_label, angle, direction(CW/CCW), centroid, bbox}`。
- 实现：`BRepBuilderAPI_Transform` + `gp_Trsf.SetRotation`（`gp_Ax1` 轴过质心）。
- `__main__` 自检：对 x/y/z 三轴 × 顺/逆时针各旋转 30° 并导出。

---

### 4.5b `hole_filler.py` —— ☆孔洞补齐（自动识别孔洞并补齐至平面、无超出）

**类 `HoleFiller`**：
- `load(filepath)`：导入 STEP 并提取第一个 Solid；
- `detect_holes() -> list`：识别所有孔洞（圆形贯穿孔/盲孔），返回孔壁信息列表（已去重）；
- `fill(filepath, output_dir=None, product_name=None, max_holes=1000) -> dict`：补齐所有孔洞并导出 `{name}_HOLE_FILLED.stp`；
  - 返回 `{output_path, holes, hole_count, filled_count, skipped_count, volume_before, volume_after, bbox, valid}`。

**识别原理**（关键——不误判折弯圆角）：
- 遍历所有【完整圆柱面】（`GeomAbs_Cylinder` 且 U 参数跨度 ≈ 2π）作为【孔壁】；
  - 折弯圆角/加强筋是**部分圆弧**（U 跨度 < 2π），自动排除；
  - 外凸台虽是完整圆柱，但填充其精确包围柱为**无害空操作**（已被实体占据），不改变几何。

**补齐（贴合、无超出）**：
- 每个孔壁取包围盒沿轴线投影 → 两端面位置 `t_min/t_max`（= 法兰面/孔底）；
- 构造 `BRepPrimAPI_MakeCylinder(轴, 半径, t_max-t_min)`，两端面恰好落在钣金面上 → **与两面平齐、不超出**；
- `BRepAlgoAPI_Fuse` 逐孔融合（失败跳过并计数），`BRepCheck` 校验有效。

**实测**（合成板 3 × 贯穿孔）：识别 3、补齐 3，补齐后体积=整板（孔全消、无超出），BRepCheck 有效，重载为 1 实体；91 处折弯圆弧不会被误识别为孔。

---

### 4.6 `mold_generator.py` —— 凹凸模粗生成（方案3）

**类 `MoldGenerator`**（输入产品 STEP，输出凹模+凸模两个 compound STEP）：

- `load_product(filepath)`：导入产品并**平移到质心**为原点；
- `detect_top_bottom_plane()`：`Z_A = 包围盒 Zmax`（顶面/分模面），`Z_B = Zmin`（底面）；
- `generate(filepath, side_margin=5.0, base_thickness=10.0, output_dir, product_name) -> dict`：
  - **凹模（下模）**：`Cut(长方体[Z_B−base_thickness .. Z_A], 产品)` → compound `[带型腔基座 + 产品]`；
  - **凸模（上模）**：`长方体[Z_A .. Z_A+base_thickness]`（产品上移0.1mm刺入做补腔）→ compound `[长方体实体块 + 产品]`；
  - 输出 `{name}_CAVITY.stp` / `{name}_PUNCH.stp`；
  - 返回 `{cavity, punch, info: {product_size, Z_A, Z_B, cavity_size, punch_size, parting_z, ...}}`。
- 关键：产品经居中平移，分模面即 `Z_A`；生成的模具是"基座+产品"两结构体 compound，**修复阶段**再加工。

---

### 4.7 `mold_fix.py` —— ★核心★ 模具修复（v42，系统最复杂模块）

**定位**：对 `MoldGenerator` 生成的"基座+产品"compound 模具做修复，输出可直接交付的结构。
**关键原则**：产品始终作为**独立结构体**保留，模具结构体需干净（凸模 = 基座+凸起台+产品；凹模 = 型腔+产品）。

> **v42 关键变更**（配合 fit_checker 三件贴合验证）：
> - 凹模挖空 `open_offset` 由 0.5mm → **0**：型腔顶面与分模面 Z_A 齐平，消除产品顶部 0.5mm 外露带；
> - 凹模新增【凸起空间清空】：用 `_build_full_protrusion` 重建凸起（同一进程内优先用 fill 生成的精确凸起 `self._last_protrusion`），`Cut(型腔, 凸起)` 杜绝【凸起↔型腔】干涉（修复前实测重叠 18k mm³）；
> - 凸起构造流水线提取为 `_build_full_protrusion()`（fill 与 hollow 共用，消除重复代码）；

#### 4.7.1 凸模修复 `fill()`

```python
fixer.fill(filepath, product_filepath=None, output_dir=None, output_name=None,
           cache_dir=None, no_cache=False, force=False)
```
- **输出**：compound `[基座, 凸起台, 产品]` —— 三个独立结构体，互不重叠；
- **凸起** = 产品朝上外表面（主板顶面/翻边环面/加强筋/Boss 圆柱面/球面等）沿 +Z 拉伸到分模面的并集，再 `Cut(凸起, 产品)` 贴合产品上表面；
- 内部处理：
  1. `_build_protrusion_core()` 补凹坑/圆盘空腔（每个内孔 = 一个凹坑开口，贴合曲面）；
  2. `_build_fill_body()` 逐面填充（回退方案）；`_make_fill_plate()` 纯平板回退；
  3. 补充边缘特征（翻边/斜面/凸台）沿 +Z 拉伸并 Fuse；
  4. `_fill_internal_voids()` 填实内部封闭空腔；`_consolidate_protrusion()` 只保留体积最大的主凸起台，**丢弃布尔残片**；
  5. **去产品重叠**（v31）：输出前 `Cut(凸起, 产品)`；
  6. **去底部薄鳍**（v33）：精确切除薄鳍自身包围盒到产品上表面（0.01mm 余量，不越界）。

#### 4.7.2 凹模修复 `hollow()`

```python
fixer.hollow(filepath, product_filepath=None, output_dir=None, output_name=None,
             cache_dir=None, no_cache=False, force=False,
             method='solid3d', clearance=0.1, trim_edge=False)
```
- **输出**：compound `[型腔(1实体), 产品]`；
- `method='solid3d'`（默认）：`Cut(基座(顶面=分模面-0.5mm), 产品)` 得到**随形型腔**（型腔底面=产品下形变面，完全贴合；侧壁精确贴合产品外侧），再 `Cut` 掉产品上表面以上、产品轮廓以内的安装间隙体；
- `method='prisms'`（备选）：旧直筒切除（下形变面柱/井柱）；
- `clearance`：配合间隙/收缩率（占位参数，当前 OCP 偏置不可用 → 完全贴合）；
- `trim_edge=False`（默认）：不切外边缘，保留产品周围的基座模具壁/边框；
- `_consolidate_hollow_result()`：只保留体积最大的型腔主体，丢弃产品足迹内+分模面以下的残留碎片 → 恒为 2 结构体。

#### 4.7.3 快速检测（check-only，不写文件）

- `check(filepath, ...) -> {'result': 'PASS'|'FAIL', 'protrusions': [...], 'cached': bool}`：凸模残留凸起数学检测（布尔交集）；
- `check_cavity(filepath, ...) -> {'result', 'residuals', 'cached'}`：凹模型腔残留检测；
- 只做数学层面交集检测，绝不导出/渲染，可节省约 80% 等待时间。

#### 4.7.4 几何状态缓存（JSON，`mold_fix_cache/`）

- 文件名：`{输入名}.geom_cache.json`；schema=1；
- `_input_fingerprint()`：文件 size + mtime + 前 1MB sha256 前缀 → 判断输入是否变化；
- 字段：`last_check` / `last_fix` / `last_cavity_check`（检测结果 + 残留坐标 + 时间戳）；
- 指纹未变且残留仍原位 → 跳过重新导入巨大 STP / 重复布尔计算（实测 52s → 1.9s）。

#### 4.7.5 辅助函数（模块级，理解时可快速定位）

| 函数 | 作用 |
|---|---|
| `_force_utf8()` | Windows 控制台与 stdout 强制 UTF-8（中文不乱码） |
| `_extract_solids(filepath)` | 导入 STEP，按体积降序提取独立 Solid/Shell |
| `_extract_base_and_product()` | 从模具 compound 分离【基座(体积大)】与【产品(体积小)】 |
| `_ensure_base_parting()` | 基座底面平齐校验 |
| `_classify_surfaces(product)` | 产品面分类：上表面(upper) / 下表面(lower) |
| `_find_fill_face()` / `_find_footprint_face()` | 找主板面 / 产品 XY 足迹面 |
| `_slice_outline_face()` / `_footprint_outline_face()` | 外轮廓线提取 |
| `_make_column()` / `_make_fill_plate()` / `_build_fill_body()` | 凸起构造 |
| `detect_bottom_fins()` / `_fin_cut_box()` / `_fix_fin_local()` / `_remove_bottom_fins()` | 凸模薄鳍检测与精确切除 |
| `detect_cavity_fins()` / `_remove_cavity_fins()` | 型腔小薄片检测与去除（**可选工具，默认不自动启用**，见第 9 节注意事项） |
| `_hollow_cavity_solid3d()` / `_hollow_cavity_prisms()` | 凹模挖腔两方案 |
| `_merge_cavity_top_openings()` / `_build_product_footprint_face()` | 凹模顶面开口合并（清除分隔开口的"连接条"，默认开启） |
| `_top_projection_is_clear()` | 校验型腔顶面投影带是否已全部挖空（0=达成，写入 `top_projection_clear`，仅浅带） |
| `_demold_compat_check()` | ★竖直脱模/完全挖空校验★：产品沿 +Z 上移扫描，与型腔全程布尔交集应≈0（用户提出的验证思想）。检出残留条带/倒扣/未挖净时返回 FAIL + 重叠位置（bbox/质心）。`--verify-demold` 集成 |
| `_consolidate_protrusion()` / `_consolidate_hollow_result()` | 结构体整合 |
| `_do_cut/_do_fuse/_do_common/_make_compound/_export` | 布尔运算与导出封装 |

#### 4.7.6 输入自动识别 `_detect_op(filepath)`

- 产品顶面 ≈ 基座顶面 → `hollow`（凹模）；
- 产品顶面 ≈ 基座底面 → `fill`（凸模）。

---

### 4.8 `mold_refix.py` —— ★再修理★（在第一次修复输出之上二次修复）

**定位**：对 `mold_fix.py` 的第一次修复输出（`PUNCH_FILLED` / `CAVITY_HOLLOWED`）做两项再修复并输出新 STEP。

- **需求 1：去除产品结构体** —— 输入 compound 内【产品】独立剔除：
  - 上模 `[基座, 凸起台, 产品]` → `[基座, 凸起台]`；
  - 下模 `[型腔, 产品]` → `[型腔]`；
  - 产品实体按体积降序下标识别（上模默认 `2`、下模默认 `1`），并校验上/下模产品体积一致（差≈0）。
- **需求 2：上模凸起台角落尖角自动补齐（延伸求交）**：
  - 自动识别凸起台**外缘角落**的"微小空缺"细面簇（用"到 XY 中心的距离 > 阈值"判定外缘，适配扇形/跑道形等任意轮廓，不再受 z 高度限制）；
  - 从簇邻接的弯曲大面中，按**朝外法向对向 + 边界边端点最接近**选出构成本角落的两块侧曲面（两条大圆弧/大曲边）；
  - 沿两条曲边在角落端点处的**切线方向**延长 → 两射线求交得**尖角顶点**；
  - 以尖角+两端点构造楔形块（`MakePolygon`/`MakeFace`/`MakePrism`），并**把楔形底边沿曲边压进墙体**、**裁剪回模具包围盒**（避免 z 向伸出成游离薄片）→ `Fuse` 融合为单一实体；
  - 每个角落只填一次（先对原始凸起解析几何再依次融合，避免同角反复补料），输出保持单一连续封闭实体。
- **需求 3：下模型腔角落凸起切除（默认执行）**：
  - 下模型腔是上模凸起台的互补：凸起台同位置的"空缺"在型腔里是"凸起"（多出来的薄片/薄翼）。
  - **★识别不准/过度切除的根因与修复（2026-08-28）★**：若让型腔"自己"解析 apex，会选中**横跨整件模具的周界条带**（型腔比凸起台更宽，边界边长达数百 mm），导致 apex 外偏、楔块过大，把贴合产品的角落一并切掉、破坏三件贴合（实测每角切 ~1200mm³、角落现 3.1mm 空隙）。
  - 修复：**型腔薄翼 = 上模凸起台缺口的互补体**（二者同坐标系、角落同位），故在需求 3 前先解析上模凸起台角落几何，构造 `上模 loft 楔块 − 凸起台本体 = 新增材料实体`，再用 `BRepAlgoAPI_Cut` 从型腔中切除该实体 —— 切除量严格等于上模补齐量（1131 实测每角 ~268mm³，原为 1200mm³）。这样既得到锋利正常尖角，又不伤及贴合产品的曲面。
  - 每个角落只切一次；`BRepCheck_Analyzer` 校验合法 + 体积/实体数校验，失败逐一记录、不中断。默认开启，可用 `--no-cavity-trim` 关闭。
  - **贴合验证注意事项**：`fit_checker.py` 需要识别上模 compound 内的"凸起台"实体，而需求 1 已把产品剔除（上模仅剩 2 个实体），此时 fit_checker 无法再用凸起台覆盖产品面，会误报产品底/侧壁出现 ~2mm 全局空隙（该空隙本由凸起台填补）。**正确验证**：把 refix 前的产品重新并入得上模`[基座,凸起台,产品]`、下模`[型腔,产品]`后跑 fit_checker；1131 实测 `gap_points=0`、四处干涉均=0，`--verify-fit` PASS。
- **技术底座**（OCP 7.8.1.1）：`BRepBuilderAPI_MakeWire/MakeFace/MakePolygon/MakeSolid/MakePrism`、`BRepAdaptor_Curve/BRepLProp_CLProps`（切线）、`BRepAlgoAPI_Fuse/_Cut/_Common`、`BRepBuilderAPI_Sewing/ShapeFix_Shape/BRepCheck_Analyzer`（缝合/校验）。
- **输出**：`{输入名}_REFIXED.stp` + `REFIX_report.txt/.json`（尖角坐标/楔块体积）。
- **可调**：`--radial-frac`（外缘判定阈值）、`--wall-area`、`--cluster-dist`、`--max-corners`、`--wedge-depth`（楔块拉伸厚度）。

**CLI**：
```bash
venv\Scripts\python mold_refix.py --punch fix_output\..._PUNCH_FILLED.stp \
    --cavity fix_output\..._CAVITY_HOLLOWED.stp --output-dir refix_output
# 只做需求1（默认，角落补齐已关闭）：--wedge-depth/楔块调参已无作用；显式启用需求2：--corner-fill
```

**可调参数**：`--sliver-area`/`--wall-area`/`--cluster-dist`/`--z-cap-frac`（角落识别阈值）、`--max-corners`、`--wedge-depth`（楔块拉伸厚度，默认自动）。

---

### 4.9 `BoxGenerator.py` —— 长方体生成小工具（tkinter 独立 GUI）

- 输入长/宽/高 → 生成 `.stp`（或 `.stl`）并保存；
- `StepBoxWriter`：**纯 Python** ISO 10303-21 STEP AP214 长方体 B-Rep 生成器（无 OCC 依赖时的兜底）；
- `StlBoxWriter`：纯 Python ASCII STL 生成器；
- 若环境装有 pythonocc-core（`OCC.*`）则优先用 `BRepPrimAPI_MakeBox`。
- 无 GUI 环境调用：`StepBoxWriter(L,W,H).write(path)` 可直接在代码中使用（Web 后端 `/generate` 用到）。

---

### 4.9b `mold_contact_surface_extractor.py` —— ★形变配合面提取（根据产品）★

**定位**：提取与模具配合的【形变面】。⚠️ 用户 2026-09 修正逻辑：**不再从生成的模具中提取**，而是直接**根据产品**提取——钣金产品的表面形变区域本身就是模具型腔/凸起台曲面的直接来源，因此只需读产品 STEP，无需先生成并修复模具。

**几何定义**：
- `extract_punch(filepath)`（filepath=产品STEP）→ 凸模配合面 = 产品【上表面】(upper)；
- `extract_cavity(filepath)`（filepath=产品STEP）→ 凹模配合面 = 产品【下表面】(lower)。

**上下表面识别（投影法，独立模块 `product_surface_classifier.py`）**：
- 设【较高面 A】= z=Zmax+margin、【较低面 B】= z=Zmin-margin（均平行 xOy）；
- 上表面 = 沿 +Z 向面A 投影**能投影出面积**的面；下表面 = 沿 -Z 向面B 投影**能投影出面积**的面；
- 数学等价：投影面积 = 面面积 × |朝外法向 Z 分量|（多点采样平均，稳健）；近竖直侧壁 |nz|≤0.1（`nz_eps` 默认 0.1，剔除边缘近竖直微斜小片）→ 归为 side，**排除**。

**实现**：用 `MoldFixer._extract_solids` 加载产品、`classify_product_surfaces` 投影法分上/下表面，直接取该面集为配合面（每片覆盖率=100%）。

**输出**：`{stem}_PUNCH_CONTACT_FACES.stp` / `{stem}_CAVITY_CONTACT_FACES.stp`（Compound，仅面）+ 对应 JSON + 汇总 `contact_surface_report.json`。

**CLI 示例**：
```bash
# 同时提取凸/凹模配合面（同一产品分别取上/下表面）
python mold_contact_surface_extractor.py --punch YA-1131-505_MIRROR_xOy.stp \
    --cavity YA-1238-01_MIRROR_xOy.stp --output-dir contact_surface_out --show-summary
```

> ⚠️ 输入是【产品】STEP；`--tol`/`--ratio` 为保留参数（新逻辑恒 100% 贴合，不再使用）。

---

### 4.9c `mold_method2_generator.py` —— ★模具生成法二★（独立新功能，由形变面生成上/下模）

**方向设定**（用户 2026-09）：上方=+Z，下方=-Z，水平面方向=垂直于 Z 轴（xOy 平面）。

**输入**：上/下【形变面】STEP（`mold_contact_surface_extractor` 基于产品提取的 `*_PUNCH_CONTACT_FACES.stp`=产品上表面、`*_CAVITY_CONTACT_FACES.stp`=产品下表面，均为 Compound 仅含面）。

**上模（PUNCH_M2）**：导入上形变面 → 把每个点沿 +Z 拉伸填充至同一高度面 **P**（平面平行 xOy，`P=上形变面最低点z+h1`，h1 可设定/自动推荐=上形变面高度）→ 顶面=平面 P、底面=上形变面、侧面竖直 → 导出。

**下模（CAVITY_M2）**：
1. 导入下形变面 + 下模基座长宽高（自动推荐≥产品长宽高、可手动调整）；
2. 基座与下形变面沿 Z 对齐（xOy 投影中心均在 Z 轴上）；
3. 在基座上顶面向下挖出柱体空腔（上下底面=下形变面在基座上顶面的投影轮廓；柱体高 `h_cavity` 自动推荐=下形变面高度）；
4. 下形变面沿 -Z 竖直放入空腔（最低点恰好接触空腔最低点）；
5. 填充下形变面以下的空腔（型腔底面=下形变面，其上为开口空腔）→ 导出。

**几何等价实现**（OCP）：上模=`Cut(拉伸(形变面,+Z至远高于P), z>P上半空间)`；下模=`Cut(基座长方体, 挖空体)`，挖空体=`Common(下形变面沿+Z拉伸空间V_up, box[zmin..z_top])`，`z_top=zmin+h_cavity`、`zmin=下形变面最低点`（最低点接触空腔底），基座 xy 中心=下形变面中心。

**CLI 示例**：
```bash
python mold_method2_generator.py --upper xxx_PUNCH_CONTACT_FACES.stp \
    --lower xxx_CAVITY_CONTACT_FACES.stp --output-dir mold_method2 --show-summary
# 可选 --h1 30 --h-cavity 20 --base-lx/-ly/-hz --margin 5 --no-export-step
```

**Web**：`/api/generate_mold_method2`（upload 上/下形变面后 POST）+ `/api/download_mold_method2`；前端面板「🛠 模具生成法二」。

**★法二B（推荐·稳定单壳有效·贴合）★**：逐面拉伸开放形变面会残留内腔/薄片（多壳）。改用**完整产品实体 + mold_fix 成熟内核**做成「贴合又稳定单壳」：
- 下模 CAVITY_M2 = `Cut(基座盒[Z_B−base_thk .. Z_A], 产品实体)` → 型腔底面贴合产品下表面；
- 上模 PUNCH_M2 = `Cut(产品外轮廓柱, 产品实体)`（`mold_fix._build_full_protrusion`，顶面=Z_A+h1）→ 贴合产品上表面的随形凸起（翻边/凹坑/圆盘/贯穿孔正确处理），整合为单一主体。
- 产物强制 **1实体1壳 + BRepCheck 有效**（否则报错不产坏件）。
- CLI：`--product <产品.stp>`（自动 h1=产品高、基座长宽≥产品+margin、下模高=垫厚+型腔高；可 `--h1/--base-lx/-ly/-hz/--margin` 微调）。实测 1131：上模 1壳224面、下模 1壳140面，均有效。

#### 4.9c-1 ★法二修复（2026-09-11，三个真实缺陷 + 实测数据）★

**缺陷 1：法二B 上模在 CLI/库调用下 100% 静默退化成矩形块。**
`mold_fix.py` 在几何流程内部大量 `print`，字符串含 `³ ² ↔ − ▶ ✓` —— Windows 控制台默认 GBK 编不出来 → `print` 抛 `UnicodeEncodeError` → 异常从布尔构造中途冒出 → 上层 `except` 判"构造失败" → 回退成 `250×60` 长方体。
证据：`punch['details']['src'] == 'block'`（三个产品全中）；报错文本 `'gbk' codec can't encode character '\xb3'`。
Web 端因 `app.py:24-33` 全局 `reconfigure(utf-8)` 才没暴露，**任何库方式调用都会中招**。
修复：`mold_fix.py` 装安全 `print`（打印永不抛异常，不可编码字符降级 `?`），并提供 `install_safe_print(globals())` 给 `mold_method2_generator.py` / `mold_contact_surface_extractor.py` 复用。验证：强制 `stdout=gbk` 时 `src == 'protrusion'`（不再退化）。

**缺陷 2：法二A（形变面路径）顶面裁剪失效。**
`_stretch_up` 旧式拉伸高度 `big=(z_end-bb[2])+|bb[5]-bb[2]|+5` 会把高处的面拉到远超 `z_end`（实测 2ROM 拉到 z=319，而裁顶盒只到 196）→ 盒盖不住，残留 248mm 柱体。
修复：拉伸高度改为 `z_end - bb[5] + 2`（顶面恰落 z_end）。
另：下模 `Common(开放壳, box)` 也会外甩（顶面超出基座 +3.2~6mm），且**再 Cut 一次同样失败**（OCP 对开放壳布尔不可靠）→ 改为**事后校验 + 诚实报错**，绝不交付错件：
`上模顶面未平齐高度面 P（期望 71.000，实际 73.626）` / `下模超出基座范围（实际 z[...,72.239] vs 基座 z[...,69.000]）——请改用【法二B：直接传产品实体】`。

**缺陷 3：2ROM31-6061 这类深槽件，法二B 上模只覆盖 22%。**
`_build_full_protrusion` 是"单一最大主板面"假设：柱体足迹只按该面轮廓拉，且 `_consolidate_protrusion` 把"Fuse 后仍不相连"的合法凸起块当布尔残片丢弃（1131 丢 6 块、1238 丢 8 块）。平板件没事，深槽件崩。
新增 `MoldMethod2Generator.build_punch_by_extrusion()`：产品**全部朝上表面**（+ 非贯穿孔竖直壁，靠"面上点正下方是否有料"判定）沿 +Z 拉伸到 P 的**逐个**并集（★必须逐个 Fuse，打包 compound 一次 Fuse 会返回十几块并列实体★），减去产品、裁平到 P；若仍不相连，用 `_build_product_footprint_face` 在分模面以上补封顶体连通。
**两种内核按 XY 足迹覆盖率择优**（先跑成熟内核，覆盖率 ≥0.95 就用它，否则用拉伸法）：

| 产品 | 凸起内核(mold_fix) | 拉伸法(新) | 实际选用 |
|---|---|---|---|
| 2ROM31-6061 深槽 | 覆盖 **22%**、贴合 12.8%、最大离缝 **183mm** | 覆盖 100%、贴合 **98.3%**、离缝 **0** | **拉伸法** |
| YA-1131 曲面件 | 覆盖 99.3%、贴合 **98.9%**、最大离缝 1.2mm | 覆盖 99.3%、贴合 93.5%、最大离缝 2.6mm | **凸起内核** |
| YA-1238 曲面件 | 覆盖 99.7%、贴合 **98.6%**、最大离缝 0 | 覆盖 99.7%、贴合 85.3%、最大离缝 2.6mm | **凸起内核** |

> 上表贴合率口径：面参数域 6×6 采样，并**剔除落在面内孔里的假点**（参数域矩形含内孔，中值取样会落在孔上——早期误把这类假点当成"79mm/161mm 边缘缺料"，实为测量伪影，特此更正）。

下模 `CAVITY_M2 = Cut(盒[Z_B−base .. Z_A], 产品)` 三件均 1实体1壳、BRepCheck 有效、`Common(下模,产品)=0`、顶面齐 Z_A，未改动。

#### 4.9c-2 ★两个全局性陷阱（2026-09-11，都真实踩过、都很难查）★

**陷阱 A：`Bnd_Box` 的容差扩张会让"正确的结果"被判成失败。**

`mold_fix.MoldFixer._bbox_tuple()` 走的是 `Bnd_Box` + `BRepBndLib.Add_s`，**返回值会被形状容差扩张**。布尔结果的容差往往不小，实测：

```
真实几何（BRepBndLib.AddOptimal）  顶面 = 71.0009   ← 恰等于 P，完全正确
Bnd_Box（_bbox_tuple）             顶面 = 73.6275   ← 虚高 2.63mm
```

法二A 的 punch/cavity 就因此被错杀了好几轮——校验写成 `abs(bbox[5] - P) > 0.05 → 失败`，拿虚高值去卡，**把正确结果判成失败**；当时还误以为"OCP 布尔对开放壳不可靠"，白折腾了很久。

**规避（`mold_method2_generator._bbox_accurate`）**：
```python
b = Bnd_Box(); BRepBndLib.AddOptimal_s(shape, b, False, False); return b.Get()
```
**凡是"用包围盒做精确校验"的场合（平齐检查、越界检查、体积/尺寸比对）一律用 `AddOptimal`**；`_bbox_tuple` 只适合粗筛和保守估计。

**陷阱 B：`_clamp_to_box` 这类"保险裁剪"会与目标盒共面 → OCC 布尔卡死。**

为防"顶面不平齐/下模越界"，曾在 `generate_punch` / `generate_cavity` 里加 3 处 `_clamp_to_box(...)`。它每次做 2 个布尔，而"裁剪外盒"与目标实体的包围盒**正好共面**——OCC 处理共面布尔极慢，**法二A 从 ~3 秒卡到分钟级甚至更久，Web 端表现为"一直卡在生成中"**。而它要防的问题（陷阱 A 的"超出"）根本不存在，所以它**既多余又致命**。

**已全部移除**（函数定义保留、无调用点）。教训：**不要用"再布尔一次"去修一个用测量就能确认不存在的问题**；先分清是几何错了还是量错了。

> 这两条同样适用于本项目任何新增的布尔/校验代码。



---



**定位**：在「再修理」输出（`*_REFIXED` 上模 = [基座,凸起] / 下模 = [型腔]）之上，把【再修理后的上模】＋【产品】＋【再修理后的下模】按同一坐标系拼合成一个**可下载的【合拢装配体】**并输出 STEP——下载后打开即是三件已配合合拢（插合、贴面、无干涉）的整体，可直接查看配合效果。

**关键机制（坐标系洞察）**：
- 再修理后的上/下模（`*_REFIXED`）与【原始 `fix_output` 的 `PUNCH_FILLED` / `CAVITY_HOLLOWED` 模具 compound】**共享同一坐标框架**（如 YA-1131 Z≈[-36.9, 32.5]，产品 Z[-10.5,12.5]，三件天然同框架）；
- 而独立的产品 STEP（如 `*_MIRROR_xOy.stp`）是**另一框架**（Z 偏置近 5 万）——直接拼合会错位近 5 万 mm **无法吻合**；
- 因此**正确做法**：从原始 `fix_output` 模具 compound 内自动提取「产品结构体」（体积最小+Z最低自动识别，或 `--product-role` 指定），该产品实体与再修理后的上/下模天然同框架 → 真正无间隙、无干涉吻合（实测 `Common(上模,产品)=0`、`Common(下模,产品)=0`）。

**输出**：`{前缀}_ASSEMBLY.stp` —— `compound [上模(基座+凸起), 产品, 下模型腔]`（各结构体独立但**同框架合拢插合**、贴面无干涉，一个可下载装配体文件）；可选 `--fuse` 布尔融合为单一实体（**实验性**，默认关；型腔随形贴合时 OCCT 布尔常失败/损坏，失败时仍保证输出合拢 compound 主产物）。

**内置待命自检**：
- 干涉：`Common(上模,产品)` / `Common(下模,产品)` / `Common(上模,下模)` 应≈0（**硬约束**，> `interfere_tol` → FAIL）；
- 间隙：产品表面采样点距【上模∪下模】最近距离 ≤ `gap_tol`（默认 0.1mm）；默认 **WARN**（再修理需求3的下模角落切除会留 ~1.6mm 已知局部差异），`--strict-gap` 时强制 FAIL；
- 同框架判据：产品来源=原始模具 compound 时最强信号判同框架（0 平移）；外部产品时按 Z 重叠量判断，不同框架且无 `--align parting` → FAIL（避免错误拼合）。

**CLI 示例**：
```bash
python assembly_builder.py \
    --punch refix_output/xxx_PUNCH_FILLED_REFIXED.stp \
    --cavity refix_output/xxx_CAVITY_HOLLOWED_REFIXED.stp \
    --product-src fix_output/xxx_PUNCH_FILLED.stp \
    --output-dir assembly_output
```
退出码：0=PASS；1=FAIL（干涉/间隙/异常）；2=参数/文件错误。

---

### 4.9d `mold_method3_solidworks.py` + `mold_method3_macro.txt` —— ★方法三（SolidWorks 二次开发）：已打通★

**定位**：用 **SolidWorks 2024 的 API** 实现法二A 的几何，用 SW 的几何内核替代 OCP 里不可靠的开放壳布尔。两条腿：
- `mold_method3_solidworks.py` —— COM 底座（pywin32，late binding）
- `mold_method3_macro.txt` —— **VBA 几何宏**（SW 进程内运行，能调用 pywin32 调不动的对象型参数 API）

**环境（本机实测）**：SOLIDWORKS 2024 SP5（`RevisionNumber 32.5.0`，`D:\Program Files\SW\SOLIDWORKS`）+ Python 3.10.8/64 位 + **pywin32 312**。

**当前状态：✅ 已能在 SolidWorks 里真实生成上下模，并经 OCC 校验通过。**

| 能力 | 状态 |
|---|---|
| COM 连接 / 版本 / `available()` 可用性探测 | ✅ 0.1s 挂上运行中实例 |
| STEP 导入（`LoadFile4` 4 参 + `GetImportFileData`） | ✅ 产品实体 → 1 body |
| 形变面导入（OCP 先 `BRepBuilderAPI_Sewing` 缝成单一 SHELL 再导出） | ✅ SW 识别为 1 个曲面体 |
| 草图 + 盲拉盒体（`FeatureExtrusion3` 23 参） | ✅ 盒体尺寸精确 |
| **★「成形到曲面」拉伸/切除（`FeatureExtrusion3`/`FeatureCut4`, T1=4, mark=1）★** | ✅ **成功**（见下"最终方案"） |
| 导出的 STEP 是曲面模型（残留曲面体没删干净） | ⚠ 用 `_m2_debug/m3_post.py` 取闭合壳转实体 |
| pywin32 直连的 `InsertCutSurface` / `Body2.Select2` | ❌ 仍调不动（对象型参数封送失败）→ 改走 VBA |

**实测产物（2ROM31-6061，生成 2.9 秒/个）**

| 零件 | 实体/壳/面 | 体积 | 包围盒 (mm) |
|---|---|---|---|
| 上模 | 1 / 1 / 24 | 386 438 mm³ | x[-120,120] y[-25,25] z[3,72] |
| 下模 | 1 / 1 / 23 | 1 661 453 mm³ | x[-125,125] y[-30,30] z[-67,71] |

上下模**干涉 = 0.0000 mm³**，两个都是 BRepCheck 有效实体。上模底面 = 上形变面（z=3.000），顶面 = 高度面 P（z=72.000）。

#### ★最终方案：不要"用曲面去切"，改成"让 SolidWorks 自己算终止位置"★

```
上模 = 在 z=P 的基准面上画形变面的投影轮廓 → 向下【成形到曲面】拉伸到上形变面
下模 = 先拉基座块 → 在基座顶面画投影轮廓 → 向下【成形到曲面】切除到下形变面
```

这两式**正好等价于法二A**（法二A 上模 = 形变面沿 +Z 拉伸并集裁到 z≤P；下模 = 基座 − 下形变面沿 +Z 拉伸并集）。
**全程没有任何布尔、没有曲面切除**，只有 SW 最标准的两个拉伸特征。

旧的"使用曲面切除"必然失败：SW 要求曲面把实体**切断**，而旧版盒体四周比形变面大 5mm，
形变面边界整圈悬在实体内部 → 实体从边界外侧绕过去 → **静默不切**
（`InsertCutSurface` 返回 `err=0`、特征非空，体积前后一位不差）。

**★13 条 SW API 实测要点（逐条踩过，改代码前必读）★**
1. **中性格式必须用 `LoadFile4`，不能用 `OpenDoc6`**：`OpenDoc6` 对任何 STEP/IGES（连 OCP 导出的最简长方体）都返回 `swFileRequiresRepairError(2097152)` 且不打开；`LoadFile4` 返回 `errors=0`。签名是 **4 参** `(FileName, ArgString, ImportData, Errors)`。
2. **3D Interconnect 必须关闭**，否则 `LoadFile4` 返回 `swGenericError(1)`，且它会弹「要不要建派生零件」模态框（静默模式无法应答 → 必然失败）。开关 `swMultiCAD_Enable3DInterconnect`(偏好号 691)，**必须重启 SW 才生效、经 API 设置不持久化**。可靠改法（SW 关闭时改注册表）：
   `HKCU\Software\SolidWorks\SOLIDWORKS 2024\ImportSettings\MULTI_CAD_ENABLE_3D_INTERCONNECT = 0`
3. **形变面 STEP 必须先缝合成单一壳**：`mold_contact_surface_extractor` 导出的是"面 Compound"，SW 会把每片面**拆成独立文档**；缝成 1 个 SHELL 后才导入为 1 个曲面体。
4. **本机 gb 模板的基准面命名与标准相反**：`前视基准面` 才是 **Z 法向**，`上视基准面` 是 Y 法向。
5. **对象型参数不能传 Python `None`**，要传 `VARIANT(VT_DISPATCH, None)`；`Errors/Warnings` 这类 `[out] long*` 要传 `VARIANT(VT_BYREF|VT_I4)`。
6. **`SaveAs` 用 6 参形式**才成功：`(Name, Version, Options, ExportData, Errors, Warnings)`。
7. **`Dispatch` 之后别立刻操作**：冷启动要等 ~20s，否则 SW 进程直接崩（RPC `0x800706BE`）。
8. `ModelDoc2.GetBox()` 在 late-binding 下取不到；用 `Body2.GetBodyBox()`（返回**米**，顺序 `[xmin,ymin,zmin,xmax,ymax,zmax]`）。
9. **`Body2.Select2` 与 `FeatureManager.InsertCutSurface` 在 pywin32 下无法调用** → 这些必须在 VBA 里做。
10. `EnsureDispatch` 会失败，需先 `python -m win32com.client.makepy <sldworks.tlb|swconst.tlb>`。
11. **★`FeatureExtrusion3` 的选择标记（官方帮助原文）★**：3D草图=0、拉伸方向参考边=16、起始条件参考实体=**32**、终止条件参考实体=**1**、受影响实体=8。**`Dir=True` 才是翻转拉伸方向**，`Flip` 是"翻转切除侧"。凸台默认沿草图法向，切除默认与草图法向**相反**。
12. **★`T1` 终止条件★**：0=盲、4=成形到曲面、7=成形到实体、10=成形到选定（10 取代 4 和 3）。实测**唯一可用组合 = `SURFACEBODY` + `mark 1` + `T1=4`**；`FACE` 参考 + `mark 2` 会把 SW 直接打崩。
13. **★「成形到曲面」要求终止曲面完整盖住草图轮廓★** —— 这是最大的坑：
    - 形变面的 XY 投影只有包围盒的 **76~92%**（1131 是机头罩壳，投影是半椭圆），所以**包围盒矩形轮廓必然失败**，必须用形变面自己的投影轮廓。
    - **形变面上的孔会让拉伸失败**（孔处没有曲面可终止）。实测：把轮廓换成避开孔的角落小矩形立刻成功。解决 = 在 OCC 侧用 `BRepBuilderAPI_MakeFace` 把孔补平（`_m2_debug/m3_fill.py`）。
    - **轮廓点表首尾必须去重**：`matplotlib.contour` 的闭合环首尾点重合，原样喂给 VBA 会多画一条**零长度线段** → 草图轮廓失效 → 拉伸返回 `Nothing`。
    - 对**曲面体**调 `SketchUseEdge3` 返回 `True` 却生成 **0 个实体**；逐个选**边**能产出但会混入面间接缝和孔洞环 → 轮廓最终改由 OCC 算好、以点表喂给 VBA。

> **长度单位**：SolidWorks API 内部一律用**米**；本项目其余部分用毫米，进出必须换算。

#### ★为什么上下模会天然干涉（法二A 的固有特性）★

上下形变面是沿**板料法向**偏 2mm（板厚）的，**不是 Z 向偏 2mm**。
在近竖直壁处，上表面的竖直投影会低于下表面的竖直投影 → 上下模咬入。
实测咬入量 **6863 mm³ ≈ 板厚 2mm × 壁高 68mm × 壁长 50mm**，位置正是 x=±71 的两面墙。
`_m2_debug/m3_post.py` 用「上模 − 下模」消除，复核干涉 = **0.0000 mm³**。

#### 关于 `.swp`（宏文件）

SW 全部 API 里**没有"程序化创建/打开宏编辑器"的接口**（只有 `RunMacro2` / `GetMacroMethods` 能操作已有 `.swp`）。
所以 `.swp` 需人工开一次 VBE（Alt+F11）建好；之后 `_m2_debug/m3_drive.py install` 就能**全自动**把
`mold_method3_macro.txt` 贴进 VBE 并保存（SendKeys + 剪贴板）。

完整进展、复现步骤与全部踩坑见 **`_m2_debug/M3_PROGRESS.md`**。

**CLI**：
```bash
python mold_method3_solidworks.py --selftest     # 只验证 COM 管线（连接/导入/取体/导出）
python mold_method3_solidworks.py --probe-api    # 探测可用 API 面
python _m2_debug/m3_drive.py install             # 把宏贴进 VBE 并保存（需 VBE 已开）
python _m2_debug/m3_drive.py run punch           # RunMacro2 生成上模
```

---

### 4.9e `moldkit/` + `moldgen.py` + `mold_sw_engine.txt` —— ★★模具自动生成软件（统一方法选择）★★

**定位**：把原来散落在各处的模具生成办法，统一成「**统一输入 → 选一个方法 → 统一输出与校验**」，
并给出可操作的界面（Web 控制台 / 命令行 / SolidWorks 内入口）。
详细手册见 **`MOLDKIT.md`**。

**★★已集成进原来的 Web 界面★★**（2026-09-20 补）
左侧功能栏「**🏭 模具自动生成**」（原「模具生成法二」面板升级而来）：
选输入 → 勾方法（**可多选做对比**）→ 生成 → 面板内实时日志 + 结果表（体积/干涉/贴合度）+ STEP 下载链接。
原有的「法二」单独入口保留在同一面板下方，逻辑未改动。

新增接口（`web_box_generator/app.py` **追加**，不影响任何现有接口）：
`/api/moldkit/methods`、`/api/moldkit/run`（后台线程 + 轮询，避免几十秒卡死浏览器）、
`/api/moldkit/job/<id>`、`/api/moldkit/file/<id>/<name>`。

> ⚠ **5001 只能跑一份 app.py**。曾经同时跑两份，表现为页面请求随机超时；重启前先清理残留进程。

**四种用法**

```powershell
$py = .\venv\Scripts\python.exe
$py .\web_box_generator\app.py --port 5001      # ① 原界面（已集成）-> http://127.0.0.1:5001
$py -m moldkit.webapp 5002                      # ② 独立控制台（多轮廓预览图）
$py moldgen.py list                             # ③ 命令行：列方法
$py moldgen.py run  -m m3 --upper U.stp --lower L.stp -o mold_out --name 2ROM
$py moldgen.py compare --upper U.stp --lower L.stp      #    所有方法跑一遍对比
$py moldgen.py install-sw                       # ④ 装 SolidWorks 侧引擎（需 VBE 已开）
```

**六个方法（一个方法一个文件）**

| 方法 | 文件 | 引擎 | 输入 | 实测 |
|---|---|---|---|---|
| `m1` legacy mold_fix v42 | `methods/m1_legacy.py` | OCP | 凸/凹模**毛坯**文件 | ✅ 2ROM：干涉 0.0000、产品贴合 0/0（界面上选中它会出现毛坯上传口） |
| `m2a` 形变面竖向拉伸 | `methods/m2a_surface_stretch.py` | OCP | 上/下形变面 | ✅ 2ROM：9.5s |
| `m2b` 产品实体布尔 | `methods/m2b_product_boolean.py` | OCP | 产品实体 | ✅ 1131：67.5s |
| **`m3` SW「成形到曲面」** | `methods/m3_sw_uptosurface.py` | SolidWorks | 上/下形变面 | ✅ 2ROM：**贴合度 100%** |
| `m4` SW「基座 − 产品」 | `methods/m4_sw_product_combine.py` | SolidWorks | 产品实体 | ✅ 1131：干涉 0.0000 |
| `m5` SW 模具工具 | `methods/m5_sw_moldtools.py` | SolidWorks | 产品实体 | ⚠ 已实现并跑通流程；薄壁件在**分型线**这一步返回 Nothing（SW 模具工具需要实心零件的分型线） |

**★★核心设计：SolidWorks 侧做成"通用操作解释器"，加方法不用改 VBA★★**

```
Python（moldkit/swjobs.Job）  →  %TEMP%\moldsv_job.txt  →  VBA（mold_sw_engine.txt / RunMoldSV）逐行执行
```

```python
Job().import_step(srf, "srf").plane_z(72, "ptop") \
     .sketch_poly("ptop", poly, "skp") \
     .boss_upto("skp", "srf", "ptop", "DOWN", "punch") \
     .delete_surfaces().check().export(out)
```

支持的操作用 `python moldgen.py --help`，或看 `mold_sw_engine.txt` 头部注释。
**新增一种模具做法 = 只写一个 Python 文件**，不用改 VBA、不用重开 VBE。

**★为什么这条路能成、老路不能★**
老路是"用曲面去切实体"——SW 要求曲面把实体**切断**，而盒体四周都比形变面大，
形变面边界整圈悬在实体内部 → 实体从边界外绕过去 → **静默不切**（`err=0` 但体积一位不差）。
新路改成**让 SolidWorks 自己算终止位置**：「成形到曲面」拉伸/切除，
全程没有布尔、没有曲面切除。三个必做预处理（软件已自动完成）：
散面缝合 → **补掉形变面上的孔** → 用**投影轮廓**（不是包围盒矩形，投影只有 76~92%）→ 点表首尾去重。

**统一校验指标**（`verify.py`）：
① 结构（实体数/壳数/面数/BRepCheck，**分成多块会标出来而不是丢掉**）；
② 上模∩下模干涉；③ **贴合度**（按形变面工作的方法：`Σ(P−z质心)×投影面积` 算"应有体积"，2ROM 实测 100.0%）；
④ **产品贴合**（按产品工作的方法：上模∩产品 与 下模∩产品，都期望 0）。

**★统一闸门 `pipeline.audit()`★**：不通过就不算成功 —— 干涉 > 1 mm³、模具侵入产品 > 产品的 1%、
或半模不是「≥1 实体且 BRepCheck 有效」一律判失败。
**为什么必须有**：几何流水线最怕"静默产出错的东西还报成功"。实测踩过两个：
① `GetBodies2` 顺序不稳定导致组合选错体，下模体积从 4 338 095 变成 91 906，日志却一路"成功"
→ 改用 `@特征名` 反查实体（`IFeature.GetFaces`→`IFace2.GetBody`）+ 工具体 `ALL`，彻底不依赖顺序；
② "壳→实体"只取最大的那块，**静默丢掉 144 806 mm³ 材料** → 改成**全部保留**（多块合成 compound），
只丢开放壳。两个 bug 都是被新加的校验指标抓出来的。

**★关于上下模干涉（不是 bug）★**：去干涉前咬入 6863 mm³。上下形变面是沿**板料法向**偏一个板厚的，
**不是 Z 向偏**；近竖直壁处上表面的竖直投影会低于下表面的竖直投影。
实测咬入量 = 板厚 2mm × 壁高 68mm × 壁长 50mm（位置正好在 x=±71 的两面墙）。
`post.clearance()` 用「上模 − 下模」消掉，复核 = 0.0000 mm³。

**实测汇总**

| 方法 | 零件 | 上模 mm³ | 下模 mm³ | 干涉 | 产品贴合 | 结构 |
|---|---|---|---|---|---|---|
| m3 | 2ROM | 386 438 | 1 661 453 | 0.0000 | — | 1 实体 1 壳 |
| m2a | 2ROM | 361 927 | 1 667 266 | 0.0000 | — | 1 实体 1 壳 |
| m2b | 1131 | 4 882 456 | 11 524 176 | 0.0000 | 0 / 0 | 1 实体 1 壳 |
| m4 | 1131 | 4 478 600 | 4 338 095 | 0.0000 | 0 / 0 | 上模 1 块、下模 **7 块** |

> m4 下模 7 块是**真实几何**：m4 用水平分型面，1131 是深拉伸薄壁件、产品顶面穿过分型面，
> 于是「基座 − 产品」把型腔切成 1 主块 + 6 块分型面附近的板。软件**全部保留**并标注
> `⚠分成 7 块`，不丢材料。要单块模体请用方法三。

**★本轮新增 15 条 SolidWorks/VBA + 7 条 OCC 实测坑**（`MOLDKIT.md` 第 6 节有完整表）。最关键几条：
① 终止条件参考实体必须 `select mark=1`；② `Dir=True` 才是翻转方向；
③ 对曲面体调 `SketchUseEdge3` 返回 True 却生成 0 个实体；④ `FACE`+`mark 2` 会把 SW 打崩；
⑤ `InsertCombineFeature` 必须先把实体选中且 `mark=2` 再传 `(类型, Nothing, Nothing)`；
⑥ 往 VBA Dictionary 存对象必须用 `Set`（否则报 438）；
⑦ 残留曲面体被特征引用后删不掉 → SW 会把零件导成**曲面模型**（0 实体），必须外部归一化；
⑧ **`GetBodies2` 顺序不稳定**，绝不能按序号指代实体（实测因此静默产出错体）；
⑨ Flask 每请求一线程，**COM 连接必须每线程一份 + `CoInitialize`**。

---

### 4.11 `web_box_generator/app.py` —— Flask Web 后端

### 4.10 `web_box_generator/app.py` —— Flask Web 后端

**启动**：`python app.py [--port 5001]`（默认 5001；`0.0.0.0` 监听，debug=False，threaded=True）。

**核心机制**：
- 上传文件存 `uploads/{12位hex}_{原名}`，`_strip_uid_prefix()` 剥离前缀还原干净文件名；
- **STEP 解析在独立常驻子进程池**中执行（`_get_parse_pool()`，spawn 单进程）：OCP 网格化是原生 C++，主进程阻塞会卡死页面 → 子进程 + `_PARSE_TIMEOUT=120s` 强制终止 + `_reset_parse_pool()`；
- **单次导入复用**（优化复杂大模型超时）：STEP 主导耗时是**导入**（B 样条面多的模型可达 20s+），网格化很快 → 用 `step_reader.import_shapes()` 只导入一次，`topo_bbox()`（BRepBndLib）免网格化求尺寸选精度，再对**同一 shape** 网格化（三角形超限时才在同一 shape 上降精度重网格化，**不重复导入**）。由"多遍全量导入"降为"一遍导入+1~2 次同形状网格化"，复杂模型解析从 ~43s 降到 ~23s。
- **自适应网格精度**：用 `topo_bbox()` 免网格化求出模型最大尺寸 L 后选用线性/角度偏差（小件≤120mm→细，中型→中，大模具>300mm→粗），不重复导入即可确定精度；
- `_clean_mesh()`：顶点合并去重 + 删除退化三角形（阈值 1e-6，只合并完全重复顶点）；
- **运行后自动清理中间产物（默认开启）**：由环境变量 `AUTO_CLEAN_INTERMEDIATE`（`0`/`false`/`off` 关闭，默认开启）或前端顶部「🧹 自动清理中间产物」开关（`/api/auto_clean` GET/POST）控制。每次成功生成后调用 `_cleanup_intermediates(keep_paths)`：**保留最终 .stp**（传入的各 output/step/assembly 路径），清除可重建的中间产物——① 所有 `*.geom_cache.json`（`mold_fix_cache/` 与 `uploads/` 下）、② 再修理中间件 `*_NO_PRODUCT.stp`（保留 `*_REFIXED.stp` 最终件）、③ 空的 task 子目录。
- 前端 `templates/index.html`（Three.js 三维查看/测量/选点/切片/**装配拼合**）。
- **渲染质量优化**：`computeSplitNormals()` 按面夹角阈值分裂共享顶点法线（硬边/折弯锐利、圆角曲面平滑），替代单纯 `computeVertexNormals()` 的“抹圆”发糊问题；重平衡光照（弱化环境/半球光、方向光塑形）避免灰平无对比；渐变背景、地面软阴影（径向渐变圆盘，非真实阴影映射，零性能损耗）、网格/阴影贴合模型底部并随模型尺寸缩放；显式 `outputColorSpace=SRGB`、`toneMappingExposure`、`envMapIntensity` 校色。
- **装配·拼合模块（多面板）**：前端右侧新增「🧩 装配」面板，加载 **3 个结构体 = 再修理后的上模（[基座,凸起]）＋ 同框架产品 ＋ 再修理后的下模（[型腔]）**（支持文件选择或从会话结果自动填入；上/下模优先取「模具再修理」结果 `_refixFiles`，其次 `_fixedFiles`/`_moldFiles`；**产品经 `/api/assembly_product_mesh` 从原始 fix 模具提取同框架实体**）。**对齐策略（2026-09-01 支持任意导入自动对齐）**：`arrangeAsm` **优先调用 `/api/assembly_align`** 用 BREP 真实质心+分模面精确计算每件位移（对**任意导入/任意坐标框架** STEP 都能对准，如产品镜像件 Z/Y 偏置数万 → 自动拉回分模面+配合中心），前端把位移应用到各 mesh 组；仅当后端不可用（文件不在服务器/无 filepath）才回退前端网格近似对齐（同框架 0 平移 / 不同框架配合特征）。对齐后再整体包围盒重新居中到**坐标系原点**。每个部件可独立开关半透明透视、滑条/「拖动模式」沿 Z 上下拉开/合拢。渲染走独立 `asmGroup`，不干扰单模型视图。

**API 一览**：

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/api/generate_mirror` | 镜像生成（参数 filepath/plane/center_on_centroid） |
| GET | `/api/download_mirror?file=` | 下载镜像 STEP |
| POST | `/api/generate_rotate` | 旋转生成（参数 filepath/axis(x/y/z)/angle(度)/clockwise/center_on_centroid） |
| GET | `/api/download_rotate?file=` | 下载旋转后产品 STEP |
| GET | `/api/auto_clean` | 查询「运行后自动清理中间产物」开关状态（默认开启） |
| POST | `/api/auto_clean` | 开关「运行后自动清理中间产物」（参数 enabled: bool） |
| POST | `/api/generate_hole_fill` | ☆孔洞补齐☆（参数 filepath/max_holes=1000）→ 自动识别所有孔洞并补齐至平面、无超出，输出 `_HOLE_FILLED.stp` 到 `uploads/holefill_output/` |
| GET | `/api/download_hole_fill?file=` | 下载孔洞补齐后的模型 STEP |
| POST | `/api/corner_close` | 角落自动闭合补齐（`check_only=true` 仅检测；否则修复+导出，输出到 `uploads/corner_output/`） |
| GET | `/api/download_corner_close?file=` | 下载角落闭合后产品 STEP |
| POST | `/api/fix_mold` | 模具修复（凸模 fill / 凹模 hollow） |
| GET | `/api/download_fix?file=` | 下载修复后模具 |
| POST | `/api/mold_refix` | ★模具再修理★（参数 punch_filepath/cavity_filepath/corner_fill 默认 false；cavity_corner_trim 默认 true）→ 需求1 去除上下模内的产品结构体（默认）；需求2 凸起角落尖角补齐仅当 `corner_fill=true`；需求3 下模型腔角落凸起切除（默认）；输出到 `uploads/refix_output/<task_id>/` |
| GET | `/api/download_refix?file=` | 下载再修理后的上/下模 STEP |
| POST | `/api/extract_contact_faces` | ★形变配合面提取★（参数 **product_filepath=【产品】STEP**、export_step 默认 true）→ **直接根据产品**提取：凸模配合面=产品上表面，凹模配合面=产品下表面；不再传模具、无需先生成模具。输出 `_PUNCH_CONTACT_FACES.stp`/`_CAVITY_CONTACT_FACES.stp` + `*_CONTACT_FACES.json` 到 `uploads/contact_output/<task_id>/` |
| GET | `/api/download_contact_faces?file=` | 下载提取的形变配合面 STEP / JSON |
| POST | `/api/generate_mold_method2` | ★模具生成法二★（⭐**推荐法二B**：参数 **product_filepath=【产品】STEP** → 用产品实体做稳定布尔，产物强制 1实体1壳+BRepCheck有效；亦可 **法二A**：upper_filepath=上形变面 / lower_filepath=下形变面 STEP；h1/h_cavity/base_lx/base_ly/base_hz/margin 可选=自动、export_step）→ 输出 `_PUNCH_M2.stp`/`_CAVITY_M2.stp` + json 到 `uploads/m2_output/<task_id>/` |
| GET | `/api/download_mold_method2?file=` | 下载 模具生成法二 的上/下模 STEP / JSON |
| POST | `/api/build_assembly` | ★装配吻合结构体输出★（参数 punch=再修理后上模、cavity=再修理后下模、product_src=原始 fix_output 模具（含同框架产品，推荐）、product=外部产品（可选）、fuse 默认 false、align=None/parting）→ 输出 compound [上模,产品,下模]；干涉恒为 0，间隙默认 WARN；输出到 `uploads/assembly_output/<task_id>/` |
| GET | `/api/download_assembly?file=` | 下载装配吻合结构体 STEP |
| GET | `/api/assembly_product_mesh?file=` | ★装配显示用★ 从原始 fix_output 模具 compound 自动提取**同框架产品实体**网格（避免用用户上传镜像件——不同坐标框架导致下模 Y 错位）；装配前端"自动"加载产品用此接口 |
| POST | `/api/assembly_align` | ★装配自动对齐★ 用 BREP 精确计算 上模/产品/下模 三件对齐位移（真实质心+分模面，对**任意导入/任意坐标框架** STEP 都能对准，如产品镜像件 Z/Y 偏置数万）。返回每件 `offsets{dx,dy,dz}`，前端应用到 Three.js group 即可自动对准；后端异常时前端回退网格近似对齐 |
| POST | `/generate` | 生成长方体 STP/STL（BoxGenerator 引擎） |
| POST | `/upload` | 上传 STP/STEP/STL 并解析返回网格数据 |
| GET | `/api/slice_plane?file=&origin=&normal=` | 单平面切割轮廓分析 |
| GET | `/api/slice_plane_batch?file=&normal=&range_min=&range_max=&count=` | 批量切片（毛料计算） |
| POST | `/api/generate_mold` | 凹凸模生成（MoldGenerator） |
| GET | `/api/download_mold?file=` | 下载生成的模具 |
| GET | `/api/model_features?file=` | 特征检测（折弯/圆孔/板厚等） |
| GET | `/api/mesh?file=` | 按路径返回 uploads 内文件的三角网格（装配·拼合模块用） |
| GET | `/` | 前端页面 |

> 所有下载/操作类接口均有**路径白名单校验**（必须位于 uploads 内），防止任意文件读写。

---

### 4.11 `fit_checker.py` —— ★三件贴合验证模块★（v42 新增）

**用途**：验证【修复后凸模(上模) → 产品 → 修复后凹模(下模)】三者装配后依次紧密贴合。

**三类检查**：
1. **无干涉**：`Common` 体积 ≈ 0 —— 凸起↔产品、型腔↔产品、凸模基座↔产品、凸起↔型腔；
2. **无间隙**：产品表面每个采样点距【上模(基座∪凸起) ∪ 下模(型腔)】最近距离 ≤ `gap_tol`（默认 0.1mm）；
3. **型腔忠实复刻**：型腔顶面开口必须到达分模面（外露带 ≤ `band_tol`，默认 0.2mm），型腔内壁随产品外形（若型腔扩大/挖浅，产品表面采样点距型腔 > tol 会被自动判为间隙）。

**失败归属**：间隙点最近的是型腔 → 下模挖空不正确（改 `mold_fix.hollow()`）；最近的是凸起/基座 → 上模填充不正确（改 `mold_fix.fill()`）。

**CLI 用法**：
```bash
python fit_checker.py --punch <凸模修复.stp> --cavity <凹模修复.stp> [--product <产品.stp>]
python fit_checker.py --punch fix_output/xxx_PUNCH_FILLED.stp --cavity fix_output/xxx_CAVITY_HOLLOWED.stp
```
参数：`--gap-tol`（间隙容差 mm）、`--interfere-tol`（干涉容差 mm³）、`--band-tol`（外露带容差）、`--sample-cap`（每面采样点数）、`--show-gaps`、`--json`。
退出码：0=PASS（完全贴合）；1=FAIL；2=异常。

**核心实现**：`FitChecker.check()` 提取结构体（凸模=3、凹模=2）→ 产品一致性校验 → 干涉检查 → 产品表面点采样（顶点+边中点+面中心）用 `BRepExtrema_DistShapeShape` 逐点测距。也可通过 `python mold_fix.py --verify-fit --punch ... --cavity ...` 调用。

---

### 4.11 `corner_close.py` —— ★产品预处理：角落自动延伸闭合补齐★

**定位**：运行在【镜像生成之后、凹凸模生成之前】，作为产品预处理步骤。对存在法兰拐角开放缺口的钣金件，识别开放角落并自动延伸相邻法兰面、裁剪相交生成闭合补片、与原实体 Fuse 融合，补齐角落，防止模具型腔/凸起出现残缺。

**类 `CornerCloseRepair`**（构造参数：`extend_mm=2.0` 面最大延伸距离、`gap_tol=0.15` 间隙阈值、`thickness=None` 板厚（不传则复用 brep_topology 自动检测）、`corner_mode='sharp_closed'|'butt'|'overlap'`（默认 **sharp_closed 尖角闭合**）、`skip_small_features=True` 跳过极小碎面、`export_report=True`、`min_flange_area=3.0`、`angle_min_deg=10.0`、`max_gap_detect=12.0`、`proximity_tol=5.0` 平面邻近判据阈值）。

**公开方法**：
- `repair_file(filepath, output_dir, output_name=None) -> dict`：主入口，读取 STEP → 角落识别 → 逐角落闭合 → 校验 → 导出 `{name}_CORNER_CLOSED.stp` + txt 报告。返回结构化报告 dict（识别/成功/失败角落、原始/修复后体积、间隙列表）。
- `check_corner_gaps(filepath) -> dict`：仅检测不修改（类似 `mold_fix --check-only`），输出间隙列表。

**算法（复用现有组件，基于 OCP 原生）**：
1. 复用 `brep_topology.BrepTopologyExtractor` 抽取面/边/邻接/板厚/平面圆角分类，`bend_detector` 取自由边 `free_edges`；
2. 筛选法兰平面（排除圆角 fillet_faces / 倒角 chamfers，`skip_small_features` 跳过极小碎面）；
3. 对两两相交（非平行、非厚度对、非共面、非相邻）的平面，在**所有自由边对中筛选【共面】且间隙在 `(gap_tol, max_gap_detect]` 的边对**，取间隙最小者；按缺口位置**去重**同一物理角落，判定为待修复开放角落；
4. **默认 `sharp_closed` 尖角闭合**：取两条共面的缺口边，各沿边方向向缺口外延伸，两延伸线求交点 → 尖角顶点 apex；以 [缺口端点A, 缺口端点B, apex] 构造三角形面，基边向材料内搭接重叠，沿材料内侧方向拉伸板厚生成**楔形/三角补片** → `BRepAlgoAPI_Fuse` 与原实体融合成一整体；
5. `butt`/`overlap` 模式：两法兰沿自身平面扩展 + 拉伸板厚 → 并集 → 裁剪到角落包围盒（平面对接）；
5. `BRepCheck_Analyzer` 校验，单角落失败不中断整体流程并记入报告；不改动产品内部孔、不破坏折弯圆角/倒角。

**CLI 用法**：
```bash
python corner_close.py --check-only --input <产品.stp>          # 仅检测角落间隙
python corner_close.py --input <产品.stp> --output-dir fix_output --extend 6.0   # 修复+导出（默认 sharp_closed 尖角闭合）
python corner_close.py --input <产品.stp> --mode butt --extend 6.0               # 改用平面对接模式
```
参数：`--input`、`--output-dir`、`--output-name`、`--extend`、`--gap-tol`、`--thickness`、`--mode`（sharp_closed/butt/overlap，默认 sharp_closed）、`--max-gap`、`--no-skip-small`、`--no-report`。

> 说明：默认 `extend_mm=2.0` 可闭合间隙 ≤ 4mm；若产品角落间隙较大（如测试件 `YA-1131-505_MIRROR_xOy` 实测 5~11mm），请适当增大 `--extend`（如 6.0）以实际闭合。

---

## 5. 命令行用法（离线流水线）

### 5.1 镜像生成
```bash
python mirror_generator.py   # 默认测试 YA-1131-505.stp，输出到 mold_output/
# 代码调用：
#   MirrorGenerator().generate("产品.stp", plane="xOy", output_dir="mold_output")
```

### 5.2 凹凸模生成
```bash
python mold_generator.py     # 默认测试 YA-1131-505.stp → mold_output/*_CAVITY.stp + *_PUNCH.stp
# 代码调用：
#   MoldGenerator().generate(filepath, side_margin=20.0, base_thickness=100.0, output_dir="mold_output")
```

### 5.3 模具修复（最常用）
```bash
# 快速检测（不写任何文件，秒回）：
python mold_fix.py --check-only
python mold_fix.py --check-only --input <某个模具.stp>

# 完整修复 + 导出（默认同时处理凸模+凹模，输出到 fix_output/）：
python mold_fix.py
python mold_fix.py --fix --punch xxx_PUNCH.stp --cavity xxx_CAVITY.stp
python mold_fix.py --fix --input <单个模具.stp>          # 自动识别凸/凹模

# 三件贴合验证（修复后上模→产品→下模 无间隙/无干涉/型腔忠实）：
python mold_fix.py --verify-fit --punch xxx_PUNCH_FILLED.stp --cavity xxx_CAVITY_HOLLOWED.stp

# 竖直脱模/完全挖空校验（用户提出的验证思想：产品沿 +Z 上移与型腔全程无重叠交叉）：
python mold_fix.py --verify-demold --input xxx_CAVITY_HOLLOWED.stp
#   -> 要求该 *_HOLLOWED.stp 是 compound[型腔,产品] 两结构体；零交集 0=PASS，否则 1=FAIL
#   检出时打印残留位置(bbox/质心)，如 YA-1238 外弧 Y∈[-363,-185] 约 24.3k mm³ 残留条带
#   （与 --fix 结合，如 --fix --cavity xxx_CAVITY.stp --verify-demold，则在修复后自动内联校验）

# （可选）去除凹模型腔口袋内的柱状凸起（pillars），使型腔更干净简单：
python mold_fix.py --fix --cavity xxx_CAVITY.stp --remove-pillars
#   默认关闭；开启后会在生成型腔后扫描口袋内【四周被型腔开口包围的孤立材料
#   岛】（柱状凸起），把超过面积阈值的柱体融合成切除体一次性 Cut 掉。
#   仅切除产品顶面之上的凸起本身，不覆盖产品下表面，不破坏三件贴合。
```

**mold_fix.py 完整参数**：
```
--check-only / --dry-run  只做数学检测（凸模残留凸起 / 凹模型腔残留），返回 PASS/FAIL + 坐标
--fix                     执行修复并导出 .stp
--both                    同时修复凸模+凹模（默认行为）
--verify-fit              三件贴合验证（fit_checker）：上模→产品→下模 无间隙/无干涉/型腔忠实
--punch <文件>             凸模(上模) STP
--cavity <文件>            凹模(下模) STP
--input <文件>             单个待处理 STP（自动识别 fill/hollow）
--product <文件>           外部产品 STP（可选，模具 compound 内含产品时不需要）
--output-dir <目录>        输出目录（默认 fix_output/）
--cache-dir <目录>         缓存目录（默认 mold_fix_cache/）
--no-cache / --force       禁用缓存 / 强制忽略缓存重算
--method solid3d|prisms    凹模挖腔方法（默认 solid3d 随形挖腔）
--clearance <mm>           配合间隙/收缩率（默认 0.1，占位）
--trim-edge / --no-trim-edge  外边缘切齐（默认开）
--remove-pillars           去除凹模型腔口袋内高于产品顶面的柱状凸起（默认关）
--pillar-area-thresh <mm²> 去柱的柱截面面积阈值（默认 500，--remove-pillars 用）
--top-merge-depth <mm>     凹模顶面开口合并的浅带深度（默认 3；设 0 关闭）
--no-top-merge             关闭凹模顶面开口合并（保留可能存在的连接条）
--gap-tol <mm>             三件贴合验证间隙容差（默认 0.1，--verify-fit 用）
--interfere-tol <mm³>      三件贴合验证干涉容差（默认 1.0，--verify-fit 用）
--band-tol <mm>            型腔开口低于分模面允许的最大外露带（默认 0.2，--verify-fit 用）
--show-gaps <n>            三件贴合验证报告的间隙明细条数（默认 10，--verify-fit 用）
--verify-demold            竖直脱模/完全挖空校验：产品沿 +Z 上移扫描与型腔全程无重叠
                          （单独使用=校验 *_HOLLOWED.stp；与 --fix 结合=修复后内联校验）
--demold-steps <n>         脱模校验的上移扫描步数（默认 25，越大越精确，--verify-demold 用）
--demold-tol <mm³>         脱模校验许可交集体积（默认 1.0，--verify-demold 用；YA-1131 约 1.1 接近此界）
--demold-sweep             【实验性，默认关】修复凹模时用产品沿 +Z 竖直扫掠补切型腔。
                          ⚠ 实测 OCCT 布尔对薄翻边板材的自交扫掠体极脆弱（多为布尔失败），
                          通常无法真正清空残留，仅作实验探索；默认关（--no-demold-sweep）保现状
```

**退出码**：check/verify-fit 模式下 0=PASS，1=FAIL，2=异常。

### 5.4 Web 启动
```bash
cd web_box_generator
python app.py --port 5001   # → http://127.0.0.1:5001
```

---

## 6. 标准业务流水线（完整链路）

```
产品 STEP（钣金件，如 YA-1131-505.stp）
   │
   ├─[web 上传 /uploads/{uid}_*.stp]
   │
   ├─① MirrorGenerator   → {产品}_MIRROR_{xOy/xOz/yOz}.stp   （镜像对称件）
   │
   ├─①b CornerCloseRepair → {产品}_MIRROR_{plane}_CORNER_CLOSED.stp（角落闭合补齐：镜像后、模具前）
   │
   ├─② MoldGenerator     → {产品}_MIRROR_{plane}_CAVITY.stp  （凹模粗坯）
   │                       {产品}_MIRROR_{plane}_PUNCH.stp   （凸模粗坯）
   │
   ├─③ MoldFixer.fill()  → {产品}_PUNCH_FILLED.stp
   │      输出 compound[基座, 凸起台, 产品]   （凸模修复）
   │
   ├─③ MoldFixer.hollow()-> {产品}_CAVITY_HOLLOWED.stp
   │      输出 compound[型腔, 产品]            （凹模修复）
   │
│
├─④ MoldRefix.refix() → {产品}_PUNCH_FILLED_REFIXED.stp / {产品}_CAVITY_HOLLOWED_REFIXED.stp
│      输出 compound [基座,凸起] 与 [型腔]           （再修理：去除产品 + 角落处理）
│
├─④b AssemblyBuilder.build_assembly() → {产品}_ASSEMBLY.stp
│      输出 compound [上模, 产品, 下模]             （合拢装配体：同框架提取产品 + 自检，可下载）

   └─④ 下载 STEP → CAD 查看/交付
```

各步骤输出均位于：`web_box_generator/uploads/mirror_output/`、`.../mold_output/`（web）或项目根 `mold_output/`、`fix_output/`（CLI）。

**配套分析能力**（可用于人工校核/毛料计算）：
- `StepReader`：面/边/孔/圆角/倒角统计 + 网格 + 物理属性；
- `BrepTopologyExtractor` → `BendDetector`：折弯边/半径/二面角识别（`/api/model_features`）；
- `MeshSlicer`：切片轮廓 + 展开/拉直长度（`/api/slice_plane*`）。

---

## 7. 关键数据结构速查

### 7.1 STEP 导出结构约定
- `MoldGenerator` 输出 compound `[基座, 产品]`（2 结构体）；
- `MoldFixer.fill` 输出 compound `[基座, 凸起台, 产品]`（3 结构体）；
- `MoldFixer.hollow` 输出 compound `[型腔(1实体), 产品]`（2 结构体）；
- 内部按体积排序：solids[0]=基座/型腔，solids[1]=产品。

### 7.2 缓存 JSON 示例（mold_fix_cache/xxx.geom_cache.json）
```json
{
  "schema": 1,
  "input_file": "...", 
  "fingerprint": {"size": 1621233, "mtime": 1785742741.88, "sha256": "d8ce23805c192e8f"},
  "main_z": 1.516, "z_parting": 12.516,
  "last_check": {"timestamp": "2026-08-17 09:01:47", "result": "PASS", "fins": []},
  "last_fix":   {"timestamp": "...", "pre_fins": [], "post_fins": [], "result": "PASS"}
}
```

### 7.3 体积/包围盒数值参考（真实数据，用于回归校验）
- YA-1131-505 凹模修复后 ≈ 11,507,493 mm³（基座 13,128,616 − 产品∪填充 1,621,123），1 壳；
- YA-1238-01 凸模修复后 ≈ 19,222,133 mm³（基座 11,193,608 + 凸起 + 产品 1,264,243），凸起与产品重叠 = 0 mm³。

---

## 8. 版本演进史（mold_fix.py，理解算法动机必读）

> 本模块迭代非常多（v30→v42），每版都针对用户反馈的特定缺陷。改代码前务必理解以下演进逻辑，避免"修好 A 弄坏 B"。

- **v30**：凸模凸起只保留单一主凸起台，丢弃布尔残片 → 输出恰好 3 结构体；
- **v31**：输出前 `Cut(凸起, 产品)` 清除 Fuse 模糊值(1e-2)造成的薄层侵入/多余棱边 → 凸起与产品零重叠；
- **v32**：增加"去底部薄鳍"（bbox+0.5mm 扩展切除）→ **缺陷**：过度切削在角落挖出 2 处空缺 + 厚度探测失败(th=None)漏检 2 处；
- **v33**：改为**精确切除薄鳍自身包围盒到产品上表面**（0.01mm 余量，x/y 不越界），并补上 th=None 薄片检测 → 角落平整无凸起无空缺；
- **v34**：新增 `--check-only` 快速检测模式 + 几何状态缓存 JSON（52s→1.9s）+ 修复逻辑拆分为独立函数 + 局部优先修复策略 + 薄鳍 z 上限取 max(鳍顶, 产品上表面)；
- **v35**：凹模"井柱"从井底拉伸（此前只挖到主板面，井深部残留 ~11k mm³/井）→ 残留 0 mm³；
- **v36**：凹模 `check_cavity()` 检测 + 输出 compound [型腔, 产品] + `last_cavity_check` 缓存；
- **v41**：凹模挖腔重写为 `method='solid3d'`（挖空体 = 产品3D实体 ∪ 间隙体），型腔随形、侧壁精确贴合产品外侧（不再比产品外轮廓大一圈）、清除基座被包裹的上顶面，`_consolidate_hollow_result()` 清理多余结构体 → 恒为 2 结构体；
- **v42**（三件贴合验证配套）：① 凹模挖空 `open_offset` 0.5→0，型腔顶面与分模面齐平（消除产品顶部 0.5mm 外露带）；② 凹模新增【凸起空间清空】`Cut(型腔, 凸起)`（同进程用 fill 精确凸起，否则重建），杜绝【凸起↔型腔】干涉（修复前实测 18k mm³，v42 后 0）；③ 凸起构造提取为 `_build_full_protrusion()`（fill/hollow 共用）；④ 新增 `fit_checker.py` 三件贴合验证模块 + `--verify-fit` 集成。
- **v43**（尝试修复凹模开口边缘碎化，用户反馈"挖空型腔边缘有问题、中心没问题"）：根因是 v42 的【凸起空间清空】用完整凸起（顶面=分模面）直接 `Cut`，凸起侧壁在分模面处与型腔顶面开口相交，把开口边缘切成大量短边 + 0.15mm 退化细缝（实测 YA-1238 开口内环 2 边→20 边）。本版尝试把凸起顶部裁剪到 `分模面 - rim_clip` 使开口边缘保持光滑——**但 rim_clip>0 会在分模面最顶层留下未切除的凸起空间（薄台），把腔体所有面拼成实体做装配验证时出现重叠交叉，用户不接受**。故 `rim_clip` 固定为 0（完整切除、无重叠），边缘碎化暂不处理。其根源是凸起/产品在开口处的翻边外扩（腔口约 599×519，凸起外圈 646×830）被随形切除时必然在腔口留下碎齿；若要"边缘干净且无重叠"两全，需另修凸起构造使其贴合腔口、不在开口处外扩（见下）。
- **v44**（凹模顶面"连接条"清理，用户反馈"修理后的下模顶面上有几根独立连接条把开口分隔成多块，应整条挖空"）：根因是 `open_offset=0` 后产品薄壁/交叉筋在分模面处把型腔顶部开口切分成多个小孔（实测 YA-1131 顶面大板 1 孔→3 孔，孔之间残留约 1.8k mm³ 薄条）。新增【顶面开口合并】`_merge_cavity_top_openings()` + `_build_product_footprint_face()`：用产品完整 XY 投影足迹柱，从分模面向下切除一个浅薄带（`top_merge_depth`，默认 3mm），把碎片开口合并成单个完整开口并清除连接条。浅带深度避免误切深部型腔壁（YA-1238 深切到主体会破坏贴合，浅带则空操作）；产品薄壁处型腔本是空腔、不受影响 → 三件贴合保持 PASS（实测 YA-1131 3孔→1孔、YA-1238 空操作，两者 fit 均 PASS）。新增 CLI `--top-merge-depth` / `--no-top-merge`。
- **v47**（新增"竖直脱模/完全挖空"校验，用户提出验证思想"把产品沿 +Z 上移，须与基座完全无重叠交叉"）：此前 `_top_projection_is_clear()` 只校验**顶面一个浅带**（`top_merge_depth`），无法发现深部/外缘残留，故 YA-1238 用户仍看到"边上残留小条"。新增 `_demold_compat_check(cavity, product, n_steps, tol)`：把产品从就位沿 +Z 全程上移，逐点 `Common(产品+dz, 型腔)`，任一点交集>tol 即 FAIL 并定位重叠几何。**诊断结论**：YA-1238 已交付的 `_CAVITY_HOLLOWED` 在外弧 **Y∈[-363,-185]、Z∈[6.6,34.6]、横贯整宽 X** 有约 **24.3k mm³** 持续残留（产品一被顶起 dz≈2 起即干涉）→ FAIL；YA-1131 已交付件仅约 **1.1 mm³**（刚好贴近默认 tol=1.0）→ 边界 PASS，属可接受噪声。说明该判据比三件贴合（fit PASS）更严格，专门揪"贴合验收通过但无法脱模/顶出受阻"的残留。集成方式：CLI `--verify-demold`（独立校验 `/ --fix` 内联），`hollow()` 新参 `verify_demold`/`demold_steps`，返回 dict 增加 `demold_check`；`--demold-tol`/`--demold-steps` 调参。**尝试自动修复**：新增实验性 `_demold_sweep_carve()`（用产品沿 +Z 竖直扫掠补切型腔），但**实测被 OCCT 布尔脆性阻断**——`BRepPrimAPI_MakePrism` 不能直接扫掠实体（"Solids are not Processed"）；改扫掠**壳**会因薄翻边板材自交产生 **176 个自交实体**导致 Cut 失败；逐位平移切除（0..H 每 1mm，47 次）也**仅 4/47 布尔成功**、只移除约 8k mm³，24k mm³ 残留带仍在。故 `--demold-sweep` **默认关闭**，本校验定位为**只检出、不自动改型腔**；要真正消除该残留需修凸起/产品在开口处的翻边外扩被随形切除后留下的倒扣（见 v43/v44 备注），或改用拆分模/侧滑块等可脱模结构（超出当前布尔流程）。<br>**v46（真正修好 YA-1238 顶面投影，用户反馈"还是没挖空干净，外弧边缘残留一条黄色/红色细条"）**：**纠正 v45**——v45 把截面数提到 40 后 `top_projection_clear` 虽报 True，但那是**在偏小的足迹面内**校验（足迹面 X±597.4，而产品外缘薄壁实达 X±599.7），外弧边缘一圈约 2.2mm 的**细弧带仍在足迹面之外**没被挖掉，所以用户仍看到细条。**真正根因**：`_build_product_footprint_face` 把【不同高度】的水平截面面直接做 3D 融合（z 不同），融合失败后只剩"最大截面"（托盘 X±597.4），把外圈薄壁/凸缘漏掉；且各截面外圈若是细长薄壁会被拆成多条、无法得到封闭光盘。修复：① 各截面面先**平移到 z=0 共面**再做真正的 2D 并集（真实外轮廓，含外缘薄壁、不凹口外扩）；② 末尾用产品顶点真实外缘校验，若足迹面仍漏掉外缘（缺>0.5mm）则回退到 **2D 凸包足迹面**（可靠覆盖 X±599.7、清除外弧细带）；③ `_top_projection_is_clear()` 原来用网格点分类器，对大体量基座在分模面边界误判，改为**布尔 Common(型腔,足迹柱) 体积**判据（=0 即完全挖空）。实测 YA-1238：足迹面 X±599.7（凸包 547k mm²），顶带内型腔材料 **6160→0 mm³**（细带清除），三件贴合 **PASS（gap_points=0、无干涉、product_consistency=1.0）**；`top_projection_clear=True`。<br>**注意（YA-1131 回退权衡）**：凸包顶面合并会把分模面处的外缘薄壁支撑一并削去；实测对 YA-1131 会引入 10 处贴合间隙（最大 1.253mm）——而 YA-1131 用户反馈"已可用"，故**保留其既有可用输出**，未用凸包合并重出。若后续也要把 YA-1131 顶面投影挖空，需接受该贴合间隙或用 `--no-top-merge` 保留现状。



**其他模块版本注记**：
- `mold_generator.py` 方案3：凹模顶面=分模面、凸模产品上移 0.1mm 刺入；
- Web 后端关键演进：STEP 解析移入子进程池（防原生 C++ 阻塞） + 自适应网格精度（粗读估尺寸 → L/2500 细网格）+ 网格清理 `_clean_mesh`。

---

## 9. 已知问题 / 注意事项（给 AI 的提示）

1. **mold_fix.py / step_reader.py 的 `__main__` 测试块**硬编码了他人电脑目录（`d:\Users\liyis\...`），直接 `python 模块.py` 可能报"文件不存在"，不是 bug，请用实际文件路径调用类方法。
2. **Windows 编码**：控制台 GBK 会导致中文/特殊符号乱码，模块内已用 `_force_utf8()` / `stdout.reconfigure(utf-8, errors='replace')` 处理；**新写的打印代码也要避免裸 print 特殊 Unicode**。
3. **OCP 布尔运算**是系统最脆弱的环节（Cut/Fuse/Common 可能 FAIL/留碎片/出薄鳍），所有调用都要走封装函数（`_do_cut` 等），并在失败时有回退分支。
4. **clearance 是占位参数**：当前 cadquery-ocp 版本无可用偏置 API，实际输出为完全贴合（0 间隙）。不要误导用户该参数生效。
5. **产品必须居中后处理**：`MoldGenerator.load_product` 会把产品平移到质心原点；后续所有 Z 平面（Z_A/Z_B/分模面）都以平移后坐标系为准。
6. **文件命名中的 `{12位hex}_` 前缀**是上传存储标识，业务文件名需 `_strip_uid_prefix()` 剥离。
7. `BoxGenerator.py` 依赖 tkinter（Windows 自带）；服务器无桌面环境时只能调用 `StepBoxWriter/StlBoxWriter`，不能用 GUI。
8. **型腔内表面的"小片/薄条"通常是承载产品筋/肋的型腔壁（布尔随形复刻），不是可去除的瑕疵**。`detect_cavity_fins()` / `_remove_cavity_fins()` 可检测并切除小面积（<2mm²）薄片，但**默认不自动启用**——实测对 YA-1238 去除后产生 6 处间隙（最大 0.47mm），破坏三件贴合。若确需清理，务必用 `fit_checker.py` 验证去除后无间隙；对 YA-1131 仅去除上顶面角落小片（面积<2）可保持贴合。
10. **Windows 下 `print` 会中断几何流程（严重）**：`mold_fix.py` 在布尔构造内部散布大量 `print`，字符串含 `³ ² ↔ − ▶ ✓` 等 **GBK 不可编码字符**；一旦 `stdout` 是 GBK（CLI/库调用，未先 `_force_utf8()`），`print` 抛 `UnicodeEncodeError` → 异常从几何中途冒出 → 上层 `except` 判"构造失败" → **静默退化成错误结果**（实测：法二B 上模 100% 退化为矩形块，`details['src']=='block'`）。`mold_fix.py` 现已内置安全 `print`（打印永不抛异常），其它模块可用 `from mold_fix import install_safe_print; install_safe_print(globals())` 获得同样保护。**新写打印代码仍应避免裸 print 特殊 Unicode。**
11. **方法三（SolidWorks）的调用限制与最终方案**：pywin32 下 `Body2.Select2`、`FeatureManager.InsertCutSurface` 无法调用（对象型参数封送失败）→ **几何层改走 VBA 宏**（SW 进程内运行，能调）。VBA 里**可行**的路线是「**成形到曲面**」拉伸/切除（`FeatureExtrusion3`/`FeatureCut4`，`T1=4`，终止参考用 `SURFACEBODY` + `select mark 1`），**不要**走"用曲面切除"——那条路 SW 要求曲面把实体切断，必然静默失败。另外「成形到曲面」要求**终止曲面完整盖住草图轮廓**，所以轮廓必须是形变面自己的投影（不能用包围盒矩形），且形变面上的孔必须先补平、轮廓点表首尾必须去重。完整 13 条要点见 **4.9d** 与 `_m2_debug/M3_PROGRESS.md`。
12. **⭐`MoldFixer._bbox_tuple()` 的包围盒是"带容差扩张"的，不能用来做精确校验**（实测虚高 2.63mm）。精确校验一律用 `mold_method2_generator._bbox_accurate()`（内部走 `BRepBndLib.AddOptimal`）。踩坑详情见 **4.9c-2 陷阱 A**。
13. **⭐不要给布尔运算加"保险裁剪"**：`_clamp_to_box` 式的补救会与目标实体**共面**，OCC 处理共面布尔极慢，会把秒级操作拖成分钟级（Web 端表现为"一直卡在生成中"）。**先确认是几何真错了，还是只是量错了** —— 见 **4.9c-2 陷阱 B**。
14. **改动 Python 模块后必须重启 Web 服务**：`app.py` 是常驻进程，`import` 过的模块会被缓存，改文件不重启等于没改（日志在 `_m2_debug\web.log` / `web.err.log`）。

---

## 10. AI 快速上手指南（建议执行顺序）

1. **读本文件**（第 1~7 节）——先建立整体认知；
2. **跑一遍流水线**：
   ```bash
   venv\Scripts\activate
   python mold_generator.py     # 生成 mold_output 下的模具
   python mold_fix.py --check-only
   python mold_fix.py           # 修复并导出 fix_output
   ```
3. **要改几何算法** → 找 `mold_fix.py` 中的对应函数（第 4.7 节函数表）与其版本演进（第 8 节）；
4. **要改 Web 功能** → `web_box_generator/app.py` 找 API（第 4.9 节表格）→ `templates/index.html` 改前端；
5. **要做新分析能力** → 参照 `step_reader.py` + `brep_topology.py` + `bend_detector.py` + `slice_analyzer.py` 的组合模式；
6. **验证标准**：重导入修复结果 STEP，检查结构体数量（凸模=3、凹模=2）、`Common(凸起,产品)=0`、无残留凸起/薄鳍、BRepCheck 有效、体积与第 7.3 节参考值一致。

---

## 11. 常见修改场景速查

| 需求 | 修改位置 |
|---|---|
| 凸模凸起形状不对/漏特征 | `mold_fix.py`: `_build_protrusion_core` / `_build_fill_body` / `_build_fill_face` |
| 凹模型腔不随形/侧壁过大 | `mold_fix.py`: `_hollow_cavity_solid3d` / `_product_outer_profile` |
| 凹模顶面开口被连接条分隔成多块（顶面不整条挖空）| `mold_fix.py`: `_merge_cavity_top_openings` / `_build_product_footprint_face`（`--top-merge-depth` 调深度，`--no-top-merge` 关闭） |
| 验证凹模是否"完全挖空/可竖直脱模"（顶出不受阻、无残留条带）| `mold_fix.py`: `_demold_compat_check`（CLI `--verify-demold`；产品沿 +Z 上移扫描与型腔全程交集应≈0） |
| 凹模口袋内柱状凸起（pillars）| `mold_fix.py`: `_remove_cavity_pillars`（`--remove-pillars` 开启） |
| 角落出现薄鳍/凸起 | `mold_fix.py`: `detect_bottom_fins` / `_fin_cut_box` / `_remove_bottom_fins` |
| 输出结构体数量不对 | `mold_fix.py`: `_consolidate_protrusion` / `_consolidate_hollow_result` |
| 分模面/上下表面识别 | `mold_generator.py`: `detect_top_bottom_plane`；`mold_fix.py`: `_find_fill_face` |
| 折弯识别 | `bend_detector.py`（依赖 `brep_topology.py` 的拓扑） |
| 产品角落开放缺口（镜像后预处理） | `corner_close.py`: `CornerCloseRepair.repair_file` / `check_corner_gaps` |
| 展开/毛料尺寸 | `slice_analyzer.py`（MeshSlicer） |
| 上下模去除产品结构体 | `mold_refix.py`: `remove_product` / `_auto_product_index` |
| 上模凸起台角落尖角自动补齐（延伸求交）| `mold_refix.py`: `detect_corners` / `_corner_geometry` / `_build_wedge_prism` / `fill_corners`（`--wedge-depth` 调楔块大小） |
| 下模型腔角落凸起切除（需求3，默认）| `mold_refix.py`: `trim_cavity_corners` / `refix` 预计算区（上模互补切除体，`--no-cavity-trim` 关闭） |
| 输出装配吻合结构体（上模+产品+下模） | `assembly_builder.py`: `build_assembly`（默认输出 `_ASSEMBLY.stp` 合拢 compound；`--product-src` 原始模具提取同框架产品；`--strict-gap` 严格间隙；`--align parting` 近似对齐；`--fuse` 实验性单实体） |
| 法二上模边缘缺料 / 深槽件只覆盖一角 | `mold_method2_generator.py`: `build_punch_by_extrusion`（全朝上表面拉伸法）+ `generate_from_product` 里的**覆盖率择优**（≥0.95 用凸起内核，否则用拉伸法） |
| 法二A 顶面裁不平 / 下模外甩 | `mold_method2_generator.py`: `_stretch_up`（拉伸高度改为 `z_end - bb[5] + 2`）+ `generate_punch/generate_cavity` 的事后校验（诚实报错，不交付错件） |
| 法二在 CLI/库调用下上模变成矩形块 | `mold_fix.py` 的安全 `print`（GBK 下打印不再抛异常中断几何）+ `install_safe_print` |
| **方法三（SolidWorks COM）** | `mold_method3_solidworks.py`（COM 底座）+ `mold_sw_engine.txt`（VBA 通用操作解释器）；**已打通，能生成上下模并校验通过，见 4.9d / 4.9e** |
| **★要生成模具 / 想换一种生成办法★** | 直接用新软件：`python -m moldkit.webapp 5002`（Web 控制台）或 `python moldgen.py compare`（命令行）。六个方法自由选：`moldkit/methods/`，一个方法一个文件。**加新方法只写 Python，不用改 VBA。** 手册见 `MOLDKIT.md` |
| **Web 端"一直卡在生成中"** | 先查是否有"保险裁剪"式多余布尔（4.9c-2 陷阱 B）；再确认服务已重启加载新代码（第 9 节第 14 条） |
| **尺寸/平齐校验误报** | 用 `_bbox_accurate()` 而不是 `MoldFixer._bbox_tuple()`（4.9c-2 陷阱 A，虚高可达 2.6mm） |

| 网格精度/解析卡顿 | `web_box_generator/app.py`: `_parse_step_worker`（自适应 L/2500） |
| 新增 Web 按钮/功能 | `app.py` 加路由 + `index.html` 加交互 |
| 识别/补齐模型孔洞（至平面、无超出） | `hole_filler.py`: `HoleFiller` / `detect_holes` / `fill` |






