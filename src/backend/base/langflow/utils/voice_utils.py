"""
模块名称：voice_utils

本模块提供语音处理相关的实用函数，主要用于音频重采样和文件操作。
主要功能包括：
- 将24kHz音频帧重采样到16kHz
- 将音频数据异步写入文件

设计背景：在语音处理应用中需要将不同采样率的音频数据进行转换和保存
注意事项：使用numpy和scipy进行音频信号处理
"""

import asyncio
import base64
from pathlib import Path

import numpy as np
from lfx.log import logger
from scipy.signal import resample

# 采样率常量定义
SAMPLE_RATE_24K = 24000  # 24kHz采样率
VAD_SAMPLE_RATE_16K = 16000  # VAD(语音活动检测)使用16kHz采样率
FRAME_DURATION_MS = 20  # 帧持续时间20毫秒
BYTES_PER_SAMPLE = 2  # 每个采样点占用2字节(16位)

# 每帧字节数计算
BYTES_PER_24K_FRAME = int(SAMPLE_RATE_24K * FRAME_DURATION_MS / 1000) * BYTES_PER_SAMPLE  # 24kHz下每帧字节数
BYTES_PER_16K_FRAME = int(VAD_SAMPLE_RATE_16K * FRAME_DURATION_MS / 1000) * BYTES_PER_SAMPLE  # 16kHz下每帧字节数


def resample_24k_to_16k(frame_24k_bytes):
    """将20ms的24kHz音频帧重采样到16kHz。
    
    关键路径（三步）：
    1) 验证输入帧大小是否为960字节（24kHz，20ms）
    2) 将字节转换为numpy数组，使用scipy.signal.resample进行重采样
    3) 将重采样后的数据转回字节格式
    
    异常流：如果输入帧不是恰好960字节，抛出ValueError
    性能瓶颈：音频重采样计算
    排障入口：检查输入的字节数是否符合24kHz音频帧的标准大小
    """
    if len(frame_24k_bytes) != BYTES_PER_24K_FRAME:
        msg = f"Expected exactly {BYTES_PER_24K_FRAME} bytes for 24kHz frame, got {len(frame_24k_bytes)}"
        raise ValueError(msg)

    # Convert bytes to numpy array of int16
    frame_24k = np.frombuffer(frame_24k_bytes, dtype=np.int16)

    # Resample from 24kHz to 16kHz (2/3 ratio)
    # For a 20ms frame, we go from 480 samples to 320 samples
    frame_16k = resample(frame_24k, int(len(frame_24k) * 2 / 3))

    # Convert back to int16 and then to bytes
    frame_16k = frame_16k.astype(np.int16)
    return frame_16k.tobytes()


# def resample_24k_to_16k(frame_24k_bytes: bytes) -> bytes:
#    """
#    Convert one 20ms chunk (960 bytes @ 24kHz) to 20ms @ 16kHz (640 bytes).
#    Raises ValueError if the frame is not exactly 960 bytes.
#    """
#    if len(frame_24k_bytes) != BYTES_PER_24K_FRAME:
#        raise ValueError(
#            f"Expected exactly {BYTES_PER_24K_FRAME} bytes for a 20ms 24k frame, "
#            f"but got {len(frame_24k_bytes)}"
#        )
#    # Convert bytes -> int16 array (480 samples)
#    samples_24k = np.frombuffer(frame_24k_bytes, dtype=np.int16)
#
#    # Resample 24k => 16k (ratio=2/3)
#    # Should get 320 samples out if the chunk was exactly 480 samples in
#    samples_16k = resample_poly(samples_24k, up=2, down=3)
#
#    # Round & convert to int16
#    samples_16k = np.rint(samples_16k).astype(np.int16)
#
#    # Convert back to bytes
#    frame_16k_bytes = samples_16k.tobytes()
#    if len(frame_16k_bytes) != BYTES_PER_16K_FRAME:
#        raise ValueError(
#            f"Expected exactly {BYTES_PER_16K_FRAME} bytes after resampling "
#            f"to 20ms@16kHz, got {len(frame_16k_bytes)}"
#        )
#    return frame_16k_bytes
#


async def write_audio_to_file(audio_base64: str, filename: str = "output_audio.raw") -> None:
    """异步解码base64编码的音频并写入(追加)到文件。
    
    关键路径（三步）：
    1) 解码base64音频数据
    2) 使用asyncio.to_thread在单独线程中执行文件I/O
    3) 记录写入操作的日志
    
    异常流：捕获并记录OSError和base64解码错误
    性能瓶颈：文件I/O操作，使用线程池避免阻塞事件循环
    排障入口：检查base64字符串是否有效，文件路径是否可写
    """
    try:
        audio_bytes = base64.b64decode(audio_base64)
        # Use asyncio.to_thread to perform file I/O without blocking the event loop
        await asyncio.to_thread(_write_bytes_to_file, audio_bytes, filename)
        await logger.ainfo(f"Wrote {len(audio_bytes)} bytes to {filename}")
    except (OSError, base64.binascii.Error) as e:  # type: ignore[attr-defined]
        await logger.aerror(f"Error writing audio to file: {e}")


def _write_bytes_to_file(data: bytes, filename: str) -> None:
    """使用上下文管理器将字节写入文件的帮助函数。
    
    关键路径（单步）：
    1) 以追加二进制模式打开文件并写入数据
    
    异常流：无显式异常处理，异常将向上传播
    性能瓶颈：磁盘I/O操作
    排障入口：检查文件路径是否有效，是否有写入权限
    """
    with Path(filename).open("ab") as f:
        f.write(data)
