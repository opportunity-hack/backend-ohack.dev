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
    
    @patch('api.leaderboard.leaderboard_service.get_hackathon_by_event_id')
    def test_get_github_organizations(self, mock_get_hackathon):
        """Test retrieving GitHub organizations."""
        # Setup mock for valid event ID
        mock_get_hackathon.return_value = {
            "title": "Arizona Opportunity Hackathon 2024",
            "github_org": "2024-Arizona-Opportunity-Hack",
            "event_id": "2024_fall"
        }
        
        # Test with valid event ID
        result = get_github_organizations("2024_fall")
        assert "github_organizations" in result
        assert len(result["github_organizations"]) == 1
        assert result["github_organizations"][0]["name"] == "2024-Arizona-Opportunity-Hack"
        
        # Setup mock for invalid event ID
        mock_get_hackathon.return_value = None
        
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
    
    @patch('api.leaderboard.leaderboard_service.get_hackathon_by_event_id')
    @patch('api.leaderboard.leaderboard_service.get_all_repos')
    @patch('api.leaderboard.leaderboard_service.get_db')
    def test_get_github_contributors(self, mock_get_db, mock_get_all_repos, mock_get_hackathon):
        """Test retrieving GitHub contributors."""
        # Setup mocks
        mock_get_all_repos.return_value = mock_repos()
        
        # Mock hackathon data
        mock_get_hackathon.return_value = {
            "title": "Arizona Opportunity Hackathon 2024",
            "github_org": "2024-Arizona-Opportunity-Hack",
            "event_id": "2024_fall"
        }
        
        # Mock Firestore document reference and query results
        mock_collection = MagicMock()
        mock_document = MagicMock()
        mock_subcollection = MagicMock()
        mock_subdocument = MagicMock()
        mock_subsubcollection = MagicMock()
        mock_collection_group = MagicMock()
        mock_where = MagicMock()
        
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
        
        # Mock references for parent relationships
        mock_repo_ref = MagicMock()
        mock_org_ref = MagicMock()
        mock_contributor_doc.reference.parent.parent = mock_repo_ref
        mock_repo_ref.parent.parent = mock_org_ref
        mock_repo_ref.id = "test-repo"
        mock_org_ref.id = "test-org"
        
        # Setup the Firestore query chain
        mock_get_db.return_value = mock_collection
        mock_collection.collection_group.return_value = mock_collection_group
        mock_collection_group.where.return_value = mock_where
        mock_where.stream.return_value = [mock_contributor_doc]
        
        # Test with valid event ID
        result = get_github_contributors("2024_fall")
        assert "github_contributors" in result
        assert len(result["github_contributors"]) == 1
        assert result["github_contributors"][0]["login"] == "UmangBid"
        
        # Test with invalid event ID (no hackathon found)
        mock_get_hackathon.return_value = None
        result = get_github_contributors("invalid_id")
        assert "github_contributors" in result
        assert len(result["github_contributors"]) == 0
        
        # Test with exception
        mock_get_hackathon.return_value = {
            "title": "Arizona Opportunity Hackathon 2024",
            "github_org": "2024-Arizona-Opportunity-Hack",
            "event_id": "2024_fall"
        }
        mock_get_db.side_effect = Exception("Test error")
        result = get_github_contributors("2024_fall")
        assert "github_contributors" in result
        assert len(result["github_contributors"]) == 0
    
    @patch('api.leaderboard.leaderboard_service.get_github_organizations')
    @patch('api.leaderboard.leaderboard_service.get_github_repositories')
    @patch('api.leaderboard.leaderboard_service.get_github_contributors')
    @patch('api.leaderboard.leaderboard_service.get_hackathon_by_event_id')
    def test_get_github_leaderboard(self, mock_get_hackathon, mock_contributors, mock_repos_func, mock_orgs):
        """Test retrieving the complete GitHub leaderboard."""
        # Setup mocks
        mock_orgs.return_value = {
            "github_organizations": [{"__id__": "test-org", "name": "test-org"}]
        }
        mock_repos_func.return_value = {
            "github_repositories": [{"__id__": "test-repo", "name": "test-repo"}]
        }
        mock_contributors.return_value = {
            "github_contributors": [
                {
                    "__id__": "test-user", 
                    "login": "test-user",
                    "commits": 15,
                    "pull_requests": {"merged": 5}
                }
            ]
        }
        mock_get_hackathon.return_value = {
            "title": "Test Hackathon",
            "github_org": "test-org",
            "event_id": "2024_fall"
        }
        
        # Test the combined function
        result = get_github_leaderboard("2024_fall")
        assert "github_organizations" in result
        assert "github_repositories" in result
        assert "github_contributors" in result
        assert "eventName" in result
        assert "githubOrg" in result
        assert "generalStats" in result
        assert "individualAchievements" in result
        assert "teamAchievements" in result
        assert len(result["github_organizations"]) == 1
        assert len(result["github_repositories"]) == 1
        assert len(result["github_contributors"]) == 1
        assert result["eventName"] == "Test Hackathon"
        assert result["githubOrg"] == "test-org"
        
        # Check that stats are calculated correctly
        assert any(stat["stat"] == "GitHub Commits" and stat["value"] == 15 for stat in result["generalStats"])
        assert any(stat["stat"] == "Pull Requests" and stat["value"] == 5 for stat in result["generalStats"])
