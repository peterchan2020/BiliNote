import json
import os
import time
import uuid
import tempfile
import subprocess
import threading
from pathlib import Path
from http import HTTPStatus
from concurrent.futures import ThreadPoolExecutor

from dashscope.audio.asr import Transcription

from app.decorators.timeit import timeit
from app.models.transcriber_model import TranscriptSegment, TranscriptResult
from app.services.transcriber_config_manager import TranscriberConfigManager
from app.transcriber.base import Transcriber
from app.utils.logger import get_logger
from app.utils.oss_client import upload_file_to_oss, delete_oss_file
from events import transcription_finished

logger = get_logger(__name__)

# 重试配置
MAX_SUBMIT_RETRIES = 3  # 提交任务最大重试次数
MAX_FULL_RETRIES = 3    # 整体流程最大重试次数（包括重新上传）
RETRY_BASE_DELAY = 3   # 重试基础延迟（秒）

# 全局串行执行器：确保同一时间只有一个 Aliyun ASR 任务在运行
# ThreadPoolExecutor(max_workers=1) 保证所有任务串行执行，不会并发
_aliyun_asr_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="AliyunASR")


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

    # fun-asr 支持的音频格式：aac, amr, avi, flac, flv, m4a, mkv, mov, mp3, mp4,
    # mpeg, ogg, opus, wav, webm, wma, wmv
    # 只有不支持的格式才需要转换为 WAV
    _SUPPORTED_FORMATS = {
        '.aac', '.amr', '.avi', '.flac', '.flv', '.m4a', '.mkv', '.mov',
        '.mp3', '.mp4', '.mpeg', '.ogg', '.opus', '.wav', '.webm', '.wma', '.wmv'
    }

    def _convert_to_wav(self, file_path: str) -> str:
        """使用 FFmpeg 将音频转换为 16kHz 单声道 WAV 格式
        
        仅当原始格式不被 fun-asr 支持时才转换。
        参考：https://help.aliyun.com/zh/model-studio/funauidio-asr-recorded-speech-recognition-python-sdk
        """
        ext = Path(file_path).suffix.lower()
        if ext in self._SUPPORTED_FORMATS:
            logger.info(f"音频格式 {ext} 已被 fun-asr 支持，无需转换: {os.path.basename(file_path)}")
            return file_path

        logger.info(f"音频格式 {ext} 不被 fun-asr 支持，将转换为 WAV")
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

    def _get_audio_content_type(self, file_path: str) -> str:
        """根据文件扩展名返回 content_type"""
        ext = Path(file_path).suffix.lower()
        content_type_map = {
            '.mp3': 'audio/mpeg',
            '.mp4': 'audio/mp4',
            '.wav': 'audio/wav',
            '.flac': 'audio/flac',
            '.ogg': 'audio/ogg',
            '.m4a': 'audio/mp4',
            '.aac': 'audio/aac',
            '.wma': 'audio/x-ms-wma',
            '.webm': 'audio/webm',
        }
        return content_type_map.get(ext, 'application/octet-stream')

    def _upload_audio(self, file_path: str) -> tuple[str, str]:
        """上传音频文件到阿里云 OSS，返回 (public_url, oss_key)"""
        wav_path = self._convert_to_wav(file_path)
        oss_key = ""
        content_type = self._get_audio_content_type(wav_path)

        try:
            public_url, oss_key = upload_file_to_oss(
                Path(wav_path),
                sub_dir="bilinote/asr",
                content_type=content_type,
            )
            logger.info(f"音频已上传至 OSS: key={oss_key}, url={public_url}, content_type={content_type}")
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

    def _submit_and_wait(self, audio_url: str, api_key: str) -> dict:
        """提交ASR任务并等待完成，返回最终响应。
        
        按照阿里云官方文档推荐方式：
        1. async_call 提交任务
        2. wait 同步等待任务完成（SDK内部处理轮询）
        参考：https://help.aliyun.com/zh/model-studio/funauidio-asr-recorded-speech-recognition-python-sdk
        """
        # 提交异步转写任务
        logger.info(f"提交 Aliyun ASR 任务: {audio_url}")
        task_resp = Transcription.async_call(
            model="fun-asr",
            file_urls=[audio_url],
            api_key=api_key,
        )
        task_id = task_resp.output.task_id
        logger.info(f"Aliyun ASR 任务已提交，task_id={task_id}")

        # 使用官方推荐的 wait 方法同步等待任务完成
        logger.info(f"等待任务完成: task_id={task_id}")
        final_resp = Transcription.wait(task=task_id, api_key=api_key)
        return final_resp

    def _parse_result(self, final_resp) -> TranscriptResult:
        """解析阿里云ASR响应，返回TranscriptResult"""
        # 检查任务状态
        if final_resp.status_code != HTTPStatus.OK:
            error_msg = final_resp.output.message if hasattr(final_resp.output, 'message') else "未知错误"
            raise Exception(f"Aliyun ASR 任务失败: {error_msg}")

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

    def _do_transcript(self, file_path: str) -> TranscriptResult:
        """实际执行转录过程（在单线程执行器中运行）
        
        包含完整重试逻辑：当遇到服务端错误时，会重新上传音频并重新提交任务。
        """
        last_error = None

        for full_attempt in range(MAX_FULL_RETRIES):
            oss_key = ""
            try:
                logger.info(f"开始处理音频文件 (整体尝试 {full_attempt + 1}/{MAX_FULL_RETRIES}): {file_path}")

                api_key = self._ensure_config()

                # 上传音频到 OSS
                audio_url, oss_key = self._upload_audio(file_path)

                # 提交任务并等待（带提交重试）
                last_submit_error = None
                for submit_attempt in range(MAX_SUBMIT_RETRIES):
                    try:
                        final_resp = self._submit_and_wait(audio_url, api_key)
                        # 等待成功，解析结果
                        return self._parse_result(final_resp)
                    except Exception as e:
                        last_submit_error = e
                        error_str = str(e)
                        # 如果是服务端错误（SERVER_ERROR），重新提交可能有用
                        if "SERVER_ERROR" in error_str:
                            logger.warning(
                                f"阿里云服务端错误 (提交尝试 {submit_attempt + 1}/{MAX_SUBMIT_RETRIES}): {e}"
                            )
                            if submit_attempt < MAX_SUBMIT_RETRIES - 1:
                                delay = RETRY_BASE_DELAY * (2 ** submit_attempt)
                                logger.info(f"等待 {delay}秒 后重试...")
                                time.sleep(delay)
                                continue
                        # 非服务端错误，直接抛出
                        raise

                # 所有提交重试都失败了
                raise last_submit_error

            except Exception as e:
                last_error = e
                error_str = str(e)
                logger.warning(f"转录失败 (整体尝试 {full_attempt + 1}/{MAX_FULL_RETRIES}): {e}")

                # 如果是服务端错误，尝试完全重做（重新上传+重新提交）
                if "SERVER_ERROR" in error_str and full_attempt < MAX_FULL_RETRIES - 1:
                    logger.info("检测到服务端错误，将重新上传音频并重试...")
                    # 清理当前 OSS 文件
                    if oss_key:
                        try:
                            delete_oss_file(oss_key)
                            oss_key = ""
                        except Exception:
                            pass
                    delay = RETRY_BASE_DELAY * (2 ** full_attempt)
                    logger.info(f"等待 {delay}秒 后重新上传并重试...")
                    time.sleep(delay)
                    continue
                else:
                    raise
            finally:
                if oss_key:
                    try:
                        delete_oss_file(oss_key)
                    except Exception as cleanup_err:
                        logger.warning(f"清理OSS文件失败: {cleanup_err}")

        raise last_error

    @timeit
    def transcript(self, file_path: str) -> TranscriptResult:
        """执行转录过程，符合 Transcriber 接口
        
        使用全局单线程执行器确保同一时间只有一个 Aliyun ASR 任务在运行，
        彻底解决 dashscope SDK 的线程安全问题。
        """
        logger.info("提交 Aliyun ASR 任务到串行执行器（如有其他任务正在运行，将等待）...")
        # 在单线程执行器中同步执行，确保串行化
        # max_workers=1 保证同一时间只有一个任务在运行
        future = _aliyun_asr_executor.submit(self._do_transcript, file_path)
        return future.result()

    def on_finish(self, video_path: str, result: TranscriptResult) -> None:
        """转写完成的回调"""
        logger.info(f"Aliyun ASR 转写完成: {video_path}")
        transcription_finished.send({"file_path": video_path})
