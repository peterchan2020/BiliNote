import json
import os
import time
import uuid
import tempfile
import subprocess
from pathlib import Path
from http import HTTPStatus

import dashscope
from dashscope.audio.asr import Transcription

from app.decorators.timeit import timeit
from app.models.transcriber_model import TranscriptSegment, TranscriptResult
from app.services.transcriber_config_manager import TranscriberConfigManager
from app.transcriber.base import Transcriber
from app.utils.logger import get_logger
from app.utils.oss_client import upload_file_to_oss, delete_oss_file
from events import transcription_finished

logger = get_logger(__name__)

# Aliyun ASR API base URL (Beijing region)
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"

# Polling config
POLL_TIMEOUT = 600  # 10 minutes max
POLL_INTERVAL = 5   # seconds between polls


class AliyunTranscriber(Transcriber):
    """阿里云 Fun-ASR 语音识别转写器"""

    def __init__(self) -> None:
        self._config_initialized = False

    def _ensure_config(self) -> str:
        """获取并验证 API Key"""
        config_manager = TranscriberConfigManager()
        aliyun_config = config_manager.get_aliyun_config()
        api_key = aliyun_config.get("aliyun_api_key", "")
        if not api_key:
            raise ValueError(
                "阿里云 API Key 未配置，请在「音频转写配置」页面配置 Aliyun ASR 的 API Key。"
            )
        return api_key

    def _convert_to_wav(self, file_path: str) -> str:
        """使用 FFmpeg 将音频转换为 16kHz 单声道 WAV 格式"""
        wav_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        wav_path = wav_file.name
        wav_file.close()

        cmd = [
            "ffmpeg", "-y", "-i", file_path,
            "-f", "wav", "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le",
            wav_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=300)
            if result.returncode != 0:
                logger.warning(f"FFmpeg 转换失败，尝试直接使用原文件: {result.stderr[:200]}")
                os.unlink(wav_path)
                return file_path
            logger.info(f"FFmpeg 转换完成: {os.path.basename(file_path)} -> {wav_path}")
            return wav_path
        except Exception as e:
            logger.warning(f"FFmpeg 转换异常，使用原文件: {e}")
            try:
                os.unlink(wav_path)
            except Exception:
                pass
            return file_path

    def _upload_audio(self, file_path: str) -> tuple[str, str]:
        """上传音频文件到阿里云 OSS，返回 (public_url, oss_key)"""
        wav_path = self._convert_to_wav(file_path)
        oss_key = ""

        try:
            public_url, oss_key = upload_file_to_oss(
                Path(wav_path),
                sub_dir="bilinote/asr",
                content_type="audio/wav",
            )
            logger.info(f"音频已上传至 OSS: key={oss_key}, url={public_url}")
            return public_url, oss_key
        finally:
            if wav_path != file_path:
                try:
                    os.unlink(wav_path)
                    logger.info(f"已清理临时WAV文件: {wav_path}")
                except Exception:
                    pass

    def _fetch_transcription_json(self, transcription_url: str) -> dict:
        """从 transcription_url 下载转写结果 JSON"""
        import urllib.request

        try:
            with urllib.request.urlopen(transcription_url, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except Exception as e:
            logger.error(f"下载 transcription_url 失败: {e}")
            raise

    def _poll_task(self, task_id: str, api_key: str) -> dict:
        """轮询等待任务完成，返回最终 TranscriptionResponse"""
        start_time = time.time()
        last_status = None

        while time.time() - start_time < POLL_TIMEOUT:
            resp = Transcription.fetch(task=task_id, api_key=api_key)
            task_status = resp.output.task_status
            logger.info(f"Aliyun 任务状态: task_id={task_id}, status={task_status}")

            if task_status == "SUCCEEDED":
                return resp
            if task_status == "FAILED":
                error_msg = resp.output.message or "任务失败"
                raise Exception(f"Aliyun ASR 任务失败: {error_msg}")

            last_status = task_status
            time.sleep(POLL_INTERVAL)

        raise Exception(
            f"Aliyun ASR 轮询超时 (已等待{POLL_TIMEOUT}秒)，最后状态: {last_status}"
        )

    @timeit
    def transcript(self, file_path: str) -> TranscriptResult:
        """执行转录过程，符合 Transcriber 接口"""
        oss_key = ""
        try:
            logger.info(f"开始处理音频文件: {file_path}")

            api_key = self._ensure_config()

            # 配置 dashscope
            dashscope.base_http_api_url = DASHSCOPE_BASE_URL
            dashscope.api_key = api_key

            # 上传音频到 OSS，获取公开可访问的 URL
            audio_url, oss_key = self._upload_audio(file_path)

            # 提交异步转写任务
            logger.info(f"提交 Aliyun ASR 任务: {audio_url}")
            task_resp = Transcription.async_call(
                model="fun-asr",
                file_urls=[audio_url],
                api_key=api_key,
            )

            task_id = task_resp.output.task_id
            logger.info(f"Aliyun ASR 任务已提交，task_id={task_id}")

            # 等待任务完成
            final_resp = self._poll_task(task_id, api_key)

            # 获取 transcription_url 并下载结果
            results = final_resp.output.results
            if not results:
                raise Exception("Aliyun ASR 返回结果为空")

            result_item = results[0]
            subtask_status = result_item.get("subtask_status", "")

            if subtask_status != "SUCCEEDED":
                code = result_item.get("code", "unknown")
                message = result_item.get("message", "未知错误")
                raise Exception(f"Aliyun ASR 子任务失败: code={code}, message={message}")

            transcription_url = result_item["transcription_url"]
            logger.info(f"下载转写结果: {transcription_url}")

            transcription_data = self._fetch_transcription_json(transcription_url)

            # 解析转写结果
            segments: list[TranscriptSegment] = []
            full_text = ""

            transcripts = transcription_data.get("transcripts", [])
            for channel_data in transcripts:
                channel_text_parts = []
                sentences = channel_data.get("sentences", [])
                for sentence in sentences:
                    seg_text = sentence.get("text", "").strip()
                    if not seg_text:
                        continue
                    begin_time = int(sentence.get("begin_time", 0)) / 1000.0
                    end_time = int(sentence.get("end_time", 0)) / 1000.0
                    segments.append(
                        TranscriptSegment(
                            start=begin_time,
                            end=end_time,
                            text=seg_text,
                        )
                    )
                    channel_text_parts.append(seg_text)
                full_text += " ".join(channel_text_parts) + " "

            result = TranscriptResult(
                language="zh",
                full_text=full_text.strip(),
                segments=segments,
                raw=transcription_data,
            )
            logger.info(f"Aliyun ASR 转写完成，文字长度: {len(full_text)}")
            return result

        except Exception as e:
            logger.error(f"Aliyun ASR 处理失败: {str(e)}")
            raise
        finally:
            if oss_key:
                try:
                    delete_oss_file(oss_key)
                except Exception as cleanup_err:
                    logger.warning(f"清理OSS文件失败: {cleanup_err}")

    def on_finish(self, video_path: str, result: TranscriptResult) -> None:
        """转写完成的回调"""
        logger.info(f"Aliyun ASR 转写完成: {video_path}")
        transcription_finished.send({"file_path": video_path})
