# RAW Photo Curator

[中文](#中文) · [English](#english)

[![Latest release](https://img.shields.io/github/v/release/hyjing/raw-photo-curator?display_name=tag)](https://github.com/hyjing/raw-photo-curator/releases/latest)
[![CI](https://github.com/hyjing/raw-photo-curator/actions/workflows/ci.yml/badge.svg)](https://github.com/hyjing/raw-photo-curator/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/github/license/hyjing/raw-photo-curator)](LICENSE)
![macOS Apple Silicon](https://img.shields.io/badge/macOS-Apple%20Silicon-black?logo=apple)
![Local only](https://img.shields.io/badge/privacy-local--only-55c987)

![RAW Photo Curator Alaska Top 5 演示](docs/assets/demo.gif)

| 动态 Top 5 | 连拍选最佳 | 明确的选片终点 |
| --- | --- | --- |
| ![大图 Top 5 与可解释评分](docs/assets/top5.png) | ![连拍组内选最佳](docs/assets/burst-review.png) | ![复制保留照片或生成 XMP](docs/assets/finish.png) |

<a id="中文"></a>

## 中文

面向 Sony ARW 和 RAW 工作流的开源离线 AI 选片工具。它根据清晰度、曝光、构图、动态
范围、主体分离以及你的保留/淘汰反馈筛选照片，不上传图片。直接在 Finder 选择文件夹即可
获得可解释的 Top 5 推荐；轻量本地排序模型会逐步学习个人偏好。

> 当前为公开 alpha。macOS 下载版不需要安装 Python。

## 下载 macOS 版

[下载最新版本](https://github.com/hyjing/raw-photo-curator/releases/latest)，打开 DMG 后把
**RAW Photo Curator** 拖入 Applications。当前 alpha 尚未经过 Apple 公证；如果 macOS
提示“无法验证是否包含恶意软件”，先尝试打开一次，再进入**系统设置 → 隐私与安全**，在
安全性区域点击**仍要打开**。不要关闭整个 Gatekeeper。详情与校验方法见
[发布与签名说明](docs/RELEASE.md)。

## 三步完成选片

1. 点击**在 Finder 中选择**，选一个包含 ARW、JPEG、PNG 或 TIFF 的文件夹。程序会自动
   开始分析；再次打开同一批照片时直接使用本地缓存。
2. 查看大图 Top 5。淘汰会立即由下一张推荐补位；保留满 5 张后完成一轮，个人偏好模型
   自动更新，再进入下一轮。
3. 随时点击**完成选片**。你可以把保留的原始照片复制到新目录，或生成 Lightroom /
   Capture One 可读取的 XMP sidecar。程序绝不修改 RAW 字节、不覆盖已有文件，每次导出
   都保存审计记录。完成页还会总结保留照片、选片进度、照片优势和个人偏好学习状态。

六边形图解释清晰度、曝光、动态范围、对比、色彩和构图。Travel、Portrait、Landscape、
Wildlife 和 Custom 是可切换的显式标准。“连拍选最佳”一次只显示一组相关照片，只需点出
其中最好的一张。

## 适合谁

- 需要批量筛选 Sony ARW 的 Alpha 相机用户；
- 一次拍摄数百张 RAW 的旅行、婚礼、野生动物和体育摄影师；
- 希望在连拍和近重复照片中快速找出最佳帧的用户；
- 不愿把私人照片上传到云端的摄影师；
- 需要 Lightroom / Capture One XMP sidecar 工作流的用户；
- 希望选片工具逐步学习个人审美，而不是依赖固定通用分数的用户。

## 为什么不同

| 能力 | RAW Photo Curator | 通用照片管理器 | 云端 AI 选片 |
| --- | --- | --- | --- |
| 完全本地、无需上传 | ✓ | 通常支持 | 通常不支持 |
| Sony ARW 原生流程 | ✓ | 视产品而定 | 视产品而定 |
| 可解释的客观证据 | ✓ | 较少 | 通常不透明 |
| 根据 keep/reject 现场学习 | ✓ | 较少 | 视产品而定 |
| 连拍与近重复组内选择 | ✓ | 部分支持 | 部分支持 |
| Lightroom / Capture One XMP | ✓ | 部分支持 | 部分支持 |
| 开源、可审计 | ✓ | 通常不支持 | 通常不支持 |

## 可复现的实测

- 真实测试集：355 张 Sony ARW；
- 第二次扫描：355 / 355 缓存命中；
- 隐私边界：照片和反馈只留在本机；
- 自动化验证：42 项测试；
- 真实反馈示例：31 条反馈后，本地个性化权重达到受限上限 40%。

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
./scripts/package_macos_release.sh v0.2.2
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

---

<a id="english"></a>

## English

An open-source, offline AI photo culling app for Sony ARW and RAW workflows. It ranks photos using
sharpness, exposure, composition, dynamic range, subject separation, and your personal keep/reject
feedback—without uploading images. Choose a folder in Finder to get an explainable Top 5, while a
lightweight on-device ranker gradually learns your preferences.

> Public alpha. The downloadable macOS app does not require Python.

### Download for macOS

[Download the latest release](https://github.com/hyjing/raw-photo-curator/releases/latest), open the
DMG, and drag **RAW Photo Curator** to Applications. The current alpha is not Apple-notarized. If
macOS says it cannot verify the app for malicious software, try opening it once, then go to
**System Settings → Privacy & Security** and click **Open Anyway** in the Security section. Do not
disable Gatekeeper globally. See [release and signing notes](docs/RELEASE.md).

### Three-step workflow

1. Click **Choose in Finder** and select a folder containing ARW, JPEG, PNG, or TIFF photos. Analysis
   starts automatically and is cached for subsequent runs.
2. Review the large Top 5 cards. A rejection is replaced immediately; five keeps finish a round and
   train the per-profile preference model before the next round.
3. Click **Finish culling** at any time. Copy kept originals to another folder or generate
   Lightroom/Capture One-compatible XMP sidecars. RAW bytes are never modified, existing files are
   never overwritten, and every export has an audit record. The finish screen also summarizes the
   kept photos, review progress, visual strengths, and personalization status.

The hexagonal chart explains sharpness, exposure, dynamic range, contrast, color, and composition.
Travel, Portrait, Landscape, Wildlife, and Custom profiles provide explicit starting standards.
Burst review presents one related set at a time and asks only for the best frame.

### Who it is for

- Sony Alpha users culling large ARW shoots;
- travel, wedding, wildlife, and sports photographers reviewing hundreds of RAW files;
- photographers choosing the best frame from bursts and near-duplicates;
- privacy-conscious users who do not want to upload personal photos;
- Lightroom or Capture One users who need non-destructive XMP sidecars;
- anyone who wants a culling tool to learn personal taste instead of applying one universal score.

### How it differs

| Capability | RAW Photo Curator | General photo managers | Cloud AI culling |
| --- | --- | --- | --- |
| Fully local, no upload | ✓ | Often | Usually no |
| Sony ARW workflow | ✓ | Product-dependent | Product-dependent |
| Explainable objective evidence | ✓ | Uncommon | Usually opaque |
| Learns from keep/reject feedback | ✓ | Uncommon | Product-dependent |
| Burst and near-duplicate review | ✓ | Partial | Partial |
| Lightroom / Capture One XMP | ✓ | Partial | Partial |
| Open source and auditable | ✓ | Usually no | Usually no |

### Reproducible evidence

- real test set: 355 Sony ARW files;
- second scan: 355 / 355 cache hits;
- privacy boundary: photos and feedback remain on-device;
- automated verification: 42 tests;
- real feedback example: 31 decisions raised local personalization to its bounded 40% influence.

### Why it is technically interesting

- incremental, versioned SQLite feature cache for RAW workflows;
- objective criteria with confidence, evidence, cost, and graceful degradation;
- time/EXIF/perceptual-hash/local-descriptor burst grouping with persisted corrections;
- strongly regularized pairwise personalization, isolated per profile;
- active Top 5 selection balancing rank, uncertainty, and visual diversity;
- reproducible holdout metrics and acceptance reports;
- audited JSON/CSV/XMP/copy/link exports with conflict protection and undo support.

Read [System Design](DESIGN.md), [Roadmap](ROADMAP.md), [Plugin SDK](docs/PLUGIN_SDK.md),
[Privacy](docs/PRIVACY.md), [Performance](docs/PERFORMANCE.md), and
[Models & limitations](docs/MODELS.md).

### Developer setup

Requires Python 3.10+:

```bash
./scripts/bootstrap.sh
.venv/bin/raw-curator serve /path/to/photos --output reports/live
```

Open `http://127.0.0.1:8765`. To build the macOS app or complete release artifacts:

```bash
.venv/bin/pip install -e '.[packaging,vision]'
./scripts/build_macos.sh
./scripts/package_macos_release.sh v0.2.2
```

Optional models are explicit, pinned downloads: `raw-curator install-model face` and
`raw-curator install-model aesthetic`. Evidence that cannot be measured remains unknown rather than
being treated as a failure.

### Safety and privacy

The server listens only on `127.0.0.1`. No cloud account or upload is required. Exports use a
plan/apply/audit workflow, skip conflicts, and can be undone from the recorded audit file.

### License

See [LICENSE](LICENSE).
