from __future__ import annotations

import pandas as pd

# expiry: pandas Timestamp (date of expiration); right: "C" or "P".
# source_iv is the data vendor's own IV (decimal), kept only for comparison.
CHAIN_COLUMNS = ["expiry", "strike", "right", "bid", "ask", "last", "volume", "open_interest", "source_iv"]


def empty_chain() -> pd.DataFrame:
    return pd.DataFrame(columns=CHAIN_COLUMNS)


def normalize_chain(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return empty_chain()
    out = df.copy()
    for col in CHAIN_COLUMNS:
        if col not in out:
            out[col] = float("nan")
    out["expiry"] = pd.to_datetime(out["expiry"]).dt.normalize()
    out["right"] = out["right"].astype(str).str.upper().str[0]
    for col in ("strike", "bid", "ask", "last", "volume", "open_interest", "source_iv"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out[CHAIN_COLUMNS].reset_index(drop=True)
