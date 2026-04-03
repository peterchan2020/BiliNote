from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MinerUParseResult:
    """
    MinerU PDF 解析结果

    Attributes:
        md_content: 带层级结构的 Markdown 内容（包含 # ## ### 等标题层级）
        images: 图片字典，key 为图片路径，value 为 base64 数据 URL
        content_list: 详细内容列表，包含每个块的类型、位置、页码等信息
        page_count: 页数
        parsing_time_ms: 解析耗时（毫秒）
    """
    md_content: str
    images: dict[str, str]
    content_list: list[dict]
    page_count: int
    parsing_time_ms: Optional[int] = None


@dataclass
class MinerUParseConfig:
    """
    MinerU 解析配置

    Attributes:
        base_url: MinerU 服务地址（如 http://localhost:8080）
        api_key: 可选的 API Key（部分部署需要）
        backend: 解析引擎
            - "hybrid-auto-engine": VLM 驱动，最高质量，需要 GPU
            - "pipeline": 基础模式，无 VLM，更快
        parse_method: 解析方法，"auto" 自动检测
        return_md: 是否返回 Markdown
        return_images: 是否返回图片
        return_content_list: 是否返回内容列表
        formula_enable: 是否提取公式
        table_enable: 是否提取表格
    """
    base_url: str
    api_key: Optional[str] = None
    backend: str = "hybrid-auto-engine"
    parse_method: str = "auto"
    return_md: bool = True
    return_images: bool = True
    return_content_list: bool = True
    formula_enable: bool = True
    table_enable: bool = True


@dataclass
class MinerUContentListItem:
    """
    MinerU 内容列表项

    Attributes:
        type: 项目类型 ("text", "image", "table", "title")
        page_idx: 页码索引
        bbox: 边界框 [x0, y0, x1, y1]
        content: 文本内容（如果有）
        level: 标题级别（如果 type="title"）
    """

    type: str
    page_idx: int
    bbox: Optional[list[float]] = None
    content: Optional[str] = None
    level: Optional[int] = None

    @classmethod
    def from_dict(cls, d: dict) -> "MinerUContentListItem":
        """Create instance from dictionary."""
        return cls(
            type=d.get("type", "unknown"),
            page_idx=d.get("page_idx", 0),
            bbox=d.get("bbox"),
            content=d.get("content"),
            level=d.get("level"),
        )


@dataclass
class MinerUMarkdownQualityReport:
    """
    Markdown 质量检查报告

    Attributes:
        total_chars: 总字符数
        heading_count: 标题总数
        h1_count: 一级标题数量
        h2_count: 二级标题数量
        h3_count: 三级标题数量
        h4_plus_count: 四级及以下标题数量
        max_heading_depth: 最大标题深度
        has_valid_structure: 是否有有效的层级结构
        estimated_chapter_count: 预估章节数（基于 H1/H2）
        warnings: 警告列表
        heading_hierarchy_valid: 标题层级是否合法（H1→H2→H3→H4）
        heading_hierarchy_violations: 标题层级违规列表
        content_list_aligned: content_list 是否与 md_content 对齐
        content_list_warnings: content_list 对齐警告列表
    """
    total_chars: int
    heading_count: int
    h1_count: int
    h2_count: int
    h3_count: int
    h4_plus_count: int
    max_heading_depth: int
    has_valid_structure: bool
    estimated_chapter_count: int
    warnings: list[str] = field(default_factory=list)
    # Extended fields for Phase 1
    heading_hierarchy_valid: bool = True
    heading_hierarchy_violations: list[str] = field(default_factory=list)
    content_list_aligned: bool = True
    content_list_warnings: list[str] = field(default_factory=list)
