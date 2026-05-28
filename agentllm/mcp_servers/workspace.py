from __future__ import annotations

from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "This server requires the official MCP Python SDK.\n"
        "Install it with: pip install 'mcp[cli]'"
    ) from exc
"""
在这里，生成真正的底层工具逻辑，并且把他们打包成MCP server，
好比本地的handler被FunctionTool打包成真正的tool。
"""

# MCP 服务器实例，启用 JSON 响应格式
mcp = FastMCP("Workspace File Server", json_response=True)
"""
@mcp.tool()的作用：
    1、注册工具，使客户端可以通过tools/list看到这个工具
    2、自动将JSON-RPC参数转换为函数参数
    3、将返回值自动转化为JSON格式
    4、异步支持
    5、根据函数生成工具的JSON Schema,这个东西是用来让客户端知道工具需要什么参数
"""
# 项目根目录（当前文件向上追溯两级）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 工作区根目录，所有文件操作限制在此目录下
WORKSPACE_ROOT = PROJECT_ROOT


def _resolve_workspace_path(path_str: str) -> Path:
    """
    将相对路径解析为工作区内的绝对路径。

    Args:
        path_str: 相对于工作区的路径字符串。

    Returns:
        解析后的绝对路径。

    Raises:
        ValueError: 路径超出工作区范围（路径遍历攻击防护）。
    """
    path = (WORKSPACE_ROOT / path_str).resolve()
    if path != WORKSPACE_ROOT and WORKSPACE_ROOT not in path.parents:
        raise ValueError("path is outside workspace")
    return path


@mcp.tool()
async def read_text_file(path: str, encoding: str = "utf-8") -> str:
    """
    读取指定路径的文本文件内容。

    Args:
        path: 相对于工作区的文件路径。
        encoding: 文件编码，默认为 utf-8。

    Returns:
        文件文本内容。

    Raises:
        FileNotFoundError: 文件不存在。
        IsADirectoryError: 路径是目录而非文件。
        ValueError: 路径超出工作区范围。
    """
    file_path = _resolve_workspace_path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"file not found: {path}")
    if file_path.is_dir():
        raise IsADirectoryError(f"path is a directory: {path}")
    return file_path.read_text(encoding=encoding)


@mcp.tool()
async def list_files(directory: str = ".") -> list[str]:
    """
    列出指定目录下的所有文件和子目录。

    Args:
        directory: 相对于工作区的目录路径，默认为当前目录。

    Returns:
        目录项的相对路径列表（按字母排序）。

    Raises:
        FileNotFoundError: 目录不存在。
        NotADirectoryError: 路径不是目录。
        ValueError: 路径超出工作区范围。
    """
    dir_path = _resolve_workspace_path(directory)
    if not dir_path.exists():
        raise FileNotFoundError(f"directory not found: {directory}")
    if not dir_path.is_dir():
        raise NotADirectoryError(f"not a directory: {directory}")
    return [str(child.relative_to(WORKSPACE_ROOT)) for child in sorted(dir_path.iterdir())]


@mcp.tool()
async def search_text(query: str, directory: str = ".") -> list[dict[str, str]]:
    """
    在指定目录及其子目录中搜索包含查询文本的文件行。

    Args:
        query: 要搜索的文本字符串。
        directory: 相对于工作区的起始目录，默认为当前目录。

    Returns:
        匹配结果列表，每项包含文件路径、行号和行内容。

    Raises:
        FileNotFoundError: 目录不存在。
        NotADirectoryError: 路径不是目录。
        ValueError: 路径超出工作区范围。
    """
    dir_path = _resolve_workspace_path(directory)
    if not dir_path.exists():
        raise FileNotFoundError(f"directory not found: {directory}")
    if not dir_path.is_dir():
        raise NotADirectoryError(f"not a directory: {directory}")

    matches: list[dict[str, str]] = []
    for file_path in dir_path.rglob("*"):
        if not file_path.is_file():
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            # 跳过无法解码的二进制文件或无法读取的文件
            continue

        for line_number, line in enumerate(text.splitlines(), start=1):
            if query in line:
                matches.append(
                    {
                        "path": str(file_path.relative_to(WORKSPACE_ROOT)),
                        "line_number": str(line_number),
                        "line": line,
                    }
                )
    return matches


if __name__ == "__main__":
    # 以 stdio 传输方式启动 MCP 服务器
    mcp.run(transport="stdio")
