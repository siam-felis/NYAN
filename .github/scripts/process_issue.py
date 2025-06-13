from github import Github, GithubException
import os
import re
from typing import Tuple, List
import base64
from datetime import datetime

# TBD read from file
RECOVERY_USERS_SET = {"siam-felis", }
WILDCARD_USERS_SET = RECOVERY_USERS_SET | {}
COMMIT_USERS_SET = WILDCARD_USERS_SET | {}
BAN_USERS_SET = {}

STRICT_DOMAIN_PATTERN = re.compile(
    r"(?:^|\s)domain:\s*((\*\.)?[a-zA-Z0-9-]{1,63}(\.[a-zA-Z0-9-]{1,63})+)", re.IGNORECASE
)


def is_wildcard(domain: str) -> bool:
    return domain.startswith("*.")


def validate_issue(title: str, body: str, user_login: str) -> Tuple[bool, str, str, List[str]]:
    if (user_login in BAN_USERS_SET):
        return False, "許可されていないユーザーです。", "", []

    title_lower = title.strip().lower()
    if title_lower not in ["[abuse]", "[recovery]"]:
        return False, "タイトルは [abuse] または [recovery] のみ許可されています。", "", []

    action_type = "rpz" if title_lower == "[abuse]" else "white"

    if title_lower == "[recovery]" and not (user_login in RECOVERY_USERS_SET):
        return False, "[recovery] は許可されたユーザーのみ利用可能です。", "", []

    domains = [match[0] for match in STRICT_DOMAIN_PATTERN.findall(body)]
    if not domains:
        return False, "本文に 'domain: example.com' 形式で有効なドメインを記載してください。", "", []

    # ワイルドカードの制限（許可ユーザー以外は不可）
    for domain in domains:
        if is_wildcard(domain) and not (user_login in WILDCARD_USERS_SET):
            return False, f"ワイルドカードドメイン '{domain}' は許可ユーザーのみ利用可能です。", "", []

    return True, f"{len(domains)} 件のドメインが検出されました。", action_type, domains


def add_domains_with_date_block(existing_lines: List[str], new_domains: List[str], is_rpz: bool) -> List[str]:
    today_str = datetime.utcnow().date().strftime("%Y-%m-%d")
    date_header = f"# added: {today_str}"
    new_lines = [f"{d} CNAME ." if is_rpz else d for d in new_domains]

    # すでに今日のブロックが存在する場合はヘッダーを追加しない
    if date_header in existing_lines:
        return existing_lines + new_lines
    else:
        return existing_lines + [date_header] + new_lines


def clean_expired_blocks(lines: List[str], max_age_days=30) -> List[str]:
    result = []
    block_date = None
    buffer = []

    for line in lines:
        match = re.match(r"# added: (\d{4}-\d{2}-\d{2})", line)
        if match:
            if block_date:
                if (datetime.utcnow().date() - block_date).days <= max_age_days:
                    result.extend(buffer)
            block_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
            buffer = [line]
        else:
            buffer.append(line)

    if block_date and (datetime.utcnow().date() - block_date).days <= max_age_days:
        result.extend(buffer)

    return result


def remove_domains_from_lines(lines: List[str], domains_to_remove: List[str]) -> List[str]:
    normalized = set(d.lower() for d in domains_to_remove)
    return [line for line in lines if not any(d in line.lower() for d in normalized if d)]


def update_file_in_repo(repo, file_path: str, new_domains: List[str], is_rpz: bool, remove_domains: List[str], issue_number: int):
    contents = repo.get_contents(file_path)
    existing_text = base64.b64decode(contents.content).decode("utf-8")
    lines = existing_text.strip().split("\n")

    # クリーンアップと削除
    lines = clean_expired_blocks(lines)
    if remove_domains:
        lines = remove_domains_from_lines(lines, remove_domains)

    # 新規ドメイン追加
    lines = add_domains_with_date_block(lines, new_domains, is_rpz)

    # コミット実行
    updated_text = "\n".join(lines) + "\n"
    repo.update_file(
        path=file_path,
        message=f"Update {file_path} from Issue #{issue_number}",
        content=updated_text,
        sha=contents.sha
    )


def main():
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise EnvironmentError("GITHUB_TOKEN not found in environment variables")
    repo_name = os.getenv("REPO_NAME")
    if not repo_name:
        raise EnvironmentError("REPO_NAME not found in environment variables")
    github_instance = Github(token)

    repo = github_instance.get_repo(repo_name)
    issues = repo.get_issues(state='open')
    for issue in issues:
        print(f"Processing issue #{issue.number} by {issue.user.login}")
        if issue.pull_request is not None:
            continue

        valid, message, action_type, domains = validate_issue(
            issue.title,
            issue.body,
            issue.user.login
        )

        if valid:
            try:
                if action_type == "white":
                    # まず nyan.white を更新
                    update_file_in_repo(
                        repo, "nyan.white", domains, is_rpz=False,
                        remove_domains=[], issue_number=issue.number
                    )
                    # 次に nyan.rpz から該当ドメインを除外
                    update_file_in_repo(
                        repo, "nany.rpz", [], is_rpz=True,
                        remove_domains=domains, issue_number=issue.number
                    )
                else:
                    # nany.rpz に追加（nyan.white の除去対象として使う）
                    update_file_in_repo(
                        repo, "nany.rpz", domains, is_rpz=True,
                        remove_domains=[], issue_number=issue.number
                    )
                issue.create_comment(f"✅ {len(domains)} 件のドメインを `{action_type}` に反映しました。")
                issue.edit(state="closed")
            except Exception as e:
                issue.create_comment(f"❌ ファイル更新エラー: {e}")
        else:
            issue.create_comment(f"❌ バリデーションエラー: {message}")
            issue.edit(state="closed")

if __name__ == "__main__":
    main()
