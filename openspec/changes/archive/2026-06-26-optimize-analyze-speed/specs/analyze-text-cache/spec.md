## ADDED Requirements

### Requirement: 上传阶段同步解析文件文本
上传招标文件时，`create_original_task` SHALL 同步调用 DocumentParser 解析文件内容，并将解析后的纯文本保存到本地临时缓存文件。

#### Scenario: 上传后文本立即可用
- **WHEN** 用户上传招标文件并收到上传成功响应
- **THEN** 文件对应的纯文本内容 SHALL 已经解析完成并写入本地缓存
- **AND** 分析阶段 SHALL 直接从本地缓存读取文本，无需等待 ChromaDB

### Requirement: 分析阶段不依赖 ChromaDB 就绪
`_complete_analysis` SHALL NOT 包含任何 ChromaDB 就绪的轮询等待逻辑。

#### Scenario: ChromaDB 未就绪时分析
- **WHEN** ChromaDB 异步入库尚未完成
- **THEN** 分析阶段 SHALL 从本地文本缓存读取文件内容
- **AND** SHALL 正常完成结构化分析和分包检测
- **AND** ChromaDB 入库 SHALL 在后台继续完成，互不影响

### Requirement: 本地缓存文件管理
本地文本缓存文件 SHALL 在文件上传时创建，在任务删除时清理。

#### Scenario: 缓存文件生命周期
- **WHEN** 招标文件上传
- **THEN** 缓存文件 SHALL 保存在 StorageService 管理的缓存目录中
- **AND** 文件名 SHALL 关联到 file_record.id，便于查找和清理
