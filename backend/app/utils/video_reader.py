import base64
import hashlib
import os
import platform
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
import ffmpeg
from PIL import Image, ImageDraw, ImageFont

from app.utils.logger import get_logger
from app.utils.path_helper import get_app_dir

logger = get_logger(__name__)
class VideoReader:
    def __init__(self,
                 video_path: str,
                 grid_size=(3, 3),
                 frame_interval=2,
                 dedupe_enabled=True,
                 unit_width=960,
                 unit_height=540,
                 save_quality=90,
                 font_path: Optional[str] = None,
                 frame_dir=None,
                 grid_dir=None,
                 use_scene_detection=False,
                 max_scene_frames=36,
                 task_id: Optional[str] = None):
        # Use system font path if not provided, fallback to a common location
        if font_path is None:
            system = platform.system()
            if system == "Windows":
                font_path = "C:\\Windows\\Fonts\\arial.ttf"
            elif system == "Darwin":
                font_path = "/System/Library/Fonts/Helvetica.ttc"
            else:
                font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        self.video_path = video_path
        self.grid_size = grid_size
        self.frame_interval = frame_interval
        self.dedupe_enabled = dedupe_enabled
        self.unit_width = unit_width
        self.unit_height = unit_height
        self.save_quality = save_quality
        # 按 task_id 隔离输出目录，避免并发任务互相覆盖
        if task_id:
            self.frame_dir = frame_dir or get_app_dir(os.path.join(task_id, "output_frames"))
            self.grid_dir = grid_dir or get_app_dir(os.path.join(task_id, "grid_output"))
        else:
            self.frame_dir = frame_dir or get_app_dir("output_frames")
            self.grid_dir = grid_dir or get_app_dir("grid_output")
        self.use_scene_detection = use_scene_detection
        self.max_scene_frames = max_scene_frames
        logger.debug(f"VideoReader 初始化: video_path={video_path}, frame_dir={self.frame_dir}, grid_dir={self.grid_dir}")
        self.font_path = font_path

    @staticmethod
    def _calculate_file_hash(file_path: str) -> str:
        """Calculate SHA256 hash of file for deduplication."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def format_time(self, seconds: float) -> str:
        mm = int(seconds // 60)
        ss = int(seconds % 60)
        return f"{mm:02d}_{ss:02d}"

    def extract_time_from_filename(self, filename: str) -> float:
        match = re.search(r"frame_(\d{2})_(\d{2})\.jpg", filename)
        if match:
            mm, ss = map(int, match.groups())
            return mm * 60 + ss
        return float('inf')

    @staticmethod
    def _validate_frame_file(path: str) -> bool:
        """验证帧文件是否有效：存在、非空、可被 PIL 正常解析。"""
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return False
        try:
            with Image.open(path) as img:
                img.verify()
            return True
        except OSError as e:
            logger.warning(f"帧文件 IO 错误: {path}, 错误: {e}")
            return False
        except Exception as e:
            logger.warning(f"帧文件无法解析: {path}, 错误: {e}")
            return False

    def _extract_single_frame(self, ts: int) -> str | None:
        """提取单帧，返回输出路径或 None（失败时）。"""
        time_label = self.format_time(ts)
        output_path = os.path.join(self.frame_dir, f"frame_{time_label}.jpg")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(ts), "-i", self.video_path, "-frames:v", "1", "-q:v", "2", "-y", output_path]
        try:
            subprocess.run(cmd, check=True)
            if not self._validate_frame_file(output_path):
                logger.warning(f"帧文件无效或损坏，跳过: {output_path}")
                return None
            return output_path
        except subprocess.CalledProcessError:
            return None

    def _extract_frames_at_timestamps(self, timestamps: list[int]) -> list[str]:
        """在指定时间戳列表提取帧"""
        os.makedirs(self.frame_dir, exist_ok=True)

        # 并行提取帧
        max_workers = min(os.cpu_count() or 4, 8, len(timestamps))
        frame_results: dict[int, str | None] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(self._extract_single_frame, ts): ts for ts in timestamps}
            for future in as_completed(futures):
                ts = futures[future]
                frame_results[ts] = future.result()

        # 按时间戳顺序整理结果
        image_paths = []
        for ts in sorted(timestamps):
            output_path = frame_results.get(ts)
            if output_path and os.path.exists(output_path):
                image_paths.append(output_path)

        return image_paths

    def _extract_frames_scene_based(self, max_frames: int) -> list[str]:
        """
        基于场景检测提取帧（使用 PySceneDetect）

        Args:
            max_frames: 最大提取帧数

        Returns:
            list[str]: 提取的帧文件路径列表
        """
        try:
            from app.utils.scene_detector import SceneDetector

            logger.info("开始使用场景检测提取关键帧...")

            with SceneDetector(self.video_path) as detector:
                timestamps = detector.get_keyframe_timestamps(max_frames=max_frames)
                scene_count = detector.get_scene_count()
                logger.info(f"场景检测完成，检测到 {scene_count} 个场景，提取 {len(timestamps)} 个关键帧")

            return self._extract_frames_at_timestamps(timestamps)
        except ImportError:
            raise ImportError(
                "PySceneDetect 未安装，请运行 'pip install scenedetect[opencv]' 安装后重试"
            )

    def _extract_frames_fixed_interval(self, max_frames: int) -> list[str]:
        """原有的固定间隔采样逻辑"""
        try:
            os.makedirs(self.frame_dir, exist_ok=True)
            duration = float(ffmpeg.probe(self.video_path)["format"]["duration"])
            timestamps = [i for i in range(0, int(duration), self.frame_interval)][:max_frames]

            # 并行提取帧
            max_workers = min(os.cpu_count() or 4, 8, len(timestamps))
            frame_results: dict[int, str | None] = {}
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(self._extract_single_frame, ts): ts for ts in timestamps}
                for future in as_completed(futures):
                    ts = futures[future]
                    frame_results[ts] = future.result()

            # 按时间戳顺序整理结果，并进行去重
            image_paths = []
            last_hash = None
            for ts in timestamps:
                output_path = frame_results.get(ts)
                if not output_path or not os.path.exists(output_path):
                    continue

                if self.dedupe_enabled:
                    frame_hash = self._calculate_file_hash(output_path)
                    if frame_hash == last_hash:
                        os.remove(output_path)
                        continue
                    last_hash = frame_hash

                image_paths.append(output_path)
            return image_paths
        except Exception as e:
            logger.error(f"分割帧发生错误：{str(e)}")
            raise ValueError("视频处理失败")

    def extract_frames(self, max_frames=1000) -> list[str]:
        """
        提取视频帧

        当 use_scene_detection=True 时使用场景检测，否则使用固定间隔采样
        """
        if self.use_scene_detection:
            return self._extract_frames_scene_based(max_frames)
        else:
            return self._extract_frames_fixed_interval(max_frames)

    def group_images(self) -> list[list[str]]:
        image_files = [os.path.join(self.frame_dir, f) for f in os.listdir(self.frame_dir) if
                       f.startswith("frame_") and f.endswith(".jpg")]
        image_files.sort(key=lambda f: self.extract_time_from_filename(os.path.basename(f)))
        group_size = self.grid_size[0] * self.grid_size[1]
        return [image_files[i:i + group_size] for i in range(0, len(image_files), group_size)]

    def concat_images(self, image_paths: list[str], name: str) -> str | None:
        os.makedirs(self.grid_dir, exist_ok=True)
        font = ImageFont.truetype(self.font_path, 48) if os.path.exists(self.font_path) else ImageFont.load_default()
        images = []

        for path in image_paths:
            try:
                img = Image.open(path).convert("RGB").resize((self.unit_width, self.unit_height), Image.Resampling.LANCZOS)
            except Exception as e:
                logger.warning(f"帧文件损坏，跳过: {path}, 错误: {e}")
                continue
            timestamp = re.search(r"frame_(\d{2})_(\d{2})\.jpg", os.path.basename(path))
            time_text = f"{timestamp.group(1)}:{timestamp.group(2)}" if timestamp else ""
            draw = ImageDraw.Draw(img)
            draw.text((10, 10), time_text, fill="yellow", font=font, stroke_width=1, stroke_fill="black")
            images.append(img)

        if not images:
            logger.warning(f"网格 {name} 中所有帧均无效，跳过生成")
            return None

        cols, rows = self.grid_size
        grid_img = Image.new("RGB", (self.unit_width * cols, self.unit_height * rows), (255, 255, 255))

        for i, img in enumerate(images):
            x = (i % cols) * self.unit_width
            y = (i // cols) * self.unit_height
            grid_img.paste(img, (x, y))

        save_path = os.path.join(self.grid_dir, f"{name}.jpg")
        grid_img.save(save_path, quality=self.save_quality)
        return save_path

    def encode_images_to_base64(self, image_paths: list[str]) -> list[str]:
        base64_images = []
        for path in image_paths:
            with open(path, "rb") as img_file:
                encoded_string = base64.b64encode(img_file.read()).decode("utf-8")
                base64_images.append(f"data:image/jpeg;base64,{encoded_string}")
        return base64_images

    def run(self)->list[str]:
        logger.info("开始提取视频帧...")
        try:
            # 确保目录存在
            os.makedirs(self.frame_dir, exist_ok=True)
            os.makedirs(self.grid_dir, exist_ok=True)
            #清空帧文件夹
            for file in os.listdir(self.frame_dir):
                if file.startswith("frame_"):
                    os.remove(os.path.join(self.frame_dir, file))
            #清空网格文件夹
            for file in os.listdir(self.grid_dir):
                if file.startswith("grid_"):
                    os.remove(os.path.join(self.grid_dir, file))
            self.extract_frames()
            logger.info("开始拼接网格图...")
            image_paths = []
            groups = self.group_images()
            for idx, group in enumerate(groups, start=1):
                if len(group) < self.grid_size[0] * self.grid_size[1]:
                    logger.warning(f"跳过第 {idx} 组，图片不足 {self.grid_size[0] * self.grid_size[1]} 张")
                    continue
                out_path = self.concat_images(group, f"grid_{idx}")
                if out_path:
                    image_paths.append(out_path)

            logger.info("开始编码图像...")
            urls = self.encode_images_to_base64(image_paths)
            return urls
        except Exception as e:
            logger.error(f"发生错误：{str(e)}")
            raise ValueError("视频处理失败")


