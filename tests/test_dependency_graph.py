from __future__ import annotations

from unity_extractor.dependency_graph import build_dependency_graph


def obj(resource_id: str, *, name: str | None = None, type_name: str = "TextAsset"):
    return {
        "resource_id": resource_id,
        "source_id": "fixture.apk!/assets/test.bundle",
        "container_id": "sharedassets0.assets",
        "path_id": len(resource_id),
        "type_name": type_name,
        "name": name or resource_id,
    }


def ref(source: str, target: str | None, *, resolved: bool = True):
    return {
        "source_resource_id": source,
        "target_resource_id": target,
        "resolved": resolved,
    }


def test_builds_forward_and_reverse_dependency_degrees():
    objects = [obj("a"), obj("b"), obj("c"), obj("d")]
    references = [ref("a", "b"), ref("a", "c"), ref("b", "c")]

    graph = build_dependency_graph(objects, references)
    nodes = {item["resource_id"]: item for item in graph["nodes"]}

    assert graph["summary"]["node_count"] == 4
    assert graph["summary"]["edge_count"] == 3
    assert nodes["a"]["dependency_count"] == 2
    assert nodes["a"]["dependent_count"] == 0
    assert nodes["c"]["dependency_count"] == 0
    assert nodes["c"]["dependent_count"] == 2
    assert graph["unreferenced_resource_ids"] == ["a", "d"]
    assert graph["isolated_resource_ids"] == ["d"]
    assert graph["terminal_resource_ids"] == ["c", "d"]


def test_aggregates_duplicate_edges_without_losing_reference_count():
    graph = build_dependency_graph(
        [obj("a"), obj("b")],
        [ref("a", "b"), ref("a", "b"), ref("a", "b")],
    )

    assert graph["summary"]["edge_count"] == 1
    assert graph["edges"] == [{
        "source_resource_id": "a",
        "target_resource_id": "b",
        "reference_count": 3,
    }]


def test_counts_unresolved_and_dangling_references_separately():
    graph = build_dependency_graph(
        [obj("a"), obj("b")],
        [
            ref("a", None, resolved=False),
            ref("a", "missing", resolved=True),
            ref("a", "b", resolved=True),
        ],
    )

    assert graph["summary"]["reference_count"] == 3
    assert graph["summary"]["unresolved_reference_count"] == 1
    assert graph["summary"]["dangling_reference_count"] == 1
    assert graph["summary"]["edge_count"] == 1


def test_detects_cycles_including_self_loops():
    graph = build_dependency_graph(
        [obj("a"), obj("b"), obj("c"), obj("d")],
        [ref("a", "b"), ref("b", "a"), ref("c", "c"), ref("b", "c")],
    )

    assert graph["cycles"] == [["a", "b"], ["c"]]
    assert graph["summary"]["cycle_count"] == 2


def test_returns_shortest_dependency_chains_from_requested_root():
    graph = build_dependency_graph(
        [obj("a"), obj("b"), obj("c"), obj("d")],
        [ref("a", "b"), ref("a", "c"), ref("b", "d"), ref("c", "d")],
        root_resource_id="a",
        max_depth=2,
    )

    chains = {item["resource_id"]: item for item in graph["dependency_chains"]}
    assert set(chains) == {"a", "b", "c", "d"}
    assert chains["a"]["path"] == ["a"]
    assert chains["d"]["depth"] == 2
    assert chains["d"]["path"] in (["a", "b", "d"], ["a", "c", "d"])
    assert graph["dependency_chain_error"] is None


def test_missing_chain_root_is_reported_without_failing_graph_build():
    graph = build_dependency_graph([obj("a")], [], root_resource_id="missing")

    assert graph["dependency_chains"] == []
    assert graph["dependency_chain_error"]


def test_long_graph_does_not_depend_on_python_recursion_limit():
    objects = [obj(str(index)) for index in range(1_200)]
    references = [ref(str(index), str(index + 1)) for index in range(1_199)]

    graph = build_dependency_graph(objects, references)

    assert graph["summary"]["node_count"] == 1_200
    assert graph["summary"]["edge_count"] == 1_199
    assert graph["summary"]["cycle_count"] == 0
