from __future__ import annotations

from collections import defaultdict
from math import floor
from typing import Generic, Iterable, Iterator, TypeVar

T = TypeVar("T")


class SpatialHashIndex(Generic[T]):
    """Simple 2D grid-hash index for candidate neighbor pruning."""

    def __init__(self, cell_size_nm: float) -> None:
        if cell_size_nm <= 0:
            raise ValueError("cell_size_nm must be positive")
        self.cell_size_nm = float(cell_size_nm)
        self._cells: dict[tuple[int, int], list[tuple[float, float, T]]] = defaultdict(list)

    def _cell_for(self, x_nm: float, y_nm: float) -> tuple[int, int]:
        return floor(x_nm / self.cell_size_nm), floor(y_nm / self.cell_size_nm)

    def insert(self, x_nm: float, y_nm: float, item: T) -> None:
        self._cells[self._cell_for(x_nm, y_nm)].append((x_nm, y_nm, item))

    def query_neighbors(self, x_nm: float, y_nm: float) -> Iterator[T]:
        cx, cy = self._cell_for(x_nm, y_nm)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for _px, _py, item in self._cells.get((cx + dx, cy + dy), []):
                    yield item

    def query_bbox(self, min_x_nm: float, min_y_nm: float, max_x_nm: float, max_y_nm: float) -> Iterator[T]:
        min_cx, min_cy = self._cell_for(min_x_nm, min_y_nm)
        max_cx, max_cy = self._cell_for(max_x_nm, max_y_nm)
        for cx in range(min_cx, max_cx + 1):
            for cy in range(min_cy, max_cy + 1):
                for px, py, item in self._cells.get((cx, cy), []):
                    if min_x_nm <= px <= max_x_nm and min_y_nm <= py <= max_y_nm:
                        yield item

    def items(self) -> Iterable[T]:
        for records in self._cells.values():
            for _x, _y, item in records:
                yield item
