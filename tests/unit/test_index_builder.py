from __future__ import annotations

import pytest

from backend.engine.index_builder import MetadataSerializationError, serialize_index_metadata


def test_metadata_serialization_preserves_scalars_and_is_deterministic():
    first = serialize_index_metadata({
        "none": None,
        "bbox": [1, 2, {"z": True, "a": "x"}],
        "flag": True,
        "score": 1.5,
    })
    second = serialize_index_metadata({
        "score": 1.5,
        "flag": True,
        "bbox": [1, 2, {"a": "x", "z": True}],
        "none": None,
    })
    assert first == second
    assert first["none"] == ""
    assert first["bbox"] == '[1,2,{"a":"x","z":true}]'


@pytest.mark.parametrize("value", [object(), float("nan"), {"bad": object()}])
def test_metadata_serialization_rejects_unsupported_values(value):
    with pytest.raises(MetadataSerializationError):
        serialize_index_metadata({"value": value})
