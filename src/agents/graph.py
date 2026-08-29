"""
LangGraph workflow for the reconciliation agent.

This module defines a state graph that orchestrates the reconciliation process:
1. Retrieve: Fetch invoice data and candidate transactions.
2. Evaluate: Use an LLM to reason about candidates and decide on a match.
3. Route: Finalize the decision and prepare the output.

The graph is compiled into a runnable application that can be invoked
with an initial state.
"""

from typing import Any, Dict

from langgraph.graph import END, StateGraph

from src.agents.nodes import evaluate_node, retrieve_node, route_node
from src.agents.state import ReconciliationState

# Constants for node names – helps avoid typos and makes code more maintainable
NODE_RETRIEVE = "retrieve"
NODE_EVALUATE = "evaluate"
NODE_ROUTE = "route"


def build_reconciliation_graph() -> StateGraph:
    """
    Build and compile the LangGraph workflow for invoice reconciliation.

    The workflow consists of three sequential nodes:
        - retrieve: fetches invoice data and candidate transactions.
        - evaluate: uses an LLM to reason about candidates and decide on a match.
        - route: finalizes the decision and prepares the output.

    Returns:
        Compiled StateGraph: A runnable graph that can be invoked with a state dict.
    """
    # 1. Create the state graph with the defined state schema
    workflow = StateGraph(ReconciliationState)

    # 2. Add nodes (each node is a function that updates the state)
    workflow.add_node(NODE_RETRIEVE, retrieve_node)
    workflow.add_node(NODE_EVALUATE, evaluate_node)
    workflow.add_node(NODE_ROUTE, route_node)

    # 3. Define the execution flow
    workflow.set_entry_point(NODE_RETRIEVE)
    workflow.add_edge(NODE_RETRIEVE, NODE_EVALUATE)
    workflow.add_edge(NODE_EVALUATE, NODE_ROUTE)
    workflow.add_edge(NODE_ROUTE, END)

    # 4. Compile the graph (makes it runnable)
    return workflow.compile()