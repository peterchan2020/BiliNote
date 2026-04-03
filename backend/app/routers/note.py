# app/routers/note.py
import json
import os
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.responses import JSONResponse
from pydantic import BaseModel, validator, field_validator
from dataclasses import asdict

from app.db.video_task_dao import get_task_by_video
from app.enmus.exception import NoteErrorEnum
from app.enmus.note_enums import DownloadQuality
from app.exceptions.note import NoteError
from app.services.note import NoteGenerator, logger
from app.services.task_serial_executor import task_serial_executor
from app.utils.response import ResponseWrapper as R
from app.utils.url_parser import extract_video_id
from app.validators.video_url_validator import is_supported_video_url
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
import httpx
from app.enmus.task_status_enums import TaskStatus

# from app.services.downloader import download_raw_audio
# from app.services.whisperer import transcribe_audio

router = APIRouter()


class RecordRequest(BaseModel):
    video_id: str
    platform: str


class VideoRequest(BaseModel):
    video_url: str
    platform: str
    quality: DownloadQuality
    screenshot: Optional[bool] = False
    link: Optional[bool] = False
    model_name: str
    provider_id: str
    task_id: Optional[str] = None
    format: Optional[list] = []
    style: str = None
    extras: Optional[str]=None
    video_understanding: Optional[bool] = False
    video_interval: Optional[int] = 0
    grid_size: Optional[list] = []

    @field_validator("video_url")
    def validate_supported_url(cls, v):
        url = str(v)
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https"):
            # 是网络链接，继续用原有平台校验
            if not is_supported_video_url(url):
                raise NoteError(code=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.code,
                                message=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.message)

        return v


NOTE_OUTPUT_DIR = os.getenv("NOTE_OUTPUT_DIR", "note_results")
UPLOAD_DIR = "uploads"


def save_note_to_file(task_id: str, note):
    os.makedirs(NOTE_OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(NOTE_OUTPUT_DIR, f"{task_id}.json"), "w", encoding="utf-8") as f:
        json.dump(asdict(note), f, ensure_ascii=False, indent=2)


def run_note_task(task_id: str, video_url: str, platform: str, quality: DownloadQuality,
                  link: bool = False, screenshot: bool = False, model_name: str = None, provider_id: str = None,
                  _format: list = None, style: str = None, extras: str = None, video_understanding: bool = False,
                  video_interval: int = 0, grid_size: Optional[list] = None
                  ):
    if grid_size is None:
        grid_size = []

    if not model_name or not provider_id:
        logger.error(f"任务缺少模型或提供者配置 (task_id={task_id})")
        NoteGenerator()._update_status(task_id, TaskStatus.FAILED, message="请选择模型和提供者")
        return

    try:
        def _execute_note_task():
            return NoteGenerator().generate(
                video_url=video_url,
                platform=platform,
                quality=quality,
                task_id=task_id,
                model_name=model_name,
                provider_id=provider_id,
                link=link,
                _format=_format,
                style=style,
                extras=extras,
                screenshot=screenshot,
                video_understanding=video_understanding,
                video_interval=video_interval,
                grid_size=grid_size,
            )

        logger.info(f"任务进入执行队列 (task_id={task_id})")
        note = task_serial_executor.run(_execute_note_task)
        logger.info(f"Note generated: {task_id}")
        if not note or not note.markdown:
            logger.warning(f"任务 {task_id} 执行失败，跳过保存")
            return
        save_note_to_file(task_id, note)

        # TODO: 重新启用向量索引（当前禁用，因 GPU 环境下 VectorStoreManager 初始化会崩溃）
        # try:
        #     from app.services.vector_store import VectorStoreManager
        #     VectorStoreManager().index_task(task_id)
        # except Exception as e:
        #     logger.warning(f"向量索引失败（不影响笔记）: {e}")

    except Exception as e:
        logger.error(f"任务执行异常 (task_id={task_id}): {e}", exc_info=True)
        NoteGenerator()._update_status(task_id, TaskStatus.FAILED, message=str(e))


@router.post('/delete_task')
def delete_task(data: RecordRequest):
    try:
        # TODO: 待持久化完成
        # NoteGenerator().delete_note(video_id=data.video_id, platform=data.platform)
        return R.success(msg='删除成功')
    except Exception as e:
        return R.error(msg=e)


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # Sanitize filename: extract basename, replace problematic chars with underscores
    import re
    filename = os.path.basename(file.filename)
    if not filename or filename.startswith('.') or '/' in filename or '\\' in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    # Replace spaces, parentheses, Chinese brackets with underscores
    filename = re.sub(r'[ （）\(\)]+', '_', filename)
    # Validate: only alphanumeric, dots, underscores, hyphens allowed
    if len(filename) > 255 or not all(c.isalnum() or c in '._-' for c in filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_location = os.path.join(UPLOAD_DIR, filename)
    # Defensive: ensure resolved path stays within UPLOAD_DIR
    real_path = os.path.realpath(file_location)
    if not real_path.startswith(os.path.realpath(UPLOAD_DIR)):
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Validate file size before reading (limit to 100MB)
    content = await file.read()
    if len(content) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 100MB)")

    with open(file_location, "wb+") as f:
        f.write(content)

    return R.success({"url": f"/uploads/{filename}"})


@router.post("/generate_note")
def generate_note(data: VideoRequest, background_tasks: BackgroundTasks):
    try:
        # local_doc is handled by MinerU polling, not this endpoint
        if data.platform == 'local_doc':
            raise HTTPException(status_code=400, detail="local_doc platform is not supported by this endpoint")

        video_id = extract_video_id(data.video_url, data.platform)
        # if not video_id:
        #     raise HTTPException(status_code=400, detail="无法提取视频 ID")
        # existing = get_task_by_video(video_id, data.platform)
        # if existing:
        #     return R.error(
        #         msg='笔记已生成，请勿重复发起',
        #
        #     )
        if data.task_id:
            # 如果传了task_id，说明是重试！
            task_id = data.task_id
            logger.info(f"重试模式，复用已有 task_id={task_id}")
        else:
            # 正常新建任务
            task_id = str(uuid.uuid4())

        # 统一先写入 PENDING，表示已进入队列等待串行执行
        NoteGenerator()._update_status(task_id, TaskStatus.PENDING)

        background_tasks.add_task(run_note_task, task_id, data.video_url, data.platform, data.quality, data.link,
                                  data.screenshot, data.model_name, data.provider_id, data.format, data.style,
                                  data.extras, data.video_understanding, data.video_interval, data.grid_size)
        return R.success({"task_id": task_id})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/task_status/{task_id}")
def get_task_status(task_id: str):
    status_path = os.path.join(NOTE_OUTPUT_DIR, f"{task_id}.status.json")
    result_path = os.path.join(NOTE_OUTPUT_DIR, f"{task_id}.json")
    knowledge_graph_path = os.path.join(NOTE_OUTPUT_DIR, f"{task_id}_knowledge_graph.md")

    # 优先读状态文件
    if os.path.exists(status_path):
        with open(status_path, "r", encoding="utf-8") as f:
            status_content = json.load(f)

        status = status_content.get("status")
        message = status_content.get("message", "")

        if status == TaskStatus.SUCCESS.value:
            # 成功状态的话，继续读取最终笔记内容
            if os.path.exists(result_path):
                with open(result_path, "r", encoding="utf-8") as rf:
                    result_content = json.load(rf)
                
                # 读取知识图谱（如果存在）
                knowledge_graph = None
                if os.path.exists(knowledge_graph_path):
                    with open(knowledge_graph_path, "r", encoding="utf-8") as kgf:
                        knowledge_graph = kgf.read()
                
                return R.success({
                    "status": status,
                    "result": result_content,
                    "knowledge_graph": knowledge_graph,
                    "message": message,
                    "task_id": task_id
                })
            else:
                # 理论上不会出现，保险处理
                return R.success({
                    "status": TaskStatus.PENDING.value,
                    "message": "任务完成，但结果文件未找到",
                    "task_id": task_id
                })

        if status == TaskStatus.FAILED.value:
            return JSONResponse(
                status_code=500,
                content={
                    "code": 500,
                    "msg": message or "任务失败",
                    "data": {"task_id": task_id, "status": TaskStatus.FAILED.value, "message": message or "任务失败"}
                }
            )

        # 处理中状态
        return R.success({
            "status": status,
            "message": message,
            "task_id": task_id
        })

    # 没有状态文件，但有结果
    if os.path.exists(result_path):
        with open(result_path, "r", encoding="utf-8") as f:
            result_content = json.load(f)
        
        # 读取知识图谱（如果存在）
        knowledge_graph = None
        if os.path.exists(knowledge_graph_path):
            with open(knowledge_graph_path, "r", encoding="utf-8") as kgf:
                knowledge_graph = kgf.read()
        
        return R.success({
            "status": TaskStatus.SUCCESS.value,
            "result": result_content,
            "knowledge_graph": knowledge_graph,
            "task_id": task_id
        })

    # 什么都没有，默认PENDING
    return R.success({
        "status": TaskStatus.PENDING.value,
        "message": "任务排队中",
        "task_id": task_id
    })


@router.get("/image_proxy")
async def image_proxy(request: Request, url: str):
    # Validate URL to prevent SSRF attacks
    parsed_url = urlparse(url)

    # Only allow http/https schemes
    if parsed_url.scheme not in ('http', 'https'):
        raise HTTPException(status_code=400, detail="Invalid URL scheme")

    # Enforce domain allowlist (no pass-through)
    hostname = parsed_url.hostname or ""
    allowed_domains = ('bilibili.com', 'bilivideo.com', 'biliplus.com',
                       'hdslb.com', 'cncdn.io', 'cloudflare.com', 'akamaized.net',
                       'douyin.com', 'douyinvod.com', 'kuaishou.com')
    if not any(hostname.endswith(domain) or hostname == domain for domain in allowed_domains):
        raise HTTPException(status_code=403, detail="Domain not allowed")

    # Resolve hostname to IP and verify it's not private
    import socket
    try:
        ip = socket.gethostbyname(hostname)
    except socket.gaierror:
        raise HTTPException(status_code=400, detail="Invalid hostname")
    # Block private IP ranges after DNS resolution
    private_ip_patterns = (
        ip.startswith('10.') or
        ip.startswith('192.168.') or
        ip.startswith('172.16.') or ip.startswith('172.17.') or
        ip.startswith('172.18.') or ip.startswith('172.19.') or
        ip.startswith('172.20.') or ip.startswith('172.21.') or
        ip.startswith('172.22.') or ip.startswith('172.23.') or
        ip.startswith('172.24.') or ip.startswith('172.25.') or
        ip.startswith('172.26.') or ip.startswith('172.27.') or
        ip.startswith('172.28.') or ip.startswith('172.29.') or
        ip.startswith('172.30.') or ip.startswith('172.31.') or
        ip in ('127.0.0.1', '0.0.0.0') or
        ip.startswith('169.254.') or  # link-local
        ':' in ip  # IPv6 addresses contain colons
    )
    if private_ip_patterns:
        raise HTTPException(status_code=403, detail="Access to private IPs not allowed")

    headers = {
        "Referer": "https://www.bilibili.com/",
        "User-Agent": request.headers.get("User-Agent", ""),
    }

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)

            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail="图片获取失败")

            content_type = resp.headers.get("Content-Type", "image/jpeg")
            return StreamingResponse(
                resp.aiter_bytes(),
                media_type=content_type,
                headers={
                    "Cache-Control": "public, max-age=86400",
                    "Content-Type": content_type,
                }
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
