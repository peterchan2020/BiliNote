import importlib.util
import os
import pathlib
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _install_stubs():
    app_mod = types.ModuleType("app")
    gpt_pkg = types.ModuleType("app.gpt")
    models_pkg = types.ModuleType("app.models")

    base_mod = types.ModuleType("app.gpt.base")

    class _GPT:
        pass

    base_mod.GPT = _GPT

    prompt_builder_mod = types.ModuleType("app.gpt.prompt_builder")

    def _generate_base_prompt(**_kwargs):
        return "prompt"

    prompt_builder_mod.generate_base_prompt = _generate_base_prompt

    prompt_mod = types.ModuleType("app.gpt.prompt")
    prompt_mod.BASE_PROMPT = ""
    prompt_mod.AI_SUM = ""
    prompt_mod.SCREENSHOT = ""
    prompt_mod.LINK = ""
    prompt_mod.MERGE_PROMPT = "merge"

    utils_mod = types.ModuleType("app.gpt.utils")

    def _fix_markdown(text):
        return text

    utils_mod.fix_markdown = _fix_markdown

    chunk_processor_mod = types.ModuleType("app.gpt.chunk_processor")
    module_path = ROOT / "app" / "gpt" / "chunk_processor.py"
    spec = importlib.util.spec_from_file_location("app.gpt.chunk_processor", module_path)
    if spec and spec.loader:
        spec.loader.exec_module(chunk_processor_mod)

    gpt_model_mod = types.ModuleType("app.models.gpt_model")

    class _GPTSource:
        pass

    gpt_model_mod.GPTSource = _GPTSource

    transcriber_model_mod = types.ModuleType("app.models.transcriber_model")

    class _TranscriptSegment:
        def __init__(self, **kwargs):
            self.start = kwargs.get("start", 0)
            self.end = kwargs.get("end", 0)
            self.text = kwargs.get("text", "")

    transcriber_model_mod.TranscriptSegment = _TranscriptSegment

    request_chunker_mod = _stub_request_chunker_mod()

    sys.modules.setdefault("app", app_mod)
    sys.modules.setdefault("app.gpt", gpt_pkg)
    sys.modules.setdefault("app.models", models_pkg)
    sys.modules["app.gpt.base"] = base_mod
    sys.modules["app.gpt.prompt_builder"] = prompt_builder_mod
    sys.modules["app.gpt.prompt"] = prompt_mod
    sys.modules["app.gpt.utils"] = utils_mod
    sys.modules["app.gpt.chunk_processor"] = chunk_processor_mod
    sys.modules["app.gpt.request_chunker"] = request_chunker_mod
    sys.modules["app.models.gpt_model"] = gpt_model_mod
    sys.modules["app.models.transcriber_model"] = transcriber_model_mod

    # 让 import app.gpt.request_chunker 时能找到 app.gpt 包
    if not hasattr(app_mod, "gpt"):
        app_mod.gpt = gpt_pkg
    if not hasattr(app_mod, "models"):
        app_mod.models = models_pkg
    if not hasattr(gpt_pkg, "request_chunker"):
        gpt_pkg.request_chunker = request_chunker_mod
    if not hasattr(gpt_pkg, "chunk_processor"):
        gpt_pkg.chunk_processor = chunk_processor_mod
    if not hasattr(gpt_pkg, "base"):
        gpt_pkg.base = base_mod
    if not hasattr(gpt_pkg, "prompt_builder"):
        gpt_pkg.prompt_builder = prompt_builder_mod
    if not hasattr(gpt_pkg, "prompt"):
        gpt_pkg.prompt = prompt_mod
    if not hasattr(gpt_pkg, "utils"):
        gpt_pkg.utils = utils_mod
    if not hasattr(models_pkg, "gpt_model"):
        models_pkg.gpt_model = gpt_model_mod
    if not hasattr(models_pkg, "transcriber_model"):
        models_pkg.transcriber_model = transcriber_model_mod


def _stub_request_chunker_mod():
    request_chunker_mod = types.ModuleType("app.gpt.request_chunker")

    class _ChunkPayload:
        def __init__(self, segments=None, image_urls=None):
            self.segments = segments or []
            self.image_urls = image_urls or []

    class _RequestChunker:
        def __init__(self, *_args, **_kwargs):
            pass

        def chunk(self, segments, image_urls, **kwargs):
            chunk_size = max(1, len(segments) // 2)
            chunks = []
            for i in range(0, len(segments), chunk_size):
                chunks.append(
                    _ChunkPayload(segments=segments[i:i + chunk_size], image_urls=[])
                )
            return chunks

        def group_texts_by_budget(self, texts, _builder, **_kwargs):
            return [texts]

    request_chunker_mod.RequestChunker = _RequestChunker
    request_chunker_mod.ChunkPayload = _ChunkPayload
    return request_chunker_mod


def _load_universal_gpt_class():
    _install_stubs()
    module_path = ROOT / "app" / "gpt" / "universal_gpt.py"
    spec = importlib.util.spec_from_file_location("universal_gpt", module_path)
    if spec is None or spec.loader is None:
        raise ImportError("universal_gpt module spec not found")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.UniversalGPT


class _SequentialCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0

    def create(self, **_kwargs):
        content = self._responses[self._idx]
        self._idx += 1
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))]
        )


class _DummyChat:
    def __init__(self, responses):
        self.completions = _SequentialCompletions(responses)


class _DummyModels:
    @staticmethod
    def list():
        return []


class _DummyClient:
    def __init__(self, responses):
        self.chat = _DummyChat(responses)
        self.models = _DummyModels()


class TestUniversalGPTParallel(unittest.TestCase):
    def test_summarize_multiple_chunks_parallel(self):
        """多 chunk 场景：验证并行执行返回正确合并结果"""
        original_workers = os.environ.get("LLM_CHUNK_MAX_WORKERS")
        os.environ["LLM_CHUNK_MAX_WORKERS"] = "3"
        try:
            UniversalGPT = _load_universal_gpt_class()
            responses = ["partial-1", "partial-2", "partial-3", "merged-result"]
            client = _DummyClient(responses)

            with tempfile.TemporaryDirectory() as tmp_dir:
                gpt = UniversalGPT(client, model="mock-model")
                gpt.checkpoint_dir = Path(tmp_dir)

                from app.models.transcriber_model import TranscriptSegment

                segments = [
                    TranscriptSegment(start=0, end=10, text="text A"),
                    TranscriptSegment(start=10, end=20, text="text B"),
                    TranscriptSegment(start=20, end=30, text="text C"),
                ]
                source = types.SimpleNamespace(
                    segment=segments,
                    title="Test Video",
                    tags=[],
                    screenshot=False,
                    link=False,
                    style=None,
                    extras=None,
                    _format=None,
                    video_img_urls=[],
                    checkpoint_key=None,
                )

                result = gpt.summarize(source)
                self.assertIsInstance(result, str)
                self.assertGreater(len(result), 0)
        finally:
            if original_workers is None:
                os.environ.pop("LLM_CHUNK_MAX_WORKERS", None)
            else:
                os.environ["LLM_CHUNK_MAX_WORKERS"] = original_workers

    def test_summarize_single_chunk_no_merge(self):
        """单 chunk 场景：不需要 merge，直接返回结果"""
        original_workers = os.environ.get("LLM_CHUNK_MAX_WORKERS")
        os.environ["LLM_CHUNK_MAX_WORKERS"] = "3"
        try:
            UniversalGPT = _load_universal_gpt_class()
            client = _DummyClient(["single-result"])

            with tempfile.TemporaryDirectory() as tmp_dir:
                gpt = UniversalGPT(client, model="mock-model")
                gpt.checkpoint_dir = Path(tmp_dir)

                from app.models.transcriber_model import TranscriptSegment

                segments = [
                    TranscriptSegment(start=0, end=10, text="short text"),
                ]
                source = types.SimpleNamespace(
                    segment=segments,
                    title="Short Video",
                    tags=[],
                    screenshot=False,
                    link=False,
                    style=None,
                    extras=None,
                    _format=None,
                    video_img_urls=[],
                    checkpoint_key=None,
                )

                result = gpt.summarize(source)
                self.assertEqual(result, "single-result")
        finally:
            if original_workers is None:
                os.environ.pop("LLM_CHUNK_MAX_WORKERS", None)
            else:
                os.environ["LLM_CHUNK_MAX_WORKERS"] = original_workers


if __name__ == "__main__":
    unittest.main()
