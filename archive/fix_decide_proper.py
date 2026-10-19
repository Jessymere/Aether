filepath = "/Users/jessy/Documents/Codex/2026-10-16/hey-i-have-local/outputs/multi_ai_builder/agents.py"

with open(filepath, "r") as f:
    content = f.read()

# Find start and end of decide_proposal
start_idx = content.find("def decide_proposal(")
if start_idx == -1:
    print("ERROR: Could not find start of decide_proposal")
else:
    end_marker = "    return decide_review(input_text, api_key=OLLAMA_URL)\n"
    end_idx = content.find(end_marker, start_idx)
    if end_idx == -1:
        print("ERROR: Could not find end of decide_proposal")
    else:
        end_idx += len(end_marker)
        
        new_func = '''def decide_proposal(
    request: str,
    proposal: dict,
    review: str | None = None,
    security: str | None = None,
) -> dict:
    """Run a proposal through the decider (gpt-6-luna-decisions).

    This is a fast, cheap gate that runs BEFORE expensive validation.
    It asks the decider model whether the proposal should be approved
    or revised, based on the request, proposal, reviewer findings,
    and security report.

    Args:
        request: The original user request.
        proposal: The proposed file changes dict.
        review: Optional reviewer report string.
        security: Optional security review string.

    Returns:
        A dict with the decision result from decider.
    """
    input_text = f"Request: {request}\\n\\n"

    if proposal:
        input_text += "Proposed files:\\n"
        for item in proposal.get("files", []):
            input_text += f"  - {item.get('path', 'unknown')}: {item.get('content', '')[:500]}\\n"
        input_text += "\\nSummary: " + proposal.get("summary", "") + "\\n"

    if review:
        input_text += "\\nReviewer: " + review[:1000]
    if security:
        input_text += "\\nSecurity: " + security[:1000]

    return decide_review(input_text, api_key=OLLAMA_URL)
'''
    content = content[:start_idx] + new_func + content[end_idx:]
    
    with open(filepath, "w") as f:
        f.write(content)
    print("Successfully replaced decide_proposal function")