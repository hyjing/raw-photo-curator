import importlib.util

import numpy as np
from PIL import Image, ImageFilter

from .criteria import (
    AnalysisContext,
    Availability,
    CriterionCost,
    CriterionDefinition,
    CriterionKind,
    CriterionResult,
    PhotoInput,
    PluginManifest,
    RuntimeEnvironment,
)


class SaliencyPlugin:
    id = "builtin.saliency"
    version = "1.0.0"
    manifest = PluginManifest(
        "主体与显著性",
        "无需模型，以多尺度局部对比估计视觉主体集中度和背景分离。",
        0,
        CriterionCost.CHEAP,
    )
    criteria = (
        CriterionDefinition(
            "subject.saliency_concentration", "主体集中度", CriterionKind.SOFT_WEIGHT
        ),
        CriterionDefinition(
            "subject.background_separation", "主体背景分离", CriterionKind.SOFT_WEIGHT
        ),
    )

    def available(self, environment: RuntimeEnvironment) -> Availability:
        return Availability(True, "ready")

    def analyze(self, photo: PhotoInput, context: AnalysisContext) -> list[CriterionResult]:
        with Image.open(photo.path) as image:
            return self.analyze_image(image.convert("RGB"))

    def analyze_image(self, image: Image.Image) -> list[CriterionResult]:
        preview = image.convert("L").resize((160, 120), Image.Resampling.LANCZOS)
        array = np.asarray(preview, dtype=np.float32) / 255.0
        blurred = np.asarray(preview.filter(ImageFilter.GaussianBlur(8)), dtype=np.float32) / 255.0
        saliency = np.abs(array - blurred)
        threshold = float(np.percentile(saliency, 85))
        mask = saliency >= threshold
        h, w = saliency.shape
        center = mask[h // 5 : 4 * h // 5, w // 5 : 4 * w // 5]
        concentration = float(center.sum() / max(1, mask.sum()))
        foreground = float(saliency[mask].mean()) if np.any(mask) else 0.0
        background = float(saliency[~mask].mean()) if np.any(~mask) else 0.0
        separation = min(1.0, max(0.0, (foreground - background) * 5))
        return [
            CriterionResult(
                "subject.saliency_concentration",
                round(concentration * 100, 1),
                concentration,
                0.65,
                {"method": "multiscale_local_contrast", "threshold": threshold},
                self.version,
            ),
            CriterionResult(
                "subject.background_separation",
                round(separation * 100, 1),
                separation,
                0.55,
                {"method": "saliency_foreground_background_delta"},
                self.version,
            ),
        ]


class TimingDepthPlugin:
    id = "builtin.timing-depth"
    version = "1.0.0"
    manifest = PluginManifest(
        "景深与动作时机代理",
        "无需模型，比较主体区与背景高频信息，并估计方向性运动模糊。",
        0,
        CriterionCost.CHEAP,
    )
    criteria = (
        CriterionDefinition("depth.separation", "景深分离", CriterionKind.SOFT_WEIGHT),
        CriterionDefinition("timing.motion_clarity", "动作清晰度", CriterionKind.SOFT_WEIGHT),
    )

    def available(self, environment: RuntimeEnvironment) -> Availability:
        return Availability(True, "ready")

    def analyze(self, photo: PhotoInput, context: AnalysisContext) -> list[CriterionResult]:
        with Image.open(photo.path) as image:
            return self.analyze_image(image.convert("RGB"))

    def analyze_image(self, image: Image.Image) -> list[CriterionResult]:
        gray = np.asarray(image.convert("L").resize((240, 160)), dtype=np.float32) / 255.0
        gx = np.abs(np.diff(gray, axis=1, prepend=gray[:, :1]))
        gy = np.abs(np.diff(gray, axis=0, prepend=gray[:1, :]))
        detail = gx + gy
        h, w = gray.shape
        center = detail[h // 4 : 3 * h // 4, w // 4 : 3 * w // 4]
        border = np.concatenate(
            (detail[: h // 5].ravel(), detail[-h // 5 :].ravel(), detail[:, : w // 5].ravel(), detail[:, -w // 5 :].ravel())
        )
        separation = min(1.0, max(0.0, 0.5 + (float(center.mean()) - float(border.mean())) * 8))
        direction_balance = min(float(gx.mean()), float(gy.mean())) / max(
            1e-6, max(float(gx.mean()), float(gy.mean()))
        )
        clarity = min(1.0, float(detail.mean()) * 12) * (0.65 + 0.35 * direction_balance)
        return [
            CriterionResult(
                "depth.separation",
                round(separation * 100, 1),
                separation,
                0.5,
                {"method": "center_background_detail_delta", "warning": "proxy_not_depth_map"},
                self.version,
            ),
            CriterionResult(
                "timing.motion_clarity",
                round(clarity * 100, 1),
                clarity,
                0.55,
                {"method": "gradient_direction_balance", "warning": "proxy_not_motion_tracking"},
                self.version,
            ),
        ]


class OptionalModelPlugin:
    def __init__(
        self,
        plugin_id: str,
        name: str,
        description: str,
        criteria: tuple[CriterionDefinition, ...],
        package: str,
        size_mb: float,
        install_hint: str,
    ):
        self.id = plugin_id
        self.version = "1.0.0"
        self.criteria = criteria
        self.package = package
        self.manifest = PluginManifest(
            name,
            description,
            size_mb,
            CriterionCost.EXPENSIVE,
            "模型和照片完全在本机运行，不联网",
            install_hint,
        )

    def available(self, environment: RuntimeEnvironment) -> Availability:
        installed = importlib.util.find_spec(self.package) is not None
        return Availability(
            False,
            "model_not_configured" if installed else "not_installed",
            "模型适配器尚未配置" if installed else "所需本地模型运行时未安装",
        )

    def analyze(self, photo: PhotoInput, context: AnalysisContext) -> list[CriterionResult]:
        raise RuntimeError("optional model adapter is not installed")


FACE_PLUGIN = OptionalModelPlugin(
    "optional.face-eyes",
    "人脸、眼睛与表情",
    "检测人脸、眼睛对焦、闭眼和表情；缺少模型时所有结果保持 unknown。",
    (
        CriterionDefinition("face.eye_focus", "眼睛对焦", CriterionKind.SOFT_WEIGHT, cost=CriterionCost.EXPENSIVE),
        CriterionDefinition("face.blink", "闭眼", CriterionKind.HARD_RULE, "boolean", CriterionCost.EXPENSIVE),
        CriterionDefinition("face.expression", "表情", CriterionKind.SOFT_WEIGHT, cost=CriterionCost.EXPENSIVE),
    ),
    "mediapipe",
    32,
    "安装可选依赖 raw-photo-curator[vision]",
)

AESTHETIC_PLUGIN = OptionalModelPlugin(
    "optional.aesthetic-embedding",
    "通用审美 Embedding",
    "冻结的轻量审美向量，仅作为低优先级先验，不覆盖用户规则。",
    (
        CriterionDefinition(
            "aesthetic.embedding_score",
            "审美先验",
            CriterionKind.LEARNED_FEATURE,
            cost=CriterionCost.EXPENSIVE,
        ),
    ),
    "onnxruntime",
    24,
    "安装可选依赖 raw-photo-curator[vision] 并下载模型文件",
)


def builtin_plugins() -> tuple[object, ...]:
    return SaliencyPlugin(), TimingDepthPlugin(), FACE_PLUGIN, AESTHETIC_PLUGIN
