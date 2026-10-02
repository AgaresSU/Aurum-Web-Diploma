import hashlib
import hmac
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from django.conf import settings
from django.db import DatabaseError, OperationalError, ProgrammingError

MONEY_QUANT = Decimal("0.01")
OP_STATE_URL = "https://auth.robokassa.ru/Merchant/WebService/Service.asmx/OpStateExt"
MAX_STATE_RESPONSE_BYTES = 128 * 1024


def _amount(value):
    return str(_money(value))


def _money(value):
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _json_number(value):
    value = _money(value)
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _clean_receipt_name(value, fallback="Услуга AurumWeb"):
    text = " ".join(str(value or "").split()).strip()
    return (text or fallback)[:128]


@dataclass(frozen=True)
class RobokassaConfig:
    merchant_login: str
    password1: str
    password2: str
    test_mode: bool = True
    hash_algorithm: str = "sha256"
    payment_url: str = "https://auth.robokassa.ru/Merchant/Index.aspx"
    receipt_enabled: bool = True
    receipt_sno: str = ""
    receipt_tax: str = "none"
    receipt_payment_method: str = "full_payment"
    receipt_payment_object: str = "service"

    @classmethod
    def from_settings(cls):
        data = settings.ROBOKASSA
        values = {
            "merchant_login": data["MERCHANT_LOGIN"],
            "password1": data["PASSWORD1"],
            "password2": data["PASSWORD2"],
            "test_mode": data["TEST_MODE"],
            "hash_algorithm": data["HASH_ALGORITHM"],
            "payment_url": data["PAYMENT_URL"],
            "receipt_enabled": data.get("RECEIPT_ENABLED", True),
            "receipt_sno": data.get("RECEIPT_SNO", ""),
            "receipt_tax": data.get("RECEIPT_TAX", "none"),
            "receipt_payment_method": data.get("RECEIPT_PAYMENT_METHOD", "full_payment"),
            "receipt_payment_object": data.get("RECEIPT_PAYMENT_OBJECT", "service"),
        }
        database_values = cls._database_values()
        if database_values:
            values.update(database_values)
        return cls(**values)

    @classmethod
    def _database_values(cls):
        try:
            from apps.integrations.models import RobokassaSettings

            config = RobokassaSettings.objects.order_by("pk").first()
        except (DatabaseError, OperationalError, ProgrammingError):
            return {}
        if not config or not config.is_enabled:
            return {}
        return {
            "merchant_login": config.merchant_login,
            "password1": config.get_password1(),
            "password2": config.get_password2(),
            "test_mode": config.test_mode,
            "hash_algorithm": config.hash_algorithm or "sha256",
            "payment_url": config.payment_url or "https://auth.robokassa.ru/Merchant/Index.aspx",
            "receipt_enabled": config.receipt_enabled,
            "receipt_sno": config.receipt_sno,
            "receipt_tax": config.receipt_tax or "none",
            "receipt_payment_method": config.receipt_payment_method or "full_payment",
            "receipt_payment_object": config.receipt_payment_object or "service",
        }


@dataclass(frozen=True)
class RobokassaOperationState:
    result_code: int
    result_description: str = ""
    state_code: int | None = None
    state_date: str = ""
    request_date: str = ""
    out_sum: Decimal | None = None
    operation_key: str = ""
    payment_method: str = ""

    @property
    def successful(self):
        return self.result_code == 0


class RobokassaClient:
    def __init__(self, config=None):
        self.config = config or RobokassaConfig.from_settings()

    @property
    def configured(self):
        return bool(self.config.merchant_login and self.config.password1 and self.config.password2)

    def _digest(self, raw):
        algorithm = self.config.hash_algorithm.lower()
        try:
            factory = getattr(hashlib, algorithm)
        except AttributeError as exc:
            raise ValueError(f"Unsupported Robokassa hash algorithm: {algorithm}") from exc
        return factory(raw.encode("utf-8")).hexdigest()

    def payment_signature(self, out_sum, inv_id, receipt=None):
        parts = [self.config.merchant_login, _amount(out_sum), str(inv_id)]
        if receipt:
            parts.append(receipt)
        parts.append(self.config.password1)
        raw = ":".join(parts)
        return self._digest(raw)

    def result_signature(self, out_sum, inv_id):
        raw = f"{out_sum}:{inv_id}:{self.config.password2}"
        return self._digest(raw)

    def success_signature(self, out_sum, inv_id):
        raw = f"{out_sum}:{inv_id}:{self.config.password1}"
        return self._digest(raw)

    def operation_state_signature(self, inv_id):
        raw = f"{self.config.merchant_login}:{inv_id}:{self.config.password2}"
        return self._digest(raw)

    def verify_result(self, out_sum, inv_id, signature):
        expected = self.result_signature(out_sum, inv_id)
        return hmac.compare_digest(expected.lower(), (signature or "").lower())

    def verify_success(self, out_sum, inv_id, signature):
        expected = self.success_signature(out_sum, inv_id)
        return hmac.compare_digest(expected.lower(), (signature or "").lower())

    def amount_matches(self, expected, received):
        return hmac.compare_digest(_amount(expected), _amount(received))

    def receipt_payload(self, invoice):
        if not self.config.receipt_enabled:
            return None
        payload = {"items": self._receipt_items(invoice)}
        if self.config.receipt_sno:
            payload["sno"] = self.config.receipt_sno
        return payload

    def receipt_json(self, invoice):
        payload = self.receipt_payload(invoice)
        if not payload:
            return None
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def _receipt_items(self, invoice):
        configured_items = self._configured_invoice_items(invoice)
        if configured_items:
            return configured_items
        return [self._receipt_item(invoice.title, "", 1, invoice.amount)]

    def _configured_invoice_items(self, invoice):
        items = []
        total = Decimal("0.00")
        for item in invoice.items.all():
            line_total = _money(item.quantity * item.unit_price)
            if line_total <= 0:
                continue
            total += line_total
            items.append(self._receipt_item(item.name, item.note, item.quantity, line_total))
        if items and _money(total) == _money(invoice.amount):
            return items
        return []

    def _receipt_item(self, name, note, quantity, line_total):
        full_name = name
        if note:
            full_name = f"{name} - {note}"
        return {
            "name": _clean_receipt_name(full_name),
            "quantity": _json_number(quantity),
            "sum": _json_number(line_total),
            "payment_method": self.config.receipt_payment_method,
            "payment_object": self.config.receipt_payment_object,
            "tax": self.config.receipt_tax,
        }

    def build_payment_url(self, payment, description):
        receipt = self.receipt_json(payment.invoice)
        params = {
            "MerchantLogin": self.config.merchant_login,
            "OutSum": _amount(payment.amount),
            "InvId": payment.id,
            "Description": description[:100],
            "SignatureValue": self.payment_signature(payment.amount, payment.id, receipt),
            "Culture": "ru",
        }
        email = (payment.invoice.client_email or "").strip()
        if email:
            params["Email"] = email[:100]
        if receipt:
            params["Receipt"] = receipt
        if self.config.test_mode:
            params["IsTest"] = 1
        return f"{self.config.payment_url}?{urlencode(params)}"

    def operation_state_url(self, inv_id):
        params = {
            "MerchantLogin": self.config.merchant_login,
            "InvoiceID": inv_id,
            "Signature": self.operation_state_signature(inv_id),
        }
        return f"{OP_STATE_URL}?{urlencode(params)}"

    def get_operation_state(self, inv_id, timeout=10):
        if not self.configured:
            raise ValueError("Robokassa is not configured.")
        if self.config.test_mode:
            raise ValueError("Robokassa OpStateExt is unavailable in test mode.")

        request = Request(
            self.operation_state_url(inv_id),
            headers={"Accept": "application/xml", "User-Agent": "AurumWeb/1.0"},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = response.read(MAX_STATE_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"Robokassa state request failed: {exc}") from exc
        if len(payload) > MAX_STATE_RESPONSE_BYTES:
            raise RuntimeError("Robokassa state response is too large.")
        return self._parse_operation_state(payload)

    @staticmethod
    def _parse_operation_state(payload):
        try:
            root = ElementTree.fromstring(payload)
        except (ElementTree.ParseError, ValueError) as exc:
            raise RuntimeError("Robokassa returned invalid XML.") from exc

        def text_at(path, default=""):
            element = root.find(path)
            return (element.text or "").strip() if element is not None else default

        def integer_at(path):
            value = text_at(path)
            try:
                return int(value) if value else None
            except ValueError:
                return None

        def decimal_at(path):
            value = text_at(path)
            try:
                return _money(value) if value else None
            except (ArithmeticError, ValueError):
                return None

        result_code = integer_at("{*}Result/{*}Code")
        if result_code is None:
            raise RuntimeError("Robokassa response does not contain Result.Code.")
        return RobokassaOperationState(
            result_code=result_code,
            result_description=text_at("{*}Result/{*}Description"),
            state_code=integer_at("{*}State/{*}Code"),
            state_date=text_at("{*}State/{*}StateDate"),
            request_date=text_at("{*}State/{*}RequestDate"),
            out_sum=decimal_at("{*}Info/{*}OutSum"),
            operation_key=text_at("{*}Info/{*}OpKey"),
            payment_method=text_at("{*}Info/{*}PaymentMethod/{*}Code"),
        )
