"""Minimal, dependency-free Aho-Corasick multi-pattern matcher.

Why not a C extension: Windows developer machines without MSVC should be able to
run the whole test suite. Pattern counts here are ~10^3, where a clean Python
implementation is fast enough and - more importantly - fully testable.

Returns ALL matches, including overlapping and nested ones; callers decide how to
resolve overlaps (the matcher keeps the longest hit per family).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator


class AhoCorasick:
    def __init__(self) -> None:
        self._children: list[dict[str, int]] = [{}]
        self._fail: list[int] = [0]
        self._outputs: list[list[tuple[object, int]]] = [[]]  # (payload, pattern_length)
        self._built = False

    def add(self, pattern: str, payload: object) -> None:
        if not pattern:
            raise ValueError("pattern must not be empty")
        if self._built:
            raise RuntimeError("cannot add patterns after build()")
        node = 0
        for char in pattern:
            next_node = self._children[node].get(char)
            if next_node is None:
                next_node = len(self._children)
                self._children[node][char] = next_node
                self._children.append({})
                self._fail.append(0)
                self._outputs.append([])
            node = next_node
        self._outputs[node].append((payload, len(pattern)))

    def build(self) -> "AhoCorasick":
        queue: deque[int] = deque()
        for child in self._children[0].values():
            self._fail[child] = 0
            queue.append(child)
        while queue:
            current = queue.popleft()
            for char, child in self._children[current].items():
                queue.append(child)
                fail = self._fail[current]
                while fail and char not in self._children[fail]:
                    fail = self._fail[fail]
                self._fail[child] = self._children[fail].get(char, 0)
                # Merge suffix outputs so patterns that end here also match.
                self._outputs[child].extend(self._outputs[self._fail[child]])
        self._built = True
        return self

    def finditer(self, text: str) -> Iterator[tuple[int, int, object]]:
        """Yield ``(start, end, payload)`` for every pattern occurrence."""
        if not self._built:
            raise RuntimeError("build() must be called before finditer()")
        node = 0
        for index, char in enumerate(text):
            while node and char not in self._children[node]:
                node = self._fail[node]
            node = self._children[node].get(char, 0)
            for payload, pattern_length in self._outputs[node]:
                yield index - pattern_length + 1, index + 1, payload

    def find_all(self, text: str) -> list[tuple[int, int, object]]:
        return list(self.finditer(text))

    @property
    def pattern_count(self) -> int:
        return sum(len(outputs) for outputs in self._outputs)
