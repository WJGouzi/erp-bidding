"""单元测试：章节索引构建。"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.infrastructure.document_parser import StructuredDocument, Section, ContentBlock


class TestBuildSectionIndex(unittest.TestCase):
    """测试结构化文档的章节索引构建。"""

    def test_empty_document(self):
        doc = StructuredDocument()
        index = doc.build_section_index()
        self.assertEqual(index, [])

    def test_single_section(self):
        doc = StructuredDocument()
        doc.sections.append(Section(title="第一章 招标公告", level=1))
        index = doc.build_section_index()
        self.assertEqual(len(index), 1)
        self.assertEqual(index[0]["title"], "第一章 招标公告")
        self.assertEqual(index[0]["level"], 1)
        self.assertEqual(index[0]["parent_id"], None)

    def test_flat_sections(self):
        doc = StructuredDocument()
        doc.sections.append(Section(title="第一章", level=1, page_range=[1, 3]))
        doc.sections.append(Section(title="第二章", level=1, page_range=[4, 10]))
        doc.sections.append(Section(title="第三章", level=1, page_range=[11, 20]))
        index = doc.build_section_index()
        self.assertEqual(len(index), 3)
        self.assertEqual(index[0]["id"], "sec_1")
        self.assertEqual(index[1]["id"], "sec_2")
        self.assertEqual(index[2]["id"], "sec_3")

    def test_nested_sections(self):
        doc = StructuredDocument()
        parent = Section(title="第二章 投标人须知", level=1, page_range=[4, 15])
        child1 = Section(title="一、项目概况", level=2, page_range=[4, 6])
        child2 = Section(title="二、投标人资格要求", level=2, page_range=[7, 10])
        parent.children = [child1, child2]
        doc.sections.append(parent)
        index = doc.build_section_index()
        self.assertEqual(len(index), 3)  # 父 + 2 子
        # 父节点
        self.assertEqual(index[0]["title"], "第二章 投标人须知")
        self.assertEqual(index[0]["parent_id"], None)
        # 子节点
        self.assertEqual(index[1]["title"], "一、项目概况")
        self.assertEqual(index[1]["parent_id"], "sec_1")
        self.assertEqual(index[2]["title"], "二、投标人资格要求")
        self.assertEqual(index[2]["parent_id"], "sec_1")
        # 父节点的 children_ids
        self.assertEqual(len(index[0]["children_ids"]), 2)

    def test_multi_level_nesting(self):
        doc = StructuredDocument()
        l1 = Section(title="第一章", level=1)
        l2 = Section(title="一、技术方案", level=2)
        l3 = Section(title="（一）实施计划", level=3)
        l2.children = [l3]
        l1.children = [l2]
        doc.sections.append(l1)
        index = doc.build_section_index()
        self.assertEqual(len(index), 3)
        self.assertEqual(index[0]["title"], "第一章")
        self.assertEqual(index[1]["title"], "一、技术方案")
        self.assertEqual(index[1]["parent_id"], "sec_1")
        self.assertEqual(index[2]["title"], "（一）实施计划")
        self.assertEqual(index[2]["parent_id"], "sec_2")

    def test_page_range_preserved(self):
        doc = StructuredDocument()
        sec = Section(title="项目概况", level=2, page_range=[5, 7])
        doc.sections.append(sec)
        index = doc.build_section_index()
        self.assertEqual(index[0]["page_range"], [5, 7])

    def test_no_title_section(self):
        doc = StructuredDocument()
        doc.sections.append(Section(title="", level=1))
        index = doc.build_section_index()
        self.assertEqual(len(index), 1)
        self.assertEqual(index[0]["title"], "")

    def test_unique_ids(self):
        """所有 sec_id 应唯一。"""
        doc = StructuredDocument()
        for i in range(10):
            doc.sections.append(Section(title=f"第{i+1}章", level=1))
        index = doc.build_section_index()
        ids = [e["id"] for e in index]
        self.assertEqual(len(ids), len(set(ids)))

    def test_circular_ref_not_possible(self):
        """Section 不应有循环引用。"""
        doc = StructuredDocument()
        parent = Section(title="父", level=1)
        child = Section(title="子", level=2)
        parent.children = [child]
        doc.sections.append(parent)
        index = doc.build_section_index()
        # 子节点的 parent_id 指向父
        self.assertEqual(index[1]["parent_id"], "sec_1")
        # 父节点的 children_ids 包含子
        self.assertIn("sec_2", index[0].get("children_ids", []))


if __name__ == "__main__":
    unittest.main()
