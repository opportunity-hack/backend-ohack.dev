from common.utils.slack import send_slack_audit, send_slack, invite_user_to_channel, slack_id_from_user_id
import sys
sys.path.append("../")
#
from common.utils.firebase import get_team_by_name, get_users_in_team_by_name

from dotenv import load_dotenv
load_dotenv()




# Ref: https: // api.slack.com/reference/surfaces/formatting  # linking-urls
def send_funded_slack(details):
    # Lookup Slack channel
    fundraiser_name = details["fundraiser_name"]
    fundraiser_url = details["fundraiser_url"]
    channel_name = get_team_by_name(details["team_name"])
    users_in_team = get_users_in_team_by_name(details["team_name"])
    count_of_users = len(users_in_team)
    slack_ids = [slack_id_from_user_id(x["user_id"]) for x in users_in_team]
    donation_amount = float(details["amount"])
    amount_per_person = donation_amount / count_of_users
    people_person_wording = "people" if count_of_users > 1 else "person"
    
    message = "*You have funds*! :partyparrot: "
    for slack_user_id in slack_ids:
        invite_user_to_channel(user_id=slack_user_id, channel_name=channel_name)
        message += f"<@{slack_user_id}>"

    message += f"\n:moneybag: You have received a donation of ${donation_amount:.2f} to your fundraiser: <{fundraiser_url}|*{fundraiser_name}*> "
    message += f"\nWith a team size of {count_of_users} {people_person_wording}, this is ${amount_per_person:.2f} per person."
    message += "\nKeep up the good work and share your donation link to get even more prize money <https://i.imgur.com/PotAlNG.png|#funded>!"
    message += "\n"
    
    print(message)

    send_slack(username="The Good News Bot of Wonder and Amazement",
                message=message,
                channel=channel_name,
                icon_emoji=":astronaut-hooray-woohoo-yeahfistpump:")
    return

funded_details = {
    "amount": "100",
    "fundraiser_url": "https://www.paypal.com/donate/?hosted_button_id=K4NCTNNGU2BLG",
    "fundraiser_name": "Test Completion Prize: 1st Place Team",
    "team_name": "Test"
    #"team_name": "Newsletters for Change"
    #"team_name": "ReactLovers"
}

send_funded_slack(funded_details)
