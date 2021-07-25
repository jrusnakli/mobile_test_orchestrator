import asyncio
import logging
import os
import platform
import shutil
from pathlib import Path
from typing import Iterator, AsyncIterator, Any


logging.basicConfig(level=logging.DEBUG if os.environ.get("MTO_LOG_DEBUG") else logging.WARNING)


async def _async_iter_adapter(iter: Iterator[Any]) -> AsyncIterator[Any]:
    for item in iter:
        yield item
        await asyncio.sleep(0)


def _adb_path() -> Path:
    """
    Locate adb location
    """
    home_dir = Path(os.path.expanduser("~"))
    ANDROID_SDK_ROOT_STD_LOC = {
        "linux": home_dir / "Android" / "Sdk",
        "darwin": home_dir / "Library" / "Android"/ "sdk",
        "win32": home_dir / "Android" / "Sdk",
    }.get(platform.system().lower())
    adb_cmd = "adb.exe" if platform.system().lower == 'win32' else "adb"
    sdk_root = os.environ.get("ANDROID_SDK_ROOT")
    if sdk_root:
        sdk_path = Path(sdk_root)
        adb_path = sdk_path / "platform-tools" / adb_cmd
        if not adb_path.exists():
            raise SystemError("Invalid or incomplete Android SDK location in ANDROID_SDK_RROT env var; "
                              f"{str(adb_path)} does not exist")
        return adb_path
    elif shutil.which(adb_cmd):
        return Path(shutil.which(adb_cmd))
    elif ANDROID_SDK_ROOT_STD_LOC and ANDROID_SDK_ROOT_STD_LOC.exists():
        adb_path = ANDROID_SDK_ROOT_STD_LOC / "platform-tools" / adb_cmd
        return adb_path
    else:
        raise EnvironmentError("ANDROID_SDK_ROOT not set and adb not found in path nor in standard SDK user location")


ADB_PATH = _adb_path()
__all__ = [_async_iter_adapter, ADB_PATH]
