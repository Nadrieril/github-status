import sys, json, subprocess
import base64, argparse
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

def github_api(token, endpoint, json=None):
    headers = {"Authorization": f"Bearer {token}"}
    if json:
        response = requests.post(f"https://api.github.com/{endpoint}", json=json, headers=headers)
    else:
        response = requests.get(f"https://api.github.com/{endpoint}", headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception("Query failed to run by returning code of {}. {}".format(response.status_code, response.text))

def run_graphql_query(token, query):
    return github_api(token, 'graphql', {'query': query})

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
  isDraft
  headRefName
  mergeable
  reviewDecision
  commits(last:1) {
    nodes {
      commit {
        statusCheckRollup {
          state
        }
      }
    }
  }
  latestReviews(last: 1) {
    nodes {
      state
    }
  }
  reviewDecision
  reviewRequests(last: 1) {
    nodes {
      requestedReviewer {
        ... on User {
          login
        }
      }
    }
  }
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

# Used to construct a notification token
MAGIC_BITS = b'\x93\x00\xCE\x00\x67\x82\xa6\xb3'
def report_notifications(rows, user_id, orgs=set()):
    rows.sort(key = lambda row: row['updated_at'])

    table = Table(title="Notifications", box=box.SIMPLE)
    table.add_column("Repo")
    table.add_column("Title")
    table.add_column("Type")
    table.add_column("Reason")
    table.add_column("Updated", style="bright_black")
    table.add_column("Token")

    for row in rows:
        if row['repository']['owner']['login'] not in orgs:
            continue
        url = row['subject']['url'].replace("api.", "").replace("repos/", "").replace("pulls", "pull")
        notification_token = base64.b64encode(MAGIC_BITS + f"{row['id']}:{user_id}".encode()).decode().rstrip('=')
        url = url + f"?notification_referrer_id=NT_{notification_token}"
        table.add_row(
            row['repository']['name'],
            Text(row['subject']['title'], style=Style(link=url)),
            row['subject']['type'],
            row['reason'],
            date_ago(row['updated_at']),
            notification_token,
        )
    return table

def report_open_prs(data):
    rows = data['nodes']
    rows.sort(key = lambda row: row['updatedAt'])

    table = Table(title="Open PRs", box=box.SIMPLE)
    table.add_column("Repo")
    table.add_column("Number")
    table.add_column("CI")
    table.add_column("Review")
    table.add_column("Title")
    table.add_column("Branch", style="cyan")
    table.add_column("Updated", style="bright_black")

    for row in rows:
        ci_status = "✅"
        rolledup_status = row['commits']['nodes'][0]['commit']['statusCheckRollup']['state']
        if row['mergeable'] != 'MERGEABLE':
            ci_status = "❌"
        elif rolledup_status == 'PENDING':
            ci_status = "🟡"
        elif rolledup_status != 'SUCCESS':
            ci_status = "❌"

        review_status = "❔"
        if row['isDraft']:
            review_status = ""
        if row['reviewDecision'] == 'APPROVED':
            review_status = "✅"
        else:
            reviews = row['latestReviews']['nodes']
            if len(reviews) == 0:
                review_requests = row['reviewRequests']['nodes']
                if len(review_requests) != 0:
                    review_status = "🟡"
            else:
                state = reviews[0]['state']
                if state == 'APPROVED':
                    review_status = "✅"
                elif state == 'CHANGES_REQUESTED':
                    review_status = "❌"

        number_color = "white" if row['isDraft'] else "green"
        table.add_row(
            row['repository']['name'],
            Text(f"#{row['number']}", style=number_color),
            ci_status,
            review_status,
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
    table.add_column("Fix", style="blue")
    table.add_column("Labels")
    table.add_column("Updated", style="bright_black")

    for row in rows:
        style = None
        if row['closing_pr'] or row['blocked']:
            style = Style(dim=True)
        labels = row['labels']['nodes']
        labels = ", ".join(l['name'] for l in labels)
        table.add_row(
            row['repository']['name'],
            f"#{row['number']}",
            Text(row['title'], style=Style(link=row['url'])),
            row['closing_pr'],
            labels,
            date_ago(row['updatedAt']),
            style=style,
        )
    return table

if __name__ == "__main__":
    # Retrieve the GitHub login token.
    out = subprocess.run(["gh", "auth", "token"], capture_output=True)
    assert out.returncode == 0
    GITHUB_TOKEN = out.stdout.decode('utf-8').strip()

    parser = argparse.ArgumentParser(prog='github-status')
    parser.add_argument('--org', action='append',
                        help='filter everything for this organization. Can be supplied several times.')
    parser.add_argument('--auto-org', action='store_true',
                        help='use the organization of the current repo as filter.')
    args = parser.parse_args()

    # Filter organizations
    orgs = args.org or []
    if args.auto_org:
        out = subprocess.run(["gh", "repo", "view", "--json", "owner"], capture_output=True)
        if out.returncode == 0:
            json = json.loads(out.stdout)
            orgs.append(json['owner']['login'])

    # Fetch data
    notifications = github_api(GITHUB_TOKEN, 'notifications')

    open_prs_query = "state:open author:@me is:pr"
    assigned_query = "state:open assignee:@me"
    for org in orgs:
        open_prs_query += f" org:{org}"
        assigned_query += f" org:{org}"
    query = FRAGMENT_ISSUE + FRAGMENT_PR + f"""query {{
        user: viewer {{ databaseId }}
        open_prs: {search_query(open_prs_query)}
        assigned: {search_query(assigned_query)}
    }}"""
    graphql_result = run_graphql_query(GITHUB_TOKEN, query)['data']

    # Display
    user_id = graphql_result['user']['databaseId']
    print(report_notifications(notifications, user_id, set(orgs)))
    print(report_open_prs(graphql_result['open_prs']))
    print(report_assigned(graphql_result['assigned']))
