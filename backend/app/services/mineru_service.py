"""
MinerU PDF 结构化解析服务

支持两种部署模式:
    - 本地自部署 (self-hosted): http://localhost:8080
        POST /file_parse (multipart/form-data)，同步返回结果
    - 云端服务 (cloud): https://mineru.net/api/v4/extract/
        POST /api/v4/extract/task (JSON) 异步提交 → GET /api/v4/extract/task/{id} 轮询结果

API 规范参考: https://github.com/opendatalab/MinerU
"""

import asyncio
import json
import logging
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Optional

import httpx

from app.models.mineru_model import (
    MinerUMarkdownQualityReport,
    MinerUParseConfig,
    MinerUParseResult,
)
from app.services.mineru_quality_validator import MinerUQualityValidator

logger = logging.getLogger(__name__)


class MinerUServiceError(Exception):
    """MinerU 服务异常"""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class MinerUService:
    """
    MinerU PDF 解析服务

    自动检测部署模式（云端 vs 本地）并路由到对应 API。
    """

    DEFAULT_TIMEOUT = 300.0  # 5 分钟超时（大文件 PDF）
    MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
    CLOUD_API_BASE = "https://mineru.net/api/v4/extract"
    LOCAL_HEALTH_PATH = "/health"

    def __init__(self, config: MinerUParseConfig):
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self._is_cloud_mode = self._detect_cloud_mode()

    # ---------------- 部署模式检测 ----------------

    def _detect_cloud_mode(self) -> bool:
        """
        检测是否为云端部署模式。

        云端: base_url 包含 mineru.net
        本地: 其他所有地址
        """
        return "mineru.net" in self.base_url

    # ---------------- 公有方法 ----------------

    async def parse_pdf_bytes(
        self,
        pdf_bytes: bytes,
        file_name: str,
    ) -> MinerUParseResult:
        """
        将 PDF 字节流发送给 MinerU API 并解析结果。

        :param pdf_bytes: PDF 文件的字节内容
        :param file_name: 文件名（用于 API 响应中的 key 匹配）
        :return: MinerUParseResult
        :raises MinerUServiceError: API 调用失败
        """
        if self._is_cloud_mode:
            return await self._parse_cloud_mode(pdf_bytes, file_name)
        else:
            return await self._parse_local_mode(pdf_bytes, file_name)

    async def parse_pdf_bytes_with_retry(
        self,
        pdf_bytes: bytes,
        file_name: str,
        max_retries: int = 2,
    ) -> MinerUParseResult:
        """
        带重试的解析（仅本地模式重试，云端模式由调用方轮询）。

        Args:
            pdf_bytes: PDF bytes.
            file_name: File name.
            max_retries: 最大重试次数（仅对本地模式生效）。

        Returns:
            MinerUParseResult.
        """
        if self._is_cloud_mode:
            # 云端模式不重试，异步任务由调用方轮询
            return await self.parse_pdf_bytes(pdf_bytes, file_name)

        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return await self.parse_pdf_bytes(pdf_bytes, file_name)
            except httpx.TimeoutException as exc:
                last_error = exc
                logger.warning(
                    f"[MinerU] Attempt {attempt + 1}/{max_retries + 1} timed out, "
                    f"{'retrying...' if attempt < max_retries else 'exhausted retries'}"
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (400, 401, 403, 404):
                    raise MinerUServiceError(
                        f"MinerU API client error ({exc.response.status_code}): {exc.response.text[:200]}",
                        status_code=exc.response.status_code,
                    )
                last_error = exc
            except MinerUServiceError:
                raise
            except Exception as exc:
                last_error = exc

        raise MinerUServiceError(
            f"MinerU API failed after {max_retries + 1} attempts: {last_error}",
            status_code=None,
        )

    # ---------------- 本地部署模式 ----------------

    async def _parse_local_mode(
        self,
        pdf_bytes: bytes,
        file_name: str,
    ) -> MinerUParseResult:
        """
        本地部署模式: POST /file_parse (multipart/form-data)，同步返回。
        """
        logger.info(f"[MinerU][Local] Parsing PDF: {file_name}, {len(pdf_bytes):,} bytes")

        start_time = time.time()
        url = f"{self.base_url}/file_parse"

        files = {
            "files": (file_name, BytesIO(pdf_bytes), "application/pdf"),
        }

        data = {
            "parse_method": self.config.parse_method,
            "backend": self.config.backend,
            "return_md": "true" if self.config.return_md else "false",
            "return_images": "true" if self.config.return_images else "false",
            "return_content_list": "true" if self.config.return_content_list else "false",
            "formula_enable": "true" if self.config.formula_enable else "false",
            "table_enable": "true" if self.config.table_enable else "false",
        }

        headers = self._build_headers()

        async with httpx.AsyncClient(timeout=self.DEFAULT_TIMEOUT) as client:
            response = await client.post(url, files=files, data=data, headers=headers)

        elapsed_ms = int((time.time() - start_time) * 1000)
        self._handle_http_error(response)
        json_data = response.json()

        result = self._parse_response(json_data, file_name)
        result.parsing_time_ms = elapsed_ms

        logger.info(
            f"[MinerU][Local] Done: {result.page_count} pages, "
            f"Markdown {len(result.md_content):,} chars, {elapsed_ms:,}ms"
        )
        return result

    async def check_local_health(self) -> dict:
        """
        检查本地 MinerU 服务连通性（仅本地模式有效）。
        对云端模式调用此方法无意义。

        Returns:
            {"status": "UP"|"DOWN"|"UNREACHABLE", "detail": str}
        """
        url = f"{self.base_url}{self.LOCAL_HEALTH_PATH}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
                if response.is_success:
                    data = response.json()
                    return {"status": "UP", "detail": data}
                return {"status": "DOWN", "detail": f"HTTP {response.status_code}"}
        except Exception as exc:
            return {"status": "UNREACHABLE", "detail": str(exc)}

    # ---------------- 云端模式 ----------------

    async def _parse_cloud_mode(
        self,
        pdf_bytes: bytes,
        file_name: str,
    ) -> MinerUParseResult:
        """
        云端部署模式（mineru.net）。

        由于云端 API 要求提供公开 URL，本地文件需要先上传到云端存储。
        当前实现: 将 PDF bytes 作为 multipart/form-data 上传到 /api-upload 端点，
        获得 task_id 后轮询结果。
        """
        logger.info(f"[MinerU][Cloud] Parsing PDF: {file_name}, {len(pdf_bytes):,} bytes")

        start_time = time.time()

        # Step 1: 上传文件到云端，获得 task_id
        task_id = await self._submit_cloud_task(pdf_bytes, file_name)

        # Step 2: 轮询直到完成
        result = await self._poll_cloud_task(task_id, start_time)

        elapsed_ms = int((time.time() - start_time) * 1000)
        result.parsing_time_ms = elapsed_ms

        logger.info(
            f"[MinerU][Cloud] Done: {result.page_count} pages, "
            f"Markdown {len(result.md_content):,} chars, {elapsed_ms:,}ms"
        )
        return result

    async def _submit_cloud_task(self, pdf_bytes: bytes, file_name: str) -> str:
        """
        向云端 API 提交解析任务。

        云端接受两种方式:
        1. URL 方式: POST /api/v4/extract/task with {"url": "..."}  ← 优先使用
        2. 文件上传: POST /api-upload (multipart/form-data)

        优先使用 URL 方式：先上传到 OSS，拿到公开 URL 后提交给 MinerU。
        如果 OSS 未配置，则降级到文件上传方式。
        """
        # 优先走 OSS URL 方式
        try:
            from app.utils.oss_client import upload_bytes_to_oss

            oss_key = f"mineru/pdfs/{uuid.uuid4().hex}_{file_name}"
            oss_url = upload_bytes_to_oss(pdf_bytes, oss_key)
            logger.info(f"[MinerU][Cloud] OSS 上传成功: {oss_url}")

            # 使用 URL 方式提交任务
            task_url = f"{self.CLOUD_API_BASE}/task"
            headers = self._build_headers()
            headers["Content-Type"] = "application/json"
            payload = {
                "url": oss_url,
                "model_version": "vlm",
                "parse_method": self.config.parse_method,
            }

            async with httpx.AsyncClient(timeout=self.DEFAULT_TIMEOUT) as client:
                response = await client.post(task_url, headers=headers, json=payload)

            self._handle_http_error(response)
            resp_data = response.json()

            if resp_data.get("code") != 0:
                raise MinerUServiceError(
                    f"MinerU cloud task submit failed: {resp_data.get('msg', 'unknown error')}",
                    status_code=response.status_code,
                )

            task_id = resp_data.get("data", {}).get("task_id")
            if not task_id:
                raise MinerUServiceError(
                    f"No task_id in cloud submit response: {resp_data}",
                    status_code=None,
                )

            logger.info(f"[MinerU][Cloud] Task submitted via OSS URL, task_id={task_id}")
            return task_id

        except Exception as exc:
            # OSS 未配置或 OSS 操作失败（网络/凭证/超时）→ 统一降级到文件上传
            if isinstance(exc, RuntimeError) and "OSS 环境变量未配置完整" in str(exc):
                logger.warning(f"[MinerU][Cloud] OSS 未配置，降级到文件上传方式")
            else:
                logger.warning(f"[MinerU][Cloud] OSS 上传失败（{type(exc).__name__}: {exc}），降级到文件上传方式")

        # 降级：文件上传方式
        upload_url = "https://mineru.net/api-upload"
        files = {
            "file": (file_name, BytesIO(pdf_bytes), "application/pdf"),
        }
        data = {
            "model_version": "vlm",
        }
        headers = self._build_headers()

        async with httpx.AsyncClient(timeout=self.DEFAULT_TIMEOUT) as client:
            response = await client.post(upload_url, files=files, data=data, headers=headers)

        self._handle_http_error(response)
        resp_data = response.json()

        if resp_data.get("code") != 0:
            raise MinerUServiceError(
                f"MinerU cloud upload failed: {resp_data.get('msg', 'unknown error')}",
                status_code=response.status_code,
            )

        task_id = resp_data.get("data", {}).get("task_id")
        if not task_id:
            raise MinerUServiceError(
                f"No task_id in cloud upload response: {resp_data}",
                status_code=None,
            )

        logger.info(f"[MinerU][Cloud] Uploaded (file mode), task_id={task_id}")
        return task_id

    async def _poll_cloud_task(
        self,
        task_id: str,
        submit_time: float,
    ) -> MinerUParseResult:
        """
        轮询云端任务直到完成或超时。
        对瞬时错误（5xx）进行重试，避免因临时性故障导致任务失败。
        """
        poll_url = f"{self.CLOUD_API_BASE}/task/{task_id}"
        headers = self._build_headers()
        max_wait = 900.0  # 15 分钟超时（大文件处理需要更长时间）
        poll_interval = 5  # 轮询间隔（秒）
        retry_count = 3  # 瞬时错误重试次数
        retry_delay = 10  # 重试间隔（秒）

        while time.time() - submit_time < max_wait:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(poll_url, headers=headers)

                self._handle_http_error(response)
                resp_data = response.json()

                if resp_data.get("code") != 0:
                    # 只有瞬时错误（5xx）才重试，业务错误直接失败
                    error_code = resp_data.get("code", 0)
                    if error_code >= 50000 or response.status_code >= 500:
                        if retry_count > 0:
                            logger.warning(f"[MinerU][Cloud] Task {task_id} transient error, retrying in {retry_delay}s: {resp_data.get('msg')}")
                            await asyncio.sleep(retry_delay)
                            retry_count -= 1
                            retry_delay *= 2  # 指数退避
                            continue
                    raise MinerUServiceError(
                        f"MinerU cloud poll failed: {resp_data.get('msg', 'unknown error')}",
                        status_code=response.status_code,
                    )

                data = resp_data.get("data", {})
                # 兼容两种响应格式：
                # 1. 官方文档格式：state=done/running/failed + full_zip_url
                # 2. 当前服务端格式：status=SUCCESS/PARSING + result 内联
                state = data.get("state", "")
                status = data.get("status", "")
                is_done = state == "done" or status == "SUCCESS"
                is_failed = state == "failed" or status == "FAILED"
                is_pending = state in ("pending", "running", "queued", "converting", "") or status in ("PARSING", "PENDING", "QUEUED", "RUNNING", "PROCESSING", "")

                if is_done:
                    # 优先使用 full_zip_url（官方标准响应格式）
                    full_zip_url = data.get("full_zip_url", "")
                    if full_zip_url:
                        return await self._download_and_extract_zip(full_zip_url)
                    # 兼容内联 result 格式
                    result_data = data.get("result", {})
                    if result_data:
                        return self._extract_cloud_result(result_data, task_id)
                    raise MinerUServiceError(
                        f"MinerU cloud task done but no result and no full_zip_url: {resp_data}",
                        status_code=None,
                    )
                elif is_pending:
                    # 包含空字符串情况，避免任务刚提交时立即失败
                    logger.info(f"[MinerU][Cloud] Task {task_id} state={state or status or 'initializing'}, polling...")
                    await asyncio.sleep(poll_interval)
                elif is_failed:
                    err_msg = data.get("err_msg", "") or data.get("message", "unknown error")
                    raise MinerUServiceError(
                        f"MinerU cloud task failed: {err_msg}",
                        status_code=None,
                    )
                else:
                    # 记录完整响应以便调试
                    logger.warning(f"[MinerU][Cloud] Task {task_id} unexpected state/status response: {resp_data}")
                    raise MinerUServiceError(
                        f"MinerU cloud task unexpected state={state}, status={status}",
                        status_code=None,
                    )

            except httpx.TimeoutException:
                if retry_count > 0:
                    logger.warning(f"[MinerU][Cloud] Task {task_id} timeout, retrying in {retry_delay}s")
                    await asyncio.sleep(retry_delay)
                    retry_count -= 1
                    retry_delay *= 2
                    continue
                raise MinerUServiceError(
                    f"MinerU cloud task {task_id} timeout after {max_wait}s",
                    status_code=None,
                )

        raise MinerUServiceError(
            f"MinerU cloud task {task_id} timeout after {max_wait}s",
            status_code=None,
        )

    def _extract_cloud_result(self, result_data: dict, task_id: str) -> MinerUParseResult:
        """从云端任务结果中提取 MinerUParseResult。"""
        # 云端返回格式: {md_content, images, content_list, page_count, ...}
        md_content = result_data.get("md_content", "")
        images: dict[str, str] = {}
        raw_images = result_data.get("images") or {}
        for key, value in raw_images.items():
            if isinstance(value, str):
                images[key] = value if value.startswith("data:") else f"data:image/png;base64,{value}"

        raw_content_list = result_data.get("content_list")
        content_list: list[dict] = []
        if raw_content_list:
            if isinstance(raw_content_list, str):
                content_list = json.loads(raw_content_list)
            elif isinstance(raw_content_list, list):
                content_list = raw_content_list

        page_count = result_data.get("page_count", 1)

        return MinerUParseResult(
            md_content=md_content,
            images=images,
            content_list=content_list,
            page_count=page_count,
        )

    async def _download_and_extract_zip(self, zip_url: str) -> MinerUParseResult:
        """
        下载 MinerU 返回的 full_zip_url 并解析内容。

        zip 包结构（参考官方文档）:
        - full.md: Markdown 解析结果
        - main.html: 提取后正文 HTML（仅 html 文件解析）
        - **/layout.json: 中间处理结果
        - **_model.json: 模型推理结果
        - **_content_list.json: 内容列表
        - images/: 图片目录
        """
        import zipfile
        import io

        logger.info(f"[MinerU][Cloud] Downloading zip from: {zip_url}")

        async with httpx.AsyncClient(timeout=self.DEFAULT_TIMEOUT) as client:
            zip_response = await client.get(zip_url)

        if not zip_response.is_success:
            raise MinerUServiceError(
                f"Failed to download MinerU zip ({zip_response.status_code}): {zip_url}",
                status_code=zip_response.status_code,
            )

        zip_bytes = zip_response.content
        md_content = ""
        images: dict[str, str] = {}
        content_list: list[dict] = []

        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for name in zf.namelist():
                    lower_name = name.lower()
                    if lower_name.endswith("full.md"):
                        md_content = zf.read(name).decode("utf-8", errors="replace")
                    elif lower_name.endswith("_content_list.json"):
                        raw = zf.read(name).decode("utf-8", errors="replace")
                        try:
                            content_list = json.loads(raw)
                        except json.JSONDecodeError:
                            logger.warning(f"[MinerU][Cloud] Failed to parse content_list: {name}")
                    elif lower_name.endswith(".png") or lower_name.endswith(".jpg") or lower_name.endswith(".jpeg"):
                        # 收集图片为 base64
                        img_data = zf.read(name)
                        import base64
                        ext = lower_name.split(".")[-1]
                        mime = f"image/{ext}" if ext in ("png", "jpg", "jpeg", "gif", "webp") else "image/png"
                        b64 = base64.b64encode(img_data).decode("utf-8")
                        images[name] = f"data:{mime};base64,{b64}"
        except zipfile.BadZipFile:
            raise MinerUServiceError(
                f"MinerU zip file is corrupted or invalid: {zip_url}",
                status_code=None,
            )

        page_count = len(set(item.get("page_idx", 0) for item in content_list if isinstance(item, dict))) or 1

        logger.info(
            f"[MinerU][Cloud] Extracted from zip: {len(md_content):,} chars, "
            f"{len(images)} images, {len(content_list)} content items"
        )

        return MinerUParseResult(
            md_content=md_content,
            images=images,
            content_list=content_list,
            page_count=page_count,
        )

    async def check_cloud_health(self) -> dict:
        """
        检查云端 API 可用性。

        策略: 用 API key 发送一个测试请求，根据响应判断连通性。
        - 如果返回 auth 错误 (A0202) → 服务可达，token 有效
        - 如果返回网络错误 → 服务不可达
        """
        test_url = f"{self.CLOUD_API_BASE}/task"
        headers = self._build_headers()
        headers["Content-Type"] = "application/json"

        # 发送一个必定失败的"空 URL"请求来验证连通性
        # 返回 A0201 (invalid params) 而不是 A0202 (auth failed) 说明服务可达
        test_data = {"url": "", "model_version": "vlm"}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(test_url, headers=headers, json=test_data)
            resp_data = response.json()
            msg_code = resp_data.get("msgCode", "")

            if msg_code == "A0202":
                # auth failed → token 无效（但服务可达）
                return {"status": "AUTH_INVALID", "detail": "API token 无效，请检查"}
            elif msg_code == "A0201":
                # invalid params → 服务可达
                return {"status": "UP", "detail": "服务可达"}
            else:
                return {"status": "DOWN", "detail": f"msgCode={msg_code}"}
        except Exception as exc:
            return {"status": "UNREACHABLE", "detail": str(exc)}

    # ---------------- 通用工具方法 ----------------

    def _build_headers(self) -> dict:
        """构建请求头（含认证）。"""
        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    @staticmethod
    def _handle_http_error(response: httpx.Response) -> None:
        """处理 HTTP 错误。"""
        if response.is_success:
            return
        raise MinerUServiceError(
            f"MinerU API error ({response.status_code}): {response.text[:500]}",
            status_code=response.status_code,
        )

    def _parse_response(
        self,
        json_data: dict,
        file_name: str,
    ) -> MinerUParseResult:
        """
        解析 MinerU API 响应（本地模式）。
        """
        results = json_data.get("results", {})
        file_result = results.get(file_name)

        if not file_result:
            keys = list(results.keys())
            if keys:
                logger.warning(f"[MinerU] Filename mismatch '{file_name}', using key '{keys[0]}'")
                file_result = results[keys[0]]
            else:
                raise MinerUServiceError(f"MinerU 响应无结果: {str(json_data)[:200]}")

        md_content = file_result.get("md_content", "")

        raw_images: dict = file_result.get("images", {}) or {}
        images: dict[str, str] = {}
        for key, value in raw_images.items():
            if isinstance(value, str):
                images[key] = value if value.startswith("data:") else f"data:image/png;base64,{value}"

        raw_content_list = file_result.get("content_list")
        content_list: list[dict] = []
        if raw_content_list:
            if isinstance(raw_content_list, str):
                content_list = json.loads(raw_content_list)
            elif isinstance(raw_content_list, list):
                content_list = raw_content_list

        page_indices = set()
        for item in content_list:
            if isinstance(item, dict) and "page_idx" in item:
                page_indices.add(item["page_idx"])
        page_count = max(len(page_indices), 1)

        return MinerUParseResult(
            md_content=md_content,
            images=images,
            content_list=content_list,
            page_count=page_count,
        )

    # ---------------- 静态质量验证方法 ----------------

    @staticmethod
    def validate_pdf_bytes(pdf_bytes: bytes) -> tuple[bool, str]:
        return MinerUQualityValidator.validate_pdf_bytes(pdf_bytes)

    @staticmethod
    def validate_heading_hierarchy(md_content: str) -> tuple[bool, list[str]]:
        return MinerUQualityValidator.validate_heading_hierarchy(md_content)

    @staticmethod
    def validate_content_list_mapping(
        md_content: str, content_list: list[dict]
    ) -> tuple[bool, list[str]]:
        return MinerUQualityValidator.validate_content_list_mapping(md_content, content_list)

    @staticmethod
    def validate_markdown_quality(md_content: str) -> MinerUMarkdownQualityReport:
        return MinerUQualityValidator.validate_markdown_quality(md_content)


# Backward compatibility alias
MinerUError = MinerUServiceError
