# RAW Photo Curator

一个本地优先的 RAW/照片选片工具。它扫描文件夹，为每张照片分别计算：

- **保留分**：清晰度、曝光、构图代理指标与独特性
- **调色潜力**：高光/阴影保留、对比度与主体可用性

当前版本是可解释的 MVP，不上传照片，也不会删除或移动原片。支持 Sony ARW（通过
LibRaw/rawpy）以及 JPEG、PNG、TIFF。

项目方向和架构边界见 [ROADMAP.md](ROADMAP.md) 与 [DESIGN.md](DESIGN.md)。

## 安装

需要 Python 3.10+：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## 使用

```bash
raw-curator analyze /path/to/photos --output reports/latest
```

完成后打开 `reports/latest/index.html`。分析使用最长边 1600px 的预览，不修改原文件。

推荐使用本地交互式工作台。每次评价会立即写入本地 SQLite：

```bash
raw-curator serve /path/to/photos --output reports/live
```

然后访问 `http://127.0.0.1:8765`。服务只监听本机，不会上传照片。

照片索引、客观特征和缩略图会写入输出目录的 `catalog.sqlite3`。未变化的照片再次扫描时
直接命中缓存；文件内容、大小或修改时间变化后会自动重算。Alaska 测试集 355 张 ARW 的
第二次扫描为 355/355 缓存命中。

交互采用动态 Top 5：输入文件夹后完成全量初筛；“保留”的照片停留在候选池，“淘汰”
的照片立即由下一张推荐补位。保留满 5 张后进入下一轮，已保留和已淘汰照片都会被排除，
并使用累计反馈重新计算个人推荐分。

顶部可以切换 Travel、Portrait、Landscape、Wildlife 和 Custom 选片标准。Profile 的显式
权重会直接改变推荐先验；硬规则始终先于本地学习偏好执行。

“相似组”视图使用拍摄时间、相机序列、感知哈希和本地视觉描述符识别近似重复与连拍。
组内照片支持同步缩放、最佳/淘汰反馈，以及持久化拆分和合并纠正。

可以复制 `docs/group-labels.example.json` 建立本地人工分组标注，并报告 pairwise
precision、recall 和 F1：

```bash
raw-curator evaluate-groups reports/live/catalog.sqlite3 my-group-labels.json
```

```bash
raw-curator analyze /path/to/photos --output reports/latest --limit 50
```

## 当前评分

- 清晰度 22%：灰度梯度强度
- 构图代理 18%：边缘干扰惩罚与中心区域信息量
- 曝光 12%：亮度均值与中间调偏差
- 高光保留 8%、阴影保留 6%：接近剪裁的像素比例
- 对比度 7%：5%–95% 亮度分位范围
- 噪声代理 7%：低变化区域的局部残差
- 色彩信息 5%：饱和度及色彩变化
- 白平衡代理 5%：RGB 通道均值偏差
- 独特性预留 10%：后续用于连拍/重复照片惩罚

固定权重是可解释的初始先验，不是普适审美定律。个人推荐分会根据保留、调色和淘汰
反馈计算正负偏好中心；有效反馈达到 30 条时，个人偏好权重逐步升至最多 40%。
- 独特性：基于感知缩略图的近似重复惩罚

这些分数用于初筛，不代表审美判断。连拍分组已经参与组内比较；人脸闭眼、主体检测和
景深等模型作为后续可选插件接入，不会被基础代理指标冒充。

每项客观标准同时保存置信度、结构化证据、分析器版本和组内百分位。RAW 高光余量与暗部
恢复使用传感器线性抽样，仅对高排名候选按需计算；未计算或不可用时显示“未知”，不会按
零分参与排序。

“标准设置”显示每个分析器的运行成本、下载体积、安装状态与隐私边界。主体显著性、背景
分离、景深和动作时机代理无需下载；人脸/闭眼与审美模型在适配器未安装时保持禁用和
unknown。新启用的插件独立补算自己的 Criterion，不会使已有基础分析缓存失效。

## 隐私

所有处理默认在本机完成。生成的报告只包含缩略图和分析数据。
