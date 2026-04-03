import importlib.util
import pathlib
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "app" / "gpt" / "chunk_processor.py"
spec = importlib.util.spec_from_file_location("chunk_processor", MODULE_PATH)
if spec is None or spec.loader is None:
    raise ImportError("chunk_processor module spec not found")
chunk_processor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chunk_processor)
ChunkProcessor = chunk_processor.ChunkProcessor


class TestChunkProcessor(unittest.TestCase):
    def test_process_preserves_order(self):
        """并发执行，但结果顺序必须与输入 chunks 一致"""
        processor = ChunkProcessor(max_workers=5)

        # 模拟耗时不等的处理函数：后提交的先完成
        results_order = []
        lock = threading.Lock()

        def slow_fn(chunk):
            # chunk.payload 越大，睡得越短（反序完成）
            time.sleep(0.1 - chunk.payload * 0.01)
            result = f"result-{chunk.payload}"
            with lock:
                results_order.append(chunk.payload)
            return result

        class _Chunk:
            def __init__(self, payload):
                self.payload = payload

        chunks = [_Chunk(i) for i in range(5)]
        results = processor.process(chunks, slow_fn)

        # 结果顺序必须与输入一致
        self.assertEqual(results, [f"result-{i}" for i in range(5)])
        # 验证确实是乱序完成的（并发生效）
        self.assertNotEqual(results_order, list(range(5)))

    def test_process_propagates_exception(self):
        """任一 chunk 失败，应立即抛出异常"""
        processor = ChunkProcessor(max_workers=3)

        def failing_fn(chunk):
            if chunk.payload == 2:
                raise ValueError("chunk-2 failed")
            return f"ok-{chunk.payload}"

        class _Chunk:
            def __init__(self, payload):
                self.payload = payload

        chunks = [_Chunk(i) for i in range(5)]

        with self.assertRaises(ValueError) as ctx:
            processor.process(chunks, failing_fn)
        self.assertIn("chunk-2 failed", str(ctx.exception))

    def test_process_calls_on_chunk_done(self):
        """每个 chunk 完成后触发回调"""
        processor = ChunkProcessor(max_workers=3)
        callback_results = []

        def fn(chunk):
            return f"result-{chunk.payload}"

        class _Chunk:
            def __init__(self, payload):
                self.payload = payload

        chunks = [_Chunk(i) for i in range(3)]
        processor.process(
            chunks,
            fn,
            completed_count=2,
            on_chunk_done=lambda idx, result: callback_results.append((idx, result)),
        )

        self.assertEqual(len(callback_results), 3)
        # 回调中的 index 必须包含 completed_count 偏移
        indices = [idx for idx, _ in callback_results]
        self.assertEqual(sorted(indices), [2, 3, 4])

    def test_respects_max_workers_limit(self):
        """同时执行的线程数不应超过 max_workers"""
        max_workers = 2
        processor = ChunkProcessor(max_workers=max_workers)

        state_lock = threading.Lock()
        state = {"active": 0, "peak_active": 0}

        def track_and_wait(chunk):
            with state_lock:
                state["active"] += 1
                state["peak_active"] = max(state["peak_active"], state["active"])
            time.sleep(0.05)
            with state_lock:
                state["active"] -= 1
            return f"done-{chunk.payload}"

        class _Chunk:
            def __init__(self, payload):
                self.payload = payload

        chunks = [_Chunk(i) for i in range(6)]
        processor.process(chunks, track_and_wait)

        # 峰值并发不应超过 max_workers（允许 +1 容差）
        self.assertLessEqual(state["peak_active"], max_workers + 1)

    def test_process_empty_chunks(self):
        """空 chunks 列表应返回空列表"""
        processor = ChunkProcessor(max_workers=3)
        results = processor.process([], lambda c: "x")
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
