"""将包含股票代码和请求参数的原始异常归并为稳定、可读的错误原因。"""

import re


SYMBOL_PATTERN = re.compile(r"\b\d{6}\.(?:SH|SZ|BJ)\b", re.IGNORECASE)
URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
DATE_PARAMETER_PATTERN = re.compile(r"\b(?:beg|end)=\d{8}\b", re.IGNORECASE)


def classify_scan_error(error_type, error_message):
    """返回稳定的错误类型和摘要，动态股票代码不参与错误类型统计。"""
    raw_message = str(error_message or "").strip()
    lowered = raw_message.lower()
    baostock_session_failed = "用户未登录" in raw_message or "not logged" in lowered or "not login" in lowered
    proxy_failed = "proxyerror" in lowered or "unable to connect to proxy" in lowered
    timed_out = "timeout" in lowered or "timed out" in lowered or "超时" in raw_message
    ssl_failed = "sslerror" in lowered or "certificate" in lowered or "证书" in raw_message

    if baostock_session_failed and proxy_failed:
        return "多行情源连接故障", "BaoStock 会话失效；东方财富无法连接代理服务器"
    if baostock_session_failed:
        return "BaoStock会话失效", "BaoStock 当前会话未登录或已经失效"
    if proxy_failed:
        return "代理连接失败", "行情请求无法连接代理服务器"
    if timed_out:
        return "行情接口超时", "行情源连接或读取响应超时"
    if ssl_failed:
        return "HTTPS连接失败", "行情源 HTTPS 证书或握手失败"
    if "返回空数据" in raw_message or "未返回" in raw_message:
        return "行情数据为空", "行情源未返回请求区间的数据"

    # 未命中已知规则时仍移除高频动态字段，避免不同股票被错误地计为不同类型。
    normalized = SYMBOL_PATTERN.sub("<证券代码>", raw_message)
    normalized = URL_PATTERN.sub("<请求地址>", normalized)
    normalized = DATE_PARAMETER_PATTERN.sub("<日期参数>", normalized)
    return str(error_type or "UnknownError"), normalized or "未提供错误信息"
