import re

# Matches a trailing number and optional second bound of a range,
# capturing the leading flavor text separately:
#
#   "Combustion 54"    -> flavor "Combustion",   number "54"
#   "+22%"             -> flavor "",             number "+22%"
#   "Shield Bashing +12" -> flavor "Shield Bashing", number "+12"
#   "+2 or +4"         -> flavor "",             number "+2" (lower bound)
_NUMBER_RE = re.compile(
    r"^(.*?)\s*"
    r"([+-]?\d+(?:\.\d+)?(?:/[1-9]\d*)?\s*%?)"
    r"(?:\s+or\s+[+-]?\d+(?:\.\d+)?(?:/[1-9]\d*)?\s*%?)?$"
)

# Roman-numeral magnitudes (Wizardry II, Riposte XII, ...).
_ROMAN_RE = re.compile(r"^(.*?)\s+([IVXL]+)$")

_ROMAN = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "XII": 12,
    "XIII": 13,
    "XIV": 14,
    "XV": 15,
}


def parse_magnitude(value):
    """Return the numeric magnitude of a value string, or None.

    Handles signed and unsigned numbers, percentages, fractions
    (1/2 -> 0.5), ranges (uses the lower bound) and trailing
    Roman numerals (II -> 2). Non-numeric values such as "Blue"
    or "Improved" return None.
    """
    if not value:
        return None

    value = value.strip()

    match = _NUMBER_RE.match(value)

    if match:
        number = match.group(2).replace(" ", "").replace("%", "")

        if "/" in number:
            numerator, denominator = number.split("/", 1)

            try:
                return float(numerator) / float(denominator)
            except ValueError:
                return None

        try:
            return float(number)
        except ValueError:
            return None

    roman_match = _ROMAN_RE.match(value)

    if roman_match:
        numeral = roman_match.group(2).upper()

        if numeral in _ROMAN:
            return float(_ROMAN[numeral])

    return None
