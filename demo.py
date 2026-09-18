"""Ask Jev to classify a support ticket and judge its urgency."""

import sys
from pathlib import Path
from jev_client import ask, load_key

key = load_key(Path(__file__).with_name(".env"))

ticket = " ".join(sys.argv[1:]) or (
    "My payment failed twice, and I need access before my meeting today."
)
body = {
    "model": "jev-latest",
    "state": ticket,
    "questions": {
        "team": {
            "type": "choice",
            "instructions": "Which team should handle this support ticket?",
            "criteria": {
                "billing": "Payments, invoices, or refunds",
                "technical": "Bugs, access, or integration problems",
                "sales": "Pricing, plans, or new accounts",
            },
        },
        "urgent": {
            "type": "noul",
            "instructions": "Does this ticket need a response today?",
        },
    },
}
answers = ask(key, ticket, body["questions"])["answers"]

team = answers["team"]
assert team["type"] == "choice" and answers["urgent"]["type"] == "noul"
print(f"Ticket: {ticket}")
print(f"Team: {team['choice']} (confidence {team['confidence']:.1%})")
print(f"Needs response today: {answers['urgent']['noul']:.1%} probability")
