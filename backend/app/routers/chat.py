from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel
import json
import os
import threading

from app.services.chat_service import chat as chat_service
from app.services.vector_store import VectorStoreManager
from app.utils.logger import get_logger
from app.utils.response import ResponseWrapper as R

logger = get_logger(__name__)

router = APIRouter()

# 索引状态追踪: task_id -> "indexing" | "indexed" | "failed"
_index_status: dict[str, str] = {}

# 用于保护 _index_status 字典的并发访问
_index_lock = threading.Lock()

# 持久化状态文件路径
import os
_STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "index_state.json")

def _load_persistent_status() -> dict[str, str]:
    """从持久化文件加载索引状态"""
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"加载持久化状态失败: {e}")
    return {}

def _save_persistent_status_atomic(status: dict[str, str]) -> None:
    """原子化保存索引状态到持久化文件（先写临时文件再重命名）"""
    try:
        # 先写入临时文件
        temp_path = _STATE_FILE + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False)
        # 原子重命名（覆盖原文件）
        os.replace(temp_path, _STATE_FILE)
    except Exception as e:
        logger.warning(f"保存持久化状态失败: {e}")

def _update_status(task_id: str, status: str) -> None:
    """原子化更新内存和持久化状态"""
    with _index_lock:
        _index_status[task_id] = status
        # 更新持久化状态（使用原子写入避免竞争条件）
        persistent = _load_persistent_status()
        persistent[task_id] = status
        _save_persistent_status_atomic(persistent)

# 启动时恢复持久化状态
_persistent_states = _load_persistent_status()
for task_id, status in _persistent_states.items():
    if status in ("indexing", "indexed", "failed"):
        _index_status[task_id] = status
        logger.info(f"恢复索引状态: {task_id} -> {status}")


class IndexRequest(BaseModel):
    task_id: str


class ChatMessage(BaseModel):
    role: str
    content: str


class AskRequest(BaseModel):
    task_id: str
    question: str
    history: list[ChatMessage] = []
    provider_id: str
    model_name: str


def _do_index(task_id: str):
    """后台执行索引任务"""
    try:
        _update_status(task_id, "indexing")
        store = VectorStoreManager()
        store.index_task(task_id)
        _update_status(task_id, "indexed")
        logger.info(f"索引完成: {task_id}")
    except Exception as e:
        _update_status(task_id, "failed")
        logger.error(f"索引失败: {task_id}", exc_info=True)
        raise


@router.post("/chat/index")
def index_task(data: IndexRequest, background_tasks: BackgroundTasks):
    """触发后台索引，立即返回。"""
    with _index_lock:
        # 使用锁防止并发索引同一任务
        current_status = _index_status.get(data.task_id)
        if current_status == "indexing":
            return R.success(msg="正在索引中")
        if current_status == "indexed":
            return R.success(msg="已完成索引")
        if current_status == "failed":
            # 允许重试，清除失败状态
            _update_status(data.task_id, "")

    # 如果已经索引过（持久化检查），直接返回
    store = VectorStoreManager()
    if store.is_indexed(data.task_id):
        _update_status(data.task_id, "indexed")
        return R.success(msg="已完成索引")

    _update_status(data.task_id, "indexing")
    background_tasks.add_task(_do_index, data.task_id)
    return R.success(msg="开始索引")


@router.get("/chat/status")
def chat_status(task_id: str):
    """返回索引状态：idle / indexing / indexed / failed"""
    try:
        with _index_lock:
            # 优先检查内存状态
            status = _index_status.get(task_id)
            if status:
                return R.success(data={"status": status, "indexed": status == "indexed"})

            # 内存没有记录，检查持久化
            store = VectorStoreManager()
            indexed = store.is_indexed(task_id)
            if indexed:
                _index_status[task_id] = "indexed"
            return R.success(data={"status": "indexed" if indexed else "idle", "indexed": indexed})
    except Exception as e:
        logger.error(f"查询索引状态失败: {task_id}", exc_info=True)
        return R.error(msg=f"查询索引状态失败: {str(e)}")


@router.post("/chat/ask")
def ask_question(data: AskRequest):
    """基于笔记内容的 RAG 问答。"""
    try:
        history = [{"role": m.role, "content": m.content} for m in data.history]
        result = chat_service(
            task_id=data.task_id,
            question=data.question,
            history=history,
            provider_id=data.provider_id,
            model_name=data.model_name,
        )
        return R.success(data=result)
    except ValueError as e:
        return R.error(msg=str(e))
    except Exception as e:
        logger.error(f"Chat 问答失败: {e}", exc_info=True)
        return R.error(msg=f"问答失败: {str(e)}")
