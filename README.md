# RAW Photo Curator

一个本地优先的 RAW/照片选片工具。它扫描文件夹，为每张照片分别计算：

- **保留分**：清晰度、曝光、构图代理指标与独特性
- **调色潜力**：高光/阴影保留、对比度与主体可用性

当前版本是可解释的 MVP，不上传照片，也不会删除或移动原片。支持 Sony ARW（通过
LibRaw/rawpy）以及 JPEG、PNG、TIFF。

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

```bash
raw-curator analyze /path/to/photos --output reports/latest --limit 50
```

## 当前评分

- 清晰度：灰度梯度强度
- 曝光：亮度均值与中间调占比
- 高光/阴影：接近剪裁的像素比例
- 色彩：饱和度及色彩变化
- 构图代理：边缘干扰惩罚与中心区域信息量
- 独特性：基于感知缩略图的近似重复惩罚

这些分数用于初筛，不代表审美判断。下一阶段将增加人脸闭眼、主体检测、连拍分组、景深
估计以及根据用户保留/淘汰记录训练的个人偏好模型。

## 隐私

所有处理默认在本机完成。生成的报告只包含缩略图和分析数据。
