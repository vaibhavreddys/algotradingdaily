"""
Strategy Discovery & Dynamic Registry Gateway.

Scans the `strategies/` directory to discover all strategy families
and their available version files (e.g. `v1_0.py`, `v1_1.py`).
"""

import os
import sys
import importlib
import inspect
from pathlib import Path
from typing import Dict, List, Any, Optional

from strategies.base_strategy import BaseStrategy

STRATEGIES_ROOT = Path(__file__).resolve().parent


def discover_strategies() -> List[Dict[str, Any]]:
    """
    Scans strategies/ directory and returns a structured list:
    [
        {
            "id": "vwap_stoch_breakdown",
            "name": "VWAP-Stochastic RSI Breakdown",
            "versions": [
                {"version": "1.0.0", "module": "v1_0", "timeframe": "15m", "is_default": True}
            ]
        }
    ]
    """
    discovered = []
    
    for item in STRATEGIES_ROOT.iterdir():
        if item.is_dir() and not item.name.startswith((".", "_")) and item.name != "__pycache__":
            family_id = item.name
            versions = []
            family_name = family_id.replace("_", " ").title()
            
            for py_file in sorted(item.glob("v*.py")):
                mod_name = py_file.stem
                try:
                    full_module = f"strategies.{family_id}.{mod_name}"
                    if full_module in sys.modules:
                        mod = importlib.reload(sys.modules[full_module])
                    else:
                        mod = importlib.import_module(full_module)
                    
                    strat_instance = getattr(mod, "STRATEGY_INSTANCE", None)
                    if strat_instance and isinstance(strat_instance, BaseStrategy):
                        family_name = getattr(strat_instance, "NAME", family_name)
                        v_str = getattr(strat_instance, "VERSION", "1.0.0")
                        tf_str = getattr(strat_instance, "TIMEFRAME", "15m")
                        versions.append({
                            "version": v_str,
                            "module": mod_name,
                            "timeframe": tf_str,
                            "is_default": (mod_name == "v1_0")
                        })
                except Exception:
                    pass
            
            if versions:
                discovered.append({
                    "id": family_id,
                    "name": family_name,
                    "versions": versions
                })
                
    return discovered


def load_strategy_instance(strategy_id: str, version_module: str = "v1_0") -> Optional[BaseStrategy]:
    """
    Dynamically loads and returns a BaseStrategy instance.
    """
    full_module = f"strategies.{strategy_id}.{version_module}"
    try:
        mod = importlib.import_module(full_module)
        return getattr(mod, "STRATEGY_INSTANCE", None)
    except Exception as exc:
        raise RuntimeError(f"Could not load strategy {full_module}: {exc}") from exc
