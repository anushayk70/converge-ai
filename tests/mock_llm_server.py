"""A scripted, deterministic stand-in for an OpenAI-compatible /chat/completions endpoint.

It is NOT a language model. It exists so the LLM code path (prompting, JSON parsing, citation resolution, hallucination
guards, fallbacks) can be tested without an API key. It deliberately misbehaves in two ways so the guards are exercised:
  * the 'Conclusion' section invents a figure ("777 GW") that no source contains
  * the 'Executive Summary' section cites a handle that does not exist ([U9999])
Run:  python tests/mock_llm_server.py 9911
"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

UNIT = re.compile(r"\[(U\d+)\] \(([^)]*)\) (.+)")


def judge(user: str) -> str:
    out = []
    for block in re.split(r"\n(?=CLUSTER C\d+)", user)[1:]:
        cid = re.match(r"CLUSTER (C\d+)", block).group(1)
        units = UNIT.findall(block)
        hint = "(automatic hint:" in block
        first = units[0]
        res = {"cluster": cid, "relation": "conflicting" if hint else "complementary", "merged": first[2], "covers": [first[0]],
               "unique_details": [{"detail": u[2], "unit": u[0]} for u in units[1:]], "conflicts": [], "standalone": []}
        if hint and len(units) >= 2:
            res["unique_details"] = [{"detail": u[2], "unit": u[0]} for u in units[2:]]
            res["conflicts"] = [{"topic": "Figures differ between sources", "explanation": "Mock LLM: the sources give different figures.",
                                 "positions": [{"claim": u[2], "unit": u[0]} for u in units[:2]]}]
            res["covers"] = []
        out.append(res)
    return json.dumps({"clusters": out})


def write(user: str) -> str:
    section = re.search(r"SECTION: (.+)", user).group(1)
    units = UNIT.findall(user)[:6]
    lines = [f"- {u[2][:140].rstrip()} [{u[0]}]" for u in units]
    if section.startswith("Executive"):
        lines.append("- This claim cites a passage that does not exist. [U9999]")
    if section.startswith("Conclusion"):
        lines.append("- Global capacity will reach 777 GW. [" + (units[0][0] if units else "U1") + "]")
    return "\n".join(lines) or "Nothing to report."


def answer(user: str) -> str:
    units = UNIT.findall(user)[:3]
    return "Based on the sources: " + " ".join(f"{u[2][:120]} [{u[0]}]" for u in units)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        system = body["messages"][0]["content"]
        user = body["messages"][-1]["content"]
        user = user if isinstance(user, str) else user[0]["text"]
        if "relationship-judging" in system: text = judge(user)
        elif "report-writing" in system: text = write(user)
        elif "name sections" in system:
            n = len(re.findall(r"THEME \d+:", user)); text = json.dumps({"titles": [f"Mock Theme {i + 1}" for i in range(n)]})
        elif "question-answering" in system: text = answer(user)
        else: text = "OK"
        data = json.dumps({"choices": [{"message": {"role": "assistant", "content": text}}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 9911), H).serve_forever()
