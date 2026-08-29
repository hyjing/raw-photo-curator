# RAW Photo Curator

![RAW Photo Curator Alaska Top 5 演示](docs/assets/demo.gif)

| 动态 Top 5 | 连拍选最佳 | 明确的选片终点 |
| --- | --- | --- |
| ![大图 Top 5 与可解释评分](docs/assets/top5.png) | ![连拍组内选最佳](docs/assets/burst-review.png) | ![复制保留照片或生成 XMP](docs/assets/finish.png) |

中文 · [English](README_EN.md)

本地优先、可解释的 RAW 选片工具。直接在 Finder 选择 Sony ARW 或普通照片文件夹，获得
Top 5 推荐；你只需保留或淘汰，轻量的本地排序模型会逐步学习个人偏好。照片、预览、评分
和反馈都留在你的 Mac 上。

> 当前为 private alpha。首个可下载 macOS 版本由 Release workflow 自动打包；下载版用户
> 不需要安装 Python。

## 下载 macOS 版

[下载最新版本](https://github.com/hyjing/raw-photo-curator/releases/latest)，打开 DMG 后把
**RAW Photo Curator** 拖入 Applications。正式签名和公证完成前，macOS 可能需要按住
Control 点击应用并选择“打开”。详情见[发布与签名说明](docs/RELEASE.md)。

## 三步完成选片

1. 点击**在 Finder 中选择**，选一个包含 ARW、JPEG、PNG 或 TIFF 的文件夹。程序会自动
   开始分析；再次打开同一批照片时直接使用本地缓存。
2. 查看大图 Top 5。淘汰会立即由下一张推荐补位；保留满 5 张后完成一轮，个人偏好模型
   自动更新，再进入下一轮。
3. 随时点击**完成选片**。你可以把保留的原始照片复制到新目录，或生成 Lightroom /
   Capture One 可读取的 XMP sidecar。程序绝不修改 RAW 字节、不覆盖已有文件，每次导出
   都保存审计记录。

六边形图解释清晰度、曝光、动态范围、对比、色彩和构图。Travel、Portrait、Landscape、
Wildlife 和 Custom 是可切换的显式标准。“连拍选最佳”一次只显示一组相关照片，只需点出
其中最好的一张。

## 技术特点

- 面向 RAW 工作流的增量、版本化 SQLite 特征缓存；
- 每项客观标准都保存置信度、结构化证据、成本与分析器版本；
- 使用拍摄时间、EXIF、感知哈希与本地描述符的连拍/近重复分组；
- 每个 Profile 隔离的强正则 pairwise 个性化排序；
- 同时考虑排序、模型不确定性与视觉多样性的动态 Top 5；
- 可复现的本地留出评测、学习曲线和验收报告；
- 有冲突保护、审计与撤销能力的 JSON/CSV/XMP/复制/链接工作流。

详见[系统设计](DESIGN.md)、[路线图](ROADMAP.md)、[插件 SDK](docs/PLUGIN_SDK.md)、
[隐私](docs/PRIVACY.md)、[性能](docs/PERFORMANCE.md)和[模型局限](docs/MODELS.md)。

## 开发者运行

需要 Python 3.10+：

```bash
./scripts/bootstrap.sh
.venv/bin/raw-curator serve /path/to/photos --output reports/live
```

打开 `http://127.0.0.1:8765`。构建 macOS 应用或完整发布包：

```bash
.venv/bin/pip install -e '.[packaging,vision]'
./scripts/build_macos.sh
./scripts/package_macos_release.sh v0.2.0
```

可选本地模型必须显式下载：`raw-curator install-model face` 和
`raw-curator install-model aesthetic`。无法可靠测量的证据会显示为 unknown，不会被当成
零分或自动淘汰。

## 评分、评测与 CLI

基础先验包含清晰度、曝光、高光/阴影保留、对比、噪声、色彩、白平衡、构图代理与
独特性。它不是“普适审美定律”；有足够的保留/淘汰反馈后，个人模型才逐渐参与排序，且
学习权重有上限。真实 RAW 高光余量、暗部恢复以及组内百分位作为独立证据保存。

```bash
raw-curator acceptance reports/live --expected-photos 355
raw-curator evaluate-personal reports/live --profile travel
raw-curator evaluate-groups reports/live/catalog.sqlite3 group-labels.json
raw-curator export reports/live/feedback.sqlite3 selection.json --profile travel
```

更完整的无损 XMP、复制、hardlink、symlink 与 `undo` 命令见
`raw-curator --help` 和 [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md)。

## 隐私与安全

本地服务只监听 `127.0.0.1`，无需云账号，也不上传照片。所有文件输出都先规划、检查冲突
再执行；已有目标会跳过，并可通过审计文件撤销。

## License

见 [LICENSE](LICENSE)。
