from github import Github
import os

def validate_issue(issue_body, user):
    """ここでスパムチェックやフォーマット検証を行う"""
    if "domain:" not in issue_body:
        return False, "Missing 'domain:' field."
    # 他のバリデーションを追加可能
    return True, ""

def append_to_rpz(domain):
    with open("nyan.rpz", "a") as f:
        f.write(f"{domain.strip()}. IN CNAME .\n")

def main():
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise EnvironmentError("GITHUB_TOKEN not found in environment variables")
    repo_name = os.getenv("REPO_NAME")
    if not repo_name:
        raise EnvironmentError("REPO_NAME not found in environment variables")
    github_instance = Github(token)

    try:
        repo = github_instance.get_repo(repo_name)
        issue.create_comment("Automated Comment")
        issues = repo.get_issues(state='open')
        for issue in issues:
            if issue.pull_request is not None:
                continue  # Ignore PRs
    
            valid, message = validate_issue(issue.body, issue.user.login)
    
            if valid:
                # domainを抽出してリストに追加
                domain = issue.body.split("domain:")[1].strip()
                append_to_rpz(domain)
    
                # コミット & プッシュ（実際にはGitHub ActionsでPR作成でも可）
                with open("nyan.rpz", "r") as f:
                    content = f.read()
    
                repo.update_file("nyan.rpz", f"Add {domain} from issue #{issue.number}", content, repo.get_contents("nyan.rpz").sha)
    
                issue.create_comment(f"? Domain `{domain}` added to `nyan.rpz`.")
                issue.edit(state="closed")
            else:
                issue.create_comment(f"? Issue format invalid: {message}")
                issue.edit(state="closed")
    except github.GithubException as e:
        print(f"Failed to create issue comment: {e}")
        exit(1)

if __name__ == "__main__":
    main()
