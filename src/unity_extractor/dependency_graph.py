"""Build bounded, JSON-safe dependency graph reports from enumerated objects."""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _strongly_connected_components(
    nodes: Iterable[str],
    adjacency: dict[str, set[str]],
    reverse: dict[str, set[str]],
) -> list[list[str]]:
    """Iterative Kosaraju traversal; avoids recursion limits on long asset chains."""
    ordered = sorted(nodes)
    visited: set[str] = set()
    finish: list[str] = []

    for start in ordered:
        if start in visited:
            continue
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            node, expanded = stack.pop()
            if expanded:
                finish.append(node)
                continue
            if node in visited:
                continue
            visited.add(node)
            stack.append((node, True))
            for target in sorted(adjacency.get(node, ()), reverse=True):
                if target not in visited:
                    stack.append((target, False))

    visited.clear()
    components: list[list[str]] = []
    for start in reversed(finish):
        if start in visited:
            continue
        component: list[str] = []
        stack = [start]
        visited.add(start)
        while stack:
            node = stack.pop()
            component.append(node)
            for source in sorted(reverse.get(node, ()), reverse=True):
                if source not in visited:
                    visited.add(source)
                    stack.append(source)
        if len(component) > 1 or start in adjacency.get(start, set()):
            components.append(sorted(component))
    components.sort(key=lambda group: (group[0], len(group)))
    return components


def build_dependency_graph(
    objects: Iterable[Any],
    references: Iterable[Any],
    *,
    root_resource_id: str | None = None,
    max_depth: int = 8,
    max_chain_nodes: int = 100_000,
) -> dict[str, Any]:
    """Create a graph summary, deduplicated edges, node degrees, SCCs and optional paths.

    Edges point from the referencing object to the referenced object. Only
    resolved references whose endpoints are present in the object inventory
    become graph edges; unresolved and dangling references remain counted.
    """
    if max_depth < 0:
        raise ValueError("max_depth must be zero or greater.")
    if max_chain_nodes < 1:
        raise ValueError("max_chain_nodes must be at least one.")

    object_by_id: dict[str, Any] = {}
    for obj in objects:
        resource_id = _value(obj, "resource_id")
        if isinstance(resource_id, str) and resource_id:
            object_by_id.setdefault(resource_id, obj)

    node_ids = set(object_by_id)
    adjacency: dict[str, set[str]] = {node: set() for node in node_ids}
    reverse: dict[str, set[str]] = {node: set() for node in node_ids}
    edge_reference_counts: dict[tuple[str, str], int] = defaultdict(int)
    unresolved_count = 0
    dangling_count = 0
    total_reference_count = 0

    for ref in references:
        total_reference_count += 1
        source = _value(ref, "source_resource_id")
        target = _value(ref, "target_resource_id")
        if not _value(ref, "resolved", False):
            unresolved_count += 1
            continue
        if source not in node_ids or target not in node_ids:
            dangling_count += 1
            continue
        adjacency[source].add(target)
        reverse[target].add(source)
        edge_reference_counts[(source, target)] += 1

    edges = [
        {
            "source_resource_id": source,
            "target_resource_id": target,
            "reference_count": count,
        }
        for (source, target), count in sorted(edge_reference_counts.items())
    ]

    nodes = []
    for resource_id in sorted(node_ids):
        obj = object_by_id[resource_id]
        dependency_count = len(adjacency[resource_id])
        dependent_count = len(reverse[resource_id])
        nodes.append({
            "resource_id": resource_id,
            "source_id": _value(obj, "source_id"),
            "container_id": _value(obj, "container_id"),
            "path_id": _value(obj, "path_id"),
            "type_name": _value(obj, "type_name"),
            "name": _value(obj, "name"),
            "dependency_count": dependency_count,
            "dependent_count": dependent_count,
            "is_unreferenced": dependent_count == 0,
            "is_isolated": dependency_count == 0 and dependent_count == 0,
            "is_terminal": dependency_count == 0,
        })

    unreferenced = sorted(node for node in node_ids if not reverse[node])
    isolated = sorted(node for node in node_ids if not adjacency[node] and not reverse[node])
    terminal = sorted(node for node in node_ids if not adjacency[node])
    components = _strongly_connected_components(node_ids, adjacency, reverse)

    chains: list[dict[str, Any]] = []
    root_error = None
    chains_truncated = False
    if root_resource_id is not None:
        if root_resource_id not in node_ids:
            root_error = "Requested root resource_id was not found in the object inventory."
        else:
            queue = deque([(root_resource_id, [root_resource_id], 0)])
            visited = {root_resource_id}
            while queue:
                current, path, depth = queue.popleft()
                chains.append({
                    "resource_id": current,
                    "depth": depth,
                    "path": path,
                })
                if depth >= max_depth:
                    continue
                for target in sorted(adjacency[current]):
                    if target in visited:
                        continue
                    if len(visited) >= max_chain_nodes:
                        chains_truncated = True
                        queue.clear()
                        break
                    visited.add(target)
                    queue.append((target, path + [target], depth + 1))

    return {
        "schema_version": "1.0",
        "edge_direction": "source_resource_id -> target_resource_id",
        "summary": {
            "node_count": len(node_ids),
            "edge_count": len(edges),
            "reference_count": total_reference_count,
            "unresolved_reference_count": unresolved_count,
            "dangling_reference_count": dangling_count,
            "unreferenced_object_count": len(unreferenced),
            "isolated_object_count": len(isolated),
            "terminal_object_count": len(terminal),
            "cycle_count": len(components),
        },
        "nodes": nodes,
        "edges": edges,
        "unreferenced_resource_ids": unreferenced,
        "isolated_resource_ids": isolated,
        "terminal_resource_ids": terminal,
        "cycles": components,
        "dependency_chains": chains,
        "dependency_chain_root": root_resource_id,
        "dependency_chain_error": root_error,
        "dependency_chains_truncated": chains_truncated,
    }
