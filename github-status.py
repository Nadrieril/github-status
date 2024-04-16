import json, subprocess
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
            url
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
    return """
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
    """

def report_notifications(rows, org=None):
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

def report_open_prs(data):
    rows = data['nodes']
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

def report_assigned(data):
    rows = data['nodes']
    for row in rows:
        closing_pr = None
        if cross_refs := row.get('timelineItems'):
            for item in cross_refs['nodes']:
                if item.get('willCloseTarget'):
                    number = f"#{item['source']['number']}"
                    closing_pr = Text(number, style=Style(link=item['source']['url']))
        row['closing_pr'] = closing_pr
        row['blocked'] = any('blocked' in l['name'] for l in row['labels']['nodes'])

    rows.sort(key = lambda row: (row['closing_pr'] is None, not row['blocked'], row['updatedAt']))

    table = Table(title="Assigned PRs and issues", box=box.SIMPLE)
    table.add_column("Repo")
    table.add_column("Number", style="green")
    table.add_column("Title")
    table.add_column("Ref", style="blue")
    table.add_column("Labels")
    table.add_column("Updated", style="bright_black")

    for row in rows:
        labels = row['labels']['nodes']
        labels = ", ".join(l['name'] for l in labels)
        table.add_row(
            row['repository']['name'],
            f"#{row['number']}",
            Text(row['title'], style=Style(link=row['url'])),
            row['closing_pr'],
            labels,
            date_ago(row['updatedAt']),
        )
    return table

if __name__ == "__main__":
    # Retrieve the GitHub login token.
    out = subprocess.run(["gh", "auth", "token"], capture_output=True)
    assert out.returncode == 0
    GITHUB_TOKEN = out.stdout.decode('utf-8').strip()

    # If we're in a github repo, filter all the output to this repo's organization.
    org = None
    out = subprocess.run(["gh", "repo", "view", "--json", "owner"], capture_output=True)
    if out.returncode == 0:
        json = json.loads(out.stdout)
        org = json['owner']['login']

    notifications = github_api('notifications')
    print(report_notifications(notifications, org))

    open_prs_query = "state:open author:Nadrieril is:pr"
    assigned_query = "state:open assignee:Nadrieril"
    if org:
        open_prs_query += f" org:{org}"
        assigned_query += f" org:{org}"
    query = FRAGMENT_ISSUE + FRAGMENT_PR + f"""query {{
        open_prs: {search_query(open_prs_query)}
        assigned: {search_query(assigned_query)}
    }}"""
    result = run_graphql_query(query)
    print(report_open_prs(result['data']['open_prs']))
    print(report_assigned(result['data']['assigned']))
