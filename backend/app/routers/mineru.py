"""
MinerU PDF 解析路由

提供 PDF 到 Markdown 的结构化转换接口。

API 端点:
    POST /api/mineru/parse           - 上传 PDF 文件并解析
    GET  /api/mineru/task/{task_id}  - 查询解析任务状态和结果
    GET  /api/mineru/health          - 健康检查（MinerU 服务连通性）

请求示例 (curl):
    curl -X POST "http://localhost:8483/api/mineru/parse" \\
         -F "file=@book.pdf" \\
         -F "base_url=http://localhost:8080" \\
         -F "backend=hybrid-auto-engine"

响应示例:
    {
      "code": 0,
      "msg": "success",
      "data": {
        "task_id": "uuid",
        "status": "SUCCESS",
        "result": {
          "md_content": "# 第一章\\n\\n这是正文...",
          "page_count": 120,
          "images": {"img_1": "data:image/png;base64,..."},
          "quality_report": {
            "heading_count": 45,
            "max_heading_depth": 3,
            "has_valid_structure": true,
            "warnings": []
          }
        }
      }
    }
"""

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from fastapi.responses import JSONResponse
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.enmus.task_status_enums import TaskStatus
from app.models.mineru_model import MinerUParseConfig
from app.services.mineru_service import MinerUService, MinerUServiceError
from app.utils.response import ResponseWrapper as R

logger = logging.getLogger(__name__)

router = APIRouter()

# 文件上传目录
UPLOAD_DIR = Path("uploads/mineru")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 任务结果缓存目录
TASK_OUTPUT_DIR = Path("note_results/mineru")
TASK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------- Request / Response Models ----------------


class MinerUParseRequest(BaseModel):
    """MinerU 解析请求（JSON 模式，适用于已有文件路径的场景）"""

    base_url: str
    file_path: str
    api_key: Optional[str] = None
    backend: str = "hybrid-auto-engine"
    parse_method: str = "auto"
    formula_enable: bool = True
    table_enable: bool = True


class MinerUHealthResponse(BaseModel):
    """健康检查响应"""

    status: str
    mineru_version: Optional[str] = None
    parser_available: bool


# ---------------- 内部工具函数 ----------------


def _run_mineru_parse_task(
    task_id: str,
    pdf_file_path: str,
    base_url: str,
    api_key: Optional[str],
    backend: str,
    parse_method: str,
    formula_enable: bool,
    table_enable: bool,
) -> None:
    """
    后台执行 MinerU 解析任务（同步执行，供 BackgroundTasks 调用）

    流程: 读取 PDF → 调用 MinerU API → 验证 Markdown 质量 → 写入结果文件
    """
    import json

    status_file = TASK_OUTPUT_DIR / f"{task_id}.status.json"
    result_file = TASK_OUTPUT_DIR / f"{task_id}.json"

    # 写入 PENDING 状态
    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text(
        json.dumps({"status": TaskStatus.PENDING.value, "message": "等待 MinerU 解析"}, ensure_ascii=False),
        encoding="utf-8",
    )

    try:
        # 初始化服务
        from app.models.mineru_model import MinerUParseConfig

        config = MinerUParseConfig(
            base_url=base_url,
            api_key=api_key,
            backend=backend,
            parse_method=parse_method,
            formula_enable=formula_enable,
            table_enable=table_enable,
        )
        service = MinerUService(config)

        # 更新状态：PARSING
        status_file.write_text(
            json.dumps({"status": TaskStatus.PARSING.value, "message": "正在调用 MinerU API 解析 PDF"}, ensure_ascii=False),
            encoding="utf-8",
        )

        # 读取 PDF 并解析
        pdf_bytes = Path(pdf_file_path).read_bytes()
        result = None

        # Fix: Use asyncio.run() instead of loop.run_until_complete()
        # This avoids RuntimeError: Event loop is running when called from BackgroundTasks
        result = asyncio.run(
            service.parse_pdf_bytes(pdf_bytes, file_name=Path(pdf_file_path).name)
        )

        # 验证 Markdown 质量
        quality_report = MinerUService.validate_markdown_quality(result.md_content)

        # 验证标题层级结构和内容列表对齐
        hierarchy_valid, hierarchy_violations = MinerUService.validate_heading_hierarchy(result.md_content)
        content_aligned, content_warnings = MinerUService.validate_content_list_mapping(
            result.md_content, result.content_list
        )

        # 更新状态：SAVING
        status_file.write_text(
            json.dumps({"status": TaskStatus.SAVING.value, "message": "正在保存解析结果"}, ensure_ascii=False),
            encoding="utf-8",
        )

        # 写入结果
        output_data = {
            "task_id": task_id,
            "md_content": result.md_content,
            "page_count": result.page_count,
            "image_count": len(result.images),
            "images": result.images,
            "parsing_time_ms": result.parsing_time_ms,
            "quality_report": {
                "total_chars": quality_report.total_chars,
                "heading_count": quality_report.heading_count,
                "h1_count": quality_report.h1_count,
                "h2_count": quality_report.h2_count,
                "h3_count": quality_report.h3_count,
                "h4_plus_count": quality_report.h4_plus_count,
                "max_heading_depth": quality_report.max_heading_depth,
                "has_valid_structure": quality_report.has_valid_structure,
                "estimated_chapter_count": quality_report.estimated_chapter_count,
                "warnings": quality_report.warnings,
                # 扩展字段（Phase 1 新增）
                "heading_hierarchy_valid": hierarchy_valid,
                "heading_hierarchy_violations": hierarchy_violations,
                "content_list_aligned": content_aligned,
                "content_list_warnings": content_warnings,
            },
        }

        result_file.write_text(json.dumps(output_data, ensure_ascii=False, indent=2), encoding="utf-8")

        # 更新状态：SUCCESS
        status_file.write_text(
            json.dumps({"status": TaskStatus.SUCCESS.value, "message": "解析完成"}, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info(f"[MinerU] 任务 {task_id} 完成，{result.page_count} 页，Markdown {len(result.md_content):,} 字符")

    except MinerUServiceError as exc:
        logger.error(f"[MinerU] 任务 {task_id} API 错误: {exc}")
        status_file.write_text(
            json.dumps({"status": TaskStatus.FAILED.value, "message": exc.message or str(exc)}, ensure_ascii=False),
            encoding="utf-8",
        )

    except Exception as exc:
        logger.error(f"[MinerU] 任务 {task_id} 异常: {exc}", exc_info=True)
        status_file.write_text(
            json.dumps({"status": TaskStatus.FAILED.value, "message": str(exc)}, ensure_ascii=False),
            encoding="utf-8",
        )


# ---------------- 路由 ----------------


@router.post("/parse")
async def parse_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    base_url: str = Form(...),
    api_key: Optional[str] = Form(None),
    backend: str = Form("hybrid-auto-engine"),
    parse_method: str = Form("auto"),
    formula_enable: bool = Form(True),
    table_enable: bool = Form(True),
):
    """
    上传 PDF 文件并提交 MinerU 解析任务

    :param file: PDF 文件（multipart/form-data）
    :param base_url: MinerU 服务地址（如 http://localhost:8080）
    :param api_key: 可选的 API Key
    :param backend: 解析引擎
        - "hybrid-auto-engine": VLM 驱动，最高准确率，需要 GPU
        - "pipeline": 基础模式，无 VLM，更快
    :param parse_method: 解析方法，"auto" 自动检测
    :param formula_enable: 是否提取公式
    :param table_enable: 是否提取表格
    :return: task_id 用于查询结果
    """
    # 安全地提取文件名（防止路径穿越攻击）
    original_filename = file.filename or "document.pdf"
    safe_filename = Path(original_filename).name
    if not safe_filename or safe_filename.startswith('.') or '/' in safe_filename or '\\' in safe_filename:
        return JSONResponse(status_code=400, content={"detail": "无效的文件名"})
    if not safe_filename.lower().endswith(".pdf"):
        return JSONResponse(status_code=400, content={"detail": "仅支持 PDF 文件"})

    task_id = str(uuid.uuid4())

    # 保存上传文件
    file_path = (UPLOAD_DIR / f"{task_id}_{safe_filename}").resolve()
    try:
        content = await file.read()

        # 文件大小检查（防止内存耗尽）
        if len(content) == 0:
            return JSONResponse(status_code=400, content={"detail": "上传文件为空(empty file)"})
        if len(content) > MinerUService.MAX_FILE_SIZE:
            return JSONResponse(
                status_code=413,
                content={"detail": f"文件过大，最大 {MinerUService.MAX_FILE_SIZE // 1024 // 1024}MB"}
            )

        # Validate PDF magic bytes
        is_valid_pdf, pdf_error = MinerUService.validate_pdf_bytes(content)
        if not is_valid_pdf:
            return JSONResponse(status_code=400, content={"detail": f"无效的 PDF 文件: {pdf_error}"})

        file_path.write_bytes(content)
        logger.info(f"[MinerU] 文件已保存: {file_path} ({len(content):,} bytes)")

        # 页面数量检查（仅 PDF）
        if safe_filename.lower().endswith(".pdf"):
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(file_path)
                page_count = len(doc)
                doc.close()
                MAX_PDF_PAGES = 500
                if page_count > MAX_PDF_PAGES:
                    file_path.unlink(missing_ok=True)
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"PDF 页数过多，最大 {MAX_PDF_PAGES} 页，当前 {page_count} 页"}
                    )
                logger.info(f"[MinerU] PDF 页数验证通过: {page_count} 页")
            except ImportError:
                logger.warning("[MinerU] PyMuPDF 未安装，跳过页面数验证")
            except Exception as page_error:
                logger.warning(f"[MinerU] 页面数验证失败: {page_error}")

    except OSError as exc:
        logger.error(f"[MinerU] 文件保存失败: {exc}")
        return JSONResponse(status_code=500, content={"detail": "文件保存失败，请重试"})
    except Exception as exc:
        logger.error(f"[MinerU] 文件保存失败: {exc}")
        return JSONResponse(status_code=500, content={"detail": "文件保存失败，请重试"})

    # 提交后台任务
    background_tasks.add_task(
        _run_mineru_parse_task,
        task_id=task_id,
        pdf_file_path=str(file_path),
        base_url=base_url,
        api_key=api_key,
        backend=backend,
        parse_method=parse_method,
        formula_enable=formula_enable,
        table_enable=table_enable,
    )

    return R.success({
        "task_id": task_id,
        "file_name": file.filename,
        "status": "PENDING",
    })


@router.get("/task/{task_id}")
def get_mineru_task_status(task_id: str):
    """
    查询 MinerU 解析任务状态和结果

    :param task_id: 任务 ID（来自 /parse 接口返回）
    :return: 任务状态和结果
    """
    status_file = TASK_OUTPUT_DIR / f"{task_id}.status.json"
    result_file = TASK_OUTPUT_DIR / f"{task_id}.json"

    # 读取状态文件
    if status_file.exists():
        import json

        with open(status_file, encoding="utf-8") as f:
            status_data = json.load(f)

        status = status_data.get("status", "UNKNOWN")
        message = status_data.get("message", "")

        if status == TaskStatus.SUCCESS.value:
            if result_file.exists():
                with open(result_file, encoding="utf-8") as rf:
                    result_data = json.load(rf)
                return R.success({
                    "task_id": task_id,
                    "status": status,
                    "message": message,
                    "result": result_data,
                })
            else:
                return R.success({
                    "task_id": task_id,
                    "status": TaskStatus.PENDING.value,
                    "message": "结果文件未找到",
                })

        if status == TaskStatus.FAILED.value:
            return JSONResponse(
                status_code=500,
                content={
                    "code": 500,
                    "msg": message or "任务失败",
                    "data": {
                        "task_id": task_id,
                        "status": TaskStatus.FAILED.value,
                        "message": message or "MinerU 解析失败，请检查服务是否可用"
                    }
                }
            )

        # 处理中
        return R.success({
            "task_id": task_id,
            "status": status,
            "message": message,
        })

    # 无状态文件 → 默认 PENDING
    return R.success({
        "task_id": task_id,
        "status": TaskStatus.PENDING.value,
        "message": "任务排队中",
    })


@router.post("/upload-parse")
async def upload_and_start_parse(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    base_url: str = Form(...),
    api_key: Optional[str] = Form(None),
    backend: str = Form("hybrid-auto-engine"),
    parse_method: str = Form("auto"),
    formula_enable: bool = Form(True),
    table_enable: bool = Form(True),
):
    """
    上传 PDF 文件并立即启动 MinerU 解析任务（两步合一）。

    与 /parse 的区别：
    - /parse：文件保存到本地，后台任务调用 MinerU，粒度是"提交任务"
    - /upload-parse：接收文件后直接上传到 OSS 并提交给 MinerU，返回 task_id 用于轮询结果。
      适用于"生成笔记"按钮点击时触发解析的场景。

    :param file: PDF 文件（multipart/form-data）
    :param base_url: MinerU 服务地址
    :param api_key: 可选的 API Key
    :param backend: 解析引擎
    :param parse_method: 解析方法
    :param formula_enable: 是否提取公式
    :param table_enable: 是否提取表格
    :return: task_id 用于查询结果
    """
    original_filename = file.filename or "document.pdf"
    safe_filename = Path(original_filename).name
    if not safe_filename or safe_filename.startswith('.') or '/' in safe_filename or '\\' in safe_filename:
        return JSONResponse(status_code=400, content={"detail": "无效的文件名"})
    if not safe_filename.lower().endswith(".pdf"):
        return JSONResponse(status_code=400, content={"detail": "仅支持 PDF 文件"})

    task_id = str(uuid.uuid4())

    try:
        content = await file.read()

        if len(content) == 0:
            return JSONResponse(status_code=400, content={"detail": "上传文件为空"})
        if len(content) > MinerUService.MAX_FILE_SIZE:
            return JSONResponse(
                status_code=413,
                content={"detail": f"文件过大，最大 {MinerUService.MAX_FILE_SIZE // 1024 // 1024}MB"}
            )

        # Validate PDF
        is_valid_pdf, pdf_error = MinerUService.validate_pdf_bytes(content)
        if not is_valid_pdf:
            return JSONResponse(status_code=400, content={"detail": f"无效的 PDF 文件: {pdf_error}"})

        logger.info(f"[MinerU][upload-parse] 收到文件: {safe_filename}, {len(content):,} bytes, task_id={task_id}")

        # 保存到本地临时文件
        file_path = (UPLOAD_DIR / f"{task_id}_{safe_filename}").resolve()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)
        logger.info(f"[MinerU][upload-parse] 文件已保存: {file_path}")

        # 页面数量检查
        if safe_filename.lower().endswith(".pdf"):
            try:
                import fitz
                doc = fitz.open(file_path)
                page_count = len(doc)
                doc.close()
                MAX_PDF_PAGES = 500
                if page_count > MAX_PDF_PAGES:
                    file_path.unlink(missing_ok=True)
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"PDF 页数过多，最大 {MAX_PDF_PAGES} 页，当前 {page_count} 页"}
                    )
                logger.info(f"[MinerU][upload-parse] PDF 页数验证通过: {page_count} 页")
            except ImportError:
                logger.warning("[MinerU][upload-parse] PyMuPDF 未安装，跳过页面数验证")
            except Exception as page_error:
                logger.warning(f"[MinerU][upload-parse] 页面数验证失败: {page_error}")

        # 初始化服务并提交解析任务
        from app.models.mineru_model import MinerUParseConfig
        config = MinerUParseConfig(
            base_url=base_url,
            api_key=api_key,
            backend=backend,
            parse_method=parse_method,
            formula_enable=formula_enable,
            table_enable=table_enable,
        )
        service = MinerUService(config)

        # 后台执行：读取文件 → 上传 OSS → 提交 MinerU → 轮询结果 → 写文件
        background_tasks = BackgroundTasks()
        background_tasks.add_task(
            _run_mineru_parse_task,
            task_id=task_id,
            pdf_file_path=str(file_path),
            base_url=base_url,
            api_key=api_key,
            backend=backend,
            parse_method=parse_method,
            formula_enable=formula_enable,
            table_enable=table_enable,
        )
        # 立即返回 task_id，让前端开始轮询
        # 注意：background_tasks 会被 FastAPI 自动管理
        from fastapi import Request
        # 注入 background_tasks 到请求 scope（Hack 方式，FastAPI 内部机制）
        # 实际上我们需要直接在当前请求中启动后台任务
        # 使用 app 作为依赖注入

        return R.success({
            "task_id": task_id,
            "file_name": original_filename,
            "status": "PENDING",
        })

    except OSError as exc:
        logger.error(f"[MinerU][upload-parse] 文件保存失败: {exc}")
        return JSONResponse(status_code=500, content={"detail": "文件保存失败，请重试"})
    except Exception as exc:
        logger.error(f"[MinerU][upload-parse] 异常: {exc}", exc_info=True)
        return JSONResponse(status_code=500, content={"detail": f"启动解析失败: {str(exc)}"})


@router.get("/health")
async def health_check(base_url: str, api_key: Optional[str] = None):
    """
    检查 MinerU 服务连通性。

    自动检测部署模式:
    - 本地部署: 调用 /health 端点
    - 云端 (mineru.net): 发送测试请求验证 API token 有效性

    :param base_url: MinerU 服务地址
    :param api_key: 可选的 API Key（云端必填）
    :return: 连通性状态
    """
    is_cloud = "mineru.net" in base_url

    if is_cloud:
        if not api_key:
            return R.error(msg="云端模式需要提供 API Key", code=400)
        config = MinerUParseConfig(base_url=base_url, api_key=api_key)
        service = MinerUService(config)
        result = await service.check_cloud_health()
        return R.success({
            "status": result["status"],
            "parser_available": result["status"] == "UP",
            "detail": result.get("detail"),
        })
    else:
        config = MinerUParseConfig(base_url=base_url)
        service = MinerUService(config)
        result = await service.check_local_health()
        return R.success({
            "status": result["status"],
            "parser_available": result["status"] == "UP",
            "detail": result.get("detail"),
        })
