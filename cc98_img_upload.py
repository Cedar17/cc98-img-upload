# Copyright 2026 Cedar17
# SPDX-License-Identifier: Apache-2.0

# /// script
# requires-python = ">=3.12"
# dependencies = ["requests>=2.32.4,<3", "markdown-it-py>=4,<5"]
# ///
"""上传 Markdown 内联图片中的本地文件，输出替换后的 Markdown。

用法：uv run cc98_img_upload.py /notes/post.md > /notes/post.cc98.md

图片相对路径以输入 Markdown 所在目录为准。原文只读，输出使用 UTF-8，
只有图片地址被替换；说明、标题、空白、换行和 BOM 保留。
支持 ![说明](路径 "标题")、括号转义、百分号编码及 <带空格的路径>。
代码、注释、外链、data URI、引用式图片、Obsidian 嵌入和 HTML 保持原样。

需要上传时从交互终端输入账号密码，密码隐藏，凭据不落盘。
上传遇到 HTTP 403 时等待 1 秒，仅重试当前图片一次。
任一检查或上传失败则不输出正文；已上传的远端文件无法撤回，重跑可能重传。
请先连接校园网/RVPN。文件类型和大小由 CC98 服务端检查。
输出请使用不同文件：shell 的 > 会在脚本运行前创建或截断目标文件。

退出码：0 成功；1 读取、认证或上传失败；2 参数错误；130 用户取消。
"""

import argparse
import getpass
import mimetypes
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote, unquote, urlsplit
import warnings

from markdown_it import MarkdownIt
from markdown_it.rules_inline import image as image_rule
import requests


TIMEOUT = (10, 30)
TOKEN_URL = "https://openid.cc98.org/connect/token"
UPLOAD_URL = "https://api.cc98.org/file"
# CC98 public OpenID client parameters.
CLIENT_ID = "9a1fd200-8687-44b1-4c20-08d50a96e5cd"
CLIENT_SECRET = "8b53f727-08e2-4509-8857-e34bf92b27f2"


class HttpStatusError(ValueError):
    def __init__(self, stage: str, status_code: int):
        super().__init__(f"{stage}：HTTP {status_code}")
        self.status_code = status_code


def collect_images(source: str, base: Path) -> list[tuple[int, int, Path]]:
    """Locate destination spans without rendering or reformatting the source."""
    md = MarkdownIt("commonmark")
    env = {}
    blocks = md.parse(source, env)
    # Markdown normalizes CRLF/CR; keep original line offsets for replacements.
    starts = [0, *(m.end() for m in re.finditer(r"\r\n?|\n", source))]
    lines = [source[a:b] for a, b in zip(starts, starts[1:] + [len(source)])]
    refs = []

    for block in blocks:
        if block.type != "inline" or block.map is None:
            continue
        fragment = block.content

        def record_image(state, silent):
            opening = state.pos
            matched = image_rule(state, silent)
            # Image alt text is parsed recursively; only rewrite the outer image.
            if not matched or silent or state.src is not fragment:
                return matched
            label_end = md.helpers.parseLinkLabel(state, opening + 1, False)
            pos = label_end + 1
            if fragment[pos:pos + 1] != "(":
                return matched  # Reference-style images remain untouched.
            pos += 1
            while pos < len(fragment) and fragment[pos] in " \t\n":
                pos += 1
            destination = md.helpers.parseLinkDestination(fragment, pos, len(fragment))
            target = destination.str
            if not target or target.startswith("//") or (
                re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target)
                and not Path(target).is_absolute()
            ):
                return matched

            # A link destination cannot span lines. Match its containing content
            # line after the parser has removed list/blockquote/heading prefixes.
            line_start = fragment.rfind("\n", 0, pos) + 1
            line = fragment[line_start:].split("\n", 1)[0]
            row = block.map[0] + fragment.count("\n", 0, pos)
            core = line.lstrip(" \t")
            column = lines[row].replace("\0", "\ufffd").find(core)
            if column < 0:
                raise ValueError("解析失败：无法定位图片在原文中的位置")
            offset = starts[row] + column - (len(line) - len(core)) - line_start
            angled = fragment[pos] == "<"
            start, end = pos + angled, destination.pos - angled
            if source[offset + start:offset + end] != fragment[start:end]:
                raise ValueError("解析失败：图片地址与原文位置不一致")
            try:
                path = (base / unquote(target, errors="strict")).resolve()
            except (OSError, RuntimeError, UnicodeError, ValueError):
                raise ValueError(f"图片路径无效：{target!r}") from None
            refs.append((offset + start, offset + end, path))
            return matched

        md.inline.ruler.at("image", record_image)
        md.inline.parse(fragment, md, env, [])

    return sorted(refs)


def post_json(session, url: str, stage: str, **kwargs):
    """Report failures without including credentials or response bodies."""
    try:
        response = session.post(url, timeout=TIMEOUT, allow_redirects=False, **kwargs)
    except requests.Timeout:
        raise ValueError(f"{stage}：请求超时（连接 10 秒，读取 30 秒）") from None
    except requests.RequestException:
        raise ValueError(f"{stage}：网络请求失败，请检查校园网/RVPN、代理和证书") from None
    if not 200 <= response.status_code < 300:
        raise HttpStatusError(stage, response.status_code)
    try:
        return response.json()
    except ValueError:
        raise ValueError(f"{stage}：响应不是有效 JSON") from None


def login(session) -> str:
    if not sys.stdin.isatty():
        raise ValueError("登录失败：需要交互终端输入账号密码；stdout 可以重定向")
    print("CC98 用户名：", end="", file=sys.stderr, flush=True)
    username = sys.stdin.readline().strip()
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            password = getpass.getpass("CC98 密码：", stream=sys.stderr)
        except (getpass.GetPassWarning, EOFError):
            raise ValueError("登录失败：终端无法隐藏输入密码") from None
    if not username or not password:
        raise ValueError("登录失败：用户名和密码不能为空")
    result = post_json(session, TOKEN_URL, "登录失败", data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "password",
        "username": username,
        "password": password,
        "scope": "cc98-api openid offline_access",
    })
    token = result.get("access_token") if isinstance(result, dict) else None
    if not isinstance(token, str) or not token or not all(33 <= ord(c) <= 126 for c in token):
        raise ValueError("登录失败：响应缺少有效 access token")
    return token


def upload(session, token: str, path: Path) -> str:
    stage = f"上传失败 {str(path)!r}"
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    for attempt in range(2):
        try:
            with path.open("rb") as image:
                result = post_json(session, UPLOAD_URL, stage,
                                   headers={"Authorization": f"Bearer {token}"},
                                   data={"contentType": "multipart/form-data"},
                                   files={"files": (path.name, image, mime)})
            break
        except HttpStatusError as error:
            if error.status_code != 403 or attempt:
                raise
            print(f"上传遇到 HTTP 403，1 秒后重试一次 {str(path)!r}", file=sys.stderr)
            time.sleep(1)
        except OSError as error:
            raise ValueError(f"{stage}：读取文件失败（{error.strerror}）") from None
    url = result[0] if isinstance(result, list) and result else (
        result.get("url") if isinstance(result, dict) else None
    )
    try:
        parsed = urlsplit(url) if isinstance(url, str) else None
        valid = (parsed is not None and parsed.scheme in ("http", "https")
                 and parsed.hostname and parsed.port != 0
                 and not any(c.isspace() or ord(c) < 32 or c == "\\" for c in url))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"{stage}：响应缺少有效图片 URL")
    # Keep a returned URL safe inside either bare or angle-bracket destinations.
    return quote(url, safe="/:?#[]@!$&'*+,;=%~")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("markdown", type=Path, help="UTF-8 Markdown 文件路径")
    args = parser.parse_args(argv)
    try:
        path = args.markdown.absolute()
        try:
            source = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeError) as error:
            detail = error.strerror if isinstance(error, OSError) else "文件不是 UTF-8"
            raise ValueError(f"读取 Markdown 失败 {str(path)!r}：{detail}") from None
        refs = collect_images(source, path.parent)
        unique_paths = dict.fromkeys(ref[2] for ref in refs)
        for image in unique_paths:
            try:
                if not image.is_file():
                    raise ValueError(f"检查图片失败 {str(image)!r}：文件不存在或不是普通文件")
                with image.open("rb"):
                    pass
            except OSError as error:
                raise ValueError(f"检查图片失败 {str(image)!r}：{error.strerror}") from None
        urls = {}
        if unique_paths:
            with requests.Session() as session:
                token = login(session)
                for index, image in enumerate(unique_paths, 1):
                    print(f"上传 [{index}/{len(unique_paths)}] {str(image)!r}", file=sys.stderr)
                    urls[image] = upload(session, token, image)
        for start, end, image in reversed(refs):
            source = source[:start] + urls[image] + source[end:]
        sys.stdout.buffer.write(source.encode("utf-8"))
        sys.stdout.buffer.flush()
        return 0
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # Avoid flushing the broken pipe again during interpreter shutdown.
        with open(os.devnull, "wb") as sink:
            os.dup2(sink.fileno(), sys.stdout.fileno())
        return 1
    except (OSError, ValueError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
