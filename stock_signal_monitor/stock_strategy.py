import logging
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from math import ceil

import pandas as pd

from market_data.service import get_default_service
from stock_signal_monitor.stock_status import StockStatus


logger = logging.getLogger(__name__)


def daily_check(ts_code: str, stock_name: str, service=None) -> list[StockStatus]:
    """兼容单标的调用入口；数据获取完成后交给纯策略函数计算。"""
    start_date, end_date = daily_strategy_window()
    market_data_service = service or get_default_service()
    daily_data = market_data_service.get_daily_data(ts_code, start_date, end_date)
    return evaluate_daily_strategies(daily_data, ts_code, stock_name)


def evaluate_daily_strategies(daily_data: pd.DataFrame, ts_code: str, stock_name: str) -> list[StockStatus]:
    """只基于传入日线计算策略，不访问网络、数据库或全局服务。"""
    if daily_data is None or len(daily_data) < 7:
        return [StockStatus.NO_MATCH]
    sort_column = "trade_date" if "trade_date" in daily_data.columns else "trade_time"
    prepared_data = daily_data.sort_values(by=sort_column, ascending=True).reset_index(drop=True).copy()
    # ST、北交所和科创板均进入策略计算；名称用于补齐部分免费行情源缺失的ST标记。
    if "is_st" not in prepared_data.columns:
        prepared_data["is_st"] = "ST" in stock_name.upper()
    elif "ST" in stock_name.upper():
        prepared_data["is_st"] = True
    checks = (
        (StockStatus.LIMIT_UP_STREAK, lambda: is_limit_up_streak(prepared_data, ts_code)),
        (StockStatus.RISING_VOLUME_INCREASE, lambda: is_consecutive_rise_with_increasing_volume(prepared_data)),
        (StockStatus.VOLUME_SURGE_WITH_PRICE_RISE, lambda: is_volume_surge_with_price_rise(prepared_data, days=3)),
        (StockStatus.CAPITAL_INFLOW, lambda: is_consecutive_rise_with_amount_expansion(prepared_data)),
        (StockStatus.SUPPORT_LEVEL_REBOUND, lambda: is_ma10_support_rebound(prepared_data)),
        (StockStatus.SUPPORT_LEVEL_REBOUND_60, lambda: is_volume_breakout_ma60(prepared_data)),
        (StockStatus.MACD_GOLDEN_CROSS, lambda: is_macd_golden_cross(prepared_data)),
        (StockStatus.DOUBLE_BOTTOM, lambda: is_double_bottom(prepared_data)),
        (StockStatus.BREAKOUT_AFTER_CONSOLIDATION, lambda: is_breakout_after_consolidation(prepared_data)),
        (StockStatus.IS_UPWARD_TREND, lambda: calculate_upward_trend_score(prepared_data)["matched"]),
        (StockStatus.FUNDS_INFLOW_BY_VOLUME_TURNOVER, lambda: is_funds_inflow_by_volume_turnover(prepared_data)),
    )
    results = [status for status, check in checks if check()]
    return results or [StockStatus.NO_MATCH]


def build_daily_signal_details(daily_data: pd.DataFrame, results: list[StockStatus]) -> dict:
    """生成需要随信号持久化的解释信息；普通布尔策略无需重复保存详情。"""
    if StockStatus.IS_UPWARD_TREND not in results or daily_data is None or daily_data.empty:
        return {}
    sort_column = "trade_date" if "trade_date" in daily_data.columns else "trade_time"
    prepared_data = daily_data.sort_values(by=sort_column, ascending=True).reset_index(drop=True).copy()
    evaluation = calculate_upward_trend_score(prepared_data)
    return {
        StockStatus.IS_UPWARD_TREND.value: {
            "score": evaluation["score"],
            "total_score": evaluation["total_score"],
            "reasons": evaluation["reasons"],
            "metrics": evaluation["metrics"],
        }
    }


def daily_strategy_window(today=None):
    """统一日线策略窗口，供扫描调度和策略计算共用。"""
    today = today or datetime.today()
    history_start = today - timedelta(days=200)
    return history_start.strftime('%Y%m%d'), today.strftime('%Y%m%d')

def get_price_limit_ratio(ts_code, is_st=False):
    """按证券范围返回涨跌停比例，避免用统一9.89%判断所有板块。"""
    code = str(ts_code).split('.')[0]
    if is_st:
        return Decimal('0.05')
    if code.startswith(('4', '8', '92')):
        return Decimal('0.30')
    if code.startswith(('300', '301', '688')):
        return Decimal('0.20')
    return Decimal('0.10')


def is_limit_up_row(row, ts_code):
    """优先按前收价计算交易所价格精度下的涨停价，字段不足时才使用涨幅兜底。"""
    is_st = bool(row.get('is_st', False))
    ratio = get_price_limit_ratio(ts_code, is_st)
    pre_close = row.get('pre_close')
    close = row.get('close')
    if pre_close is not None and close is not None and pre_close > 0:
        limit_price = (Decimal(str(pre_close)) * (Decimal('1') + ratio)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return Decimal(str(close)) >= limit_price
    return row.get('pct_chg', float('-inf')) >= float(ratio * 100) - 0.01


def is_limit_up_streak(daily_data, ts_code='', minimum_days=3):
    """判断标的截至最新交易日是否至少连续涨停指定天数。"""
    if minimum_days <= 0 or len(daily_data) < minimum_days:
        return False
    return all(is_limit_up_row(row, ts_code) for _, row in daily_data.iloc[-minimum_days:].iterrows())


def is_consecutive_rise_with_increasing_volume(daily_data, days=3, reference_days=10, baseline_ratio=1.15):
    """确认连续上涨、成交量逐日不下降，且短期均量明显高于此前基准。"""
    required_columns = {"pct_chg", "vol"}
    if not required_columns.issubset(daily_data.columns) or len(daily_data) < days + reference_days:
        return False
    recent_change = pd.to_numeric(daily_data["pct_chg"].iloc[-days:], errors="coerce")
    recent_volume = pd.to_numeric(daily_data["vol"].iloc[-days:], errors="coerce")
    reference_volume = pd.to_numeric(
        daily_data["vol"].iloc[-(days + reference_days):-days], errors="coerce"
    ).mean()
    if recent_change.isna().any() or recent_volume.isna().any() or pd.isna(reference_volume) or reference_volume <= 0:
        return False
    volume_increasing = bool((recent_volume.diff().dropna() >= 0).all())
    return bool((recent_change > 0).all() and volume_increasing and recent_volume.mean() >= reference_volume * baseline_ratio)


def is_volume_surge_with_price_rise(daily_data, days=3, reference_days=7, volume_multiplier=3, consecutive_rise_days=2):
    """
    判断股票是否出现大幅放量伴随价格连续上涨的情况。

    参数:
    daily_data (pandas.DataFrame): 包含股票每日数据的DataFrame，必须包含 'vol'（成交量）和 'pct_chg'（涨跌幅）列。
    days (int): 需要判断的天数窗口，默认值为3天，用于指定最近要检查的天数范围。
    reference_days (int): 用于比较的参考天数，默认值为7天，用于计算参考成交量。
    volume_multiplier (int): 成交量放大倍数，默认值为3，即当前成交量需达到参考成交量的3倍才被认为是大幅放量。
    consecutive_rise_days (int): 连续上涨的天数要求，默认值为2天，即需要连续满足放量上涨的天数。

    返回:
    bool: 如果最近 days 天内连续出现至少 consecutive_rise_days 天放量上涨，则返回 True；否则返回 False。
    """
    # 检查数据长度是否足够进行后续判断
    # 如果 daily_data 的行数少于 days + reference_days，说明数据量不足以进行计算，直接返回 False
    if len(daily_data) < days + reference_days:
        return False

    # 计算参考成交量
    # 通过 iloc 方法选取从倒数第 (days + reference_days) 行到倒数第 days 行的数据
    # 然后使用 mean() 方法计算这些数据的平均值，得到参考成交量
    reference_volume = daily_data['vol'].iloc[-(days + reference_days):-days].mean()

    # 初始化连续上涨天数计数器
    # 用于记录连续满足放量上涨条件的天数
    consecutive_rise_count = 0

    # 遍历最近 days 天的数据
    for i in range(1, days + 1):
        # 获取当天的成交量
        # 通过 iloc 方法从 daily_data 的 'vol' 列中选取倒数第 i 天的成交量
        volume_today = daily_data['vol'].iloc[-i]
        # 获取当天的涨跌幅
        # 通过 iloc 方法从 daily_data 的 'pct_chg' 列中选取倒数第 i 天的涨跌幅
        price_change = daily_data['pct_chg'].iloc[-i]

        # 判断当天是否满足放量上涨条件
        # 如果当天成交量至少达到参考成交量的 volume_multiplier 倍，并且当天价格上涨（涨跌幅大于 0）
        if volume_today >= reference_volume * volume_multiplier and price_change > 0:
            # 连续上涨天数计数器加 1
            consecutive_rise_count += 1
            # 检查连续上涨天数是否达到要求
            # 如果连续上涨天数达到或超过 consecutive_rise_days，说明满足条件，返回 True
            if consecutive_rise_count >= consecutive_rise_days:
                return True
        else:
            # 如果当天不满足放量上涨条件，将连续上涨天数计数器重置为 0
            consecutive_rise_count = 0

    # 如果遍历完最近 days 天的数据都没有满足条件，返回 False
    return False


def is_consecutive_rise_with_amount_expansion(
    daily_data,
    days=3,
    reference_days=10,
    amount_ratio=1.2,
    minimum_return=0.01,
    maximum_return=0.15,
):
    """确认连续上涨且成交额高于此前基准，并过滤涨幅过弱或已经过热的标的。"""
    required_columns = {"close", "pct_chg", "amount"}
    if not required_columns.issubset(daily_data.columns) or len(daily_data) < days + reference_days + 1:
        return False
    recent_change = pd.to_numeric(daily_data["pct_chg"].iloc[-days:], errors="coerce")
    recent_amount = pd.to_numeric(daily_data["amount"].iloc[-days:], errors="coerce")
    reference_amount = pd.to_numeric(
        daily_data["amount"].iloc[-(days + reference_days):-days], errors="coerce"
    ).mean()
    close = pd.to_numeric(daily_data["close"], errors="coerce")
    start_close, current_close = close.iloc[-days - 1], close.iloc[-1]
    if recent_change.isna().any() or recent_amount.isna().any() or pd.isna(reference_amount) or reference_amount <= 0:
        return False
    if pd.isna(start_close) or pd.isna(current_close) or start_close <= 0:
        return False
    cumulative_return = current_close / start_close - 1.0
    amount_expanded = recent_amount.mean() >= reference_amount * amount_ratio and recent_amount.iloc[-1] >= reference_amount * amount_ratio
    return bool((recent_change > 0).all() and amount_expanded and minimum_return <= cumulative_return <= maximum_return)


def is_ma10_support_rebound(
    daily_data,
    touch_days=3,
    touch_lower=0.98,
    touch_upper=1.01,
    rebound_buffer=0.005,
    maximum_distance=0.05,
    slope_days=3,
):
    """确认上升中的MA10附近回踩未有效跌破，并在最新交易日重新向上反弹。"""
    if "close" not in daily_data.columns or len(daily_data) < 10 + slope_days:
        return False
    close = pd.to_numeric(daily_data["close"], errors="coerce")
    low = pd.to_numeric(daily_data["low"], errors="coerce") if "low" in daily_data.columns else close
    ma10 = close.rolling(10).mean()
    if close.isna().any() or low.isna().any() or pd.isna(ma10.iloc[-1 - slope_days]):
        return False
    if ma10.iloc[-1] <= ma10.iloc[-1 - slope_days]:
        return False
    recent_low = low.iloc[-touch_days:]
    recent_close = close.iloc[-touch_days:]
    recent_ma10 = ma10.iloc[-touch_days:]
    touched_support = ((recent_low >= recent_ma10 * touch_lower) & (recent_low <= recent_ma10 * touch_upper)).any()
    support_not_broken = (recent_close >= recent_ma10 * touch_lower).all()
    current_close, current_ma10 = close.iloc[-1], ma10.iloc[-1]
    rebound_confirmed = current_close > close.iloc[-2] and current_close > current_ma10 * (1.0 + rebound_buffer)
    return bool(touched_support and support_not_broken and rebound_confirmed and current_close <= current_ma10 * (1.0 + maximum_distance))


def is_volume_breakout_ma60(daily_data, breakout_buffer=0.005, maximum_distance=0.05, volume_ratio=1.2):
    """确认最新交易日从下方放量突破MA60，并过滤长期位于均线上方或突破过远的情况。"""
    required_columns = {"close", "vol"}
    if not required_columns.issubset(daily_data.columns) or len(daily_data) < 63:
        return False
    close = pd.to_numeric(daily_data["close"], errors="coerce")
    volume = pd.to_numeric(daily_data["vol"], errors="coerce")
    ma60 = close.rolling(60).mean()
    if close.isna().any() or volume.isna().any() or pd.isna(ma60.iloc[-2]):
        return False
    previous_close, current_close = close.iloc[-2], close.iloc[-1]
    previous_ma60, current_ma60 = ma60.iloc[-2], ma60.iloc[-1]
    crossed = previous_close <= previous_ma60 and current_close > current_ma60 * (1.0 + breakout_buffer)
    not_extended = current_close <= current_ma60 * (1.0 + maximum_distance)
    prior_five_below = int((close.iloc[-6:-1] <= ma60.iloc[-6:-1]).sum()) >= 3
    reference_volume = volume.iloc[-21:-1].mean()
    volume_expanded = reference_volume > 0 and volume.iloc[-1] >= reference_volume * volume_ratio
    return bool(crossed and not_extended and prior_five_below and current_close > previous_close and volume_expanded)


def _seeded_ema(values, period):
    """使用首个周期的简单平均值作为种子，避免短样本从首个价格直接启动EMA。"""
    numeric = pd.to_numeric(values, errors="coerce").reset_index(drop=True).astype(float)
    result = pd.Series(float("nan"), index=numeric.index, dtype=float)
    if period <= 0 or len(numeric) < period or numeric.isna().any():
        return result
    seed_index = period - 1
    result.iloc[seed_index] = numeric.iloc[:period].mean()
    alpha = 2.0 / (period + 1.0)
    for index in range(seed_index + 1, len(numeric)):
        result.iloc[index] = numeric.iloc[index] * alpha + result.iloc[index - 1] * (1.0 - alpha)
    return result


def calculate_macd(daily_data, short_window=12, long_window=26, signal_window=9):
    """按通行的12/26/9参数计算DIF、DEA和柱值，输出与输入行一一对应。"""
    close = daily_data["close"].reset_index(drop=True)
    ema_short = _seeded_ema(close, short_window)
    ema_long = _seeded_ema(close, long_window)
    dif = ema_short - ema_long
    valid_dif = dif.dropna()
    dea = pd.Series(float("nan"), index=dif.index, dtype=float)
    if not valid_dif.empty:
        seeded_dea = _seeded_ema(valid_dif.reset_index(drop=True), signal_window)
        dea.loc[valid_dif.index] = seeded_dea.to_numpy()
    return pd.DataFrame({"dif": dif, "dea": dea, "histogram": 2.0 * (dif - dea)})


def is_macd_golden_cross(daily_data, short_window=12, long_window=26, signal_window=9, recent_days=3):
    """判断最近交易日内DIF是否从不高于DEA转为高于DEA。"""
    minimum_rows = long_window + signal_window
    if "close" not in daily_data.columns or len(daily_data) < minimum_rows:
        return False
    macd = calculate_macd(daily_data, short_window, long_window, signal_window)
    spread = macd["dif"] - macd["dea"]
    golden_cross = (spread > 0) & (spread.shift(1) <= 0)
    return bool(golden_cross.tail(max(1, recent_days)).fillna(False).any())


def _local_bottom_indices(prices, radius):
    """识别唯一局部低点，平台型低点不重复生成多个候选。"""
    bottoms = []
    for index in range(radius, len(prices) - radius):
        window = prices.iloc[index - radius:index + radius + 1]
        center = prices.iloc[index]
        if pd.notna(center) and center == window.min() and int((window == center).sum()) == 1:
            bottoms.append(index)
    return bottoms


def is_double_bottom(
    daily_data,
    local_window=3,
    min_days_between=10,
    max_days_between=60,
    price_tolerance=0.05,
    min_rebound_ratio=0.05,
    volume_ratio=1.2,
    confirmation_days=3,
    max_breakout_wait=30,
    lookback_days=120,
):
    """按双局部低点、颈线反弹和最近放量突破三个阶段确认双底。"""
    required_columns = {"close", "vol"}
    if not required_columns.issubset(daily_data.columns) or len(daily_data) < min_days_between + local_window * 2 + 2:
        return False
    data = daily_data.tail(lookback_days).reset_index(drop=True).copy()
    close = pd.to_numeric(data["close"], errors="coerce")
    low = pd.to_numeric(data["low"], errors="coerce") if "low" in data.columns else close
    high = pd.to_numeric(data["high"], errors="coerce") if "high" in data.columns else close
    volume = pd.to_numeric(data["vol"], errors="coerce")
    if close.isna().any() or low.isna().any() or high.isna().any() or volume.isna().any():
        return False

    bottoms = _local_bottom_indices(low, local_window)
    recent_confirmation_start = max(1, len(data) - max(1, confirmation_days))
    for first_position, first_index in enumerate(bottoms[:-1]):
        for second_index in bottoms[first_position + 1:]:
            days_between = second_index - first_index
            if days_between > max_days_between:
                break
            if days_between < min_days_between:
                continue
            first_price, second_price = low.iloc[first_index], low.iloc[second_index]
            if first_price <= 0 or abs(second_price - first_price) / first_price > price_tolerance:
                continue
            neckline = high.iloc[first_index + 1:second_index].max()
            average_bottom = (first_price + second_price) / 2.0
            if pd.isna(neckline) or neckline < average_bottom * (1.0 + min_rebound_ratio):
                continue
            first_bottom_volume = volume.iloc[max(0, first_index - 1):first_index + 2].mean()
            second_bottom_volume = volume.iloc[max(0, second_index - 1):second_index + 2].mean()
            if second_bottom_volume > first_bottom_volume:
                continue
            confirmation_start = max(second_index + 1, recent_confirmation_start)
            for breakout_index in range(confirmation_start, len(data)):
                if breakout_index - second_index > max_breakout_wait or close.iloc[breakout_index] <= neckline:
                    continue
                reference_volume = volume.iloc[max(0, breakout_index - 20):breakout_index].mean()
                if reference_volume > 0 and volume.iloc[breakout_index] >= reference_volume * volume_ratio:
                    return True
    return False


def is_breakout_after_consolidation(
    daily_data,
    consolidation_days=30,
    recent_days=5,
    price_threshold=0.05,
    volume_increase_threshold=1.2,
    breakout_buffer=0.005,
):
    """确认最近窗口内放量上穿横盘上沿，且最新收盘仍处于上沿之上。"""
    required_columns = {"close", "vol"}
    if not required_columns.issubset(daily_data.columns) or len(daily_data) < consolidation_days + recent_days:
        return False

    consolidation_data = daily_data.iloc[-(consolidation_days + recent_days):-recent_days].copy()
    recent_data = daily_data.iloc[-recent_days:].copy()
    consolidation_close = pd.to_numeric(consolidation_data["close"], errors="coerce")
    recent_close = pd.to_numeric(recent_data["close"], errors="coerce")
    recent_volume = pd.to_numeric(recent_data["vol"], errors="coerce")
    if consolidation_close.isna().any() or recent_close.isna().any() or recent_volume.isna().any():
        return False

    # 收盘价用于判断横盘稳定性；若有最高价字段，则用区间最高价作为更严格的突破上沿。
    max_price = consolidation_close.max()
    min_price = consolidation_close.min()
    if min_price <= 0:
        return False
    price_range = (max_price - min_price) / min_price
    if price_range > price_threshold:
        return False
    if "high" in consolidation_data.columns:
        resistance = pd.to_numeric(consolidation_data["high"], errors="coerce").max()
    else:
        resistance = max_price
    average_volume = pd.to_numeric(consolidation_data["vol"], errors="coerce").mean()
    if pd.isna(resistance) or pd.isna(average_volume) or average_volume <= 0:
        return False

    breakout_level = resistance * (1.0 + breakout_buffer)
    previous_close = consolidation_close.iloc[-1]
    breakout_confirmed = False
    for current_close, current_volume in zip(recent_close, recent_volume):
        crossed_resistance = previous_close <= breakout_level < current_close
        if crossed_resistance and current_volume >= average_volume * volume_increase_threshold:
            breakout_confirmed = True
        previous_close = current_close
    # 突破后跌回横盘上沿下方属于失败突破，不继续保留信号。
    return breakout_confirmed and recent_close.iloc[-1] > resistance


def calculate_upward_trend_score(daily_data):
    """计算上涨初期5项评分；位置约束不计分，但决定最终是否命中。"""
    empty_result = {"matched": False, "score": 0, "total_score": 5, "reasons": [], "metrics": {}}
    required_columns = {"close", "vol"}
    if not required_columns.issubset(daily_data.columns) or len(daily_data) < 61:
        return empty_result
    close = pd.to_numeric(daily_data["close"], errors="coerce")
    volume = pd.to_numeric(daily_data["vol"], errors="coerce")
    if close.isna().any() or volume.isna().any():
        return empty_result

    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    macd = calculate_macd(pd.DataFrame({"close": close}))
    histogram = macd["histogram"]
    current_close, current_ma20 = close.iloc[-1], ma20.iloc[-1]
    volume_reference = volume.iloc[-20:].mean()
    volume_ratio = volume.iloc[-5:].mean() / volume_reference if volume_reference > 0 else 0.0
    return_20d = current_close / close.iloc[-21] - 1.0 if close.iloc[-21] > 0 else float("nan")
    ma20_distance = current_close / current_ma20 - 1.0 if current_ma20 > 0 else float("nan")

    conditions = (
        (ma5.iloc[-1] > ma10.iloc[-1] and ma5.iloc[-1] > ma5.iloc[-4], "MA5位于MA10上方且保持上行"),
        (ma10.iloc[-1] > ma10.iloc[-6], "MA10趋势向上"),
        (
            macd["dif"].iloc[-1] > macd["dea"].iloc[-1] and histogram.iloc[-1] > histogram.iloc[-2] > histogram.iloc[-3],
            "MACD位于金叉状态且动能连续增强",
        ),
        (volume_ratio >= 1.10, "近5日成交量高于20日均量"),
        (pd.notna(return_20d) and 0.03 <= return_20d <= 0.15, "20日涨幅处于3%至15%"),
    )
    score = sum(bool(matched) for matched, _ in conditions)
    position_confirmed = pd.notna(ma20_distance) and 0 < ma20_distance <= 0.08
    reasons = [reason for matched, reason in conditions if matched]
    if position_confirmed:
        reasons.insert(0, "收盘价位于MA20上方且偏离不超过8%")
    metrics = {
        "close": round(float(current_close), 4),
        "ma20": round(float(current_ma20), 4),
        "ma20_distance_pct": round(float(ma20_distance * 100), 2),
        "volume_ratio_5_20": round(float(volume_ratio), 2),
        "return_20d_pct": round(float(return_20d * 100), 2),
    }
    return {"matched": bool(position_confirmed and score >= 4), "score": score, "total_score": 5, "reasons": reasons, "metrics": metrics}


def is_funds_inflow_by_volume_turnover(daily_data, n=7, m=30, ratio=1.2, pct_positive=0.66):
    """
    结合成交量和换手率判断资金流入（最近n天大部分为正涨幅）
    :param daily_data: 股票每日数据，需包含'vol'、'turnover_rate'、'pct_chg'
    :param n: 最近n天
    :param m: 前m天
    :param ratio: 放大倍数阈值
    :param pct_positive: 最近n天中正涨幅天数占比（如0.66表示2/3为正）
    :return: True/False
    """
    if len(daily_data) < n + m:
        return False
        
    # 检查数据中是否包含换手率字段
    turnover_field = 'turnover_rate' if 'turnover_rate' in daily_data.columns else 'tor'
    if turnover_field not in daily_data.columns:
        logger.debug("日线数据缺少换手率字段: %s", list(daily_data.columns))
        return False

    vol_recent = daily_data['vol'].iloc[-n:].mean()
    vol_ref = daily_data['vol'].iloc[-(n + m):-n].mean()
    turnover_recent = daily_data[turnover_field].iloc[-n:].mean()
    turnover_ref = daily_data[turnover_field].iloc[-(n + m):-n].mean()

    # 判断成交量和换手率均放大
    if vol_recent > vol_ref * ratio and turnover_recent > turnover_ref * ratio:
        # 统计最近n天正涨幅天数
        positive_days = (daily_data['pct_chg'].iloc[-n:] > 0).sum()
        if positive_days >= ceil(n * pct_positive):
            return True
    return False


if __name__ == "__main__":
    logger.info("检查结果: %s", daily_check("000001.SZ", "平安银行"))
