import sys
import unittest
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication
from src.gui.icons import get_icon, get_pixmap, get_svg_content, SVG_TEMPLATES

app = QApplication.instance()
if app is None:
    app = QApplication([])


class TestSvgIcons(unittest.TestCase):

    def test_all_svg_templates_rendering(self):
        """测试所有内置 SVG 模板均可成功渲染为非空 QIcon 与 QPixmap"""
        self.assertGreater(len(SVG_TEMPLATES), 20)

        for name in SVG_TEMPLATES:
            # 1. 验证 SVG 文本生成
            svg_xml = get_svg_content(name, "#2563eb")
            self.assertIn("<svg", svg_xml)
            self.assertIn("#2563eb", svg_xml)

            # 2. 验证 QPixmap 渲染
            pix = get_pixmap(name, "#2563eb", size=24)
            self.assertFalse(pix.isNull(), f"图标 {name} 渲染的 QPixmap 为空")
            self.assertGreater(pix.width(), 0)

            # 3. 验证 QIcon 生成
            ico = get_icon(name, "#ffffff", size=16)
            self.assertFalse(ico.isNull(), f"图标 {name} 渲染的 QIcon 为空")

    def test_caching_behavior(self):
        """测试图标缓存机制能够正常复用实例"""
        ico1 = get_icon("settings", "#2563eb", 16)
        ico2 = get_icon("settings", "#2563eb", 16)
        self.assertIs(ico1, ico2, "相同参数的图标应当命中缓存返回同一对象")

        pix1 = get_pixmap("refresh", "#10b981", 14)
        pix2 = get_pixmap("refresh", "#10b981", 14)
        self.assertIs(pix1, pix2, "相同参数的 Pixmap 应当命中缓存返回同一对象")


if __name__ == "__main__":
    unittest.main()
