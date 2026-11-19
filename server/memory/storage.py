"""Keep the data folder in a Cloudflare R2 bucket when one is configured, with the local folder as a working copy.

    uv run python -m memory.storage sync
    uv run python -m memory.storage check
"""

import argparse
import os
import sys
from functools import cache
from pathlib import Path

from memory.config import DATA_DIR

SYNCED = ("moments", "places", "glasses", "uploads")


def enabled() -> bool:
    """Say whether an R2 bucket is configured."""
    return all(os.environ.get(name) for name in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                                                  "R2_BUCKET"))


@cache
def bucket():
    """Return the boto3 bucket for R2."""
    import boto3

    s3 = boto3.resource(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )

    return s3.Bucket(os.environ["R2_BUCKET"])


def key(path: Path) -> str:
    """Return the bucket key for a path inside the data folder."""
    return Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(DATA_DIR))).as_posix()


def path_of(name: str) -> Path:
    """Return the local path for a bucket key."""
    return DATA_DIR / name


def listing(prefix: str) -> dict[str, dict]:
    """Return every key under a prefix with its size and when it was last written (seconds since 1970)."""
    if not enabled():
        return {}

    return {item.key: {"size": item.size, "modified": item.last_modified.timestamp()}
            for item in bucket().objects.filter(Prefix=prefix)}


def download(name: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    bucket().download_file(name, str(partial))
    partial.replace(target)


def local(path: Path) -> Path | None:
    """Return the path once the file is present locally, downloading it from R2 if needed, or None if it isn't kept."""
    if path.is_file():
        return path

    if not enabled():
        return None

    try:
        download(key(path), path)
    except Exception as error:
        if missing(error):
            return None
        raise

    return path


def missing(error: Exception) -> bool:
    return getattr(error, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound")


def exists(path: Path) -> bool:
    """Say whether a file is kept, locally or in R2, without downloading it."""
    if path.is_file():
        return True

    if not enabled():
        return False

    try:
        bucket().Object(key(path)).load()
    except Exception as error:
        if missing(error):
            return False
        raise

    return True


def fetch_folder(folder: Path, videos: bool = False) -> None:
    """Download every file of a folder that isn't present locally, leaving out its video unless asked."""
    if not enabled():
        return

    prefix = key(folder) + "/"

    for name in listing(prefix):
        target = path_of(name)

        if target.is_file() or (not videos and target.stem == "video"):
            continue

        download(name, target)


def save(path: Path) -> None:
    """Upload a file to R2."""
    if enabled() and path.is_file():
        bucket().upload_file(str(path), key(path))


def mirror(folder: Path) -> None:
    """Make R2 hold exactly the folder's files: upload new or changed ones and delete ones no longer there."""
    if not enabled():
        return

    prefix = key(folder) + "/"
    remote = listing(prefix)
    files = {key(path): path for path in folder.rglob("*") if path.is_file()}

    for name, path in files.items():
        if remote.get(name, {}).get("size") != path.stat().st_size:
            bucket().upload_file(str(path), name)

    stale = [{"Key": name} for name in remote if name not in files]

    for start in range(0, len(stale), 1000):
        bucket().delete_objects(Delete={"Objects": stale[start:start + 1000]})


def copy(source: Path, target: Path) -> None:
    """Copy a file R2 already holds to another key inside R2, so it needn't be sent again."""
    if not enabled():
        return

    try:
        source_key, target_key = key(source), key(target)
    except ValueError:
        return

    if source_key == target_key:
        return

    try:
        bucket().Object(target_key).copy_from(CopySource={"Bucket": bucket().name, "Key": source_key})
    except Exception as error:
        if not missing(error):
            raise


def size(path: Path) -> int | None:
    """Return a file's size in R2, or None if R2 doesn't hold it."""
    try:
        return bucket().Object(key(path)).content_length
    except Exception as error:
        if missing(error):
            return None
        raise


def delete(path: Path) -> None:
    """Delete one file from R2."""
    if enabled():
        bucket().Object(key(path)).delete()


def download_url(path: Path, media_type: str | None = None, seconds: int = 900) -> str:
    """Return a short-lived link that reads a file straight from R2."""
    params = {"Bucket": bucket().name, "Key": key(path)}

    if media_type:
        params["ResponseContentType"] = media_type

    return bucket().meta.client.generate_presigned_url("get_object", Params=params, ExpiresIn=seconds)


def upload_url(path: Path, seconds: int = 3600) -> str:
    """Return a short-lived link that writes a file straight into R2 with PUT."""
    return bucket().meta.client.generate_presigned_url("put_object", Params={"Bucket": bucket().name, "Key": key(path)},
                                                       ExpiresIn=seconds)


def remove(folder: Path) -> None:
    """Delete a folder's files from R2."""
    if enabled():
        bucket().objects.filter(Prefix=key(folder) + "/").delete()


def local_files() -> dict[str, int]:
    """Return every synced local file's key and size."""
    files = {}

    for top in SYNCED:
        for path in (DATA_DIR / top).rglob("*"):
            if path.is_file() and not path.name.endswith(".part") and not any(".building" in part for part in path.parts):
                files[key(path)] = path.stat().st_size

    return files


def remote_files() -> dict[str, int]:
    return {name: item["size"] for top in SYNCED for name, item in listing(top + "/").items()}


def sync() -> None:
    files, remote = local_files(), remote_files()
    unsent = [name for name, size in files.items() if remote.get(name) != size]

    for number, name in enumerate(unsent, 1):
        bucket().upload_file(str(path_of(name)), name)

        if number % 100 == 0:
            print(f"  {number} of {len(unsent)}…")

    sent = sum(files[name] for name in unsent)
    print(f"Uploaded {len(unsent)} files ({sent / 1e6:.1f} MB). {len(files) - len(unsent)} were already there.")


def check() -> int:
    files, remote = local_files(), remote_files()
    unsent = sorted(name for name, size in files.items() if remote.get(name) != size)
    only_remote = sorted(set(remote) - set(files))

    print(f"Local: {len(files)} files, {sum(files.values()) / 1e6:.1f} MB. "
          f"R2: {len(remote)} files, {sum(remote.values()) / 1e6:.1f} MB.")
    print(f"Missing or different in R2: {len(unsent)}. Only in R2: {len(only_remote)}.")

    for name in unsent[:10]:
        print(f"  not in R2: {name}")

    return 1 if unsent else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy the data folder to R2, compare the two, or put one file in R2.")
    parser.add_argument("command", choices=["sync", "check", "put"])
    parser.add_argument("local", nargs="?", help="put: the file to upload")
    parser.add_argument("name", nargs="?", help="put: its key in the bucket, e.g. firmware/latest.json")
    args = parser.parse_args()

    if not enabled():
        print("R2 isn't configured: set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY and R2_BUCKET in .env.",
              file=sys.stderr)
        return 1

    if args.command == "sync":
        sync()
        return 0

    if args.command == "put":
        if not args.local or not args.name:
            parser.error("put needs a local file and a bucket key")

        bucket().upload_file(args.local, args.name)
        print(f"put {args.local} at {args.name}")
        return 0

    return check()


if __name__ == "__main__":
    sys.exit(main())
