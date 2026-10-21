"""Validated cross-modal relationship matching and evidence boosts."""

from __future__ import annotations

from dataclasses import replace
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def _path_parts(value: str) -> tuple[str, ...]:
    return tuple(part for part in value.replace("\\", "/").split("/") if part)


def _identity_from_path(value: str) -> str | None:
    parts = _path_parts(value)
    if len(parts) < 3:
        return None
    match = re.fullmatch(r"page_(\d+)_img_(\d+)\.png", parts[-1], flags=re.IGNORECASE)
    if not match:
        return None
    return f"{parts[-2]}_p{int(match.group(1))}_i{int(match.group(2))}"


def _source_stem(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    name = _path_parts(value)[-1] if _path_parts(value) else value
    return name.rsplit(".", 1)[0].casefold()


def _image_keys(metadata: Mapping[str, Any]) -> tuple[set[str], str | None]:
    keys: set[str] = set()
    image_id = metadata.get("image_id")
    if isinstance(image_id, str) and image_id.strip():
        keys.add(image_id)
        keys.add(image_id.replace("\\", "/").split("/")[-1])
    image_path = metadata.get("image_path")
    if isinstance(image_path, str) and image_path.strip():
        parts = _path_parts(image_path)
        if parts:
            keys.add(parts[-1])
        identity = _identity_from_path(image_path)
        if identity:
            keys.add(identity)
    return {key for key in keys if key}, _source_stem(metadata.get("source"))


def _related(metadata: Mapping[str, Any]) -> tuple[list[str], bool]:
    value = metadata.get("related_image_ids", [])
    if value is None:
        return [], False
    if not isinstance(value, (list, tuple, set)):
        return [], True
    return [value for value in value if isinstance(value, str) and value.strip()], any(not isinstance(value, str) for value in value)


def _matches(reference: str, image_keys: set[str], image_source: str | None, text_source: str | None) -> bool:
    normalized = reference.replace("\\", "/")
    candidates = {reference, normalized, normalized.split("/")[-1]}
    identity = _identity_from_path(normalized)
    if identity:
        candidates.add(identity)
    if candidates & image_keys:
        basename_match = normalized.split("/")[-1] in image_keys and "/" not in normalized
        if basename_match and image_source and text_source and image_source != text_source:
            return False
        return True
    return False


def apply_cross_modal_boosts(text_hits: Sequence[Any], image_hits: Sequence[Any], config: Any) -> tuple[tuple[Any, ...], tuple[Any, ...], tuple[str, ...], dict[str, Any]]:
    if not config.use_cross_modal_boost or not text_hits or not image_hits:
        return tuple(text_hits), tuple(image_hits), (), {"matched_text_hits": 0, "matched_image_hits": 0}
    image_info = []
    for hit in image_hits:
        metadata = hit.metadata if isinstance(hit.metadata, Mapping) else {}
        keys, source = _image_keys(metadata)
        image_info.append((hit, keys, source))
    warnings: list[str] = []
    text_updates = []
    matched_image_ids: set[str] = set()
    for hit in text_hits:
        metadata = hit.metadata if isinstance(hit.metadata, Mapping) else {}
        refs, malformed = _related(metadata)
        if malformed:
            warnings.append(f"text candidate {hit.record_id} has malformed related_image_ids")
        matched = {image.record_id for image, keys, source in image_info if any(_matches(ref, keys, source, _source_stem(metadata.get("source"))) for ref in refs)}
        matched_image_ids.update(matched)
        count = len(matched)
        boost = min(config.cross_modal_max_boost, count * config.cross_modal_boost_per_overlap)
        distance = hit.semantic_distance
        effective_distance = distance * (1.0 - boost) if distance is not None else None
        fused = hit.fused_score * min(1.0 + count * 0.05, 1.10) if count else hit.fused_score
        text_updates.append(replace(hit, cross_modal_boost=round(float(boost), 3), boosted_fused_score=float(fused), effective_distance=effective_distance, effective_similarity=(1.0 - effective_distance) if effective_distance is not None else None))
    image_updates = []
    for hit, keys, source in image_info:
        matched = any(hit.record_id == image_id for image_id in matched_image_ids)
        boost = min(config.cross_modal_max_boost, config.cross_modal_boost_per_overlap) if matched else 0.0
        distance = hit.semantic_distance
        effective_distance = distance * (1.0 - boost) if distance is not None else None
        image_updates.append(replace(hit, cross_modal_boost=round(float(boost), 3), boosted_fused_score=hit.fused_score, effective_distance=effective_distance, effective_similarity=(1.0 - effective_distance) if effective_distance is not None else None))
    return tuple(text_updates), tuple(image_updates), tuple(warnings), {"matched_text_hits": sum(1 for hit in text_updates if hit.cross_modal_boost > 0), "matched_image_hits": sum(1 for hit in image_updates if hit.cross_modal_boost > 0)}
