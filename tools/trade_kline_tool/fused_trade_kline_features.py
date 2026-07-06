from __future__ import annotations

import sys
from pathlib import Path


def enhance_trade_kline_html(html: str, statement: Path | str | None = None, trades=None) -> str:
    """Apply the position/no-quote fused chart layer used by the static prototype."""
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from build_position_fused_trade_kline_demo import (
            fallback_position_meta,
            inject_fused_features,
            inject_position_meta,
            load_position_meta,
        )
    except ModuleNotFoundError:
        return html

    html = inject_fused_features(html)
    if statement is not None and trades is not None:
        statement_path = Path(statement)
        if statement_path.exists():
            try:
                meta = load_position_meta(statement_path, trades)
            except Exception as exc:
                meta = fallback_position_meta(trades, statement_path)
                meta["positionMetaWarning"] = str(exc)
            html = inject_position_meta(html, meta)
    return html
