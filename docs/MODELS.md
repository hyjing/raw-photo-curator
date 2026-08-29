# Models and limitations

The default stack does not download or train a foundation model. It uses preview-pixel technical
measurements, RAW sensor sampling for selected candidates, perceptual dHash, a deterministic 48-D
ColorGrid descriptor, and low-confidence saliency/depth/timing proxies. The personal ranker is a
small L2-regularized pairwise logistic model trained only from the user's local feedback.

Face/eye/blink/expression and general-aesthetic entries are adapters only and remain unavailable
until a compatible optional model is installed. Missing results stay unknown rather than becoming
zero. Proxy metrics are not semantic subject understanding and must not be presented as aesthetic
truth. Scores support culling; the photographer remains the decision maker.
