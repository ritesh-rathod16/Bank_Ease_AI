"""Deterministic reducing-balance EMI engine."""
from dataclasses import dataclass

class FinancialError(ValueError):
    pass

def emi(principal, annual_rate_pct, months):
    if principal is None or annual_rate_pct is None or months is None:
        raise FinancialError("principal, annual rate and tenure are all required")
    if principal <= 0:
        raise FinancialError("principal must be positive")
    if months <= 0 or int(months) != months:
        raise FinancialError("tenure must be a positive whole number of months")
    if annual_rate_pct < 0:
        raise FinancialError("interest rate cannot be negative")
    n = int(months)
    if annual_rate_pct == 0:
        return round(principal / n, 2)
    r = annual_rate_pct / 12 / 100
    f = (1 + r) ** n
    return round(principal * r * f / (f - 1), 2)

def summary(principal, annual_rate_pct, months, stated_emi=None):
    e = emi(principal, annual_rate_pct, months)
    total = round(e * int(months), 2)
    out = {"calculated_emi": e, "total_repayment": total,
           "total_interest": round(total - principal, 2),
           "assumptions": ["Reducing-balance method", "Monthly installments, equal size",
                           "Rate is nominal annual, divided by 12", "Fees, insurance and rate resets excluded"],
           "stated_emi": stated_emi, "difference": None, "note": None}
    if stated_emi is not None:
        d = round(stated_emi - e, 2)
        out["difference"] = d
        out["note"] = ("Stated installment is consistent with the calculation (within rounding)."
                       if abs(d) <= max(1.0, 0.005 * e) else
                       "Potential difference. The document may use different assumptions (day-count, rounding, fees). Needs review.")
    return out
