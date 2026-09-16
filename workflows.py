import json
import time
import datetime as dt
import httpx
from sqlalchemy.orm import Session
from models import Workflow, WorkflowRun
from agents import AGENTS, call_llm


async def run_workflow(db: Session, workflow: Workflow) -> WorkflowRun:
    graph = json.loads(workflow.graph_json or "{}")
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])

    run = WorkflowRun(workflow_id=workflow.id, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)

    log = []
    started = time.time()
    context = {"variables": {}}

    def next_nodes(node_id, branch=None):
        out = []
        for e in edges:
            if e["from"] == node_id and (branch is None or e.get("branch") == branch):
                out.append(e["to"])
        return out

    trigger_nodes = [n for n in nodes.values() if n["type"] == "trigger"]
    if not trigger_nodes:
        run.status = "failed"
        log.append({"step": "init", "error": "No trigger node found in workflow."})
        run.log_json = json.dumps(log)
        run.finished_at = dt.datetime.utcnow()
        db.commit()
        return run

    queue = [trigger_nodes[0]["id"]]
    visited_guard = 0

    try:
        while queue and visited_guard < 100:
            visited_guard += 1
            node_id = queue.pop(0)
            node = nodes.get(node_id)
            if not node:
                continue

            ntype = node["type"]
            cfg = node.get("config", {})
            step_log = {"node": node_id, "type": ntype, "label": node.get("label", ntype)}

            if ntype == "trigger":
                step_log["result"] = "Workflow triggered"
                log.append(step_log)
                queue.extend(next_nodes(node_id))

            elif ntype == "agent":
                agent_key = cfg.get("agent", "tara")
                prompt = cfg.get("prompt", "Summarize the current context.")
                agent = AGENTS.get(agent_key, AGENTS["tara"])
                filled_prompt = prompt.format(**context["variables"]) if context["variables"] else prompt
                reply = await call_llm(agent["system"], [{"role": "user", "content": filled_prompt}])
                context["variables"][f"{node_id}_output"] = reply
                step_log["result"] = reply[:300]
                log.append(step_log)
                queue.extend(next_nodes(node_id))

            elif ntype == "condition":
                var = cfg.get("variable", "")
                op = cfg.get("operator", "contains")
                value = cfg.get("value", "")
                actual = context["variables"].get(var, "")
                if op == "contains":
                    result = value.lower() in str(actual).lower()
                elif op == "equals":
                    result = str(actual).strip() == str(value).strip()
                else:
                    result = bool(actual)
                step_log["result"] = f"Condition '{op}' evaluated to {result}"
                log.append(step_log)
                queue.extend(next_nodes(node_id, branch="true" if result else "false"))

            elif ntype == "webhook":
                url = cfg.get("url")
                if url:
                    async with httpx.AsyncClient(timeout=20) as client:
                        try:
                            resp = await client.post(url, json={"context": context["variables"]})
                            step_log["result"] = f"Webhook called, status {resp.status_code}"
                        except Exception as exc:
                            step_log["result"] = f"Webhook failed: {exc}"
                else:
                    step_log["result"] = "No webhook URL configured"
                log.append(step_log)
                queue.extend(next_nodes(node_id))

            elif ntype == "delay":
                # simulated (real deployments should use a background job queue for long delays)
                step_log["result"] = f"Delay step ({cfg.get('seconds', 0)}s) acknowledged"
                log.append(step_log)
                queue.extend(next_nodes(node_id))

            else:
                step_log["result"] = "Unknown node type, skipped"
                log.append(step_log)
                queue.extend(next_nodes(node_id))

        run.status = "success"
    except Exception as exc:
        run.status = "failed"
        log.append({"step": "error", "error": str(exc)})

    run.log_json = json.dumps(log)
    run.finished_at = dt.datetime.utcnow()
    run.duration_ms = int((time.time() - started) * 1000)
    db.commit()
    db.refresh(run)
    return run
