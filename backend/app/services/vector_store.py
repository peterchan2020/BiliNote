import hashlib
import json
import os
import re
from typing import Optional

import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from app.utils.logger import get_logger

logger = get_logger(__name__)

NOTE_OUTPUT_DIR = os.getenv("NOTE_OUTPUT_DIR", "note_results")
VECTOR_DB_DIR = os.getenv("VECTOR_DB_DIR", "vector_db")

# UUID 格式正则（编译一次，全局复用）
_UUID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


def _chunk_markdown(markdown: str) -> list[dict]:
    """按 H2/H3 标题拆分 markdown 为语义块。"""
    sections = re.split(r'(?=^#{2,3}\s)', markdown, flags=re.MULTILINE)
    chunks = []
    for section in sections:
        section = section.strip()
        if not section or len(section) < 30:
            if section.strip():
                logger.debug(f"跳过短片段 (长度 {len(section)}): {section[:50]}...")
            continue
        heading_match = re.match(r'^(#{2,3})\s+(.+)', section)
        title = heading_match.group(2).strip() if heading_match else "intro"
        chunks.append({
            "text": section,
            "metadata": {"source_type": "markdown", "section_title": title},
        })
    return chunks


def _chunk_transcript(segments: list[dict], window_size: int = 15, overlap: int = 3) -> list[dict]:
    """将转录 segments 按滑动窗口分组。"""
    if not segments:
        return []
    chunks = []
    step = max(window_size - overlap, 1)
    for i in range(0, len(segments), step):
        window = segments[i:i + window_size]
        if not window:
            break
        text = "\n".join(
            f"[{seg.get('start', 0):.0f}s] {seg.get('text', '')}" for seg in window
        )
        chunks.append({
            "text": text,
            "metadata": {
                "source_type": "transcript",
                "start_time": window[0].get("start", 0),
                "end_time": window[-1].get("end", 0),
            },
        })
    return chunks


def _build_meta_chunk(audio_meta: dict) -> list[dict]:
    """将视频元信息（标题、作者、描述、标签等）构建为可检索的 chunk。"""
    if not audio_meta:
        return []

    raw = audio_meta.get("raw_info", {}) or {}
    parts = []

    title = audio_meta.get("title") or raw.get("title", "")
    if title:
        parts.append(f"视频标题：{title}")

    uploader = raw.get("uploader", "")
    if uploader:
        parts.append(f"视频作者/UP主：{uploader}")

    desc = raw.get("description", "")
    if desc:
        parts.append(f"视频简介：{desc[:500]}")

    tags = raw.get("tags", [])
    if tags and isinstance(tags, list):
        parts.append(f"标签：{', '.join(str(t) for t in tags[:20])}")

    duration = audio_meta.get("duration", 0)
    if duration:
        m, s = divmod(int(duration), 60)
        parts.append(f"视频时长：{m}分{s}秒")

    platform = audio_meta.get("platform", "")
    if platform:
        parts.append(f"平台：{platform}")

    url = raw.get("webpage_url", "")
    if url:
        parts.append(f"链接：{url}")

    if not parts:
        return []

    return [{
        "text": "\n".join(parts),
        "metadata": {"source_type": "meta"},
    }]


class VectorStoreManager:
    """基于 ChromaDB 的笔记向量存储管理器（单例）。"""

    _instance: Optional["VectorStoreManager"] = None
    _initialized = False

    def __new__(cls) -> "VectorStoreManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if VectorStoreManager._initialized:
            return
        VectorStoreManager._initialized = True

        os.makedirs(VECTOR_DB_DIR, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=VECTOR_DB_DIR,
            settings=Settings(anonymized_telemetry=False),
        )
        # 懒加载 embedding 函数，避免启动时下载模型
        self._embedding_fn: Optional[SentenceTransformerEmbeddingFunction] = None
        logger.info("VectorStoreManager 初始化完成（单例）")

    def _ensure_embedding_fn(self) -> SentenceTransformerEmbeddingFunction:
        """懒加载 embedding 函数，带国内镜像支持和清晰错误提示。"""
        if self._embedding_fn is not None:
            return self._embedding_fn

        model_name = "all-MiniLM-L6-v2"

        # 尝试使用国内 HF 镜像（如果配置了）
        if not os.environ.get("HF_ENDPOINT"):
            # 尝试常见国内镜像
            for mirror in ["https://hf-mirror.com", "https://huggingface.moecube.com"]:
                os.environ["HF_ENDPOINT"] = mirror
                try:
                    self._embedding_fn = SentenceTransformerEmbeddingFunction(model_name=model_name)
                    logger.info(f"使用镜像 {mirror} 加载 embedding 模型成功")
                    return self._embedding_fn
                except Exception as e:
                    logger.debug(f"镜像 {mirror} 加载失败: {e}")
                    continue

        # 没有配置镜像或镜像不可用，尝试默认
        try:
            self._embedding_fn = SentenceTransformerEmbeddingFunction(model_name=model_name)
            logger.info("加载 embedding 模型成功")
            return self._embedding_fn
        except Exception:
            raise ConnectionError(
                f"无法下载 embedding 模型 '{model_name}'。请执行以下操作之一：\n"
                f"1. 设置环境变量 HF_ENDPOINT=https://hf-mirror.com 使用国内镜像\n"
                f"2. 手动下载模型到 ~/.cache/huggingface/hub/\n"
                f"3. 确保网络可以访问 huggingface.co"
            )

    def _collection_name(self, task_id: str) -> str:
        """ChromaDB collection 名称：使用 task_id 的 hash，确保合法格式。"""
        if _UUID_PATTERN.match(task_id):
            return task_id.replace('-', '_')
        return f"task_{hashlib.sha256(task_id.encode()).hexdigest()[:16]}"

    def index_task(self, task_id: str) -> None:
        """读取笔记结果并建立向量索引。"""
        result_path = os.path.join(NOTE_OUTPUT_DIR, f"{task_id}.json")
        if not os.path.exists(result_path):
            raise FileNotFoundError(f"笔记文件不存在: {result_path}")

        with open(result_path, "r", encoding="utf-8") as f:
            note_data = json.load(f)

        markdown = note_data.get("markdown", "")
        transcript = note_data.get("transcript", {})
        segments = transcript.get("segments", [])
        audio_meta = note_data.get("audio_meta", {})

        meta_chunks = _build_meta_chunk(audio_meta)
        md_chunks = _chunk_markdown(markdown)
        tr_chunks = _chunk_transcript(segments)
        all_chunks = meta_chunks + md_chunks + tr_chunks

        if not all_chunks:
            logger.warning(f"笔记内容为空，跳过索引: {task_id}")
            return

        col_name = self._collection_name(task_id)

        try:
            self._client.delete_collection(col_name)
        except Exception as e:
            logger.debug(f"删除旧 collection 时出错（可能不存在）: {col_name}, {e}")

        collection = self._client.create_collection(
            name=col_name,
            embedding_function=self._ensure_embedding_fn(),
            metadata={"hnsw:space": "cosine"},
        )

        documents = [c["text"] for c in all_chunks]
        metadatas = [c["metadata"] for c in all_chunks]
        ids = [f"{task_id}_{i}" for i in range(len(all_chunks))]

        collection.add(documents=documents, metadatas=metadatas, ids=ids)
        logger.info(f"向量索引完成: task_id={task_id}, chunks={len(all_chunks)}")

    def delete_index(self, task_id: str) -> None:
        """删除指定任务的向量索引。"""
        col_name = self._collection_name(task_id)
        try:
            self._client.delete_collection(col_name)
            logger.info(f"已删除向量索引: {task_id}")
        except Exception:
            pass

    def is_indexed(self, task_id: str) -> bool:
        """检查指定任务是否已建立完整索引（含 meta 信息）。"""
        col_name = self._collection_name(task_id)
        try:
            col = self._client.get_collection(col_name)
            if col.count() == 0:
                return False
            meta = col.get(where={"source_type": "meta"}, limit=1)
            return len(meta["ids"]) > 0
        except Exception as e:
            logger.debug(f"检查索引状态时出错（可能不存在）: {task_id}, {e}")
            return False

    def query(self, task_id: str, query_text: str, quotas: Optional[dict] = None) -> list[dict]:
        """
        按固定配额从各来源检索向量。

        Args:
            task_id: 任务 ID
            query_text: 查询文本
            quotas: 各来源的检索配额，默认 {"meta": 2, "markdown": 5, "transcript": 8}

        Returns:
            检索到的 chunk 列表
        """
        if quotas is None:
            quotas = {"meta": 2, "markdown": 5, "transcript": 8}

        col_name = self._collection_name(task_id)
        try:
            collection = self._client.get_collection(col_name)
        except Exception:
            logger.warning(f"Collection 不存在: {task_id}")
            return []

        # 确保 embedding 函数已加载
        embedding_fn = self._ensure_embedding_fn()

        all_chunks: list[dict] = []
        for source_type, quota in quotas.items():
            try:
                results = collection.query(
                    query_texts=[query_text],
                    n_results=quota,
                    where={"source_type": source_type},
                    include=["documents", "metadatas", "distances"],
                )
                if results.get("documents") and results["documents"][0]:
                    for i in range(len(results["documents"][0])):
                        all_chunks.append({
                            "text": results["documents"][0][i],
                            "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
                            "distance": results["distances"][0][i] if results.get("distances") else None,
                        })
            except Exception as e:
                logger.debug(f"检索 {source_type} 失败: {e}")

        return all_chunks


def get_vector_store() -> VectorStoreManager:
    """获取 VectorStoreManager 单例。"""
    return VectorStoreManager()
