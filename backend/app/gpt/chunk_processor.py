from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, List, Optional, TypeVar

T = TypeVar("T")


class ChunkProcessor:
    def __init__(self, max_workers: int = 5):
        self._max_workers = max_workers

    def process(
        self,
        chunks: List[T],
        process_fn: Callable[[T], str],
        completed_count: int = 0,
        on_chunk_done: Optional[Callable[[int, str], None]] = None,
    ) -> List[str]:
        if not chunks:
            return []

        effective_workers = self._get_effective_workers(chunks, self._max_workers)
        results: List[Optional[str]] = [None] * len(chunks)

        with ThreadPoolExecutor(max_workers=effective_workers) as executor:
            future_to_index = {
                executor.submit(process_fn, chunk): idx
                for idx, chunk in enumerate(chunks)
            }

            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                result = future.result()  # 传播异常
                results[idx] = result
                if on_chunk_done is not None:
                    on_chunk_done(completed_count + idx, result)

        return results  # type: ignore[return-value]

    def _get_effective_workers(self, chunks: List, max_workers: int) -> int:
        return min(len(chunks), max_workers)
