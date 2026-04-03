import json
import os
import time
import uuid
import tempfile
import subprocess

from app.decorators.timeit import timeit
from app.models.transcriber_model import TranscriptSegment, TranscriptResult
from app.services.transcriber_config_manager import TranscriberConfigManager
from app.transcriber.base import Transcriber
from app.utils.logger import get_logger
from app.utils.oss_client import upload_file_to_oss, delete_oss_file
from events import transcription_finished

logger = get_logger(__name__)


class DoubaoTranscriber(Transcriber):
    """豆包语音识别转写器 - 使用 BigModel API 异步模式"""

    SUBMIT_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
    QUERY_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"
    RESOURCE_ID = "volc.seedasr.auc"

    # 轮询超时配置（秒）
    POLL_TIMEOUT = 120
    POLL_INTERVAL = 3

    def __init__(self):
        pass

    def _get_audio_format(self, file_path: str) -> str:
        """根据文件扩展名推断音频格式"""
        ext = os.path.splitext(file_path)[1].lower()
        format_map = {
            '.mp3': 'mp3',
            '.wav': 'wav',
            '.m4a': 'm4a',
            '.aac': 'aac',
            '.ogg': 'ogg',
            '.flac': 'flac',
            '.wma': 'wma',
        }
        return format_map.get(ext, 'mp3')

    def _convert_to_wav(self, file_path: str) -> str:
        """使用 FFmpeg 将音频转换为 16kHz 单声道 WAV 格式"""
        wav_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        wav_path = wav_file.name
        wav_file.close()

        cmd = [
            'ffmpeg', '-y', '-i', file_path,
            '-f', 'wav', '-ar', '16000', '-ac', '1', '-acodec', 'pcm_s16le',
            wav_path
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

    def _submit_with_curl(self, payload: dict, request_id: str, api_key: str) -> tuple:
        """使用 curl 发送请求，避免 Python HTTP 库的代理问题"""
        # 写入 JSON 到临时文件，避免命令行长度限制
        json_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8')
        json_path = json_file.name
        json_file.write(json.dumps(payload))
        json_file.close()

        try:
            curl_cmd = [
                'curl', '-s', '-X', 'POST', self.SUBMIT_URL,
                '-H', 'Content-Type: application/json',
                '-H', f'x-api-key: {api_key}',
                '-H', f'X-Api-Resource-Id: {self.RESOURCE_ID}',
                '-H', f'X-Api-Request-Id: {request_id}',
                '-H', 'X-Api-Sequence: -1',
                '--data-binary', f'@{json_path}',
                '--connect-timeout', '60',
                '-m', '600',
            ]
            result = subprocess.run(curl_cmd, capture_output=True, timeout=660)
            resp_text = result.stdout.decode('utf-8').strip()
            return resp_text
        finally:
            try:
                os.unlink(json_path)
            except Exception:
                pass

    def _query_with_curl(self, task_id: str, request_id: str, api_key: str) -> tuple:
        """使用 curl 查询任务结果"""
        payload = {"task_id": task_id}
        json_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8')
        json_path = json_file.name
        json_file.write(json.dumps(payload))
        json_file.close()

        try:
            curl_cmd = [
                'curl', '-s', '-X', 'POST', self.QUERY_URL,
                '-H', 'Content-Type: application/json',
                '-H', f'x-api-key: {api_key}',
                '-H', f'X-Api-Resource-Id: {self.RESOURCE_ID}',
                '-H', f'X-Api-Request-Id: {request_id}',
                '-H', 'X-Api-Sequence: -1',
                '--data-binary', f'@{json_path}',
                '--connect-timeout', '30',
                '-m', '30',
            ]
            result = subprocess.run(curl_cmd, capture_output=True, timeout=60)
            resp_text = result.stdout.decode('utf-8').strip()
            return resp_text
        finally:
            try:
                os.unlink(json_path)
            except Exception:
                pass

    def _submit(self, file_path: str, api_key: str) -> tuple[str, str]:
        """提交识别请求到豆包 BigModel 异步 API，返回 (task_id, oss_key)"""
        request_id = str(uuid.uuid4())

        # FFmpeg 转换为 WAV
        wav_path = self._convert_to_wav(file_path)
        oss_key = ""

        try:
            # 上传到 OSS，获取签名 URL
            from pathlib import Path
            oss_url, oss_key = upload_file_to_oss(
                Path(wav_path),
                sub_dir="bilinote/asr",
                content_type="audio/wav",
            )
            logger.info(f"音频已上传至 OSS: key={oss_key}, url={oss_url}")

        finally:
            if wav_path != file_path:
                try:
                    os.unlink(wav_path)
                    logger.info(f"已清理临时WAV文件: {wav_path}")
                except Exception:
                    pass

        payload = {
            "user": {"uid": "bilinote"},
            "audio": {
                "url": oss_url,
                "format": "wav",
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": False,
                "enable_speaker_info": False,
                "enable_channel_split": False,
                "show_utterances": False,
                "vad_segment": False,
            }
        }

        logger.info(f"开始向豆包API提交请求，文件: {os.path.basename(file_path)}, request_id: {request_id}")

        resp_text = self._submit_with_curl(payload, request_id, api_key)
        logger.info(f"豆包提交返回: body={resp_text}")

        # 尝试解析 JSON 获取状态码
        status_code = ''
        try:
            resp_data = json.loads(resp_text) if resp_text else {}
            status_code = str(resp_data.get('header', {}).get('code', ''))
        except Exception:
            pass

        # 检查响应体中的状态码（curl 看不到 response headers，用 body 内容判断）
        if '45000000' in resp_text or '认证失败' in resp_text or 'Invalid' in resp_text:
            raise Exception(f"豆包API认证失败: body={resp_text}")

        if '4500' in resp_text and '0000' not in resp_text:
            raise Exception(f"豆包API提交失败: body={resp_text}")

        # 20000000 在响应体中可能不出现，用 request_id 作为 task_id
        task_id = request_id
        try:
            resp_data = json.loads(resp_text) if resp_text else {}
            if resp_data.get('task_id'):
                task_id = resp_data.get('task_id')
        except Exception:
            pass

        logger.info(f"豆包任务提交成功，task_id: {task_id}")
        return task_id, oss_key

    def _query(self, task_id: str, api_key: str) -> dict:
        """查询豆包任务结果"""
        request_id = str(uuid.uuid4())

        resp_text = self._query_with_curl(task_id, request_id, api_key)
        logger.info(f"豆包查询返回: body={resp_text}")

        if '45000000' in resp_text or 'cannot find task' in resp_text.lower():
            return {'_api_status': '45000000', 'audio_info': {}, 'result': {}, 'error': 'task_not_found'}

        try:
            data = json.loads(resp_text) if resp_text else {}
            status_code = str(data.get('header', {}).get('code', ''))
            data['_api_status'] = status_code or '20000000'
            return data
        except Exception:
            return {'_api_status': 'unknown', 'audio_info': {}, 'result': {}, 'error': 'invalid_json'}

    def _poll_result(self, task_id: str, api_key: str) -> dict:
        """轮询等待任务完成"""
        start_time = time.time()
        last_error = None
        first_query_done = False
        not_found_retries = 0

        while time.time() - start_time < self.POLL_TIMEOUT:
            result_data = self._query(task_id, api_key)
            logger.info(f"豆包任务状态查询，task_id: {task_id}, result_data={result_data}")

            api_status = result_data.get('_api_status', '')
            error = result_data.get('error', '')
            audio_info = result_data.get('audio_info', {})
            result_text = result_data.get('result', {}).get('text', '')

            if error == 'task_not_found':
                if not first_query_done:
                    first_query_done = True
                    logger.info(f"豆包任务立即查询未找到，等待5秒后重试: task_id={task_id}")
                    time.sleep(5)
                    continue
                not_found_retries += 1
                if not_found_retries < 3:
                    logger.info(f"豆包任务仍未找到，等待5秒后重试 (第{not_found_retries}次): task_id={task_id}")
                    time.sleep(5)
                    continue
                raise Exception(f"豆包任务不存在（已重试{not_found_retries}次）: task_id={task_id}")

            if error and error != 'task_not_found':
                last_error = error

            has_duration = bool(audio_info.get('duration'))
            has_text = bool(result_text and result_text.strip())
            is_complete = api_status == '20000000'
            # 检查result是否有效（不为空dict）
            has_result = isinstance(result_data.get('result'), dict) and bool(result_data['result'])

            # 只有当result有实际数据时才返回，否则继续轮询或超时
            if (is_complete and has_result) or has_text:
                logger.info(f"豆包任务完成: audio_info={audio_info}, result_text_len={len(result_text)}, has_result={has_result}")
                return result_data

            time.sleep(self.POLL_INTERVAL)

        raise Exception(f"豆包任务轮询超时 (已等待{self.POLL_TIMEOUT}秒), task_id: {task_id}, last_error: {last_error}")

    @timeit
    def transcript(self, file_path: str) -> TranscriptResult:
        """执行转录过程，符合 Transcriber 接口"""
        oss_key = ""
        try:
            logger.info(f"开始处理文件: {file_path}")

            config_manager = TranscriberConfigManager()
            doubao_config = config_manager.get_doubao_config()
            api_key = doubao_config.get('doubao_api_key', '')

            if not api_key:
                raise Exception("豆包 API 配置不完整，请在音频转写配置页面配置 App Key 和 API Key。")

            task_id, oss_key = self._submit(file_path, api_key)

            logger.info(f"开始轮询豆包任务结果，task_id: {task_id}")
            result_data = self._poll_result(task_id, api_key)

            segments = []
            full_text = ""

            result_obj = result_data.get('result', {})
            # 验证result是否有有效数据
            if not isinstance(result_obj, dict) or (not result_obj.get('text') and not result_obj.get('utterances')):
                raise Exception(f"豆包API返回了空的transcription结果: result_data={result_data}")

            if isinstance(result_obj, dict):
                # 优先读取 result.text（完整合并文本）
                text = result_obj.get('text', '').strip()
                if text:
                    full_text = text
                # 同时解析 utterances 数组，提取分段落和时间信息
                utterances = result_obj.get('utterances', [])
                for item in utterances:
                    seg_text = item.get('text', '').strip()
                    if seg_text:
                        # start_time/end_time 单位为毫秒，转换为秒
                        start_time = float(item.get('start_time', 0)) / 1000.0
                        end_time = float(item.get('end_time', 0)) / 1000.0
                        segments.append(TranscriptSegment(
                            start=start_time,
                            end=end_time,
                            text=seg_text
                        ))
            elif isinstance(result_obj, list):
                for item in result_obj:
                    text = item.get('text', '').strip()
                    if text:
                        start_time = float(item.get('start_time', 0)) / 1000.0
                        end_time = float(item.get('end_time', 0)) / 1000.0
                        full_text += text + " "
                        segments.append(TranscriptSegment(
                            start=start_time,
                            end=end_time,
                            text=text
                        ))

            result = TranscriptResult(
                language="zh",
                full_text=full_text.strip(),
                segments=segments,
                raw=result_data
            )
            logger.info(f"豆包ASR转写完成，文字长度: {len(full_text)}")
            return result

        except Exception as e:
            logger.error(f"豆包ASR处理失败: {str(e)}")
            raise
        finally:
            if oss_key:
                try:
                    delete_oss_file(oss_key)
                except Exception as cleanup_err:
                    logger.warning(f"清理OSS文件失败: {cleanup_err}")

    def on_finish(self, video_path: str, result: TranscriptResult) -> None:
        """转写完成的回调"""
        logger.info(f"豆包ASR转写完成: {video_path}")
        transcription_finished.send({"file_path": video_path})
