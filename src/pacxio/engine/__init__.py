"""几何引擎（）：配方 → 刀版 + 自动折法 + 3D。核心模块不联网、无全局状态。"""

# 盒型注册表里有引擎的入口（styles/__init__ 导入 engine.style）：先把注册表载完，直接导入引擎子模块时不会循环
import pacxio.styles  # noqa: E402, F401
