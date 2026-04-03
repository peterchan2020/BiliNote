"""
知识图谱工具函数
用于分离笔记Markdown和知识图谱Markdown
"""

import re
from typing import Tuple, Optional, List, Dict, Any


# 常量定义
KNOWLEDGE_GRAPH_MAX_DEPTH = 6
ATOMIC_TYPE_TAGS = {'结论', '公式'}
DECOMPOSABLE_TYPES = {'概念', '原理', '方法', '工具', '案例', '步骤', '对比'}
KG_SEPARATOR = '<!-- KNOWLEDGE_GRAPH -->'


def split_knowledge_graph(markdown: str) -> Tuple[str, Optional[str]]:
    """
    分离笔记Markdown和知识图谱Markdown

    :param markdown: 包含笔记和知识图谱的完整Markdown文本
    :return: (笔记部分, 知识图谱部分)，如果没有知识图谱则第二个元素为None
    """
    if markdown is None:
        return None, None

    if KG_SEPARATOR in markdown:
        parts = markdown.split(KG_SEPARATOR, 1)
        return parts[0].strip(), parts[1].strip()
    return markdown, None


def parse_kg_node(line: str) -> Dict[str, Any]:
    """
    解析知识图谱节点

    :param line: Markdown节点行（如 "- 节点名 [类型]" 或 "## 节点名 [类型]"）
    :return: 包含name, typeTag, depth的字典
    """
    if not line:
        return {'name': '', 'typeTag': '通用', 'depth': 0}

    # 计算深度 - 基于标题层级或bullet缩进
    depth = 0
    content = line

    # 检查Markdown标题层级 (# ## ### #### etc)
    # 深度体系：#=0, ##=1, ###=2, ####=3, ...
    # - bullet在顶级(##下)=depth=2，在###下=depth=3
    heading_match = re.match(r'^(#+)\s+', line)
    if heading_match:
        hlen = len(heading_match.group(1))
        depth = max(0, hlen - 1)  # ##→1, ###→2, ####→3
    else:
        # 检查bullet缩进 (- 或   - )
        bullet_match = re.match(r'^(\s*)[-*]\s+', line)
        if bullet_match:
            # Bullet永远比顶级章节深一层(depth=2)，在###下为depth=3
            leading_spaces = len(bullet_match.group(1))
            depth = 2 + (leading_spaces // 2)

    # 移除标题前缀和bullet前缀来获取内容
    content = re.sub(r'^#{1,6}\s+', '', content)
    content = re.sub(r'^[\s*\-]+\s*', '', content)

    # 解析类型标签 [类型]
    tag_match = re.search(r'\[([^\]]+)\]$', content)
    if tag_match:
        type_tag = tag_match.group(1).strip()
        name = content[:tag_match.start()].strip()
    else:
        type_tag = '通用'
        name = content.strip()

    return {
        'name': name,
        'typeTag': type_tag,
        'depth': depth
    }


def _parse_kg_lines(kg: str) -> List[Dict[str, Any]]:
    """
    解析知识图谱文本为节点列表

    :param kg: 知识图谱Markdown文本
    :return: 节点列表，每个节点包含name, typeTag, depth, line等
    """
    if not kg:
        return []

    lines = kg.split('\n')
    nodes = []

    for line in lines:
        line = line.rstrip()
        if not line.strip():
            continue

        node = parse_kg_node(line)
        node['line'] = line
        nodes.append(node)

    return nodes


def validate_kg_depth(kg: str) -> Tuple[bool, int, List[Dict[str, Any]]]:
    """
    验证知识图谱深度是否超限

    :param kg: 知识图谱Markdown文本
    :return: (是否有效, 最大深度, 超过深度的节点列表)
    """
    if kg is None or kg == '':
        return True, 0, []

    nodes = _parse_kg_lines(kg)

    if not nodes:
        return True, 0, []

    max_depth = max(node['depth'] for node in nodes)
    exceeded_nodes = [node for node in nodes if node['depth'] >= KNOWLEDGE_GRAPH_MAX_DEPTH]

    is_valid = max_depth < KNOWLEDGE_GRAPH_MAX_DEPTH

    return is_valid, max_depth, exceeded_nodes


def truncate_kg_by_depth(kg: str) -> Optional[str]:
    """
    按深度截断知识图谱

    :param kg: 知识图谱Markdown文本
    :return: 截断后的知识图谱文本，如果输入为None则返回None
    """
    if kg is None:
        return None

    if kg == '':
        return ''

    lines = kg.split('\n')
    result_lines = []
    skip_until_depth = -1  # 如果设置为 >= 0，则跳过所有深度 > skip_until_depth 的节点

    for line in lines:
        original_line = line
        line = line.rstrip()

        if not line.strip():
            continue

        # 解析节点
        node = parse_kg_node(line)

        # 检查是否是原子类型 - 原子类型后面的同级别节点不再跳过
        is_atomic = node['typeTag'] in ATOMIC_TYPE_TAGS

        # 如果当前深度 <= 跳过深度限制，则重置跳过状态
        if skip_until_depth >= 0 and node['depth'] <= skip_until_depth:
            skip_until_depth = -1

        # 如果处于跳过状态且当前节点深度大于限制深度，则跳过
        if skip_until_depth >= 0 and node['depth'] > skip_until_depth:
            continue

        # 检查是否超出最大深度限制
        if node['depth'] > KNOWLEDGE_GRAPH_MAX_DEPTH:
            # 设置跳过状态：如果不是原子节点的后代，则跳过
            if not is_atomic:
                skip_until_depth = node['depth']
            continue

        # 如果是原子类型节点，设置跳过状态以阻止后续子孙节点
        if is_atomic:
            skip_until_depth = node['depth']

        result_lines.append(original_line)

    return '\n'.join(result_lines)


def build_kg_tree(kg: str) -> List[Dict[str, Any]]:
    """
    将扁平的知识图谱文本解析为嵌套树结构

    KG格式语义：
    - # 标题 (depth=0): 整个KG的根
    - ## 标题 (depth=1): 顶级章节（root的直接子节点）
    - ### 标题 (depth=2): 顶级章节的子章节
    - - bullet (depth=1): 顶级章节的内容节点
    - - bullet (depth=2): 子章节的内容节点

    由于##和-在parse_kg_node中都被计算为depth=1，导致算法无法区分：
    - 同级章节（##之间应该是兄弟）
    - 章节的内容（-应该是##的子节点）

    本函数使用章节追踪器来处理这个特殊情况：
    - 维护last_chapter（最近遇到的##节点）
    - 所有非##节点都添加到last_chapter的children
    - 遇到##时，更新last_chapter
    """
    if not kg:
        return []

    lines = kg.strip().split('\n')
    parsed_nodes = []
    for line in lines:
        line = line.rstrip()
        if not line.strip():
            continue
        node = parse_kg_node(line)
        if node.get('name'):
            node['children'] = []
            parsed_nodes.append(node)

    if not parsed_nodes:
        return []

    # 检查是否需要章节追踪（KG中有##节点）
    has_h2 = any(n['depth'] == 1 and n['name'] != parsed_nodes[0]['name']
                 for n in parsed_nodes if n.get('name'))

    if not has_h2:
        # 没有##节点，直接用深度构建树
        root: List[Dict[str, Any]] = []
        stack: List[Dict[str, Any]] = []
        for node in parsed_nodes:
            node_depth = node['depth']
            while stack and stack[-1]['depth'] > node_depth:
                stack.pop()
            if not stack:
                root.append(node)
            else:
                stack[-1]['children'].append(node)
            stack.append(node)
        return root

    # 使用章节追踪器构建树
    root: List[Dict[str, Any]] = []
    stack: List[Dict[str, Any]] = []

    for node in parsed_nodes:
        node_depth = node['depth']
        # 判断是否为顶级章节（##节点，depth=1）
        is_top_chapter = node_depth == 1

        if is_top_chapter:
            # 新章节：弹出栈中所有depth>=1的节点（清除上一个章节的残留）
            while stack and stack[-1]['depth'] >= 1:
                stack.pop()
            # 没有父节点则作为根，否则作为栈顶的子节点
            if not stack:
                root.append(node)
            else:
                stack[-1]['children'].append(node)
            stack.append(node)
        else:
            # 非顶级节点（###或更深，或bullet）：添加到最近章节的children
            # 使用 >= 而非 >：同一深度的兄弟节点应该被弹出（成为siblings而非嵌套）
            while stack and stack[-1]['depth'] >= node_depth:
                stack.pop()
            if not stack:
                # 没有章节上下文，作为根节点
                root.append(node)
                stack.append(node)
            else:
                stack[-1]['children'].append(node)
                stack.append(node)

    # 后处理：如果根节点只有一个"主题"子节点，说明子节点是包含所有章节的容器，
    # 把其子节点（所有章节）全部提升为根的直接子节点
    if len(root) == 1 and root[0].get('typeTag') in ('主题', '通用'):
        container = root[0].get('children', [])
        if len(container) == 1:
            grandchildren = container[0].get('children', [])
            if grandchildren:
                root[0]['children'] = [
                    dict(node, depth=1, children=node.get('children', []))
                    for node in grandchildren
                ]

    return root
