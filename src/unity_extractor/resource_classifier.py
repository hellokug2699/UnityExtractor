"""Deterministic, conservative classification of Unity native object types."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceClassification:
    category: str
    subcategory: str
    confidence: str
    basis: str


# Exact Unity type names only. Never infer a resource type from its filename,
# object name, extension, or serialized payload.
_TYPE_GROUPS: dict[str, tuple[str, str]] = {
    "Texture2D": ("image", "texture"),
    "Texture": ("image", "texture"),
    "Texture2DArray": ("image", "texture_array"),
    "Cubemap": ("image", "cubemap"),
    "RenderTexture": ("image", "render_texture"),
    "Sprite": ("image", "sprite"),
    "SpriteAtlas": ("image", "sprite_atlas"),
    "AudioClip": ("audio", "clip"),
    "AudioMixer": ("audio", "mixer"),
    "AudioMixerController": ("audio", "mixer_controller"),
    "Mesh": ("model", "mesh"),
    "MeshFilter": ("model", "mesh_filter"),
    "SkinnedMeshRenderer": ("model", "skinned_mesh_renderer"),
    "Material": ("material", "material"),
    "Shader": ("material", "shader"),
    "ShaderVariantCollection": ("material", "shader_variants"),
    "AnimationClip": ("animation", "clip"),
    "Animator": ("animation", "animator"),
    "AnimatorController": ("animation", "controller"),
    "Avatar": ("animation", "avatar"),
    "AvatarMask": ("animation", "avatar_mask"),
    "GameObject": ("scene", "game_object"),
    "Transform": ("scene", "transform"),
    "RectTransform": ("scene", "rect_transform"),
    "SceneAsset": ("scene", "scene_asset"),
    "Scene": ("scene", "scene"),
    "MonoBehaviour": ("script", "behaviour"),
    "MonoScript": ("script", "script"),
    "AssemblyDefinitionAsset": ("script", "assembly_definition"),
    "TextAsset": ("data", "text_asset"),
    "ScriptableObject": ("data", "scriptable_object"),
    "Font": ("font", "font"),
    "VideoClip": ("video", "clip"),
    "AssetBundle": ("container", "asset_bundle"),
    "ResourceManager": ("container", "resource_manager"),
}


def classify_unity_type(type_name: str | None) -> ResourceClassification:
    """Classify an exact Unity type name; preserve uncertainty for unknown types."""
    normalized = type_name.strip() if isinstance(type_name, str) else ""
    group = _TYPE_GROUPS.get(normalized)
    if group is None:
        return ResourceClassification(
            category="unknown",
            subcategory="unknown",
            confidence="unknown",
            basis="unrecognized_unity_type",
        )
    return ResourceClassification(
        category=group[0],
        subcategory=group[1],
        confidence="high",
        basis="unity_type_name",
    )
