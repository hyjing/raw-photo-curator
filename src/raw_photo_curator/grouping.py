import hashlib
from dataclasses import dataclass
from datetime import datetime

from .embedding import cosine_distance
from .metadata import hamming_distance


@dataclass(frozen=True)
class SimilarityGroup:
    id: str
    type: str
    confidence: float
    photo_ids: tuple[str, ...]


def _seconds(record: dict[str, object]) -> float | None:
    value = record["metadata"].get("capture_time")  # type: ignore[union-attr]
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


def _related(left: dict[str, object], right: dict[str, object]) -> tuple[bool, float]:
    left_hash = left.get("perceptual_hash")
    right_hash = right.get("perceptual_hash")
    hash_distance = (
        hamming_distance(str(left_hash), str(right_hash))
        if left_hash and right_hash
        else 64
    )
    if hash_distance <= 5:
        return True, 1.0 - hash_distance / 64
    left_time, right_time = _seconds(left), _seconds(right)
    time_distance = abs(left_time - right_time) if left_time is not None and right_time is not None else None
    left_embedding, right_embedding = left.get("embedding"), right.get("embedding")
    visual_distance = (
        cosine_distance(left_embedding, right_embedding)  # type: ignore[arg-type]
        if left_embedding and right_embedding
        else 1.0
    )
    metadata_left = left["metadata"]  # type: ignore[assignment]
    metadata_right = right["metadata"]  # type: ignore[assignment]
    sequence_distance = abs(
        int(metadata_left.get("sequence") or -10000)
        - int(metadata_right.get("sequence") or 10000)
    )
    close_in_time = time_distance is not None and time_distance <= 5
    close_in_sequence = time_distance is None and sequence_distance <= 2
    related = (close_in_time or close_in_sequence) and (
        hash_distance <= 24 or visual_distance <= 0.12
    )
    confidence = max(0.0, min(1.0, 1 - hash_distance / 40 - visual_distance / 2))
    return related, round(confidence, 3)


def build_groups(records: list[dict[str, object]]) -> list[SimilarityGroup]:
    if not records:
        return []
    ordered = sorted(
        records,
        key=lambda record: (
            _seconds(record) is None,
            _seconds(record) or record["metadata"].get("sequence", 0),  # type: ignore[union-attr]
            str(record["path"]),
        ),
    )
    parent = list(range(len(ordered)))
    confidences: dict[tuple[int, int], float] = {}

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left in range(len(ordered)):
        for right in range(left + 1, min(len(ordered), left + 8)):
            related, confidence = _related(ordered[left], ordered[right])
            if related:
                union(left, right)
                confidences[(left, right)] = confidence

    buckets: dict[int, list[int]] = {}
    for index in range(len(ordered)):
        buckets.setdefault(find(index), []).append(index)
    output = []
    for members in buckets.values():
        if len(members) < 2:
            continue
        photo_ids = tuple(str(ordered[index]["id"]) for index in members)
        group_hash = hashlib.sha256("\0".join(sorted(photo_ids)).encode()).hexdigest()[:20]
        pair_confidence = [
            confidence
            for (left, right), confidence in confidences.items()
            if left in members and right in members
        ]
        hashes = [str(ordered[index]["perceptual_hash"]) for index in members]
        duplicate = all(
            hamming_distance(hashes[0], value) <= 5 for value in hashes[1:]
        )
        output.append(
            SimilarityGroup(
                group_hash,
                "duplicate" if duplicate else "burst",
                round(sum(pair_confidence) / max(1, len(pair_confidence)), 3),
                photo_ids,
            )
        )
    return sorted(output, key=lambda group: group.id)

