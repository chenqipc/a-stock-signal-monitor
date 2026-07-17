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
    if "ST" in stock_name:
        return [StockStatus.NO_MATCH]
    if ts_code.startswith(("4", "8", "92", "688")):
        return [StockStatus.NO_MATCH]
    if daily_data is None or len(daily_data) < 7:
        return [StockStatus.NO_MATCH]
    sort_column = "trade_date" if "trade_date" in daily_data.columns else "trade_time"
    prepared_data = daily_data.sort_values(by=sort_column, ascending=True).reset_index(drop=True).copy()
    checks = (
        (StockStatus.THREE_LIMIT_UP, lambda: is_limit_up_3days(prepared_data, ts_code)),
        (StockStatus.THREE_LIMIT_UP_ONLY, lambda: is_limit_up_only_3days(prepared_data, ts_code)),
        (StockStatus.RISING_VOLUME_INCREASE, lambda: is_rising_with_volume_increase(prepared_data, days=3)),
        (StockStatus.VOLUME_SURGE_WITH_PRICE_RISE, lambda: is_volume_surge_with_price_rise(prepared_data, days=3)),
        (StockStatus.CAPITAL_INFLOW, lambda: is_capital_inflow(prepared_data)),
        (StockStatus.SUPPORT_LEVEL_REBOUND, lambda: is_stock_stabilizing(prepared_data, days=3)),
        (StockStatus.SUPPORT_LEVEL_REBOUND_60, lambda: is_stock_stabilizing_over60(prepared_data)),
        (StockStatus.MACD_GOLDEN_CROSS, lambda: is_macd_golden_cross(prepared_data)),
        (StockStatus.MACD_GOLDEN_CROSS_OVER_7, lambda: is_macd_golden_cross_7(prepared_data)),
        (StockStatus.DOUBLE_BOTTOM, lambda: is_double_bottom(prepared_data)),
        (StockStatus.DOUBLE_BOTTOM_NEW, lambda: is_double_bottom_new(prepared_data)),
        (StockStatus.BREAKOUT_AFTER_CONSOLIDATION, lambda: is_breakout_after_consolidation(prepared_data)),
        (StockStatus.IS_UPWARD_TREND, lambda: is_upward_trend(prepared_data)),
        (StockStatus.FUNDS_INFLOW_BY_VOLUME_TURNOVER, lambda: is_funds_inflow_by_volume_turnover(prepared_data)),
    )
    results = [status for status, check in checks if check()]
    return results or [StockStatus.NO_MATCH]


def daily_strategy_window(today=None):
    """统一日线策略窗口，供扫描调度和策略计算共用。"""
    today = today or datetime.today()
    history_start = today - timedelta(days=200)
    return history_start.strftime('%Y%m%d'), today.strftime('%Y%m%d')

def get_recent_days_data(daily_data, days=30, sort=False):
    """
    从原始数据中获取最近指定天数的数据。

    参数:
    daily_data (pd.DataFrame): 原始数据，假设包含日期索引。
    days (int): 要获取的最近天数，默认为30天。
    sort (bool): 是否按日期索引排序，默认为False。如果数据已经排序，可以保持False以提高效率。

    返回:
    pd.DataFrame: 最近指定天数的数据。
    """
    # 复制数据以避免修改原始数据
    daily_data_copy = daily_data.copy()

    # 获取最近指定天数的数据
    recent_days_data = daily_data_copy.tail(days)

    # 如果需要，按日期索引排序
    if sort:
        recent_days_data = recent_days_data.sort_index(ascending=True)

    return recent_days_data


# 示例用法
# 假设 daily_data 是一个已经按日期索引排序的Pandas DataFrame
# recent_30_days_data = get_recent_days_data(daily_data)

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


def is_limit_up_3days(daily_data, ts_code=''):
    """判断是否连续3天涨停"""
    # 确保有足够的天数进行判断
    if len(daily_data) < 3:
        return False

    # recent_30_days_data = get_recent_days_data(daily_data)

    return all(is_limit_up_row(row, ts_code) for _, row in daily_data.iloc[-3:].iterrows())


def is_limit_up_only_3days(daily_data, ts_code=''):
    """
    判断最近三天（不包括更早）是否连续3天涨停。
    如果涨停超过3天，则过滤掉。
    :param daily_data: 股票的每日数据，按日期升序排列
    :return: 如果满足条件返回True，否则返回False
    """
    # 需要第4天判断这是否恰好是连续3个涨停。
    if len(daily_data) < 4:
        return False
    recent_rows = daily_data.iloc[-3:]
    previous_row = daily_data.iloc[-4]
    return all(is_limit_up_row(row, ts_code) for _, row in recent_rows.iterrows()) and not is_limit_up_row(previous_row, ts_code)


def is_rising_with_volume_increase(daily_data, days=3):
    """
    判断是否连续n天上涨并且成交量逐步放大或与前一天相差不大（相差不超过10%）
    """
    # 确保有足够的天数进行判断
    if len(daily_data) < days:
        return False
    is_consecutive_rise = all(daily_data['pct_chg'].iloc[-days:] > 0)
    if is_consecutive_rise:
        vol_increase = all(daily_data['vol'].iloc[-i] >= daily_data['vol'].iloc[-i - 1] * 0.95
                           for i in range(1, days))
        return vol_increase
    return False


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


def is_capital_inflow(daily_data, min_threshold=0.90, days=3, reference_days=7, volume_increase_threshold=1.2):
    """
    判断是否存在明显的资金流入（主力资金吸筹）情况。

    条件：
    1. 过去days天的成交额不明显减少（相对于前一天减少不超过 min_threshold），并且股价涨幅为正。
    2. 最近days天的成交额显著大于前reference_days天的平均成交额。

    :param daily_data: 股票的每日数据
    :param min_threshold: 成交额最低回落阈值，默认是0.9倍，即成交额最多减少10%
    :param days: 连续天数，默认是3天
    :param reference_days: 用于比较的参考天数，默认7天
    :param volume_increase_threshold: 最近days天成交额相对于前reference_days天的放大倍数，默认1.2倍
    :return: 如果满足条件返回True，否则返回False
    """
    # 确保有足够的天数进行判断
    if len(daily_data) < days + reference_days:
        return False

    # 计算前reference_days天的平均成交额
    reference_avg_amount = daily_data['amount'].iloc[-(days + reference_days):-days].mean()

    # 计算最近days天的平均成交额
    recent_avg_amount = daily_data['amount'].iloc[-days:].mean()

    # 判断最近days天的成交额是否比前reference_days的平均成交额大很多
    if recent_avg_amount < reference_avg_amount * volume_increase_threshold:
        return False  # 最近days天的成交额没有显著增加，返回False

    recent_data = daily_data.iloc[-days:]
    if not all(recent_data['pct_chg'] > 0):
        return False
    return all(
        recent_data['amount'].iloc[index] >= recent_data['amount'].iloc[index - 1] * min_threshold
        for index in range(1, len(recent_data))
    )


def is_stock_stabilizing(daily_data, days=5):
    """
    判断股票是否出现企稳迹象，但涨幅不大。5日线上穿10日线。

    条件：
    1. 价格在低点附近形成支撑并开始反弹，但涨幅不大（2% - 5%）。
    2. 成交量在反弹期间出现放大。
    3. 5日均线逐渐上穿10日均线。

    :param daily_data: 股票的每日数据，包含'close'（收盘价）和'vol'（成交量）等字段
    :param days: 判断的天数窗口，默认是最近5天
    :return: True如果股票出现企稳迹象，False否则
    """
    # 确保有足够的天数进行判断，至少需要10天的价格数据来计算10日均线
    if len(daily_data) < 10:
        return False

    # 计算5日均线和10日均线
    daily_data['MA5'] = daily_data['close'].rolling(window=5).mean()
    daily_data['MA10'] = daily_data['close'].rolling(window=10).mean()

    # 最近days天的5日均线和10日均线
    # 最近窗口内必须实际发生“前一日不高于、当前日高于”的穿越。
    crossover = (daily_data['MA5'] > daily_data['MA10']) & (daily_data['MA5'].shift(1) <= daily_data['MA10'].shift(1))
    ma_crossover = bool(crossover.iloc[-days:].any())

    # 1. 判断是否形成低点支撑（最近days天中，价格相对较低并开始反弹）
    # 条件：当前的收盘价相对前几天的价格略有上涨，表明反弹初期
    recent_prices = daily_data['close'].iloc[-days:]
    lowest_price = recent_prices.min()
    last_close_price = recent_prices.iloc[-1]

    # 价格止跌反弹的条件：涨幅在2%到7%之间
    price_rebound = (lowest_price * 1.02 <= last_close_price <= lowest_price * 1.07)

    # 2. 判断成交量是否放大（最近几天成交量逐步增加）
    recent_volumes = daily_data['vol'].iloc[-days:]
    volume_increase = all(recent_volumes.iloc[-i] >= recent_volumes.iloc[-(i + 1)] * 0.90 for i in range(1, days))

    # 综合判断条件：价格略有反弹，成交量放大，且5日均线上穿10日均线
    if price_rebound and volume_increase and ma_crossover:
        return True

    return False


def is_stock_stabilizing_over60(daily_data, days=5, tolerance=0.05):
    """
    判断股票是否出现企稳迹象，并且反弹刚突破60日均线。

    条件：
    1. 价格在低点附近形成支撑并开始反弹，且刚刚突破60日均线（不超过5%）。
    2. 成交量在反弹期间出现放大。

    :param daily_data: 股票的每日数据，包含'close'（收盘价）和'vol'（成交量）等字段
    :param days: 判断的天数窗口，默认是最近5天
    :param tolerance: 股价突破60日均线的最大容差，默认不超过60日均线的5%。
    :return: True如果股票出现企稳迹象，False否则
    """
    # 确保有足够的天数进行判断
    if len(daily_data) < 61:  # 上一日和当前日都需要有效的60日均线
        return False

    # 计算60日均线
    daily_data['ma60'] = daily_data['close'].rolling(window=60).mean()

    # 获取最近days天的价格和成交量
    recent_prices = daily_data['close'].iloc[-days:]
    recent_ma60 = daily_data['ma60'].iloc[-days:]
    last_close_price = recent_prices.iloc[-1]
    last_ma60 = recent_ma60.iloc[-1]

    # 必须从均线下方真实穿越到上方，而不是仅判断当前仍在均线上方。
    previous_close = daily_data['close'].iloc[-2]
    previous_ma60 = daily_data['ma60'].iloc[-2]
    if pd.isna(previous_ma60) or pd.isna(last_ma60):
        return False
    if previous_close > previous_ma60 or last_close_price <= last_ma60:
        return False

    if last_close_price > last_ma60 * (1 + tolerance):
        return False  # 如果股价已经大幅高于60日均线，则过滤掉

    # 2. 判断成交量是否放大（最近几天成交量逐步增加）
    recent_volumes = daily_data['vol'].iloc[-days:]
    volume_increase = all(recent_volumes.iloc[i] >= recent_volumes.iloc[i - 1] * 0.90 for i in range(1, days))

    # 如果价格刚突破60日均线并且成交量放大，则认为股票出现企稳迹象
    if volume_increase:
        return True

    return False


def is_macd_golden_cross(daily_data, short_window=12, long_window=26, signal_window=9, days=3):
    """
    判断最近几天是否出现MACD金叉。
    """
    if len(daily_data) < long_window:
        return False

    # 计算快线EMA
    ema_short = daily_data['close'].ewm(span=short_window, adjust=False).mean()
    # 计算慢线EMA
    ema_long = daily_data['close'].ewm(span=long_window, adjust=False).mean()
    # 计算DIF
    dif = ema_short - ema_long
    # 计算DEA
    dea = dif.ewm(span=signal_window, adjust=False).mean()
    # 计算MACD
    macd = 2 * (dif - dea)

    # 判断金叉
    for i in range(1, days + 1):
        if macd.iloc[-i - 1] < 0 < macd.iloc[-i]:
            return True

    return False


def is_macd_golden_cross_7(daily_data, short_window=12, long_window=26, signal_window=9, max_price_change=0.05,
                                recent_days=7):
    """
    判断最近几天（如7天内）是否出现MACD金叉并且涨幅不大。
    :param daily_data: 股票的每日数据，必须包含'close'列。
    :param short_window: MACD的短期均线窗口，默认12天。
    :param long_window: MACD的长期均线窗口，默认26天。
    :param signal_window: 信号线的窗口，默认9天。
    :param max_price_change: 限制价格涨幅的阈值，默认是5%。
    :param recent_days: 定义在最近多少天内寻找金叉，默认7天。
    :return: 如果最近几天出现金叉并且涨幅不大，返回True，否则返回False。
    """
    # 确保有足够的天数进行判断
    if len(daily_data) < long_window + recent_days:
        return False

    # 计算短期均线和长期均线
    ema_short = daily_data['close'].ewm(span=short_window, adjust=False).mean()
    ema_long = daily_data['close'].ewm(span=long_window, adjust=False).mean()

    # 计算MACD线
    macd_line = ema_short - ema_long

    # 计算信号线
    signal_line = macd_line.ewm(span=signal_window, adjust=False).mean()

    # 检查MACD线和信号线的差值（即柱状图）
    macd_histogram = macd_line - signal_line

    # 检查MACD金叉并且价格涨幅不大
    if len(macd_histogram) < recent_days + 1:
        return False

    for i in range(-recent_days, 0):
        if macd_histogram.iloc[i - 1] < 0 < macd_histogram.iloc[i]:
            # MACD金叉发生时，检查涨幅是否在设定的范围内
            price_change = (daily_data['close'].iloc[i] - daily_data['close'].iloc[i - 1]) / daily_data['close'].iloc[
                i - 1]
            if price_change <= max_price_change:
                return True

    return False


def is_double_bottom(daily_data, min_days_between=5, max_days_between=30):
    """
    检测双底结构：
    1. 第一个低点
    2. 反弹到颈线位置
    3. 第二个低点（成交量较第一底小）
    4. 突破颈线确认
    
    参数：
    - daily_data: DataFrame，股票日线数据
    - min_days_between: int，两底之间的最小天数
    - max_days_between: int，两底之间的最大天数
    """
    if len(daily_data) < 30:
        return False

    recent_data = get_recent_days_data(daily_data, days=90)
    recent_data = recent_data.reset_index(drop=True)

    # 找到第一个低点
    min1_idx = recent_data['close'].idxmin()
    min1_price = recent_data['close'].iloc[min1_idx]
    min1_volume = recent_data['vol'].iloc[min1_idx]

    # 找到第一个低点后的反弹高点（颈线位置）
    after_min1_data = recent_data.iloc[min1_idx + 1:]
    if after_min1_data.empty:
        return False

    max_between_idx = after_min1_data['close'].idxmax()
    neckline_price = recent_data['close'].iloc[max_between_idx]

    # 找到第二个低点
    after_max_data = recent_data.iloc[max_between_idx + 1:]
    if after_max_data.empty:
        return False

    min2_idx = after_max_data['close'].idxmin()
    min2_price = recent_data['close'].iloc[min2_idx]
    min2_volume = recent_data['vol'].iloc[min2_idx]

    # 检查条件
    days_between = min2_idx - min1_idx
    price_diff_percent = abs(min2_price - min1_price) / min1_price
    
    conditions = [
        min_days_between <= days_between <= max_days_between,  # 时间间隔合适
        price_diff_percent <= 0.05,  # 两个底部价格相差不超过5%
        min2_volume < min1_volume,  # 第二底成交量小于第一底
        min2_idx > max_between_idx,  # 确保时间顺序正确
        neckline_price > min1_price * 1.05  # 颈线至少比底部高5%
    ]

    if all(conditions):
        # 检查突破颈线确认
        after_min2_data = recent_data.iloc[min2_idx + 1:]
        if not after_min2_data.empty:
            # 检查是否突破颈线位置的90%
            breakout_price = neckline_price * 0.9
            if any(after_min2_data['close'] >= breakout_price):
                # 确认突破时的成交量是否放大
                breakout_idx = after_min2_data[after_min2_data['close'] >= breakout_price].index[0]
                breakout_volume = recent_data['vol'].iloc[breakout_idx]
                avg_volume = recent_data['vol'].iloc[min2_idx-5:min2_idx].mean()
                
                if breakout_volume > avg_volume * 1.2:  # 突破时成交量至少放大20%
                    return True

    return False


def is_breakout_after_consolidation(daily_data, consolidation_days=30, recent_days=5, price_threshold=0.05,
                                    volume_increase_threshold=1.2):
    """
    判断股票是否在横盘期后出现放量上涨。

    :param daily_data: 股票的每日数据（DataFrame）
    :param consolidation_days: 横盘期的天数，默认是30天
    :param recent_days: 检查最近几天内的表现，默认是5天
    :param price_threshold: 检查横盘期股价波动的阈值，默认是5%
    :param volume_increase_threshold: 成交量放大的阈值，默认是1.2倍
    :return: 如果满足横盘期后的放量上涨条件，返回True，否则返回False
    """
    if len(daily_data) < consolidation_days + recent_days:
        return False

    # 获取横盘期和最近几天的数据
    consolidation_data = daily_data.iloc[-(consolidation_days + recent_days):-recent_days]
    recent_data = daily_data.iloc[-recent_days:]

    # 1. 检查横盘期内股价波动是否很小（最高价和最低价相差小于 price_threshold）
    max_price = consolidation_data['close'].max()
    min_price = consolidation_data['close'].min()
    price_range = (max_price - min_price) / min_price

    if price_range > price_threshold:
        return False  # 如果股价波动超过阈值，认为没有横盘

    # 2. 检查最近几天股价是否出现轻微上涨
    recent_price_change = (recent_data['close'].iloc[-1] - recent_data['close'].iloc[0]) / recent_data['close'].iloc[0]
    if recent_price_change <= 0:
        return False  # 如果没有出现上涨

    # 3. 检查最近几天成交量是否有明显放大
    avg_volume_consolidation = consolidation_data['vol'].mean()
    avg_volume_recent = recent_data['vol'].mean()

    if avg_volume_recent < avg_volume_consolidation * volume_increase_threshold:
        return False  # 如果成交量放大不足

    # 如果满足横盘期后的放量上涨条件，返回True
    return True


def is_upward_trend(daily_data):
    """
    检测股票是否处于上涨初期：
    1. 突破阻力位
    2. 低位放量上涨
    3. MACD金叉
    """
    # 确保有足够数据
    if len(daily_data) < 61:
        return False

    # 1. 检查是否刚刚突破阻力位（如均线）
    short_ma = daily_data['close'].rolling(window=5).mean()
    long_ma = daily_data['close'].rolling(window=60).mean()

    # 最近一天5日均线突破60日均线
    if short_ma.iloc[-1] > long_ma.iloc[-1] and short_ma.iloc[-2] <= long_ma.iloc[-2]:
        # 2. 检查低位放量上涨
        if is_volume_surge_with_price_rise(daily_data, days=4):
            # 3. MACD金叉
            if is_macd_golden_cross(daily_data):
                return True

    return False


def is_double_bottom_new(daily_data, window=10, price_diff=0.05, min_days=5, max_days=30, volume_ratio=1.2):
    """
    检测双底结构：
    1. 两个低点价格接近，间隔合适
    2. 第二底成交量小于第一底
    3. 反弹突破颈线且放量
    """
    if len(daily_data) < window * 3:
        return False

    closes = daily_data['close'].values
    vols = daily_data['vol'].values
    bottoms = []
    # 1. 用滑动窗口找局部低点
    for i in range(window, len(closes) - window):
        window_slice = closes[i - window:i + window + 1]
        if closes[i] == window_slice.min():
            bottoms.append(i)
    # 2. 检查所有可能的双底组合
    for i in range(len(bottoms) - 1):
        idx1, idx2 = bottoms[i], bottoms[i + 1]
        if not (min_days <= idx2 - idx1 <= max_days):
            continue
        price1, price2 = closes[idx1], closes[idx2]
        if abs(price1 - price2) / price1 > price_diff:
            continue
        # 颈线
        neckline = closes[idx1:idx2].max()
        vol1, vol2 = vols[idx1], vols[idx2]
        if vol2 >= vol1:
            continue
        # 3. 突破颈线且放量
        after_idx2 = daily_data.iloc[idx2 + 1:]
        breakout = after_idx2[after_idx2['close'] > neckline]
        if not breakout.empty:
            breakout_idx = breakout.index[0]
            breakout_vol = daily_data.loc[breakout_idx, 'vol']
            avg_vol = daily_data['vol'].iloc[max(0, idx2 - 5):idx2].mean()
            if breakout_vol > avg_vol * volume_ratio:
                return True
    return False


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
