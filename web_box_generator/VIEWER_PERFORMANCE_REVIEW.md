# 三维查看器（web_box_generator）性能与压缩体检

> 检查对象：`web_box_generator/templates/index.html`（1884 行前端查看器）
> + `web_box_generator/app.py`（2324 行 Flask 后端）
> 关注点：几十万三角面的前端性能、glTF 压缩、几何有效性/干涉在查看器中的呈现
> 测量日期：2026-10-17

## 结论先行

查看器**功能完整、结构合理**，但在"大数据量几何传输"和"大模式优化"上有 3 个高影响问题，其中 2 个是**确定性 bug**（优化形同虚设），1 个是**架构级压缩缺失**（你最担心的 glTF 压缩）。后端 STEP 解析（子进程 + 自适应精度 + 20 万面上限）反而做得不错，瓶颈全在"几何怎么传到浏览器、怎么在浏览器里建出来"。

---

## 一、实测几何传输体积（核心问题）

`/api/mesh`、`/upload`、`/api/assembly_product_mesh` 全部用 `jsonify()` 把网格以**纯文本 JSON** 下发：
`vertices`（float 扁平数组）+ `triangles`（int 扁平数组）+ BREP 拓扑。

**真实测量（项目 uploads 内 STEP，直接用 `_parse_step_worker` 跑）：**

| 模型 | 三角面 | JSON 体积（default） | JSON 体积（compact） |
|---|---|---|---|
| YA-1238-01_MIRROR_xOy | 64,750 | 13.19 MB | **12.36 MB** |
| 1clb1070-71a | 27,070 | 8.79 MB | **8.24 MB** |
| 2CJA31-5601 | 28,656 | 4.66 MB | **4.33 MB** |

- 后端 `MAX_TRIANGLES = 200000`（`app.py:1452`）→ 最坏情况单模型 ≈ **38 MB** 文本。
- 装配模块分三次拉取（上模/下模/产品）→ **100 MB+ 文本** 无压缩。
- **没有 glTF、没有 Draco、没有 KTX2、没有 HTTP gzip**（grep 全项目无 `flask_compress`/`gzip`/`GLTFLoader`/`DRACOLoader`）。
- 前端还要 `new Float32Array(巨型JS数组)` 把文本数字再拷一遍进 GPU，解析期间主线程卡死。

**影响**：本地回环尚可，一旦走真实网络（或移动端）加载极慢、内存尖峰、`JSON.parse` 阻塞 UI。

### 修复方案（按收益排序）
1. **最优｜glTF + Draco**：后端用 `trimesh`/`pygltflib` 导出 `.glb`（Draco 几何压缩），前端 `GLTFLoader + DRACOLoader` 加载。几何可压到原 JSON 的 **1/10 ~ 1/50**，且天然支持流式/分块。
2. **次优｜二进制打包 + gzip**：后端把 `Float32Array(positions)` + `Uint32Array(indices)` 直接 `tobytes()` 下发（或 base64 嵌 JSON），前端零拷贝 `Float32Array`/`Uint32Array` 建几何；再装 `flask-compress` 开 gzip。可减 **3~5×**。
3. **顺手｜`jsonify` 紧凑分隔符**：现代 Flask 默认已是 `(',',':')`，确认配置即可，收益小但免费。

---

## 二、确定性 Bug：大模式快速路径形同虚设

- 前端：`BIG_MODEL_TRIS = 300000`（`index.html:487`）
- 后端：`MAX_TRIANGLES = 200000`（`app.py:1452`）

因为后端**永远不会**返回超过 20 万面的网格，所以 `totalT > BIG_MODEL_TRIS`（30 万）**恒为 false** → `bigMode` 永远是 `false`。
后果：`index.html:558-583` 里昂贵的 JS 路径**每次都跑**：
- `computeSplitNormals`（逐三角面 JS 循环，561 行）
- 表面法向分色标注（560-574 行，额外建 3 份顶点缓冲）
- `EdgesGeometry`（579 行，对 20 万面做二面角推断）

"大模型快速模式"是一段**死代码**——你以为对大件跳过了这些，其实没跳。

**修复**：把 `index.html:487` 的 `BIG_MODEL_TRIS` 改为 **≤ 后端上限**（建议 `150000` 或 `200000`）。仅此一项，20 万面模型即跳过上述三条最重路径。

---

## 三、渲染管线陷阱：composer + FXAA 让原生 MSAA 失效

`index.html:261` 渲染器开了 `antialias:true`，但 `index.html:303-308` 用 `EffectComposer` 且只挂了 `RenderPass + FXAA`。
- 用 composer 后，场景渲染进**非多重采样**的 render target，`antialias:true` 被旁路 → **免费的抗锯齿白丢了**，只剩质量较低的 FXAA。
- 且每帧多一次全屏 pass 开销。

而当前 composer 里**没有任何真正需要后处理的 pass**（无 bloom/无 outline），FXAA 就是唯一用途。

**修复（二选一）**：
- **最简**：删掉 composer，直接 `renderer.render(scene,camera)` + 保留 `antialias:true` → 免费拿到高质量 MSAA，还省一个全屏 pass。
- **保 composer**：给 composer 的 render target 设 `samples:4`（`new THREE.WebGLRenderTarget(w,h,{samples:4})` 传给 `EffectComposer` 构造或赋值 `composer.renderTarget1/2`），恢复 MSAA。

---

## 四、GPU 显存 ×3：隐藏的克隆几何

`index.html:551、557` 对每个实体都 `geo.clone()` 给"结构体着色"和"线框"两组，**默认 `visible=false`** 却提前建好。
- 大网格（20 万面）下，主网格在 GPU 里存在 **3 份完整拷贝**（本体 + 着色 + 线框）。
- 这两组默认不显示，拷贝纯属浪费。

**修复**：懒构建——用户第一次勾选"结构体着色/线框"时再 `geo.clone()`，关闭时 `dispose()` 释放。显存 3× → 1×。

---

## 五、主线程焊接阻塞 UI

`index.html:539` 的 `weldBufferGeometry(geo, 1e-4)` **始终在主线程**运行（哪怕 bigMode），为大网格建一个 ~10万~20万键的 JS 哈希表，期间 UI 冻结数百毫秒到数秒。

**修复**：把 weld + split-normals 搬进 **Web Worker**（传 `ArrayBuffer`，回传 `Float32Array`/`Uint32Array`）；加载期间显示进度条；或对"非缝合展示"场景允许延迟焊接。

---

## 六、其它次要项

- **`addFeatureEdges` 用 fat line（LineSegments2）封顶 20 万点**（index.html:514、524）：大模型下 10 万段 × 8 段圆柱 = 80 万顶点，偏重；结合 Bug#2（bigMode 死代码）目前对大件**总是跑**。修好 Bug#2 后大件会自动跳过。
- **无流式/无 Worker 的整体加载**：fetch 回来后同步 parse→weld→法线→建网格，UI 全程空白。建议二进制流式 + Worker。
- **`/api/mesh` 每次重算**：`_clean_mesh`（Python 哈希去重 1e-7）和 `jsonify` 在每次请求都重跑；`_cache_raw` 已缓存解析结果（好），但**最终序列化响应未缓存**。可缓存序列化字节或预生成 `.glb`。
- JSON 默认分隔符：现代 Flask `jsonify` 已紧凑，确认配置即可，收益很小。

---

## 七、做得好的地方（保留）

- 后端 STEP 在**子进程**解析（`_parse_step_worker` + 进程池 + 超时强杀），避免服务端卡死/超时。
- **自适应精度 + 20 万面硬上限**（`app.py:1437-1469`），预防最坏情况爆炸。
- 几何用 **Uint32 索引**（`index.html:533`），GPU 上不重复顶点。
- 材质按颜色缓存（`getMat`，345 行）。
- 边线**优先用后端真实 BREP 拓扑边**，仅缺失时回退 `EdgesGeometry`（575-583）。
- `MAX_PTS=200000` 边线安全封顶（514 行）。
- 大模式概念本身合理，只是阈值写错（见 Bug#2）。

---

## 八、建议修复优先级

| 优先级 | 项 | 改动量 | 收益 |
|---|---|---|---|
| P0 | 几何传输改 glTF+Draco 或 二进制+gzip（对应"glTF 压缩"） | 中–大（需前后端协同） | 体积 10~50×↓，加载质变 |
| P1 | 修 `BIG_MODEL_TRIS` 阈值（Bug#2） | 极小（1 行） | 大件跳过最重 3 条 JS 路径 |
| P1 | 去 composer 或用 MSAA render target（Bug#3） | 小 | 恢复 AA + 省全屏 pass |
| P2 | 懒构建着色/线框克隆（Bug#4） | 小 | 大件 GPU 显存 3×→1× |
| P2 | weld/法线搬 Worker（Bug#5） | 中 | 消除加载卡顿 |
| P3 | 缓存序列化响应 + flask-compress | 小 | 重复请求免重算 |

> 注：几何有效性与干涉/间隙的逻辑在后端 `fit_checker`/`assembly_builder`（已在独立代码审查中修复并验证），查看器只是**呈现**这些结果（如接触面高亮、干涉体积数字）。查看器本身不重新计算几何有效性，因此"几何有效性"在查看器层面无独立问题，问题在于**大几何的传输与渲染效率**。
