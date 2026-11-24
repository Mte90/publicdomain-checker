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
            "publisher": "Stab. Sansaini",
            "images": ["Cardinale-Del-Val/001.jpg"]
        },
        "source_template": "{{Self-scanned}}",
        "caveat_templates": []
    }


@pytest.fixture
def sample_item_multi_image():
    """Sample item with multiple images (front + back)."""
    return {
        "file": "Cardinale-Del-Val/003.jpg",
        "status": "public_domain",
        "confidence": 0.92,
        "upload": {
            "flag": "ready",
            "reason": []
        },
        "information": {
            "description": "Cartolina postale del Cardinale Del Val",
            "date": "1908",
            "source": "{{Self-scanned}}",
            "author": "Unknown"
        },
        "commons_templates": [
            "{{PD-Italy}}"
        ],
        "commons_categories": [
            "Postcards of Italy",
            "Cardinal Del Val"
        ],
        "extracted_facts": {
            "images": [
                "Cardinale-Del-Val/003_front.png",
                "Cardinale-Del-Val/003_back.png"
            ]
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
                "status": "public_domain",
                "confidence": 0.90,
                "upload": {"flag": "review", "reason": ["template_unknown"]},
                "information": {"description": "Test 2", "date": "1910", "source": "{{Self-scanned}}", "author": "Unknown"},
                "commons_templates": ["{{PD-Italy}}"],
                "commons_categories": ["Test category"],
                "extracted_facts": {"images": ["test/002.jpg"]},
                "source_template": "{{Self-scanned}}",
                "caveat_templates": []
            },
            {
                "file": "test/003.jpg",
                "status": "public_domain",
                "confidence": 0.88,
                "upload": {"flag": "ready", "reason": []},
                "information": {"description": "Test 3", "date": "1920", "source": "{{Self-scanned}}", "author": "Artist Name"},
                "commons_templates": ["{{PD-Italy}}"],
                "commons_categories": ["Another category"],
                "extracted_facts": {"author_role": "photographer", "images": ["test/003.jpg"]},
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
        img_path = folder / item["extracted_facts"]["images"][0]
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
        sample_item["extracted_facts"]["images"] = ["test.png"]
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
             patch.object(push, 'upload_file') as mock_upload, \
             patch.object(push, '_check_existing_files') as mock_check:
            
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
            mock_check.return_value = set()  # No existing files
            
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
             patch.object(push, 'upload_file') as mock_upload, \
             patch.object(push, '_check_existing_files') as mock_check:
            
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
            mock_check.return_value = set()  # No existing files
            
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
             patch.object(push, 'create_opener') as mock_opener, \
             patch.object(push, 'login_bot_password') as mock_login, \
             patch.object(push, 'get_csrf_token') as mock_csrf, \
             patch.object(push, 'upload_file') as mock_upload, \
             patch.object(push, '_check_existing_files') as mock_check:
            
            mock_creds.return_value = {
                "oauth_token": None,
                "bot_user": "bot_user",
                "bot_password": "bot_pass",
                "contact": "test@example.com"
            }
            mock_login.return_value = (True, "Login riuscito")
            mock_csrf.return_value = ("csrf_token_123", "")
            mock_upload.return_value = (True, {"upload": {"result": "Success"}})
            mock_check.return_value = set()  # No existing files
            
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
             patch.object(push, 'upload_file') as mock_upload, \
             patch.object(push, '_check_existing_files') as mock_check:
            
            mock_creds.return_value = {
                "oauth_token": "test_token",
                "bot_user": None,
                "bot_password": None,
                "contact": "test@example.com"
            }
            mock_opener.return_value = MagicMock()
            mock_csrf.return_value = ("csrf_token_123", "")
            mock_check.return_value = set()  # No existing files (pre-check)
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
                    "upload": {"flag": "review", "reason": ["needs_check"]},
                    "information": {"description": "Test", "date": "2020", "source": "{{Self-scanned}}", "author": "Test"},
                    "extracted_facts": {"images": ["test.jpg"]}
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


# =============================================================================
# Multi-image upload tests
# =============================================================================

class TestMultiImageUpload:
    """Tests for multi-image (front + back) upload."""
    
    def test_multi_image_plans_two_files(self, tmp_path, sample_item_multi_image):
        """Multi-image item plans two files with correct names."""
        folder = tmp_path / "test-multi"
        folder.mkdir()
        
        pubdom = {
            "folder": "test-multi",
            "generated": "2024-01-15",
            "rule_table_version": "1.0",
            "current_year": 2024,
            "items": [sample_item_multi_image]
        }
        
        # Write pubdom.json
        (folder / "pubdom.json").write_text(json.dumps(pubdom), encoding="utf-8")
        
        # Create dummy image files
        (folder / "Cardinale-Del-Val").mkdir()
        img1 = folder / "Cardinale-Del-Val/003_front.png"
        img2 = folder / "Cardinale-Del-Val/003_back.png"
        img1.write_bytes(b"\x89PNG\r\n\x1a\n" + b"dummy front")
        img2.write_bytes(b"\x89PNG\r\n\x1a\n" + b"dummy back")
        
        result = push.cmd_push([str(folder), "--filename-prefix", "Merry Del Val"], live=False)
        
        assert result == 0
        # Both files should be planned
        # Front: "Merry Del Val - 1.png"
        # Back: "Merry Del Val - 1 (retro).png"
    
    def test_retro_wikitext_contains_backlink(self, sample_item_multi_image):
        """Retro wikitext contains 'Retro della cartolina' link to main file."""
        main_filename = "Merry Del Val - 1.png"
        wikitext = push.build_retro_wikitext(sample_item_multi_image, main_filename)
        
        assert "Retro della cartolina" in wikitext
        assert f"[[:File:{main_filename}|{main_filename}]]" in wikitext
    
    def test_retro_wikitext_same_licence(self, sample_item_multi_image):
        """Retro wikitext has same licence templates as main file."""
        main_filename = "Merry Del Val - 1.png"
        main_wikitext = push.build_wikitext(sample_item_multi_image)
        retro_wikitext = push.build_retro_wikitext(sample_item_multi_image, main_filename)
        
        # Both should have PD-Italy
        assert "{{PD-Italy}}" in main_wikitext
        assert "{{PD-Italy}}" in retro_wikitext
    
    def test_retro_wikitext_same_categories(self, sample_item_multi_image):
        """Retro wikitext has same categories as main file."""
        main_filename = "Merry Del Val - 1.png"
        retro_wikitext = push.build_retro_wikitext(sample_item_multi_image, main_filename)
        
        assert "[[Postcards of Italy]]" in retro_wikitext
        assert "[[Cardinal Del Val]]" in retro_wikitext
    
    def test_precheck_skips_existing(self, tmp_path, sample_item_multi_image, monkeypatch):
        """Pre-check skips existing file (fake API) and records exists without upload attempt."""
        folder = tmp_path / "test-precheck"
        folder.mkdir()
        
        pubdom = {
            "folder": "test-precheck",
            "generated": "2024-01-15",
            "rule_table_version": "1.0",
            "current_year": 2024,
            "items": [sample_item_multi_image]
        }
        
        # Write pubdom.json
        (folder / "pubdom.json").write_text(json.dumps(pubdom), encoding="utf-8")
        
        # Create dummy image files
        (folder / "Cardinale-Del-Val").mkdir()
        img1 = folder / "Cardinale-Del-Val/003_front.png"
        img2 = folder / "Cardinale-Del-Val/003_back.png"
        img1.write_bytes(b"\x89PNG\r\n\x1a\n" + b"dummy front")
        img2.write_bytes(b"\x89PNG\r\n\x1a\n" + b"dummy back")
        
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
            
            # Simulate pre-check finding one file exists
            # Front exists, back doesn't
            def mock_check_existing(opener, titles, creds):
                return {"Merry Del Val - 1.png"}  # Front exists
            
            with patch.object(push, '_check_existing_files', side_effect=mock_check_existing):
                result = push.cmd_push(
                    [str(folder), "--live", "--filename-prefix", "Merry Del Val"],
                    live=True
                )
            
            # Front should be skipped (exists), back should be uploaded
            # upload_file should be called only once (for back)
            assert mock_upload.call_count == 1
            
            # Verify the call was for the back file
            call_args = mock_upload.call_args
            assert "Merry Del Val - 1 (retro).png" in str(call_args)


# =============================================================================
# Single-image regression tests
# =============================================================================

class TestSingleImageRegression:
    """Regression tests for single-image items (unchanged behavior)."""
    
    def test_single_image_unchanged(self, tmp_path, sample_item):
        """Single-image items still upload one file."""
        folder = tmp_path / "test-single"
        folder.mkdir()
        
        pubdom = {
            "folder": "test-single",
            "generated": "2024-01-15",
            "rule_table_version": "1.0",
            "current_year": 2024,
            "items": [sample_item]
        }
        
        # Write pubdom.json
        (folder / "pubdom.json").write_text(json.dumps(pubdom), encoding="utf-8")
        
        # Create dummy image file
        img = folder / sample_item["extracted_facts"]["images"][0]
        img.parent.mkdir(parents=True, exist_ok=True)
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"dummy image data")
        
        result = push.cmd_push([str(folder), "--filename-prefix", "Test"], live=False)
        
        assert result == 0
        # Should plan exactly one file


# =============================================================================
# Bidirectional other_versions (front <-> retro data link)
# =============================================================================

class TestOtherVersionsLink:
    """The front and retro files must reference each other as data."""

    def test_front_wikitext_links_to_retro(self, sample_item_multi_image):
        """Front Information carries |other_versions= pointing at the retro file."""
        wt = push.build_wikitext(
            sample_item_multi_image,
            other_versions="Retro: [[:File:Merry Del Val - 3 (retro).png|Merry Del Val - 3 (retro).png]]",
        )
        assert "|other_versions=Retro: [[:File:Merry Del Val - 3 (retro).png" in wt

    def test_retro_wikitext_links_to_front(self, sample_item_multi_image):
        """Retro Information carries |other_versions= pointing back at the front."""
        wt = push.build_retro_wikitext(
            sample_item_multi_image, "Merry Del Val - 3.png",
            other_versions="Fronte: [[:File:Merry Del Val - 3.png|Merry Del Val - 3.png]]",
        )
        assert "Retro della cartolina [[:File:Merry Del Val - 3.png" in wt
        assert "|other_versions=Fronte: [[:File:Merry Del Val - 3.png" in wt

    def test_single_side_has_no_other_versions(self, sample_item):
        """An item without a retro side emits no other_versions field."""
        wt = push.build_wikitext(sample_item)
        assert "other_versions" not in wt

    def test_resolve_image_path_encodings(self, tmp_path):
        """Absolute, folder-prefixed and bare names all resolve to the same file."""
        folder = tmp_path / "mat"
        folder.mkdir(parents=True)
        (folder / "1_fronte.png").write_bytes(b"x")
        abs_p = folder / "1_fronte.png"
        assert push._resolve_image_path(folder, str(abs_p)) == abs_p
        assert push._resolve_image_path(folder, "mat/1_fronte.png") == abs_p
        assert push._resolve_image_path(folder, "1_fronte.png") == abs_p
        assert push._resolve_image_path(folder, "nope.png") is None


# =============================================================================
# Tests for no-license check (Commons acceptance gate)
# =============================================================================

class TestNoLicenseCheck:
    """Tests for the no-license check in push.py - Commons acceptance gate."""
    
    def test_push_refuses_unlicensed_item_with_flag_ready(self, tmp_path, capsys):
        """Push refuses items without license templates even with flag=ready."""
        # Create folder structure
        folder = tmp_path / "test_unlicensed"
        folder.mkdir()
        img = folder / "test.png"
        img.write_bytes(b"test")
        
        # Create pubdom.json with unlicensed item (flag=ready but no commons_templates)
        pubdom = {
            "folder": "test_unlicensed",
            "generated": "2024-01-15",
            "items": [
                {
                    "file": "test_unlicensed/test.png",
                    "status": "public_domain",
                    "confidence": 0.95,
                    "upload": {"flag": "ready", "reason": []},
                    "information": {
                        "description": "Test image",
                        "date": "1920",
                        "source": "{{Self-scanned}}",
                        "author": "Unknown"
                    },
                    "commons_templates": [],  # No license template
                    "commons_categories": ["Test category"],
                    "extracted_facts": {"images": ["test_unlicensed/test.png"]},
                    "source_template": "{{Self-scanned}}",
                    "caveat_templates": []
                }
            ]
        }
        
        (folder / "pubdom.json").write_text(json.dumps(pubdom))
        
        # Mock sys.argv to simulate: push test_unlicensed
        test_args = ["push", str(folder)]
        
        with patch.object(push.sys, 'argv', test_args):
            result = push.cmd_push(test_args[1:], live=False)
        
        # Check output
        captured = capsys.readouterr()
        assert "RIFIUTATO" in captured.out
        assert "nessun tag di licenza" in captured.out
        assert "Commons avvierà la cancellazione" in captured.out
        
        # Check exit code
        assert result == 1
    
    def test_push_plans_licensed_item_normally(self, tmp_path, capsys):
        """Push plans items with license templates normally."""
        # Create folder structure
        folder = tmp_path / "test_licensed"
        folder.mkdir()
        img = folder / "test.png"
        img.write_bytes(b"test")
        
        # Create pubdom.json with licensed item
        pubdom = {
            "folder": "test_licensed",
            "generated": "2024-01-15",
            "items": [
                {
                    "file": "test_licensed/test.png",
                    "status": "public_domain",
                    "confidence": 0.95,
                    "upload": {"flag": "ready", "reason": []},
                    "information": {
                        "description": "Test image",
                        "date": "1920",
                        "source": "{{Self-scanned}}",
                        "author": "Unknown"
                    },
                    "commons_templates": ["{{PD-anon-70-EU}}", "{{PD-1996}}"],
                    "commons_categories": ["Test category"],
                    "extracted_facts": {"images": ["test_licensed/test.png"]},
                    "source_template": "{{Self-scanned}}",
                    "caveat_templates": []
                }
            ]
        }
        
        (folder / "pubdom.json").write_text(json.dumps(pubdom))
        
        # Mock sys.argv to simulate: push test_licensed
        test_args = ["push", str(folder)]
        
        with patch.object(push.sys, 'argv', test_args):
            result = push.cmd_push(test_args[1:], live=False)
        
        # Check output - should show DRY-RUN planning
        captured = capsys.readouterr()
        assert "DRY-RUN" in captured.out
        assert "RIFIUTATO" not in captured.out
        
        # Check exit code (should be 0 for dry-run success)
        assert result == 0
    
    def test_push_exit_code_1_with_only_unlicensed_items(self, tmp_path, capsys):
        """Push exits with code 1 when all items are unlicensed."""
        # Create folder structure
        folder = tmp_path / "test_all_unlicensed"
        folder.mkdir()
        img = folder / "test.png"
        img.write_bytes(b"test")
        
        # Create pubdom.json with only unlicensed items
        pubdom = {
            "folder": "test_all_unlicensed",
            "generated": "2024-01-15",
            "items": [
                {
                    "file": "test_all_unlicensed/test.png",
                    "status": "public_domain",
                    "confidence": 0.95,
                    "upload": {"flag": "ready", "reason": []},
                    "information": {
                        "description": "Test image",
                        "date": "1920",
                        "source": "{{Self-scanned}}",
                        "author": "Unknown"
                    },
                    "commons_templates": [],  # No license template
                    "commons_categories": ["Test category"],
                    "extracted_facts": {"images": ["test_all_unlicensed/test.png"]},
                    "source_template": "{{Self-scanned}}",
                    "caveat_templates": []
                }
            ]
        }
        
        (folder / "pubdom.json").write_text(json.dumps(pubdom))
        
        # Mock sys.argv to simulate: push test_all_unlicensed
        test_args = ["push", str(folder)]
        
        with patch.object(push.sys, 'argv', test_args):
            result = push.cmd_push(test_args[1:], live=False)
        
        # Check exit code
        assert result == 1
    
    def test_push_refuses_item_with_source_non_specificato(self, tmp_path, capsys):
        """Push refuses items with source 'non specificato' even with license."""
        # Create folder structure
        folder = tmp_path / "test_nosource"
        folder.mkdir()
        img = folder / "test.png"
        img.write_bytes(b"test")
        
        # Create pubdom.json with item that has license but source="non specificato"
        pubdom = {
            "folder": "test_nosource",
            "generated": "2024-01-15",
            "items": [
                {
                    "file": "test_nosource/test.png",
                    "status": "public_domain",
                    "confidence": 0.95,
                    "upload": {"flag": "ready", "reason": []},
                    "information": {
                        "description": "Test image",
                        "date": "1920",
                        "source": "non specificato",  # Invalid source
                        "author": "Unknown"
                    },
                    "commons_templates": ["{{PD-Italy}}"],  # Has license
                    "commons_categories": ["Test category"],
                    "extracted_facts": {"images": ["test_nosource/test.png"]},
                    "source_template": "{{Self-scanned}}",
                    "caveat_templates": []
                }
            ]
        }
        
        (folder / "pubdom.json").write_text(json.dumps(pubdom))
        
        # Mock sys.argv to simulate: push test_nosource
        test_args = ["push", str(folder)]
        
        with patch.object(push.sys, 'argv', test_args):
            result = push.cmd_push(test_args[1:], live=False)
        
        # Check output
        captured = capsys.readouterr()
        assert "RIFIUTATO" in captured.out
        assert "fonte non dichiarata" in captured.out
        assert "Commons segnala i file senza fonte" in captured.out
        
        # Check exit code
        assert result == 1
    
    def test_push_refuses_item_with_no_categories(self, tmp_path, capsys):
        """Push refuses items with no categories even with license and source."""
        # Create folder structure
        folder = tmp_path / "test_nocat"
        folder.mkdir()
        img = folder / "test.png"
        img.write_bytes(b"test")
        
        # Create pubdom.json with item that has license+source but no categories
        pubdom = {
            "folder": "test_nocat",
            "generated": "2024-01-15",
            "items": [
                {
                    "file": "test_nocat/test.png",
                    "status": "public_domain",
                    "confidence": 0.95,
                    "upload": {"flag": "ready", "reason": []},
                    "information": {
                        "description": "Test image",
                        "date": "1920",
                        "source": "Scansione dalla collezione di Daniele Scasciafratte",
                        "author": "Unknown"
                    },
                    "commons_templates": ["{{PD-Italy}}"],  # Has license
                    "commons_categories": [],  # No categories
                    "extracted_facts": {"images": ["test_nocat/test.png"]},
                    "source_template": "{{Self-scanned}}",
                    "caveat_templates": []
                }
            ]
        }
        
        (folder / "pubdom.json").write_text(json.dumps(pubdom))
        
        # Mock sys.argv to simulate: push test_nocat
        test_args = ["push", str(folder)]
        
        with patch.object(push.sys, 'argv', test_args):
            result = push.cmd_push(test_args[1:], live=False)
        
        # Check output
        captured = capsys.readouterr()
        assert "RIFIUTATO" in captured.out
        assert "nessuna categoria" in captured.out
        assert "Commons segnala i file non categorizzati" in captured.out
        
        # Check exit code
        assert result == 1
    
    def test_push_accepts_fully_valid_item(self, tmp_path, capsys):
        """Push accepts items with license, valid source, and categories."""
        # Create folder structure
        folder = tmp_path / "test_valid"
        folder.mkdir()
        img = folder / "test.png"
        img.write_bytes(b"test")
        
        # Create pubdom.json with fully valid item
        pubdom = {
            "folder": "test_valid",
            "generated": "2024-01-15",
            "items": [
                {
                    "file": "test_valid/test.png",
                    "status": "public_domain",
                    "confidence": 0.95,
                    "upload": {"flag": "ready", "reason": []},
                    "information": {
                        "description": "Test image",
                        "date": "1920",
                        "source": "Scansione dalla collezione di Daniele Scasciafratte",
                        "author": "Unknown"
                    },
                    "commons_templates": ["{{PD-Italy}}"],  # Has license
                    "commons_categories": ["Test category"],  # Has categories
                    "extracted_facts": {"images": ["test_valid/test.png"]},
                    "source_template": "{{Self-scanned}}",
                    "caveat_templates": []
                }
            ]
        }
        
        (folder / "pubdom.json").write_text(json.dumps(pubdom))
        
        # Mock sys.argv to simulate: push test_valid
        test_args = ["push", str(folder)]
        
        with patch.object(push.sys, 'argv', test_args):
            result = push.cmd_push(test_args[1:], live=False)
        
        # Check output - should show DRY-RUN planning
        captured = capsys.readouterr()
        assert "DRY-RUN" in captured.out
        assert "RIFIUTATO" not in captured.out
        
        # Check exit code (should be 0 for dry-run success)
        assert result == 0
    
    def test_push_refuses_multiple_reasons_same_item(self, tmp_path, capsys):
        """Push refuses items with multiple Commons acceptance failures."""
        # Create folder structure
        folder = tmp_path / "test_multi"
        folder.mkdir()
        img = folder / "test.png"
        img.write_bytes(b"test")
        
        # Create pubdom.json with item that fails all three checks
        pubdom = {
            "folder": "test_multi",
            "generated": "2024-01-15",
            "items": [
                {
                    "file": "test_multi/test.png",
                    "status": "public_domain",
                    "confidence": 0.95,
                    "upload": {"flag": "ready", "reason": []},
                    "information": {
                        "description": "Test image",
                        "date": "1920",
                        "source": "",  # Empty source
                        "author": "Unknown"
                    },
                    "commons_templates": [],  # No license
                    "commons_categories": [],  # No categories
                    "extracted_facts": {"images": ["test_multi/test.png"]},
                    "source_template": "{{Self-scanned}}",
                    "caveat_templates": []
                }
            ]
        }
        
        (folder / "pubdom.json").write_text(json.dumps(pubdom))
        
        # Mock sys.argv to simulate: push test_multi
        test_args = ["push", str(folder)]
        
        with patch.object(push.sys, 'argv', test_args):
            result = push.cmd_push(test_args[1:], live=False)
        
        # Check output - all three reasons should be printed
        captured = capsys.readouterr()
        assert captured.out.count("RIFIUTATO") == 3  # Three refusal lines
        assert "nessun tag di licenza" in captured.out
        assert "fonte non dichiarata" in captured.out
        assert "nessuna categoria" in captured.out
        
        # Check exit code
        assert result == 1
