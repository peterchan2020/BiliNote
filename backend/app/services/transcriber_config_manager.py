import json
import os
from pathlib import Path
from typing import Optional, Dict, Any


class TranscriberConfigManager:
    """管理转写器配置，存储在 JSON 文件中，支持前端动态修改。"""

    def __init__(self, filepath: str = "config/transcriber.json"):
        self.path = Path(filepath)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _write(self, data: Dict[str, Any]):
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_config(self) -> Dict[str, Any]:
        """获取当前转写器配置，fallback 到环境变量默认值。"""
        data = self._read()
        return {
            "transcriber_type": data.get(
                "transcriber_type",
                os.getenv("TRANSCRIBER_TYPE", "fast-whisper"),
            ),
            "whisper_model_size": data.get(
                "whisper_model_size",
                os.getenv("WHISPER_MODEL_SIZE", "medium"),
            ),
            "doubao_app_key": data.get("doubao_app_key", ""),
            "doubao_api_key": data.get("doubao_api_key", ""),
            "aliyun_api_key": data.get("aliyun_api_key", ""),
        }

    def update_config(
        self,
        transcriber_type: str,
        whisper_model_size: Optional[str] = None,
        doubao_app_key: Optional[str] = None,
        doubao_api_key: Optional[str] = None,
        aliyun_api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """更新转写器配置并持久化。"""
        data = self._read()
        data["transcriber_type"] = transcriber_type
        if whisper_model_size is not None:
            data["whisper_model_size"] = whisper_model_size
        if doubao_app_key is not None:
            data["doubao_app_key"] = doubao_app_key
        if doubao_api_key is not None:
            data["doubao_api_key"] = doubao_api_key
        if aliyun_api_key is not None:
            data["aliyun_api_key"] = aliyun_api_key
        self._write(data)
        return self.get_config()

    def get_transcriber_type(self) -> str:
        return self.get_config()["transcriber_type"]

    def get_whisper_model_size(self) -> str:
        return self.get_config()["whisper_model_size"]

    def get_doubao_config(self) -> Dict[str, str]:
        """获取豆包转写器的配置。"""
        config = self.get_config()
        return {
            "doubao_app_key": config.get("doubao_app_key", ""),
            "doubao_api_key": config.get("doubao_api_key", ""),
        }

    def get_aliyun_config(self) -> Dict[str, str]:
        """获取阿里云 ASR 转写器的配置。"""
        config = self.get_config()
        return {
            "aliyun_api_key": config.get("aliyun_api_key", ""),
        }
