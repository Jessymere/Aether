import html
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.parse

from orchestrator import Orchestrator
from agents import coder, parse_proposal, planner, reviewer, security_reviewer, tester, revise, workspace_context
from config import WORKSPACE
from loop_guard import LoopGuard
from proposal_validation import validate_proposal
from web_research import needs_web, research
from vault import read_vault

STATE = {"status": "Ready", "request": "", "plan": "", "proposal": None, "review": "", "security": "", "validation": "", "tester": "", "error": ""}
LOCK = threading.Lock()


def page() -> str:
    with LOCK:
        state = dict(STATE)
    proposal = state["proposal"]
    files = "" if not proposal else "<br>".join(html.escape(item["path"]) for item in proposal["files"])
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Local Multi-AI Builder</title>
<style>*{{box-sizing:border-box}}body{{margin:0;background:#111;color:#eee;font:14px system-ui;display:grid;grid-template-columns:250px 1fr 320px;height:100vh}}aside{{background:#191919;border-right:1px solid #303030;padding:20px}}main{{display:flex;flex-direction:column;min-width:0}}.top{{height:64px;border-bottom:1px solid #303030;padding:20px 28px;font-weight:600}}.chat{{flex:1;overflow:auto;padding:28px;max-width:1000px;width:100%;margin:auto}}.composer{{border-top:1px solid #303030;padding:18px 28px}}textarea{{width:100%;height:76px;resize:vertical;background:#242424;color:#fff;border:1px solid #444;border-radius:14px;padding:14px;font:inherit}}button{{border:0;border-radius:9px;padding:10px 16px;background:#4f7cff;color:#fff;cursor:pointer;margin-top:10px}}.pill{{color:#8ee28e;font-size:12px}}.card{{background:#202020;border:1px solid #333;border-radius:14px;padding:18px;margin-bottom:18px}}pre{{white-space:pre-wrap;max-height:300px;overflow:auto;color:#dbe5ff;margin:8px 0}}.user{{background:#28599e;border-radius:14px;padding:12px 16px;margin:0 0 20px auto;max-width:75%}}.muted{{color:#999}}.files{{color:#b8d0ff}}.nav{{padding:10px 0;color:#bbb}}.brand{{font-size:18px;font-weight:700;margin-bottom:24px}}h2,h3{{margin-top:0}}</style></head><body>
<aside><div class='brand'>Local AI Builder</div><div class='nav'>⌂ New build</div><div class='nav'>▣ Project</div><div class='nav'>◷ Run history</div><p class='muted'>Local AI builder</p><p class='muted'>Planner · Coder · Reviewer · Security · Tester · Release</p></aside>
<main><div class='top'>Plan a local multi-AI build <span class='pill'>● {html.escape(state['status'])}</span></div><div class='chat'>{('<div class="user">'+html.escape(state['request'])+'</div>') if state['request'] else '<div class="card"><h2>What are we building?</h2><p class="muted">Describe a feature, bug, app, or project change below.</p></div>'}<div class='card'><h3>Activity and plan</h3><pre>{html.escape(state['plan'] or 'Waiting for a request...')}</pre></div><div class='card'><h3>Review</h3><pre>{html.escape(state['review'] or 'The reviewer will report findings here.')}</pre></div></div><div class='composer'><form method='post' action='/start'><textarea name='request' placeholder='Describe what you want to build...'>{html.escape(state['request'])}</textarea><br><button>Run AI team</button></form></div></main>
<section style='padding:20px;background:#191919;border-left:1px solid #303030'><h3>Changes</h3><p class='muted'>Proposed files</p><p class='files'>{files or 'None yet'}</p>{('<form method="post" action="/approve"><button>Approve and write files</button></form>' if proposal and state['status'] == 'Awaiting approval' else '')}{('<div class="card"><b>Error</b><p>'+html.escape(state['error'])+'</p></div>' if state['error'] else '')}</section></body></html>"""


def run_agents(request: str) -> None:
    try:
        context = workspace_context(WORKSPACE)
        vault_text = ""
        from vault import read_vault
        vault_text = read_vault()
        context = f"{context}\n\n{vault_text}" if context else vault_text

        web_brief = ""
        if needs_web(request):
            web_brief = research(request)

        plan = planner(request, context, web_brief)
        raw = coder(request, plan, context)
        proposal = parse_proposal(raw)
        review = reviewer(request, plan, raw)

        loop_guard = LoopGuard()
        for _ in range(3):
            if "REVISE" not in review.upper():
                break
            if not loop_guard.allow("revision", json.dumps(proposal, sort_keys=True) + review):
                break
            raw = revise(request, plan, json.dumps(proposal), review)
            proposal = parse_proposal(raw)
            review = reviewer(request, plan, raw)

        security_report = security_reviewer(request, json.dumps(proposal))
        validation_ok, validation_msg = validate_proposal(proposal)
        tester_report = tester(request, json.dumps(proposal), validation_msg)

        with LOCK:
            STATE.update({
                "status": "Awaiting approval",
                "request": request,
                "plan": plan,
                "proposal": proposal,
                "review": review,
                "security": security_report,
                "validation": validation_msg,
                "tester": tester_report,
                "error": "",
            })
    except Exception as exc:
        with LOCK:
            STATE.update({"status": "Error", "error": str(exc)})


class Handler(BaseHTTPRequestHandler):
    def reply(self):
        body = page().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        if self.path == "/start":
            request = form.get("request", [""])[0].strip()
            if request:
                with LOCK:
                    STATE.update({"status": "Running", "request": request, "error": ""})
                threading.Thread(target=run_agents, args=(request,), daemon=True).start()
        elif self.path == "/approve":
            with LOCK:
                proposal = STATE.get("proposal")
            if proposal:
                for item in proposal["files"]:
                    target = WORKSPACE / item["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(item["content"], encoding="utf-8")
                with LOCK:
                    STATE["status"] = "Applied"
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()


if __name__ == "__main__":
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    print("Open http://127.0.0.1:8765 in your browser")
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()