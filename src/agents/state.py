from typing import List, Dict, Any, Optional
from typing_extensions import TypedDict

class ReconciliationState(TypedDict, total=False):
    invoice_id: int
    transaction_id: Optional[int]
    invoice_data: Dict[str, Any]
    transaction_data: Dict[str, Any]
    candidates: List[Dict[str, Any]]   # each: {id, description, amount, similarity}
    analysis: Optional[str]
    confidence: float
    decision: str                       # AUTO_MATCH, NEEDS_REVIEW, NO_MATCH
    notes: str