
import os
from github import Github
from dotenv import load_dotenv
from github import GithubException

load_dotenv()

def create_github_repo(repository_name, hackathon_event_id, slack_name_of_creator, team_name, team_slack_channel, problem_statement_id, problem_statement_title, github_username):
    if hackathon_event_id == "2023_fall":
        org_name = "2023-opportunity-hack"
    else:
        raise ValueError('Not supported hackathon event id')
    
    g = Github(os.getenv('GITHUB_TOKEN'))
    org = g.get_organization(org_name)
    
    # Catch GitHubException
    try:
        repo = org.create_repo(repository_name, private = False)        
    except GithubException as e:        
        print(e)
        raise ValueError(e.data['message'])
    
    github_admins = ["bmysoreshankar", "jotpowers", "nemathew", "pkakathkar", "vertex", "gregv", "mosesj1914", "ananay", "leonkoech", "axeljonson"]
    if github_username is not None and github_username != "":
        github_admins.append(github_username)

    # Add all admins to repo
    for admin in github_admins:
        try:
            repo.add_to_collaborators(admin, permission="admin")
        except GithubException as e:
            print(e)
            raise ValueError(e.data['message'])


    # Add MIT License to repo
    repo.create_file(
        path="LICENSE",
        message="Add MIT License",
        content="MIT License"
    )

    # Add README.md to repo with hackathon, nonprofit, team, slack_channel, problem statement info, slack_name_of_creator
    repo.create_file(
        path="README.md",
        message="Add README.md",
        content=f'''
# {hackathon_event_id} Hackathon
https://hack.ohack.dev
## Team
{team_name}

## Slack Channel
`#`[{team_slack_channel}](https://opportunity-hack.slack.com/app_redirect?channel={team_slack_channel})

## Problem Statement
[{problem_statement_title}](https://ohack.dev/project/{problem_statement_id})

## Creator
@{slack_name_of_creator} (on Slack)

## Your next steps
1. ✅ Add everyone to your GitHub repo like this: https://opportunity-hack.slack.com/archives/C1Q6YHXQU/p1605657678139600
2. ✅ Create your DevPost project like this https://youtu.be/vCa7QFFthfU?si=bzMQ91d8j3ZkOD03
3. ✅ ASU Students use https://opportunity-hack-2023-asu.devpost.com/
4. ✅ Everyone else use https://opportunity-hack-2023-virtual.devpost.com/
5. ✅ Your DevPost final submission demo video should be 3 minutes or less
6. ✅ Review the judging criteria on DevPost

# What should your final Readme look like?
Examples of stellar readmes:
- ✨ [2019 Team 3](https://github.com/2019-Arizona-Opportunity-Hack/Team-3)
- ✨ [2019 Team 6](https://github.com/2019-Arizona-Opportunity-Hack/Team-6)
- ✨ [2020 Team 2](https://github.com/2020-opportunity-hack/Team-02)
- ✨ [2020 Team 4](https://github.com/2020-opportunity-hack/Team-04)
- ✨ [2020 Team 8](https://github.com/2020-opportunity-hack/Team-08)
- ✨ [2020 Team 12](https://github.com/2020-opportunity-hack/Team-12)
'''
    )
    
    # Update repo description with hackathon, nonprofit, team, problem statement info
    repo.edit(description=f"Repository for {hackathon_event_id} Hackathon, {team_name} Team, {problem_statement_title} Problem Statement")
    
    # Return the repo name
    return {
        "repo_name": repo.name,
        "full_url": f"https://github.com/{org_name}/{repo.name}"
    }
