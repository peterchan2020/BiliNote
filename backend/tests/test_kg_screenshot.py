"""测试 DetailedNotesGenerator 的截图注入逻辑"""
import unittest
from unittest.mock import MagicMock, patch
from app.utils.kg_detailed_notes import DetailedNotesGenerator, DetailedNotesConfig


class TestScreenshotPromptInjection(unittest.TestCase):
    def test_screenshot_prompt_not_injected_when_conditions_not_met(self):
        """三条件不全时，不应注入截图指令"""
        mock_gpt = MagicMock()
        mock_gpt.summarize.return_value = "## Test Chapter\n\nContent"
        mock_transcript = MagicMock()
        mock_transcript.segments = []

        config = DetailedNotesConfig()
        gen = DetailedNotesGenerator(
            gpt=mock_gpt,
            transcript=mock_transcript,
            config=config,
            video_understanding=False,  # 条件1不满足
            style="knowledge_graph",
            formats=["screenshot"],
        )
        self.assertFalse(gen.should_insert_screenshots)

    def test_screenshot_prompt_injected_when_all_conditions_met(self):
        """三条件全满足时，应注入截图指令"""
        mock_gpt = MagicMock()
        mock_gpt.summarize.return_value = "## Test Chapter\n\nContent"
        mock_transcript = MagicMock()
        mock_transcript.segments = []

        config = DetailedNotesConfig()
        gen = DetailedNotesGenerator(
            gpt=mock_gpt,
            transcript=mock_transcript,
            config=config,
            video_understanding=True,
            style="knowledge_graph",
            formats=["screenshot"],
        )
        self.assertTrue(gen.should_insert_screenshots)


class TestScreenshotPromptContent(unittest.TestCase):
    def test_chapter_prompt_contains_screenshot_instruction(self):
        """章节级 prompt 应包含截图指令"""
        mock_gpt = MagicMock()
        mock_gpt.summarize.return_value = "## Test Chapter\n\nContent here"
        mock_transcript = MagicMock()
        segment = MagicMock()
        segment.start = 0.0
        segment.end = 60.0
        segment.text = "test content"
        mock_transcript.segments = [segment]

        config = DetailedNotesConfig()
        gen = DetailedNotesGenerator(
            gpt=mock_gpt,
            transcript=mock_transcript,
            config=config,
            video_understanding=True,
            style="knowledge_graph",
            formats=["screenshot"],
        )

        node = MagicMock()
        node.depth = 1
        node.node_name = "Test Chapter"
        node.type_tag = "概念"
        node.start_time = 0.0
        node.end_time = 60.0
        node.children = []

        gen._generate_chapter_level(node)

        call_args = mock_gpt.summarize.call_args
        source = call_args[0][0]
        self.assertIn("Screenshot", source.extras)

    def test_leaf_prompt_contains_screenshot_instruction(self):
        """知识点级 prompt 应包含截图指令"""
        mock_gpt = MagicMock()
        mock_gpt.summarize.return_value = "### Test Point\n\nContent here"
        mock_transcript = MagicMock()
        segment = MagicMock()
        segment.start = 10.0
        segment.end = 30.0
        segment.text = "test point content"
        mock_transcript.segments = [segment]

        config = DetailedNotesConfig()
        gen = DetailedNotesGenerator(
            gpt=mock_gpt,
            transcript=mock_transcript,
            config=config,
            video_understanding=True,
            style="knowledge_graph",
            formats=["screenshot"],
        )

        node = MagicMock()
        node.depth = 2
        node.node_name = "Test Point"
        node.type_tag = "原理"
        node.start_time = 10.0
        node.end_time = 30.0
        node.children = []

        gen._generate_leaf_level(node)

        call_args = mock_gpt.summarize.call_args
        source = call_args[0][0]
        self.assertIn("Screenshot", source.extras)


if __name__ == "__main__":
    unittest.main()
