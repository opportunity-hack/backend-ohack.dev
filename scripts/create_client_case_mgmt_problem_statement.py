"""
Script to create the "Nonprofit Client & Case Management Platform" problem statement
and link it to the associated nonprofits from the SRD.

Usage:
    python create_client_case_mgmt_problem_statement.py
    python create_client_case_mgmt_problem_statement.py --dry-run
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
    get_nonprofit_by_name,
)

# --- Problem Statement Definition (from the SRD) ---

TITLE = "Nonprofit Client & Case Management Platform"

DESCRIPTION = (
    "Build a lightweight, open-source client and case management web application "
    "that any nonprofit can deploy for under $30/month. The system handles client "
    "registration, demographics, visit scheduling, treatment/service logging, "
    "role-based access, and basic reporting. It is the generalized version of "
    "problems submitted by 9+ OHack nonprofits across 7 hackathons (2016-2024). "
    "Key insight: every one of these nonprofits submitted fundamentally the same "
    "problem - 'We need to register clients, record what we do for them, and "
    "report on it.' The only differences are domain-specific vocabulary (patients "
    "vs. animals vs. alumni vs. families). A configurable system with customizable "
    "fields solves all of them."
)

STATUS = "hackathon"
SLACK_CHANNEL = "#npo-client-case-mgmt"
FIRST_THOUGHT_OF = "2016"
SKILLS = [
    "React/Next.js",
    "Python/FastAPI",
    "PostgreSQL/Supabase",
    "Authentication/RBAC",
    "CSV Import/Export",
    "AI/LLM Integration",
]

# Nonprofits to link (exact names as they appear in the DB)
NONPROFITS = [
    "Chandler CARE Center",
    "Lost Our Home Pet Rescue",
    "SEED SPOT",
    "NMTSA - Education Platform",
    "NMTSA - Website",
    "Tranquility Trail Animal Sanctuary",
]

# Nonprofits from the SRD that are NOT in the DB yet
MISSING_NONPROFITS = [
    "Will2Walk",
    "ICM Food & Clothing Bank",
    "Sunshine Acres",
]

# Reference links
REFERENCES = [
    {
        "name": "SRD: Client & Case Management",
        "link": "https://docs.google.com/document/d/1smz8xouHO2AzkEaa8iJj95JQZqvyxgOTDOO_BC8N6iI/edit",
    },
    {
        "name": "OHack 2020 Summer Internship (EHR + CRM)",
        "link": "https://github.com/opportunity-hack/2020-summer-volunteer-internship",
    },
    {
        "name": "Chandler CARE Center 2019 (2nd Place)",
        "link": "https://devpost.com/software/chandler-care-center-data-intake",
    },
    {
        "name": "NMTSA 2019 Schedule App",
        "link": "https://devpost.com/software/nmtsa-scheduleapp",
    },
    {
        "name": "NMTSA 2017 Project",
        "link": "https://devpost.com/software/team-3-nmtsa",
    },
]


def main():
    parser = argparse.ArgumentParser(
        description="Create the Client & Case Management problem statement and link nonprofits"
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
        print(f"\nWould link to nonprofits:")
        for np_name in NONPROFITS:
            print(f"  - {np_name}")
        print(f"\nWould add references:")
        for ref in REFERENCES:
            print(f"  - {ref['name']}: {ref['link']}")
        print(f"\nWARNING: These nonprofits are NOT in the DB and will be skipped:")
        for np_name in MISSING_NONPROFITS:
            print(f"  - {np_name}")
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
        # Already exists - returned list of DocumentSnapshots
        ps_id = result[0].id
        print(f"Problem statement already exists with ID: {ps_id}")
    else:
        ps_id = result["id"]
        print(f"Created problem statement with ID: {ps_id}")

    # 2. Link to nonprofits
    print("\nLinking to nonprofits...")
    for np_name in NONPROFITS:
        try:
            # Verify nonprofit exists first
            np = get_nonprofit_by_name(np_name)
            if not np:
                print(f"  SKIP: '{np_name}' not found in database")
                continue
            link_nonprofit_to_problem_statement(np_name, ps_id)
            print(f"  OK: Linked '{np_name}'")
        except Exception as e:
            print(f"  ERROR linking '{np_name}': {e}")

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
    print(f"\nNOTE: These nonprofits need to be created in the DB before they can be linked:")
    for np_name in MISSING_NONPROFITS:
        print(f"  - {np_name}")


if __name__ == "__main__":
    main()
