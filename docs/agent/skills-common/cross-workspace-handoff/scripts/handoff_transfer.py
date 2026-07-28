#!/usr/bin/env python3
"""Send and receive validated handoff bundles through a configured share."""

import argparse
import filecmp
import json
import os
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from handoff_bundle import fail, unpack, validate_bundle, validate_id, validate_unpacked


def project_root() -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return Path(result.stdout.strip()).resolve() if result.returncode == 0 else None


def workspace_root(config_path: Path) -> Path:
    config_path = config_path.resolve(strict=True)
    if (
        config_path.name != ".agent-env.json"
        or config_path.parent.name.casefold() != "agent"
        or config_path.parent.parent.name.casefold() != "docs"
    ):
        fail("config must be <workspace>/docs/agent/.agent-env.json")
    return config_path.parents[2]


def find_config(explicit: Path | None) -> Path:
    if explicit:
        explicit = explicit.resolve(strict=True)
        workspace_root(explicit)
        return explicit
    roots = [Path.cwd(), *Path.cwd().parents]
    if root := project_root():
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if common.returncode == 0:
            roots.insert(0, Path(common.stdout.strip()).resolve().parent)
        roots.insert(1, root)
    for root in dict.fromkeys(roots):
        candidate = root / "docs" / "agent" / ".agent-env.json"
        if candidate.is_file():
            return candidate.resolve()
    fail("cannot find docs/agent/.agent-env.json; pass --config")


def load_config(path: Path) -> tuple[str, dict[str, object]]:
    workspace_root(path)
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        handoff = config["crossWorkspaceHandoff"]
        workspace = handoff["workspaceId"]
        share = handoff["share"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        fail(f"invalid crossWorkspaceHandoff config: {error}")
    if not isinstance(workspace, str) or not isinstance(share, dict):
        fail("workspaceId must be a string and share must be an object")
    validate_id(workspace, "workspaceId")
    if workspace == "public":
        fail("'public' is reserved and cannot be a workspaceId")
    if share.get("kind") not in {"local", "sftp"}:
        fail("share.kind must be local or sftp")
    if not isinstance(share.get("root"), str):
        fail("share.root must be a string")
    return workspace, share


def quote_sftp(value: str) -> str:
    if "\n" in value or "\r" in value or "\0" in value:
        fail("SFTP paths cannot contain line breaks or NUL")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def remote_root(share: dict[str, object]) -> PurePosixPath:
    value = share.get("root")
    if not isinstance(value, str):
        fail("share.root must be a string")
    root = PurePosixPath(value)
    if not root.is_absolute() or ".." in root.parts:
        fail("SFTP share.root must be an absolute path without '..'")
    return root


def local_root(share: dict[str, object]) -> Path:
    value = os.path.expandvars(os.path.expanduser(str(share["root"])))
    root = Path(value)
    if not root.is_absolute():
        fail("local share.root must be an absolute path")
    return root.resolve(strict=True)


def pinned_host_keys(share: dict[str, object]) -> list[str] | None:
    expected = share.get("hostKeyFingerprint")
    if expected is None:
        return None
    if not isinstance(expected, str) or not expected.startswith("SHA256:"):
        fail("hostKeyFingerprint must use the SHA256:<fingerprint> form")
    host = share.get("host")
    port = share.get("port", 22)
    if not isinstance(host, str) or not isinstance(port, int):
        fail("SFTP host must be a string and port must be an integer")
    if not 1 <= port <= 65535:
        fail("SFTP port must be between 1 and 65535")
    token = host if port == 22 else f"[{host}]:{port}"
    found = subprocess.run(
        ["ssh-keygen", "-F", token],
        capture_output=True,
        text=True,
        check=False,
    )
    matching: list[str] = []
    for line in found.stdout.splitlines():
        if not line or line.startswith("#"):
            continue
        result = subprocess.run(
            ["ssh-keygen", "-lf", "-"],
            input=line + "\n",
            capture_output=True,
            text=True,
            check=False,
        )
        fingerprints = {
            word for word in result.stdout.split() if word.startswith("SHA256:")
        }
        if expected in fingerprints:
            matching.append(line)
    if not matching:
        fail("trusted host key is missing or does not match hostKeyFingerprint")
    return matching


def sftp(
    share: dict[str, object], commands: list[str]
) -> subprocess.CompletedProcess[str]:
    required = ("host", "user", "identityFile")
    if any(not isinstance(share.get(key), str) for key in required):
        fail("SFTP share requires string host, user, and identityFile")
    port = share.get("port", 22)
    if not isinstance(port, int):
        fail("SFTP port must be an integer")
    if not 1 <= port <= 65535:
        fail("SFTP port must be between 1 and 65535")
    identity = Path(
        os.path.expandvars(os.path.expanduser(str(share["identityFile"])))
    ).resolve(strict=True)
    pinned = pinned_host_keys(share)
    executable = shutil.which("sftp")
    if not executable:
        fail("sftp executable not found")
    with tempfile.TemporaryDirectory() as directory:
        host_key_args: list[str] = []
        if pinned:
            known_hosts = Path(directory) / "known_hosts"
            known_hosts.write_text("\n".join(pinned) + "\n", encoding="utf-8")
            host_key_args = [
                f"-oUserKnownHostsFile={known_hosts}",
                f"-oGlobalKnownHostsFile={os.devnull}",
            ]
        args = [
            executable,
            "-q",
            "-b",
            "-",
            "-P",
            str(port),
            "-i",
            str(identity),
            "-oBatchMode=yes",
            "-oConnectTimeout=15",
            "-oIdentitiesOnly=yes",
            "-oStrictHostKeyChecking=yes",
            *host_key_args,
            f"{share['user']}@{share['host']}",
        ]
        return subprocess.run(
            args,
            input="\n".join(commands) + "\n",
            capture_output=True,
            text=True,
            check=False,
        )


def require_sftp(result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        fail(f"SFTP transfer failed: {detail}")


def remote_message_path(
    share: dict[str, object], mailbox: str, message_id: str
) -> PurePosixPath:
    root = remote_root(share)
    return (
        root / "public" / f"{message_id}.zip"
        if mailbox == "public"
        else root / "inbox" / mailbox / f"{message_id}.zip"
    )


def remote_bundle_status(
    share: dict[str, object], source: PurePosixPath, bundle: Path
) -> bool | None:
    with tempfile.TemporaryDirectory() as directory:
        probe = Path(directory) / "bundle.zip"
        result = sftp(
            share,
            [f"-get {quote_sftp(source.as_posix())} {quote_sftp(probe.as_posix())}"],
        )
        require_sftp(result)
        return filecmp.cmp(bundle, probe, shallow=False) if probe.exists() else None


def upload(
    share: dict[str, object], bundle: Path, mailbox: str, message_id: str
) -> None:
    if share["kind"] == "local":
        root = local_root(share)
        destination = (
            root / "public" / f"{message_id}.zip"
            if mailbox == "public"
            else root / "inbox" / mailbox / f"{message_id}.zip"
        )
        staging = (
            root
            / ".staging"
            / f"{message_id}.{secrets.token_hex(4)}.zip.part"
        )
        if destination.exists():
            if filecmp.cmp(bundle, destination, shallow=False):
                return
            fail(f"message ID already exists with different content: {destination}")
        if not destination.parent.is_dir() or not staging.parent.is_dir():
            fail("target inbox/public or .staging directory does not exist")
        try:
            shutil.copyfile(bundle, staging)
            if destination.exists():
                if not filecmp.cmp(bundle, destination, shallow=False):
                    fail(
                        f"message ID already exists with different content: {destination}"
                    )
            else:
                try:
                    staging.rename(destination)
                except OSError:
                    if not destination.exists() or not filecmp.cmp(
                        bundle, destination, shallow=False
                    ):
                        raise
        finally:
            staging.unlink(missing_ok=True)
        return

    destination = remote_message_path(share, mailbox, message_id)
    staging = (
        remote_root(share)
        / ".staging"
        / f"{message_id}.{secrets.token_hex(4)}.zip.part"
    )
    existing = remote_bundle_status(share, destination, bundle)
    if existing is True:
        return
    if existing is False:
        fail("message ID already exists remotely with different content")
    result = sftp(
        share,
        [
            f"put {quote_sftp(bundle.resolve().as_posix())} "
            f"{quote_sftp(staging.as_posix())}",
            f"rename {quote_sftp(staging.as_posix())} "
            f"{quote_sftp(destination.as_posix())}",
        ],
    )
    if result.returncode:
        sftp(share, [f"-rm {quote_sftp(staging.as_posix())}"])
        existing = remote_bundle_status(share, destination, bundle)
        if existing is True:
            return
        if existing is False:
            fail("message ID already exists remotely with different content")
        require_sftp(result)


def download(
    share: dict[str, object], mailbox: str, message_id: str, destination: Path
) -> None:
    if share["kind"] == "local":
        root = local_root(share)
        source = (
            root / "public" / f"{message_id}.zip"
            if mailbox == "public"
            else root / "inbox" / mailbox / f"{message_id}.zip"
        )
        shutil.copyfile(source, destination)
    else:
        source = remote_message_path(share, mailbox, message_id)
        result = sftp(
            share,
            [f"get {quote_sftp(source.as_posix())} {quote_sftp(destination.as_posix())}"],
        )
        require_sftp(result)


def cache_root(config_path: Path) -> Path:
    return workspace_root(config_path) / ".temp" / "cross-workspace-handoff"


def send(config_path: Path, target: str, bundle: Path) -> None:
    workspace, share = load_config(config_path)
    validate_id(target, "target")
    bundle = bundle.resolve(strict=True)
    if bundle.suffix.casefold() != ".zip":
        fail("bundle must be named <Message-ID>.zip")
    message_id = bundle.stem
    validate_id(message_id, "Message-ID")
    validate_bundle(bundle, message_id=message_id, sender=workspace, target=target)
    upload(share, bundle, target, message_id)
    print(f"delivered: [{target}] {message_id}")


def receive(config_path: Path, message_id: str, public: bool) -> None:
    workspace, share = load_config(config_path)
    validate_id(message_id, "Message-ID")
    mailbox = "public" if public else workspace
    root = cache_root(config_path) / "inbox"
    if public:
        root /= "public"
    root.mkdir(parents=True, exist_ok=True)
    bundle = root / f"{message_id}.zip"
    if not bundle.exists():
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{message_id}.", suffix=".zip.part", dir=root
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        temporary.unlink()
        try:
            download(share, mailbox, message_id, temporary)
            validate_bundle(
                temporary,
                message_id=message_id,
                target="public" if public else workspace,
            )
            if bundle.exists():
                if not filecmp.cmp(temporary, bundle, shallow=False):
                    fail("cached message ID has different content")
            else:
                try:
                    temporary.replace(bundle)
                except OSError:
                    if not bundle.exists() or not filecmp.cmp(
                        temporary, bundle, shallow=False
                    ):
                        raise
        finally:
            temporary.unlink(missing_ok=True)
    metadata = validate_bundle(
        bundle,
        message_id=message_id,
        target="public" if public else workspace,
    )
    message = root / message_id
    if not message.exists():
        try:
            unpack(bundle, message, announce=False)
        except OSError:
            if not message.exists():
                raise
    validate_unpacked(bundle, message)
    print(f"received: [{mailbox}] {message_id}, from {metadata['From']}")
    print(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    send_parser = commands.add_parser("send", help="validate and send a bundle")
    send_parser.add_argument("target")
    send_parser.add_argument("bundle", type=Path)
    receive_parser = commands.add_parser(
        "receive", help="download, validate, and unpack a bundle"
    )
    receive_parser.add_argument("message_id")
    receive_parser.add_argument("--public", action="store_true")
    args = parser.parse_args()
    config = find_config(args.config)
    if args.command == "send":
        send(config, args.target, args.bundle)
    else:
        receive(config, args.message_id, args.public)


if __name__ == "__main__":
    main()
