"""
TOC 目录生成工具

扫描 Markdown 中所有标题行，生成带锚点的目录。
前端 rehype-slug 会自动为标题生成 id，无需手动插入锚点标记。
"""

import re
from typing import Tuple, Dict


def _generate_slug(text: str) -> str:
    """
    将标题文本转为 slug 锚点 ID，与 rehype-slug (github-slugger v2) 行为对齐。
    规则：小写 → 空格转 '-' → 移除非 [字母/数字/连接符标点/组合标记/连字符] 字符。
    不合并连续连字符，不去除首尾连字符。
    """
    slug = text.strip().lower()
    slug = slug.replace(' ', '-')
    # 保留：Unicode 字母、数字、连接符标点(如 _)、组合标记、连字符
    # \w 在 Python 3 中匹配 Unicode 字母/数字/下划线
    # \u0300-\u036f 覆盖常见组合变音标记（对应 \p{M} 的常用子集）
    slug = re.sub(r'[^\w\u0300-\u036f-]', '', slug)
    return slug


def generate_toc_from_markdown(markdown: str) -> Tuple[str, str]:
    """
    扫描 Markdown 文本中的所有标题行，生成 TOC 目录。

    前端 rehype-slug 会自动为标题元素生成 id 属性，因此不修改原始 Markdown。

    Args:
        markdown: 原始 Markdown 文本

    Returns:
        (toc_string, markdown) 元组
        - toc_string: 生成的目录 Markdown 段落
        - markdown: 未修改的原始 Markdown 文本
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

    return (toc_string, markdown)
