"""Compile JointJS workflow definitions into n8n workflow JSON.

Maps visual workflow step types to n8n node types:
- trigger -> n8n-nodes-base.webhook
- condition -> n8n-nodes-base.if
- approval -> n8n-nodes-base.wait (webhook-based pause)
- email -> n8n-nodes-base.emailSend
- wait/delay -> n8n-nodes-base.wait (timer-based pause)
- update_field -> n8n-nodes-base.httpRequest (PATCH to app API)
- create_record -> n8n-nodes-base.httpRequest (POST to app API)
- call_api -> n8n-nodes-base.httpRequest
- run_rule -> n8n-nodes-base.executeWorkflow (sub-workflow invocation)
- transform -> n8n-nodes-base.set
"""
import logging

logger = logging.getLogger(__name__)

_STEP_TO_N8N = {
    "trigger": {"type": "n8n-nodes-base.webhook", "defaults": {"httpMethod": "POST", "path": "webhook"}},
    "condition": {"type": "n8n-nodes-base.if", "defaults": {}},
    "approval": {"type": "n8n-nodes-base.wait", "defaults": {"resume": "webhook"}},
    "email": {"type": "n8n-nodes-base.emailSend", "defaults": {}},
    "update_field": {"type": "n8n-nodes-base.httpRequest", "defaults": {"method": "PATCH"}},
    "create_record": {"type": "n8n-nodes-base.httpRequest", "defaults": {"method": "POST"}},
    "call_api": {"type": "n8n-nodes-base.httpRequest", "defaults": {"method": "GET"}},
    "wait": {"type": "n8n-nodes-base.wait", "defaults": {"amount": 1, "unit": "hours"}},
    "delay": {"type": "n8n-nodes-base.wait", "defaults": {"amount": 1, "unit": "hours"}},
    "run_rule": {"type": "n8n-nodes-base.executeWorkflow", "defaults": {}},
    "transform": {"type": "n8n-nodes-base.set", "defaults": {}},
}


class WorkflowToN8nCompiler:
    """Compile visual workflow definitions into n8n-compatible JSON."""

    def compile(self, workflow_def: dict) -> dict:
        """Convert a visual workflow definition into n8n workflow JSON.

        Args:
            workflow_def: {"name": str, "steps": [...], "connections": [...]}

        Returns:
            n8n workflow JSON with nodes, connections, settings.
        """
        steps = workflow_def.get("steps", [])
        connections_def = workflow_def.get("connections", [])

        nodes = []
        node_map = {}
        condition_nodes = set()
        for i, step in enumerate(steps):
            node = self._compile_step(step, i)
            nodes.append(node)
            step_id = step.get("id", str(i))
            node_map[step_id] = node["name"]
            if step.get("type") == "condition":
                condition_nodes.add(step_id)

        connections = {}
        for conn in connections_def:
            from_id = conn.get("from", "")
            from_name = node_map.get(from_id)
            to_name = node_map.get(conn.get("to", ""))
            if from_name and to_name:
                if from_name not in connections:
                    if from_id in condition_nodes:
                        # Condition nodes have two outputs: [true_targets, false_targets]
                        connections[from_name] = {"main": [[], []]}
                    else:
                        connections[from_name] = {"main": [[]]}

                label = conn.get("label", "").lower()
                if from_id in condition_nodes and label == "false":
                    # Route to false branch (index 1)
                    connections[from_name]["main"][1].append({"node": to_name})
                else:
                    # Route to true branch (index 0) or default
                    connections[from_name]["main"][0].append({"node": to_name})

        return {
            "name": workflow_def.get("name", "Entelim Workflow"),
            "nodes": nodes,
            "connections": connections,
            "settings": {"executionOrder": "v1"},
        }

    def _compile_step(self, step: dict, index: int) -> dict:
        step_type = step.get("type", "call_api")
        n8n_config = _STEP_TO_N8N.get(step_type, _STEP_TO_N8N["call_api"])
        props = step.get("properties", {})

        node = {
            "name": f"{step_type.title().replace('_', ' ')} {index + 1}",
            "type": n8n_config["type"],
            "position": [250 * (index + 1), 300],
            "parameters": {**n8n_config["defaults"]},
        }

        if step_type == "condition":
            node["parameters"]["conditions"] = {
                "string": [{
                    "value1": f"={{{{$json[\"{step.get('field', 'field')}\"]}}}}",
                    "operation": self._map_operator(step.get("operator", "eq")),
                    "value2": str(step.get("value", "")),
                }]
            }
        elif step_type == "email":
            node["parameters"]["toEmail"] = step.get("to", "")
            node["parameters"]["subject"] = step.get("subject", "Notification")
        elif step_type == "trigger":
            entity = step.get("entity", "record")
            event = step.get("event", "created")
            node["parameters"]["path"] = f"{entity}-{event}"
        elif step_type == "run_rule":
            node["parameters"]["workflowId"] = str(props.get("rule_id", ""))
            node["name"] = f"Run Rule: {props.get('rule_name', 'Rule')} {index + 1}"
        elif step_type in ("delay", "wait"):
            if props.get("amount"):
                node["parameters"]["amount"] = props["amount"]
            if props.get("unit"):
                node["parameters"]["unit"] = props["unit"]
        elif step_type == "update_field":
            if props.get("url"):
                node["parameters"]["url"] = props["url"]
        elif step_type == "create_record":
            if props.get("url"):
                node["parameters"]["url"] = props["url"]
        elif step_type == "call_api":
            if props.get("url"):
                node["parameters"]["url"] = props["url"]
            if props.get("method"):
                node["parameters"]["method"] = props["method"]

        return node

    def _map_operator(self, op: str) -> str:
        return {
            "eq": "equal",
            "neq": "notEqual",
            "gt": "largerThan",
            "lt": "smallerThan",
            "contains": "contains",
        }.get(op, "equal")
