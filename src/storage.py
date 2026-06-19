# -*- coding: utf-8 -*-
"""
KarvisForAll V12 统一存储接口
支持 Local / OneDrive 两种后端，通过工厂方法按用户配置创建。

Duck Typing 协议 — LocalFileIO 和 OneDriveIO 都实现以下方法：
  get_token()
  read_text(path) -> str
  write_text(path, content) -> bool
  read_json(path) -> dict
  write_json(path, data) -> bool
  append_to_section(path, header, content) -> bool
  append_to_quick_notes(path, msg) -> bool
  upload_binary(path, data, content_type?) -> bool
  download_binary(path) -> bytes|None
  list_children(folder) -> list
"""
import sys

def _log(msg):
    from logger import log
    log(msg)


def create_storage(storage_mode: str, onedrive_config: dict = None):
    """工厂方法：根据模式创建存储实例。

    Args:
        storage_mode: "local" | "onedrive"
        onedrive_config: OneDrive 凭证字典（仅 onedrive 模式需要）
            {client_id, client_secret, refresh_token, obsidian_base}

    Returns:
        LocalFileIO 类 或 OneDriveIO 实例
    """
    if storage_mode == "onedrive":
        if not onedrive_config:
            _log("[Storage] onedrive 模式缺少 onedrive_config，回退到 local")
            from local_io import LocalFileIO
            return LocalFileIO
        from onedrive_io import OneDriveIO
        return OneDriveIO(onedrive_config)
    else:
        from local_io import LocalFileIO
        return LocalFileIO
