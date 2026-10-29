# Copyright 2026 Cedar17
# SPDX-License-Identifier: Apache-2.0

# /// script
# requires-python = ">=3.12"
# dependencies = ["requests>=2.32.4,<3", "markdown-it-py>=4,<5"]
# ///
"""Offline regression tests: uv run test/test_cc98_img_upload.py -v"""

import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cc98_img_upload as app


class TTYInput(io.StringIO):
    def isatty(self):
        return True


def response(data=None, status=200):
    result = Mock(status_code=status)
    result.json.return_value = data
    return result


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.notes = self.root / "笔记"
        self.notes.mkdir()
        self.note = self.notes / "post.md"
        self.session_factory = self.enterContext(patch.object(app.requests, "Session"))
        self.session = self.session_factory.return_value.__enter__.return_value
        self.session.post.side_effect = AssertionError("Unexpected network request")
        self.password = self.enterContext(patch.object(app.getpass, "getpass",
                                                     return_value="private-password"))

    def image(self, name="a.png"):
        path = self.notes / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"sample image bytes")
        return path

    def invoke(self, source=None, *, stdin=None, argv=None):
        if source is not None:
            self.note.write_bytes(source.encode("utf-8") if isinstance(source, str) else source)
        data, errors = io.BytesIO(), io.StringIO()
        output = io.TextIOWrapper(data, encoding="utf-8")
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), \
                patch.object(app.sys, "stdin", stdin if stdin is not None else TTYInput("user\n")):
            try:
                code = app.main([str(self.note)] if argv is None else argv)
            except SystemExit as error:
                code = error.code
            output.flush()
            return code, data.getvalue(), errors.getvalue()

    def succeed(self, *urls):
        self.session.post.side_effect = [response({"access_token": "private-token"}),
                                         *(response([url]) for url in urls)]

    def assert_failed(self, result, message):
        code, output, errors = result
        self.assertEqual(code, 1)
        self.assertEqual(output, b"")
        self.assertIn(message, errors)
        self.assertNotIn("private-password", errors)
        self.assertNotIn("private-token", errors)

    def test_destination_spans_and_decoding(self):
        source = ('![图](images/中文%20图.png "标题")\n'
                  r'![括号](images/a\(1\).png)' '\n'
                  '![空格](<images/空 格.png>)\n'
                  '![百分号](images/a%2520b.png)\n'
                  '![实体](images/a&amp;b.png)\n')
        refs = app.collect_images(source, self.notes)
        self.assertEqual([source[a:b] for a, b, _ in refs],
                         ["images/中文%20图.png", r"images/a\(1\).png", "images/空 格.png",
                          "images/a%2520b.png", "images/a&amp;b.png"])
        self.assertEqual([p.name for _, _, p in refs],
                         ["中文 图.png", "a(1).png", "空 格.png", "a%20b.png", "a&b.png"])

    def test_code_comments_escapes_and_unsupported_syntax(self):
        source = ('`![图](a.png)` 和 ``![图](a.png)``\n\n'
                  '```md\n![图](a.png)\n```\n\n'
                  '~~~md\n![图](a.png)\n~~~\n\n'
                  '    ![图](a.png)\n\n'
                  '<!-- ![图](a.png) -->\n\n'
                  '正文 <!-- ![图](a.png) --> 结束\n\n'
                  r'\![图](a.png)' '\n\n'
                  '[普通链接](a.png) ![[a.png]] <img src="a.png">\n\n'
                  '![引用][pic] ![pic][] ![pic]\n\n'
                  '[pic]: a.png\n\n'
                  '<div>\n![图](a.png)\n</div>\n')
        result = self.invoke(source, stdin=io.StringIO())
        self.assertEqual(result[:2], (0, source.encode()))
        self.session_factory.assert_not_called()
        self.password.assert_not_called()

    def test_remote_uris_are_not_uploaded(self):
        source = ('![a](https://example.com/a.png) ![b](http://example.com/b.png)\n'
                  '![c](//example.com/a.png) ![d](data:image/png;base64,AAAA)\n'
                  '![e](file:///tmp/a.png) ![f](custom:abc)\n')
        self.assertEqual(self.invoke(source, stdin=io.StringIO())[:2], (0, source.encode()))
        self.session_factory.assert_not_called()

    def test_quotes_lists_headings_and_linked_images(self):
        source = ('# ![标题](a.png) #\n\n'
                  '> ![引用](b.png)\n>\n> - ![列表](c.png)\n>   续行 ![续](d.png)\n\n'
                  '- `![示例](code.png)` 和 [![图](e.png)](https://example.com)\n\n'
                  '![下划线标题](f.png)\n===================\n')
        refs = app.collect_images(source, self.notes)
        self.assertEqual([source[a:b] for a, b, _ in refs],
                         ["a.png", "b.png", "c.png", "d.png", "e.png", "f.png"])

    def test_multiline_image_and_tab_indentation(self):
        source = ('> - ![第一行\n>   第二行](\n>   <images/a b.png>\n>   "标题"\n>   )\n\n'
                  '- \t![图](b.png)\n')
        refs = app.collect_images(source, self.notes)
        self.assertEqual([source[a:b] for a, b, _ in refs], ["images/a b.png", "b.png"])

    def test_same_markup_in_code_and_prose(self):
        self.image()
        source = '`![图](a.png)` 和 ![图](a.png)\n'
        self.succeed("https://file.cc98.org/a.png")
        self.assertEqual(self.invoke(source)[:2],
                         (0, '`![图](a.png)` 和 ![图](https://file.cc98.org/a.png)\n'.encode()))

    def test_nested_alt_images_only_upload_outer_destination(self):
        source = '![外层 ![内层](inner.png)](outer.png)\n'
        refs = app.collect_images(source, self.notes)
        self.assertEqual([source[a:b] for a, b, _ in refs], ["outer.png"])
        self.assertEqual(app.collect_images('![![内层](inner.png)][ref]\n\n[ref]: outer.png',
                                            self.notes), [])

    def test_preserve_bom_crlf_title_and_no_final_newline(self):
        self.image("a b.png")
        source = '\ufeff# 标题\r\n\r\n  ![说明](<a b.png>  \'标题\')  \r\n尾部'
        self.succeed("https://file.cc98.org/a.png")
        code, output, _ = self.invoke(source)
        self.assertEqual(code, 0)
        self.assertEqual(output, source.replace("a b.png", "https://file.cc98.org/a.png").encode())
        self.assertEqual(self.note.read_bytes(), source.encode())

    def test_no_images_preserve_all_newlines_and_unicode(self):
        for source in ["", "\ufeff# 标题\r\n", "a\rb\r", "a\nb", "a\u2028b\x85c\n"]:
            with self.subTest(source=source):
                self.assertEqual(self.invoke(source, stdin=io.StringIO())[:2], (0, source.encode()))
        self.session_factory.assert_not_called()

    def test_paths_follow_markdown_not_working_directory(self):
        image = self.image("assets/中文 图.png")
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        source = f'![a](assets/中文%20图.png) ![b](../笔记/assets/中文%20图.png) ![c](<{image}>)\n'
        self.succeed("https://file.cc98.org/a.png")
        with contextlib.chdir(elsewhere):
            code, output, _ = self.invoke(source)
        self.assertEqual(code, 0)
        self.assertEqual(output.count(b"https://file.cc98.org/a.png"), 3)
        self.assertEqual(self.session.post.call_count, 2)

    def test_symlink_aliases_are_deduplicated(self):
        image = self.image()
        (self.notes / "alias.png").symlink_to(image)
        self.succeed("https://file.cc98.org/a.png")
        code, output, _ = self.invoke("![a](a.png) ![b](./a.png) ![c](alias.png)")
        self.assertEqual(code, 0)
        self.assertEqual(output.count(b"https://file.cc98.org/a.png"), 3)
        self.assertEqual(self.session.post.call_count, 2)

    def test_missing_image_is_detected_before_login(self):
        self.image()
        self.assert_failed(self.invoke("![a](a.png) ![missing](missing.png)"), "missing.png")
        self.session_factory.assert_not_called()
        self.password.assert_not_called()

    def test_directory_image_is_rejected_before_login(self):
        self.assert_failed(self.invoke("![directory](.)"), "不是普通文件")
        self.session_factory.assert_not_called()

    def test_unreadable_image_is_detected_before_login(self):
        self.image()
        self.note.write_text("![图](a.png)", encoding="utf-8")
        original_open = Path.open

        def denied(path, *args, **kwargs):
            if path.suffix == ".png":
                raise PermissionError(13, "Permission denied")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", denied):
            self.assert_failed(self.invoke(), "Permission denied")
        self.session_factory.assert_not_called()

    def test_invalid_utf8_is_detected_before_login(self):
        self.assert_failed(self.invoke(b"\xff\xfe"), "不是 UTF-8")
        self.session_factory.assert_not_called()

    def test_missing_markdown(self):
        self.assert_failed(self.invoke(), "读取 Markdown 失败")
        self.session_factory.assert_not_called()

    def test_requires_terminal_for_login(self):
        self.image()
        self.assert_failed(self.invoke("![图](a.png)", stdin=io.StringIO()), "需要交互终端")
        self.session.post.assert_not_called()
        self.password.assert_not_called()

    def test_password_input_never_falls_back_to_echo(self):
        self.image()
        self.password.side_effect = app.getpass.GetPassWarning("Cannot hide input")
        self.assert_failed(self.invoke("![图](a.png)"), "无法隐藏输入密码")
        self.session.post.assert_not_called()

    def test_login_and_multipart_contract(self):
        image = self.image()

        def post(url, **kwargs):
            self.assertEqual(kwargs["timeout"], (10, 30))
            self.assertFalse(kwargs["allow_redirects"])
            if url == app.TOKEN_URL:
                self.assertEqual(kwargs["data"], {
                    "client_id": app.CLIENT_ID, "client_secret": app.CLIENT_SECRET,
                    "grant_type": "password", "username": "user", "password": "private-password",
                    "scope": "cc98-api openid offline_access",
                })
                return response({"access_token": "private-token", "refresh_token": "ignored"})
            self.assertEqual(url, app.UPLOAD_URL)
            self.assertEqual(kwargs["headers"], {"Authorization": "Bearer private-token"})
            self.assertEqual(kwargs["data"], {"contentType": "multipart/form-data"})
            filename, handle, mime = kwargs["files"]["files"]
            self.assertEqual((filename, mime), ("a.png", "image/png"))
            self.assertEqual(handle.read(), image.read_bytes())
            return response({"url": "https://file.cc98.org/a.png"})

        self.session.post.side_effect = post
        result = self.invoke('![图](a.png "标题")')
        self.assertEqual(result[:2], (0, '![图](https://file.cc98.org/a.png "标题")'.encode()))

    def test_multiple_images_keep_reference_order(self):
        self.image("a.png")
        self.image("b.png")
        self.succeed("https://file.cc98.org/a.png", "https://file.cc98.org/b.png")
        code, output, _ = self.invoke("![a](a.png) ![b](b.png) ![a2](a.png)")
        self.assertEqual(code, 0)
        self.assertEqual(output, b"![a](https://file.cc98.org/a.png) ![b](https://file.cc98.org/b.png) ![a2](https://file.cc98.org/a.png)")
        self.assertEqual(self.session.post.call_count, 3)

    def test_authentication_http_errors_do_not_expose_response(self):
        self.image()
        for status in [400, 401, 403, 500, 302]:
            with self.subTest(status=status):
                self.session.post.side_effect = [response({"error": "private-password"}, status)]
                self.assert_failed(self.invoke("![图](a.png)"), f"HTTP {status}")

    def test_login_requires_nonempty_string_token(self):
        self.image()
        for data in [{}, {"access_token": ""}, {"access_token": "has space"},
                     {"access_token": "null\0byte"}, {"access_token": "非 ASCII"},
                     {"access_token": 123}, [], None]:
            with self.subTest(data=data):
                self.session.post.side_effect = [response(data)]
                self.assert_failed(self.invoke("![图](a.png)"), "access token")

    def test_invalid_json_during_login_and_upload(self):
        self.image()
        invalid = response()
        invalid.json.side_effect = ValueError("private-token private-password")
        for replies in [[invalid], [response({"access_token": "private-token"}), invalid]]:
            with self.subTest(upload=len(replies) == 2):
                self.session.post.side_effect = replies
                self.assert_failed(self.invoke("![图](a.png)"), "不是有效 JSON")

    def test_network_failures_do_not_retry_or_expose_exception(self):
        self.image()
        for exception in [requests.Timeout("private-token private-password"),
                          requests.ConnectionError("private-token private-password")]:
            for upload in [False, True]:
                with self.subTest(exception=type(exception), upload=upload):
                    self.session.post.reset_mock()
                    self.session.post.side_effect = ([response({"access_token": "private-token"})]
                                                     if upload else []) + [exception]
                    result = self.invoke("![图](a.png)")
                    self.assert_failed(result, "超时" if isinstance(exception, requests.Timeout) else "网络请求失败")
                    self.assertEqual(self.session.post.call_count, 2 if upload else 1)

    def test_upload_failure_stops_remaining_images_and_output(self):
        for name in ["a.png", "b.png", "c.png"]:
            self.image(name)
        for failure in [response(status=401), response(status=403), response(status=413)]:
            with self.subTest(status=failure.status_code):
                self.session.post.reset_mock()
                self.session.post.side_effect = [response({"access_token": "private-token"}),
                                                 response(["https://file.cc98.org/a.png"]), failure]
                self.assert_failed(self.invoke("![a](a.png) ![b](b.png) ![c](c.png)"), "b.png")
                self.assertEqual(self.session.post.call_count, 3)

    def test_malformed_upload_responses(self):
        self.image()
        for data in [[], {}, None, "https://file.cc98.org/a.png", [123], {"url": ""},
                     {"url": "javascript:alert(1)"}, ["https://"], ["/relative"],
                     ["https://example.com/a\n.png"], ["https://[bad"], ["https://example.com:99999/a"]]:
            with self.subTest(data=data):
                self.session.post.side_effect = [response({"access_token": "private-token"}), response(data)]
                self.assert_failed(self.invoke("![图](a.png)"), "有效图片 URL")

    def test_uploaded_url_does_not_break_markdown_destination(self):
        self.image()
        self.succeed("https://file.cc98.org/a)b(c.png")
        self.assertEqual(self.invoke("![图](a.png)")[:2],
                         (0, '![图](https://file.cc98.org/a%29b%28c.png)'.encode()))

    def test_ctrl_c_does_not_output_document(self):
        self.image()
        self.password.side_effect = KeyboardInterrupt
        code, output, errors = self.invoke("![图](a.png)")
        self.assertEqual((code, output), (130, b""))
        self.assertIn("已取消", errors)
        self.session.post.assert_not_called()

    def test_ctrl_c_during_upload_stops_without_output(self):
        self.image("a.png")
        self.image("b.png")
        self.session.post.side_effect = [response({"access_token": "private-token"}), KeyboardInterrupt]
        code, output, errors = self.invoke("![a](a.png) ![b](b.png)")
        self.assertEqual((code, output), (130, b""))
        self.assertIn("已取消", errors)
        self.assertEqual(self.session.post.call_count, 2)

    def test_cli_bytes_and_noninteractive_error(self):
        source = '\ufeff# 标题\r\n\r\n![远程](https://example.com/a.png)\r\n'
        self.note.write_bytes(source.encode())
        elsewhere = self.root / "cli-working-directory"
        elsewhere.mkdir()
        command = [sys.executable, str(Path(app.__file__).resolve()), str(self.note)]
        result = subprocess.run(command, input=b"", capture_output=True, cwd=elsewhere)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, source.encode(), b""))
        self.image()
        self.note.write_text("![图](a.png)", encoding="utf-8")
        result = subprocess.run(command, input=b"", capture_output=True, cwd=elsewhere)
        self.assertEqual((result.returncode, result.stdout), (1, b""))
        self.assertIn("需要交互终端", result.stderr.decode())

    def test_help_and_argument_errors(self):
        code, output, _ = self.invoke(argv=["--help"])
        self.assertEqual(code, 0)
        self.assertIn("引用式图片", output.decode())
        self.assertIn("截断", output.decode())
        for argv in [[], ["one.md", "two.md"], ["--unknown"]]:
            with self.subTest(argv=argv):
                code, output, errors = self.invoke(argv=argv)
                self.assertEqual((code, output), (2, b""))
                self.assertIn("usage:", errors)
        self.session_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
