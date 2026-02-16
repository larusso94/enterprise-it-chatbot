
"""
Configuration management for CU1 IT Chatbot application.

This module provides centralized configuration manBSESSION START
- On a new session, briefly introduce yourself and ask how you can help
- Focus on being helpful and conversationalVBEHABEHAVIOR
- Be natural, friendly, and conversational while maintaining professionalism
- Detect and respond in the user's language; match their tone and formality
- Handle social exchanges (thanks, greetings, small talk) naturally - no need to always redirect to security
- For security questions: search KB first, provide clear guidance, create tickets when needed
- Ask clarifying questions only when genuinely needed

CONVERSATION FLOW Be natural, friendly, and conversational while maintaining professionalism
- Detect and respond in the user's language; match their tone and formality
- Handle social exchanges (thanks, greetings, small talk) naturally - no need to always redirect to security
- For security questions: search KB first, provide clear guidance, create tickets when needed
- Ask clarifying questions only when genuinely needed- Detect and reply entirely in the user's language; adapt formality to the user.
- Professional, concise, natural. Avoid templates, fillers, and repeated expressions.
- Handle social exchanges naturally (greetings, thanks, small talk) with brief, friendly responses before offering security help.
- If the request is vague/underspecified, ask exactly ONE concise clarifying question before any tool call.
- Prefer bullet-like, actionable steps for KB guidance. If nothing meaningful to add, keep it brief.nt using environment variables
with sensible defaults. It supports development environment file loading and 
production environment variable usage.

Features:
- Environment variable loading with .env file support
- Configuration validation and missing secrets detection
- Structured configuration with type hints
- Default values for non-critical settings
"""

from __future__ import annotations
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

try:
    from dotenv import load_dotenv
    
    # Load environment files in development only
    # Production environments should use direct environment variables
    if not os.getenv("container-env", "").lower() in ["true", "1", "yes"]:
        env_files = [".env.dev", ".env", ".env.local"]
        for env_file in env_files:
            env_path = Path(__file__).parent / env_file
            if env_path.exists():
                load_dotenv(env_path)
                _loaded_env_file = env_file
                break
        
except ImportError:
    # Continue without dotenv if not installed
    pass


@dataclass(frozen=True)
class AppConfig:
    """
    Application configuration loaded from environment variables.
    
    This class centralizes all configuration parameters and provides
    validation for critical secrets. All values are loaded from
    environment variables with appropriate defaults where applicable.
    """
    # OpenAI
    openai_api_base: Optional[str] = field(default_factory=lambda: os.getenv("openai-api-base"))
    openai_api_key: Optional[str] = field(default_factory=lambda: os.getenv("openai-api-key"))
    openai_deployment_name: Optional[str] = field(default_factory=lambda: os.getenv("openai-deployment-name"))
    openai_api_version: Optional[str] = field(default_factory=lambda: os.getenv("openai-api-version"))

    # ServiceNow - Authentication
    servicenow_instance_url: Optional[str] = field(default_factory=lambda: os.getenv("servicenow-instance-url"))
    servicenow_username: Optional[str] = field(default_factory=lambda: os.getenv("servicenow-username"))
    servicenow_password: Optional[str] = field(default_factory=lambda: os.getenv("servicenow-password"))
    servicenow_token: Optional[str] = field(default_factory=lambda: os.getenv("servicenow-token"))
    
    # ServiceNow - OAuth (Client Credentials flow - no redirect URL needed)
    servicenow_oauth_client_id: Optional[str] = field(default_factory=lambda: os.getenv("servicenow-oauth-client-id"))
    servicenow_oauth_client_secret: Optional[str] = field(default_factory=lambda: os.getenv("servicenow-oauth-client-secret"))

    # ServiceNow Catalog Items - Security Request Item sys_id
    # Must be configured via environment variable for your ServiceNow instance
    servicenow_security_request_item_id: Optional[str] = field(
        default_factory=lambda: os.getenv("servicenow-security-request-item-id")
    )
    
    # ServiceNow Assignment Groups - Security Operation Center sys_id  
    # Must be configured via environment variable for your ServiceNow instance
    servicenow_security_operation_center_id: Optional[str] = field(
        default_factory=lambda: os.getenv("servicenow-security-operation-center-id")
    )

    # Cosmos
    cosmos_endpoint: Optional[str] = field(default_factory=lambda: os.getenv("cosmos-endpoint"))
    cosmos_key: Optional[str] = field(default_factory=lambda: os.getenv("cosmos-key"))
    cosmos_database: str = field(default_factory=lambda: os.getenv("cosmos-database", "itchatbot"))
    cosmos_container_sessions: str = field(default_factory=lambda: os.getenv("cosmos-container-sessions", "sessions"))
    cosmos_container_vectors: str = field(default_factory=lambda: os.getenv("cosmos-container-vectors", "vstore"))
    partition_key: str = field(default_factory=lambda: os.getenv("partition-key", "/id"))

    # OpenAI Embeddings
    openai_embed_deployment: Optional[str] = field(default_factory=lambda: os.getenv("openai-embed-deployment"))
    embedding_dim: int = field(default_factory=lambda: int(os.getenv("embedding-dim", "1536")))
    embedding_fallback_prime_base: int = field(default_factory=lambda: int(os.getenv("embedding-fallback-prime-base", "999983")))

    # Agent Configuration
    agent_max_context_turns: int = field(default_factory=lambda: int(os.getenv("agent-max-context-turns", "6")))
    agent_max_tokens: int = field(default_factory=lambda: int(os.getenv("agent-max-tokens", "2000")))
    vector_search_top_k: int = field(default_factory=lambda: int(os.getenv("vector-search-top-k", "3")))
    
    # ServiceNow Configuration  
    servicenow_timeout: int = field(default_factory=lambda: int(os.getenv("servicenow-timeout", "30")))
    servicenow_oauth_token_buffer: int = field(default_factory=lambda: int(os.getenv("servicenow-oauth-token-buffer", "300")))
    servicenow_oauth_default_expiry: int = field(default_factory=lambda: int(os.getenv("servicenow-oauth-default-expiry", "3600")))
    
    # Bot Framework Timeout Configuration - Resilience Settings
    bot_framework_timeout: int = field(default_factory=lambda: int(os.getenv("bot-framework-timeout", "30")))  # Reduced from 100s default
    bot_framework_connection_timeout: int = field(default_factory=lambda: int(os.getenv("bot-framework-connection-timeout", "10")))
    bot_framework_read_timeout: int = field(default_factory=lambda: int(os.getenv("bot-framework-read-timeout", "20")))
    
    # Retry Configuration - Exponential Backoff
    max_retry_attempts: int = field(default_factory=lambda: int(os.getenv("max-retry-attempts", "3")))
    retry_base_delay: float = field(default_factory=lambda: float(os.getenv("retry-base-delay", "1.0")))  # seconds
    retry_max_delay: float = field(default_factory=lambda: float(os.getenv("retry-max-delay", "10.0")))  # seconds
    retry_backoff_factor: float = field(default_factory=lambda: float(os.getenv("retry-backoff-factor", "2.0")))
    
    # Circuit Breaker Configuration
    circuit_breaker_failure_threshold: int = field(default_factory=lambda: int(os.getenv("circuit-breaker-failure-threshold", "5")))
    circuit_breaker_recovery_timeout: int = field(default_factory=lambda: int(os.getenv("circuit-breaker-recovery-timeout", "60")))  # seconds
    circuit_breaker_expected_exception_threshold: int = field(default_factory=lambda: int(os.getenv("circuit-breaker-expected-exception-threshold", "10")))
    
    agent_default_sys_prompt: str = field(default_factory=lambda: os.getenv("agent-default-sys-prompt", 
        """
ROLE
You are **IT Support Assistant**, a multilingual Teams bot for enterprise IT self-service and ticket triage.

SESSION START
- On a new session (no prior turns), briefly introduce yourself (role, capabilities: KB guidance; incident/request creation after confirmation) and ask ONE short question to elicit the user's need.
- Provide ticket information only when asked. Do NOT mention identity, email, or internal systems.

SCOPE & PURPOSE
- Resolve *ONLY* topics covered by the knowledgebase via self-help guidance or IT topics if the knowledge base doesn't cover a specific topic.
- If knowledgebase guidance is insufficient to fulfill user needs or the user asks to escalate, confirm and then create an incident or access/tools request.
- Politely decline non-security or out-of-scope topics.

TOOLS
- `knowledge_base_search` → {title, short_summary, article_id, tags}
- `create_security_incident` / `create_security_request` → open new tickets (read-only thereafter)
- `list_my_incidents` / `list_my_request_items` → shows not closed tickets (open, in-progress, pending, etc.) - Use when ticket information is requested, no confirmation needed.

BEHAVIOR
- Detect and reply entirely in the user’s language; adapt formality to the user.
- Professional, concise, natural. Avoid templates, fillers, and repeated expressions.
- If the request is vague/underspecified, ask exactly ONE concise clarifying question before any tool call.
- Prefer bullet-like, actionable steps for KB guidance. If nothing meaningful to add, keep it brief.

CONVERSATION FLOW
1) Social interactions → respond naturally (e.g., "¡De nada!" "You're welcome!" "¿Algo más?")
2) Security questions → search knowledge base and provide helpful guidance  
3) Need to escalate → confirm details and create appropriate ticket
4) Show tickets only when explicitly requested by user

PRIVACY GUIDELINES
- Don't display internal system IDs or technical details to users
- Keep ticket information user-friendly (number, status, description)
- KB references: show title, summary, and URL only

GOAL
Be helpful, natural, and human-like. Handle both casual conversation and cybersecurity needs efficiently and professionally.
"""))

    # Misc / Logging
    environment: str = field(default_factory=lambda: os.getenv("environment", "development"))
    log_level: str = field(default_factory=lambda: os.getenv("log-level", "INFO").upper())
    log_json: bool = field(default_factory=lambda: os.getenv("log-json", "1") not in ["0", "false", "False"])
    port: int = field(default_factory=lambda: int(os.getenv("port", "8000")))

    # Microsoft Bot Framework
    microsoft_app_id: Optional[str] = field(default_factory=lambda: os.getenv("microsoft-app-id"))
    microsoft_app_password: Optional[str] = field(default_factory=lambda: os.getenv("microsoft-app-password"))
    microsoft_app_type: str = field(default_factory=lambda: os.getenv("microsoft-app-type", "SingleTenant"))
    microsoft_tenant_id: Optional[str] = field(default_factory=lambda: os.getenv("microsoft-tenant-id"))

    @property
    def required_secrets_missing(self) -> List[str]:
        """Return list of critical secrets that are missing or have placeholder values."""
        missing = []
        
        # OpenAI API key
        if not self.openai_api_key or self.openai_api_key == "<Azure OpenAI API key>" or self.openai_api_key.startswith("<"):
            missing.append("openai-api-key")
        
        # ServiceNow credentials - either token OR username/password OR OAuth client credentials required
        has_token = self.servicenow_token and self.servicenow_token != "<ServiceNow bearer token>" and not self.servicenow_token.startswith("<")
        has_basic_auth = (self.servicenow_username and self.servicenow_password and 
                         not self.servicenow_username.startswith("<") and 
                         not self.servicenow_password.startswith("<"))
        has_oauth_credentials = (self.servicenow_oauth_client_id and self.servicenow_oauth_client_secret and
                               not self.servicenow_oauth_client_id.startswith("<") and
                               not self.servicenow_oauth_client_secret.startswith("<"))
        if not has_token and not has_basic_auth and not has_oauth_credentials:
            missing.append("servicenow-credentials")
        
        # Cosmos DB key
        if not self.cosmos_key or self.cosmos_key == "<Cosmos DB primary key (base64)>" or self.cosmos_key.startswith("<"):
            missing.append("cosmos-key")
        
        # Bot Framework password
        if not self.microsoft_app_password or self.microsoft_app_password == "<Bot Framework client secret>" or self.microsoft_app_password.startswith("<"):
            missing.append("microsoft-app-password")
            
        return missing

    def summary(self) -> dict:
        """
        Generate a summary of non-sensitive configuration values.
        
        Returns:
            dict: Configuration summary with sensitive values masked
        """
        return {
            "openai_api_base": self.openai_api_base,
            "openai_deployment_name": self.openai_deployment_name,
            "servicenow_instance_url": self.servicenow_instance_url,
            "servicenow_oauth_configured": bool(self.servicenow_oauth_client_id and self.servicenow_oauth_client_secret),
            "cosmos_endpoint_set": bool(self.cosmos_endpoint),
            "cosmos_database": self.cosmos_database,
            "agent_max_context_turns": self.agent_max_context_turns,
            "log_level": self.log_level,
            "port": self.port,
            "microsoft_app_id_set": bool(self.microsoft_app_id),
            "microsoft_app_type": self.microsoft_app_type,
            "microsoft_tenant_id_set": bool(self.microsoft_tenant_id),
            "environment": self.environment,
        }


# Global configuration instance - recommended for dependency injection or direct import
config = AppConfig()

# Initialize logging for environment configuration
try:
    # Import logger after config creation to prevent circular imports
    from ..clients.logging_client import log
    
    # Log environment configuration loading status
    log.info("config.env.loaded", 
             env_file=globals().get('_loaded_env_file', 'none'),
             environment=config.environment,
             port=config.port,
             log_level=config.log_level)
             
except ImportError:
    # Logger not available during initialization, continue silently
    pass
