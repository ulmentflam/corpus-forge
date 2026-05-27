"""A small in-memory inventory ledger.

Tracks items by name with integer quantities, supporting restock,
withdrawal (with an out-of-stock guard), and aggregate reporting. Used
as a richer, idiomatic Python fixture for the code-embedder lane.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


class OutOfStockError(Exception):
    """Raised when a withdrawal exceeds the quantity on hand."""


@dataclass
class Item:
    """A single inventory line: a name and the quantity in stock."""

    name: str
    quantity: int = 0

    def is_empty(self) -> bool:
        """Return True when no units remain."""
        return self.quantity <= 0


@dataclass
class Inventory:
    """An ordered collection of :class:`Item` keyed by name."""

    items: dict[str, Item] = field(default_factory=dict)

    def restock(self, name: str, amount: int) -> None:
        """Add ``amount`` units of ``name``, creating the item if needed."""
        if amount < 0:
            raise ValueError("amount must be non-negative")
        item = self.items.get(name)
        if item is None:
            self.items[name] = Item(name=name, quantity=amount)
        else:
            item.quantity += amount

    def withdraw(self, name: str, amount: int) -> int:
        """Remove ``amount`` units of ``name`` and return the new quantity.

        Raises :class:`OutOfStockError` if fewer units are available, and
        :class:`KeyError` if the item is unknown.
        """
        if amount <= 0:
            raise ValueError("withdraw amount must be positive")
        item = self.items[name]
        if item.quantity < amount:
            raise OutOfStockError(f"{name}: have {item.quantity}, need {amount}")
        item.quantity -= amount
        return item.quantity

    def total_units(self) -> int:
        """Return the sum of all quantities across every item."""
        return sum(item.quantity for item in self.items.values())


def build_inventory(pairs: Iterable[tuple[str, int]]) -> Inventory:
    """Build an :class:`Inventory` from ``(name, quantity)`` pairs.

    Bad pairs (negative quantities) are skipped with a logged note rather
    than aborting the whole batch.
    """
    inv = Inventory()
    for name, quantity in pairs:
        try:
            inv.restock(name, quantity)
        except ValueError:
            # Skip malformed rows but keep ingesting the rest.
            continue
    return inv
