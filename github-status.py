import json, os, subprocess, yaml
from dateutil.parser import parse
from datetime import datetime, UTC
from babel.dates import format_timedelta
import requests
from rich import print, box
from rich.table import Table
from rich.text import Text
from rich.style import Style

def date_ago(date):
    now = datetime.now(UTC)
    date = parse(date) - now
    return format_timedelta(date, add_direction=True)

with open(os.path.expanduser("~/.config/gh/hosts.yml"), "r") as f:
    GITHUB_TOKEN = yaml.safe_load(f)['github.com']['oauth_token']

def github_api(endpoint, json=None):
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}
    if json:
        response = requests.post(f"https://api.github.com/{endpoint}", json=json, headers=headers)
    else:
        response = requests.get(f"https://api.github.com/{endpoint}", headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception("Query failed to run by returning code of {}. {}".format(response.status_code, response.text))

def run_graphql_query(query):
    return github_api('graphql', {'query': query})

FRAGMENT_COMMON = """
  author { login }
  labels(first: 20) {
    nodes { name }
  }
  number
  repository {
      owner { login }
      name
      nameWithOwner
  }
  timelineItems(itemTypes: CROSS_REFERENCED_EVENT, last: 20) {
    nodes {
      ... on CrossReferencedEvent {
        willCloseTarget
        source {
          ... on PullRequest {
            number
          }
        }
      }
    }
  }
  title
  updatedAt
  url
"""

FRAGMENT_ISSUE = """
fragment Issue on Issue {
  """ + FRAGMENT_COMMON + """
}
"""

FRAGMENT_PR = """
fragment PR on PullRequest {
  """ + FRAGMENT_COMMON + """
  headRefName
  mergeable
  reviewDecision
}
"""

def search_query(search):
    return FRAGMENT_ISSUE + FRAGMENT_PR + """
        query {
          search(first: 100, type: ISSUE, query: \""""+search+"""\") {
            nodes {
              ... on PullRequest {
                  ...PR
              }
              ... on Issue {
                  ...Issue
              }
            }
          }
        }
    """

def notifications(org=None):
    rows = github_api('notifications')
    rows.sort(key = lambda row: row['updated_at'])

    table = Table(title="Notifications", box=box.SIMPLE)
    table.add_column("Repo")
    table.add_column("Title")
    table.add_column("Type")
    table.add_column("Reason")
    table.add_column("Updated", style="bright_black")

    for row in rows:
        if org:
            if row['repository']['owner']['login'] != org:
                continue
        url = row['subject']['url'].replace("api.", "").replace("repos/", "").replace("pulls", "pull")
        table.add_row(
            row['repository']['name'],
            Text(row['subject']['title'], style=Style(link=url)),
            row['subject']['type'],
            row['reason'],
            date_ago(row['updated_at']),
        )
    return table

def open_prs(org=None):
    query = "state:open author:Nadrieril is:pr"
    if org:
        query += f" org:{org}"
    query = search_query(query)
    result = run_graphql_query(query)
    rows = result['data']['search']['nodes']
    rows.sort(key = lambda row: row['updatedAt'])

    table = Table(title="Open PRs", box=box.SIMPLE)
    table.add_column("Repo")
    table.add_column("Number", style="green")
    table.add_column("Title")
    table.add_column("Branch", style="cyan")
    table.add_column("Updated", style="bright_black")

    for row in rows:
        table.add_row(
            row['repository']['name'],
            f"#{row['number']}",
            Text(row['title'], style=Style(link=row['url'])),
            row['headRefName'],
            date_ago(row['updatedAt']),
        )
    return table

def assigned(org=None):
    query = "state:open assignee:Nadrieril"
    if org:
        query += f" org:{org}"
    query = search_query(query)
    result = run_graphql_query(query)
    rows = result['data']['search']['nodes']
    rows.sort(key = lambda row: row['updatedAt'])

    table = Table(title="Assigned PRs and issues", box=box.SIMPLE)
    table.add_column("Repo")
    table.add_column("Number", style="green")
    table.add_column("Title")
    table.add_column("Ref", style="blue")
    table.add_column("Updated", style="bright_black")

    for row in rows:
        closing_pr = ""
        if cross_refs := row.get('timelineItems'):
            for item in cross_refs['nodes']:
                if item.get('willCloseTarget'):
                    closing_pr = f"#{item['source']['number']}"

        table.add_row(
            row['repository']['name'],
            f"#{row['number']}",
            Text(row['title'], style=Style(link=row['url'])),
            closing_pr,
            date_ago(row['updatedAt']),
        )
    return table

if __name__ == "__main__":
    # If we're in a github repo, filter all the output to this repo's organization.
    org = None
    out = subprocess.run(["gh", "repo", "view", "--json", "owner"], capture_output=True)
    if out.returncode == 0:
        json = json.loads(out.stdout)
        org = json['owner']['login']

    print(notifications(org))
    print(open_prs(org))
    print(assigned(org))
