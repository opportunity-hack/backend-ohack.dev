"""
Test cases for cache invalidation in messages_service.

These tests verify that cache is properly invalidated when hackathon data is modified.
"""
import pytest
from unittest.mock import patch, MagicMock, call
from api.messages.messages_service import (
    add_nonprofit_to_hackathon,
    remove_nonprofit_from_hackathon,
    save_hackathon,
    clear_cache
)


class TestCacheInvalidation:
    """Test cases for cache invalidation in hackathon operations."""
    
    @patch('api.messages.messages_service.clear_cache')
    @patch('api.messages.messages_service.get_db')
    def test_add_nonprofit_to_hackathon_clears_cache(self, mock_db, mock_clear_cache):
        """Test that adding a nonprofit to a hackathon clears the cache."""
        # Setup
        mock_hackathon_doc = MagicMock()
        mock_hackathon_data = MagicMock()
        mock_hackathon_data.exists = True
        mock_hackathon_data.to_dict.return_value = {"nonprofits": []}
        mock_hackathon_doc.get.return_value = mock_hackathon_data
        
        mock_nonprofit_doc = MagicMock()
        mock_nonprofit_data = MagicMock()
        mock_nonprofit_data.exists = True
        mock_nonprofit_doc.get.return_value = mock_nonprofit_data
        
        mock_collection = MagicMock()
        mock_collection.document.side_effect = lambda doc_id: (
            mock_hackathon_doc if doc_id == "hackathon123" else mock_nonprofit_doc
        )
        mock_db.return_value.collection.return_value = mock_collection
        
        json_data = {
            "hackathonId": "hackathon123",
            "nonprofitId": "nonprofit456"
        }
        
        # Execute
        result = add_nonprofit_to_hackathon(json_data)
        
        # Assert
        assert result["message"] == "Nonprofit added to hackathon"
        mock_clear_cache.assert_called_once()
    
    @patch('api.messages.messages_service.clear_cache')
    @patch('api.messages.messages_service.get_db')
    def test_remove_nonprofit_from_hackathon_clears_cache(self, mock_db, mock_clear_cache):
        """Test that removing a nonprofit from a hackathon clears the cache."""
        # Setup
        mock_nonprofit_ref = MagicMock()
        mock_nonprofit_ref.id = "nonprofit456"
        
        mock_hackathon_doc = MagicMock()
        mock_hackathon_data = MagicMock()
        mock_hackathon_data.to_dict.return_value = {"nonprofits": [mock_nonprofit_ref]}
        mock_hackathon_doc.get.return_value = mock_hackathon_data
        
        mock_nonprofit_doc = MagicMock()
        
        mock_collection = MagicMock()
        mock_collection.document.side_effect = lambda doc_id: (
            mock_hackathon_doc if doc_id == "hackathon123" else mock_nonprofit_doc
        )
        mock_db.return_value.collection.return_value = mock_collection
        
        json_data = {
            "hackathonId": "hackathon123",
            "nonprofitId": "nonprofit456"
        }
        
        # Execute
        result = remove_nonprofit_from_hackathon(json_data)
        
        # Assert
        assert result["message"] == "Nonprofit removed from hackathon"
        mock_clear_cache.assert_called_once()
    
    @patch('api.messages.messages_service.clear_cache')
    @patch('api.messages.messages_service.get_db')
    @patch('api.messages.messages_service.validate_hackathon_data')
    def test_save_hackathon_clears_cache(self, mock_validate, mock_db, mock_clear_cache):
        """Test that saving a hackathon clears the cache."""
        # Setup
        mock_db_instance = MagicMock()
        mock_db.return_value = mock_db_instance
        mock_validate.return_value = None
        
        # Mock transaction
        mock_transaction = MagicMock()
        mock_db_instance.transaction.return_value = mock_transaction
        
        # Mock collection and document
        mock_hackathon_ref = MagicMock()
        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_hackathon_ref
        mock_db_instance.collection.return_value = mock_collection
        
        json_data = {
            "title": "Test Hackathon",
            "description": "Test Description",
            "location": "Test Location",
            "start_date": "2024-01-01",
            "end_date": "2024-01-02",
            "type": "virtual",
            "image_url": "https://example.com/image.png",
            "event_id": "event123"
        }
        
        # Execute
        result = save_hackathon(json_data, "user123")
        
        # Assert
        assert result.text == "Saved Hackathon"
        mock_clear_cache.assert_called_once()
    
    @patch('api.messages.messages_service.doc_to_json')
    @patch('api.messages.messages_service.get_single_hackathon_event')
    @patch('api.messages.messages_service.get_single_hackathon_id')
    @patch('api.messages.messages_service.get_hackathon_list')
    def test_clear_cache_clears_all_caches(
        self, 
        mock_get_hackathon_list,
        mock_get_single_hackathon_id,
        mock_get_single_hackathon_event,
        mock_doc_to_json
    ):
        """Test that clear_cache clears all hackathon-related caches."""
        # Execute
        clear_cache()
        
        # Assert - verify that cache_clear was called on all cached functions
        mock_doc_to_json.cache_clear.assert_called_once()
        mock_get_single_hackathon_event.cache_clear.assert_called_once()
        mock_get_single_hackathon_id.cache_clear.assert_called_once()
        mock_get_hackathon_list.cache_clear.assert_called_once()
