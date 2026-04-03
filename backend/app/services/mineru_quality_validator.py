"""
MinerU Quality Validation Module

Extracts and enhances quality validation from MinerUService.
Provides heading hierarchy validation and content-list alignment checks.
"""

import re
from app.models.mineru_model import MinerUMarkdownQualityReport


class MinerUQualityValidator:
    """
    Quality validation for MinerU PDF parsing results.

    Validates:
    - Heading hierarchy (H1→H2→H3→H4 structure)
    - Content-list to markdown alignment
    """

    # PDF magic bytes for validation
    PDF_MAGIC = b"%PDF"

    @staticmethod
    def validate_pdf_bytes(pdf_bytes: bytes) -> tuple[bool, str]:
        """
        Validate PDF magic bytes.

        Args:
            pdf_bytes: Raw bytes of the file to validate.

        Returns:
            Tuple of (is_valid, error_message).
            error_message is empty string if valid.
        """
        if len(pdf_bytes) == 0:
            return False, "File is empty"

        if len(pdf_bytes) < 5:
            return False, "PDF file is too short"

        if not pdf_bytes.startswith(MinerUQualityValidator.PDF_MAGIC):
            return False, "Invalid PDF: missing magic bytes '%PDF'"

        return True, ""

    @staticmethod
    def validate_heading_hierarchy(md_content: str) -> tuple[bool, list[str]]:
        """
        Verify H1→H2→H3→H4 hierarchy.

        Rules:
        - H2 cannot appear before H1
        - H3 cannot appear before H2
        - H4 cannot appear before H3
        - No skipping levels (H1→H4 without H2/H3)

        Args:
            md_content: Markdown content to validate.

        Returns:
            Tuple of (is_valid, list_of_violations).
            Empty violations list if valid.
        """
        violations: list[str] = []

        if not md_content.strip():
            return True, violations

        # Find all headings with their line numbers
        lines = md_content.split("\n")
        heading_levels: list[tuple[int, int]] = []  # (line_number, level)

        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                # Use regex to count heading level: # followed by space (or end of #s)
                match = re.match(r"^(#{1,6})\s", stripped)
                if match:
                    level = len(match.group(1))
                    heading_levels.append((i + 1, level))

        if not heading_levels:
            # No headings at all - not a violation, just no hierarchy to validate
            return True, violations

        # Check each heading for hierarchy violations
        prev_level = 0
        for line_num, level in heading_levels:
            if prev_level == 0:
                # First heading - must be H1 or any level is ok if no H1 exists
                if level != 1:
                    # Check if there's any H1 before this
                    has_h1_before = any(lvl == 1 for _, lvl in heading_levels if _ < line_num)
                    if not has_h1_before and level > 1:
                        violations.append(
                            f"Line {line_num}: H{level} appears before any H1 heading"
                        )
            elif level > prev_level + 1:
                # Skipped a level (e.g., H1 → H3, or H2 → H4)
                violations.append(
                    f"Line {line_num}: H{level} skips intermediate level(s) after H{prev_level} "
                    f"(expected H{prev_level + 1} next)"
                )

            prev_level = level

        return len(violations) == 0, violations

    @staticmethod
    def validate_content_list_mapping(
        md_content: str, content_list: list[dict]
    ) -> tuple[bool, list[str]]:
        """
        Cross-check content_list titles appear in md_content.

        Args:
            md_content: Markdown content to check against.
            content_list: List of content items with 'type' and 'content' fields.
                         Only items with type='title' are checked.

        Returns:
            Tuple of (is_aligned, list_of_warnings).
            Empty warnings list if all titles found.
        """
        warnings: list[str] = []

        if not content_list:
            return True, warnings

        if not md_content.strip():
            if content_list:
                warnings.append("md_content is empty but content_list has items")
                return False, warnings
            return True, warnings

        # Extract all title-type items from content_list
        title_items = [
            item for item in content_list
            if isinstance(item, dict) and item.get("type") == "title"
        ]

        if not title_items:
            return True, warnings

        # Check each title appears in md_content
        for item in title_items:
            content = item.get("content", "")
            if content and content not in md_content:
                level = item.get("level", "?")
                warnings.append(
                    f"Title '{content}' (level {level}) from content_list not found in md_content"
                )

        return len(warnings) == 0, warnings

    @staticmethod
    def validate_markdown_quality(md_content: str) -> "MinerUMarkdownQualityReport":
        """
        Validate markdown quality and return a comprehensive quality report.

        Args:
            md_content: Markdown content to validate.

        Returns:
            MinerUMarkdownQualityReport with heading counts, warnings, and hierarchy validation.
        """
        from app.models.mineru_model import MinerUMarkdownQualityReport

        warnings: list[str] = []
        h1_count = 0
        h2_count = 0
        h3_count = 0
        h4_plus_count = 0
        max_heading_depth = 0

        if not md_content.strip():
            return MinerUMarkdownQualityReport(
                total_chars=0,
                heading_count=0,
                h1_count=0,
                h2_count=0,
                h3_count=0,
                h4_plus_count=0,
                max_heading_depth=0,
                has_valid_structure=False,
                estimated_chapter_count=0,
                warnings=["Empty markdown content"],
            )

        total_chars = len(md_content)

        # Find all headings with their levels
        lines = md_content.split("\n")
        heading_levels: list[int] = []

        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("#"):
                match = re.match(r"^(#{1,6})\s", stripped)
                if match:
                    level = len(match.group(1))
                    heading_levels.append(level)
                    if level == 1:
                        h1_count += 1
                    elif level == 2:
                        h2_count += 1
                    elif level == 3:
                        h3_count += 1
                    else:
                        h4_plus_count += 1
                    max_heading_depth = max(max_heading_depth, level)

        heading_count = h1_count + h2_count + h3_count + h4_plus_count

        # Check for scanned PDF (no headings)
        if heading_count == 0:
            warnings.append("No headings found - this may be a scanned PDF")

        # Check for valid structure (at least one H1)
        has_valid_structure = h1_count > 0

        if h1_count == 0 and heading_count > 0:
            warnings.append("No H1 heading found - markdown should start with an H1")

        # Validate heading hierarchy
        hierarchy_valid, hierarchy_violations = MinerUQualityValidator.validate_heading_hierarchy(md_content)

        # Estimate chapter count based on H1 + H2
        estimated_chapter_count = h1_count + h2_count

        return MinerUMarkdownQualityReport(
            total_chars=total_chars,
            heading_count=heading_count,
            h1_count=h1_count,
            h2_count=h2_count,
            h3_count=h3_count,
            h4_plus_count=h4_plus_count,
            max_heading_depth=max_heading_depth,
            has_valid_structure=has_valid_structure,
            estimated_chapter_count=estimated_chapter_count,
            warnings=warnings,
            heading_hierarchy_valid=hierarchy_valid,
            heading_hierarchy_violations=hierarchy_violations,
        )
