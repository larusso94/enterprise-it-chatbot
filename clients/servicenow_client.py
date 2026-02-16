"""
ServiceNow Client for IT Operations and Service Management.

A clean, maintainable client that provides core ServiceNow functionality:
- User management and lookups
- Incident and request tracking
- Security incident and request creation

Supports multiple authentication methods with automatic fallback.
"""

import logging
import requests
import time
import asyncio
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from ..mcp.config import config
from .resilience_utils import ResilienceManager, with_resilience


class AuthMethod(Enum):
    """Authentication methods supported by the ServiceNow client."""
    OAUTH = "oauth"
    BEARER_TOKEN = "token"  
    BASIC_AUTH = "basic"


@dataclass
class ServiceNowConfig:
    """Configuration container for ServiceNow client."""
    instance_url: str
    base_url: str
    auth_method: AuthMethod
    
    # OAuth credentials
    oauth_client_id: Optional[str] = None
    oauth_client_secret: Optional[str] = None
    
    # Basic auth credentials
    username: Optional[str] = None
    password: Optional[str] = None
    
    # Bearer token
    token: Optional[str] = None


class ServiceNowClientError(Exception):
    """Custom exception for ServiceNow client errors."""
    pass


class ServiceNowClient:
    """
    ServiceNow client for IT operations and service management.
    
    Provides a clean, readable interface to ServiceNow's REST API with:
    - Multiple authentication methods (OAuth 2.0, Bearer token, Basic auth)
    - Automatic token management and renewal
    - Structured error handling and logging
    - Type hints for better code clarity
    
    Core Operations:
    - User lookups and management
    - Incident creation and tracking
    - Request item (RITM) management
    - Security incident and request creation
    """
    
    def __init__(self):
        """Initialize ServiceNow client with automatic configuration detection and resilience."""
        self._config = self._build_configuration()
        self._setup_authentication()
        self._setup_logging()
        
        # Initialize resilience manager
        self.resilience_manager = ResilienceManager(config)

    def _build_configuration(self) -> ServiceNowConfig:
        """Build client configuration from environment settings."""
        instance_url = config.servicenow_instance_url or ""
        
        # Ensure proper URL format
        if instance_url and not instance_url.startswith(("http://", "https://")):
            instance_url = f"https://{instance_url}"
        
        base_url = f"{instance_url.rstrip('/')}/api/now"
        
        # Determine authentication method and validate credentials
        auth_method, credentials_valid = self._detect_auth_method()
        
        if not credentials_valid:
            raise ServiceNowClientError(
                "Missing required ServiceNow configuration. "
                "Need OAuth credentials, bearer token, or username/password"
            )
        
        return ServiceNowConfig(
            instance_url=instance_url,
            base_url=base_url,
            auth_method=auth_method,
            oauth_client_id=config.servicenow_oauth_client_id,
            oauth_client_secret=config.servicenow_oauth_client_secret,
            username=config.servicenow_username,
            password=config.servicenow_password,
            token=config.servicenow_token
        )

    def _detect_auth_method(self) -> Tuple[AuthMethod, bool]:
        """Detect the best available authentication method."""
        # Check OAuth (preferred)
        if all([
            config.servicenow_instance_url,
            config.servicenow_oauth_client_id,
            config.servicenow_oauth_client_secret
        ]):
            return AuthMethod.OAUTH, True
        
        # Check Bearer token
        if config.servicenow_token and config.servicenow_instance_url:
            return AuthMethod.BEARER_TOKEN, True
        
        # Check Basic auth
        if all([
            config.servicenow_instance_url,
            config.servicenow_username,
            config.servicenow_password
        ]):
            return AuthMethod.BASIC_AUTH, True
        
        return AuthMethod.BASIC_AUTH, False

    def _setup_authentication(self) -> None:
        """Setup authentication for HTTP requests."""
        # OAuth token management
        self._access_token: Optional[str] = None
        self._token_expires_at: int = 0
        self._oauth_token_url = f"{self._config.instance_url.rstrip('/')}/oauth_token.do"
        
        # Setup HTTP session
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json"
        })
        
        # Configure authentication based on method
        if self._config.auth_method == AuthMethod.BEARER_TOKEN:
            self.session.headers.update({
                "Authorization": f"Bearer {self._config.token}"
            })
        elif self._config.auth_method == AuthMethod.BASIC_AUTH:
            self.session.auth = (self._config.username, self._config.password)
        # OAuth authentication is handled dynamically in _make_request

    def _setup_logging(self) -> None:
        """Setup structured logging for the client."""
        self.logger = logging.getLogger(__name__)

    # ================================
    # Authentication Methods
    # ================================

    def _get_oauth_token(self) -> str:
        """Get OAuth 2.0 access token using Client Credentials flow."""
        # Return cached token if still valid (with configurable buffer)
        if self._access_token and time.time() < (self._token_expires_at - config.servicenow_oauth_token_buffer):
            return self._access_token
        
        if self._config.auth_method != AuthMethod.OAUTH:
            raise ServiceNowClientError("OAuth not configured for this client")
        
        try:
            # Prepare OAuth token request
            token_data = {
                'grant_type': 'client_credentials',
                'client_id': self._config.oauth_client_id,
                'client_secret': self._config.oauth_client_secret
            }
            
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json'
            }
            
            self.logger.debug(
                f"servicenow.oauth.requesting_token: client_id={self._config.oauth_client_id}"
            )
            
            # Request access token
            response = requests.post(
                self._oauth_token_url,
                data=token_data,
                headers=headers,
                timeout=config.servicenow_timeout
            )
            
            response.raise_for_status()
            token_response = response.json()
            
            # Extract token and expiration
            self._access_token = token_response['access_token']
            expires_in = token_response.get('expires_in', config.servicenow_oauth_default_expiry)  # Configurable default expiry
            self._token_expires_at = time.time() + expires_in
            
            self.logger.info(
                f"servicenow.oauth.token_obtained: expires_in={expires_in}, "
                f"token_type={token_response.get('token_type', 'Bearer')}"
            )
            return self._access_token
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"servicenow.oauth.token_request_failed: {str(e)}")
            raise ServiceNowClientError(f"OAuth token request failed: {str(e)}")
        except KeyError as e:
            self.logger.error(f"servicenow.oauth.invalid_response: missing {str(e)}")
            raise ServiceNowClientError(f"Invalid OAuth response: missing {str(e)}")

    def get_oauth_status(self) -> Dict[str, Any]:
        """
        Get OAuth configuration status and test token acquisition.
        
        Returns:
            Dictionary with OAuth status information
        """
        oauth_configured = bool(
            self._config.oauth_client_id and 
            self._config.oauth_client_secret and 
            self._config.auth_method == AuthMethod.OAUTH
        )
        
        result = {
            "service": "ServiceNow OAuth (Client Credentials)",
            "auth_type": "client_credentials",
            "oauth_configured": oauth_configured,
            "client_id_set": bool(self._config.oauth_client_id),
            "client_secret_set": bool(self._config.oauth_client_secret),
            "auth_method": self._config.auth_method.value,
            "info": "ServiceNow uses Client Credentials flow - no redirect URL needed",
            "required_config": [
                "oauth_client_id",
                "oauth_client_secret"
            ]
        }
        
        if oauth_configured:
            try:
                # Test token acquisition
                self._get_oauth_token()
                result.update({
                    "status": "ready",
                    "token_status": "OAuth token acquired successfully",
                    "token_cached": bool(self._access_token),
                    "token_expires_at": self._token_expires_at if self._access_token else None
                })
            except Exception as e:
                result.update({
                    "status": "error",
                    "token_status": f"OAuth token acquisition failed: {str(e)}"
                })
        else:
            result.update({
                "status": "not_configured",
                "token_status": "OAuth credentials not configured"
            })
            
        return result

    def get_oauth_info(self) -> Dict[str, Any]:
        """
        Get OAuth setup information and instructions.
        
        Returns:
            Dictionary with OAuth setup instructions
        """
        return {
            "service": "ServiceNow OAuth Configuration",
            "auth_type": "Client Credentials",
            "flow_description": "Direct client-to-client authentication without user interaction",
            "benefits": [
                "No redirect URL configuration needed",
                "No user interaction required",
                "Secure client-to-client authentication",
                "Automatic token refresh"
            ],
            "setup_instructions": [
                "1. Log in to ServiceNow instance as admin",
                "2. Navigate to System OAuth > Application Registry",
                "3. Create new OAuth API endpoint for external clients",
                "4. Set grant_type to 'client_credentials'",
                "5. Copy Client ID and Client Secret to environment variables",
                "6. No redirect URL configuration needed for Client Credentials flow"
            ],
            "required_env_vars": {
                "servicenow-oauth-client-id": "OAuth Client ID from ServiceNow",
                "servicenow-oauth-client-secret": "OAuth Client Secret from ServiceNow"
            },
            "endpoints_info": {
                "token_endpoint": f"{self._config.instance_url}/oauth_token.do",
                "grant_type": "client_credentials",
                "auth_header": "Bearer <access_token>"
            }
        }

    def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """
        Make authenticated HTTP request to ServiceNow API.
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (relative to /api/now/)
            **kwargs: Additional request parameters
            
        Returns:
            JSON response from ServiceNow API
            
        Raises:
            ServiceNowClientError: If request fails or authentication issues occur
        """
        # Build URL for regular ServiceNow Table API endpoints
        url = f"{self._config.base_url}/{endpoint}"
        
        # Use resilience manager for the request with retry and circuit breaker
        async def execute_request():
            try:
                # Handle OAuth authentication dynamically
                if self._config.auth_method == AuthMethod.OAUTH:
                    access_token = self._get_oauth_token()
                    headers = kwargs.get('headers', {})
                    headers['Authorization'] = f'Bearer {access_token}'
                    kwargs['headers'] = headers
                
                # Ensure timeout is set for all requests
                if 'timeout' not in kwargs:
                    kwargs['timeout'] = config.servicenow_timeout
                
                response = self.session.request(method, url, **kwargs)
                response.raise_for_status()
                return response.json()
                
            except requests.exceptions.RequestException as e:
                self.logger.error(
                    f"servicenow.api.request_failed: {method} {endpoint} - {str(e)} "
                    f"(auth: {self._config.auth_method.value})"
                )
                raise ServiceNowClientError(f"API request failed: {str(e)}")
        
        # Execute with resilience patterns
        try:
            # Run the async function in a sync context
            return asyncio.run(self.resilience_manager.execute_with_retry(
                func=execute_request,
                service_name="servicenow_api",
                fallback_response=None
            ))
        except Exception as e:
            # Final fallback
            self.logger.error(f"servicenow.api.resilience_failed: {method} {endpoint} - {str(e)}")
            raise ServiceNowClientError(f"ServiceNow API call failed after retries: {str(e)}")

    # ================================
    # User Management Methods
    # ================================

    def _get_user_sys_id_by_email(self, email: str) -> Optional[str]:
        """
        Get ServiceNow user sys_id by email address.
        
        Args:
            email: User's email address
            
        Returns:
            User's sys_id if found, None otherwise
        """
        if not email or not email.strip():
            self.logger.warning("Empty or invalid email provided for user lookup")
            return None
            
        # Normalize email: trim whitespace and convert to lowercase
        normalized_email = email.strip().lower()
        self.logger.info(f"Looking up user by email: {normalized_email}")
        
        try:
            params = {
                "sysparm_query": f"email={normalized_email}",
                "sysparm_fields": "sys_id,email,name"
            }
            response = self._make_request("GET", "table/sys_user", params=params)
            
            results = response.get("result", [])
            if results:
                user_sys_id = results[0]["sys_id"]
                user_name = results[0].get("name", "Unknown")
                self.logger.info(f"Found user: {user_name} (sys_id: {user_sys_id}) for email: {normalized_email}")
                
                # Validate sys_id is not empty
                if not user_sys_id or not user_sys_id.strip():
                    self.logger.error(f"Retrieved sys_id is empty for email: {normalized_email}")
                    return None
                    
                return user_sys_id
            else:
                self.logger.warning(f"No user found for email: {normalized_email}")
                return None
            
        except Exception as e:
            self.logger.error(f"Failed to get user sys_id for {normalized_email}: {e}")
            return None

    # ================================
    # Query and Retrieval Methods
    # ================================

    def list_user_request_items(self, user_email: str) -> List[Dict[str, Any]]:
        """
        List not closed service catalog request items (RITMs) for a user.
        
        RITMs are what users and technicians actually see in "My Requests" interface.
        Returns items that are not closed (state != 3), including open, in-progress, pending, etc.
        
        Args:
            user_email: User's email address
            limit: Maximum number of not closed request items to return
            
        Returns:
            List of not closed request item dictionaries with key fields including catalog item info
        """
        user_sys_id = self._get_user_sys_id_by_email(user_email)
        if not user_sys_id:
            return []

        params = {
            "sysparm_query": f"requested_for={user_sys_id}^state!=3^ORDERBYDESCsys_created_on",
            "sysparm_fields": (
                "number,short_description,state,sys_created_on,stage,priority,urgency,"
                "cat_item.name,cat_item.sys_id,request.number,assignment_group.name"
            )
        }

        try:
            response = self._make_request("GET", "table/sc_req_item", params=params)
            return response.get("result", [])
        except Exception as e:
            self.logger.error(f"Failed to list request items for {user_email}: {e}")
            return []

    def list_user_incidents(self, user_email: str) -> List[Dict[str, Any]]:
        """
        List not closed incidents reported by a user.
        
        Returns incidents that are not closed (state != 7), including open, in-progress, pending, etc.
        
        Args:
            user_email: User's email address  
            limit: Maximum number of not closed incidents to return
            
        Returns:
            List of not closed incident dictionaries with key fields
        """
        user_sys_id = self._get_user_sys_id_by_email(user_email)
        if not user_sys_id:
            return []

        params = {
            "sysparm_query": f"caller_id={user_sys_id}^state!=7^ORDERBYDESCsys_created_on",
            "sysparm_fields": (
                "number,short_description,state,sys_created_on,priority,urgency,"
                "u_type_of_issue,assignment_group"
            )
        }

        try:
            response = self._make_request("GET", "table/incident", params=params)
            return response.get("result", [])
        except Exception as e:
            self.logger.error(f"Failed to list incidents for {user_email}: {e}")
            return []

    # ================================
    # Security Operations
    # ================================

    def create_security_incident(self, variables: Dict[str, Any], user_email: str) -> Dict[str, Any]:
        """
        Create a security incident directly in the incident table.
        
        Creates security incidents with security classification and
        automatic assignment to SOC. Direct incident creation without catalog dependency.
        
        Args:
            variables: Incident variables (short_description, description, urgency, priority)
            user_email: Email of the reporting user
            
        Returns:
            Dictionary containing incident details (number, sys_id, state)
            
        Raises:
            ServiceNowClientError: If user not found or incident creation fails
        """
        self.logger.info(f"Creating security incident for user: {user_email}")
        
        user_sys_id = self._get_user_sys_id_by_email(user_email)
        if not user_sys_id:
            error_msg = (
                f"Cannot create incident: User not found for email '{user_email}'. "
                f"Please ensure the user exists in ServiceNow and the email address is correct. "
                f"Common issues: email case sensitivity, extra spaces, or user not provisioned."
            )
            self.logger.error(error_msg)
            raise ServiceNowClientError(error_msg)

        # Additional validation to ensure user_sys_id is valid
        if not user_sys_id.strip():
            error_msg = f"Cannot create incident: Invalid user sys_id (empty) for email '{user_email}'"
            self.logger.error(error_msg)
            raise ServiceNowClientError(error_msg)

        self.logger.info(f"Validated user sys_id: {user_sys_id} for email: {user_email}")

        # Build security incident payload with security classification
        payload = {
            "caller_id": user_sys_id,
            "opened_by": user_sys_id,  # Explicitly set who opened/submitted the incident
            "short_description": variables.get("short_description", "Security Incident Report"),
            "description": variables.get("description", ""),
            "state": "1",  # New
            "urgency": variables.get("urgency", "3"),
            "priority": variables.get("priority", "4"),
            "u_type_of_issue": "Security",  # Security issue classification
            "assignment_group": config.servicenow_security_operation_center_id,  # Auto-assign to SOC
            "contact_type": "web"  # Submitted via web interface
        }

        # Final validation: Ensure caller_id is set in payload
        if not payload.get("caller_id"):
            error_msg = f"Critical error: caller_id not set in payload for user '{user_email}'"
            self.logger.error(error_msg)
            raise ServiceNowClientError(error_msg)

        self.logger.info(f"Payload validated. Creating incident with caller_id: {payload['caller_id']}")

        try:
            response = self._make_request("POST", "table/incident", json=payload)
            result = response["result"]
            incident_number = result.get("number")
            incident_sys_id = result.get("sys_id")
            
            # Verify that the incident was created with the correct caller
            created_caller_id = result.get("caller_id")
            if created_caller_id != user_sys_id:
                self.logger.warning(
                    f"Incident {incident_number} created but caller_id mismatch. "
                    f"Expected: {user_sys_id}, Got: {created_caller_id}"
                )
            
            self.logger.info(
                f"Security incident {incident_number} created successfully. "
                f"Caller: {user_email} (sys_id: {user_sys_id}), "
                f"SOC assigned, sys_id: {incident_sys_id}"
            )
            
            return {
                "number": incident_number,
                "sys_id": incident_sys_id,
                "state": result.get("state"),
                "table": "incident",
                "assignment_group": "Security Operation Center",
                "caller_id": created_caller_id,
                "caller_email": user_email
            }
        except Exception as e:
            error_msg = (
                f"Failed to create security incident for user '{user_email}' "
                f"(sys_id: {user_sys_id}): {str(e)}"
            )
            self.logger.error(error_msg)
            self.logger.error(f"Payload that failed: {payload}")
            raise ServiceNowClientError(f"Security incident creation failed: {str(e)}")

    def create_security_request(self, variables: Dict[str, Any], user_email: str) -> Dict[str, Any]:
        """
        Create a security service request following ServiceNow catalog workflow.
        
        This method replicates the exact process that the ServiceNow catalog uses:
        1. Creates sc_request (REQ) record as the container
        2. Creates sc_req_item (RITM) record with catalog item reference  
        3. Links RITM to REQ via request field
        4. Users see the RITM in "My Requests" interface
        
        Args:
            variables: Request variables (short_description, description, urgency, priority)
            user_email: Email of the requesting user
            
        Returns:
            Dictionary containing REQ and RITM details
            
        Raises:
            ServiceNowClientError: If user not found or request creation fails
        """
        self.logger.info(f"Creating security request for user: {user_email}")
        
        user_sys_id = self._get_user_sys_id_by_email(user_email)
        if not user_sys_id:
            error_msg = (
                f"Cannot create request: User not found for email '{user_email}'. "
                f"Please ensure the user exists in ServiceNow and the email address is correct."
            )
            self.logger.error(error_msg)
            raise ServiceNowClientError(error_msg)

        # Additional validation to ensure user_sys_id is valid
        if not user_sys_id.strip():
            error_msg = f"Cannot create request: Invalid user sys_id (empty) for email '{user_email}'"
            self.logger.error(error_msg)
            raise ServiceNowClientError(error_msg)

        self.logger.info(f"Validated user sys_id: {user_sys_id} for email: {user_email}")

        # Get security catalog item ID from configuration
        from ..mcp.config import config
        security_catalog_item_id = config.servicenow_security_request_item_id

        # Step 1: Create sc_request (REQ) - Container record
        req_payload = {
            "requested_for": user_sys_id,
            "opened_by": user_sys_id,  # Explicitly set who opened/submitted the request
            "short_description": variables.get("short_description", "Security Request"),
            "description": f"Security catalog request container\n\n{variables.get('description', '')}",
            "state": "1",  # Submitted
            "urgency": variables.get("urgency", "3"),
            "priority": variables.get("priority", "4"),
            "assignment_group": config.servicenow_security_operation_center_id  # Same as incidents
            # Note: REQ does NOT have cat_item field - that's on RITM
        }

        try:
            req_response = self._make_request("POST", "table/sc_request", json=req_payload)
            req_result = req_response["result"]
            req_sys_id = req_result["sys_id"]
            req_number = req_result["number"]
            
            self.logger.info(f"Created security REQ container: {req_number}")

            # Step 2: Create sc_req_item (RITM) - What users actually see
            ritm_payload = {
                "request": req_sys_id,  # Link to parent REQ
                "cat_item": security_catalog_item_id,  # Link to security catalog item
                "requested_for": user_sys_id,
                "opened_by": user_sys_id,  # Explicitly set who opened/submitted the request
                "short_description": variables.get("short_description", "Security Access Request"),
                "description": variables.get("description", "Security catalog request item"),
                "state": "1",  # Requested
                "urgency": variables.get("urgency", "3"),
                "priority": variables.get("priority", "4"),
                "stage": "request_approved",  # Move to approved stage
                "assignment_group": config.servicenow_security_operation_center_id  # Auto-assign to SOC
            }

            ritm_response = self._make_request("POST", "table/sc_req_item", json=ritm_payload)
            ritm_result = ritm_response["result"]
            ritm_number = ritm_result["number"]
            
            self.logger.info(f"Created security RITM: {ritm_number} linked to REQ: {req_number}")

            return {
                # REQ details (container)
                "req_number": req_number,
                "req_id": req_sys_id,
                "req_state": req_result.get("state"),
                
                # RITM details (what users see)
                "ritm_number": ritm_number,
                "ritm_id": ritm_result["sys_id"],
                "ritm_state": ritm_result.get("state"),
                
                # Catalog linkage
                "catalog_item": security_catalog_item_id,
                "method_used": "proper_catalog_workflow",
                "user_visible_record": ritm_number  # This is what users will see
            }
            
        except Exception as e:
            self.logger.error(f"Failed to create security request via catalog workflow: {e}")
            raise ServiceNowClientError(f"Security request creation failed: {str(e)}")

    # ================================
    # Properties for External Access
    # ================================

    @property
    def instance_url(self) -> str:
        """ServiceNow instance URL."""
        return self._config.instance_url

    @property
    def base_url(self) -> str:  
        """ServiceNow API base URL."""
        return self._config.base_url

    @property
    def auth_method(self) -> str:
        """Current authentication method."""
        return self._config.auth_method.value