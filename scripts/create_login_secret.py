"""Generate login config interactively; output hashes only, never the password."""
import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.authentication import make_config


def main():
    username = input("登录账号 [judge]：").strip() or "judge"
    password = getpass.getpass("设置密码（至少 12 位）：")
    if password != getpass.getpass("再次输入密码："):
        raise SystemExit("两次输入不一致")
    config = make_config(username, password)
    print(f'LOGIN_USERNAME = {json.dumps(config.username)}')
    print(f'LOGIN_SALT = "{config.salt.hex()}"')
    print(f'LOGIN_PASSWORD_HASH = "{config.digest.hex()}"')


if __name__ == "__main__":
    main()
