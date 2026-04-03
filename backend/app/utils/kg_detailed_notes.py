import logging
from dataclasses import dataclass
from typing import List, Optional
from app.models.transcriber_model import TranscriptSegment, TranscriptResult
from app.utils.segment_aligner import KGNodeWithTimestamp
from app.gpt.base import GPT
from app.models.gpt_model import GPTSource

logger = logging.getLogger(__name__)


@dataclass
class DetailedNotesConfig:
    """详细笔记生成配置"""
    chapter_min_words: int = 300   # 章节级总结最小字数
    leaf_min_words: int = 200      # 知识点级展开最小字数
    max_segments_per_call: int = 50  # 单次 LLM 调用最大 segment 数


@dataclass
class GenerationResult:
    """生成结果"""
    markdown: str
    node_count: int
    llm_call_count: int


class DetailedNotesGenerator:
    """
    详细笔记生成器

    遍历知识图谱节点，对每个节点调用 LLM 生成详细笔记：
    - 一级节点：章节级总结（提取对应时间段转写原文 → AI详细总结）
    - 叶子节点：知识点级详细展开（提取相关时间段 → AI详细描述）
    """

    def __init__(
        self,
        gpt: GPT,
        transcript: TranscriptResult,
        config: Optional[DetailedNotesConfig] = None,
        video_understanding: bool = False,
        style: Optional[str] = None,
        formats: Optional[List[str]] = None,
    ):
        self.gpt = gpt
        self.transcript = transcript
        self.config = config or DetailedNotesConfig()
        self._video_understanding = video_understanding
        self._style = style
        self._formats = formats or []
        self._llm_call_count = 0
        self._should_insert_screenshots()

    def _should_insert_screenshots(self) -> None:
        """检测是否应插入截图标记"""
        self.should_insert_screenshots = (
            self._video_understanding
            and self._style == "knowledge_graph"
            and "screenshot" in self._formats
        )

    def generate(self, aligned_nodes: List[KGNodeWithTimestamp]) -> GenerationResult:
        """
        遍历知识图谱节点生成详细笔记

        Args:
            aligned_nodes: 带时间戳的知识图谱节点列表

        Returns:
            GenerationResult: 包含 markdown 文本、节点数、LLM 调用次数
        """
        if not aligned_nodes:
            return GenerationResult(markdown="", node_count=0, llm_call_count=0)

        sections = []
        total_nodes = 0

        # 1. 生成笔记目录（无 LLM 调用）
        toc = self._generate_toc(aligned_nodes)
        if toc:
            sections.append(toc)

        # 2. 遍历节点生成章节/知识点内容
        for node in aligned_nodes:
            total_nodes += self._count_node_and_descendants(node)
            section = self._process_node(node)
            if section:
                sections.append(section)

        # 3. 生成视频整体总结（1次 LLM 调用）
        summary = self._generate_video_summary(aligned_nodes)
        if summary:
            sections.append(summary)

        markdown = "\n\n".join(sections)

        return GenerationResult(
            markdown=markdown,
            node_count=total_nodes,
            llm_call_count=self._llm_call_count
        )

    def _process_node(self, node: KGNodeWithTimestamp) -> Optional[str]:
        """处理单个节点"""
        if node.depth == 1:
            # 一级节点：章节级总结，并递归处理子节点
            sections = [self._generate_chapter_level(node)]
            for child in node.children:
                child_section = self._process_node(child)
                if child_section:
                    sections.append(child_section)
            return "\n\n".join(sections)
        elif not node.children:
            # 叶子节点：知识点级详细展开
            return self._generate_leaf_level(node)
        else:
            # 非叶子非一级的中间节点：只处理子节点
            sections = []
            for child in node.children:
                child_section = self._process_node(child)
                if child_section:
                    sections.append(child_section)
            return "\n\n".join(sections) if sections else None

    def _generate_chapter_level(self, node: KGNodeWithTimestamp) -> str:
        """生成章节级总结"""
        segments_text = self._get_segments_text(node.start_time, node.end_time)

        prompt = f"""你是一位专业的学习笔记生成专家。请根据以下转写内容，生成该章节的详细笔记。

【章节】{node.node_name}
【类型】{node.type_tag}
【时间范围】{self._format_time(node.start_time)} - {self._format_time(node.end_time)}

【转写原文】
{segments_text}

要求：
1. 详细展开该章节的所有关键内容
2. 保留具体的案例、数据、结论
3. 字数不少于 {self.config.chapter_min_words} 字
4. 使用 Markdown 格式，保持良好的结构和可读性
5. 在章节标题下方或开头标注时间戳，格式为「[mm:ss - mm:ss]」，例如「[00:30 - 05:20]」
"""

        if self.should_insert_screenshots:
            mid_time = self._format_time((node.start_time + node.end_time) / 2)
            prompt += f"""
6. 【原片截图】请在章节内容结束后，插入一个原片截图标记，格式为 *Screenshot-[{mid_time}]。
   这些标记会被自动替换为对应时间点的视频关键帧截图。
"""

        # 构造 GPTSource（复用现有架构）
        source = GPTSource(
            title=node.node_name,
            segment=self._get_segments(node.start_time, node.end_time),
            tags=[],
            screenshot=False,
            video_img_urls=[],
            link=False,
            _format=[],
            style=None,
            extras=prompt,
            checkpoint_key=None,
        )

        try:
            result = self.gpt.summarize(source)
            self._llm_call_count += 1
            logger.info(f"章节级总结生成成功: {node.node_name}")
            return result
        except Exception as e:
            logger.error(f"章节级总结生成失败: {node.node_name}, error: {e}")
            return f"## {node.node_name}\n\n[生成失败: {str(e)}]"

    def _generate_leaf_level(self, node: KGNodeWithTimestamp) -> str:
        """生成叶子节点详细展开"""
        segments_text = self._get_segments_text(node.start_time, node.end_time)

        prompt = f"""你是一位专业的学习笔记生成专家。请根据以下原文内容，生成该知识点的详细描述。

【知识点】{node.node_name}
【类型标签】{node.type_tag}
【相关转写片段】
{segments_text}

要求：
1. 详细解释该知识点的核心概念
2. 提供具体的例子和应用场景
3. 包含原文中的关键细节和结论
4. 字数不少于 {self.config.leaf_min_words} 字
5. 使用 Markdown 格式
6. 在知识点标题下方或开头标注时间戳，格式为「[mm:ss - mm:ss]」，例如「[02:30 - 04:15]」
"""

        if self.should_insert_screenshots:
            mid_time = self._format_time((node.start_time + node.end_time) / 2)
            prompt += f"""
7. 【原片截图】请在知识点内容结束后，插入一个原片截图标记，格式为 *Screenshot-[{mid_time}]。
   这些标记会被自动替换为对应时间点的视频关键帧截图。
"""

        source = GPTSource(
            title=node.node_name,
            segment=self._get_segments(node.start_time, node.end_time),
            tags=[],
            screenshot=False,
            video_img_urls=[],
            link=False,
            _format=[],
            style=None,
            extras=prompt,
            checkpoint_key=None,
        )

        try:
            result = self.gpt.summarize(source)
            self._llm_call_count += 1
            logger.info(f"知识点级展开生成成功: {node.node_name}")
            return result
        except Exception as e:
            logger.error(f"知识点级展开生成失败: {node.node_name}, error: {e}")
            return f"### {node.node_name} [{node.type_tag}]\n\n[生成失败: {str(e)}]"

    def _get_segments(self, start_time: float, end_time: float) -> List[TranscriptSegment]:
        """获取指定时间段内的所有 segments（使用区间交集判断）"""
        return [
            seg for seg in self.transcript.segments
            if seg.end >= start_time and seg.start <= end_time
        ]

    def _get_segments_text(self, start_time: float, end_time: float) -> str:
        """获取指定时间段内的转写文本"""
        segments = self._get_segments(start_time, end_time)
        return "\n".join([seg.text for seg in segments])

    def _format_time(self, seconds: float) -> str:
        """秒数格式化为 hh:mm:ss 或 mm:ss（超过1小时用 hh:mm:ss）"""
        total_minutes = int(seconds // 60)
        secs = int(seconds % 60)
        if total_minutes >= 60:
            hours = total_minutes // 60
            minutes = total_minutes % 60
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{total_minutes:02d}:{secs:02d}"

    def _count_node_and_descendants(self, node: KGNodeWithTimestamp) -> int:
        """递归统计节点及其所有子节点的总数"""
        count = 1  # 计数当前节点
        for child in node.children:
            count += self._count_node_and_descendants(child)
        return count

    def _generate_toc(self, aligned_nodes: List[KGNodeWithTimestamp]) -> str:
        """
        生成笔记目录（无需 LLM 调用，纯遍历拼接）

        Args:
            aligned_nodes: 带时间戳的知识图谱节点列表

        Returns:
            str: 目录 markdown 字符串
        """
        if not aligned_nodes:
            return ""

        lines = ["## 笔记目录", ""]
        for i, node in enumerate(aligned_nodes, 1):
            start = self._format_time(node.start_time)
            end = self._format_time(node.end_time)
            lines.append(f"{i}. {node.node_name} [{start} - {end}]")

        return "\n".join(lines)

    def _generate_video_summary(
        self,
        aligned_nodes: List[KGNodeWithTimestamp],
        video_title: str = "视频内容"
    ) -> str:
        """
        生成视频整体总结（额外 1 次 LLM 调用）

        Args:
            aligned_nodes: 带时间戳的知识图谱节点列表
            video_title: 视频标题

        Returns:
            str: 总结 markdown 字符串
        """
        # 拼接各章节概要（无需 LLM，直接遍历）
        chapter_summaries = []
        for i, node in enumerate(aligned_nodes, 1):
            start = self._format_time(node.start_time)
            end = self._format_time(node.end_time)
            chapter_summaries.append(f"{i}. {node.node_name} [{start} - {end}]")
        chapter_summaries_text = "\n".join(chapter_summaries)

        # 计算视频总时长
        total_duration = 0.0
        if aligned_nodes and self.transcript.segments:
            total_duration = self.transcript.segments[-1].end

        prompt = f"""你是一位专业的学习笔记生成专家。请根据以下信息，生成视频的整体总结。

【视频主题】{video_title}
【视频时长】{self._format_time(total_duration)}
【章节数量】{len(aligned_nodes)}
【各章节概要】
{chapter_summaries_text}

要求：
1. 字数 300-500 字
2. 包含【内容概览】【核心观点】【学习建议】三个板块
3. 内容概览：简要说明视频的主题、时长、内容结构
4. 核心观点：基于各章节概要，列出核心观点（3-5条），每个观点一句话概括
5. 学习建议：基于视频内容给出学习路径建议
6. 使用 Markdown 格式，结构清晰
"""

        source = GPTSource(
            title=f"{video_title} - 视频总结",
            segment=self.transcript.segments,
            tags=[],
            screenshot=False,
            video_img_urls=[],
            link=False,
            _format=[],
            style=None,
            extras=prompt,
            checkpoint_key=None,
        )

        try:
            result = self.gpt.summarize(source)
            self._llm_call_count += 1
            logger.info(f"视频整体总结生成成功")
            return f"## 视频总结\n\n{result}"
        except Exception as e:
            logger.error(f"视频整体总结生成失败: {e}")
            return f"## 视频总结\n\n[生成失败: {str(e)}]"
