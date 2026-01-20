"""
模块名称：storage_utils

本模块提供同时兼容本地文件与对象存储的文件读取与校验工具。
主要功能包括：
- 功能1：解析对象存储路径并读取字节/文本。
- 功能2：获取文件大小与存在性判断。
- 功能3：通过魔数检测图片真实格式并验证扩展名。

使用场景：组件需在 `local` 与 `s3` 存储之间无感切换时。
关键组件：
- 函数 `parse_storage_path`
- 函数 `read_file_bytes` / `read_file_text`
- 函数 `detect_image_type_from_bytes`
- 函数 `validate_image_content_type`

设计背景：组件需要在 `local` 与 `s3` 间无感切换。
注意事项：`s3` 路径格式为 `flow_id/filename`；无法识别的图片类型会被放行。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lfx.services.deps import get_settings_service, get_storage_service
from lfx.utils.async_helpers import run_until_complete

if TYPE_CHECKING:
    from collections.abc import Callable

    from lfx.services.storage.service import StorageService

# 注意：路径解析约束，S3 采用 `flow_id/filename` 两段格式。
EXPECTED_PATH_PARTS = 2  # 注意：路径格式为 `flow_id/filename`


def parse_storage_path(path: str) -> tuple[str, str] | None:
    """解析对象存储路径为 `(flow_id, filename)`。

    契约：仅针对 `s3` 路径格式；无效格式返回 `None`。
    关键路径：检查分隔符 -> 拆分两段 -> 校验非空。
    决策：
    问题：调用方需要显式区分 `flow_id` 与文件名。
    方案：以首个 `/` 分割，并要求两段均非空。
    代价：不支持更深层级路径。
    重评：当存储层支持多级目录或前缀时。
    """
    if not path or "/" not in path:
        return None

    parts = path.split("/", 1)
    if len(parts) != EXPECTED_PATH_PARTS or not parts[0] or not parts[1]:
        return None

    return parts[0], parts[1]


async def read_file_bytes(
    file_path: str,
    storage_service: StorageService | None = None,
    resolve_path: Callable[[str], str] | None = None,
) -> bytes:
    """从对象存储或本地文件系统读取字节内容。

    契约：`file_path` 可为 `flow_id/filename` 或本地路径；返回字节数组。
    关键路径：
    1) `s3` 模式解析路径并调用 `storage_service.get_file`；
    2) 本地路径通过 `resolve_path` 解析；
    3) 校验存在性后读取字节。
    异常流：路径无效抛 `ValueError`；文件不存在抛 `FileNotFoundError`。
    决策：
    问题：组件需要统一读路径，避免上层区分存储类型。
    方案：在函数内部根据 `storage_type` 分流。
    代价：运行期多一次分支判断。
    重评：当存储服务统一提供读接口并可直接注入时。
    """
    settings = get_settings_service().settings

    if settings.storage_type == "s3":
        parsed = parse_storage_path(file_path)
        if not parsed:
            msg = f"Invalid S3 path format: {file_path}. Expected 'flow_id/filename'"
            raise ValueError(msg)

        if storage_service is None:
            storage_service = get_storage_service()

        flow_id, filename = parsed
        return await storage_service.get_file(flow_id, filename)

    # 注意：本地存储可使用 `resolve_path` 处理相对路径。
    if resolve_path:
        file_path = resolve_path(file_path)

    path_obj = Path(file_path)
    if not path_obj.exists():
        msg = f"File not found: {file_path}"
        raise FileNotFoundError(msg)

    return path_obj.read_bytes()


async def read_file_text(
    file_path: str,
    encoding: str = "utf-8",
    storage_service: StorageService | None = None,
    resolve_path: Callable[[str], str] | None = None,
    newline: str | None = None,
) -> str:
    r"""从对象存储或本地文件系统读取文本内容。

    契约：支持自定义 `encoding`；`newline=""` 时统一换行为 `\n`。
    关键路径：
    1) `s3` 模式读取字节并解码；
    2) 可选换行归一化；
    3) 本地路径按 `newline` 模式读取。
    异常流：文件不存在抛 `FileNotFoundError`。
    决策：
    问题：`s3` 不支持直接文本读取，需要字节解码。
    方案：先读取字节再解码，并手动做换行归一化。
    代价：对大文件会增加内存占用。
    重评：当存储服务提供流式文本读取时。
    """
    settings = get_settings_service().settings

    if settings.storage_type == "s3":
        content = await read_file_bytes(file_path, storage_service, resolve_path)
        text = content.decode(encoding)
        # 注意：`newline=""` 时在 S3 模式下手动归一化换行，保持与本地一致。
        if newline == "":
            # 实现：统一 `\r\n`/`\r` 为 `\n`。
            text = text.replace("\r\n", "\n").replace("\r", "\n")
        return text
    # 注意：本地存储可使用 `resolve_path` 处理相对路径。
    if resolve_path:
        file_path = resolve_path(file_path)

    path_obj = Path(file_path)
    if newline is not None:
        with path_obj.open(newline=newline, encoding=encoding) as f:  # noqa: ASYNC230
            return f.read()
    return path_obj.read_text(encoding=encoding)


def get_file_size(file_path: str, storage_service: StorageService | None = None) -> int:
    """获取文件大小（字节）。

    契约：`s3` 模式通过存储服务查询；本地路径直接 `stat()`。
    关键路径：解析路径 -> 调用存储服务或 `stat()`。
    异常流：路径格式错误抛 `ValueError`；文件不存在抛 `FileNotFoundError`。
    决策：
    问题：多数调用方在同步上下文中需要文件大小。
    方案：提供同步包装，并在 `s3` 情况下调用异步接口。
    代价：同步等待可能阻塞线程。
    重评：当上层全面迁移到异步调用时。
    """
    settings = get_settings_service().settings

    if settings.storage_type == "s3":
        parsed = parse_storage_path(file_path)
        if not parsed:
            msg = f"Invalid S3 path format: {file_path}. Expected 'flow_id/filename'"
            raise ValueError(msg)

        if storage_service is None:
            storage_service = get_storage_service()

        flow_id, filename = parsed
        return run_until_complete(storage_service.get_file_size(flow_id, filename))

    # 注意：本地路径直接走 `stat()`，不做额外解析。
    path_obj = Path(file_path)
    if not path_obj.exists():
        msg = f"File not found: {file_path}"
        raise FileNotFoundError(msg)

    return path_obj.stat().st_size


def file_exists(file_path: str, storage_service: StorageService | None = None) -> bool:
    """判断文件是否存在（存储服务或本地）。

    契约：返回布尔值；内部复用 `get_file_size`。
    关键路径：调用 `get_file_size` 并捕获异常。
    决策：
    问题：不同存储类型的存在性判断逻辑不同。
    方案：通过 `get_file_size` 统一判定。
    代价：存在性判断会触发一次大小查询。
    重评：当存储服务提供轻量 `exists` 接口时。
    """
    try:
        get_file_size(file_path, storage_service)
    except (FileNotFoundError, ValueError):
        return False
    else:
        return True


# 注意：常见图片格式的魔数字节签名。
MIN_IMAGE_HEADER_SIZE = 12  # 注意：检测图片类型所需的最小字节数。

IMAGE_SIGNATURES: dict[str, list[tuple[bytes, int]]] = {
    "jpeg": [(b"\xff\xd8\xff", 0)],
    "jpg": [(b"\xff\xd8\xff", 0)],
    "png": [(b"\x89PNG\r\n\x1a\n", 0)],
    "gif": [(b"GIF87a", 0), (b"GIF89a", 0)],
    "webp": [(b"RIFF", 0)],  # 注意：WebP 以 RIFF 开头，偏移 8 为 WEBP。
    "bmp": [(b"BM", 0)],
    "tiff": [(b"II*\x00", 0), (b"MM\x00*", 0)],  # 注意：TIFF 支持大小端签名。
}


def detect_image_type_from_bytes(content: bytes) -> str | None:
    """通过魔数字节检测图片真实类型。

    契约：输入至少 12 字节；返回类型字符串或 `None`。
    关键路径：优先匹配 WebP -> 遍历签名表。
    决策：
    问题：扩展名可能被伪装，需以内容为准。
    方案：使用魔数签名匹配。
    代价：无法识别的格式会返回 `None`。
    重评：当引入更完整的图片识别库时。
    """
    if len(content) < MIN_IMAGE_HEADER_SIZE:
        return None

    # 注意：WebP 需要同时校验 RIFF 与偏移 8 的 WEBP 标记。
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"

    # 实现：遍历其他格式的签名。
    for image_type, signatures in IMAGE_SIGNATURES.items():
        if image_type == "webp":
            continue  # 注意：WebP 已在前面单独处理。
        for signature, offset in signatures:
            if content[offset : offset + len(signature)] == signature:
                return image_type

    return None


def validate_image_content_type(
    file_path: str,
    content: bytes | None = None,
    storage_service: StorageService | None = None,
    resolve_path: Callable[[str], str] | None = None,
) -> tuple[bool, str | None]:
    """校验图片扩展名与内容类型是否一致。

    契约：返回 `(is_valid, error_message)`；非图片扩展名直接放行。
    关键路径：
    1) 根据扩展名判断是否需要校验；
    2) 读取内容并检测真实类型；
    3) 比较并生成可执行的修复提示。
    异常流：读取失败时放行，避免误判阻塞。
    决策：
    问题：扩展名与内容不一致会导致下游 API 报错。
    方案：仅在能明确识别类型时拒绝，否则放行。
    代价：未知格式可能在下游才失败。
    重评：当引入更强的格式检测能力时。
    """
    # 实现：仅依赖扩展名判断是否进入校验流程。
    path_obj = Path(file_path)
    extension = path_obj.suffix[1:].lower() if path_obj.suffix else ""

    # 注意：非图片扩展名直接放行，避免影响其他文件类型。
    image_extensions = {"jpeg", "jpg", "png", "gif", "webp", "bmp", "tiff"}
    if extension not in image_extensions:
        return True, None

    # 注意：未提供内容时按存储类型读取字节。
    if content is None:
        try:
            content = run_until_complete(read_file_bytes(file_path, storage_service, resolve_path))
        except (FileNotFoundError, ValueError):
            # 注意：读取失败先放行，避免误报导致阻塞。
            return True, None

    # 实现：通过魔数检测真实图片类型。
    detected_type = detect_image_type_from_bytes(content)

    # 注意：无法识别类型时视为无效图片，返回明确错误信息。
    if detected_type is None:
        return False, (
            f"File '{path_obj.name}' has extension '.{extension}' but its content "
            f"is not a valid image format. The file may be corrupted, empty, or not a real image."
        )

    # 实现：归一化扩展名（`jpg` == `jpeg`）。
    extension_normalized = "jpeg" if extension == "jpg" else extension
    detected_normalized = "jpeg" if detected_type == "jpg" else detected_type

    if extension_normalized != detected_normalized:
        return False, (
            f"File '{path_obj.name}' has extension '.{extension}' but contains "
            f"'{detected_type.upper()}' image data. This mismatch will cause API errors. "
            f"Please rename the file with the correct extension '.{detected_type}' or "
            f"re-save it in the correct format."
        )

    return True, None
