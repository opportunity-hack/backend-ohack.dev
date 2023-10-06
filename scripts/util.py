import argparse
import os
import sys
import logging
from dotenv import load_dotenv
sys.path.append("../")
load_dotenv()
# add logger
logger = logging.getLogger(__name__)
# set logger to standard out
logger.addHandler(logging.StreamHandler())
# set log level
logger.setLevel(logging.INFO)
#
from common.utils.slack import send_slack, get_active_users
from common.utils.firebase import get_hackathon_by_event_id, add_reference_link_to_problem_statement, create_new_problem_statement, link_nonprofit_to_problem_statement, link_problem_statement_to_hackathon_event, get_nonprofit_by_id, add_image_to_nonprofit_by_nonprofit_id, add_image_to_nonprofit, add_nonprofit_to_hackathon, create_new_problem_statement, create_new_nonprofit, create_new_hackathon, link_nonprofit_to_problem_statement, link_problem_statement_to_hackathon_event, get_nonprofit_by_name, create_team, add_user_by_email_to_team, add_user_by_slack_id_to_team, add_team_to_hackathon, add_problem_statement_to_team, get_user_by_user_id, add_reference_link_to_problem_statement, get_user_by_email, create_user, add_user_to_team, delete_user_by_id, get_team_by_name, get_user_by_id, remove_user_from_team
from common.utils.cdn import upload_to_cdn
import re
import urllib.request

# util.py handles command-line arguments to perform database actions
# for example, to add a nonprofit to a hackathon, run: 
# python3 util.py add_nonprofit_to_hackathon --nonprofit_name "American Red Cross" --hackathon_name "Hack Arizona 2021"
# to add a problem statement to a team, run:
# python3 util.py add_problem_statement_to_team --problem_statement_id "1" --team_id "1"

def util_add_image_to_nonprofit(nonprofit_id=None, nonprofit_name=None, image_url=None):
    # keep original url 
    original_image_url = image_url

    # url deconde image url
    image_url = urllib.parse.unquote(image_url)

    #log
    logger.info(f"Image url: {image_url}")

    # Get image and ensure it exists
    image_name = image_url.split("/")[-1]
    
    #replace all special characters with underscores in a single regex
    image_name = re.sub('[^A-Za-z0-9\.]+', '_', image_name)
    
    # Get the nonprofit name by id
    if nonprofit_name is None:
        nonprofit_name = get_nonprofit_by_id(nonprofit_id)["name"]

    # Prefix nonprofit name to image name, use the same regex to replace special characters    
    image_name = re.sub('[^A-Za-z0-9\.]+', '_', nonprofit_name) + "_" + image_name

    # log
    logger.info(f"Image name: {image_name}")

    urllib.request.urlretrieve(original_image_url, image_name)
    if not os.path.exists(image_name):
        logger.error(f"Image {image_name} does not exist")
        return
    else:
        logger.info(f"Image {image_name} downloaded")
    
    # Upload image to CDN
    upload_to_cdn("nonprofit_images", image_name)
    logger.info(f"Image {image_name} uploaded to CDN")

    # Construct CDN url
    cdn_url = f"{os.getenv('CDN_SERVER')}/nonprofit_images/{image_name}"

    # Remove image from local directory
    os.remove(image_name)


    # Add image to nonprofit
    if nonprofit_id is not None:
        add_image_to_nonprofit_by_nonprofit_id(nonprofit_id, cdn_url)
    else:
        add_image_to_nonprofit(nonprofit_name, cdn_url)



# Handle command-line arguments
parser = argparse.ArgumentParser(description='Utility script for database actions')
parser.add_argument('action', type=str, help='action to perform')
parser.add_argument('--nonprofit_name', type=str, help='nonprofit name')
parser.add_argument('--nonprofit_id', type=str, help='nonprofit id')
parser.add_argument('--description', type=str, help='nonprofit description')
parser.add_argument('--website', type=str, help='nonprofit website')
parser.add_argument('--contact_name', type=str, help='nonprofit contact name')

parser.add_argument('--hackathon_event_id', type=str, help='hackathon event id')
parser.add_argument('--problem_statement_id', type=str, help='problem statement id')
parser.add_argument('--image_url', type=str, help='image url')
parser.add_argument('--team_id', type=str, help='team id')

parser.add_argument('--problem_statement_title', type=str, help='problem statement title')
parser.add_argument('--problem_statement_description', type=str, help='problem statement description')
parser.add_argument('--problem_statement_status', type=str, help='problem statement status')
parser.add_argument('--problem_statement_slack_channel', type=str, help='problem statement slack channel')
parser.add_argument('--problem_statement_first_though_of', type=str, help='problem statement first thought of')
parser.add_argument('--problem_statement_skills', type=str, help='problem statement skills')
parser.add_argument('--problem_statement_reference_name', type=str, help='problem statement reference name')
parser.add_argument('--problem_statement_reference_url', type=str, help='problem statement reference url')




parser.add_argument('--slack_channel', type=str, help='user slack channel')
parser.add_argument('--slack_message', type=str, help='slack message')

args = parser.parse_args()

# Perform action
if args.action == "add_nonprofit_to_hackathon":
    add_nonprofit_to_hackathon(args.nonprofit_name, args.hackathon_event_id)
elif args.action == "add_problem_statement_to_team":
    add_problem_statement_to_team(args.problem_statement_id, args.team_id)
elif args.action == "add_image_to_nonprofit":
    util_add_image_to_nonprofit(nonprofit_name=args.nonprofit_name, nonprofit_id=args.nonprofit_id, image_url=args.image_url)
elif args.action == "send_slack":
    send_slack(channel=args.slack_channel, message=args.slack_message)
elif args.action == "active_slack_users":
    print("\n".join(get_active_users()))
elif args.action == "create_new_nonprofit":
    create_new_nonprofit(
        name=args.nonprofit_name, description=args.description, 
        website=args.website, 
        slack_channel=args.slack_channel,
        contact_people=args.contact_name,
        image=args.image_url)
elif args.action == "create_problem_statement":
    result = create_new_problem_statement(
        args.problem_statement_title,
        args.problem_statement_description,
        args.problem_statement_status,
        args.problem_statement_slack_channel,
        args.problem_statement_first_though_of,
        args.problem_statement_skills.split(","))
    
    if args.nonprofit_name is not None:        
        link_nonprofit_to_problem_statement(args.nonprofit_name, result["id"])

    if args.hackathon_event_id is not None:
        link_problem_statement_to_hackathon_event(hackathon_event_id=args.hackathon_event_id, problem_statement_id=result["id"])        

    if args.problem_statement_reference_name is not None:
        add_reference_link_to_problem_statement(
            problem_statement_id=result["id"],
            name=args.problem_statement_reference_name,
            link=args.problem_statement_reference_url)
        

else:
    # Print help
    parser.print_help()

