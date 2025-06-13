from github import Github
import os
import re
from typing import Tuple, List
import base64
from datetime import datetime, timedelta

# TBD read from file
RECOVERY_USERS_SET = {"siam-felis", }
WILDCARD_USERS_SET = RECOVERY_USERS_SET | {"siam-felis", }
COMMIT_USERS_SET = WILDCARD_USERS_SET | {"siam-felis", }
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


def extract_existing_domains(lines: List[str]) -> set:
    domain_pattern = re.compile(r"^(?!#)(\*\.)?[a-zA-Z0-9.-]+")
    return {line.strip().split()[0] for line in lines if domain_pattern.match(line.strip())}


def add_domains_with_result(existing_lines: List[str], new_domains: List[str], is_rpz: bool, is_privileged: bool) -> Tuple[List[str], List[str]]:
    today_str = datetime.utcnow().date().strftime("%Y-%m-%d")
    date_header = f"# added: {today_str}"
    updated_lines = existing_lines[:]
    result_messages = []
    existing_domains = extract_existing_domains(existing_lines)

    if date_header not in updated_lines:
        updated_lines.append(date_header)

    for domain in new_domains:
        if is_wildcard(domain) and not is_privileged:
            result_messages.append(f"\u274c `{domain}`: 権限が無いため SKIP")
            continue
        if domain in existing_domains:
            result_messages.append(f"\u26a0\ufe0f `{domain}`: 重複のため SKIP")
            continue
        line = f"{domain} CNAME ." if is_rpz else domain
        updated_lines.append(line)
        result_messages.append(f"\u2705 `{domain}`: 更新成功")

    return updated_lines, result_messages


def remove_old_entries(lines: List[str], max_age_days: int = 30) -> List[str]:
    result = []
    current_block_date = None
    cutoff_date = datetime.utcnow().date() - timedelta(days=max_age_days)
    buffer = []

    for line in lines:
        line = line.strip()
        if line.startswith("# added: "):
            if buffer and current_block_date and current_block_date >= cutoff_date:
                result.extend([f"# added: {current_block_date.strftime('%Y-%m-%d')}"] + buffer)
            buffer = []
            date_str = line.replace("# added: ", "").strip()
            try:
                current_block_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                current_block_date = None
        else:
            buffer.append(line)

    if buffer and current_block_date and current_block_date >= cutoff_date:
        result.extend([f"# added: {current_block_date.strftime('%Y-%m-%d')}"] + buffer)

    return result


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

        valid, message, action_type, domains = validate_issue(issue.title, issue.body, issue.user.login)
        if not valid:
            issue.create_comment(f"\u2753 バリデーションエラー: {message}")
            issue.edit(state="closed")
            continue

        try:
            is_rpz = (action_type == "rpz")
            rpz_path = "nyan.rpz"
            white_path = "nyan.white"
            target_path = rpz_path if is_rpz else white_path

            contents = repo.get_contents(target_path)
            existing_lines = base64.b64decode(contents.content).decode("utf-8").splitlines()
            updated_lines, result_messages = add_domains_with_result(
                existing_lines,
                domains,
                is_rpz=is_rpz,
                is_privileged=(issue.user.login in WILDCARD_USERS_SET)
            )
            cleaned_lines = remove_old_entries(updated_lines)

            if cleaned_lines != existing_lines:
                repo.update_file(
                    path=target_path,
                    message=f"Update {target_path} from Issue #{issue.number}",
                    content="\n".join(cleaned_lines) + "\n",
                    sha=contents.sha
                )

            # recovery の場合は rpz も更新する
            if not is_rpz:
                rpz_contents = repo.get_contents(rpz_path)
                rpz_lines = base64.b64decode(rpz_contents.content).decode("utf-8").splitlines()
                white_domains = extract_existing_domains(cleaned_lines)
                rpz_filtered = [line for line in rpz_lines if line.strip().split()[0] not in white_domains and not line.startswith("# added:") or re.match("# added: \\d{4}-\\d{2}-\\d{2}", line)]
                rpz_cleaned = remove_old_entries(rpz_filtered)
                repo.update_file(
                    path=rpz_path,
                    message=f"Auto sync {rpz_path} after white update from Issue #{issue.number}",
                    content="\n".join(rpz_cleaned) + "\n",
                    sha=rpz_contents.sha
                )

            issue.create_comment("\n".join(result_messages))
            issue.edit(state="closed")

        except Exception as e:
            issue.create_comment(f"\u274c ファイル更新中にエラー: {e}")
            issue.edit(state="closed")


if __name__ == "__main__":
    main()
