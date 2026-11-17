from __future__ import annotations

from types import SimpleNamespace

from unity_extractor.backends.unitypy import UnityPyBackend


class FakePPtr:
    def __init__(self, file_id: int, path_id: int, container: str = "sharedassets0.assets"):
        self.m_FileID = file_id
        self.m_PathID = path_id
        self._container = container

    def deref(self):
        return SimpleNamespace(
            assets_file=SimpleNamespace(name=self._container)
        )


class FakeField:
    def __init__(self, name: str):
        self.name = name


class FakeTypedObject:
    __attrs_attrs__ = (FakeField("m_Reference"), FakeField("m_FontData"))

    def __init__(self, reference, font_data):
        self.m_Reference = reference
        self.m_FontData = font_data


class FakeObjectReader:
    def __init__(self, typed):
        self.typed = typed
        self.called = False

    def parse_as_object(self):
        self.called = True
        return self.typed

    def parse_as_dict(self):
        raise AssertionError("parse_as_dict must not be used for PPtr extraction")


def test_reference_traversal_uses_typed_objects_and_resolves_pptr():
    reader = FakeObjectReader(
        FakeTypedObject(FakePPtr(0, 42), [1, 2, 3])
    )
    backend = object.__new__(UnityPyBackend)

    references = list(backend.iter_references(reader))

    assert reader.called
    assert references == [(0, 42, "sharedassets0.assets", True)]


def test_large_primitive_arrays_do_not_consume_reference_node_budget():
    reader = FakeObjectReader(
        FakeTypedObject(FakePPtr(0, 7), list(range(150_000)))
    )
    backend = object.__new__(UnityPyBackend)

    references = list(backend.iter_references(reader))

    assert references == [(0, 7, "sharedassets0.assets", True)]


def test_unresolvable_pptr_is_preserved_as_unresolved():
    class BrokenPPtr:
        m_FileID = 3
        m_PathID = 99

        def deref(self):
            raise FileNotFoundError("external file not found")

    reader = FakeObjectReader(FakeTypedObject(BrokenPPtr(), []))
    backend = object.__new__(UnityPyBackend)

    references = list(backend.iter_references(reader))

    assert references == [(3, 99, None, False)]
