"""
阿里云 OSS 上传工具。

将本地文件或字节流上传到阿里云 OSS，返回带签名的公开访问 URL（1小时有效期）。
 MinerU 云端模式通过 OSS URL 拉取文件，避免大文件走 HTTP multipart 中转。
"""

import os
import uuid
from pathlib import Path

from app.utils.logger import get_logger

logger = get_logger(__name__)

# 签名 URL 有效期（秒），MinerU 解析一个大 PDF 通常在 15 分钟内
OSS_SIGN_URL_EXPIRES = 3600  # 1 小时


def _get_oss_config() -> tuple[str, str, str, str]:
    """从环境变量获取 OSS 配置。"""
    access_key_id = os.getenv("OSS_ACCESS_KEY_ID", "")
    access_key_secret = os.getenv("OSS_ACCESS_KEY_SECRET", "")
    bucket_name = os.getenv("OSS_BUCKET_NAME", "")
    endpoint = os.getenv("OSS_ENDPOINT", "")

    if not all([access_key_id, access_key_secret, bucket_name, endpoint]):
        raise RuntimeError(
            "OSS 环境变量未配置完整，请检查: OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET, "
            "OSS_BUCKET_NAME, OSS_ENDPOINT"
        )

    return access_key_id, access_key_secret, bucket_name, endpoint


def upload_bytes_to_oss(
    content: bytes,
    key: str,
    content_type: str = "application/pdf",
) -> str:
    """
    将字节内容上传到 OSS，返回公开访问 URL（无签名）。

    :param content: 文件字节内容
    :param key: OSS 对象 key（如 "mineru/pdfs/task_id.pdf"）
    :param content_type: MIME 类型
    :return: OSS 公开访问 URL
    """
    access_key_id, access_key_secret, bucket_name, endpoint = _get_oss_config()

    import oss2  # lazy import，避免模块加载时 oss2 未安装则无法使用

    auth = oss2.Auth(access_key_id, access_key_secret)
    bucket = oss2.Bucket(auth, endpoint, bucket_name)

    headers = {"Content-Type": content_type, "x-oss-object-acl": "public-read"}
    result = bucket.put_object(key, content, headers=headers)

    if result.status == 200:
        logger.info(f"[OSS] 上传成功: {key}, 大小: {len(content):,} bytes")
    else:
        raise RuntimeError(f"[OSS] 上传失败，HTTP status: {result.status}")

    # 公开 URL 格式：http://{bucket}.{endpoint_host}/{key}
    # 例如：http://my-bucket.oss-cn-beijing.aliyuncs.com/path/to/file
    from urllib.parse import urlparse
    parsed = urlparse(endpoint)
    public_url = f"http://{bucket_name}.{parsed.netloc}/{key}"
    logger.info(f"[OSS] 公开 URL: {public_url}")
    return public_url


def upload_file_to_oss(
    file_path: Path,
    sub_dir: str = "mineru/pdfs",
    content_type: str = "application/pdf",
) -> tuple[str, str]:
    """
    将本地文件上传到 OSS，返回 OSS 签名 URL 和 OSS key。

    :param file_path: 本地文件路径
    :param sub_dir: OSS 存储子目录
    :param content_type: MIME 类型
    :return: (oss_signed_url, oss_key)
    """
    content = file_path.read_bytes()
    # 生成唯一 key，避免冲突
    key = f"{sub_dir.strip('/')}/{uuid.uuid4().hex}_{file_path.name}"
    oss_url = upload_bytes_to_oss(content, key, content_type)
    return oss_url, key


def delete_oss_file(key: str) -> None:
    """
    从 OSS 删除指定文件。

    :param key: OSS 对象 key（如 "mineru/pdfs/task_id.pdf"）
    """
    access_key_id, access_key_secret, bucket_name, endpoint = _get_oss_config()

    import oss2  # lazy import

    auth = oss2.Auth(access_key_id, access_key_secret)
    bucket = oss2.Bucket(auth, endpoint, bucket_name)

    result = bucket.delete_object(key)
    if result.status == 204:
        logger.info(f"[OSS] 文件已删除: {key}")
    else:
        logger.warning(f"[OSS] 文件删除失败，HTTP status: {result.status}, key: {key}")


def ensure_oss_configured() -> bool:
    """检查 OSS 配置是否完整。"""
    try:
        _get_oss_config()
        return True
    except RuntimeError:
        return False
