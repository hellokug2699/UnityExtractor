from __future__ import annotations

from unity_extractor.resource_classifier import classify_unity_type
from unity_extractor.object_models import ObjectRecord


def test_known_unity_types_are_classified_by_exact_type_name():
    cases = {
        "Texture2D": ("image", "texture"),
        "Sprite": ("image", "sprite"),
        "AudioClip": ("audio", "clip"),
        "Mesh": ("model", "mesh"),
        "Material": ("material", "material"),
        "AnimationClip": ("animation", "clip"),
        "GameObject": ("scene", "game_object"),
        "MonoBehaviour": ("script", "behaviour"),
        "TextAsset": ("data", "text_asset"),
        "Font": ("font", "font"),
        "VideoClip": ("video", "clip"),
        "AssetBundle": ("container", "asset_bundle"),
    }

    for type_name, expected in cases.items():
        result = classify_unity_type(type_name)
        assert (result.category, result.subcategory) == expected
        assert result.confidence == "high"
        assert result.basis == "unity_type_name"


def test_unknown_or_empty_type_is_not_guessed_from_name():
    for type_name in (None, "", "MyTexture2D", "UnknownCustomAsset"):
        result = classify_unity_type(type_name)
        assert result.category == "unknown"
        assert result.subcategory == "unknown"
        assert result.confidence == "unknown"
        assert result.basis == "unrecognized_unity_type"


def test_object_record_includes_classification_in_json_model():
    record = ObjectRecord(
        resource_id="id",
        source_id="source",
        container_id="container",
        path_id=1,
        type_name="AudioClip",
        name="background_music",
    )

    result = record.__dict__
    assert result["resource_category"] == "audio"
    assert result["resource_subcategory"] == "clip"
    assert result["classification_confidence"] == "high"
    assert result["classification_basis"] == "unity_type_name"
