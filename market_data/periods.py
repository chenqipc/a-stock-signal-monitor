"""证券分钟周期转换工具。"""

import pandas as pd


A_SHARE_120MIN_SESSIONS = {
    "10:30": "morning",
    "11:30": "morning",
    "14:00": "afternoon",
    "15:00": "afternoon",
}


def merge_a_share_60min_to_120min(data):
    """按A股上午、下午固定交易时段合并，仅返回由两根完整60分钟K线组成的120分钟K线。"""
    if data is None or data.empty:
        return data
    result = data.copy()
    result["trade_time"] = pd.to_datetime(result["trade_time"], errors="coerce")
    result = result.dropna(subset=["trade_time", "close"]).sort_values("trade_time")
    result = result.drop_duplicates("trade_time", keep="last")
    result["trade_date"] = result["trade_time"].dt.date
    result["bar_clock"] = result["trade_time"].dt.strftime("%H:%M")
    result["session"] = result["bar_clock"].map(A_SHARE_120MIN_SESSIONS)
    result = result.dropna(subset=["session"])
    if result.empty:
        return result.drop(columns=["trade_date", "bar_clock", "session"], errors="ignore")
    group_columns = ["trade_date", "session"]
    complete_groups = result.groupby(group_columns)["bar_clock"].transform("nunique") == 2
    result = result[complete_groups]
    if result.empty:
        return result.drop(columns=["trade_date", "bar_clock", "session"], errors="ignore")
    aggregations = {
        "trade_time": ("trade_time", "max"),
        "open": ("open", "first"),
        "close": ("close", "last"),
        "high": ("high", "max"),
        "low": ("low", "min"),
    }
    for column, method in (("vol", "sum"), ("amount", "sum"), ("pre_close", "first"), ("turnover_rate", "sum"), ("is_st", "max")):
        if column in result.columns:
            aggregations[column] = (column, method)
    merged = result.groupby(group_columns, as_index=False).agg(**aggregations)
    if "pre_close" in merged.columns:
        merged["pct_chg"] = (merged["close"] / merged["pre_close"] - 1) * 100
    merged = merged.drop(columns=group_columns).sort_values("trade_time").reset_index(drop=True)
    merged.attrs.update(data.attrs)
    return merged
