//! A small in-memory inventory ledger.
//!
//! Mirrors the Python / TypeScript / Go fixtures: items keyed by name
//! with integer quantities, restock + guarded withdrawal, and aggregate
//! reporting. Idiomatic Rust for the code-embedder lane.

use std::collections::HashMap;

/// Why a withdrawal failed.
#[derive(Debug, PartialEq, Eq)]
pub enum WithdrawError {
    /// No item is registered under the requested name.
    Unknown,
    /// The item exists but does not have enough units on hand.
    OutOfStock { have: u32, need: u32 },
}

/// A single inventory line: a name and the quantity in stock.
#[derive(Debug, Clone)]
pub struct Item {
    pub name: String,
    pub quantity: u32,
}

/// A collection of items keyed by name.
#[derive(Debug, Default)]
pub struct Inventory {
    items: HashMap<String, Item>,
}

impl Inventory {
    /// Create an empty inventory.
    pub fn new() -> Self {
        Inventory {
            items: HashMap::new(),
        }
    }

    /// Add `amount` units of `name`, creating the item if needed.
    pub fn restock(&mut self, name: &str, amount: u32) {
        match self.items.get_mut(name) {
            Some(item) => item.quantity += amount,
            None => {
                self.items.insert(
                    name.to_string(),
                    Item {
                        name: name.to_string(),
                        quantity: amount,
                    },
                );
            }
        }
    }

    /// Remove `amount` units of `name`, returning the new quantity.
    pub fn withdraw(&mut self, name: &str, amount: u32) -> Result<u32, WithdrawError> {
        let item = self.items.get_mut(name).ok_or(WithdrawError::Unknown)?;
        if item.quantity < amount {
            return Err(WithdrawError::OutOfStock {
                have: item.quantity,
                need: amount,
            });
        }
        item.quantity -= amount;
        Ok(item.quantity)
    }

    /// Sum of all quantities across every item.
    pub fn total_units(&self) -> u32 {
        self.items.values().map(|item| item.quantity).sum()
    }
}
