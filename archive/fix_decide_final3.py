filepath = "/Users/jessy/Documents/Codex/2026-10-16/hey-i-have-local/outputs/multi_ai_builder/agents.py"

with open(filepath, "r") as f:
    content = f.read()

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
        
        bs = chr(92)   # backslash character
        nl = chr(10)   # newline character
        
        new_func = f'''def decide_proposal(
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
    input_text = "Request: " + request + {bs}{nl}{bs}{nl}

    if proposal:
        input_text += "Proposed files:" + {bs}{nl}
        for item in proposal.get("files", []):
            input_text += "  - " + item.get("path", "unknown") + ": " + item.get("content", "")[:500] + {bs}{nl}
        input_text += "\\nSummary: " + proposal.get("summary", "") + {bs}{'n'}

    if review:
        input_text += "{bs}{nl}Reviewer: " + review[:1000]
    if security:
        input_text += "{bs}{nl}Security: " + security[:1000]

    return decide_review(input_text, api_key=OLLAMA_URL)
'''
    content = content[:start_idx] + new_func + content[end_idx:]
    
    with open(filepath, "w") as f:
        f.write(content)
    print("Successfully replaced decide_proposal function")