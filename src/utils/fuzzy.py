from rapidfuzz import fuzz

def vendor_similarity(vendor: str, description: str) -> float:
    """Return a similarity score between 0 and 100."""
    return fuzz.token_sort_ratio(vendor, description)

def amount_within_tolerance(amount1: float, amount2: float, tolerance: float = 0.05) -> bool:
    """Check if two amounts are within tolerance (default 5%)."""
    if amount1 == 0 and amount2 == 0:
        return True
    diff = abs(amount1 - amount2) / max(abs(amount1), abs(amount2))
    return diff <= tolerance