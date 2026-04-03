from dataclasses import dataclass
from typing import Optional

from app.models.audio_model import AudioDownloadResult
from app.models.transcriber_model import TranscriptResult


@dataclass
class NoteResult:
    markdown: str                  # GPT 总结的 Markdown 内容
    transcript: TranscriptResult                # Whisper 转写结果
    audio_meta: AudioDownloadResult  # 音频下载的元信息（title、duration、封面等）
    knowledge_graph: Optional[str] = None  # 知识图谱 Markdown（可选）
    kg_warning: Optional[str] = None  # 知识图谱深度超限警告（可选）
    detailed_notes: Optional[str] = None  # 详细长笔记（可选）