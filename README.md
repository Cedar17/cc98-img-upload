# cc98-img-upload

将 Markdown 中的本地内联图片上传到 CC98，输出替换后的 Markdown。

## 使用

需要 uv、Python ≥ 3.12，以及能访问 CC98 的网络环境。

```sh
uv run cc98_img_upload.py --help  # 查看帮助，也可用 -h
uv run cc98_img_upload.py /path/to/post.md > /path/to/post.cc98.md
```

按提示输入 CC98 用户名和密码。图片相对路径以 Markdown 所在目录为准。

正文写入 stdout，提示、进度和错误写入 stderr。输入和输出使用不同文件。

## 测试

```sh
uv run test/test_cc98_img_upload.py -v
```
