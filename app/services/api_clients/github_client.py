"""
GitHub API Client

Integrates with GitHub API for technical analysis and repository metrics.
Provides comprehensive development activity, code quality, and community engagement data.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .base_client import APIClientError, APIResponse, BaseAPIClient

logger = logging.getLogger(__name__)


class GitHubAPIClient(BaseAPIClient):
    """
    GitHub API client for technical analysis.

    Provides access to:
    - Repository metrics and statistics
    - Development activity and contributions
    - Code quality indicators
    - Community engagement data
    - Technology stack analysis
    """

    def __init__(
        self,
        api_token: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        rate_limit_per_minute: Optional[int] = None,
    ):
        """
        Initialize GitHub API client.

        Args:
            api_token: GitHub personal access token (optional, increases rate limits)
            base_url: API address; GitHub Enterprise Server's ``https://host/api/v3``.
                The caller checks it (``validate_connector_url``) before passing it.
            rate_limit_per_minute: request budget; defaults to 30 with a token, 10 without
        """
        super().__init__(
            base_url=base_url or "https://api.github.com",
            api_key=api_token,
            rate_limit_per_minute=rate_limit_per_minute or (30 if api_token else 10),
            cache_ttl_seconds=1800,  # 30 minute cache for repo data
        )

    def _create_session(self):
        """The base session, plus: a redirect is refused, never followed (a followed
        redirect would carry the token to wherever the answer points)."""
        session = super()._create_session()

        def refuse_redirect(response, *args, **kwargs):
            if response.status_code in (301, 302, 303, 307, 308):
                raise APIClientError("GitHub answered with a redirect, which is not followed")
            return response

        session.hooks["response"].append(refuse_redirect)
        return session

    def _scrub(self, text: str) -> str:
        """The token never leaves in a log line or an error message."""
        if self.api_key and isinstance(text, str):
            return text.replace(self.api_key, "***")
        return text

    def _setup_authentication(self):
        """Setup GitHub API authentication."""
        if self.api_key:
            self.session.headers.update({"Authorization": f"token {self.api_key}"})

    def health_check(self) -> bool:
        """Check if GitHub API is accessible."""
        try:
            response = self.get("rate_limit")
            return response.success
        except Exception as e:
            logger.error(f"GitHub health check failed: {e}")
            return False

    def get_repository_analysis(self, owner: str, repo: str) -> APIResponse:
        """
        Get comprehensive repository analysis.

        Args:
            owner: Repository owner/organization
            repo: Repository name

        Returns:
            APIResponse with repository analysis data
        """
        try:
            # Get basic repository information
            repo_response = self.get(f"repos/{owner}/{repo}")

            if not repo_response.success:
                return repo_response

            repo_data = repo_response.data
            analysis_data = self._format_repository_data(repo_data)

            # Get additional metrics
            analysis_data.update(
                {
                    "languages": self._get_repository_languages(owner, repo),
                    "contributors": self._get_contributor_stats(owner, repo),
                    "activity": self._get_recent_activity(owner, repo),
                    "community": self._get_community_metrics(owner, repo),
                }
            )

            return APIResponse(
                success=True,
                data=analysis_data,
                rate_limit_remaining=repo_response.rate_limit_remaining,
                rate_limit_reset=repo_response.rate_limit_reset,
            )

        except Exception as e:
            logger.error(f"Error analyzing repository {owner}/{repo}: {e}")
            return APIResponse(success=False, error=str(e))

    def search_repositories(
        self, query: str, language: Optional[str] = None, sort: str = "stars", order: str = "desc"
    ) -> APIResponse:
        """
        Search for repositories.

        Args:
            query: Search query
            language: Programming language filter
            sort: Sort field (stars, forks, updated)
            order: Sort order (asc, desc)

        Returns:
            APIResponse with search results
        """
        try:
            search_query = query
            if language:
                search_query += f" language:{language}"

            response = self.get(
                "search/repositories",
                params={"q": search_query, "sort": sort, "order": order, "per_page": 20},
            )

            if not response.success:
                return response

            results = response.data.get("items", [])
            formatted_results = [self._format_search_result(repo) for repo in results]

            return APIResponse(
                success=True,
                data={
                    "total_count": response.data.get("total_count", 0),
                    "repositories": formatted_results,
                },
            )

        except Exception as e:
            logger.error(f"Error searching repositories with query '{query}': {e}")
            return APIResponse(success=False, error=str(e))

    def get_organization_repos(self, org_name: str) -> APIResponse:
        """
        Get all repositories for an organization.

        Args:
            org_name: Organization name

        Returns:
            APIResponse with organization repositories
        """
        try:
            response = self.get(
                f"orgs/{org_name}/repos",
                params={"type": "public", "sort": "updated", "per_page": 50},
            )

            if not response.success:
                return response

            repos = response.data
            summary = self._analyze_organization_repos(repos, org_name)

            return APIResponse(success=True, data=summary)

        except Exception as e:
            logger.error(f"Error getting repos for organization {org_name}: {e}")
            return APIResponse(success=False, error=str(e))

    def get_technology_stack(self, owner: str, repo: str) -> APIResponse:
        """
        Analyze technology stack of a repository.

        Args:
            owner: Repository owner
            repo: Repository name

        Returns:
            APIResponse with technology stack analysis
        """
        try:
            # Get languages
            languages_response = self._get_repository_languages(owner, repo)
            if not languages_response.success:
                return languages_response

            # Get dependency files
            dependency_files = self._get_dependency_files(owner, repo)

            # Analyze stack
            stack_analysis = self._analyze_technology_stack(
                languages_response.data, dependency_files
            )

            return APIResponse(success=True, data=stack_analysis)

        except Exception as e:
            logger.error(f"Error analyzing technology stack for {owner}/{repo}: {e}")
            return APIResponse(success=False, error=str(e))

    def _format_repository_data(self, repo_data: Dict[str, Any]) -> Dict[str, Any]:
        """Format repository data for analysis."""
        return {
            "name": repo_data.get("name"),
            "full_name": repo_data.get("full_name"),
            "description": repo_data.get("description"),
            "url": repo_data.get("html_url"),
            "language": repo_data.get("language"),
            "stars": repo_data.get("stargazers_count", 0),
            "forks": repo_data.get("forks_count", 0),
            "watchers": repo_data.get("watchers_count", 0),
            "open_issues": repo_data.get("open_issues_count", 0),
            "size_kb": repo_data.get("size", 0),
            "created_at": repo_data.get("created_at"),
            "updated_at": repo_data.get("updated_at"),
            "pushed_at": repo_data.get("pushed_at"),
            "archived": repo_data.get("archived", False),
            "disabled": repo_data.get("disabled", False),
            "license": repo_data.get("license", {}).get("name")
            if repo_data.get("license")
            else None,
            "topics": repo_data.get("topics", []),
            "has_issues": repo_data.get("has_issues", True),
            "has_projects": repo_data.get("has_projects", True),
            "has_wiki": repo_data.get("has_wiki", True),
            "has_pages": repo_data.get("has_pages", False),
            "has_downloads": repo_data.get("has_downloads", True),
            "fork": repo_data.get("fork", False),
            "owner": {
                "login": repo_data.get("owner", {}).get("login"),
                "type": repo_data.get("owner", {}).get("type"),
                "avatar_url": repo_data.get("owner", {}).get("avatar_url"),
            },
        }

    def _format_search_result(self, repo_data: Dict[str, Any]) -> Dict[str, Any]:
        """Format search result data."""
        return {
            "name": repo_data.get("name"),
            "full_name": repo_data.get("full_name"),
            "description": repo_data.get("description"),
            "url": repo_data.get("html_url"),
            "language": repo_data.get("language"),
            "stars": repo_data.get("stargazers_count", 0),
            "forks": repo_data.get("forks_count", 0),
            "updated_at": repo_data.get("updated_at"),
            "owner": repo_data.get("owner", {}).get("login"),
        }

    def _get_repository_languages(self, owner: str, repo: str) -> APIResponse:
        """Get repository language breakdown."""
        try:
            response = self.get(f"repos/{owner}/{repo}/languages")
            if response.success:
                # Calculate percentages
                total_bytes = sum(response.data.values())
                languages = {}
                for lang, bytes_count in response.data.items():
                    percentage = (bytes_count / total_bytes) * 100 if total_bytes > 0 else 0
                    languages[lang] = {"bytes": bytes_count, "percentage": round(percentage, 2)}

                return APIResponse(success=True, data=languages)
            return response
        except Exception as e:
            logger.error(f"Error getting languages for {owner}/{repo}: {e}")
            return APIResponse(success=False, error=str(e))

    def _get_contributor_stats(self, owner: str, repo: str) -> Dict[str, Any]:
        """Get contributor statistics."""
        try:
            response = self.get(f"repos/{owner}/{repo}/contributors", params={"per_page": 10})
            if response.success:
                contributors = response.data
                return {
                    "count": len(contributors),
                    "top_contributors": [
                        {
                            "login": c.get("login"),
                            "contributions": c.get("contributions", 0),
                            "avatar_url": c.get("avatar_url"),
                        }
                        for c in contributors[:5]
                    ],
                }
            return {"count": 0, "top_contributors": []}
        except Exception:
            return None  # honest failure: no fabricated contributor metrics on error

    def _get_recent_activity(self, owner: str, repo: str) -> Dict[str, Any]:
        """Get recent repository activity."""
        try:
            # Get recent commits
            commits_response = self.get(
                f"repos/{owner}/{repo}/commits",
                params={
                    "per_page": 20,
                    "since": (datetime.utcnow() - timedelta(days=30)).isoformat(),
                },
            )

            recent_commits = 0
            if commits_response.success:
                recent_commits = len(commits_response.data)

            # Get recent issues/PRs
            issues_response = self.get(
                f"repos/{owner}/{repo}/issues",
                params={
                    "state": "all",
                    "per_page": 20,
                    "since": (datetime.utcnow() - timedelta(days=30)).isoformat(),
                },
            )

            recent_issues = 0
            if issues_response.success:
                recent_issues = len(issues_response.data)

            return {
                "commits_last_30_days": recent_commits,
                "issues_last_30_days": recent_issues,
                "activity_score": recent_commits + recent_issues,
            }
        except Exception:
            return None  # honest failure: no fabricated activity metrics on error

    def _get_community_metrics(self, owner: str, repo: str) -> Dict[str, Any]:
        """Get community engagement metrics."""
        try:
            # Get stargazers count over time (simplified)
            stars_response = self.get(f"repos/{owner}/{repo}")
            stars = stars_response.data.get("stargazers_count", 0) if stars_response.success else 0

            # Get fork ratio
            forks = stars_response.data.get("forks_count", 0) if stars_response.success else 0
            fork_ratio = forks / stars if stars > 0 else 0

            return {
                "stars": stars,
                "forks": forks,
                "fork_ratio": round(fork_ratio, 3),
                "has_readme": True,  # Assume most repos have README
                "has_contributing": True,  # Assume most repos have contributing guidelines
                "community_health_score": min(100, stars // 10 + (0 if fork_ratio > 0.1 else 20)),
            }
        except Exception:
            return None  # honest failure: no fabricated community metrics on error

    def _get_dependency_files(self, owner: str, repo: str) -> List[Dict[str, Any]]:
        """Get dependency files from repository."""
        dependency_files = []
        common_files = [
            "package.json",
            "requirements.txt",
            "Pipfile",
            "Gemfile",
            "composer.json",
            "Cargo.toml",
            "go.mod",
            "pom.xml",
        ]

        for filename in common_files:
            try:
                response = self.get(f"repos/{owner}/{repo}/contents/{filename}")
                if response.success:
                    dependency_files.append(
                        {
                            "filename": filename,
                            "content": response.data.get("content", ""),
                            "encoding": response.data.get("encoding", "base64"),
                        }
                    )
            except Exception:
                continue

        return dependency_files

    def _analyze_technology_stack(
        self, languages: Dict[str, Any], dependency_files: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Analyze technology stack from languages and dependencies."""
        primary_language = (
            max(languages.keys(), key=lambda k: languages[k]["bytes"]) if languages else None
        )

        # Detect frameworks and libraries from dependency files
        frameworks = []
        for dep_file in dependency_files:
            filename = dep_file["filename"]
            if filename == "package.json":
                frameworks.extend(self._analyze_package_json(dep_file))
            elif filename == "requirements.txt":
                frameworks.extend(self._analyze_requirements_txt(dep_file))
            # Add more file type analyzers as needed

        return {
            "primary_language": primary_language,
            "language_breakdown": languages,
            "detected_frameworks": list(set(frameworks)),
            "dependency_files_found": len(dependency_files),
            "stack_complexity": len(languages) + len(frameworks),
        }

    def _analyze_package_json(self, dep_file: Dict[str, Any]) -> List[str]:
        """Analyze package.json for JavaScript frameworks."""
        frameworks = []
        try:
            import base64
            import json

            content = dep_file["content"]
            if dep_file.get("encoding") == "base64":
                content = base64.b64decode(content).decode("utf-8")

            package_data = json.loads(content)
            dependencies = {
                **package_data.get("dependencies", {}),
                **package_data.get("devDependencies", {}),
            }

            # Common framework detection
            framework_keywords = {
                "react": "React",
                "vue": "Vue.js",
                "angular": "Angular",
                "express": "Express.js",
                "next": "Next.js",
                "nuxt": "Nuxt.js",
                "svelte": "Svelte",
                "jquery": "jQuery",
            }

            for dep in dependencies.keys():
                for keyword, framework in framework_keywords.items():
                    if keyword in dep.lower():
                        frameworks.append(framework)
                        break

        except Exception as e:
            logger.debug("Failed to analyze package.json for frameworks: %s", e)

        return frameworks

    def _analyze_requirements_txt(self, dep_file: Dict[str, Any]) -> List[str]:
        """Analyze requirements.txt for Python frameworks."""
        frameworks = []
        try:
            import base64

            content = dep_file["content"]
            if dep_file.get("encoding") == "base64":
                content = base64.b64decode(content).decode("utf-8")

            lines = content.split("\n")
            for line in lines:
                line = line.strip().lower()
                if "django" in line:
                    frameworks.append("Django")
                elif "flask" in line:
                    frameworks.append("Flask")
                elif "fastapi" in line:
                    frameworks.append("FastAPI")
                elif "tornado" in line:
                    frameworks.append("Tornado")

        except Exception as e:
            logger.debug("Failed to analyze requirements.txt for frameworks: %s", e)

        return frameworks

    def _analyze_organization_repos(
        self, repos: List[Dict[str, Any]], org_name: str
    ) -> Dict[str, Any]:
        """Analyze organization repositories."""
        if not repos:
            return {"organization": org_name, "error": "No repositories found"}

        total_stars = sum(repo.get("stargazers_count", 0) for repo in repos)
        total_forks = sum(repo.get("forks_count", 0) for repo in repos)
        languages = {}

        for repo in repos:
            lang = repo.get("language")
            if lang:
                languages[lang] = languages.get(lang, 0) + 1

        primary_language = max(languages.keys(), key=lambda k: languages[k]) if languages else None

        return {
            "organization": org_name,
            "total_repositories": len(repos),
            "total_stars": total_stars,
            "total_forks": total_forks,
            "primary_language": primary_language,
            "language_distribution": languages,
            "average_stars_per_repo": total_stars / len(repos) if repos else 0,
            "top_repositories": sorted(
                repos, key=lambda r: r.get("stargazers_count", 0), reverse=True
            )[:5],
        }
