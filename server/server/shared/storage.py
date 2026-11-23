"""Keep the data folder in a Cloudflare R2 bucket, with the local folder as a working copy.

uv run python -m server.shared.storage sync
uv run python -m server.shared.storage check
"""

import argparse
import os
import sys
from functools import cache
from pathlib import Path

from server.shared.config import DATA_DIR, require_env

SYNCED_FOLDERS = ("moments", "places", "glasses", "uploads", "evals")


@cache
def bucket():
    """Return the boto3 bucket for R2."""
    import boto3

    s3 = boto3.resource(
        "s3",
        endpoint_url=f"https://{require_env('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
        aws_access_key_id=require_env("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=require_env("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
    )

    return s3.Bucket(require_env("R2_BUCKET"))


def bucket_key(path: Path) -> str:
    """Return the bucket key for a path inside the data folder."""
    return Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(DATA_DIR))).as_posix()


def local_path(name: str) -> Path:
    """Return the local path for a bucket key."""
    return DATA_DIR / name


def list_keys(prefix: str) -> dict[str, dict]:
    """Return every key under a prefix with its size and when it was last written (seconds since 1970)."""
    return {
        item.key: {"size": item.size, "modified": item.last_modified.timestamp()}
        for item in bucket().objects.filter(Prefix=prefix)
    }


def download(name: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    bucket().download_file(name, str(partial))
    partial.replace(target)


def local_copy(path: Path) -> Path | None:
    """Return the path once the file is present locally, downloading it from R2 if needed, or None if it isn't kept."""
    if path.is_file():
        return path

    try:
        download(bucket_key(path), path)
    except Exception as error:
        if is_not_found(error):
            return None
        raise

    return path


def is_not_found(error: Exception) -> bool:
    return getattr(error, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound")


def read_range(path: Path, start: int, end: int) -> bytes:
    """Return bytes start to end (inclusive) of a kept file, from the local copy if there is one, else from R2."""
    if path.is_file():
        with open(path, "rb") as file:
            file.seek(start)
            return file.read(end - start + 1)

    response = bucket().meta.client.get_object(Bucket=bucket().name, Key=bucket_key(path), Range=f"bytes={start}-{end}")

    return response["Body"].read()


def exists(path: Path) -> bool:
    """Say whether a file is kept, locally or in R2, without downloading it."""
    if path.is_file():
        return True

    try:
        bucket().Object(bucket_key(path)).load()
    except Exception as error:
        if is_not_found(error):
            return False
        raise

    return True


def fetch_folder(folder: Path, videos: bool = False) -> None:
    """Download every file of a folder that isn't present locally, leaving out its video unless asked."""
    prefix = bucket_key(folder) + "/"

    for name in list_keys(prefix):
        target = local_path(name)

        if target.is_file() or (not videos and target.stem == "video"):
            continue

        download(name, target)


def save(path: Path) -> None:
    """Upload a file to R2."""
    if path.is_file():
        bucket().upload_file(str(path), bucket_key(path))


def mirror(folder: Path) -> None:
    """Make R2 hold exactly the folder's files: upload new or changed ones and delete ones no longer there."""
    prefix = bucket_key(folder) + "/"
    remote = list_keys(prefix)
    files = {bucket_key(path): path for path in folder.rglob("*") if path.is_file()}

    for name, path in files.items():
        if remote.get(name, {}).get("size") != path.stat().st_size:
            bucket().upload_file(str(path), name)

    stale = [{"Key": name} for name in remote if name not in files]

    for start in range(0, len(stale), 1000):
        bucket().delete_objects(Delete={"Objects": stale[start : start + 1000]})


def copy(source: Path, target: Path) -> None:
    """Copy a file R2 already holds to another key inside R2, so it needn't be sent again."""
    try:
        source_key, target_key = bucket_key(source), bucket_key(target)
    except ValueError:
        return

    if source_key == target_key:
        return

    try:
        bucket().Object(target_key).copy_from(CopySource={"Bucket": bucket().name, "Key": source_key})
    except Exception as error:
        if not is_not_found(error):
            raise


def stored_size(path: Path) -> int | None:
    """Return a file's size in R2, or None if R2 doesn't hold it."""
    try:
        return bucket().Object(bucket_key(path)).content_length
    except Exception as error:
        if is_not_found(error):
            return None
        raise


def delete_file(path: Path) -> None:
    """Delete one file from R2."""
    bucket().Object(bucket_key(path)).delete()


def download_url(path: Path, media_type: str | None = None, seconds: int = 900) -> str:
    """Return a short-lived link that reads a file straight from R2."""
    params = {"Bucket": bucket().name, "Key": bucket_key(path)}

    if media_type:
        params["ResponseContentType"] = media_type

    return bucket().meta.client.generate_presigned_url("get_object", Params=params, ExpiresIn=seconds)


def upload_url(path: Path, seconds: int = 3600) -> str:
    """Return a short-lived link that writes a file straight into R2 with PUT."""
    return bucket().meta.client.generate_presigned_url(
        "put_object", Params={"Bucket": bucket().name, "Key": bucket_key(path)}, ExpiresIn=seconds
    )


def delete_folder(folder: Path) -> None:
    """Delete a folder's files from R2."""
    bucket().objects.filter(Prefix=bucket_key(folder) + "/").delete()


def local_files() -> dict[str, int]:
    """Return every synced local file's key and size."""
    files = {}

    for top in SYNCED_FOLDERS:
        for path in (DATA_DIR / top).rglob("*"):
            if (
                path.is_file()
                and not path.name.endswith(".part")
                and not any(".building" in part for part in path.parts)
            ):
                files[bucket_key(path)] = path.stat().st_size

    return files


def remote_files() -> dict[str, int]:
    return {name: item["size"] for top in SYNCED_FOLDERS for name, item in list_keys(top + "/").items()}


def sync() -> None:
    files, remote = local_files(), remote_files()
    unsent = [name for name, size in files.items() if remote.get(name) != size]

    for number, name in enumerate(unsent, 1):
        bucket().upload_file(str(local_path(name)), name)

        if number % 100 == 0:
            print(f"  {number} of {len(unsent)}…")

    sent = sum(files[name] for name in unsent)
    print(f"Uploaded {len(unsent)} files ({sent / 1e6:.1f} MB). {len(files) - len(unsent)} were already there.")


def check() -> int:
    files, remote = local_files(), remote_files()
    unsent = sorted(name for name, size in files.items() if remote.get(name) != size)
    only_remote = sorted(set(remote) - set(files))

    print(
        f"Local: {len(files)} files, {sum(files.values()) / 1e6:.1f} MB. "
        f"R2: {len(remote)} files, {sum(remote.values()) / 1e6:.1f} MB."
    )
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
