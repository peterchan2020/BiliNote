"""
PySceneDetect 场景检测模块
用于智能检测视频中的场景边界，并提取关键帧
"""
from scenedetect import open_video, SceneManager, ContentDetector

from app.utils.logger import get_logger

logger = get_logger(__name__)

# 推荐配置参数（不暴露给用户）
DEFAULT_THRESHOLD = 30.0  # ContentDetector 敏感度，值越小越敏感
DEFAULT_MIN_SCENE_LEN = 15  # 最小场景帧数，避免检测到噪点
DEFAULT_MAX_FRAMES = 36  # 最大帧数限制（适配 3x3 网格）



class SceneDetector:
    """
    基于 PySceneDetect 的场景检测器

    使用 ContentDetector 检测视频中的场景边界，
    并为每个场景提供关键帧时间戳。
    """

    def __init__(self, video_path: str):
        """
        初始化场景检测器

        Args:
            video_path: 视频文件路径
        """
        self.video_path = video_path
        self._video = None
        self._scene_manager = None
        self._scenes = []
        self._fps = None

    def detect(self) -> list:
        """
        执行场景检测，返回场景列表

        Returns:
            list: 场景列表，每个元素为 (start_timecode, end_timecode) 元组
        """
        self._video = open_video(self.video_path)
        self._scene_manager = SceneManager()
        self._scene_manager.add_detector(
            ContentDetector(
                threshold=DEFAULT_THRESHOLD,
                min_scene_len=DEFAULT_MIN_SCENE_LEN,
            )
        )

        # 执行检测（SceneManager 默认 auto_downscale=True，自动选择最优下采样因子）
        self._scene_manager.detect_scenes(video=self._video)
        self._scenes = self._scene_manager.get_scene_list()

        # 获取帧率用于时间转换
        self._fps = self._video.frame_rate

        logger.info(f"检测到 {len(self._scenes)} 个场景，帧率: {self._fps:.2f}fps")

        return self._scenes

    def get_keyframe_timestamps(
        self, max_frames: int = DEFAULT_MAX_FRAMES
    ) -> list[int]:
        """
        获取每个场景的关键帧时间戳（秒）

        每个场景取中间帧作为关键帧。

        Args:
            max_frames: 最大帧数限制

        Returns:
            list[int]: 关键帧的时间戳列表（秒为单位）
        """
        if not self._scenes:
            self.detect()

        timestamps = []
        for scene in self._scenes:
            start_frame = scene[0].get_frames()
            end_frame = scene[1].get_frames()
            # 取场景中间帧
            mid_frame = (start_frame + end_frame) // 2
            # 转换为秒
            timestamp = mid_frame / self._fps if self._fps else 0
            timestamps.append(int(timestamp))

        logger.info(f"原始场景关键帧数量: {len(timestamps)}")

        # 应用最大帧数限制
        if len(timestamps) > max_frames:
            logger.warning(
                f"场景数量 ({len(timestamps)}) 超过限制 ({max_frames})，将均匀采样"
            )
            step = len(timestamps) / max_frames
            timestamps = [timestamps[int(i * step)] for i in range(max_frames)]
            logger.info(f"采样后关键帧数量: {len(timestamps)}")

        return timestamps

    def get_scene_count(self) -> int:
        """获取检测到的场景数量"""
        if not self._scenes:
            self.detect()
        return len(self._scenes)

    def release(self):
        """释放资源"""
        self._video = None
        self._scene_manager = None

    def __enter__(self):
        """支持 with 语句"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """支持 with 语句自动释放资源"""
        self.release()

    def __del__(self):
        """析构时确保释放资源"""
        self.release()
