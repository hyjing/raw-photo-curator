# Models and limitations

The default stack does not download or train a foundation model. It uses preview-pixel technical
measurements, RAW sensor sampling for selected candidates, perceptual dHash, a deterministic 48-D
ColorGrid descriptor, and low-confidence saliency/depth/timing proxies. The personal ranker is a
small L2-regularized pairwise logistic model trained only from the user's local feedback.

Optional models are installed explicitly with `raw-curator install-model face` or
`raw-curator install-model aesthetic`. Downloads are pinned and SHA-256 verified; weights are kept
under `~/.cache/raw-photo-curator/models` and are not redistributed in this repository.

- Face detection: OpenCV Zoo YuNet 2023mar (Apache-2.0 directory license), 227 KB.
- Expression: OpenCV Zoo Progressive Teacher / MobileFaceNet (Apache-2.0 directory license),
  4.6 MB, seven classes, reported 88.27% RAF-DB accuracy by its model card.
- Aesthetic prior: NIMA MobileNet ONNX, 12.2 MB. It emits the full AVA 1–10 distribution and is
  deliberately weighted as a weak generic prior, not a personalized truth. The external weight
  file has no clear license declaration, so users must verify suitability for their intended use.

YuNet landmarks align faces for expression inference. Eye-focus uses an eye crop only when OpenCV
actually detects one. The current lightweight package can confirm visibly open eyes, but it cannot
reliably infer a blink from a missed eye; glasses, small faces and occlusion therefore produce
`unknown`, never an automatic rejection. Missing models and low-confidence results likewise stay
unknown rather than becoming zero. Proxy metrics are not semantic subject understanding. Scores
support culling; the photographer remains the decision maker.
