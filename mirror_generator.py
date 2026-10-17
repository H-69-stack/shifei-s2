"""
镜像模型生成器
基于 OpenCASCADE (CadQuery / OCP bindings)
支持关于 xOy / xOz / yOz 面（经过模型质心）生成镜像模型
"""

import os
import math
import cadquery as cq
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.gp import gp_Pnt, gp_Dir, gp_Ax1, gp_Ax2, gp_Trsf
from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID, TopAbs_SHELL


class MirrorGenerator:
    """
    镜像与旋转模型生成器

    【镜像】关于指定坐标面生成镜像模型
        xOy 面: 法向 (0,0,1)，经过质心时为 Z=cz 平面，镜像后 Z → 2*cz - Z
        xOz 面: 法向 (0,1,0)，经过质心时为 Y=cy 平面，镜像后 Y → 2*cy - Y
        yOz 面: 法向 (1,0,0)，经过质心时为 X=cx 平面，镜像后 X → 2*cx - X

    【旋转】绕指定坐标轴 (X/Y/Z) 顺时针/逆时针旋转指定角度
        旋转轴经过模型质心（默认），角度单位为度，遵循右手定则：
            逆时针(CCW) = 绕 +轴向正转角；顺时针(CW) = 绕 +轴向负转角。
    """

    PLANES = {
        'xOy': {'label': 'XOY 面 (Z 方向)', 'normal': (0, 0, 1)},
        'xOz': {'label': 'XOZ 面 (Y 方向)', 'normal': (0, 1, 0)},
        'yOz': {'label': 'YOZ 面 (X 方向)', 'normal': (1, 0, 0)},
    }

    # 旋转轴配置：axis -> {label, direction}
    AXES = {
        'x': {'label': 'X 轴', 'dir': (1, 0, 0)},
        'y': {'label': 'Y 轴', 'dir': (0, 1, 0)},
        'z': {'label': 'Z 轴', 'dir': (0, 0, 1)},
    }

    def __init__(self):
        self._shape = None
        self._centroid = None

    def load(self, filepath: str):
        """加载 STEP 文件，提取第一个 Solid/Shell"""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"文件不存在: {filepath}")
        result = cq.importers.importStep(filepath)
        shapes = result.vals() if hasattr(result, 'vals') else []
        if not shapes:
            raise ValueError("STEP 文件未读取到几何体")
        shape = None
        for shp in shapes:
            s = shp.wrapped
            for face_type in (TopAbs_SOLID, TopAbs_SHELL):
                exp = TopExp_Explorer(s, face_type)
                while exp.More():
                    shape = exp.Current()
                    break
                if shape:
                    break
            if shape:
                break
        if shape is None:
            raise ValueError("未找到 Solid 或 Shell 实体")
        self._shape = shape
        self._centroid = self._compute_centroid(shape)
        return shape

    def _compute_centroid(self, shape):
        """计算实体质心（体积属性），失败时退化为包围盒中心"""
        try:
            p = GProp_GProps()
            BRepGProp.VolumeProperties_s(shape, p)
            c = p.CentreOfMass()
            return (c.X(), c.Y(), c.Z())
        except Exception:
            b = Bnd_Box()
            BRepBndLib.Add_s(shape, b)
            x1, y1, z1, x2, y2, z2 = b.Get()
            return ((x1 + x2) / 2, (y1 + y2) / 2, (z1 + z2) / 2)

    def get_centroid(self):
        return self._centroid

    def mirror(self, plane: str = 'xOy', center_on_centroid: bool = True):
        """
        应用镜像变换

        Args:
            plane: 'xOy' | 'xOz' | 'yOz'
            center_on_centroid:
                True  → 镜像面经过模型质心（平行于指定坐标面）
                False → 镜像面为坐标面本身 (Z=0 / Y=0 / X=0)

        Returns:
            镜像后的 TopoDS_Shape
        """
        if plane not in self.PLANES:
            raise ValueError(f"不支持的镜像面: {plane}，可选: {list(self.PLANES.keys())}")

        cfg = self.PLANES[plane]
        nx, ny, nz = cfg['normal']

        if center_on_centroid and self._centroid:
            ox, oy, oz = self._centroid
        else:
            ox = oy = oz = 0.0

        trsf = gp_Trsf()
        trsf.SetMirror(gp_Ax2(gp_Pnt(ox, oy, oz), gp_Dir(nx, ny, nz)))

        # B10：Copy=True —— 默认 Copy=False 会原地修改 self._shape 的底层
        # 几何，同一实例连续 mirror/rotate 时第二次作用于已变换的 shape。
        transformer = BRepBuilderAPI_Transform(self._shape, trsf, True)
        transformer.Build()
        return transformer.Shape()

    def generate(self, filepath: str, plane: str = 'xOy',
                 output_dir: str = None, center_on_centroid: bool = True,
                 product_name: str = None) -> dict:
        """
        加载 STEP 文件并生成镜像模型，导出为新的 STEP 文件

        Args:
            filepath: 输入 STEP 文件路径
            plane: 镜像面 'xOy' / 'xOz' / 'yOz'
            output_dir: 输出目录（默认与源文件同目录）
            center_on_centroid: 镜像面是否经过模型质心
            product_name: 输出文件名使用的基础名（不含扩展名）；
                默认取 filepath 的基本名

        Returns:
            {
                'output_path': str,       # 镜像 STEP 文件路径
                'plane': str,             # 'xOy' / 'xOz' / 'yOz'
                'plane_label': str,       # 中文平面标签
                'centroid': [cx, cy, cz], # 原始模型质心
                'bbox': {...},            # 镜像后包围盒
            }
        """
        self.load(filepath)

        if product_name:
            name = product_name
        else:
            name = os.path.splitext(os.path.basename(filepath))[0]
        if not output_dir:
            output_dir = os.path.dirname(os.path.abspath(filepath))
        os.makedirs(output_dir, exist_ok=True)

        cfg = self.PLANES[plane]
        mirrored = self.mirror(plane, center_on_centroid)

        out_path = os.path.join(output_dir, f"{name}_MIRROR_{plane}.stp")
        w = STEPControl_Writer()
        w.Transfer(mirrored, STEPControl_AsIs)
        if w.Write(out_path) != IFSelect_RetDone:
            raise RuntimeError(f"导出失败: {out_path}")

        # 计算镜像后包围盒
        b = Bnd_Box()
        BRepBndLib.Add_s(mirrored, b)
        x1, y1, z1, x2, y2, z2 = b.Get()

        return {
            'output_path': out_path,
            'plane': plane,
            'plane_label': cfg['label'],
            'centroid': [round(v, 3) for v in self._centroid],
            'bbox': {
                'min': [x1, y1, z1],
                'max': [x2, y2, z2],
                'size': [x2 - x1, y2 - y1, z2 - z1],
            },
        }

    def rotate(self, axis: str = 'z', angle: float = 0.0,
               clockwise: bool = True, center_on_centroid: bool = True):
        """
        绕指定坐标轴旋转模型（右手定则）

        Args:
            axis: 'x' | 'y' | 'z'
            angle: 旋转角度（度），取绝对值
            clockwise:
                True  → 顺时针（绕 +轴向取负转角）
                False → 逆时针（绕 +轴向取正转角）
            center_on_centroid:
                True  → 旋转轴经过模型质心（默认）
                False → 旋转轴为坐标轴本身 (过原点)

        Returns:
            旋转后的 TopoDS_Shape
        """
        if axis not in self.AXES:
            raise ValueError(f"不支持的旋转轴: {axis}，可选: {list(self.AXES.keys())}")

        angle = float(angle)
        dx, dy, dz = self.AXES[axis]['dir']

        if center_on_centroid and self._centroid:
            ox, oy, oz = self._centroid
        else:
            ox = oy = oz = 0.0

        # 右手定则：正角 = 绕 +轴向逆时针；顺时针取负角
        radians = math.radians(abs(angle))
        if clockwise:
            radians = -radians

        trsf = gp_Trsf()
        trsf.SetRotation(
            gp_Ax1(gp_Pnt(ox, oy, oz), gp_Dir(dx, dy, dz)), radians
        )

        # B10：Copy=True —— 默认 Copy=False 会原地修改 self._shape 的底层
        # 几何，同一实例连续 mirror/rotate 时第二次作用于已变换的 shape。
        transformer = BRepBuilderAPI_Transform(self._shape, trsf, True)
        transformer.Build()
        return transformer.Shape()

    def generate_rotation(self, filepath: str, axis: str = 'z',
                          angle: float = 0.0, clockwise: bool = True,
                          output_dir: str = None, center_on_centroid: bool = True,
                          product_name: str = None) -> dict:
        """
        加载 STEP 文件并绕坐标轴旋转，导出为新的 STEP 文件

        Args:
            filepath: 输入 STEP 文件路径
            axis: 旋转轴 'x' / 'y' / 'z'
            angle: 旋转角度（度）
            clockwise: True=顺时针，False=逆时针
            output_dir: 输出目录（默认与源文件同目录）
            center_on_centroid: 旋转轴是否经过模型质心
            product_name: 输出文件名使用的基础名（不含扩展名）；
                默认取 filepath 的基本名

        Returns:
            {
                'output_path': str,       # 旋转 STEP 文件路径
                'axis': str,              # 'x' / 'y' / 'z'
                'axis_label': str,        # 中文轴标签
                'angle': float,           # 旋转角度
                'direction': str,         # 'CW' / 'CCW'
                'centroid': [cx, cy, cz], # 原始模型质心
                'bbox': {...},            # 旋转后包围盒
            }
        """
        if axis not in self.AXES:
            raise ValueError(f"不支持的旋转轴: {axis}，可选: {list(self.AXES.keys())}")

        self.load(filepath)

        if product_name:
            name = product_name
        else:
            name = os.path.splitext(os.path.basename(filepath))[0]
        if not output_dir:
            output_dir = os.path.dirname(os.path.abspath(filepath))
        os.makedirs(output_dir, exist_ok=True)

        cfg = self.AXES[axis]
        rotated = self.rotate(axis, angle, clockwise, center_on_centroid)

        direction = 'CW' if clockwise else 'CCW'
        deg = int(round(abs(float(angle))))
        out_path = os.path.join(
            output_dir, f"{name}_ROTATE_{axis}_{direction}_{deg}deg.stp"
        )

        w = STEPControl_Writer()
        w.Transfer(rotated, STEPControl_AsIs)
        if w.Write(out_path) != IFSelect_RetDone:
            raise RuntimeError(f"导出失败: {out_path}")

        # 计算旋转后包围盒
        b = Bnd_Box()
        BRepBndLib.Add_s(rotated, b)
        x1, y1, z1, x2, y2, z2 = b.Get()

        return {
            'output_path': out_path,
            'axis': axis,
            'axis_label': cfg['label'],
            'angle': float(angle),
            'direction': direction,
            'centroid': [round(v, 3) for v in self._centroid],
            'bbox': {
                'min': [x1, y1, z1],
                'max': [x2, y2, z2],
                'size': [x2 - x1, y2 - y1, z2 - z1],
            },
        }


if __name__ == '__main__':
    import sys
    test_file = "YA-1131-505.stp"
    if not os.path.exists(test_file):
        test_file = os.path.join(os.path.dirname(__file__), "YA-1131-505.stp")
    if not os.path.exists(test_file):
        print("未找到测试文件 YA-1131-505.stp")
        sys.exit(1)

    out_dir = os.path.join(os.path.dirname(__file__), "mold_output")
    os.makedirs(out_dir, exist_ok=True)

    gen = MirrorGenerator()
    for plane in ('xOy', 'xOz', 'yOz'):
        try:
            r = gen.generate(test_file, plane=plane, output_dir=out_dir)
            print(f"\n[结果] {plane} 镜像: {r['output_path']}")
            print(f"  质心: {r['centroid']}")
            print(f"  镜像后包围盒: {r['bbox']['size']}")
        except Exception as e:
            print(f"[失败] {plane}: {e}")

    # 旋转功能自检
    for axis in ('x', 'y', 'z'):
        for clockwise in (True, False):
            try:
                r = gen.generate_rotation(
                    test_file, axis=axis, angle=30.0,
                    clockwise=clockwise, output_dir=out_dir,
                )
                direction = '顺时针' if clockwise else '逆时针'
                print(f"\n[结果] 绕 {axis} 轴 {direction} 30°: {r['output_path']}")
                print(f"  质心: {r['centroid']}")
                print(f"  旋转后包围盒: {r['bbox']['size']}")
            except Exception as e:
                print(f"[失败] 绕 {axis} 轴: {e}")