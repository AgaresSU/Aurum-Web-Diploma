from urllib.parse import urlparse


def same_origin_or_no_origin(request):
    origin = request.headers.get("Origin", "").strip()
    if origin:
        parsed = urlparse(origin)
        if parsed.netloc != request.get_host():
            return False
        return not (request.is_secure() and parsed.scheme != "https")

    referer = request.headers.get("Referer", "").strip()
    if referer:
        parsed = urlparse(referer)
        if parsed.netloc and parsed.netloc != request.get_host():
            return False
        if request.is_secure() and parsed.scheme and parsed.scheme != "https":
            return False

    return True


def cross_origin_error():
    return {"ok": False, "error": "Запрос отклонен: источник формы не совпадает с сайтом."}
