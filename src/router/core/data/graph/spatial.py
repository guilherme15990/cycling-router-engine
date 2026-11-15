"""Lazy immutable coordinate/bbox index for explicit network extracts."""
from __future__ import annotations

from bisect import bisect_left, bisect_right


class GraphSpatialIndex:
    def __init__(self, nodes, arcs):
        self._nodes = sorted((node.lat, index, node.lon, node.id)
                             for index, node in enumerate(nodes.values()))
        self._latitudes = [item[0] for item in self._nodes]
        boxes = []
        for index, arc in enumerate(arcs.values()):
            points = arc.geometry or (nodes[arc.source].coord, nodes[arc.target].coord)
            south = min(point[0] for point in points)
            north = max(point[0] for point in points)
            west = min(point[1] for point in points)
            east = max(point[1] for point in points)
            boxes.append((south, index, north, west, east, arc.id))
        self._boxes = sorted(boxes)
        self._souths = [item[0] for item in self._boxes]

    def node_ids(self, bbox):
        south, west, north, east = bbox
        start, end = bisect_left(self._latitudes, south), bisect_right(self._latitudes, north)
        return tuple(node_id for _, _, lon, node_id in self._nodes[start:end] if west <= lon <= east)

    def arc_ids(self, bbox):
        south, west, north, east = bbox
        end = bisect_right(self._souths, north)
        return tuple(arc_id for _, _, top, left, right, arc_id in self._boxes[:end]
                     if top >= south and left <= east and right >= west)
