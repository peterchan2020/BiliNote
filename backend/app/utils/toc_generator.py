"""
TOC 目录生成工具

扫描 Markdown 中所有标题行，生成带锚点的目录，并在原文标题前插入锚点标记。
"""

import re
from typing import Tuple, Dict


def _generate_slug(text: str) -> str:
    """
    将标题文本转为 slug 锚点 ID。
    - 中文保留
    - 空格转 -
    - 移除特殊字符（保留字母、数字、中文、连字符）
    - 转小写（仅英文部分）
    """
    slug = text.strip()
    # 转小写
    slug = slug.lower()
    # 空格转连字符
    slug = slug.replace(' ', '-')
    # 只保留：字母、数字、中文、连字符、下划线
    slug = re.sub(r'[^\w\u4e00-\u9fff-]', '', slug)
    # 合并连续连字符
    slug = re.sub(r'-{2,}', '-', slug)
    # 去除首尾连字符
    slug = slug.strip('-')
    return slug


def generate_toc_from_markdown(markdown: str) -> Tuple[str, str]:
    """
    扫描 Markdown 文本中的所有标题行，生成 TOC 目录和带锚点标记的正文。

    Args:
        markdown: 原始 Markdown 文本

    Returns:
        (toc_string, modified_markdown) 元组
        - toc_string: 生成的目录 Markdown 段落
        - modified_markdown: 在每个标题前插入了 <a id="slug"></a> 的 Markdown 文本
    """
    # 匹配标题行：## 标题, ### 标题, #### 标题 等
    heading_pattern = re.compile(r'^(#{2,6})\s+(.+)$', re.MULTILINE)

    # 第一遍：收集所有标题，生成 slug（处理重复）
    slug_counter: Dict[str, int] = {}
    headings = []  # list of (level, text, slug, match_obj)

    for match in heading_pattern.finditer(markdown):
        level = len(match.group(1))  # ## = 2, ### = 3, etc.
        text = match.group(2).strip()
        base_slug = _generate_slug(text)

        if not base_slug:
            base_slug = 'heading'

        # 处理重复 slug
        if base_slug in slug_counter:
            slug_counter[base_slug] += 1
            slug = f"{base_slug}-{slug_counter[base_slug]}"
        else:
            slug_counter[base_slug] = 0
            slug = base_slug

        headings.append((level, text, slug, match))

    if not headings:
        return ("", markdown)

    # 找到最小标题级别作为顶级（通常是 ##）
    min_level = min(h[0] for h in headings)

    # 生成 TOC
    toc_lines = ["## 笔记目录", ""]
    for level, text, slug, _ in headings:
        indent = "  " * (level - min_level)
        toc_lines.append(f"{indent}- [{text}](#{slug})")

    toc_string = "\n".join(toc_lines)

    # 第二遍：在原文每个标题行前插入锚点标记（从后往前替换，避免偏移）
    modified = markdown
    for level, text, slug, match in reversed(headings):
        anchor = f'<a id="{slug}"></a>\n'
        start = match.start()
        modified = modified[:start] + anchor + modified[start:]

    return (toc_string, modified)
