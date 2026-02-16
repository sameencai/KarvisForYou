# -*- coding: utf-8 -*-
"""
KarvisForAll 统一存储接口
多用户版只使用 Lite 本地模式（不支持 OneDrive）。
"""
from local_io import LocalFileIO

IO = LocalFileIO
STORAGE_MODE = "local"
