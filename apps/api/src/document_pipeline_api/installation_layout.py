"""Keep program ownership separate from business data, including custom paths."""
import os
from pathlib import Path


def validate_installation_layout(installation_dir: Path, data_dir: Path) -> None:
    install = installation_dir.absolute()
    data = data_dir.absolute()
    for candidate in (install, data):
        for path in (candidate, *candidate.parents):
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise ValueError("安装目录和数据目录不能经过目录链接，请选择独立的普通文件夹。")
    install, data = install.resolve(), data.resolve()
    if install == Path(install.anchor) or install == data or install.is_relative_to(data) or data.is_relative_to(install):
        raise ValueError("程序目录与业务数据目录不能相同或互相包含；请使用独立的安装文件夹。")
    if not install.exists():
        return
    entries = list(install.iterdir())
    if entries and not ((install / "Zhiyi.exe").is_file() and (install / "ZhiyiCLI.exe").is_file()):
        raise ValueError("所选目录已有其他文件，请选择空文件夹或原知意安装目录。")
    for root, directories, files in os.walk(install, followlinks=False):
        for name in directories + files:
            path = Path(root) / name
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise ValueError("安装目录包含链接，已停止文件替换或清理。")
        if {"document-pipeline.db", "queue.db"} & set(files):
            raise ValueError("安装目录内含业务数据库，已停止操作并保留数据；请先将数据移到独立目录。")
