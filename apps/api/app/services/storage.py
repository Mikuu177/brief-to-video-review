import base64
import hashlib
import os
import tempfile
import zipfile
from pathlib import Path

import httpx
from app.errors import ReviewError
from app.settings import settings
from PIL import Image

MIMES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".txt": "text/plain",
    ".mp4": "video/mp4",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
ALLOWED = {
    "brief": {".pdf", ".doc", ".docx", ".txt"},
    "script": {".pdf", ".doc", ".docx", ".txt", ".xlsx"},
    "video": {".mp4"},
    "cover": {".png", ".jpg", ".jpeg", ".webp"},
    "caption": {".txt"},
}


def local_path(key: str) -> Path:
    root = settings.data_dir.resolve()
    result = (root / key).resolve()
    if not result.is_relative_to(root):
        raise ReviewError("INVALID_PATH", "非法文件位置")
    return result


def tos_client():
    import tos

    if not all((settings.tos_access_key, settings.tos_secret_key, settings.tos_bucket)):
        raise ReviewError("STORAGE_NOT_CONFIGURED", "私有 TOS 存储尚未配置")
    return tos.TosClientV2(
        settings.tos_access_key, settings.tos_secret_key, settings.tos_endpoint, settings.tos_region
    )


def ensure_local(key: str) -> Path:
    path = local_path(key)
    if path.exists():
        return path
    if settings.storage_backend != "tos":
        raise ReviewError("ASSET_MISSING", "本地私有文件不存在，请检查存储卷")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".download")
    try:
        tos_client().get_object_to_file(settings.tos_bucket, key, str(temporary))
        expected = path.stem
        if len(expected) == 64 and hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
            raise ReviewError("STORAGE_INTEGRITY_ERROR", "远端素材哈希校验失败")
        temporary.replace(path)
    except ReviewError:
        raise
    except Exception as exc:
        raise ReviewError("PRIVATE_STORAGE_ERROR", "私有存储读取失败，未返回未验证文件") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return path


def validate_type(path: Path, suffix: str):
    with path.open("rb") as stream:
        header = stream.read(32)
    valid = False
    if suffix == ".pdf":
        valid = header.startswith(b"%PDF-")
    elif suffix == ".doc":
        valid = header.startswith(bytes.fromhex("d0cf11e0a1b11ae1"))
    elif suffix in (".docx", ".xlsx"):
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                expected = "word/document.xml" if suffix == ".docx" else "xl/workbook.xml"
                entries = archive.infolist()
                valid = expected in archive.namelist()
                if sum(item.file_size for item in entries) > 500 * 1024 * 1024 or len(entries) > 10000:
                    raise ReviewError("INVALID_FILE", "Office 文件解压大小超出限制")
    elif suffix == ".mp4":
        valid = header[4:8] == b"ftyp"
    elif suffix in (".png", ".jpg", ".jpeg", ".webp"):
        try:
            with Image.open(path) as image:
                image.verify()
                valid = (
                    image.format == {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}[suffix]
                )
        except (ValueError, OSError):
            valid = False
    elif suffix == ".txt":
        try:
            decode_text(path.read_bytes())
            valid = b"\x00" not in header
        except UnicodeError:
            valid = False
    if not valid:
        raise ReviewError("INVALID_FILE_TYPE", "文件内容与扩展名不匹配或文件损坏")


def decode_text(data: bytes) -> str:
    encodings = ["utf-8-sig"]
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings.append("utf-16")
    encodings.append("gb18030")
    for encoding in encodings:
        try:
            text = data.decode(encoding)
            if "\x00" not in text:
                return text
        except UnicodeError:
            continue
    raise UnicodeError("无法识别文本编码")


def save_upload(fileobj, filename: str, kind: str) -> dict:
    suffix = Path(filename).suffix.lower()
    if kind not in ALLOWED or suffix not in ALLOWED[kind]:
        raise ReviewError("UNSUPPORTED_FILE_TYPE", "该素材类型不支持此文件格式")
    temp_dir = local_path("tmp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    count = 0
    fd, temp = tempfile.mkstemp(dir=temp_dir)
    path = Path(temp)
    try:
        with os.fdopen(fd, "wb") as output:
            while chunk := fileobj.read(1024 * 1024):
                count += len(chunk)
                if count > settings.review_max_bytes:
                    raise ReviewError("FILE_TOO_LARGE", "文件超过当前 200 MB 上传范围")
                output.write(chunk)
                digest.update(chunk)
        if count == 0:
            raise ReviewError("INVALID_FILE", "文件为空")
        validate_type(path, suffix)
        sha = digest.hexdigest()
        key = f"objects/{sha[:2]}/{sha}{suffix}"
        target = local_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        path.replace(target)
        if settings.storage_backend == "tos":
            try:
                tos_client().put_object_from_file(settings.tos_bucket, key, str(target))
            except ReviewError:
                raise
            except Exception as exc:
                raise ReviewError("PRIVATE_STORAGE_ERROR", "私有对象存储写入失败，素材尚未登记") from exc
        return {"storage_key": key, "sha256": sha, "bytes": count, "mime": MIMES[suffix]}
    finally:
        path.unlink(missing_ok=True)


def external_audio_url(path: Path) -> str:
    import tos

    client = tos_client()
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    key = f"creator-review/audio/{sha}.wav"
    if settings.tos_local_address:
        headers = {
            "Content-Length": str(path.stat().st_size),
            "Content-Type": "audio/wav",
            "Content-MD5": base64.b64encode(hashlib.md5(path.read_bytes()).digest()).decode(),
        }
        upload = client.pre_signed_url(
            tos.HttpMethodType.Http_Method_Put, settings.tos_bucket, key, expires=3600, header=headers
        )
        try:
            with (
                httpx.Client(
                    timeout=settings.provider_timeout_seconds,
                    transport=httpx.HTTPTransport(local_address=settings.tos_local_address),
                    trust_env=False,
                ) as transport,
                path.open("rb") as stream,
            ):
                response = transport.put(upload.signed_url, headers=headers, content=stream)
            if response.status_code >= 400:
                raise ReviewError(
                    "PRIVATE_STORAGE_ERROR", f"私有音轨上传HTTP {response.status_code}，未提交ASR"
                )
        except httpx.HTTPError as exc:
            raise ReviewError(
                "PRIVATE_STORAGE_ERROR",
                "私有音轨上传连接失败，未提交ASR；远端对象状态待核对",
                unknown_remote=True,
            ) from exc
    else:
        try:
            client.put_object_from_file(settings.tos_bucket, key, str(path))
        except Exception as exc:
            raise ReviewError(
                "PRIVATE_STORAGE_ERROR",
                "私有音轨上传失败，未提交ASR；远端对象状态待核对",
                unknown_remote=True,
            ) from exc
    return client.pre_signed_url(
        tos.HttpMethodType.Http_Method_Get, settings.tos_bucket, key, expires=3600
    ).signed_url
