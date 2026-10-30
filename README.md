# cc98-img-upload

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white) ![uv](https://img.shields.io/badge/uv-supported-6A5ACD?style=flat-square) ![License](https://img.shields.io/badge/license-Apache--2.0-green?style=flat-square)

将 Markdown 中的本地内联图片上传到 CC98，输出替换后的 Markdown。

## 使用

需要 uv、Python ≥ 3.12，以及能访问 CC98 的网络环境。依赖由 uv 自动安装。

```sh
uv run cc98_img_upload.py --help  # 查看帮助，也可用 -h
uv run cc98_img_upload.py /path/to/post.md > /path/to/post.cc98.md
```

按提示输入 CC98 用户名和密码。图片相对路径以 Markdown 所在目录为准。

上传遇到 HTTP 403 时等待 1 秒，仅重试当前图片一次；再次失败则退出。

正文写入 stdout，提示、进度和错误写入 stderr。输入和输出使用不同文件。

## 测试

```sh
uv run test/test_cc98_img_upload.py -v
```

本项目采用 [Apache-2.0](LICENSE) 许可证。
