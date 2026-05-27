// Package inventory implements a small in-memory inventory ledger.
//
// It mirrors the Python / TypeScript / Rust fixtures: items keyed by
// name with integer quantities, restock + guarded withdrawal, and
// aggregate reporting. Idiomatic Go for the code-embedder lane.
package inventory

import (
	"errors"
	"fmt"
)

// ErrOutOfStock is returned when a withdrawal exceeds the quantity on hand.
var ErrOutOfStock = errors.New("out of stock")

// Item is a single inventory line: a name and the quantity in stock.
type Item struct {
	Name     string
	Quantity int
}

// Inventory is a collection of items keyed by name.
type Inventory struct {
	items map[string]*Item
}

// New returns an empty, ready-to-use Inventory.
func New() *Inventory {
	return &Inventory{items: make(map[string]*Item)}
}

// Restock adds amount units of name, creating the item if needed.
func (inv *Inventory) Restock(name string, amount int) error {
	if amount < 0 {
		return errors.New("amount must be non-negative")
	}
	if item, ok := inv.items[name]; ok {
		item.Quantity += amount
		return nil
	}
	inv.items[name] = &Item{Name: name, Quantity: amount}
	return nil
}

// Withdraw removes amount units of name and returns the new quantity.
//
// It returns a wrapped ErrOutOfStock if too few units are available.
func (inv *Inventory) Withdraw(name string, amount int) (int, error) {
	item, ok := inv.items[name]
	if !ok {
		return 0, fmt.Errorf("unknown item %q", name)
	}
	if item.Quantity < amount {
		return 0, fmt.Errorf("%s: %w", name, ErrOutOfStock)
	}
	item.Quantity -= amount
	return item.Quantity, nil
}

// TotalUnits returns the sum of all quantities across every item.
func (inv *Inventory) TotalUnits() int {
	total := 0
	for _, item := range inv.items {
		total += item.Quantity
	}
	return total
}
