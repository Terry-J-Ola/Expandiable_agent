from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from agentllm.tools.base import FunctionTool, Tool
from agentllm.types import JsonDict, ToolResult

def _resolve_existing_file(path_value: str) -> Path:
    file_path = Path(path_value).resolve()
    if not file_path.exists():
        raise FileNotFoundError(path_value)
    if file_path.is_dir():
        raise IsADirectoryError(path_value)
    return file_path


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return f"{text[: limit - 3]}..."


def make_calculator_tool() -> Tool:
    """Create a simple arithmetic tool."""

    def handler(arguments: JsonDict) -> ToolResult:
        left = int(arguments["left"])
        right = int(arguments["right"])
        operator = arguments["operator"]

        if operator == "+":
            result = left + right
        elif operator == "-":
            result = left - right
        elif operator == "*":
            result = left * right
        elif operator == "/":
            if right == 0:
                return ToolResult(ok=False, content="division by zero")
            result = left / right
        else:
            return ToolResult(ok=False, content=f"unsupported operator: {operator}")

        return ToolResult(ok=True, content=str(result), data={"value": result})

    return FunctionTool(
        name="calculator",
        description="Safely compute arithmetic on two integers.",
        input_schema={
            "type": "object",
            "properties": {
                "left": {"type": "integer"},
                "operator": {"type": "string", "enum": ["+", "-", "*", "/"]},
                "right": {"type": "integer"},
            },
            "required": ["left", "operator", "right"],
            "additionalProperties": False,
        },
        handler=handler,
    )


def make_utc_now_tool() -> Tool:
    """Create a read-only UTC time tool."""

    def handler(arguments: JsonDict) -> ToolResult:
        del arguments
        now = datetime.now(timezone.utc).isoformat()
        return ToolResult(ok=True, content=now)

    return FunctionTool(
        name="utc_now",
        description="Return the current UTC time in ISO 8601 format.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=handler,
    )


def make_web_search_tool() -> Tool:
    """Create a web search tool backed by SerpAPI."""

    def handler(arguments: JsonDict) -> ToolResult:
        query = str(arguments.get("query", "")).strip()
        top_k = int(arguments.get("top_k", 3))

        if not query:
            return ToolResult(ok=False, content="missing query")

        api_key = os.getenv("SERPAPI_API_KEY", "").strip()
        if not api_key:
            return ToolResult(ok=False, content="missing SERPAPI_API_KEY")

        params = {
            "engine": "google",
            "q": query,
            "gl": "cn",
            "hl": "zh-cn",
            "num": str(top_k),
            "api_key": api_key,
        }
        url = "https://serpapi.com/search.json?" + parse.urlencode(params)

        try:
            req = request.Request(url, method="GET")
            with request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))

            organic_results = payload.get("organic_results", [])
            if not organic_results:
                return ToolResult(
                    ok=True,
                    content="no search results found",
                    data={"query": query, "results": []},
                )

            results = []
            for item in organic_results[:top_k]:
                results.append(
                    {
                        "title": item.get("title", ""),
                        "link": item.get("link", ""),
                        "snippet": item.get("snippet", ""),
                    }
                )

            summary_lines = []
            for idx, item in enumerate(results, start=1):
                summary_lines.append(
                    f"{idx}. {item['title']}\n{item['link']}\n{item['snippet']}"
                )

            return ToolResult(
                ok=True,
                content="\n\n".join(summary_lines),
                data={"query": query, "results": results},
            )
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return ToolResult(
                ok=False,
                content=f"SerpAPI HTTP error {exc.code}: {detail}",
            )
        except error.URLError as exc:
            return ToolResult(
                ok=False,
                content=f"SerpAPI network error: {exc}",
            )
        except json.JSONDecodeError:
            return ToolResult(
                ok=False,
                content="failed to decode SerpAPI response",
            )

    return FunctionTool(
        name="web_search",
        description="Search the web for recent information using SerpAPI.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=handler,
    )


def make_read_file_tool() -> Tool:
    """Create a tool for reading local text files."""

    def handler(arguments: JsonDict) -> ToolResult:
        path_value = str(arguments.get("path", "")).strip()
        encoding = str(arguments.get("encoding", "utf-8")).strip() or "utf-8"

        if not path_value:
            return ToolResult(ok=False, content="missing required argument: path")

        try:
            file_path = _resolve_existing_file(path_value)
            content = file_path.read_text(encoding=encoding)
            return ToolResult(
                ok=True,
                content=content,
                data={"path": str(file_path), "encoding": encoding},
            )
        except FileNotFoundError:
            return ToolResult(ok=False, content=f"file not found: {path_value}")
        except IsADirectoryError:
            return ToolResult(ok=False, content=f"path is a directory: {path_value}")
        except UnicodeDecodeError:
            return ToolResult(
                ok=False,
                content=f"failed to decode file with encoding: {encoding}",
            )
        except OSError as exc:
            return ToolResult(ok=False, content=f"failed to read file: {exc}")

    return FunctionTool(
        name="read_file",
        description="Read the content of a local text file.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "encoding": {"type": "string"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        handler=handler,
    )


def make_read_pdf_file_tool() -> Tool:
    """Create a tool for extracting text from PDF files."""

    def handler(arguments: JsonDict) -> ToolResult:
        path_value = str(arguments.get("path", "")).strip()
        max_pages = int(arguments.get("max_pages", 10))
        max_chars = int(arguments.get("max_chars", 12000))

        if not path_value:
            return ToolResult(ok=False, content="missing required argument: path")

        try:
            from pypdf import PdfReader
        except ImportError:
            return ToolResult(
                ok=False,
                content="missing dependency: install `pypdf` to read PDF files",
            )

        try:
            file_path = _resolve_existing_file(path_value)
            if file_path.suffix.lower() != ".pdf":
                return ToolResult(ok=False, content="path is not a .pdf file")

            reader = PdfReader(str(file_path))
            text_parts: list[str] = []
            pages_to_read = min(len(reader.pages), max_pages)

            for page_index in range(pages_to_read):
                page_text = reader.pages[page_index].extract_text() or ""
                if page_text.strip():
                    text_parts.append(page_text.strip())

            content = "\n\n".join(text_parts).strip()
            if not content:
                return ToolResult(
                    ok=False,
                    content="no extractable text found in PDF",
                    data={"path": str(file_path), "pages_read": pages_to_read},
                )

            content = _truncate_text(content, max_chars)
            return ToolResult(
                ok=True,
                content=content,
                data={
                    "path": str(file_path),
                    "pages_total": len(reader.pages),
                    "pages_read": pages_to_read,
                },
            )
        except FileNotFoundError:
            return ToolResult(ok=False, content=f"file not found: {path_value}")
        except IsADirectoryError:
            return ToolResult(ok=False, content=f"path is a directory: {path_value}")
        except OSError as exc:
            return ToolResult(ok=False, content=f"failed to read PDF file: {exc}")
        except Exception as exc:
            return ToolResult(ok=False, content=f"failed to parse PDF file: {exc}")

    return FunctionTool(
        name="read_pdf_file",
        description="Extract text from a local PDF file.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_pages": {"type": "integer", "minimum": 1, "maximum": 100},
                "max_chars": {"type": "integer", "minimum": 100, "maximum": 50000},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        handler=handler,
    )


def make_read_docx_file_tool() -> Tool:
    """Create a tool for extracting text from Word `.docx` files."""

    def handler(arguments: JsonDict) -> ToolResult:
        path_value = str(arguments.get("path", "")).strip()
        max_chars = int(arguments.get("max_chars", 12000))

        if not path_value:
            return ToolResult(ok=False, content="missing required argument: path")

        try:
            from docx import Document
        except ImportError:
            return ToolResult(
                ok=False,
                content="missing dependency: install `python-docx` to read DOCX files",
            )

        try:
            file_path = _resolve_existing_file(path_value)
            if file_path.suffix.lower() != ".docx":
                return ToolResult(ok=False, content="path is not a .docx file")

            document = Document(str(file_path))
            paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
            content = "\n".join(paragraphs).strip()

            if not content:
                return ToolResult(
                    ok=False,
                    content="no extractable text found in DOCX file",
                    data={"path": str(file_path), "paragraphs": 0},
                )

            content = _truncate_text(content, max_chars)
            return ToolResult(
                ok=True,
                content=content,
                data={
                    "path": str(file_path),
                    "paragraphs": len(paragraphs),
                },
            )
        except FileNotFoundError:
            return ToolResult(ok=False, content=f"file not found: {path_value}")
        except IsADirectoryError:
            return ToolResult(ok=False, content=f"path is a directory: {path_value}")
        except OSError as exc:
            return ToolResult(ok=False, content=f"failed to read DOCX file: {exc}")
        except Exception as exc:
            return ToolResult(ok=False, content=f"failed to parse DOCX file: {exc}")

    return FunctionTool(
        name="read_docx_file",
        description="Extract text from a local Word .docx file.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_chars": {"type": "integer", "minimum": 100, "maximum": 50000},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        handler=handler,
    )


def make_weather_tool() -> Tool:
    """Create a tool for querying weather with the free wttr.in service."""

    def handler(arguments: JsonDict) -> ToolResult:
        city = str(arguments.get("city", "")).strip()
        lang = str(arguments.get("lang", "zh")).strip() or "zh"

        if not city:
            return ToolResult(ok=False, content="missing required argument: city")

        encoded_city = parse.quote(city)
        url = f"https://wttr.in/{encoded_city}?format=j1&lang={lang}"

        try:
            req = request.Request(
                url,
                method="GET",
                headers={"User-Agent": "curl/7.68.0"},
            )
            with request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            current = data["current_condition"][0]
            location = data["nearest_area"][0]
            if lang == "zh" and current.get("lang_zh"):
                weather_text = current["lang_zh"][0]["value"]
            else:
                weather_text = current["weatherDesc"][0]["value"]

            content = (
                f"{location['areaName'][0]['value']}天气\n"
                f"温度: {current['temp_C']}°C (体感 {current['FeelsLikeC']}°C)\n"
                f"天气: {weather_text}\n"
                f"湿度: {current['humidity']}%\n"
                f"风速: {current['windspeedKmph']} km/h"
            )

            return ToolResult(
                ok=True,
                content=content,
                data={
                    "city": city,
                    "temp_c": current["temp_C"],
                    "feels_like_c": current["FeelsLikeC"],
                    "humidity": current["humidity"],
                    "weather_desc": current["weatherDesc"][0]["value"],
                },
            )
        except error.HTTPError as exc:
            if exc.code == 404:
                return ToolResult(ok=False, content=f"city not found: {city}")
            return ToolResult(ok=False, content=f"weather service HTTP error {exc.code}")
        except error.URLError as exc:
            return ToolResult(ok=False, content=f"network error: {exc}")
        except (json.JSONDecodeError, KeyError) as exc:
            return ToolResult(ok=False, content=f"failed to parse weather response: {exc}")

    return FunctionTool(
        name="get_weather",
        description="Get the current weather for a city using wttr.in.",
        input_schema={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name, such as Beijing, Shanghai, or New York.",
                },
                "lang": {
                    "type": "string",
                    "description": "Language code, such as zh or en.",
                    "default": "zh",
                },
            },
            "required": ["city"],
            "additionalProperties": False,
        },
        handler=handler,
    )


def make_write_text_tool() -> Tool:
    """Create a tool for writing local text files."""

    def handler(arguments: JsonDict) -> ToolResult:
        path_value = str(arguments.get("path", "")).strip()
        content = str(arguments.get("content", ""))
        encoding = str(arguments.get("encoding", "utf-8")).strip() or "utf-8"
        overwrite = bool(arguments.get("overwrite", False))

        if not path_value:
            return ToolResult(ok=False, content="missing required argument: path")

        try:
            file_path = Path(path_value).resolve()

            dangerous_roots = (
                Path("/etc"),
                Path("/sys"),
                Path("/proc"),
                Path("/dev"),
                Path("C:/Windows").resolve(),
            )
            for dangerous_root in dangerous_roots:
                if file_path == dangerous_root or dangerous_root in file_path.parents:
                    return ToolResult(
                        ok=False,
                        content=f"refusing to write inside protected directory: {dangerous_root}",
                    )

            if file_path.exists() and file_path.is_dir():
                return ToolResult(ok=False, content=f"path is a directory: {path_value}")

            if file_path.exists() and not overwrite:
                return ToolResult(
                    ok=False,
                    content=f"file already exists, set overwrite=true to replace it: {path_value}",
                )

            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding=encoding)

            return ToolResult(
                ok=True,
                content=f"wrote file: {file_path}",
                data={
                    "path": str(file_path),
                    "size": len(content),
                    "encoding": encoding,
                },
            )
        except OSError as exc:
            return ToolResult(ok=False, content=f"failed to write file: {exc}")
        except Exception as exc:
            return ToolResult(ok=False, content=f"unexpected error: {exc}")

    return FunctionTool(
        name="write_file",
        description="Write content to a local text file and optionally overwrite it.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the target file."},
                "content": {"type": "string", "description": "Text to write."},
                "encoding": {
                    "type": "string",
                    "description": "File encoding, default is utf-8.",
                    "default": "utf-8",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Whether to overwrite an existing file.",
                    "default": False,
                },
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        handler=handler,
    )
