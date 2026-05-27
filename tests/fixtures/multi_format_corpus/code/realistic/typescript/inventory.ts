/**
 * A small in-memory inventory ledger.
 *
 * Mirrors the Python / Go / Rust fixtures: items keyed by name with
 * integer quantities, restock + withdrawal (guarded), and aggregate
 * reporting. Richer, idiomatic TypeScript for the code-embedder lane.
 */

/** A single inventory line: a name and the quantity in stock. */
export interface Item {
  name: string;
  quantity: number;
}

/** Either a successful new quantity or a typed failure reason. */
export type WithdrawResult =
  | { ok: true; remaining: number }
  | { ok: false; reason: "unknown" | "out-of-stock" };

/** An ordered collection of items keyed by name. */
export class Inventory {
  private readonly items = new Map<string, Item>();

  /** Add `amount` units of `name`, creating the item if needed. */
  restock(name: string, amount: number): void {
    if (amount < 0) {
      throw new Error("amount must be non-negative");
    }
    const existing = this.items.get(name);
    if (existing === undefined) {
      this.items.set(name, { name, quantity: amount });
    } else {
      existing.quantity += amount;
    }
  }

  /** Remove `amount` units, returning a typed result rather than throwing. */
  withdraw(name: string, amount: number): WithdrawResult {
    const item = this.items.get(name);
    if (item === undefined) {
      return { ok: false, reason: "unknown" };
    }
    if (item.quantity < amount) {
      return { ok: false, reason: "out-of-stock" };
    }
    item.quantity -= amount;
    return { ok: true, remaining: item.quantity };
  }

  /** Sum of all quantities across every item. */
  totalUnits(): number {
    let total = 0;
    for (const item of this.items.values()) {
      total += item.quantity;
    }
    return total;
  }
}

/** Build an Inventory from `[name, quantity]` pairs, skipping bad rows. */
export function buildInventory(pairs: ReadonlyArray<[string, number]>): Inventory {
  const inv = new Inventory();
  for (const [name, quantity] of pairs) {
    try {
      inv.restock(name, quantity);
    } catch {
      // Skip malformed rows but keep ingesting the rest.
      continue;
    }
  }
  return inv;
}
