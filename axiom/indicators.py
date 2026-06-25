# Compatibility shim — agents naturally import from axiom.indicators but the
# module lives at axiom.strategies.indicators. Re-export the public API.
from axiom.strategies.indicators import (  # noqa: F401
    ParamSpec,
    IndicatorDef,
    indicator_kinds,
    output_names,
    default_panel,
    compute_indicator,
    metadata,
)
