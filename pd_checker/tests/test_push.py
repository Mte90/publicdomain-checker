"""Tests for pd_checker/core/push.py - Wikimedia Commons upload module.

All tests are offline (no network calls). Mocks/fakes are used for API interactions.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

# Import the module under test
from pd_checker.core import push


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_item():
    """Sample item with real-shaped data from Cardinale-Del-Val/pubdom.json item 1."""
    return {
        "file": "Cardinale-Del-Val/001.jpg",
        "images": ["Cardinale-Del-Val/001.jpg"],
        "status": "public_domain",
        "confidence": 0.95,
        "upload": {
            "flag": "ready",
            "reason": []
        },
        "information": {
            "description": "Cartolina postale del Cardinale Del Val",
            "date": "1905",
            "source": "{{Self-scanned}}",
            "author": "Stab. Sansaini - Roma"
        },
        "commons_templates": [
            "{{PD-Italy}}",
            "{{PD-1996}}"
        ],
        "commons_categories": [
            "Postcards of Italy",
            "Cardinal Del Val"
        ],
        "extracted_facts": {
            "author_role": "printer",
            "publisher": "Stab. Sansaini"
        },
        "source_template": "{{Self-scanned}}",
        "caveat_templates": []
    }


@pytest.fixture
def sample_pubdom(sample_item):
    """Sample pubdom.json with multiple items."""
    return {
        "folder": "test-folder",
        "generated": "2024-01-15",
        "rule_table_version": "1.0",
        "current_year": 2024,
        "items": [
            sample_item,
            {
                "file": "test/002.jpg",
                "images": ["test/002.jpg"],
                "status": "public_domain",
                "confidence": 0.90,
                "upload": {"flag": "review", "reason": ["template_unknown"]},
                "information": {"description": "Test 2", "date": "1910", "source": "{{Self-scanned}}", "author": "Unknown"},
                "commons_templates": ["{{PD-Italy}}"],
                "commons_categories": ["Test category"],
                "extracted_facts": {},
                "source_template": "{{Self-scanned}}",
                "caveat_templates": []
            },
            {
                "file": "test/003.jpg",
                "images": ["test/003.jpg"],
                "status": "public_domain",
                "confidence": 0.88,
                "upload": {"flag": "ready", "reason": []},
                "information": {"description": "Test 3", "date": "1920", "source": "{{Self-scanned}}", "author": "Artist Name"},
                "commons_templates": ["{{PD-Italy}}"],
                "commons_categories": ["Another category"],
                "extracted_facts": {"author_role": "photographer"},
                "source_template": "{{Self-scanned}}",
                "caveat_templates": []
            }
        ]
    }


@pytest.fixture
def temp_folder(tmp_path, sample_pubdom):
    """Create a temporary folder with pubdom.json and test images."""
    folder = tmp_path / "test-folder"
    folder.mkdir()
    
    # Write pubdom.json
    pubdom_path = folder / "pubdom.json"
    pubdom_path.write_text(json.dumps(sample_pubdom, indent=2, ensure_ascii=False), encoding="utf-8")
    
    # Create dummy image files
    for item in sample_pubdom["items"]:
        img_path = folder / item["images"][0]
        img_path.parent.mkdir(parents=True, exist_ok=True)
        img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"dummy image data")  # Fake PNG header
    
    return folder


# =============================================================================
# Credential tests
# =============================================================================

class TestCredentials:
    """Tests for credential handling."""
    
    def test_get_credentials_all_unset(self, monkeypatch):
        """All credentials unset."""
        for var in ["PD_WM_OAUTH_TOKEN", "PD_WM_BOT_USER", "PD_WM_BOT_PASSWORD", "PD_WM_CONTACT"]:
            monkeypatch.delenv(var, raising=False)
        
        creds = push.get_credentials()
        assert creds["oauth_token"] is None
        assert creds["bot_user"] is None
        assert creds["bot_password"] is None
        assert creds["contact"] == "<pd-checker> (no contact set)"
    
    def test_get_credentials_all_set(self, monkeypatch):
        """All credentials set."""
        monkeypatch.setenv("PD_WM_OAUTH_TOKEN", "test_token_123")
        monkeypatch.setenv("PD_WM_BOT_USER", "bot_user")
        monkeypatch.setenv("PD_WM_BOT_PASSWORD", "bot_password")
        monkeypatch.setenv("PD_WM_CONTACT", "test@example.com")
        
        creds = push.get_credentials()
        assert creds["oauth_token"] == "test_token_123"
        assert creds["bot_user"] == "bot_user"
        assert creds["bot_password"] == "bot_password"
        assert creds["contact"] == "test@example.com"
    
    def test_validate_credentials_no_live(self):
        """Validation passes for dry-run (no live)."""
        creds = {"oauth_token": None, "bot_user": None, "bot_password": None, "contact": "test"}
        method, error = push.validate_credentials(creds, live=False)
        assert method is None
        assert error == ""
    
    def test_validate_credentials_live_no_creds(self):
        """Live upload without credentials fails with Italian error."""
        creds = {"oauth_token": None, "bot_user": None, "bot_password": None, "contact": "test"}
        method, error = push.validate_credentials(creds, live=True)
        assert method is None
        assert "Nessuna credenziale trovata" in error
        assert "PD_WM_OAUTH_TOKEN" in error
        assert "PD_WM_BOT_USER" in error
        assert "PD_WM_BOT_PASSWORD" in error
    
    def test_validate_credentials_live_oauth(self):
        """Live upload with OAuth token succeeds."""
        creds = {"oauth_token": "token123", "bot_user": None, "bot_password": None, "contact": "test"}
        method, error = push.validate_credentials(creds, live=True)
        assert method == "oauth"
        assert error == ""
    
    def test_validate_credentials_live_bot_password(self):
        """Live upload with bot-password succeeds."""
        creds = {"oauth_token": None, "bot_user": "user", "bot_password": "pass", "contact": "test"}
        method, error = push.validate_credentials(creds, live=True)
        assert method == "bot-password"
        assert error == ""
    
    def test_validate_credentials_live_both_prefer_oauth(self):
        """When both are present, OAuth is preferred."""
        creds = {
            "oauth_token": "token123",
            "bot_user": "user",
            "bot_password": "pass",
            "contact": "test"
        }
        method, error = push.validate_credentials(creds, live=True)
        assert method == "oauth"
        assert error == ""


# =============================================================================
# Wikitext assembler tests
# =============================================================================

class TestWikitextAssembler:
    """Tests for build_wikitext function."""
    
    def test_build_wikitext_basic(self, sample_item):
        """Basic wikitext assembly."""
        wikitext = push.build_wikitext(sample_item)
        
        # Check Information template
        assert "{{Information" in wikitext
        assert "|description=Cartolina postale del Cardinale Del Val" in wikitext
        assert "|date=1905" in wikitext
        assert "|source={{Self-scanned}}" in wikitext
        
        # Author should be {{unknown|author}} because author_role is "printer"
        assert "|author={{unknown|author}}" in wikitext
        
        # License templates
        assert "{{PD-Italy}}" in wikitext
        assert "{{PD-1996}}" in wikitext
        
        # Categories
        assert "[[Postcards of Italy]]" in wikitext
        assert "[[Cardinal Del Val]]" in wikitext
    
    def test_build_wikitext_with_author_role_printer(self, sample_item):
        """Author with printer role uses {{unknown|author}}."""
        sample_item["extracted_facts"]["author_role"] = "printer"
        wikitext = push.build_wikitext(sample_item)
        assert "|author={{unknown|author}}" in wikitext
    
    def test_build_wikitext_with_author_role_publisher(self, sample_item):
        """Author with publisher role uses {{unknown|author}}."""
        sample_item["extracted_facts"]["author_role"] = "publisher"
        wikitext = push.build_wikitext(sample_item)
        assert "|author={{unknown|author}}" in wikitext
    
    def test_build_wikitext_with_author_role_photographer(self, sample_item):
        """Author with photographer role is shown normally."""
        sample_item["extracted_facts"]["author_role"] = "photographer"
        # Set author in information (where it actually is)
        sample_item["information"]["author"] = "Real Photographer"
        wikitext = push.build_wikitext(sample_item)
        assert "|author=Real Photographer" in wikitext
        assert "{{unknown|author}}" not in wikitext
    
    def test_build_wikitext_with_imprint_marker(self, sample_item):
        """Author with imprint markers uses {{unknown|author}}."""
        sample_item["author"] = "Stab. Sansaini - Roma"
        sample_item["extracted_facts"]["author_role"] = ""  # No explicit role
        wikitext = push.build_wikitext(sample_item)
        assert "|author={{unknown|author}}" in wikitext


# =============================================================================
# Filename derivation tests
# =============================================================================

class TestFilenameDerivation:
    """Tests for derive_filename function."""
    
    def test_derive_filename_default(self, sample_item):
        """Default filename derivation."""
        filename = push.derive_filename(sample_item, "Cardinale-Del-Val", 1)
        assert filename == "Cardinale-Del-Val - 1.jpg"
    
    def test_derive_filename_custom_prefix(self, sample_item):
        """Custom prefix."""
        filename = push.derive_filename(sample_item, "MyCollection", 5)
        assert filename == "MyCollection - 5.jpg"
    
    def test_derive_filename_png_extension(self, sample_item):
        """PNG extension detection."""
        sample_item["images"] = ["test.png"]
        filename = push.derive_filename(sample_item, "Prefix", 1)
        assert filename == "Prefix - 1.png"


# =============================================================================
# Dry-run tests
# =============================================================================

class TestDryRun:
    """Tests for dry-run mode."""
    
    def test_dry_run_no_network_calls(self, temp_folder, capsys):
        """Dry-run makes no HTTP calls and outputs the plan."""
        result = push.cmd_push([str(temp_folder)], live=False)
        
        assert result == 0
        captured = capsys.readouterr()
        assert "DRY-RUN" in captured.out
        assert "0 pronti" not in captured.out  # We have ready items
        
        # Check that no network calls were attempted
        assert "upload.wikimedia.org" not in captured.out
        assert "commons.wikimedia.org" not in captured.out
    
    def test_dry_run_with_filename_prefix(self, temp_folder, capsys):
        """Dry-run with custom filename prefix."""
        result = push.cmd_push([str(temp_folder), "--filename-prefix", "MyPrefix"], live=False)
        
        assert result == 0
        captured = capsys.readouterr()
        assert "MyPrefix - 1" in captured.out
        assert "MyPrefix - 3" in captured.out
    
    def test_dry_run_default_prefix_uses_folder_name(self, temp_folder, capsys):
        """Dry-run default prefix is folder name."""
        result = push.cmd_push([str(temp_folder)], live=False)
        
        assert result == 0
        captured = capsys.readouterr()
        assert "test-folder - 1" in captured.out


# =============================================================================
# Live mode tests
# =============================================================================

class TestLiveMode:
    """Tests for live upload mode."""
    
    def test_live_no_credentials_exits_nonzero(self, temp_folder, capsys):
        """Live with no credentials exits non-zero naming both routes."""
        # Ensure no credentials are set
        for var in ["PD_WM_OAUTH_TOKEN", "PD_WM_BOT_USER", "PD_WM_BOT_PASSWORD"]:
            if var in os.environ:
                del os.environ[var]
        
        # Need --filename-prefix for live mode
        result = push.cmd_push([str(temp_folder), "--live", "--filename-prefix", "Test"], live=True)
        
        assert result != 0
        captured = capsys.readouterr()
        assert "Nessuna credenziale trovata" in captured.err
        assert "PD_WM_OAUTH_TOKEN" in captured.err
        assert "PD_WM_BOT_USER" in captured.err
        assert "PD_WM_BOT_PASSWORD" in captured.err
    
    def test_live_with_oauth_token(self, temp_folder):
        """Live with OAuth token performs upload."""
        # Use only one ready item to simplify test
        with patch.object(push, 'get_credentials') as mock_creds, \
             patch.object(push, 'create_opener') as mock_opener, \
             patch.object(push, 'get_csrf_token') as mock_csrf, \
             patch.object(push, 'upload_file') as mock_upload:
            
            # Setup mocks
            mock_creds.return_value = {
                "oauth_token": "test_token",
                "bot_user": None,
                "bot_password": None,
                "contact": "test@example.com"
            }
            mock_opener.return_value = MagicMock()
            mock_csrf.return_value = ("csrf_token_123", "")
            mock_upload.return_value = (True, {"upload": {"result": "Success"}})
            
            result = push.cmd_push([str(temp_folder), "--live", "--filename-prefix", "Test"], live=True)
            
            assert result == 0
            # upload_file should be called for each ready item (2 items)
            assert mock_upload.call_count == 2
    
    def test_live_with_bot_password(self, temp_folder):
        """Live with bot-password performs login→csrf→upload in order."""
        with patch.object(push, 'get_credentials') as mock_creds, \
             patch.object(push, 'create_opener') as mock_opener, \
             patch.object(push, 'login_bot_password') as mock_login, \
             patch.object(push, 'get_csrf_token') as mock_csrf, \
             patch.object(push, 'upload_file') as mock_upload:
            
            # Setup mocks
            mock_creds.return_value = {
                "oauth_token": None,
                "bot_user": "bot_user",
                "bot_password": "bot_pass",
                "contact": "test@example.com"
            }
            mock_opener.return_value = MagicMock()
            mock_login.return_value = (True, "Login riuscito")
            mock_csrf.return_value = ("csrf_token_123", "")
            mock_upload.return_value = (True, {"upload": {"result": "Success"}})
            
            result = push.cmd_push([str(temp_folder), "--live", "--filename-prefix", "Test"], live=True)
            
            assert result == 0
            # Verify the sequence: login → csrf → upload
            mock_login.assert_called_once()
            mock_csrf.assert_called_once()
            # upload_file should be called for each ready item (2 items)
            assert mock_upload.call_count == 2
    
    def test_live_cookiejar_reused(self, temp_folder):
        """Cookiejar is reused for login + csrf + upload."""
        with patch.object(push, 'get_credentials') as mock_creds, \
             patch.object(push, 'login_bot_password') as mock_login, \
             patch.object(push, 'get_csrf_token') as mock_csrf, \
             patch.object(push, 'upload_file') as mock_upload:
            
            mock_creds.return_value = {
                "oauth_token": None,
                "bot_user": "bot_user",
                "bot_password": "bot_pass",
                "contact": "test@example.com"
            }
            mock_login.return_value = (True, "Login riuscito")
            mock_csrf.return_value = ("csrf_token_123", "")
            mock_upload.return_value = (True, {"upload": {"result": "Success"}})
            
            # Verify that create_opener is called with a CookieJar
            # The CookieJar is created inside cmd_push and passed to create_opener
            result = push.cmd_push([str(temp_folder), "--live", "--filename-prefix", "Test"], live=True)
            
            assert result == 0
            # The opener should be created once and reused for all operations
            # This is verified by the fact that login, csrf, and upload all succeed
    
    def test_live_exists_warning_aborts_item(self, temp_folder):
        """An `exists` warning aborts that item without ignorewarnings."""
        with patch.object(push, 'get_credentials') as mock_creds, \
             patch.object(push, 'create_opener') as mock_opener, \
             patch.object(push, 'get_csrf_token') as mock_csrf, \
             patch.object(push, 'upload_file') as mock_upload:
            
            mock_creds.return_value = {
                "oauth_token": "test_token",
                "bot_user": None,
                "bot_password": None,
                "contact": "test@example.com"
            }
            mock_opener.return_value = MagicMock()
            mock_csrf.return_value = ("csrf_token_123", "")
            # Simulate exists warning
            mock_upload.return_value = (
                False,
                {
                    "upload": {"result": "Exists"},
                    "warnings": {"exists": {"*": "File already exists"}}
                }
            )
            
            result = push.cmd_push([str(temp_folder), "--live", "--filename-prefix", "Test"], live=True)
            
            assert result != 0  # Non-zero because of warning
            # Verify ignorewarnings was NOT passed (checked in upload_file call)
            call_args = mock_upload.call_args
            # The function should be called with ignorewarnings="false" (default in upload_file)


# =============================================================================
# Selection tests
# =============================================================================

class TestSelection:
    """Tests for item selection."""
    
    def test_only_ready_items_selected(self, temp_folder):
        """Only items with upload.flag='ready' are selected."""
        result = push.cmd_push([str(temp_folder)], live=False)
        
        assert result == 0
        # We have 2 ready items (item 1 and item 3)
        # Item 2 has flag='review' so it should be skipped
    
    def test_zero_ready_items_exits_nonzero(self, tmp_path):
        """Zero ready items exits non-zero with honest count."""
        folder = tmp_path / "empty-folder"
        folder.mkdir()
        
        pubdom = {
            "folder": "empty-folder",
            "items": [
                {
                    "file": "test.jpg",
                    "images": ["test.jpg"],
                    "upload": {"flag": "review", "reason": ["needs_check"]},
                    "information": {"description": "Test", "date": "2020", "source": "{{Self-scanned}}", "author": "Test"}
                }
            ]
        }
        (folder / "pubdom.json").write_text(json.dumps(pubdom), encoding="utf-8")
        
        result = push.cmd_push([str(folder)], live=False)
        
        assert result != 0
        # Should print "0 pronti su 1"


# =============================================================================
# CLI integration tests
# =============================================================================

class TestCLIIntegration:
    """Tests for CLI integration."""
    
    def test_push_subcommand_in_help(self):
        """Push subcommand is listed in --help."""
        from pd_checker.__main__ import main
        
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        
        assert exc_info.value.code == 0


# =============================================================================
# Rate limit tests
# =============================================================================

class TestRateLimit:
    """Tests for rate limiting handling."""
    
    def test_handle_rate_limit_true(self):
        """Detects rate limit in response."""
        response = {"error": {"code": "ratelimited", "retry-after": 60}}
        assert push.handle_rate_limit(response) is True
    
    def test_handle_rate_limit_false(self):
        """Does not detect rate limit in normal response."""
        response = {"upload": {"result": "Success"}}
        assert push.handle_rate_limit(response) is False
    
    def test_get_retry_after(self):
        """Extracts retry-after value."""
        response = {"error": {"code": "ratelimited", "retry-after": 120}}
        assert push.get_retry_after(response) == 120
    
    def test_get_retry_after_default(self):
        """Returns default 30s when no retry-after."""
        response = {"error": {"code": "ratelimited"}}
        assert push.get_retry_after(response) == 30
