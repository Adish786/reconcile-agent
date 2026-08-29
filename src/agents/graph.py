from langgraph.graph import StateGraph, END
from src.agents.state import ReconciliationState
from src.agents.nodes import retrieve_node, evaluate_node, route_node

def build_reconciliation_graph():
    workflow = StateGraph(ReconciliationState)

    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("evaluate", evaluate_node)
    workflow.add_node("route", route_node)

    workflow.set_entry_point("retrieve")
    workflow.add_edge("retrieve", "evaluate")
    workflow.add_edge("evaluate", "route")
    workflow.add_edge("route", END)

    return workflow.compile()