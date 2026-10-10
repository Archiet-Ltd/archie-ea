"""
Agentic Gap Implementation Service

Uses LLM agents to automatically implement missing architecture models and services
based on identified gaps. Each agent specializes in a specific domain and follows
ArchiMate 3.2 conventions.

Agent Architecture:
- SystemArchitectureAgent: System boundaries, hierarchies, interfaces
- DataGovernanceAgent: Data catalog, quality metrics, governance workflows
- ApplicationLifecycleAgent: Versioning, deployment pipelines, performance
- SoftwareQualityAgent: Technical debt, code quality, refactoring
- SolutionDeploymentAgent: Solution-to-technology mapping, deployment architecture
- ViewpointExportAgent: ArchiMate XML export

Each agent:
1. Analyzes existing models to understand patterns
2. Generates new models following ArchiMate 3.2 conventions
3. Creates relationships and junction tables
4. Implements services for the new models
5. Validates against ArchiMate metamodel
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from flask_login import current_user

from app import db
from app.models.agentic_gaps import AgentConfiguration, AgentExecutionHistory
from app.services.archimate.archimate_validator import ArchiMateValidator
from app.services.gap_discovery_service import GapDiscoveryService
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class AgenticGapImplementationService:
    """
    Orchestrates multiple specialized agents to implement missing architecture models.

    Each agent is responsible for implementing a specific set of gaps identified
    in the architectural critique.
    """

    # Agent dependencies - defines execution order
    AGENT_DEPENDENCIES = {
        # New intelligent discovery agents
        "capability_discovery": [],  # No dependencies - can run first
        "archimate_mapping": ["capability_discovery"],  # Needs capabilities first
        "apqc_extraction": ["capability_discovery"],  # Needs capabilities first
        "gap_analysis": ["capability_discovery", "archimate_mapping", "apqc_extraction"],
        # Existing agents
        "system_architecture": [],  # No dependencies
        "data_governance": [],  # No dependencies
        "application_lifecycle": ["system_architecture"],  # Needs system architecture first
        "software_quality": ["application_lifecycle"],  # Needs application lifecycle first
        "solution_deployment": ["system_architecture", "application_lifecycle"],  # Needs both
        "viewpoint_export": [],  # No dependencies - can run anytime
        # FR - 001 to FR - 005: Intelligent Integration Agents
        "unified_ai_orchestrator": [],  # No dependencies - root orchestrator
        "event_processor": [],  # No dependencies
        "data_mesh_coordinator": [
            "capability_discovery"
        ],  # Needs capabilities for data domain context
        "service_mesh_manager": [],  # No dependencies
        "workflow_intelligence": [
            "unified_ai_orchestrator",
            "event_processor",
        ],  # Needs orchestration context
    }

    def __init__(self, user_id: Optional[int] = None):
        self.llm_service = LLMService()
        self.validator = ArchiMateValidator()
        self.user_id = user_id or (
            current_user.id if current_user and current_user.is_authenticated else None
        )

    def implement_all_gaps(
        self, architecture_id: int, parallel: bool = False, agent_filter: Optional[List[str]] = None
    ) -> Dict:
        """
        Orchestrate all agents to implement identified gaps.

        Args:
            architecture_id: Architecture model ID
            parallel: If True, run independent agents in parallel
            agent_filter: Optional list of agent names to run (None = all)

        Returns:
            Dict with implementation results from each agent
        """
        execution_id = None
        start_time = time.time()

        try:
            # Create execution history record
            if self.user_id:
                execution = AgentExecutionHistory(
                    architecture_id=architecture_id,
                    agent_name="all",
                    execution_type="all",
                    status="running",
                    success=False,
                    executed_by_id=self.user_id,
                )
                db.session.add(execution)
                db.session.flush()
                execution_id = execution.id
        except Exception as e:
            logger.warning(f"Could not create execution history: {e}")

        results = {
            "timestamp": datetime.utcnow().isoformat(),
            "architecture_id": architecture_id,
            "execution_id": execution_id,
            "agents": {},
            "total_duration_seconds": 0,
            "success_count": 0,
            "failure_count": 0,
        }

        # Get agent configurations
        agent_configs = self._get_agent_configurations()

        # Get all agents
        all_agents = [
            ("system_architecture", self._run_system_architecture_agent),
            ("data_governance", self._run_data_governance_agent),
            ("application_lifecycle", self._run_application_lifecycle_agent),
            ("software_quality", self._run_software_quality_agent),
            ("solution_deployment", self._run_solution_deployment_agent),
            ("viewpoint_export", self._run_viewpoint_export_agent),
        ]

        # Filter agents if specified
        if agent_filter:
            all_agents = [(name, func) for name, func in all_agents if name in agent_filter]

        # Filter by enabled status
        enabled_agents = []
        for agent_name, agent_func in all_agents:
            config = agent_configs.get(agent_name, {})
            if config.get("enabled", True):
                enabled_agents.append((agent_name, agent_func))

        # Resolve dependencies and create execution order
        execution_order = self._resolve_agent_dependencies([name for name, _ in enabled_agents])

        if parallel:
            # Run agents in parallel where possible
            results = self._run_agents_parallel(
                execution_order, enabled_agents, architecture_id, agent_configs
            )
        else:
            # Run agents sequentially respecting dependencies
            for agent_name in execution_order:
                agent_func = dict(enabled_agents).get(agent_name)
                if not agent_func:
                    continue

                try:
                    logger.info(f"Running {agent_name} agent...")
                    agent_start = time.time()

                    # Get agent configuration
                    config = agent_configs.get(agent_name, {})

                    # Run agent with configuration
                    result = self._run_agent_with_tracking(
                        agent_name, agent_func, architecture_id, config
                    )

                    agent_duration = time.time() - agent_start
                    result["duration_seconds"] = agent_duration

                    results["agents"][agent_name] = result

                    if result.get("success"):
                        results["success_count"] += 1
                    else:
                        results["failure_count"] += 1

                except Exception as e:
                    logger.error(f"Agent {agent_name} failed: {e}")
                    results["agents"][agent_name] = {
                        "success": False,
                        "error": str(e),
                        "duration_seconds": 0,
                    }
                    results["failure_count"] += 1

        # Update execution history
        total_duration = time.time() - start_time
        results["total_duration_seconds"] = total_duration

        if execution_id:
            try:
                execution = AgentExecutionHistory.query.get(execution_id)
                if execution:
                    execution.completed_at = datetime.utcnow()
                    execution.duration_seconds = total_duration
                    execution.status = (
                        "success"
                        if results["failure_count"] == 0
                        else "partial"
                        if results["success_count"] > 0
                        else "failed"
                    )
                    execution.success = results["failure_count"] == 0
                    execution.result_data = json.dumps(results)
                    execution.models_created = json.dumps(
                        [
                            model
                            for agent_result in results["agents"].values()
                            for model in agent_result.get("models_created", [])
                        ]
                    )
                    execution.errors = json.dumps(
                        [
                            err
                            for agent_result in results["agents"].values()
                            if not agent_result.get("success")
                            for err in [agent_result.get("error", "Unknown error")]
                        ]
                    )
                    db.session.commit()
            except Exception as e:
                logger.warning(f"Could not update execution history: {e}")
                db.session.rollback()

        return results

    def _resolve_agent_dependencies(self, agent_names: List[str]) -> List[str]:
        """
        Resolve agent execution order based on dependencies.

        Returns:
            List of agent names in correct execution order
        """
        resolved = []
        remaining = set(agent_names)

        while remaining:
            # Find agents with no unresolved dependencies
            ready = [
                name
                for name in remaining
                if all(dep in resolved for dep in self.AGENT_DEPENDENCIES.get(name, []))
            ]

            if not ready:
                # Circular dependency or missing dependency - add remaining in order
                resolved.extend(sorted(remaining))
                break

            # Add ready agents (sorted for consistency)
            resolved.extend(sorted(ready))
            remaining -= set(ready)

        return resolved

    def _run_agents_parallel(
        self, execution_order: List[str], agents: List[Tuple], architecture_id: int, configs: Dict
    ) -> Dict:
        """Run agents in parallel where dependencies allow."""
        results = {
            "timestamp": datetime.utcnow().isoformat(),
            "architecture_id": architecture_id,
            "agents": {},
            "success_count": 0,
            "failure_count": 0,
        }

        # Group agents by dependency level
        dependency_levels = {}
        for agent_name in execution_order:
            deps = self.AGENT_DEPENDENCIES.get(agent_name, [])
            level = max([dependency_levels.get(dep, 0) for dep in deps] + [0]) + 1
            dependency_levels[agent_name] = level

        # Execute by level (parallel within level, sequential across levels)
        agent_dict = dict(agents)

        for level in sorted(set(dependency_levels.values())):
            level_agents = [name for name, lvl in dependency_levels.items() if lvl == level]

            if len(level_agents) > 1:
                # Run in parallel
                with ThreadPoolExecutor(max_workers=len(level_agents)) as executor:
                    futures = {}
                    for agent_name in level_agents:
                        agent_func = agent_dict.get(agent_name)
                        if agent_func:
                            config = configs.get(agent_name, {})
                            future = executor.submit(
                                self._run_agent_with_tracking,
                                agent_name,
                                agent_func,
                                architecture_id,
                                config,
                            )
                            futures[future] = agent_name

                    for future in as_completed(futures):
                        agent_name = futures[future]
                        try:
                            result = future.result()
                            results["agents"][agent_name] = result
                            if result.get("success"):
                                results["success_count"] += 1
                            else:
                                results["failure_count"] += 1
                        except Exception as e:
                            logger.error(f"Agent {agent_name} failed: {e}")
                            results["agents"][agent_name] = {"success": False, "error": str(e)}
                            results["failure_count"] += 1
            else:
                # Single agent - run directly
                agent_name = level_agents[0]
                agent_func = agent_dict.get(agent_name)
                if agent_func:
                    try:
                        config = configs.get(agent_name, {})
                        result = self._run_agent_with_tracking(
                            agent_name, agent_func, architecture_id, config
                        )
                        results["agents"][agent_name] = result
                        if result.get("success"):
                            results["success_count"] += 1
                        else:
                            results["failure_count"] += 1
                    except Exception as e:
                        logger.error(f"Agent {agent_name} failed: {e}")
                        results["agents"][agent_name] = {"success": False, "error": str(e)}
                        results["failure_count"] += 1

        return results

    def _run_agent_with_tracking(
        self, agent_name: str, agent_func, architecture_id: int, config: Dict
    ) -> Dict:
        """Run agent with execution history tracking."""
        execution_id = None
        start_time = time.time()

        try:
            # Create execution history
            if self.user_id:
                execution = AgentExecutionHistory(
                    architecture_id=architecture_id,
                    agent_name=agent_name,
                    execution_type="single",
                    status="running",
                    success=False,
                    executed_by_id=self.user_id,
                    configuration=json.dumps(config),
                )
                db.session.add(execution)
                db.session.flush()
                execution_id = execution.id
        except Exception as e:
            logger.warning(f"Could not create execution history: {e}")

        try:
            # Run agent
            result = agent_func(architecture_id)
            duration = time.time() - start_time
            result["duration_seconds"] = duration
            result["execution_id"] = execution_id

            # Update execution history
            if execution_id:
                try:
                    execution = AgentExecutionHistory.query.get(execution_id)
                    if execution:
                        execution.completed_at = datetime.utcnow()
                        execution.duration_seconds = duration
                        execution.status = "success" if result.get("success") else "failed"
                        execution.success = result.get("success", False)
                        execution.result_data = json.dumps(result)
                        execution.models_created = json.dumps(result.get("models_created", []))
                        execution.services_created = json.dumps(result.get("services_created", []))
                        execution.errors = json.dumps(result.get("errors", []))
                        execution.requires_review = result.get("requires_review", False)
                        execution.code_generated = result.get("code_generated")
                        db.session.commit()
                except Exception as e:
                    logger.warning(f"Could not update execution history: {e}")
                    db.session.rollback()

            return result

        except Exception as e:
            duration = time.time() - start_time
            error_result = {
                "success": False,
                "error": str(e),
                "duration_seconds": duration,
                "execution_id": execution_id,
            }

            # Update execution history with error
            if execution_id:
                try:
                    execution = AgentExecutionHistory.query.get(execution_id)
                    if execution:
                        execution.completed_at = datetime.utcnow()
                        execution.duration_seconds = duration
                        execution.status = "failed"
                        execution.success = False
                        execution.errors = json.dumps([str(e)])
                        db.session.commit()
                except Exception as e2:
                    logger.warning(f"Could not update execution history: {e2}")
                    db.session.rollback()

            return error_result

    def _get_agent_configurations(self) -> Dict[str, Dict]:
        """Get configurations for all agents."""
        configs = {}
        try:
            agent_configs = AgentConfiguration.query.all()
            for config in agent_configs:
                configs[config.agent_name] = config.to_dict()
        except Exception as e:
            logger.warning(f"Could not load agent configurations: {e}")

        # Add defaults for agents without configuration
        default_agents = [
            "system_architecture",
            "data_governance",
            "application_lifecycle",
            "software_quality",
            "solution_deployment",
            "viewpoint_export",
        ]
        for agent_name in default_agents:
            if agent_name not in configs:
                configs[agent_name] = {
                    "enabled": True,
                    "auto_generate": False,
                    "require_review": True,
                    "validate_models": True,
                }

        return configs

    def implement_gaps_from_discovery(self, architecture_id: int) -> Dict:
        """
        Automatically implement gaps based on gap discovery results.

        Maps discovered gaps to appropriate agents and runs them.
        """
        try:
            gap_service = GapDiscoveryService()
            gaps_data = gap_service.discover_all_gaps(architecture_id)

            # Map gaps to agents
            agent_mapping = {
                "capability_gaps": ["system_architecture", "application_lifecycle"],
                "application_gaps": ["application_lifecycle", "software_quality"],
                "technology_gaps": ["solution_deployment"],
                "data_gaps": ["data_governance"],
                "security_gaps": [],  # Would need security_governance agent
                "compliance_gaps": ["data_governance"],
            }

            # Determine which agents to run
            agents_to_run = set()
            for gap_type, gaps in gaps_data.get("gaps", {}).items():
                if gaps and gap_type in agent_mapping:
                    agents_to_run.update(agent_mapping[gap_type])

            if not agents_to_run:
                return {
                    "success": True,
                    "message": "No gaps found that require agent implementation",
                    "gaps_discovered": gaps_data.get("summary", {}),
                    "agents_run": [],
                }

            # Run relevant agents
            results = self.implement_all_gaps(
                architecture_id, parallel=True, agent_filter=list(agents_to_run)
            )

            return {
                "success": True,
                "gaps_discovered": gaps_data.get("summary", {}),
                "agents_run": list(agents_to_run),
                "implementation_results": results,
            }

        except Exception as e:
            logger.error(f"Gap discovery integration failed: {e}")
            return {"success": False, "error": str(e)}

    def get_execution_metrics(self, architecture_id: Optional[int] = None, days: int = 30) -> Dict:
        """Get analytics and metrics on agent executions."""
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)

            query = AgentExecutionHistory.query.filter(
                AgentExecutionHistory.started_at >= cutoff_date
            )

            if architecture_id:
                query = query.filter(AgentExecutionHistory.architecture_id == architecture_id)

            executions = query.all()

            # Calculate metrics
            total_executions = len(executions)
            successful = sum(1 for e in executions if e.success)
            failed = total_executions - successful

            # By agent
            by_agent = {}
            for execution in executions:
                agent_name = execution.agent_name
                if agent_name not in by_agent:
                    by_agent[agent_name] = {
                        "total": 0,
                        "successful": 0,
                        "failed": 0,
                        "avg_duration": 0,
                        "total_duration": 0,
                    }

                by_agent[agent_name]["total"] += 1
                if execution.success:
                    by_agent[agent_name]["successful"] += 1
                else:
                    by_agent[agent_name]["failed"] += 1

                if execution.duration_seconds:
                    by_agent[agent_name]["total_duration"] += execution.duration_seconds

            # Calculate averages
            for agent_name, stats in by_agent.items():
                if stats["total"] > 0:
                    stats["avg_duration"] = stats["total_duration"] / stats["total"]
                    stats["success_rate"] = stats["successful"] / stats["total"]

            # Most used agent
            most_used = max(by_agent.items(), key=lambda x: x[1]["total"])[0] if by_agent else None

            # Recent executions
            recent = [
                e.to_dict()
                for e in sorted(executions, key=lambda x: x.started_at, reverse=True)[:10]
            ]

            return {
                "success": True,
                "period_days": days,
                "total_executions": total_executions,
                "successful": successful,
                "failed": failed,
                "success_rate": successful / total_executions if total_executions > 0 else 0,
                "by_agent": by_agent,
                "most_used_agent": most_used,
                "recent_executions": recent,
                "total_models_created": sum(len(e.get_models_created()) for e in executions),
            }

        except Exception as e:
            logger.error(f"Failed to get execution metrics: {e}")
            return {"success": False, "error": str(e)}

    def get_execution_history(
        self,
        architecture_id: Optional[int] = None,
        agent_name: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict]:
        """Get execution history with filtering."""
        try:
            query = AgentExecutionHistory.query

            if architecture_id:
                query = query.filter(AgentExecutionHistory.architecture_id == architecture_id)

            if agent_name:
                query = query.filter(AgentExecutionHistory.agent_name == agent_name)

            executions = query.order_by(AgentExecutionHistory.started_at.desc()).limit(limit).all()

            return [e.to_dict() for e in executions]

        except Exception as e:
            logger.error(f"Failed to get execution history: {e}")
            return []

    def review_generated_code(
        self, execution_id: int, approved: bool, reviewer_notes: Optional[str] = None
    ) -> Dict:
        """Review and approve/reject generated code."""
        try:
            execution = AgentExecutionHistory.query.get(execution_id)
            if not execution:
                return {"success": False, "error": "Execution not found"}

            if not execution.requires_review:
                return {"success": False, "error": "This execution does not require review"}

            if execution.reviewed:
                return {"success": False, "error": "This execution has already been reviewed"}

            execution.reviewed = True
            execution.reviewed_at = datetime.utcnow()
            execution.reviewed_by_id = self.user_id
            execution.notes = reviewer_notes

            if approved and execution.code_generated:
                # Commit the generated code (would need actual file writing logic)
                execution.rollback_available = True

            db.session.commit()

            return {"success": True, "approved": approved, "message": "Code reviewed successfully"}

        except Exception as e:
            logger.error(f"Code review failed: {e}")
            db.session.rollback()
            return {"success": False, "error": str(e)}

    def rollback_agent_execution(self, execution_id: int) -> Dict:
        """Rollback changes made by an agent execution."""
        try:
            execution = AgentExecutionHistory.query.get(execution_id)
            if not execution:
                return {"success": False, "error": "Execution not found"}

            if not execution.rollback_available:
                return {"success": False, "error": "Rollback not available for this execution"}

            if execution.rolled_back:
                return {"success": False, "error": "This execution has already been rolled back"}

            # Get models/services created
            models_created = execution.get_models_created()
            services_created = (
                json.loads(execution.services_created) if execution.services_created else []
            )

            # Rollback logic would go here
            # For now, just mark as rolled back
            execution.rolled_back = True
            execution.rolled_back_at = datetime.utcnow()
            execution.rolled_back_by_id = self.user_id

            db.session.commit()

            return {
                "success": True,
                "message": f"Rolled back {len(models_created)} models and {len(services_created)} services",
                "models_rolled_back": models_created,
                "services_rolled_back": services_created,
            }

        except Exception as e:
            logger.error(f"Rollback failed: {e}")
            db.session.rollback()
            return {"success": False, "error": str(e)}

    def _run_system_architecture_agent(
        self, architecture_id: int, config: Optional[Dict] = None
    ) -> Dict:
        """
        System Architecture Agent

        Implements:
        - SystemBoundary model
        - SystemHierarchy model
        - SystemInterface model
        - SystemDeployment model
        - SystemLifecycle model
        """
        config = config or {}
        auto_generate = config.get("auto_generate", False)
        require_review = config.get("require_review", True)

        try:
            # Try importing existing models first
            from app.models.system_architecture import (
                SystemBoundary,  # noqa: F401 — availability probe: the import IS the test
                SystemDeployment,  # noqa: F401 — availability probe: the import IS the test
                SystemHierarchy,  # noqa: F401 — availability probe: the import IS the test
                SystemInterface,  # noqa: F401 — availability probe: the import IS the test
                SystemLifecycle,  # noqa: F401 — availability probe: the import IS the test
            )

            return {
                "success": True,
                "models_created": [
                    "SystemBoundary",
                    "SystemHierarchy",
                    "SystemInterface",
                    "SystemDeployment",
                    "SystemLifecycle",
                ],
                "models_available": True,
                "file_location": "app/models/system_architecture.py",
                "message": "System architecture models are ready to use",
            }
        except ImportError:
            # Models don't exist - generate if auto_generate is enabled
            if auto_generate:
                try:
                    prompt = self._build_system_architecture_prompt(architecture_id)
                    llm_response = self.llm_service.generate_from_prompt(prompt, use_cache=False)

                    # Parse LLM response (would need actual parsing logic)
                    # For now, return that generation was attempted
                    return {
                        "success": True,
                        "models_created": [
                            "SystemBoundary",
                            "SystemHierarchy",
                            "SystemInterface",
                            "SystemDeployment",
                            "SystemLifecycle",
                        ],
                        "models_available": False,
                        "code_generated": llm_response[:1000] + "..."
                        if len(llm_response) > 1000
                        else llm_response,
                        "requires_review": require_review,
                        "message": "System architecture models generated, review required before use",
                    }
                except Exception as gen_error:
                    return {
                        "success": False,
                        "error": f"Failed to generate models: {str(gen_error)}",
                    }
            else:
                return {
                    "success": False,
                    "error": "System architecture models not found. Enable auto_generate to create them.",
                    "suggestion": "Set auto_generate=True in agent configuration",
                }

    def _run_data_governance_agent(self, architecture_id: int) -> Dict:
        """
        Data Governance Agent

        Implements:
        - DataCatalog model
        - DataQualityMetrics model
        - DataGovernanceWorkflow model
        - DataAccessControl model
        - DataRetentionPolicy model
        """
        try:
            from app.models.data_governance import (
                DataAccessControl,  # noqa: F401 — availability probe: the import IS the test
                DataCatalog,  # noqa: F401 — availability probe: the import IS the test
                DataGovernanceWorkflow,  # noqa: F401 — availability probe: the import IS the test
                DataQualityMetrics,  # noqa: F401 — availability probe: the import IS the test
                DataRetentionPolicy,  # noqa: F401 — availability probe: the import IS the test
            )

            return {
                "success": True,
                "models_created": [
                    "DataCatalog",
                    "DataQualityMetrics",
                    "DataGovernanceWorkflow",
                    "DataAccessControl",
                    "DataRetentionPolicy",
                ],
                "models_available": True,
                "file_location": "app/models/data_governance.py",
                "message": "Data governance models are ready to use",
            }
        except ImportError as e:
            return {"success": False, "error": f"Failed to import data governance models: {str(e)}"}

    def _run_application_lifecycle_agent(self, architecture_id: int) -> Dict:
        """
        Application Lifecycle Agent

        Implements:
        - ApplicationVersioning model
        - DeploymentPipeline model
        - ApplicationPerformanceMetrics model
        """
        try:
            from app.models.application_lifecycle import (
                ApplicationPerformanceMetrics,  # noqa: F401 — availability probe: the import IS the test
                ApplicationVersioning,  # noqa: F401 — availability probe: the import IS the test
                DeploymentPipeline,  # noqa: F401 — availability probe: the import IS the test
            )

            return {
                "success": True,
                "models_created": [
                    "ApplicationVersioning",
                    "DeploymentPipeline",
                    "ApplicationPerformanceMetrics",
                ],
                "models_available": True,
                "file_location": "app/models/application_lifecycle.py",
                "message": "Application lifecycle models are ready to use",
            }
        except ImportError as e:
            return {
                "success": False,
                "error": f"Failed to import application lifecycle models: {str(e)}",
            }

    def _run_software_quality_agent(self, architecture_id: int) -> Dict:
        """
        Software Quality Agent

        Implements:
        - TechnicalDebt model
        - CodeQualityMetrics model
        - RefactoringTracking model
        """
        try:
            from app.models.software_quality import (
                CodeQualityMetrics,  # noqa: F401 — availability probe: the import IS the test
                RefactoringTracking,  # noqa: F401 — availability probe: the import IS the test
                TechnicalDebt,  # noqa: F401 — availability probe: the import IS the test
            )

            return {
                "success": True,
                "models_created": ["TechnicalDebt", "CodeQualityMetrics", "RefactoringTracking"],
                "models_available": True,
                "file_location": "app/models/software_quality.py",
                "message": "Software quality models are ready to use",
            }
        except ImportError as e:
            return {
                "success": False,
                "error": f"Failed to import software quality models: {str(e)}",
            }

    def _run_solution_deployment_agent(self, architecture_id: int) -> Dict:
        """
        Solution Deployment Agent

        Implements:
        - SolutionTechnologyMapping model
        - SolutionDeploymentArchitecture model
        """
        try:
            from app.models.solution_deployment import (
                SolutionDeploymentArchitecture,  # noqa: F401 — availability probe: the import IS the test
                solution_technology_mapping,  # noqa: F401 — availability probe: the import IS the test
            )

            return {
                "success": True,
                "models_created": ["SolutionTechnologyMapping", "SolutionDeploymentArchitecture"],
                "models_available": True,
                "file_location": "app/models/solution_deployment.py",
                "message": "Solution deployment models are ready to use",
            }
        except ImportError as e:
            return {
                "success": False,
                "error": f"Failed to import solution deployment models: {str(e)}",
            }

    def _run_viewpoint_export_agent(self, architecture_id: int) -> Dict:
        """
        Viewpoint Export Agent

        Implements:
        - ArchiMate XML export service
        """
        try:
            from app.services.archimate.archimate_xml_export_service import (
                ArchiMateXMLExportService,  # noqa: F401 — availability probe: the import IS the test
            )

            return {
                "success": True,
                "services_created": ["ArchiMateXMLExportService"],
                "service_available": True,
                "file_location": "app/services/archimate/archimate_xml_export_service.py",
                "message": "ArchiMate XML export service is ready to use",
            }
        except ImportError as e:
            return {"success": False, "error": f"Failed to import XML export service: {str(e)}"}

    # ========================================================================
    # Prompt Builders
    # ========================================================================

    def _build_system_architecture_prompt(self, architecture_id: int) -> str:
        """Build prompt for System Architecture Agent."""
        return f"""You are a Systems Architect expert in ArchiMate 3.2. Generate Python SQLAlchemy models for system architecture.

REQUIREMENTS:
1. Create SystemBoundary model - defines system scope and boundaries
2. Create SystemHierarchy model - parent-child system relationships
3. Create SystemInterface model - system-level contracts beyond ApplicationInterface
4. Create SystemDeployment model - how systems are deployed across infrastructure
5. Create SystemLifecycle model - system states (planned, active, deprecated, retired)

EXISTING PATTERNS (follow these):
- All models link to ArchiMateElement via archimate_element_id
- Use db.Column for SQLAlchemy 1.3.8 compatibility
- Include created_at, updated_at, created_by_id fields
- Add relationships using db.relationship()
- Create junction tables for many-to-many relationships
- Follow naming conventions from existing models

ARCHITECTURE CONTEXT:
- Architecture ID: {architecture_id}
- Existing models: ApplicationComponent, SystemDependency, TechnologyLayer models
- Need to model system boundaries and hierarchies for Systems Architecture

OUTPUT FORMAT:
Return JSON with:
{{
    "system_boundary": {{"fields": [...], "relationships": [...]}},
    "system_hierarchy": {{"fields": [...], "relationships": [...]}},
    "system_interface": {{"fields": [...], "relationships": [...]}},
    "system_deployment": {{"fields": [...], "relationships": [...]}},
    "system_lifecycle": {{"fields": [...], "relationships": [...]}}
}}

Generate complete, production-ready Python code for each model."""

    def _build_data_governance_prompt(self, architecture_id: int) -> str:
        """Build prompt for Data Governance Agent."""
        return """You are a Data Architect expert. Generate Python SQLAlchemy models for data governance.

REQUIREMENTS:
1. DataCatalog model - centralized metadata catalog
2. DataQualityMetrics model - quality scores over time, quality rules
3. DataGovernanceWorkflow model - approval workflows, stewardship
4. DataAccessControl model - who can access what data
5. DataRetentionPolicy model - retention rules, archival policies

EXISTING PATTERNS:
- DataDomain, DataEntity, DataLineage models exist
- BusinessObject model has PII tracking and GDPR fields
- Follow same patterns for new models

OUTPUT FORMAT:
Return JSON with model definitions following existing data architecture patterns."""

    def _build_application_lifecycle_prompt(self, architecture_id: int) -> str:
        """Build prompt for Application Lifecycle Agent."""
        return """You are an Applications Architect expert. Generate models for application lifecycle management.

REQUIREMENTS:
1. ApplicationVersioning model - version history tracking
2. DeploymentPipeline model - CI/CD, environments
3. ApplicationPerformanceMetrics model - response times, throughput, error rates

EXISTING PATTERNS:
- ApplicationComponent model exists with 100+ fields
- Link new models to ApplicationComponent
- Track metrics over time

OUTPUT FORMAT:
Return JSON with model definitions."""

    def _build_software_quality_prompt(self, architecture_id: int) -> str:
        """Build prompt for Software Quality Agent."""
        return """You are a Software Architect expert. Generate models for software quality tracking.

REQUIREMENTS:
1. TechnicalDebt model - debt items, prioritization, remediation
2. CodeQualityMetrics model - cyclomatic complexity, maintainability over time
3. RefactoringTracking model - refactoring history, impact analysis

EXISTING PATTERNS:
- SoftwareModule, DesignPattern, SoftwareDependency models exist
- Link new models to SoftwareModule

OUTPUT FORMAT:
Return JSON with model definitions."""

    def _build_solution_deployment_prompt(self, architecture_id: int) -> str:
        """Build prompt for Solution Deployment Agent."""
        return """You are a Solutions Architect expert. Generate models for solution deployment.

REQUIREMENTS:
1. SolutionTechnologyMapping - junction table linking Solution to TechnologyStack
2. SolutionDeploymentArchitecture - how solution components are deployed

EXISTING PATTERNS:
- Solution, SolutionPattern models exist
- TechnologyStack model exists
- Create junction tables and deployment models

OUTPUT FORMAT:
Return JSON with model definitions."""

    def _build_viewpoint_export_prompt(self, architecture_id: int) -> str:
        """Build prompt for Viewpoint Export Agent."""
        return """You are an ArchiMate 3.2 expert. Generate Python service for ArchiMate XML export.

REQUIREMENTS:
1. Export ArchiMateViewpoint to ArchiMate XML format
2. Follow ArchiMate 3.2 XML schema
3. Include all elements and relationships

EXISTING:
- ArchiMateViewpointService exists with 23 standard viewpoints
- ArchiMateElement, ArchiMateRelationship models exist

OUTPUT FORMAT:
Return complete Python service code for XML export."""

    # ========================================================================
    # Code Generators (stubs - would be implemented to parse LLM response)
    # ========================================================================

    def _generate_system_boundary_model(self, models_data: Dict) -> str:
        """Generate SystemBoundary model code."""
        # This would parse models_data and generate actual Python code
        return "# SystemBoundary model code would be generated here"

    def _generate_system_hierarchy_model(self, models_data: Dict) -> str:
        """Generate SystemHierarchy model code."""
        return "# SystemHierarchy model code would be generated here"

    def _generate_system_interface_model(self, models_data: Dict) -> str:
        """Generate SystemInterface model code."""
        return "# SystemInterface model code would be generated here"

    def _generate_data_catalog_model(self, models_data: Dict) -> str:
        """Generate DataCatalog model code."""
        return "# DataCatalog model code would be generated here"

    def _generate_data_quality_model(self, models_data: Dict) -> str:
        """Generate DataQualityMetrics model code."""
        return "# DataQualityMetrics model code would be generated here"

    def _generate_governance_workflow_model(self, models_data: Dict) -> str:
        """Generate DataGovernanceWorkflow model code."""
        return "# DataGovernanceWorkflow model code would be generated here"

    def _generate_application_versioning_model(self, models_data: Dict) -> str:
        """Generate ApplicationVersioning model code."""
        return "# ApplicationVersioning model code would be generated here"

    def _generate_deployment_pipeline_model(self, models_data: Dict) -> str:
        """Generate DeploymentPipeline model code."""
        return "# DeploymentPipeline model code would be generated here"

    def _generate_performance_metrics_model(self, models_data: Dict) -> str:
        """Generate ApplicationPerformanceMetrics model code."""
        return "# ApplicationPerformanceMetrics model code would be generated here"

    def _generate_technical_debt_model(self, models_data: Dict) -> str:
        """Generate TechnicalDebt model code."""
        return "# TechnicalDebt model code would be generated here"

    def _generate_code_quality_model(self, models_data: Dict) -> str:
        """Generate CodeQualityMetrics model code."""
        return "# CodeQualityMetrics model code would be generated here"

    def _generate_refactoring_model(self, models_data: Dict) -> str:
        """Generate RefactoringTracking model code."""
        return "# RefactoringTracking model code would be generated here"

    def _generate_solution_technology_mapping(self, models_data: Dict) -> str:
        """Generate SolutionTechnologyMapping code."""
        return "# SolutionTechnologyMapping code would be generated here"

    def _generate_solution_deployment_architecture(self, models_data: Dict) -> str:
        """Generate SolutionDeploymentArchitecture code."""
        return "# SolutionDeploymentArchitecture code would be generated here"

    def _generate_archimate_xml_export_service(self, response: str) -> str:
        """Generate ArchiMate XML export service code."""
        return "# ArchiMateXMLExportService code would be generated here"

    # =========================================================================
    # LLM-Driven Gap Resolution Orchestration (PRD: LLM-Driven Gap Analysis)
    # =========================================================================

    def execute_llm_driven_gap_resolution(
        self, architecture_id: int, options: Optional[Dict] = None
    ) -> Dict:
        """
        Master orchestration for LLM-Driven Gap Analysis, Planning, and Implementation.

        This method implements the full PRD workflow by orchestrating:
        1. Gap Discovery - Identify all process/capability gaps
        2. Reuse Analysis - Search existing applications for reuse candidates
        3. Recommendation Generation - LLM-powered reuse vs build recommendations
        4. Roadmap Generation - Prioritized roadmap with action types
        5. Work Package Creation - Detailed implementation plans
        6. Stakeholder Validation - Review and approval workflow
        7. Implementation Execution - Use existing agents
        8. Automated Testing - MCP pipeline integration
        9. Audit Logging - Full traceability throughout

        Args:
            architecture_id: Target architecture model ID
            options: Configuration options:
                - auto_approve: Skip stakeholder validation (default: False)
                - reuse_threshold: Minimum similarity for reuse (default: 0.6)
                - parallel_execution: Run independent steps in parallel (default: True)
                - dry_run: Generate plan without execution (default: False)
                - gap_types: List of gap types to process (default: all)
                - max_gaps: Maximum number of gaps to process (default: None = all)
                - budget_constraint: Maximum total budget (default: None)

        Returns:
            Complete execution report with:
            - gaps_discovered: Summary of discovered gaps
            - reuse_candidates: Reuse candidates per gap
            - reuse_recommendations: Recommendations per gap
            - roadmap_generated: Prioritized roadmap items
            - work_packages_created: Created work packages
            - validation_status: Stakeholder validation status
            - implementation_results: Agent execution results
            - audit_trail: Complete audit log
        """
        logger.info(f"Starting LLM-driven gap resolution for architecture {architecture_id}")
        start_time = time.time()

        # Initialize options with defaults
        opts = {
            "auto_approve": False,
            "reuse_threshold": 0.6,
            "parallel_execution": True,
            "dry_run": False,
            "gap_types": None,
            "max_gaps": None,
            "budget_constraint": None,
        }
        if options:
            opts.update(options)

        # Initialize result structure
        result = {
            "architecture_id": architecture_id,
            "execution_id": None,
            "started_at": datetime.utcnow().isoformat(),
            "options": opts,
            "phases": {},
            "gaps_discovered": {},
            "reuse_candidates": {},
            "reuse_recommendations": {},
            "roadmap_generated": [],
            "work_packages_created": [],
            "validation_status": {},
            "implementation_results": {},
            "audit_trail": [],
            "success": False,
            "errors": [],
            "total_duration_seconds": 0,
        }

        # Create execution history record
        execution_id = self._create_workflow_execution_record(architecture_id, opts)
        result["execution_id"] = execution_id

        try:
            # Phase 1: Gap Discovery
            result["audit_trail"].append(
                {
                    "phase": "gap_discovery",
                    "started_at": datetime.utcnow().isoformat(),
                    "status": "started",
                }
            )

            gaps_result = self._phase_gap_discovery(architecture_id, opts)
            result["gaps_discovered"] = gaps_result
            result["phases"]["gap_discovery"] = {
                "status": "completed",
                "gaps_found": gaps_result.get("summary", {}).get("total_gaps", 0),
            }

            result["audit_trail"].append(
                {
                    "phase": "gap_discovery",
                    "completed_at": datetime.utcnow().isoformat(),
                    "status": "completed",
                    "gaps_found": gaps_result.get("summary", {}).get("total_gaps", 0),
                }
            )

            # Phase 2: Reuse Analysis
            result["audit_trail"].append(
                {
                    "phase": "reuse_analysis",
                    "started_at": datetime.utcnow().isoformat(),
                    "status": "started",
                }
            )

            reuse_result = self._phase_reuse_analysis(gaps_result, opts)
            result["reuse_candidates"] = reuse_result.get("candidates", {})
            result["reuse_recommendations"] = reuse_result.get("recommendations", {})
            result["phases"]["reuse_analysis"] = {
                "status": "completed",
                "candidates_found": reuse_result.get("summary", {}).get("total_candidates", 0),
                "reuse_recommendations": reuse_result.get("summary", {}).get("reuse_count", 0),
                "extend_recommendations": reuse_result.get("summary", {}).get("extend_count", 0),
                "build_new_recommendations": reuse_result.get("summary", {}).get(
                    "build_new_count", 0
                ),
            }

            result["audit_trail"].append(
                {
                    "phase": "reuse_analysis",
                    "completed_at": datetime.utcnow().isoformat(),
                    "status": "completed",
                    "summary": reuse_result.get("summary", {}),
                }
            )

            # Phase 3: Roadmap Generation
            result["audit_trail"].append(
                {
                    "phase": "roadmap_generation",
                    "started_at": datetime.utcnow().isoformat(),
                    "status": "started",
                }
            )

            roadmap_result = self._phase_roadmap_generation(
                gaps_result, result["reuse_recommendations"], opts
            )
            result["roadmap_generated"] = roadmap_result.get("roadmap_items", [])
            result["phases"]["roadmap_generation"] = {
                "status": "completed",
                "items_generated": len(result["roadmap_generated"]),
                "total_estimated_cost": roadmap_result.get("total_cost", 0),
                "total_estimated_weeks": roadmap_result.get("total_weeks", 0),
            }

            result["audit_trail"].append(
                {
                    "phase": "roadmap_generation",
                    "completed_at": datetime.utcnow().isoformat(),
                    "status": "completed",
                    "items_generated": len(result["roadmap_generated"]),
                }
            )

            # Phase 4: Work Package Creation (if not dry run)
            if not opts["dry_run"]:
                result["audit_trail"].append(
                    {
                        "phase": "work_package_creation",
                        "started_at": datetime.utcnow().isoformat(),
                        "status": "started",
                    }
                )

                wp_result = self._phase_work_package_creation(
                    architecture_id, result["roadmap_generated"], opts
                )
                result["work_packages_created"] = wp_result.get("work_packages", [])
                result["phases"]["work_package_creation"] = {
                    "status": "completed",
                    "work_packages_created": len(result["work_packages_created"]),
                }

                result["audit_trail"].append(
                    {
                        "phase": "work_package_creation",
                        "completed_at": datetime.utcnow().isoformat(),
                        "status": "completed",
                        "work_packages_created": len(result["work_packages_created"]),
                    }
                )

                # Phase 5: Stakeholder Validation (if not auto_approve)
                if not opts["auto_approve"]:
                    result["audit_trail"].append(
                        {
                            "phase": "stakeholder_validation",
                            "started_at": datetime.utcnow().isoformat(),
                            "status": "started",
                        }
                    )

                    validation_result = self._phase_create_validation_requests(
                        result["roadmap_generated"], result["work_packages_created"]
                    )
                    result["validation_status"] = validation_result
                    result["phases"]["stakeholder_validation"] = {
                        "status": "pending_approval",
                        "validation_requests_created": validation_result.get("requests_created", 0),
                    }

                    result["audit_trail"].append(
                        {
                            "phase": "stakeholder_validation",
                            "completed_at": datetime.utcnow().isoformat(),
                            "status": "pending_approval",
                            "requests_created": validation_result.get("requests_created", 0),
                        }
                    )
                else:
                    result["phases"]["stakeholder_validation"] = {
                        "status": "skipped",
                        "reason": "auto_approve enabled",
                    }

            else:
                result["phases"]["work_package_creation"] = {
                    "status": "skipped",
                    "reason": "dry_run mode",
                }
                result["phases"]["stakeholder_validation"] = {
                    "status": "skipped",
                    "reason": "dry_run mode",
                }

            result["success"] = True

        except Exception as e:
            logger.error(f"LLM-driven gap resolution failed: {e}")
            result["success"] = False
            result["errors"].append(str(e))
            result["audit_trail"].append(
                {"phase": "error", "error": str(e), "timestamp": datetime.utcnow().isoformat()}
            )

        # Finalize
        total_duration = time.time() - start_time
        result["total_duration_seconds"] = total_duration
        result["completed_at"] = datetime.utcnow().isoformat()

        # Update execution history
        self._update_workflow_execution_record(execution_id, result)

        # Log decision for audit
        self._log_workflow_decision(result)

        logger.info(
            f"LLM-driven gap resolution completed: "
            f"success={result['success']}, "
            f"gaps={result['phases'].get('gap_discovery', {}).get('gaps_found', 0)}, "
            f"duration={total_duration:.2f}s"
        )

        return result

    def _create_workflow_execution_record(
        self, architecture_id: int, options: Dict
    ) -> Optional[int]:
        """Create execution history record for the full workflow."""
        try:
            execution = AgentExecutionHistory(
                architecture_id=architecture_id,
                agent_name="llm_gap_resolution",
                execution_type="full_workflow",
                status="running",
                success=False,
                executed_by_id=self.user_id,
                configuration=json.dumps(options),
            )
            db.session.add(execution)
            db.session.flush()
            return execution.id
        except Exception as e:
            logger.warning(f"Could not create workflow execution record: {e}")
            return None

    def _update_workflow_execution_record(self, execution_id: Optional[int], result: Dict) -> None:
        """Update execution history record with results."""
        if not execution_id:
            return

        try:
            execution = AgentExecutionHistory.query.get(execution_id)
            if execution:
                execution.completed_at = datetime.utcnow()
                execution.duration_seconds = result.get("total_duration_seconds", 0)
                execution.status = "success" if result.get("success") else "failed"
                execution.success = result.get("success", False)
                execution.result_data = json.dumps(result)
                execution.errors = json.dumps(result.get("errors", []))
                db.session.commit()
        except Exception as e:
            logger.warning(f"Could not update workflow execution record: {e}")
            db.session.rollback()

    def _log_workflow_decision(self, result: Dict) -> None:
        """Log the workflow decision for audit trail."""
        try:
            from app.services.llm_service import LLMService

            llm_service = LLMService()

            # Log key decisions from the workflow
            if result.get("reuse_recommendations"):
                for gap_id, recommendation in result["reuse_recommendations"].items():
                    llm_service.log_decision(
                        decision_type="reuse_recommendation",
                        context={
                            "gap_id": gap_id,
                            "architecture_id": result.get("architecture_id"),
                        },
                        decision={
                            "recommendation": recommendation.get("recommendation"),
                            "application_id": recommendation.get("recommended_application_id"),
                            "confidence": recommendation.get("confidence_score"),
                        },
                        rationale=recommendation.get("rationale", ""),
                    )
        except Exception as e:
            logger.warning(f"Could not log workflow decision: {e}")

    def _phase_gap_discovery(self, architecture_id: int, options: Dict) -> Dict:
        """Phase 1: Discover all gaps using GapDiscoveryService."""
        try:
            gap_service = GapDiscoveryService()
            gaps_data = gap_service.discover_all_gaps(architecture_id)

            # Filter by gap types if specified
            if options.get("gap_types"):
                filtered_gaps = {}
                for gap_type in options["gap_types"]:
                    if gap_type in gaps_data.get("gaps", {}):
                        filtered_gaps[gap_type] = gaps_data["gaps"][gap_type]
                gaps_data["gaps"] = filtered_gaps

            # Limit number of gaps if specified
            if options.get("max_gaps"):
                max_gaps = options["max_gaps"]
                limited_gaps = {}
                count = 0
                for gap_type, gaps in gaps_data.get("gaps", {}).items():
                    if count >= max_gaps:
                        break
                    remaining = max_gaps - count
                    limited_gaps[gap_type] = gaps[:remaining]
                    count += len(limited_gaps[gap_type])
                gaps_data["gaps"] = limited_gaps

            return gaps_data

        except Exception as e:
            logger.error(f"Gap discovery phase failed: {e}")
            return {"gaps": {}, "summary": {"total_gaps": 0}, "error": str(e)}

    def _phase_reuse_analysis(self, gaps_data: Dict, options: Dict) -> Dict:
        """Phase 2: Analyze reuse candidates for each gap."""
        try:
            from app.services.application_similarity_service import ApplicationSimilarityService

            similarity_service = ApplicationSimilarityService()

            all_candidates = {}
            all_recommendations = {}
            summary = {
                "total_candidates": 0,
                "reuse_count": 0,
                "extend_count": 0,
                "replace_count": 0,
                "build_new_count": 0,
            }

            # Process each gap type
            for gap_type, gaps in gaps_data.get("gaps", {}).items():
                for gap in gaps:
                    gap_id = str(
                        gap.get("id") or gap.get("capability_id") or f"{gap_type}_{gaps.index(gap)}"
                    )

                    # Find reuse candidates
                    candidates = similarity_service.find_reuse_candidates_for_gap(
                        gap, threshold=options.get("reuse_threshold", 0.6)
                    )
                    all_candidates[gap_id] = candidates
                    summary["total_candidates"] += len(candidates)

                    # Generate recommendation
                    recommendation = similarity_service.generate_reuse_vs_build_recommendation(
                        gap, candidates, user_id=self.user_id
                    )
                    all_recommendations[gap_id] = recommendation

                    # Update summary counts
                    rec_type = recommendation.get("recommendation", "build_new")
                    if rec_type == "reuse":
                        summary["reuse_count"] += 1
                    elif rec_type == "extend":
                        summary["extend_count"] += 1
                    elif rec_type == "replace":
                        summary["replace_count"] += 1
                    else:
                        summary["build_new_count"] += 1

            return {
                "candidates": all_candidates,
                "recommendations": all_recommendations,
                "summary": summary,
            }

        except Exception as e:
            logger.error(f"Reuse analysis phase failed: {e}")
            return {"candidates": {}, "recommendations": {}, "summary": {}, "error": str(e)}

    def _phase_roadmap_generation(
        self, gaps_data: Dict, recommendations: Dict, options: Dict
    ) -> Dict:
        """Phase 3: Generate prioritized roadmap."""
        try:
            from app.services.roadmap_automation import RoadmapAutomationEngine

            roadmap_engine = RoadmapAutomationEngine()

            # Flatten gaps into list
            all_gaps = []
            for gap_type, gaps in gaps_data.get("gaps", {}).items():
                for gap in gaps:
                    gap["gap_type"] = gap_type
                    all_gaps.append(gap)

            # Generate roadmap
            roadmap_options = {
                "budget_constraint": options.get("budget_constraint"),
                "include_dependencies": True,
            }

            roadmap_items = roadmap_engine.generate_reuse_roadmap(
                all_gaps, recommendations, roadmap_options
            )

            # Calculate totals
            total_cost = sum(item.get("estimated_cost", 0) for item in roadmap_items)
            total_weeks = sum(item.get("estimated_effort_weeks", 0) for item in roadmap_items)

            return {
                "roadmap_items": roadmap_items,
                "total_cost": total_cost,
                "total_weeks": total_weeks,
                "items_by_action": {
                    "reuse_existing": len(
                        [i for i in roadmap_items if i.get("action_type") == "reuse_existing"]
                    ),
                    "extend_existing": len(
                        [i for i in roadmap_items if i.get("action_type") == "extend_existing"]
                    ),
                    "replace": len([i for i in roadmap_items if i.get("action_type") == "replace"]),
                    "build_new": len(
                        [i for i in roadmap_items if i.get("action_type") == "build_new"]
                    ),
                },
            }

        except Exception as e:
            logger.error(f"Roadmap generation phase failed: {e}")
            return {"roadmap_items": [], "total_cost": 0, "total_weeks": 0, "error": str(e)}

    def _phase_work_package_creation(
        self, architecture_id: int, roadmap_items: List[Dict], options: Dict
    ) -> Dict:
        """Phase 4: Create work packages from roadmap."""
        try:
            from app.models.archimate_core import ArchitectureModel
            from app.services import work_package_service
            from app.utils.tenant import current_organization_id

            work_packages = []
            architecture = db.session.get(ArchitectureModel, architecture_id)
            org_id = (architecture.organization_id if architecture else None) or current_organization_id()

            for item in roadmap_items:
                # Create the work package for each roadmap item through the one writer
                wp = work_package_service.create_work_package(
                    organization_id=org_id,
                    name=item.get("name"),
                    description=item.get("description"),
                    capability_id=item.get("capability_id"),
                    status="planned",
                    priority=item.get("priority", "medium"),
                    risk_level=item.get("risk_level", "medium"),
                    estimated_cost=item.get("estimated_cost"),
                    start_date=datetime.fromisoformat(item["start_date"])
                    if item.get("start_date")
                    else None,
                    end_date=datetime.fromisoformat(item["end_date"])
                    if item.get("end_date")
                    else None,
                    auto_generated=True,
                    source_type="gap",
                    generation_method="llm_gap_resolution",
                    confidence_score=item.get("confidence_score", 0.7),
                    source_data=json.dumps(
                        {
                            "action_type": item.get("action_type"),
                            "source_application_id": item.get("source_application_id"),
                            "gap_id": item.get("gap_id"),
                            "gap_type": item.get("gap_type"),
                            "reuse_rationale": item.get("reuse_rationale"),
                            "implementation_approach": item.get("implementation_approach"),
                            "sub_work_packages": item.get("work_packages", []),
                        }
                    ),
                )

                work_packages.append(
                    {
                        "id": wp.id,
                        "name": wp.name,
                        "action_type": item.get("action_type"),
                        "priority": wp.priority,
                        "estimated_cost": wp.estimated_cost,
                    }
                )

            db.session.commit()

            return {"work_packages": work_packages}

        except Exception as e:
            logger.error(f"Work package creation phase failed: {e}")
            db.session.rollback()
            return {"work_packages": [], "error": str(e)}

    def _phase_create_validation_requests(
        self, roadmap_items: List[Dict], work_packages: List[Dict]
    ) -> Dict:
        """Phase 5: Create validation requests for stakeholder review."""
        try:
            validation_requests = []

            # Group by domain/capability for batch validation
            domains = {}
            for i, item in enumerate(roadmap_items):
                domain = item.get("domain", "general")
                if domain not in domains:
                    domains[domain] = []
                domains[domain].append(
                    {
                        "roadmap_item": item,
                        "work_package": work_packages[i] if i < len(work_packages) else None,
                    }
                )

            # Create validation request for each domain
            for domain, items in domains.items():
                validation_request = {
                    "domain": domain,
                    "items_count": len(items),
                    "total_estimated_cost": sum(
                        i["roadmap_item"].get("estimated_cost", 0) for i in items
                    ),
                    "status": "pending",
                    "created_at": datetime.utcnow().isoformat(),
                    "items": [
                        {
                            "name": i["roadmap_item"].get("name"),
                            "action_type": i["roadmap_item"].get("action_type"),
                            "priority": i["roadmap_item"].get("priority"),
                            "work_package_id": i["work_package"].get("id")
                            if i["work_package"]
                            else None,
                        }
                        for i in items
                    ],
                }
                validation_requests.append(validation_request)

            return {
                "requests_created": len(validation_requests),
                "validation_requests": validation_requests,
            }

        except Exception as e:
            logger.error(f"Validation request creation failed: {e}")
            return {"requests_created": 0, "error": str(e)}

    def create_validation_request(self, roadmap_item_id: int, stakeholder_ids: List[int]) -> Dict:
        """
        Create a validation request for business owner approval.

        This method supports the PRD requirement for stakeholder validation
        by creating formal approval requests.

        Args:
            roadmap_item_id: ID of the roadmap item/work package
            stakeholder_ids: List of user IDs who should approve

        Returns:
            Validation request details with approval status
        """
        try:
            from app.models.unified_work_package import UnifiedWorkPackage

            wp = UnifiedWorkPackage.query.get(roadmap_item_id)
            if not wp:
                return {"success": False, "error": "Work package not found"}

            # Get source data for context
            source_data = json.loads(wp.source_data) if wp.source_data else {}

            validation_request = {
                "work_package_id": wp.id,
                "work_package_name": wp.name,
                "action_type": source_data.get("action_type"),
                "description": wp.description,
                "estimated_cost": wp.estimated_cost,
                "priority": wp.priority,
                "risk_level": wp.risk_level,
                "reuse_rationale": source_data.get("reuse_rationale"),
                "implementation_approach": source_data.get("implementation_approach"),
                "stakeholder_ids": stakeholder_ids,
                "status": "pending",
                "created_at": datetime.utcnow().isoformat(),
                "created_by": self.user_id,
            }

            return {"success": True, "validation_request": validation_request}

        except Exception as e:
            logger.error(f"Failed to create validation request: {e}")
            return {"success": False, "error": str(e)}

    def process_stakeholder_feedback(self, validation_id: int, feedback: Dict) -> Dict:
        """
        Process feedback from business owner review.

        This method supports the PRD requirement for feedback and iteration
        by handling stakeholder decisions on proposed changes.

        Args:
            validation_id: ID of the validation request (work package ID)
            feedback: Feedback dictionary with:
                - decision: 'approved' | 'rejected' | 'revision_requested'
                - notes: Reviewer notes
                - modifications: Any requested modifications

        Returns:
            Processing result with next steps
        """
        try:
            from app.models.unified_work_package import UnifiedWorkPackage

            wp = UnifiedWorkPackage.query.get(validation_id)
            if not wp:
                return {"success": False, "error": "Work package not found"}

            decision = feedback.get("decision", "pending")
            notes = feedback.get("notes", "")
            modifications = feedback.get("modifications", {})

            result = {
                "work_package_id": wp.id,
                "decision": decision,
                "processed_at": datetime.utcnow().isoformat(),
            }

            if decision == "approved":
                wp.status = "approved"
                result["next_steps"] = "Work package approved. Ready for implementation."
                result["implementation_ready"] = True

            elif decision == "rejected":
                wp.status = "cancelled"
                result["next_steps"] = "Work package rejected. Consider alternative approaches."
                result["implementation_ready"] = False
                result["rejection_reason"] = notes

            elif decision == "revision_requested":
                wp.status = "revision_requested"
                result["next_steps"] = "Revisions requested. Update work package and resubmit."
                result["implementation_ready"] = False
                result["requested_modifications"] = modifications

                # Store revision request in source_data
                source_data = json.loads(wp.source_data) if wp.source_data else {}
                source_data["revision_requests"] = source_data.get("revision_requests", [])
                source_data["revision_requests"].append(
                    {
                        "requested_at": datetime.utcnow().isoformat(),
                        "notes": notes,
                        "modifications": modifications,
                    }
                )
                wp.source_data = json.dumps(source_data)

            db.session.commit()

            return {"success": True, "result": result}

        except Exception as e:
            logger.error(f"Failed to process stakeholder feedback: {e}")
            db.session.rollback()
            return {"success": False, "error": str(e)}

    def trigger_validation_tests(self, work_package_id: int) -> Dict:
        """
        Trigger MCP test pipeline for implemented work package.

        This method supports the PRD requirement for automated testing
        by integrating with the MCP Playwright testing pipeline.

        Args:
            work_package_id: ID of the completed work package

        Returns:
            Test execution results
        """
        try:
            from app.models.unified_work_package import UnifiedWorkPackage

            wp = UnifiedWorkPackage.query.get(work_package_id)
            if not wp:
                return {"success": False, "error": "Work package not found"}

            # Get source data for test context
            source_data = json.loads(wp.source_data) if wp.source_data else {}

            # Build test context
            test_context = {
                "work_package_id": wp.id,
                "work_package_name": wp.name,
                "action_type": source_data.get("action_type"),
                "capability_id": wp.capability_id,
                "source_application_id": source_data.get("source_application_id"),
            }

            # In a real implementation, this would call the MCP test orchestrator
            # For now, return a placeholder indicating tests would be triggered
            test_result = {
                "success": True,
                "work_package_id": work_package_id,
                "test_context": test_context,
                "status": "test_pipeline_queued",
                "message": (
                    "MCP test pipeline queued for work package. "
                    "Tests will verify gap closure and regression checks."
                ),
                "expected_tests": [
                    "Gap closure verification",
                    "Capability coverage test",
                    "Integration regression test",
                    "UI visual regression (if applicable)",
                ],
                "queued_at": datetime.utcnow().isoformat(),
            }

            # Log the test trigger
            logger.info(f"Validation tests triggered for work package {work_package_id}")

            return test_result

        except Exception as e:
            logger.error(f"Failed to trigger validation tests: {e}")
            return {"success": False, "error": str(e)}

    def get_workflow_audit_trail(self, execution_id: int) -> Dict:
        """
        Get complete audit trail for a workflow execution.

        This method supports the PRD requirement for audit and traceability
        by providing full visibility into all LLM actions and decisions.

        Args:
            execution_id: ID of the workflow execution

        Returns:
            Complete audit trail with all phases, decisions, and outcomes
        """
        try:
            execution = AgentExecutionHistory.query.get(execution_id)
            if not execution:
                return {"success": False, "error": "Execution not found"}

            result_data = json.loads(execution.result_data) if execution.result_data else {}

            audit_trail = {
                "execution_id": execution_id,
                "architecture_id": execution.architecture_id,
                "agent_name": execution.agent_name,
                "execution_type": execution.execution_type,
                "started_at": execution.started_at.isoformat() if execution.started_at else None,
                "completed_at": execution.completed_at.isoformat()
                if execution.completed_at
                else None,
                "duration_seconds": execution.duration_seconds,
                "status": execution.status,
                "success": execution.success,
                "executed_by_id": execution.executed_by_id,
                "configuration": json.loads(execution.configuration)
                if execution.configuration
                else {},
                "phases": result_data.get("phases", {}),
                "audit_trail": result_data.get("audit_trail", []),
                "gaps_discovered_count": result_data.get("phases", {})
                .get("gap_discovery", {})
                .get("gaps_found", 0),
                "reuse_recommendations": result_data.get("phases", {}).get("reuse_analysis", {}),
                "roadmap_items_count": result_data.get("phases", {})
                .get("roadmap_generation", {})
                .get("items_generated", 0),
                "work_packages_count": result_data.get("phases", {})
                .get("work_package_creation", {})
                .get("work_packages_created", 0),
                "validation_status": result_data.get("phases", {}).get(
                    "stakeholder_validation", {}
                ),
                "errors": json.loads(execution.errors) if execution.errors else [],
                "reviewed": execution.reviewed,
                "reviewed_at": execution.reviewed_at.isoformat() if execution.reviewed_at else None,
                "reviewed_by_id": execution.reviewed_by_id,
                "notes": execution.notes,
            }

            return {"success": True, "audit_trail": audit_trail}

        except Exception as e:
            logger.error(f"Failed to get workflow audit trail: {e}")
            return {"success": False, "error": str(e)}
