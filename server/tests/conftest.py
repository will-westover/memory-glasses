import boto3
import pytest
from moto import mock_aws

from memory import storage


@pytest.fixture(autouse=True)
def r2(tmp_path, monkeypatch):
    with mock_aws():
        bucket = boto3.resource("s3", region_name="us-east-1").create_bucket(Bucket="memory")
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        monkeypatch.setattr(storage, "bucket", lambda: bucket)
        yield bucket
