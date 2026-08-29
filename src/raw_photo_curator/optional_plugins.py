import importlib.util
import os
from pathlib import Path

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


class FaceEyesPlugin:
    id = "optional.face-eyes"
    version = "2.0.0"
    manifest = PluginManifest(
        "人脸、眼睛与表情",
        "YuNet 检测人脸，OpenCV MobileFaceNet 识别表情；眼睛证据不足时返回 unknown。",
        5.1,
        CriterionCost.EXPENSIVE,
        "模型随 OpenCV 安装并完全在本机运行，不联网",
        "运行 raw-curator install-model face",
    )
    criteria = (
        CriterionDefinition("face.eye_focus", "眼睛对焦", CriterionKind.SOFT_WEIGHT, cost=CriterionCost.EXPENSIVE),
        CriterionDefinition("face.blink", "闭眼", CriterionKind.HARD_RULE, "boolean", CriterionCost.EXPENSIVE),
        CriterionDefinition("face.expression", "表情", CriterionKind.SOFT_WEIGHT, cost=CriterionCost.EXPENSIVE),
    )

    def __init__(
        self,
        model_path: Path | None = None,
        expression_model: Path | None = None,
    ):
        self.model_path = model_path or face_model_path()
        self.expression_model = expression_model or expression_model_path()
        self._face_detector = None
        self._expression_net = None
        self._cascades = None

    def available(self, environment: RuntimeEnvironment) -> Availability:
        installed = importlib.util.find_spec("cv2") is not None
        ready = installed and self.model_path.is_file() and self.expression_model.is_file()
        reason = "" if ready else (
            "需要安装 raw-photo-curator[vision]" if not installed
            else f"未找到人脸模型包：{self.model_path.parent}"
        )
        return Availability(ready, "ready" if ready else "model_not_configured", reason)

    def analyze(self, photo: PhotoInput, context: AnalysisContext) -> list[CriterionResult]:
        with Image.open(photo.path) as image:
            return self.analyze_image(image.convert("RGB"))

    def analyze_image(self, image: Image.Image) -> list[CriterionResult]:
        import cv2

        rgb = np.asarray(image.resize((960, 640), Image.Resampling.LANCZOS))
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        gray = cv2.equalizeHist(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
        if self._face_detector is None:
            self._face_detector = cv2.FaceDetectorYN.create(
                str(self.model_path), "", (960, 640), 0.9, 0.3, 5000
            )
        self._face_detector.setInputSize((960, 640))
        _, detected = self._face_detector.detect(bgr)
        faces = [] if detected is None else detected
        if self._cascades is None:
            root = Path(cv2.data.haarcascades)
            self._cascades = cv2.CascadeClassifier(
                str(root / "haarcascade_eye_tree_eyeglasses.xml")
            )
        eye_detector = self._cascades
        eye_scores: list[float] = []
        eye_count = 0
        boxes = []
        confidences = []
        expression_probabilities: list[np.ndarray] = []
        for face in faces:
            x, y, width, height = (int(value) for value in face[:4])
            confidences.append(round(float(face[14]), 4))
            roi = gray[y : y + height, x : x + width]
            eyes = eye_detector.detectMultiScale(
                roi[: int(height * 0.65)], 1.1, 5, minSize=(12, 12)
            )
            eye_count += min(2, len(eyes))
            boxes.append([int(x), int(y), int(width), int(height)])
            for ex, ey, ew, eh in eyes[:2]:
                variance = float(cv2.Laplacian(roi[ey : ey + eh, ex : ex + ew], cv2.CV_64F).var())
                eye_scores.append(min(1.0, variance / 350.0))
            expression_probabilities.append(self._expression_distribution(bgr, face))
        labels = ("angry", "disgust", "fearful", "happy", "neutral", "sad", "surprised")
        expression_mean = (
            np.mean(expression_probabilities, axis=0)
            if expression_probabilities else np.zeros(7, dtype=np.float32)
        )
        expression_index = int(np.argmax(expression_mean))
        expression_confidence = float(expression_mean[expression_index])
        evidence = {
            "method": "yunet_plus_mobilefacenet_fer",
            "faces": len(faces),
            "eyes": eye_count,
            "face_boxes_preview": boxes,
            "face_confidence": confidences,
            "expression": labels[expression_index] if len(faces) else "unknown",
            "expression_probabilities": {
                label: round(float(value), 4)
                for label, value in zip(labels, expression_mean, strict=True)
            },
            "warning": "blink_unknown_without_eyelid_landmarks",
        }
        if not len(faces):
            return [
                CriterionResult(criterion.id, "unknown", None, 0.0, evidence, self.version)
                for criterion in self.criteria
            ]
        focus = float(np.mean(eye_scores)) if eye_scores else None
        # The Haar eye detector can establish visible/open eyes, but absence is not
        # evidence of a blink (small faces, glasses and occlusion all cause misses).
        blink_visible = eye_count >= len(faces) * 2
        expression = float(expression_mean[3] + 0.5 * expression_mean[6])
        return [
            CriterionResult(
                "face.eye_focus", round(focus * 100, 1) if focus is not None else "unknown",
                focus, 0.62 if eye_scores else 0.0, evidence, self.version,
            ),
            CriterionResult(
                "face.blink", False if blink_visible else "unknown",
                1.0 if blink_visible else None, 0.55 if blink_visible else 0.0,
                evidence, self.version,
            ),
            CriterionResult(
                "face.expression", labels[expression_index], expression,
                expression_confidence, evidence, self.version,
            ),
        ]

    def _expression_distribution(self, bgr: np.ndarray, face: np.ndarray) -> np.ndarray:
        import cv2

        if self._expression_net is None:
            self._expression_net = cv2.dnn.readNet(str(self.expression_model))
        source = np.asarray(face[4:14], dtype=np.float32).reshape(5, 2)
        target = np.asarray(
            [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
             [41.5493, 92.3655], [70.7299, 92.2041]],
            dtype=np.float32,
        )
        transform, _ = cv2.estimateAffinePartial2D(source, target, method=cv2.LMEDS)
        aligned = cv2.warpAffine(bgr, transform, (112, 112))
        rgb = cv2.cvtColor(aligned, cv2.COLOR_BGR2RGB).astype(np.float32) / 127.5 - 1.0
        blob = cv2.dnn.blobFromImage(rgb)
        self._expression_net.setInput(blob, "data")
        logits = np.asarray(self._expression_net.forward("label"), dtype=np.float32).reshape(-1)
        logits -= float(logits.max())
        probabilities = np.exp(logits)
        return probabilities / max(1e-8, float(probabilities.sum()))


def face_model_path() -> Path:
    configured = os.environ.get("RAW_CURATOR_FACE_MODEL")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache/raw-photo-curator/models/face_detection_yunet_2023mar.onnx"


def expression_model_path() -> Path:
    configured = os.environ.get("RAW_CURATOR_EXPRESSION_MODEL")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache/raw-photo-curator/models/facial_expression_recognition_mobilefacenet_2022july.onnx"


def nima_model_path() -> Path:
    configured = os.environ.get("RAW_CURATOR_NIMA_MODEL")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache/raw-photo-curator/models/nima_mobilenet_aesthetic.onnx"


class NimaAestheticPlugin:
    id = "optional.aesthetic-embedding"
    version = "2.0.0"
    manifest = PluginManifest(
        "通用审美 Embedding",
        "冻结 NIMA MobileNet 输出 AVA 1–10 评分分布，仅作低优先级先验。",
        12.9,
        CriterionCost.EXPENSIVE,
        "权重和照片完全在本机运行，不联网",
        "运行 raw-curator install-model aesthetic",
    )
    criteria = (
        CriterionDefinition(
            "aesthetic.embedding_score",
            "审美先验",
            CriterionKind.LEARNED_FEATURE,
            cost=CriterionCost.EXPENSIVE,
        ),
    )

    def __init__(self, model_path: Path | None = None):
        self.model_path = model_path or nima_model_path()
        self._session = None

    def available(self, environment: RuntimeEnvironment) -> Availability:
        runtime = importlib.util.find_spec("onnxruntime") is not None
        ready = runtime and self.model_path.is_file()
        reason = "" if ready else (
            "需要安装 raw-photo-curator[vision]" if not runtime
            else f"未找到模型：{self.model_path}"
        )
        return Availability(ready, "ready" if ready else "model_not_configured", reason)

    def analyze(self, photo: PhotoInput, context: AnalysisContext) -> list[CriterionResult]:
        with Image.open(photo.path) as image:
            return self.analyze_image(image.convert("RGB"))

    def analyze_image(self, image: Image.Image) -> list[CriterionResult]:
        if self._session is None:
            import onnxruntime

            self._session = onnxruntime.InferenceSession(
                str(self.model_path), providers=["CPUExecutionProvider"]
            )
        array = np.asarray(
            image.resize((224, 224), Image.Resampling.LANCZOS), dtype=np.float32
        )[None, ...]
        array = array / 127.5 - 1.0
        distribution = np.asarray(
            self._session.run(None, {self._session.get_inputs()[0].name: array})[0]
        ).reshape(-1)
        distribution = np.maximum(distribution, 0)
        distribution /= max(1e-8, float(distribution.sum()))
        mean = float(distribution @ np.arange(1, 11, dtype=np.float32))
        standard_deviation = float(
            np.sqrt(distribution @ ((np.arange(1, 11) - mean) ** 2))
        )
        normalized = max(0.0, min(1.0, (mean - 1) / 9))
        confidence = max(0.35, min(0.85, 1 - standard_deviation / 4.5))
        return [CriterionResult(
            "aesthetic.embedding_score", round(mean, 3), normalized, confidence,
            {
                "method": "nima_mobilenet_ava_onnx",
                "distribution": [round(float(value), 5) for value in distribution],
                "standard_deviation": round(standard_deviation, 3),
                "warning": "generic_prior_not_personal_taste",
            },
            self.version,
        )]


def builtin_plugins() -> tuple[object, ...]:
    return SaliencyPlugin(), TimingDepthPlugin(), FaceEyesPlugin(), NimaAestheticPlugin()
