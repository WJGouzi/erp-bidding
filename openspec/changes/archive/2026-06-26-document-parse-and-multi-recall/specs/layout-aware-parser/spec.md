# Layout-Aware Parser — Specification

## Overview

替代当前的纯文本解析器，实现版面感知的文档解析。DOCX 保留标题层级和表格结构，PDF 采用混合策略（纯文本页快速提取 + 扫描页 PaddleOCR 视觉识别），输出结构化文档模型。

## Structured Document Model (JSON)

```json
{
  "file_name": "招标文件.pdf",
  "file_sha256": "a1b2c3...",
  "parse_version": "1.0",
  "pages": 45,
  "sections": [
    {
      "title": "第一章 投标须知",
      "level": 1,
      "children": [
        {
          "title": "1.1 项目概况",
          "level": 2,
          "content": [
            {"type": "paragraph", "text": "本项目位于北京市..."},
            {"type": "table", "headers": ["序号", "名称", "数量"], "rows": [["1", "设备A", "10"]]}
          ],
          "page_range": [3, 5]
        }
      ],
      "page_range": [3, 10]
    }
  ]
}
```

## Requirements

### R1: DOCX 版面解析
#### R1.1 标题层级检测
- SHALL 通过 python-docx 读取段落样式（Heading 1/2/3...），识别标题层级
- SHALL 识别手动编号（"一、""1."等），建立标题→子标题的树形结构

#### R1.2 表格完整提取
- SHALL 保留表格行列结构，包括合并单元格信息
- SHALL 记录表格所在的 section 上下文

#### R1.3 列表识别
- SHALL 识别有序列表和无序列表
- SHALL 保留列表层级缩进关系

#### R1.4 纯文本提取速度
- SHALL 优先使用 python-docx（速度快），docx2python 仅作为兜底
- SHALL 对 100 页 DOCX 解析耗时 ≤ 3 秒

### R2: PDF 版面解析
#### R2.1 页面类型检测
- SHALL 使用 fitz 逐页检测：
  - 含 /Image 或 /XObject → 判定为"含图片页"
  - 文本长度 < 50 字符 → 判定为"扫描页"
  - 否则 → "纯文本页"

#### R2.2 纯文本页处理
- SHALL 使用 fitz get_text() 提取，速度 ≤ 5ms/页

#### R2.3 扫描页/图片页处理
- SHALL 通过 PaddleOCRClient 并发识别（max_workers=5）
- SHALL 将图片缩放到最大边长 1024px 后再发送，加速识别
- SHALL 对单页 OCR 超时设定 ≤ 10 秒

#### R2.4 版面重建
- SHALL 将 OCR 返回的文本+坐标聚类，识别标题区/正文区/表格区
- SHALL 按 y 坐标排序重建阅读顺序

#### R2.5 性能目标
- SHALL 对 50 页混合 PDF（80% 文字页 + 20% 扫描页）总解析耗时 ≤ 8 秒

### R3: PaddleOCR 客户端
#### R3.1 API 调用
- SHALL 使用异步任务模式：提交 POST /api/v2/ocr/jobs → 轮询任务状态
- SHALL 支持多页并发调用
- SHALL 内置 LRU 缓存（相同图片哈希命中直接返回）

#### R3.2 错误处理
- SHALL 对网络错误自动重试（最多 2 次）
- SHALL 对单页 OCR 失败降级返回空文本，不阻塞整体流程

### R4: 语义切片
#### R4.1 切片策略
- SHALL 按标题为自然边界切割，不打破标题下的完整段落
- SHALL 表格独立成 chunk，不被分到两个切片中
- SHALL 每个 chunk 携带 metadata: section_path, content_type, page_range

#### R4.2 Chunk 元数据
```json
{
  "file_id": 123,
  "chunk_index": 5,
  "section_path": "第一章 > 1.2 > 1.2.1",
  "content_type": "mixed",
  "page_range": [3, 5],
  "sha256": "abc123..."
}
```

### R5: 缓存策略
- R5.1 解析结果 SHALL 写入 `doc_parse_cache`（key: file_id）
- R5.2 切片结果 SHALL 写入 `doc_chunks`（key: file_id + chunk_index）
- R5.3 缓存命中条件：file_sha256 一致 + parse_version 一致
- R5.4 缓存失效时 SHALL 自动重解析

## Scenarios

### Scenario: 上传招标文件后解析
- **WHEN** 用户上传招标文件，解析器收到 `parse_structured(filename, payload)`
- **THEN** SHALL 检查 `doc_parse_cache` 是否有缓存
- **AND** 缓存命中时直接返回结构化 JSON
- **AND** 缓存未命中时执行版面解析，完成后写入缓存

### Scenario: PDF 混合类型解析
- **GIVEN** 一份 50 页的 PDF，其中 10 页为扫描件
- **WHEN** 执行 `parse_structured`
- **THEN** 40 页纯文本页用 fitz 在 0.2 秒内完成
- **AND** 10 页扫描件用 PaddleOCR 并发 5 页，约 2 秒完成
- **AND** 版面重建 ≤ 0.5 秒
- **AND** 总耗时 ≤ 3 秒
