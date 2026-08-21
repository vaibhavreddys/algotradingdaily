import math
from typing import Optional
from config import CONFIG, TradingConfig


def get_slot_margin(current_capital: float, max_concurrent_positions: int) -> float:
    """
    Splits the current available account capital equally across max_concurrent_positions slots.

    Args:
        current_capital: Total active equity / capital balance in INR.
        max_concurrent_positions: Number of concurrent position slots (e.g. 2).

    Returns:
        Cash margin allocated for a single open position slot.
    """
    if max_concurrent_positions <= 0:
        raise ValueError("max_concurrent_positions must be greater than 0")
    if current_capital <= 0:
        return 0.0
    return current_capital / max_concurrent_positions


def get_slot_exposure(
    current_capital: float,
    max_concurrent_positions: int,
    leverage_mis: int = 5
) -> float:
    """
    Calculates the gross trading exposure (buying power) for one position slot with MIS intraday leverage.

    Args:
        current_capital: Total active equity / capital balance in INR.
        max_concurrent_positions: Number of concurrent position slots.
        leverage_mis: Broker MIS leverage multiplier (default: 5x).

    Returns:
        Gross turnover capacity allocated for the trade in INR.
    """
    slot_margin = get_slot_margin(current_capital, max_concurrent_positions)
    return slot_margin * leverage_mis


def calculate_order_quantity(
    entry_price: float,
    current_capital: float,
    max_concurrent_positions: int,
    leverage_mis: int = 5
) -> int:
    """
    Calculates the discrete integer number of shares to buy/short for an entry.

    Args:
        entry_price: Executed or projected fill price of the underlying asset.
        current_capital: Total active equity / capital balance in INR.
        max_concurrent_positions: Number of concurrent position slots.
        leverage_mis: Broker MIS leverage multiplier.

    Returns:
        Integer share quantity (minimum 1 if capital allows, or 0 if capital is depleted).
    """
    if entry_price <= 0 or current_capital <= 0:
        return 0
    exposure = get_slot_exposure(current_capital, max_concurrent_positions, leverage_mis)
    qty = int(math.floor(exposure / entry_price))
    return max(1, qty) if exposure >= entry_price else 0


def get_persisted_paper_capital(
    initial_capital: float = 10000.0,
    mode: str = "paper"
) -> float:
    """
    Reconstructs the cumulative paper trading account balance by summing initial capital
    with all historical realized net PnL from the local SQLite trade journal.

    Args:
        initial_capital: Starting seed capital in INR (default: 10,000.0).
        mode: Database mode ('paper' or 'live').

    Returns:
        Current real-time accumulated capital balance in INR.
    """
    try:
        from core.trade_db import get_db_connection, init_db
        init_db(mode)
        with get_db_connection(mode) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT balance_after_trade FROM trade_history ORDER BY id DESC LIMIT 1;")
            row = cursor.fetchone()
            if row and row[0] is not None:
                return float(row[0])
            cursor.execute("SELECT SUM(net_pnl) FROM trade_history;")
            cum_sum = cursor.fetchone()[0]
            if cum_sum is not None:
                return initial_capital + float(cum_sum)
        return initial_capital
    except Exception:
        return initial_capital
