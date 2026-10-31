"""Deterministic reducing-balance EMI engine."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

class FinancialError(ValueError):
    pass


def _decimal(value, name):
    if isinstance(value, bool) or value is None:
        raise FinancialError(f"{name} must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise FinancialError(f"{name} must be a finite number") from exc
    if not result.is_finite():
        raise FinancialError(f"{name} must be a finite number")
    return result


def _validate(principal, annual_rate_pct, months):
    if principal is None or annual_rate_pct is None or months is None:
        raise FinancialError("principal, annual rate and tenure are all required")
    amount = _decimal(principal, "principal")
    rate = _decimal(annual_rate_pct, "annual rate")
    tenure = _decimal(months, "tenure")
    if amount <= 0:
        raise FinancialError("principal must be positive")
    if tenure <= 0 or tenure != tenure.to_integral_value():
        raise FinancialError("tenure must be a positive whole number of months")
    if rate < 0:
        raise FinancialError("interest rate cannot be negative")
    return amount, rate, int(tenure)


def _emi_decimal(principal, annual_rate_pct, months):
    amount, rate, tenure = _validate(principal, annual_rate_pct, months)
    cents = Decimal("0.01")
    if rate == 0:
        return (amount / Decimal(tenure)).quantize(cents, rounding=ROUND_HALF_UP)
    monthly_rate = rate / Decimal(1200)
    factor = (Decimal(1) + monthly_rate) ** tenure
    return (amount * monthly_rate * factor / (factor - Decimal(1))).quantize(cents, rounding=ROUND_HALF_UP)


def emi(principal, annual_rate_pct, months):
    """Calculate standard monthly EMI using Decimal; return the legacy JSON number."""
    return float(_emi_decimal(principal, annual_rate_pct, months))

def summary(principal, annual_rate_pct, months, stated_emi=None):
    amount, rate, tenure = _validate(principal, annual_rate_pct, months)
    payment = _emi_decimal(amount, rate, tenure)
    cents = Decimal("0.01")
    total = (payment * tenure).quantize(cents, rounding=ROUND_HALF_UP)
    interest = (total - amount).quantize(cents, rounding=ROUND_HALF_UP)
    out = {"calculated_emi": float(payment), "total_repayment": float(total),
           "total_interest": float(interest),
           "assumptions": ["Reducing-balance method", "Monthly installments, equal size",
                           "Rate is nominal annual, divided by 12", "Fees, insurance and rate resets excluded"],
           "stated_emi": stated_emi, "difference": None, "note": None}
    if stated_emi is not None:
        difference = (_decimal(stated_emi, "stated EMI") - payment).quantize(cents, rounding=ROUND_HALF_UP)
        out["difference"] = float(difference)
        out["note"] = ("Stated installment is consistent with the calculation (within rounding)."
                       if abs(difference) <= max(Decimal("1.00"), Decimal("0.005") * payment) else
                       "Potential difference. The document may use different assumptions (day-count, rounding, fees). Needs review.")
    out["annual_schedule"] = annual_schedule(amount, rate, tenure, payment)
    return out

def annual_schedule(principal, annual_rate_pct, months, monthly_payment=None):
    """Return a rounded yearly amortization summary using the same reducing-balance EMI."""
    amount, rate, tenure = _validate(principal, annual_rate_pct, months)
    payment = _emi_decimal(amount, rate, tenure) if monthly_payment is None else _decimal(monthly_payment, "monthly payment")
    if payment < 0:
        raise FinancialError("monthly payment cannot be negative")
    balance = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    monthly_rate = rate / Decimal(1200)
    cents = Decimal("0.01")
    years = []
    for month in range(1, tenure + 1):
        interest = (balance * monthly_rate).quantize(cents, rounding=ROUND_HALF_UP)
        principal_paid = min(balance, max(Decimal(0), payment - interest))
        if month == tenure:
            principal_paid = balance
        balance = max(Decimal(0), balance - principal_paid)
        year_index = (month - 1) // 12
        if year_index == len(years):
            years.append({"year": year_index + 1, "principal_paid": Decimal(0),
                          "interest_paid": Decimal(0), "ending_balance": Decimal(0)})
        years[year_index]["principal_paid"] += principal_paid
        years[year_index]["interest_paid"] += interest
        years[year_index]["ending_balance"] = balance
    return [{key: (float(value.quantize(cents, rounding=ROUND_HALF_UP)) if isinstance(value, Decimal) else value)
             for key, value in row.items()} for row in years]
