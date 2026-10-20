import re

PHONE_MATCH_DIGITS = 10
LONG_DISTANCE_PREFIXES = ('00', '011')


def normalize_phone(raw_phone, default_country_code):
    """Digits only, with country code: '(55) 1234-5678' -> '525512345678'."""
    digits = re.sub(r'\D', '', raw_phone or '')
    for prefix in LONG_DISTANCE_PREFIXES:
        if digits.startswith(prefix) and len(digits) > PHONE_MATCH_DIGITS + len(prefix):
            digits = digits[len(prefix):]
    if len(digits) == PHONE_MATCH_DIGITS and default_country_code:
        digits = f'{default_country_code}{digits}'
    return digits


def phone_tail(phone):
    """Last 10 digits: what two spellings of the same national number have in common."""
    return (phone or '')[-PHONE_MATCH_DIGITS:]
