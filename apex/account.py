import copy
import json
import logging
import os
from getpass import getpass
from pathlib import Path
from typing import Dict, Optional


BOHRIUM_WORKFLOWS_HOST = "https://workflows.deepmodeling.com"
DEFAULT_BOHRIUM_CONFIG = {
    "dflow_host": BOHRIUM_WORKFLOWS_HOST,
    "k8s_api_server": BOHRIUM_WORKFLOWS_HOST,
    "batch_type": "Bohrium",
    "context_type": "Bohrium",
    "apex_image_name": "registry.dp.tech/dptech/dp/native/prod-397637/apex:1.3.0",
}
SENSITIVE_KEYS = {"password"}
ACCOUNT_FILE_ENV = "APEX_ACCOUNT_FILE"


def _deep_update(base: dict, updates: Optional[dict]) -> None:
    if not updates:
        return
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value


def get_account_config_path(path: Optional[os.PathLike] = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    env_path = os.environ.get(ACCOUNT_FILE_ENV)
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / ".apex" / "account.json"


def load_account_config(path: Optional[os.PathLike] = None) -> dict:
    account_path = get_account_config_path(path)
    if not account_path.is_file():
        return {}
    try:
        with account_path.open("r", encoding="utf-8") as fp:
            return json.load(fp)
    except json.JSONDecodeError:
        logging.warning(
            "Failed to parse account config file %s, ignoring it.",
            account_path
        )
        return {}


def save_account_config(config: dict, path: Optional[os.PathLike] = None) -> Path:
    account_path = get_account_config_path(path)
    account_path.parent.mkdir(parents=True, exist_ok=True)
    with account_path.open("w", encoding="utf-8") as fp:
        json.dump(config, fp, indent=2)
        fp.write("\n")
    if os.name != "nt":
        try:
            os.chmod(account_path.parent, 0o700)
            os.chmod(account_path, 0o600)
        except PermissionError:
            pass
    return account_path


def remove_account_config(path: Optional[os.PathLike] = None) -> bool:
    account_path = get_account_config_path(path)
    if account_path.exists():
        account_path.unlink()
        return True
    return False


def mask_sensitive_config(config: dict) -> dict:
    masked = copy.deepcopy(config)
    for key in SENSITIVE_KEYS:
        if masked.get(key):
            masked[key] = "******"
    return masked


def _is_bohrium_context(config_dict: dict) -> bool:
    for key in ("context_type", "batch_type"):
        value = config_dict.get(key)
        if isinstance(value, str) and "bohrium" in value.lower():
            return True
    machine = config_dict.get("machine", {})
    if isinstance(machine, dict):
        for key in ("context_type", "batch_type"):
            value = machine.get(key)
            if isinstance(value, str) and "bohrium" in value.lower():
                return True
        remote_profile = machine.get("remote_profile", {})
        if isinstance(remote_profile, dict) and any(
                key in remote_profile for key in ("email", "password", "program_id")):
            return True
    dflow_config = config_dict.get("dflow_config", {})
    if isinstance(dflow_config, dict):
        if dflow_config.get("host") == BOHRIUM_WORKFLOWS_HOST:
            return True
    return any(
        key in config_dict for key in ("email", "password", "program_id", "phone", "bohrium_config")
    ) or config_dict.get("dflow_host") == BOHRIUM_WORKFLOWS_HOST


def _is_explicit_non_bohrium_context(config_dict: dict) -> bool:
    context_values = []
    for key in ("context_type", "batch_type"):
        value = config_dict.get(key)
        if isinstance(value, str) and value.strip():
            context_values.append(value.lower())
    machine = config_dict.get("machine", {})
    if isinstance(machine, dict):
        for key in ("context_type", "batch_type"):
            value = machine.get(key)
            if isinstance(value, str) and value.strip():
                context_values.append(value.lower())
    if not context_values:
        return False
    return all("bohrium" not in value for value in context_values)


def should_apply_bohrium_defaults(
        config_dict: dict,
        config_file: Optional[os.PathLike],
        account_config: dict
) -> bool:
    if _is_explicit_non_bohrium_context(config_dict):
        return False
    if _is_bohrium_context(config_dict):
        return True
    config_name = str(config_file).lower() if config_file else ""
    if "bohrium" in config_name:
        return True
    return bool(account_config)


def merge_bohrium_defaults(
        config_dict: Optional[dict],
        config_file: Optional[os.PathLike] = None
) -> dict:
    user_config = copy.deepcopy(config_dict or {})
    account_config = load_account_config()
    if not should_apply_bohrium_defaults(user_config, config_file, account_config):
        return user_config

    merged = copy.deepcopy(DEFAULT_BOHRIUM_CONFIG)
    _deep_update(merged, account_config)
    _deep_update(merged, user_config)
    missing_required = [
        key for key in ("email", "password", "program_id")
        if merged.get(key) in (None, "")
    ]
    if missing_required:
        logging.warning(
            "Missing Bohrium account fields: %s. Run `apex account` or provide them in %s.",
            ", ".join(missing_required),
            config_file or "the config file"
        )
    return merged


def _prompt_value(prompt: str, default: Optional[str]) -> Optional[str]:
    default_text = f" [{default}]" if default not in (None, "") else ""
    value = input(f"{prompt}{default_text}: ").strip()
    if value:
        return value
    return default


def _prompt_program_id(default: Optional[int]) -> Optional[int]:
    while True:
        raw = _prompt_value("Bohrium program_id", str(default) if default is not None else None)
        if raw in (None, ""):
            return None
        try:
            return int(raw)
        except ValueError:
            print("program_id must be an integer.")


def prompt_for_account_fields(current_config: dict) -> dict:
    updated = {}
    email = _prompt_value("Bohrium email", current_config.get("email"))
    if email is not None:
        updated["email"] = email
    password = getpass("Bohrium password [leave empty to keep current]: ").strip()
    if password:
        updated["password"] = password
    elif current_config.get("password"):
        updated["password"] = current_config["password"]
    program_id = _prompt_program_id(current_config.get("program_id"))
    if program_id is not None:
        updated["program_id"] = program_id
    return updated


def account_from_args(args) -> None:
    account_path = get_account_config_path(args.file)
    if args.reset:
        if remove_account_config(account_path):
            print(f"Removed account config: {account_path}")
        else:
            print(f"No account config found: {account_path}")
        return

    current_raw = load_account_config(account_path)
    current = copy.deepcopy(DEFAULT_BOHRIUM_CONFIG)
    _deep_update(current, current_raw)

    cli_updates = {}
    for key in (
            "dflow_host",
            "k8s_api_server",
            "batch_type",
            "context_type",
            "email",
            "password",
            "program_id",
            "apex_image_name",
    ):
        value = getattr(args, key)
        if value is not None:
            cli_updates[key] = value

    if not cli_updates and not args.show and not args.non_interactive:
        cli_updates = prompt_for_account_fields(current)

    if cli_updates:
        _deep_update(current, cli_updates)
        saved_path = save_account_config(current, account_path)
        print(f"Saved account config to {saved_path}")

    if args.show or not cli_updates:
        print(json.dumps(mask_sensitive_config(current), indent=2))
        print(f"Config path: {account_path}")
