
import os
from github import Github
from dotenv import load_dotenv
from github import GithubException

load_dotenv()

def create_github_repo(repository_name, hackathon_event_id, slack_name_of_creator, team_name, team_slack_channel, problem_statement_id, problem_statement_title, github_username, nonprofit_name, nonprofit_id):
    if hackathon_event_id == "2024_fall":
        org_name = "2024-Arizona-Opportunity-Hack"
    else:
        raise ValueError('Not supported hackathon event id')
    
    g = Github(os.getenv('GITHUB_TOKEN'))
    org = g.get_organization(org_name)
    
    if does_repo_exist(repository_name, hackathon_event_id):
        raise ValueError(f"Repository {repository_name} already exists")

    # Catch GitHubException
    try:
        repo = org.create_repo(repository_name, private = False)        
    except GithubException as e:        
        print(e)
        raise ValueError(e.data['message'])
    
    github_admins = ["bmysoreshankar", "jotpowers", "nemathew", "pkakathkar", "vertex", "gregv", "mosesj1914", "ananay", "axeljonson"]
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
# {hackathon_event_id} Hackathon Project

## Quick Links
- [Hackathon Details](https://www.ohack.dev/hack/{hackathon_event_id})
- [Team Slack Channel](https://opportunity-hack.slack.com/app_redirect?channel={team_slack_channel})
- [Nonprofit Partner](https://ohack.dev/nonprofit/{nonprofit_id})
- [Problem Statement](https://ohack.dev/project/{problem_statement_id})

## Creator
@{slack_name_of_creator} (on Slack)

## Team "{team_name}"
- [Team Member 1](GitHub profile link)
- [Team Member 2](GitHub profile link)
- [Team Member 3](GitHub profile link)
<!-- Add all team members -->

## Project Overview
Brief description of your project and its goals.

## Tech Stack
- Frontend: 
- Backend: 
- Database: 
- APIs: 
<!-- Add/modify as needed -->


## Getting Started
Instructions on how to set up and run your project locally.

```bash
# Example commands
git clone [your-repo-link]
cd [your-repo-name]
npm install
npm start
```


## Your next steps
1. ✅ Add everyone on your team to your GitHub repo like [this video posted in our Slack channel](https://opportunity-hack.slack.com/archives/C1Q6YHXQU/p1605657678139600)
2. ✅ Create your DevPost project [like this video](https://youtu.be/vCa7QFFthfU?si=bzMQ91d8j3ZkOD03)
3. ✅ Use the [2024 DevPost](https://opportunity-hack-2024-arizona.devpost.com) to submit your project
4. ✅ Your DevPost final submission demo video should be 4 minutes or less
5. ✅ Review the judging criteria on DevPost

# What should your final Readme look like?
Your readme should be a one-stop-shop for the judges to understand your project. It should include:
- Team name
- Team members
- Slack channel
- Problem statement
- Tech stack
- Link to your DevPost project
- Link to your final demo video
- Any other information you think is important

You'll use this repo as your resume in the future, so make it shine! 🌟

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


def does_repo_exist(repo_name, hackathon_event_id):
    if hackathon_event_id == "2024_fall":
        org_name = "2024-Arizona-Opportunity-Hack"
    else:
        raise ValueError('Not supported hackathon event id')
    
    g = Github(os.getenv('GITHUB_TOKEN'))
    org = g.get_organization(org_name)
    try:
        repo = org.get_repo(repo_name)
        return True
    except GithubException as e:
        print(e)
        return False
    

def validate_github_username(github_username):
    g = Github(os.getenv('GITHUB_TOKEN'))
    try:
        user = g.get_user(github_username)
        return True
    except GithubException as e:
        print(e)
        return False
    


def get_all_repos(hackathon_event_id):
    if hackathon_event_id == "2024_fall":
        org_name = "2024-Arizona-Opportunity-Hack"
    else:
        raise ValueError('Not supported hackathon event id')
    
    g = Github(os.getenv('GITHUB_TOKEN'))
    org = g.get_organization(org_name)
    repos = org.get_repos()
    
    repo_list = []
    for repo in repos:
        repo_list.append({
            "repo_name": repo.name,
            "full_url": f"{repo.html_url}",
            "description": repo.description,
            "owners": [owner.login for owner in repo.get_collaborators()],
            "created_at": repo.created_at,
            "updated_at": repo.updated_at            
        })
