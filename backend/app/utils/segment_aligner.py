from dataclasses import dataclass, field
from typing import List, Dict, Any
from app.models.transcriber_model import TranscriptSegment, TranscriptResult


@dataclass
class KGNodeWithTimestamp:
    """带时间戳的知识图谱节点"""
    node_name: str
    type_tag: str
    depth: int
    start_time: float
    end_time: float
    children: List['KGNodeWithTimestamp'] = field(default_factory=list)


class SegmentAligner:
    """
    将知识图谱节点与转写时间段对齐

    策略：
    - 顶级节点均分所有 segments
    - 子节点按直接子节点数递归均分父节点的时间范围
    - 每个节点（不论层级）都获得自己独立的 segment 范围
    """

    def align(self, kg_nodes: List[Dict], transcript: TranscriptResult) -> List[KGNodeWithTimestamp]:
        if not kg_nodes or not transcript.segments:
            return []

        # 后处理：将单链容器节点展平，使内容节点成为真正的顶级节点
        kg_nodes = self._flatten_single_chain(kg_nodes)

        segments = transcript.segments
        total_segments = len(segments)
        total_top = len(kg_nodes)

        if total_top == 0:
            return []

        aligned = []
        segment_idx = 0

        for i, node in enumerate(kg_nodes):
            # 最后一个顶级节点获取所有剩余 segments
            if i == total_top - 1:
                segment_count = total_segments - segment_idx
            else:
                segment_count = max(1, (total_segments - segment_idx) // (total_top - i))

            start_idx = segment_idx
            end_idx = min(segment_idx + segment_count, total_segments)

            if start_idx >= total_segments:
                start_time = 0.0
                end_time = 0.0
            elif end_idx > start_idx:
                start_time = segments[start_idx].start
                end_time = segments[end_idx - 1].end
            else:
                start_time = segments[start_idx].start
                end_time = start_time

            # 递归分配子节点（在自己的 segment 范围内细分）
            children_aligned = []
            if node.get("children"):
                children_aligned = self._align_children(
                    node["children"], segments, start_idx, end_idx
                )

            aligned.append(KGNodeWithTimestamp(
                node_name=node.get("name", ""),
                type_tag=node.get("typeTag", "通用"),
                depth=node.get("depth", 0),
                start_time=start_time,
                end_time=end_time,
                children=children_aligned
            ))

            segment_idx = end_idx

        return aligned

    def _flatten_single_chain(self, kg_nodes: List[Dict]) -> List[Dict]:
        """
        后处理：将单链容器节点展平

        场景：知识图谱有一个根主题节点（depth=0），其唯一子节点是包含所有
        内容章节的"容器"节点（depth=1），而这28个内容章节才是真正的顶级节点。
        此时需要把容器节点的子节点提升为顶级节点。
        """
        if len(kg_nodes) != 1:
            return kg_nodes

        root = kg_nodes[0]
        children = root.get("children", [])
        if not children:
            return kg_nodes

        if len(children) != 1:
            return kg_nodes

        # 容器判断：根节点是"主题"类型且只有一个子节点
        root_tag = root.get("typeTag", "")
        is_container = root_tag in ("主题", "通用") and len(children) == 1

        if not is_container:
            return kg_nodes

        container = children[0]
        real_children = container.get("children", [])
        if not real_children:
            return kg_nodes

        # 把容器节点的子节点提升为顶级节点
        result = []
        for child in real_children:
            child_copy = dict(child)
            child_copy["depth"] = 1
            result.append(child_copy)

        return result if result else kg_nodes

    def _align_children(
        self,
        children: List[Dict],
        segments: List[TranscriptSegment],
        parent_start_idx: int,
        parent_end_idx: int
    ) -> List[KGNodeWithTimestamp]:
        """
        递归分配子节点：按直接子节点数均分父节点的 segment 范围
        """
        if not children:
            return []

        parent_segment_count = parent_end_idx - parent_start_idx
        if parent_segment_count <= 0:
            return []

        num_children = len(children)
        aligned = []
        segment_idx = parent_start_idx

        for i, child in enumerate(children):
            # 最后一个子节点获取所有剩余 segments，保证最小为 1
            if i == num_children - 1:
                child_segment_count = parent_end_idx - segment_idx
            else:
                child_segment_count = max(1, (parent_end_idx - segment_idx) // (num_children - i))

            child_start_idx = segment_idx
            child_end_idx = min(segment_idx + child_segment_count, parent_end_idx)

            child_start_time = segments[child_start_idx].start
            child_end_time = segments[child_end_idx - 1].end

            # 递归处理孙节点
            grandchildren_aligned = []
            if child.get("children"):
                grandchildren_aligned = self._align_children(
                    child["children"], segments, child_start_idx, child_end_idx
                )

            aligned.append(KGNodeWithTimestamp(
                node_name=child.get("name", ""),
                type_tag=child.get("typeTag", "通用"),
                depth=child.get("depth", 0),
                start_time=child_start_time,
                end_time=child_end_time,
                children=grandchildren_aligned
            ))

            segment_idx = child_end_idx

        return aligned
