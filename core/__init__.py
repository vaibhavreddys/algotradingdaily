"""
Core Package.

Provides core trading analytics, risk controls, position management,
and trade persistence models.
"""

from .indicators import (
    compute_adx,
    compute_relative_weakness,
    compute_stoch_rsi,
    compute_vwap,
)

from .risk import (
    calculate_risk_based_quantity,
    should_trail_to_breakeven,
    is_daily_loss_limit_reached,
)

from .capital import (
    get_slot_margin,
    get_slot_exposure,
    calculate_order_quantity,
    get_persisted_paper_capital,
)

from .charges import (
    calculate_charges,
    BROKER_CHARGES_CONFIG,
)

from .trade_db import (
    TradeExitReason,
    EXIT_DISPLAY_LABELS,
    get_db_path,
    get_db_connection,
    init_db,
    save_active_position,
    update_trailing_sl,
    close_and_archive_position,
    get_active_positions,
    get_trade_journal,
    get_stale_positions,
    reconcile_stale_positions,
    get_today_realized_pnl,
)

from .report import (
    format_outcome_distribution,
    print_simulation_report,
    print_multi_broker_matrix,
    print_daily_eod_report,
)

__all__ = [
    "compute_adx",
    "compute_relative_weakness",
    "compute_stoch_rsi",
    "compute_vwap",
    "calculate_stop_and_target",
    "calculate_risk_based_quantity",
    "should_trail_to_breakeven",
    "is_daily_loss_limit_reached",
    "get_slot_margin",
    "get_slot_exposure",
    "calculate_order_quantity",
    "get_persisted_paper_capital",
    "calculate_charges",
    "BROKER_CHARGES_CONFIG",
    "TradeExitReason",
    "EXIT_DISPLAY_LABELS",
    "get_db_path",
    "get_db_connection",
    "init_db",
    "save_active_position",
    "update_trailing_sl",
    "close_and_archive_position",
    "get_active_positions",
    "get_trade_journal",
    "get_stale_positions",
    "reconcile_stale_positions",
    "get_today_realized_pnl",
    "format_outcome_distribution",
    "print_simulation_report",
    "print_multi_broker_matrix",
    "print_daily_eod_report",
]

from .market_calendar import (
    get_market_profile,
    get_market_timezone,
    is_continuous_market,
    get_market_open_close,
    get_strategy_entry_window,
    get_squareoff_time,
    get_platform_hard_squareoff_time,
    is_market_open,
    is_market_closed,
    is_entry_window_active,
    is_squareoff_time,
    get_seconds_until_market_open,
    get_seconds_until_entry_window,
    get_next_market_session,
)
