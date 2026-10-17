# 方法三（SolidWorks VBA 宏）—— 最终结论（2026-09-20）

## ✅ 结论：**VBA 路线完全打通，已能在 SolidWorks 里真实生成上下模，并用 OCC 校验通过**

实测产物（2ROM31-6061）：

| 零件 | 实体/壳/面 | 体积 | 包围盒 (mm) | BRepCheck |
|---|---|---|---|---|
| 上模 `2ROM_PUNCH_M3C.stp` | 1 / 1 / 24 | 386 438 mm³ | x[-120,120] y[-25,25] z[3,72] | 有效 |
| 下模 `2ROM_CAVITY_M3.stp` | 1 / 1 / 23 | 1 661 453 mm³ | x[-125,125] y[-30,30] z[-67,71] | 有效 |

- 上模底面 = 上形变面（z 最低 3.000），顶面 = 高度面 P = 72.000 ✓
- 下模型腔底 = 下形变面（z 最低 1.000），基座顶 = 71.000 ✓
- **上下模干涉 = 0.0000 mm³** ✓
- 生成耗时：**2.9 秒 / 个**（旧版 `InsertCutSurface` 路线是静默失败，OCC 多实体路线上限 20 面）

---

## ★核心思路：不要"用曲面去切"，改成"让 SolidWorks 自己算终止位置"★

```
上模 = 在 z = P 的基准面上画形变面的投影轮廓
        → 向下【成形到曲面】拉伸到上形变面
下模 = 先拉伸一个基座块
        → 在基座顶面画投影轮廓，向下【成形到曲面】切除到下形变面
```

这两式**正好等价于法二A**：
- 法二A 上模 = 形变面各面沿 +Z 拉伸的并集，再裁到 z ≤ P
- 法二A 下模 = 基座 − （下形变面各面沿 +Z 拉伸的并集）

好处：**没有布尔、没有曲面切除**，只有 SW 最标准的两个拉伸特征，秒级完成。

### 为什么旧版"使用曲面切除"必然失败
SW 要求曲面把实体**切断**。旧版盒体四周都比形变面大 5mm，
形变面的边界整圈悬在实体内部 → 实体能从边界外侧绕过去 → **静默不切**
（实测 `InsertCutSurface` 返回 `err=0`、特征对象非空，但体积前后一位不差）。

---

## ★从 SW 自带帮助里查证到的关键 API 语义★

> `sldworksapi.chm` 可以用 `hh.exe -decompile` 解出 14400 个 HTML，
> 里面**带官方 VBA 示例**。这一条比任何猜测都值钱。

| 事实 | 出处 |
|---|---|
| `FeatureExtrusion3` 选择标记：3D草图=0、拉伸方向参考边=16、**起始条件参考实体=32**、**终止条件参考实体=1**、受影响实体=8 | `IFeatureManager~FeatureExtrusion3` Remarks |
| **`Dir=True` 才是翻转拉伸方向**；`Flip` 是"翻转切除侧" | 同上 |
| 凸台默认沿草图法向，切除默认与草图法向**相反** | 同上 |
| `T1`：0=盲 4=成形到曲面 7=成形到实体 10=成形到选定（10 取代 4、3） | `swEndConditions_e` / `HoleWizard5` Remarks |
| `T0`：0=草图基准面 3=等距（配 `StartOffset`+`FlipStartOffset`） | `swStartConditions_e` |
| `InsertRefPlane(8, 距离, 0,0,0,0)`，8 = `swRefPlaneReferenceConstraint_Distance` | `Convert_Edges_and_Inner_Loops...Example_VB` |
| `SketchUseEdge3(Chain, InnerLoops)` 返回 **Boolean** | `ISketchManager~SketchUseEdge3` |
| 草图**打开期间**就可以选模型实体再转换实体引用 | 同上官方示例 |

最终扫描出来的**唯一可用组合**：`SURFACEBODY / mark 1 / T1=4 (成形到曲面)`

---

## ★踩过的坑（每条都是实测，不是推测）★

### 几何/语义类

1. **★投影轮廓必须是"最外环"，且必须避开形变面上的孔★**
   - SW 的「成形到曲面」要求终止曲面**完整盖住草图轮廓**。
   - 形变面的 XY 投影只有包围盒的 **76~92%**（1131 是个机头罩壳，投影是半椭圆），
     所以**包围盒矩形轮廓必然失败**。
   - 2ROM 的形变面投影中间有圆孔，轮廓盖住孔 → 孔处没有曲面可终止 → 返回 `Nothing`。
     **实测确认**：把轮廓换成避开孔的角落小矩形，立刻 `★ 成功`。
   - 解决：在 OCC 侧把形变面的孔用 `BRepBuilderAPI_MakeFace` 补平（`m3_fill.py`），
     孔处上下模相互贴合 —— 真实成形模在孔的位置本来就是上下模对压的。

2. **★轮廓点表首尾必须去重★**
   `matplotlib` 的 `contour` 返回的闭合环**首尾点重合**。原样喂给 VBA，
   会多画一条 "点n → 点1" 的**零长度线段** → 草图轮廓失效 → 拉伸返回 `Nothing`。
   **这一个坑让「成形到曲面」白失败了一整轮。**

3. **★上下模会天然干涉，这是法二A的固有特性★**
   上下形变面是沿**板料法向**偏 2mm（板厚）的，不是 Z 向偏 2mm。
   在近竖直壁处，上表面的竖直投影会低于下表面的竖直投影 → 上下模咬入。
   实测咬入量 **6863 mm³ ≈ 板厚 2mm × 壁高 68mm × 壁长 50mm**，位置正是 x=±71 的两面墙。
   解决：上模 − 下模（`m3_post.py`），复核干涉 = 0.0000 mm³。

### VBA / SW 环境类

4. **对曲面体调 `SketchUseEdge3` 返回 `True` 却生成 0 个实体**（日志铁证）。
   逐个选**边**能产出（2ROM 50 个实体），但会混进面间内部接缝 + 孔洞环。
   —— 所以轮廓最终改由 OCC 算好、以点表形式喂进来。

5. **`FeatureExtrusion3` 用 `FACE` 参考 + `mark 2` 会把 SW 打崩**（RPC 0x800706BE）。
   终止参考扫描里必须去掉这组。

6. **SW 导出的 STEP 是 `SHELL_BASED_SURFACE_MODEL`，不是 `MANIFOLD_SOLID_BREP`**
   —— 因为文档里残留的输入曲面体（13 面）没被删掉，SW 就把整体按曲面导出，
   读回来 = **0 实体**，下游装配/干涉检查全部失效。
   两层解决：宏里 `DeleteSurfaceBodies` 多级重试 + 复查；
   `m3_post.py` 取闭合壳 `BRepBuilderAPI_MakeSolid` 做格式归一。

7. **`CreateCornerRectangle` 返回的是【草图实体数组】不是 Boolean**
   —— 只能用语句形式调用，`ok = ...` 再 `CStr(ok)` 会运行时报错。

8. **`CloseStaleDoc`：上轮中途报错的文档会留在 SW 里**，下轮 `LoadFile4` 直接返回它，
   日志表现为莫名其妙的"文档里没有体"。

9. **`Dispatch` 之后要等 SW 稳下来**（冷启动 20s），否则 `远程过程调用失败`。

10. **SW 全部 API 里没有"程序化创建/打开宏编辑器"的接口**
    —— 只有 `RunMacro2` / `GetMacroMethods` 能操作已有 `.swp`。
    所以每次改宏代码都要人工开一次 VBE（Alt+F11）粘贴保存。

11. `Attribute VB_Name` 不能粘进代码窗口；`RunMacro2` 只能调零参 `Public Sub`；
    config 文件必须 ANSI/GBK（Python 侧 `encoding='mbcs'`）；路径尽量纯 ASCII。

---

## 复现步骤

```powershell
cd C:\Users\何雨卓\Desktop\SF\S2
$py = .\venv\Scripts\python.exe

# 1) 形变面：缝合 → 补孔                      （OCC 预处理）
$py .\_m2_debug\m3_fill.py

# 2) 算投影轮廓点表（最外环 + 内缩 0.5mm）      （OCC 预处理）
#    需要先把补孔后的曲面喂给 m3_outer.py（脚本里改 items 指向 *_filled*.stp）
$py .\_m2_debug\m3_outer.py

# 3) 装宏（需 VBE 已打开）→ 跑上模/下模        （SolidWorks VBA）
$py .\_m2_debug\m3_drive.py install
$py .\_m2_debug\m3_drive.py run punch  "SRC=C:\Temp\m3test\upper_filled2rom.stp" "POLY=C:\Temp\m3test\2ROM_upper_poly.txt"
$py .\_m2_debug\m3_drive.py run cavity "SRC=C:\Temp\m3test\lower_filled2rom.stp" "POLY=C:\Temp\m3test\2ROM_lower_poly.txt"

# 4) 归一化 + 去干涉 + 出验证图                （OCC 后处理）
$py .\_m2_debug\m3_post.py
$py .\_m2_debug\m3_verify.py
```

## 相关文件

| 文件 | 说明 |
|---|---|
| `mold_method3_macro.txt` | VBA 几何宏源码（最终版；含全部实测结论注释） |
| `mold_method3.swp` | SW 里的宏（模块 `mold_method31`，入口 `RunM3`） |
| `_m2_debug/m3_drive.py` | 全自动驱动：贴宏进 VBE → 保存 → `RunMacro2` → 收结果 |
| `_m2_debug/m3_fill.py` | 形变面补孔 |
| `_m2_debug/m3_outer.py` | 投影轮廓点表（栅格 + 内缩 + 最外环 + 简化） |
| `_m2_debug/m3_post.py` | 壳→实体归一化 + 上下模去干涉 |
| `_m2_debug/m3_verify.py` | 干涉检查 + 三视图验证图 |
| `_m2_debug/m3_out/*.stp` | 产物（`*_M3.stp` 忠实法二A；`*_M3C.stp` 已去干涉） |
| `_m2_debug/swhelp.py` | SW 自带 API 帮助的纯文本查询器（改宏前先查它） |
