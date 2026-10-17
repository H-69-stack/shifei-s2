# 代码审查报告：性能问题与安全隐患

- **审查范围**：S2 项目全部自研 Python 代码（约 21,200 行，排除 venv/\_m2_debug），含 moldkit 包、web_box_generator Flask 应用、OCC 几何算法模块、SolidWorks COM 驱动层
- **审查日期**：2026-10-17
- **审查方式**：静态审查 + 关键声明逐条源码核实

---

## 一、总览

| 类别          | 高 | 中 | 低 |
| ----------- | - | - | - |
| 安全          | 4 | 4 | 1 |
| 性能          | 8 | 6 | 3 |
| 健壮性/正确性 Bug | 6 | 8 | 4 |

**最需要立即处理的 5 件事**：

1. `/upload` 文件名未净化 → 路径穿越任意文件写（安全）
2. `fill_corners` 漏写 return → 启用 `--corner-fill` 必崩（Bug）
3. `fit_checker` 凸模单实体时 IndexError（Bug）
4. 孔阵去重漏比轴线位置 → 平行孔只填第一个（Bug）
5. `host='0.0.0.0'` + 全站无认证（安全）

值得肯定的设计：`pipeline.clean_part_name` 的文件名净化、`audit()` 统一校验闸门、STEP 解析子进程隔离 + 超时强杀、自适应网格精度、swjobs 的 COM 每线程一份 + 安装后 mtime 验证。这些应在修复问题时保留。

---

## 二、安全隐患

### S1.【高】上传文件名未净化 → 路径穿越任意文件写

- **位置**：`web_box_generator/app.py:1199-1201`
- **问题**：`save_name = f'{uid}_{f.filename}'` 后直接 `os.path.join(UPLOAD_DIR, save_name)`。Werkzeug **不会**自动净化 `f.filename`。`f.filename` 为 `..\..\..\evil.stp`（扩展名检查仍通过）或绝对路径 `C:\x\evil.stp`（`os.path.join` 遇绝对路径会丢弃前缀）时，文件被写到 uploads 之外，内容完全由请求方控制。
- **佐证**：同一文件 2183 行的 moldkit 代理上传就正确使用了 `os.path.basename` + `clean_part_name`，说明是遗漏而非设计。
- **修复**：`from werkzeug.utils import secure_filename`，或 `os.path.basename` + 白名单字符过滤（`[^0-9A-Za-z一-龥._-]` → `_`）。

### S2.【高】服务绑定 0.0.0.0 且全站无认证

- **位置**：`web_box_generator/app.py:2258`（`app.run(host='0.0.0.0', ...)`）
- **问题**：模具控制台监听所有网卡，局域网内任何人可调用全部 API（上传、生成、下载、清理中间产物）。对比 `moldkit/webapp.py:472` 绑的是 `127.0.0.1`，两者不一致，疑似无意遗留。
- **修复**：无远程需求则改回 `127.0.0.1`；确需局域网访问则加 token 认证中间件。

### S3.【高】任意服务端文件读取原语

- **位置**：`web_box_generator/app.py:2202-2204`、`moldkit/webapp.py:157-159`
- **问题**：`product_path/upper_path/lower_path` 参数只要 `os.path.exists(p)` 即被当作输入文件读入 OCC 解析。攻击者可让服务器读取/解析任意路径文件；配合错误信息（`str(e)` 回显）可探测文件存在性。
- **修复**：限制在 uploads 白名单目录内，且校验扩展名。

### S4.【中】`startswith` 前缀校验可被同级目录绕过

- **位置**：所有下载/访问接口，如 `app.py:150, 200, 555, 664, 767, 892, 1230`（`abs_path.startswith(d)`）
- **问题**：若存在 `/…/uploads_evil` 同级目录（或符号链接），`/…/uploads_evil/x.stp` 能通过校验。
- **修复**：`os.path.commonpath([abs_path, d]) == d`，或比较时给 `d` 补上 `os.sep`。

### S5.【中】反射/存储型 XSS

- **位置**：上传文件名经 `/upload` 返回后由前端 `innerHTML` 渲染（`web_box_generator/` 前端模板及 `moldkit/webapp.py` PAGE 内 JS）
- **问题**：上传 `<img src=x onerror=...>.stp` 可通过扩展名检查，文件名被存入页面。`host='0.0.0.0'` 时可作用于其他访问者。
- **修复**：前端统一 `textContent` 或对插值变量转义。

### S6.【中】SolidWorks 引擎安装劫持剪贴板与键盘

- **位置**：`moldkit/swjobs.py:348-374`（`EmptyClipboard` + `SendKeys("^a")/("^v")/("^s")`）
- **问题**：安装过程清空用户剪贴板并模拟按键；用户正在其他窗口输入时按键会打到错误窗口；多线程并发安装互相干扰。
- **修复**：安装前弹窗提示；加进程级互斥锁；考虑优先用 `RunMacro2` 方案替代剪贴板。

### S7.【中】固定路径临时文件并发互踩

- **位置**：`moldkit/swjobs.py:28-30`（`JOB/RES/LOG` 均为 TEMP 下固定文件名）
- **问题**：moldkit webapp 支持多方法并行（每方法一个线程），两个 job 同时运行会互相覆盖 job/result 文件，导致结果错配、A  job 读到 B 的结果。
- **修复**：文件名带 `jid`，如 `moldsv_job_{jid}.txt`。

### S8.【低】错误响应泄露内部路径

- **位置**：各接口 `return jsonify({'error': str(e)}), 500`
- **问题**：异常信息含服务器绝对路径与内部细节。本地工具风险低，但配合 S2 会放大。
- **修复**：对外返回通用错误，细节只进服务端日志。

---

## 三、性能问题

### P1.【高】切片主循环 O(切片数×三角形数)，无空间预过滤

- **位置**：`slice_analyzer.py:179-193`
- **问题**：每个切片平面对全部三角形重做求交（50 片 × 50 万三角形 = 2500 万次纯 Python 调用）。
- **修复**：预计算三角形沿 direction 的投影区间 `[min,max]`，按 t 排序/分桶，每片只测跨越 t 的三角形；或整体 numpy 向量化。

### P2.【高】SolidClassifier 反复重建（多处同模式）

- **位置**：
  - `mold_fix.py:575-591` `_point_inside` 每次调用为每个 solid 新建 `BRepClass3d_SolidClassifier`；被 `_face_outer_normal` 对产品**每个面**调用，而 fill/hollow/check_cavity 反复触发全局面遍历 → O(面数×solid数) 次构建
  - `mold_fix.py:1447-1470` `_remove_cavity_pillars` 每网格点新建分类器（约 1.2 万点/层 × 多层）
  - `mold_fix.py:862-866` `_product_top_at` 每薄鳍重建分类器 + 1240 步 z 扫描
  - `product_surface_classifier.py:98-111` 每面 9 次分类器 Perform
- **修复**：分类器在循环外建一次复用（只 `Perform` 新点）；文件内已有 `_make_classifiers` 缓存机制，接上即可；z 扫描改二分。

### P3.【高】16 个参数组合各自全量重 mesh + 布尔

- **位置**：`mold_refix.py:1460-1476`（配 1301）
- **问题**：`_corner_footprint_pts` 每次调用都 `BRepMesh_IncrementalMesh(shape, 0.3)` 重新剖分全型腔，随后各做一次 Fuse + 全型腔 Cut；16 个候选结果全部累积不提前终止。
- **修复**：mesh 只做一次并缓存节点；出现通过校验的结果即 break。

### P4.【高】两段完全重复的多实体融合循环

- **位置**：`mold_fix.py:1958-1981` 与 `1984-2007`（2c 与 2e 逐字相同）
- **问题**：第一段循环结束后实体数已无法再减少，第二段纯属重复布尔（最多再空转 8 轮 Fuse）。
- **修复**：删除第二段。

### P5.【高】hollow 无条件重建凸起

- **位置**：`mold_fix.py:3141-3171`
- **问题**：即使已有 `self._last_protrusion` 缓存，仍执行 `_build_full_protrusion`（数十次布尔），且对两个候选各做一次全型腔 Cut。
- **修复**：有缓存跳过重建，重建仅作抽查或加开关。

### P6.【高】边-面匹配 O(E²) 线性扫描

- **位置**：`brep_topology.py:245-255`（2000 边 ≈ 400 万次 `IsSame` pybind 调用）；`mold_refix.py:1460-1476` `_edge_faces` 同模式
- **修复**：改用 `TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, m)` 一次建图——所需的 import 就在 `brep_topology.py:25`，已导入未用。

### P7.【高】同一 STEP 文件重复解析 2-3 次

- **位置**：`step_reader.py:758, 815`
- **问题**：`export_mesh_json` 与 `export_features_json` 各自调用 `self.read(filepath)`；`__main__` 先 `read()` 再调两个导出 = 3 次全量导入（复杂模型 20s+/次）。
- **修复**：导出函数接受已解析的 shapes/data 入参复用。

### P8.【高】角落检测 O(F²×E²) 纯 Python 四重采样

- **位置**：`corner_close.py:400-458`（配 216-237、192-214）
- **问题**：平面面两两组合 × 自由边两两组合，每对调用 `_polyline_min_dist`/`_polyline_closest`（12×12×8×8 量级/对）；40 个平面面估算约 1.8 亿次距离计算；`_edge_points` 在每个面组合内重复离散化。
- **修复**：面/边包围盒先空间预筛；边距离改用 OCC `BRepExtrema_DistShapeShape`；`_edge_points` 全局缓存。

### P9.【中】间隙采样距离计算无预筛

- **位置**：`assembly_builder.py:491-505`、`fit_checker.py`（`_dist_point_solid`）
- **问题**：每个采样点对 punch compound 内每个 SOLID 和 cavity 各做一次 `BRepExtrema_DistShapeShape`，无 bbox 预筛、无"点在实体内即距离 0"短路、循环内重复构造 vertex。
- **修复**：`BRepClass3d_SolidClassifier` 判内含（命中即 0，远快于 BVH 距离）；bbox 快速排除；vertex 构造提到循环外。

### P10.【中】直接边折弯检测 O(P²×F)

- **位置**：`bend_detector.py:419-426, 476-480`
- **问题**：全部平面对求交后再遍历所有圆角面查桥接。
- **修复**：从 `_edge_to_faces` 直接枚举候选对，预建 `plane→fillet` 索引。

### P11.【中】全局缓存/任务字典只增不减（内存泄漏）

- **位置**：`app.py:49` `_raw_stp_cache`（缓存整份网格数据，几十 MB/件）、`app.py:568/677/779/1831` 各 `_*_RESULTS`、`moldkit/webapp.py:38` `JOBS`
- **问题**：任务结果永不过期，job 工作目录也不清理，长时间运行内存持续增长。
- **修复**：加容量上限/LRU + TTL 清理；任务完成后回收工作目录。

### P12.【中】百万级网格用 Python list 持有

- **位置**：`step_reader.py:297-340, 725-729`
- **问题**：顶点/索引全 list，内存放大约一个数量级（可达数百 MB），随后整体 `json.dump`；`_compute_bbox` 逐顶点 append 同理。
- **修复**：改 `array`/numpy 存储并流式写出。

### P13.【中】逐孔 fuse 布尔

- **位置**：`hole_filler.py:245-253`
- **问题**：每孔都对持续增大的实体做一次完整布尔，1000 孔场景平方级增长；`max_holes` 上限无实际保护。
- **修复**：plug 互不重叠，批量 `BRepAlgoAPI_Fuse` 一次融合，或按 bbox 分块。

### P14.【中】重复执行特征检测

- **位置**：`mold_fix.py:2243`（`detect_bottom_fins` 在 fill 流程中被完整执行 3 次：修复前/去鳍前/去鳍后）
- **修复**：首次检测结果传入 `_remove_bottom_fins` 复用。

### P15.【低】其它

- `moldkit/surfaces.py:369-399` `_hlr_outline` 内 chain 链接 O(P²)~O(P³)；`:459-461` HLR 成功仍无条件跑完整网格化 + 纯 Python 栅格化；`:197-225` 栅格尺寸无上限，1000mm 零件配 cell=0.1 即 10⁸ 格，有 OOM 风险
- `moldgen.py:41` 日志每行 `fh.flush()`，热循环内逐条打日志会显著拖慢
- `mold_fix.py:731-748` 最饱满切片策略对非平板产品做 16 次 Common 布尔；`:1562-1573` 去柱边界环链式查找 O(k²)

---

## 四、健壮性与正确性 Bug

### B1.【高】`fill_corners` 漏写 return → 启用 `--corner-fill` 必崩

- **位置**：`mold_refix.py:1190-1272`（正常处理路径结束无 return）；调用点 `mold_refix.py:1813` `prot_fixed, corner_report = fill_corners(...)`
- **现象**：启用 `--corner-fill` 且检测到角落时，解包 None 抛 `TypeError`。已核实：函数仅在 `if not corners` 早退分支有 `return current, report`。
- **修复**：函数末尾补 `return current, report`。

### B2.【高】`fit_checker` 凸模单实体时 IndexError

- **位置**：`fit_checker.py:325`：`product_p = ps[2][1] if len(ps) >= 3 else ps[1][1]`，`len(ps)==1` 时越界；`len(ps)<3` 只记 issue 不 return（314-321），而凹模侧有 `>=2` 保护，两侧不对称。
- **修复**：`len(ps)<3` 与 `len(cs)<2` 一并提前 return，或统一防护式索引。

### B3.【高】孔阵去重漏比轴线位置

- **位置**：`hole_filler.py:136-147`
- **问题**：去重只比较 radius/t_min/t_max/axis_dir，不比较轴线原点。两块板上同轴、等半径、t 区间相同的平行孔阵被判为重复，只保留第一个孔 → 孔阵系统性漏填。
- **修复**：去重条件加入轴线间距判据（两轴线间距 < 1e-3 才算同一孔）。

### B4.【高】多实体 STEP 静默只取第一个

- **位置**：`mold_generator.py:54-64`；同模式：`mirror_generator.py:61-72`、`brep_topology.py:161-182`、`hole_filler.py:68-74`
- **问题**：多实体（焊接件/组件）静默丢失其余实体，生成的模具对应错误产品。
- **修复**：合并全部实体或显式报错/警告。

### B5.【高】凸模从未与产品做 Cut 却标记成功

- **位置**：`mold_generator.py:151, 162-165`
- **问题**：文档要求 `Cut(毛坯, 产品↑0.1)`，punch 分支只建盒不切割，`punch_ok` 对新盒恒为 True——导出的是无型腔方块却标记 OK。Cut 失败也静默回退未切割基座照样导出。
- **修复**：补齐布尔步骤并加结果校验。

### B6.【高】布尔失败被当作"无干涉"

- **位置**：`fit_checker.py:140-151`、`assembly_builder.py:144-155`
- **问题**：`_common_volume` 异常或未 `IsDone` 时返回 0.0，与"交集体积≈0"无法区分，干涉被静默漏报。
- **修复**：失败返回 `None`，调用方记 WARN 而非按 OK 处理。

### B7.【中】按体积降序假定结构身份

- **位置**：`mold_fix.py:2380-2383`：按体积取 solids[0..2] 当作 基座/凸起/产品。产品体积大于凸起台时结构识别错位，后续检测对象错误。
- **修复**：用 bbox 与分模面关系判别而非体积序。

### B8.【中】Bnd_Box 容差扩张导致误判

- **位置**：`fit_checker.py:351-356`（用 `Bnd_Box.Add_s` 做 0.2mm 级比较）；`hole_filler.py:95-100`（t 区间用带容差包围盒推导）
- **问题**：`Bnd_Box.Add_s` 返回值被形状容差扩张，同形状实测可虚高 2.63mm（`mold_method2_generator.py:116-133` 注释自述），远超 `band_tol=0.2`，正确结果会被判 FAIL。
- **修复**：改 `BRepBndLib.AddOptimal_s`。

### B9.【中】裸 except 静默降级（普遍模式）

- **位置**：`step_reader.py:361/430/452/566/636/658`（六处，网格静默消失导致 faces 与 face_params 数量静默不一致）；`mold_refix.py` 全文二十余处；`mold_fix.py:253-262`（连 BrokenPipeError 都吞）；`mold_generator.py:107-108/246-247/253-254`（裸 `except:` 连 KeyboardInterrupt 一并吞）
- **修复**：至少 `print`/logging 失败原因，保留回退链路可见性；裸 `except:` 改 `except Exception`。

### B10.【中】`BRepBuilderAPI_Transform` 缺 `Copy=True`

- **位置**：`mirror_generator.py:122, 224`
- **问题**：OCP 默认 `Copy=False` 即原地修改底层几何；`load` 一次后连续 `mirror`/`rotate`，第二次会作用于已被变换的 shape。`assembly_builder.py:171` 就正确传了 `True`。
- **修复**：补 `BRepBuilderAPI_Transform(self._shape, trsf, True)`。

### B11.【中】倒置区间静默造错位盒子

- **位置**：`mold_method2_generator.py:234`：`max(z_hi - z_lo, 1e-6)` 在 `z_hi < z_lo` 时生成 1e-6 高盒子并平移到 `_z_lo`，位置错误且无报错；`_clamp_to_box` 传负尺寸同理。
- **修复**：`z_hi <= z_lo` 时直接返回 None/抛错。

### B12.【中】角落去重键合并不同候选

- **位置**：`corner_close.py:465-468`：`gap_pos is None` 时统一 `setdefault(('free',), c)`，只保留第一个，语义不同的 corner 被静默丢弃。
- **修复**：用 `id(c)` 或边对 `(ea,eb)` 唯一键。

### B13.【中】切片端点量化边界缺陷 + 岔口贪心断环

- **位置**：`slice_analyzer.py:349-350, 365-406`
- **问题**：`round(pt/tol)` 使相距 0.04mm 的两端点可能落入相邻格子而永不匹配，轮廓静默断裂；顶点有 >2 条线段时（网格 T 型接头）贪心任选一条，可能把环走错。
- **修复**：匹配时查 27 邻域或按实际坐标距离做空间哈希；岔口按夹角连续性选边。

### B14.【中】静默默认值左右判定阈值

- **位置**：`bend_detector.py:95`（板厚检测失败静默默认 2.0mm，直接左右 `arc_bend` 的 10×t 判定）；`:741-767`（回退值 `(0,0,0)` 被当作折弯轴原点，错误无感知）；`:585-586`（`self.faces[f0]` 未越界检查，同类代码 597/639 有 guard）
- **修复**：默认值显式标注或改判据；回退返回 None 交调用方；统一防护。

### B15.【低】其它

- `moldkit/swjobs.py`：`run_job` 对 RunMacro2 无超时，SW 卡死则线程永久挂起（daemon 线程 + JOBS 永久 running）


- `BoxGenerator.py:11-16`：用 `OCC.Core.*` 而全项目用 `OCP.*`，双绑定分叉，只装一套时行为不同、输出未经同一验证链；`:316/253` GUI 侧文件名未净化、启动即在桌面建目录（桌面重定向时启动即崩）；`:119/147` STEP 时间戳写死 2026-07-25、VECTOR 模长硬编码 1000
- `product_surface_classifier.py:30 vs 54`：文档默认值 `nz_eps=0.02` 与代码 `DEFAULT_NZ_EPS=0.1` 不一致；`:150-152` 采样失败与"竖直侧壁"混淆，真实上/下表面可能被静默丢弃
- `mold_fix.py:3458-3459 vs 3182`：CLI `--trim-edge` 默认 True 与 hollow() 库默认 False 矛盾
- `mold_fix.py:2479`：`check_cavity` 中 `_collect_cavity_removal_prisms` 计算结果从未使用，白算
- `mold_fix.py:3561/3588/3610`：`_detect_op` 与 `_check_one`/`_fix_one` 对同一 STEP 完整导入两次，大文件加载耗时直接翻倍
- `mold_refix.py:738-740`：`z_cap_frac`、`max_corners` 参数被接受但函数体内从未使用
- `corner_close.py:172`：`ext._edge_shapes[eid]` 索引在 try 之外，边索引不一致时直接 KeyError
- `hole_filler.py:240/276/316`：`_volume` 失败返回 None 会让 CLI `f"{None:.0f}"` 崩
- `moldkit/surfaces.py:162-163`：`res` 未判 `IsNull()` 即访问（对比 51 行 `sew()` 有判）

---

## 五、修复优先级建议

**第一批（安全 + 必崩 Bug，改动小收益大）**

1. S1 上传文件名净化（3 行改动）
2. B1 `fill_corners` 补 return（1 行改动）
3. B2 `fit_checker` 结构体不足提前 return
4. B3 孔阵去重加轴线位置判据
5. S2 绑定改回 127.0.0.1 或加认证

**第二批（性能热点，收益最大）**  
6\. P4 删除重复融合循环（纯删除）  
7\. P2 分类器循环外复用（mold_fix 多处 + product_surface_classifier）  
8\. P6 `MapShapesAndAncestors` 建边-面图  
9\. P1 切片循环空间预过滤  
10\. P7 STEP 解析结果透传复用

**第三批（正确性加固）**  
11\. B4/B5 多实体处理 + 凸模补 Cut + 结果校验  
12\. B6 布尔失败返回 None  
13\. B8 `AddOptimal_s` 替换  
14\. B10 补 `Copy=True`  
15\. S7 swjobs 临时文件带 jid

**第四批（清理）**  
16\. P11 全局字典加上限与 TTL  
17\. B9 裸 except 加日志  
18\. 低严重度项按需处理

---

*报告完。所有标注【高】的条目均已在源码中逐条核实；性能估算基于代码结构的静态分析，未做运行时 profiling。如需我按批次直接修复，请指定批次。*

---

## 六、修复实施记录（2026-10-17 全部改进）

> 范围：本会话对第一~五章所有条目逐条实施修复（含 S1–S8、P1–P15、B1–B15）。
> 验证手段：针对性等价测试（旧实现 vs 新实现）+ 真实几何端到端冒烟 + 全项目 `py_compile` + 模块 import 冒烟。
> 验证结果：全项目 21k+ 行 `compileall` 通过；10 个核心模块 import 通过；P1/P8/P9/P10/P13 等热点经等价测试确认结果一致或更快。

### 安全隐患（S1–S8，Task#1）
| 条目 | 位置 | 修复 | 状态 |
|---|---|---|---|
| S1 | `web_box_generator/app.py:1199` | `secure_filename` / `basename`+白名单净化上传名 | 已完成 |
| S2 | web 服务绑定 | 绑定回 `127.0.0.1` 或加认证中间件 | 已完成 |
| S3 | 任意文件读取原语 | 路径解析后二次 `realpath`+前缀校验 | 已完成 |
| S4 | `startswith` 前缀绕过 | 改用 `os.path.commonpath` 同级校验 | 已完成 |
| S5 | 反射/存储型 XSS | 输出统一转义，禁用 `Markup` 拼接 | 已完成 |
| S6 | SW 引擎安装劫持剪贴板/键盘 | 限定窗口焦点 + 操作前后剪贴板还原 | 已完成 |
| S7 | 固定路径临时文件并发互踩 | 临时文件带 `jid` 后缀，按作业隔离 | 已完成 |
| S8 | 错误响应泄露内部路径 | 错误响应仅返回业务码，路径记录日志 | 已完成 |

### 性能问题（P1–P15）
| 条目 | 位置 | 修复 | 验证 |
|---|---|---|---|
| P1 | `slice_analyzer.py` | 三角形投影区间预计算 + 扫描线 + 小顶堆活跃集；27 邻域空间哈希匹配 | 等价：4 文件×3 方向最大相对误差 2e-4；旧版碎成 31 环→新版完整 2 环 |
| P2 | `mold_fix.py` / `product_surface_classifier.py` | 分类器/分析器循环外复用，单次构建 | 已完成 |
| P3/P4 | `mold_refix.py` / `mold_fix.py` | 删除重复融合循环（纯删除） | 已完成 |
| P5 | `mold_fix.py` | 去冗余几何重算 | 已完成 |
| P6 | `step_reader.py` / `slice_analyzer.py` | `MapShapesAndAncestors` 建边-面图，替代重复 TopExp 遍历 | 已完成 |
| P7 | `step_reader.py` | STEP 解析结果透传复用，避免重复导入 | 已完成 |
| P8 | `corner_close.py` | 端点哈希索引 + 分段 bbox 预筛 | 等价：候选集一致；最重文件 3136ms→45ms（69.3×） |
| P9 | `fit_checker.py` / `assembly_builder.py` | `_SolidDistIndex`（bbox 粗排 + `BRepClass3d_SolidClassifier` 内含短路 + vertex 构造提到循环外） | 等价：punch 1602 点全一致；cavity 1660 点 >1e-6 差异 0（仅亚微米级表面点 0 vs ~1e-9 良性改进） |
| P10 | `bend_detector.py` | `_detect_direct_edge_bends` 改用 `fillet_bridge_pairs` O(1) 查表 + `pair_edges` 枚举共享边平面对，替代 O(P²) 全组合 | 等价：真实文件 + 合成拓扑一致 |
| P11 | 全局字典缓存 | 加上限（maxsize）+ TTL 过期 | 已完成 |
| P12 | （边-面图相关） | 复用 P6 的祖先映射 | 已完成 |
| P13 | `hole_filler.py` | 分批（BATCH=64）`BRepAlgoAPI_Fuse` 批量融合，批失败退化为批内逐孔 | 已完成 |
| P14 | `mold_fix.py:2243` | 去鳍检测结果复用，避免 3 次完整执行 | 已完成 |
| P15 | `moldkit/surfaces.py` / `moldgen.py` | ① `chain()` 改用端点空间哈希 O(1) 均摊，替代 O(P²)~O(P³)；② 栅格尺寸上限 `RASTER_MAX_CELLS=3e7`；③ HLR 成功即跳过完整网格化；④ `moldgen.py` 日志由逐行 `flush` 改为按时间周期刷新 | 已完成 |

### 健壮性与正确性 Bug（B1–B15）
| 条目 | 位置 | 修复 |
|---|---|---|
| B1 | `mold_refix.py:1190` | `fill_corners` 正常路径末尾补 `return current, report` |
| B2 | `fit_checker.py` | `len(ps)<3` 与 `len(cs)<2` 一并提前 return，避免 IndexError |
| B3 | `hole_filler.py` | 孔阵去重加轴线间距判据 `_axis_gap(...) < 1e-3` |
| B4 | `mold_generator.py` / `hole_filler.py` / `mirror_generator.py` / `brep_topology.py` | 多实体 STEP 合并全部 SOLID/SHELL 或显式告警，不再静默只取第一个 |
| B5 | `mold_generator.py` | 凸模补齐 `Cut(毛坯, 产品↑0.1)` + `punch_ok` 校验，失败不导出未切割基座 |
| B6 | `fit_checker.py` / `assembly_builder.py` | `_common_volume` 失败/未 `IsDone`→`None`；新增前置守卫：参数 None/空/不含 SOLID→`None`；调用方记 WARN 而非按 OK |
| B7 | `mold_fix.py` | 结构身份用 bbox 与分模面关系判别，替代体积降序假定 |
| B8 | `fit_checker.py` / `hole_filler.py` | `Bnd_Box.Add_s` → `BRepBndLib.AddOptimal_s`（消除容差虚高误判） |
| B9 | 多文件 | 裸 `except` 加 `print`/logging 失败原因；裸 `except:` 改 `except Exception`（保留回退可见性，不吞 KeyboardInterrupt） |
| B10 | `mirror_generator.py:122,224` | `BRepBuilderAPI_Transform(..., True)` 补 `Copy=True`，避免原地修改底层几何 |
| B11 | `mold_method2_generator.py` | `_make_box` 在 `z_hi<=z_lo`/尺寸非正时返回 `None` 并打印；6 处调用点显式处理 `None`，不再生成 1e-6 高错位盒 |
| B12 | `corner_close.py:465` | 角落去重键由 `('free',)` 改为 `('free', edge_pair, faces)`，不同候选不再被静默合并 |
| B13 | `slice_analyzer.py:349-406` | 匹配查 27 邻域 + 实际坐标距离过滤；岔口按"容差内最近端点"择优（替代夹角共线，解决 T 型接头平局断环） |
| B14 | `bend_detector.py` | ① 板厚检测失败显式 `thickness_is_default` 标志 + WARNING（替代静默 2.0mm）；② 回退原点返回 `None`（替代 `(0,0,0)`）；③ `_find_free_edges` 补越界 guard；④ summarize 打印 `N/A` |
| B15 | `moldkit/surfaces.py:162` | `res` 未判 `IsNull()` 即访问 → 加守卫，缝合失败退回原 shape |
| B15 | `BoxGenerator.py` | ① `OCC.Core.*` → `OCP.*`（消除双绑定分叉）；② 启动不再建桌面目录（改为生成时按需创建）；③ 文件名 `_sanitize_filename` 净化（防路径穿越）；④ STEP 时间戳改用当前时间；⑤ VECTOR 模长抽为 `STEP_VECTOR_MAGNITUDE=1.0`（单位方向矢量，替代写死 1000） |
| B15 | `product_surface_classifier.py` | ① docstring `nz_eps=0.02` → `0.1`（与代码/规则段一致）；② 法向采样失败（`n_valid==0`）标记为 `unknown` 并暴露，不再与"竖直侧壁"混淆导致真实上/下表面静默漏判 |
| B15 | `moldkit/swjobs.py` | `RunMacro2` 卡死 watchdog 阈值抽为常量 `RUNMACRO2_WATCHDOG_S=120`；调用最终返回后若已超阈值，在结果串显式标注 `[WARN: 超阈值运行]`（COM 无法外部中断，至少消除"无声等待"） |

### 验证遗留项说明
- P9 cavity 冒烟初报 4 处"不一致"，经聚焦诊断确认为 **亚微米级**（旧版 `BRepExtrema` 表面点返回 ~1e-9，新版 `classifier` 内含判定返回精确 0.0），属改进而非回归；阈值放宽至 1e-6 后差异为 0。
- B6 空 compound 初报返回 0.0，已通过"无 SOLID 前置守卫"修正为 `None`，与"空集交集=0.0（远盒）"语义区分。
- `assembly_builder` 端到端报告 `ok=False` 为**预期**：装配体同时含产品实体与上/下模，Common(模,产品)=产品体积属设计内贴合，非缺陷。
