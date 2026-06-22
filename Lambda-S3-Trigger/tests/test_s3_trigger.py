"""Unit tests for the S3 trigger Lambda handler."""

import importlib
import json
import sys
from unittest.mock import MagicMock, patch

import pytest

# boto3 is not installed in the dev venv (it's a Lambda runtime dependency).
# Stub it before importing the handler so module-level `import boto3` succeeds.
_mock_boto3 = MagicMock()
sys.modules.setdefault("boto3", _mock_boto3)

# "lambda" is a Python keyword, so we import the module via importlib.
_handler_mod = importlib.import_module("lambda.s3_trigger.handler")

_parse_collection_and_filename = _handler_mod._parse_collection_and_filename
_handle_created = _handler_mod._handle_created
_handle_removed = _handler_mod._handle_removed
handler = _handler_mod.handler

# Patch targets use the dotted module name string.
_MOD = "lambda.s3_trigger.handler"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_s3_event(event_name, bucket, key, size=1024):
    """Build a minimal S3 event record."""
    return {
        "Records": [
            {
                "eventName": event_name,
                "s3": {
                    "bucket": {"name": bucket},
                    "object": {"key": key, "size": size},
                },
            }
        ]
    }


def _record(event_name="ObjectCreated:Put", bucket="test-bucket", key="col/file.pdf", size=1024):
    return _make_s3_event(event_name, bucket, key, size)["Records"][0]


# ===========================================================================
# _parse_collection_and_filename
# ===========================================================================

class TestParseCollectionAndFilename:
    def test_standard_prefix(self):
        collection, filename = _parse_collection_and_filename("ecommerce/faq.pdf")
        assert collection == "ecommerce"
        assert filename == "faq.pdf"

    def test_nested_prefix(self):
        collection, filename = _parse_collection_and_filename("policies/2024/return_policy.md")
        assert collection == "policies"
        assert filename == "return_policy.md"

    def test_no_prefix(self):
        collection, filename = _parse_collection_and_filename("file.pdf")
        assert collection is None
        assert filename == "file.pdf"

    def test_deep_nesting(self):
        collection, filename = _parse_collection_and_filename("a/b/c/d/doc.txt")
        assert collection == "a"
        assert filename == "doc.txt"


# ===========================================================================
# _handle_created
# ===========================================================================

class TestHandleCreated:
    @patch(f"{_MOD}.requests.post")
    @patch(f"{_MOD}.s3_client")
    def test_downloads_and_posts_file(self, mock_s3, mock_post):
        body = MagicMock()
        body.read.return_value = b"file content"
        mock_s3.get_object.return_value = {"Body": body}

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "num_chunks": 5}
        mock_post.return_value = mock_resp

        record = _record(key="ecommerce/faq.pdf", size=2048)
        _handle_created("test-bucket", "ecommerce/faq.pdf", record)

        mock_s3.get_object.assert_called_once_with(Bucket="test-bucket", Key="ecommerce/faq.pdf")
        mock_post.assert_called_once()

        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["data"]["collection"] == "ecommerce"
        assert "file" in call_kwargs[1]["files"]
        file_tuple = call_kwargs[1]["files"]["file"]
        assert file_tuple[0] == "faq.pdf"
        assert file_tuple[1] == b"file content"

    @patch(f"{_MOD}.requests.post")
    @patch(f"{_MOD}.s3_client")
    def test_skips_unsupported_extension(self, mock_s3, mock_post):
        record = _record(key="ecommerce/image.png", size=500)
        _handle_created("test-bucket", "ecommerce/image.png", record)

        mock_s3.get_object.assert_not_called()
        mock_post.assert_not_called()

    @patch(f"{_MOD}.requests.post")
    @patch(f"{_MOD}.s3_client")
    def test_skips_oversized_file(self, mock_s3, mock_post):
        oversized = 100 * 1024 * 1024  # 100 MB
        record = _record(key="ecommerce/huge.pdf", size=oversized)
        _handle_created("test-bucket", "ecommerce/huge.pdf", record)

        mock_s3.get_object.assert_not_called()
        mock_post.assert_not_called()

    @patch(f"{_MOD}.requests.post")
    @patch(f"{_MOD}.s3_client")
    def test_sends_correct_metadata(self, mock_s3, mock_post):
        body = MagicMock()
        body.read.return_value = b"data"
        mock_s3.get_object.return_value = {"Body": body}

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True}
        mock_post.return_value = mock_resp

        record = _record(key="col/doc.txt", size=100)
        _handle_created("mybucket", "col/doc.txt", record)

        call_data = mock_post.call_args[1]["data"]
        metadata = json.loads(call_data["metadata"])
        assert metadata["source"] == "doc.txt"
        assert metadata["s3_bucket"] == "mybucket"
        assert metadata["s3_key"] == "col/doc.txt"

    @patch(f"{_MOD}.requests.post")
    @patch(f"{_MOD}.s3_client")
    def test_no_collection_when_no_prefix(self, mock_s3, mock_post):
        body = MagicMock()
        body.read.return_value = b"data"
        mock_s3.get_object.return_value = {"Body": body}

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True}
        mock_post.return_value = mock_resp

        record = _record(key="file.md", size=100)
        _handle_created("mybucket", "file.md", record)

        call_data = mock_post.call_args[1]["data"]
        assert "collection" not in call_data

    @patch(f"{_MOD}.requests.post")
    @patch(f"{_MOD}.s3_client")
    def test_400_does_not_raise(self, mock_s3, mock_post):
        body = MagicMock()
        body.read.return_value = b"data"
        mock_s3.get_object.return_value = {"Body": body}

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad request"
        mock_post.return_value = mock_resp

        record = _record(key="col/bad.txt", size=10)
        # Should not raise — 400 is a non-retryable client error
        _handle_created("bucket", "col/bad.txt", record)

    @patch(f"{_MOD}.requests.post")
    @patch(f"{_MOD}.s3_client")
    def test_500_raises_for_retry(self, mock_s3, mock_post):
        body = MagicMock()
        body.read.return_value = b"data"
        mock_s3.get_object.return_value = {"Body": body}

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = Exception("Server error")
        mock_post.return_value = mock_resp

        record = _record(key="col/doc.pdf", size=10)
        with pytest.raises(Exception, match="Server error"):
            _handle_created("bucket", "col/doc.pdf", record)


# ===========================================================================
# _handle_removed
# ===========================================================================

class TestHandleRemoved:
    @patch(f"{_MOD}.requests.delete")
    def test_calls_delete_endpoint(self, mock_delete):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True}
        mock_delete.return_value = mock_resp

        record = _record(event_name="ObjectRemoved:Delete", key="ecommerce/faq.pdf")
        _handle_removed("test-bucket", "ecommerce/faq.pdf", record)

        mock_delete.assert_called_once()
        call_kwargs = mock_delete.call_args
        assert call_kwargs[1]["params"]["source"] == "faq.pdf"
        assert call_kwargs[1]["params"]["collection"] == "ecommerce"

    @patch(f"{_MOD}.requests.delete")
    def test_handles_404_gracefully(self, mock_delete):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_delete.return_value = mock_resp

        record = _record(event_name="ObjectRemoved:Delete", key="ecommerce/gone.pdf")
        # Should not raise
        _handle_removed("test-bucket", "ecommerce/gone.pdf", record)

    @patch(f"{_MOD}.requests.delete")
    def test_no_collection_when_no_prefix(self, mock_delete):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True}
        mock_delete.return_value = mock_resp

        record = _record(event_name="ObjectRemoved:Delete", key="file.pdf")
        _handle_removed("test-bucket", "file.pdf", record)

        params = mock_delete.call_args[1]["params"]
        assert params["source"] == "file.pdf"
        assert "collection" not in params

    @patch(f"{_MOD}.requests.delete")
    def test_500_raises_for_retry(self, mock_delete):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = Exception("Server error")
        mock_delete.return_value = mock_resp

        record = _record(event_name="ObjectRemoved:Delete", key="col/doc.pdf")
        with pytest.raises(Exception, match="Server error"):
            _handle_removed("bucket", "col/doc.pdf", record)


# ===========================================================================
# handler (top-level dispatch)
# ===========================================================================

class TestHandler:
    @patch(f"{_MOD}._handle_created")
    def test_dispatches_created(self, mock_created):
        event = _make_s3_event("ObjectCreated:Put", "bucket", "col/file.pdf")
        result = handler(event, None)

        assert result["statusCode"] == 200
        mock_created.assert_called_once_with("bucket", "col/file.pdf", event["Records"][0])

    @patch(f"{_MOD}._handle_removed")
    def test_dispatches_removed(self, mock_removed):
        event = _make_s3_event("ObjectRemoved:Delete", "bucket", "col/file.pdf")
        result = handler(event, None)

        assert result["statusCode"] == 200
        mock_removed.assert_called_once_with("bucket", "col/file.pdf", event["Records"][0])

    @patch(f"{_MOD}._handle_created")
    @patch(f"{_MOD}._handle_removed")
    def test_ignores_unknown_event(self, mock_removed, mock_created):
        event = _make_s3_event("ObjectRestore:Post", "bucket", "col/file.pdf")
        result = handler(event, None)

        assert result["statusCode"] == 200
        mock_created.assert_not_called()
        mock_removed.assert_not_called()

    @patch(f"{_MOD}._handle_created")
    def test_url_decodes_key(self, mock_created):
        event = _make_s3_event("ObjectCreated:Put", "bucket", "col/my+file+%282%29.pdf")
        handler(event, None)

        mock_created.assert_called_once()
        # unquote_plus converts + to space and %28/%29 to parens
        assert mock_created.call_args[0][1] == "col/my file (2).pdf"
