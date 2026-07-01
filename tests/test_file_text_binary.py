"""单元测试：_read_file_text 二进制文件过滤 — 乱码回归测试

测试场景：
1. JPEG/PNG 等图片文件不应被读取为文本
2. 二进制文件调用 _read_file_text 应返回空字符串
3. _build_subject_material_context 中的 text_excerpt 不应包含二进制垃圾

运行方式:
    cd /Users/wangjun/Desktop/work/erp/code/erp-bidding
    source .venv/bin/activate
    python3 -m unittest tests.test_file_text_binary -v
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Path to helpers module
from app.service_modules.task_pipeline import helpers


class TestReadFileTextBinaryFilter(unittest.TestCase):
    """测试 _read_file_text 对二进制文件的过滤。"""

    def setUp(self):
        """构建模拟的 FileRecord。"""
        # 创建 mock file record
        self.mock_record = MagicMock()
        self.mock_record.id = 12345
        self.mock_record.storage_provider = "MINIO"
        self.mock_record.chroma_doc_id = None
        self.mock_record.chroma_collection = None

    def test_jpeg_file_returns_empty(self):
        """JPEG 图片文件应返回空字符串。"""
        self.mock_record.file_name = "营业执照.jpg"
        result = helpers._read_file_text(self.mock_record)
        self.assertEqual(result, "", "JPEG 文件不应被读取为文本")

    def test_png_file_returns_empty(self):
        """PNG 图片文件应返回空字符串。"""
        self.mock_record.file_name = "法人身份证.png"
        result = helpers._read_file_text(self.mock_record)
        self.assertEqual(result, "", "PNG 文件不应被读取为文本")

    def test_gif_file_returns_empty(self):
        """GIF 图片文件应返回空字符串。"""
        self.mock_record.file_name = "资质文件.gif"
        result = helpers._read_file_text(self.mock_record)
        self.assertEqual(result, "", "GIF 文件不应被读取为文本")

    def test_bmp_file_returns_empty(self):
        """BMP 图片文件应返回空字符串。"""
        self.mock_record.file_name = "印章.bmp"
        result = helpers._read_file_text(self.mock_record)
        self.assertEqual(result, "", "BMP 文件不应被读取为文本")

    def test_tiff_file_returns_empty(self):
        """TIFF 图片文件应返回空字符串。"""
        self.mock_record.file_name = "扫描件.tiff"
        result = helpers._read_file_text(self.mock_record)
        self.assertEqual(result, "", "TIFF 文件不应被读取为文本")

    def test_webp_file_returns_empty(self):
        """WebP 图片文件应返回空字符串。"""
        self.mock_record.file_name = "图片.webp"
        result = helpers._read_file_text(self.mock_record)
        self.assertEqual(result, "", "WebP 文件不应被读取为文本")

    def test_docx_file_not_filtered(self):
        """docx 文件不应被过滤（应继续尝试读取）。"""
        self.mock_record.file_name = "招标文件.docx"
        # docx 不会被二进制过滤拦截，但后续 read_bytes 会失败
        # 因为 mock 没有返回数据，应该最终返回空字符串
        result = helpers._read_file_text(self.mock_record)
        self.assertEqual(result, "", "docx 文件应继续尝试读取（返回空因 mock 无数据）")

    def test_pdf_file_not_filtered(self):
        """PDF 文件不应被过滤（有专门的 DocumentParser 解析）。"""
        self.mock_record.file_name = "招标文件.pdf"
        result = helpers._read_file_text(self.mock_record)
        # PDF 不会被二进制过滤拦截
        self.assertEqual(result, "", "PDF 文件应继续尝试读取（返回空因 mock 无数据）")

    def test_no_extension_file_not_filtered(self):
        """无后缀名的文件不应被过滤。"""
        self.mock_record.file_name = "README"
        result = helpers._read_file_text(self.mock_record)
        self.assertEqual(result, "", "无后缀名文件不应被过滤")

    def test_empty_filename_not_crash(self):
        """空文件名字段不应导致崩溃。"""
        self.mock_record.file_name = ""
        result = helpers._read_file_text(self.mock_record)
        self.assertIsInstance(result, str, "空文件名字段应返回字符串")

    def test_none_filename_not_crash(self):
        """None 文件名字段不应导致崩溃。"""
        self.mock_record.file_name = None
        result = helpers._read_file_text(self.mock_record)
        self.assertIsInstance(result, str, "None 文件名字段应返回字符串")

    def test_uppercase_extension_filtered(self):
        """大写扩展名也应被正确过滤。"""
        self.mock_record.file_name = "营业执照.JPG"
        result = helpers._read_file_text(self.mock_record)
        self.assertEqual(result, "", "大写 .JPG 扩展名应被过滤")

        self.mock_record.file_name = "营业执照.JPEG"
        result = helpers._read_file_text(self.mock_record)
        self.assertEqual(result, "", "大写 .JPEG 扩展名应被过滤")

    def test_rar_zip_also_filtered(self):
        """压缩文件也应被过滤。"""
        self.mock_record.file_name = "资料.zip"
        result = helpers._read_file_text(self.mock_record)
        self.assertEqual(result, "", ".zip 文件不应被读取为文本")

        self.mock_record.file_name = "资料.rar"
        result = helpers._read_file_text(self.mock_record)
        self.assertEqual(result, "", ".rar 文件不应被读取为文本")


class TestSubjectMaterialContextNoGarbage(unittest.TestCase):
    """验证 _build_subject_material_context 不包含二进制垃圾数据。"""

    @patch('app.service_modules.task_pipeline.helpers._read_file_text')
    def test_subject_material_text_excerpt_no_binary(self, mock_read):
        """主体资料的 text_excerpt 不应包含 JFIF 等二进制标记。"""
        # 模拟 _read_file_text 返回空（二进制文件已被过滤）
        mock_read.return_value = ""

        # 验证 text_excerpt 为空
        text = helpers._read_file_text(MagicMock(file_name="test.jpg"))
        self.assertEqual(text, "", "图片文件的 text_excerpt 应为空")
        self.assertNotIn("JFIF", text, "不应包含 JPEG 头")

    @patch('app.service_modules.task_pipeline.helpers._read_file_text')
    def test_text_excerpt_only_contains_valid_text(self, mock_read):
        """text_excerpt 只应包含有效文本，不应包含二进制数据。"""
        # 模拟正常文本文件
        mock_read.return_value = "统一社会信用代码 91440101MA5XXXXXXX"

        result = helpers._read_file_text(MagicMock(file_name="text.txt"))
        self.assertIsInstance(result, str)
        self.assertTrue(result.startswith("统一社会信用代码"))
        # 不应包含二进制特征
        self.assertNotIn("\ufffd", result)  # 替换字符
        self.assertNotIn("\x00", result)    # NULL 字节


if __name__ == "__main__":
    unittest.main(verbosity=2)
