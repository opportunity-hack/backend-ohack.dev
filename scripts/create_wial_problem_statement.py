"""
Script to create the "WIAL Global Chapter Network Platform" problem statement
and link it to the World Institute for Action Learning nonprofit.

Usage:
    python create_wial_problem_statement.py
    python create_wial_problem_statement.py --dry-run
"""
import argparse
import os
import sys
import logging
from dotenv import load_dotenv

sys.path.append("../")
load_dotenv()

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)

from common.utils.firebase import (
    create_new_problem_statement,
    link_nonprofit_to_problem_statement,
    add_reference_link_to_problem_statement,
)

# --- Problem Statement Definition (from the WIAL SRD) ---

TITLE = "WIAL Global Chapter Network Platform"

DESCRIPTION = (
    "Build a multi-site website platform for the World Institute for Action Learning (WIAL), "
    "a global nonprofit that certifies Action Learning Coaches across 20+ countries. WIAL needs "
    "a system where chapter leads can provision branded chapter websites from a shared template, "
    "manage a local coach directory that syncs to a global directory, collect membership dues via "
    "Stripe/PayPal, and maintain consistent branding across all sites. The platform must support "
    "multi-language content, role-based access (Super Admin, Chapter Lead, Content Creator, Coach), "
    "and low-bandwidth design for chapters in Africa and SE Asia. AI features include cross-lingual "
    "semantic coach directory search, AI-generated chapter content with cultural adaptation, and "
    "smart coach matching for prospective clients."
)

STATUS = "hackathon"
SLACK_CHANNEL = "#npo-wial"
FIRST_THOUGHT_OF = "2025"
SKILLS = [
    "Next.js/React",
    "Cloudflare Pages",
    "Multi-site Architecture",
    "Stripe/PayPal Integration",
    "Multi-language/i18n",
    "AI/LLM Integration",
    "Semantic Search/Embeddings",
    "Low-bandwidth Design",
]

# Nonprofit to link - provided by user
NONPROFIT_ID = "O5AOBkkTAsUhSrcVaX7V"

# Reference links
REFERENCES = [
    {
        "name": "SRD: WIAL Global Chapter Network",
        "link": "https://docs.google.com/document/d/1smz8xouHO2AzkEaa8iJj95JQZqvyxgOTDOO_BC8N6iI/edit",
    },
    {
        "name": "WIAL Global Website",
        "link": "https://wial.org",
    },
    {
        "name": "WIAL-USA Chapter (example)",
        "link": "https://wial-usa.org",
    },
    {
        "name": "WIAL Nigeria Chapter (example)",
        "link": "https://wialnigeria.org",
    },
]


def main():
    parser = argparse.ArgumentParser(
        description="Create the WIAL problem statement and link nonprofit"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without making changes",
    )
    args = parser.parse_args()

    if args.dry_run:
        print("=== DRY RUN ===\n")
        print(f"Would create problem statement:")
        print(f"  Title:       {TITLE}")
        print(f"  Status:      {STATUS}")
        print(f"  Slack:       {SLACK_CHANNEL}")
        print(f"  First thought of: {FIRST_THOUGHT_OF}")
        print(f"  Skills:      {', '.join(SKILLS)}")
        print(f"  Description: {DESCRIPTION[:150]}...")
        print(f"\nWould link to nonprofit ID: {NONPROFIT_ID}")
        print(f"\nWould add references:")
        for ref in REFERENCES:
            print(f"  - {ref['name']}: {ref['link']}")
        return

    # 1. Create the problem statement
    print("Creating problem statement...")
    result = create_new_problem_statement(
        title=TITLE,
        description=DESCRIPTION,
        status=STATUS,
        slack_channel=SLACK_CHANNEL,
        first_thought_of=FIRST_THOUGHT_OF,
        skills=SKILLS,
    )

    # Handle case where it already exists
    if isinstance(result, list):
        ps_id = result[0].id
        print(f"Problem statement already exists with ID: {ps_id}")
    else:
        ps_id = result["id"]
        print(f"Created problem statement with ID: {ps_id}")

    # 2. Link to nonprofit by directly updating Firestore
    # link_nonprofit_to_problem_statement uses name lookup, but we have the ID.
    # We'll use the firebase utils to link by looking up the nonprofit name first.
    print("\nLinking to World Institute for Action Learning...")
    try:
        from common.utils.firebase import get_db
        db = get_db()
        # Get the nonprofit doc to find its name
        np_doc = db.collection("nonprofits").document(NONPROFIT_ID).get()
        if np_doc.exists:
            np_name = np_doc.to_dict().get("name", "")
            print(f"  Found nonprofit: {np_name}")
            link_nonprofit_to_problem_statement(np_name, ps_id)
            print(f"  OK: Linked '{np_name}'")
        else:
            print(f"  ERROR: Nonprofit {NONPROFIT_ID} not found in database")
    except Exception as e:
        print(f"  ERROR linking nonprofit: {e}")

    # 3. Add reference links
    print("\nAdding references...")
    for ref in REFERENCES:
        try:
            add_reference_link_to_problem_statement(
                problem_statement_id=ps_id,
                name=ref["name"],
                link=ref["link"],
            )
            print(f"  OK: Added '{ref['name']}'")
        except Exception as e:
            print(f"  ERROR adding ref '{ref['name']}': {e}")

    print(f"\nDone! Problem statement ID: {ps_id}")


if __name__ == "__main__":
    main()
