from unittest.mock import patch, MagicMock
import pytest

from api.leaderboard.leaderboard_service import (
    get_github_organizations,
    get_github_repositories,
    get_github_contributors,
    get_github_leaderboard
)

@pytest.fixture
def mock_repos():
    return [
        {
            "repo_name": "Alpaca-3--LiminalWorks-LiminalWorksTheoryofChangeGPTAssistant",
            "full_url": "https://github.com/org/repo",
            "description": "Repo description",
            "owners": ["UmangBid", "dheerajkallakuri"],
            "created_at": "2025-01-09T16:21:00.000Z",
            "updated_at": "2025-01-09T16:22:00.000Z"
        }
    ]

class TestLeaderboardService:
    """Test cases for leaderboard service functions."""
    
    def test_get_github_organizations(self):
        """Test retrieving GitHub organizations."""
        # Test with valid event ID
        result = get_github_organizations("2024_fall")
        assert "github_organizations" in result
        assert len(result["github_organizations"]) == 1
        assert result["github_organizations"][0]["name"] == "2024-Arizona-Opportunity-Hack"
        
        # Test with invalid event ID
        result = get_github_organizations("invalid_id")
        assert "github_organizations" in result
        assert len(result["github_organizations"]) == 0
    
    @patch('api.leaderboard.leaderboard_service.get_all_repos')
    def test_get_github_repositories(self, mock_get_all_repos):
        """Test retrieving GitHub repositories."""
        # Setup mock
        mock_get_all_repos.return_value = mock_repos()
        
        # Test with valid event ID
        result = get_github_repositories("2024_fall")
        assert "github_repositories" in result
        assert len(result["github_repositories"]) == 1
        assert result["github_repositories"][0]["name"] == mock_repos()[0]["repo_name"]
        
        # Test with error
        mock_get_all_repos.side_effect = ValueError("Test error")
        result = get_github_repositories("2024_fall")
        assert "github_repositories" in result
        assert len(result["github_repositories"]) == 0
    
    @patch('api.leaderboard.leaderboard_service.get_all_repos')
    @patch('api.leaderboard.leaderboard_service.get_db')
    def test_get_github_contributors(self, mock_get_db, mock_get_all_repos):
        """Test retrieving GitHub contributors."""
        # Setup mocks
        mock_get_all_repos.return_value = mock_repos()
        
        # Mock Firestore document reference and query results
        mock_collection = MagicMock()
        mock_document = MagicMock()
        mock_subcollection = MagicMock()
        mock_subdocument = MagicMock()
        mock_subsubcollection = MagicMock()
        
        # Mock contributor document
        mock_contributor_doc = MagicMock()
        mock_contributor_doc.id = "UmangBid"
        mock_contributor_doc.to_dict.return_value = {
            "additions": 0,
            "commits": 15,
            "deletions": 0,
            "issues": {"closed": 0, "open": 0, "total": 0},
            "login": "UmangBid",
            "pull_requests": {"closed": 2, "merged": 10, "open": 0, "total": 12},
            "reviews": 0,
            "timestamp": "__Timestamp__2025-01-09T16:22:06.849Z"
        }
        
        # Setup the Firestore query chain
        mock_get_db.return_value = mock_collection
        mock_collection.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_document
        mock_document.collection.return_value = mock_subcollection
        mock_subcollection.document.return_value = mock_subdocument
        mock_subdocument.collection.return_value = mock_subsubcollection
        mock_subsubcollection.stream.return_value = [mock_contributor_doc]
        
        # Test with valid event ID
        result = get_github_contributors("2024_fall")
        assert "github_contributors" in result
        assert len(result["github_contributors"]) == 1
        assert result["github_contributors"][0]["login"] == "UmangBid"
        
        # Test with exception
        mock_get_all_repos.side_effect = Exception("Test error")
        result = get_github_contributors("2024_fall")
        assert "github_contributors" in result
        assert len(result["github_contributors"]) == 0
    
    @patch('api.leaderboard.leaderboard_service.get_github_organizations')
    @patch('api.leaderboard.leaderboard_service.get_github_repositories')
    @patch('api.leaderboard.leaderboard_service.get_github_contributors')
    def test_get_github_leaderboard(self, mock_contributors, mock_repos_func, mock_orgs):
        """Test retrieving the complete GitHub leaderboard."""
        # Setup mocks
        mock_orgs.return_value = {
            "github_organizations": [{"__id__": "test-org", "name": "test-org"}]
        }
        mock_repos_func.return_value = {
            "github_repositories": [{"__id__": "test-repo", "name": "test-repo"}]
        }
        mock_contributors.return_value = {
            "github_contributors": [{"__id__": "test-user", "login": "test-user"}]
        }
        
        # Test the combined function
        result = get_github_leaderboard("2024_fall")
        assert "github_organizations" in result
        assert "github_repositories" in result
        assert "github_contributors" in result
        assert len(result["github_organizations"]) == 1
        assert len(result["github_repositories"]) == 1
        assert len(result["github_contributors"]) == 1
