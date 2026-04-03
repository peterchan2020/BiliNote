"""
Chat 问答服务 — 非 Tool Calling RAG 版本。

针对国产模型（通义千问、文心、MiniMax、智谱等）通过 OpenAI 兼容接口
不一定支持 function calling 的问题，降级为单次检索 + 一次生成。

核心流程：
1. 向量检索获取相关上下文（扩大配额以弥补无法主动查询的缺陷）
2. 拼接上下文到 prompt
3. 一次 LLM 调用生成回答
4. 返回答案 + 来源信息
"""

from app.gpt.gpt_factory import GPTFactory
from app.models.model_config import ModelConfig
from app.services.provider import ProviderService
from app.services.vector_store import get_vector_store
from app.utils.logger import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = """你是一个视频笔记问答助手。请基于以下参考资料回答问题。

参考资料：
{context}

回答要求：
- 严格基于参考资料回答，不要编造
- 如果参考资料不足以回答问题，请明确告知用户"笔记中没有相关内容"
- 回答中可适当引用原文片段来增强可信度（用引号标注）
- 请用中文回答，保持简洁准确
- 如果涉及视频中的时间点，可以标注大致时间位置"""


def _build_context(chunks: list[dict]) -> tuple[str, list[dict]]:
    """将检索到的片段拼接为上下文文本，同时构建来源列表。"""
    if not chunks:
        return "（未检索到相关笔记内容）", []

    parts = []
    sources = []
    for chunk in chunks:
        meta = chunk.get("metadata", {})
        source_type = meta.get("source_type", "unknown")
        if source_type == "meta":
            label = "[视频信息]"
        elif source_type == "markdown":
            label = f"[笔记 - {meta.get('section_title', '未知章节')}]"
        else:
            start = meta.get("start_time", 0)
            end = meta.get("end_time", 0)
            label = f"[转录 - {start:.0f}s~{end:.0f}s]"

        text = chunk.get("text", "")
        parts.append(f"{label}\n{text}")

        source = {
            "text": text[:200],
            "source_type": source_type,
        }
        if meta.get("section_title"):
            source["section_title"] = meta["section_title"]
        if meta.get("start_time") is not None:
            source["start_time"] = meta["start_time"]
        if meta.get("end_time") is not None:
            source["end_time"] = meta["end_time"]
        sources.append(source)

    return "\n\n---\n\n".join(parts), sources


def chat(
    task_id: str,
    question: str,
    history: list[dict],
    provider_id: str,
    model_name: str,
) -> dict:
    """
    非 Tool Calling RAG 问答。
    1. 向量检索获取上下文
    2. 拼接上下文到 system prompt
    3. 一次 LLM 调用生成回答
    4. 返回答案 + 来源信息
    """
    # 1. 向量检索
    vector_store = get_vector_store()
    chunks = vector_store.query(task_id, question)

    if not chunks:
        return {
            "answer": "该笔记尚未完成索引或索引数据不完整，请重新生成笔记。",
            "sources": [],
        }

    context, sources = _build_context(chunks)

    # 2. 构建消息
    system_msg = SYSTEM_PROMPT.format(context=context)
    messages = [{"role": "system", "content": system_msg}]

    for msg in history[-20:]:
        messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": question})

    # 3. 获取 LLM client
    provider = ProviderService.get_provider_by_id(provider_id)
    if not provider:
        raise ValueError(f"未找到模型供应商: {provider_id}")

    config = ModelConfig(
        api_key=provider["api_key"],
        base_url=provider["base_url"],
        model_name=model_name,
        provider=provider["type"],
        name=provider["name"],
    )
    gpt = GPTFactory.from_config(config)

    logger.info(f"Chat: task_id={task_id}, model={model_name}, chunks={len(chunks)}")

    # 4. 一次 LLM 调用（不使用 tools 参数）
    response = gpt.client.chat.completions.create(
        model=gpt.model,
        messages=messages,
        temperature=0.7,
    )

    answer = response.choices[0].message.content or ""
    logger.info(f"Chat 回答完成，长度: {len(answer)} 字符")

    return {"answer": answer, "sources": sources}
