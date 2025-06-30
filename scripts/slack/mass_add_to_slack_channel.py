import sys
sys.path.append("../../")


from common.utils.slack import invite_user_to_channel_id


SLACK_USER_IDS = ["U08V5VDDALA",
    "U08UVKVQ944",
    "U0901NNGZ3J",
    "U08UX7M650W",
    "U08UTDHV5UH",
    "U08V7GHG8AH",
    "U08M5KDK3HC",
    "U08V4QNHAQG",
    "U05TTFF7M1B",
    "U07S9HBBK6U",
    "U08U692UBP1",
    "U08V4Q1APPT",
    "U08UF19E022",
    "U08ULL5171R",
    "U08UTUMC7TL",
    "U07KXQSFAAH",
    "U08UM1YUJA1",
    "U08VAQ5FG4Q",
    "U08V6SN26QH",
    "U08UVCXQH0C",
    "U08V1SZPNNS",
    "U08UJE7USDA",
    "U08UMLGE7TQ",
    "U08UUHWSQSZ",
    "U08F1NZ6Q2J",
    "U0476G7EYCQ",
    "U07QHE3J8GN",
    "U08UD1UHRSR",
    "U07RPHDGFE0",
    "U08UFN1GNNT",
    "U08UUMTAHLM",
    "U08UT5XN0MB",
    "U08UJTP4VD3",
    "U08UKGFDAS1",
    "U08UK1EVC5S",
    "U08VBHKT3E0",
    "U08VCBGPAV6",
    "U08UPUQ6UJF",
    "U08UJ3SFSVA",
    "U09079MQCSD",
    "U08UQ171BV5",
    "U07RE7F95DL",
    "U06294V92DS",
    "U05U7KTNWTC",
    "U0476G7EYCQ",
    "U08UJTWSXLM",
    "U08V6E1LU2H",
    "U08UP3CRS7R",
    "U08USSCL0SW",
    "U08U8FRHVNF",
    "U08ULRRU4BF",
    "U08UFNA3VLN",
    "U08UMK9URSS",
    "U08UVELCC9Z",
    "U08UJ0WPTJP",
    "U08UNS1CLLT",
    "U08UX3U8Q59",
    "U08UETWMHSA",
    "U08UYCFQ7ED",
    "U05UNL0J0F6",
    "U09034C81EU",
    "U05UVJK271B",
    "U06JEHWD0AC",
    "U08T8HLAC75",
    "U076705DGKY",
    "U09015ZN0Q0",
    "U08UFGFQLBU",
    "U088F7M4RHV",
    "U08UU3AMLJD",
    "U08UMH2JVNY",
    "U08ULDRJV43",
    "U08UJMY9CBF"
    ]


def mass_add_to_slack_channel(slack_user_ids):
    channel_id = "C093ZB02W72"
    for user_id in slack_user_ids:
        print(f"Inviting {user_id} to channel_id {channel_id}")
        invite_user_to_channel_id(user_id=user_id, channel_id=channel_id)

if __name__ == "__main__":
    mass_add_to_slack_channel(SLACK_USER_IDS)
    print("All users invited to the channel.")

# This script invites a list of Slack user IDs to a specified Slack channel.
# It uses the `invite_user_to_channel` function from the common.utils.slack module.