"""
LangChain Tools for MN8 IT Security Assistant.

This module provides ReAct tools for IT support and security operations:
- Query user request items (RITMs) and incidents from ServiceNow
- Create security requests using ServiceNow catalog workflow
- Create security incidents for SOC assignment
- Search knowledge base for auto-resolution procedures

All tools are designed to work with LangChain agents and follow the @tool decorator pattern.
"""

from typing import List
import json
from langchain.tools import tool

from ..clients.cosmos_client import CosmosDBClient
from ..clients.servicenow_client import ServiceNowClient


def create_support_tools(servicenow_client: ServiceNowClient, cosmos_client: CosmosDBClient, agent_ref=None) -> List:
    """
    Create essential LangChain tools for IT support and security operations.
    
    This function creates a set of tools that enable the agent to:
    - Query user's existing tickets and requests
    - Create new security incidents and requests
    - Search the knowledge base for solutions
    
    Args:
        servicenow_client: ServiceNow client for IT operations
        cosmos_client: Cosmos DB client for knowledge base search
        agent_ref: Reference to the agent for accessing user context
        
    Returns:
        List: LangChain tools ready for agent use
    """

    # ---------------- ServiceNow: Query user items/tickets ----------------

    @tool("list_my_request_items")
    def list_my_request_items() -> str:
        """
        List not closed user request items (RITMs) from ServiceNow.
        
        RITMs are what users see in the "My Requests" interface in ServiceNow.
        Returns items that are not closed (includes open, in-progress, pending, etc.).
        
        Returns:
            JSON string with user's not closed request items
        """
        try:
            # Get user email from agent context
            user_email = getattr(agent_ref, '_current_user_email', None) if agent_ref else None
            if not user_email:
                return "Error: User context not available. Please try again."
                
            request_items = servicenow_client.list_user_request_items(user_email)
            if not request_items:
                return json.dumps({
                    "message": f"No not closed request items found for {user_email}",
                    "user_email": user_email,
                    "request_items": []
                }, indent=2)
            
            return json.dumps({
                "user_email": user_email,
                "total_found": len(request_items),
                "request_items": request_items
            }, indent=2)
        except Exception as e:
            return f"Error listing user request items: {str(e)}"

    @tool("list_my_incidents")  
    def list_my_incidents() -> str:
        """
        List not closed user incidents from ServiceNow.
        
        Uses the ServiceNow client's list_user_incidents method to retrieve
        incidents that are not closed (includes open, in-progress, pending, etc.).
        
        Returns:
            JSON string with user's not closed incidents
        """
        try:
            # Get user email from agent context
            user_email = getattr(agent_ref, '_current_user_email', None) if agent_ref else None
            if not user_email:
                return "Error: User context not available. Please try again."
                
            incidents = servicenow_client.list_user_incidents(user_email)
            if not incidents:
                return json.dumps({
                    "message": f"No not closed incidents found for {user_email}",
                    "user_email": user_email,
                    "incidents": []
                }, indent=2)
            
            return json.dumps({
                "user_email": user_email,
                "total_found": len(incidents),
                "incidents": incidents
            }, indent=2)
        except Exception as e:
            return f"Error listing user incidents: {str(e)}"

    # ---------------- ServiceNow: Create hardcoded items ----------------

    @tool("create_security_request")
    def create_security_request(variables_json: str) -> str:
        """
        Create Security Request using ServiceNow catalog workflow (REQ + RITM).
        
        Creates a container REQ record and linked RITM that appears in user's "My Requests".
        The request is automatically routed to the appropriate security team based on 
        the catalog item configuration.
        
        Args:
            variables_json: JSON string containing request variables:
                - short_description (required): Brief title of the security request
                - description (optional): Detailed description of what is needed
                - urgency (optional): "1" (High), "2" (Medium), "3" (Low) - defaults to "3"
                - priority (optional): "1" (Critical), "2" (High), "3" (Moderate), "4" (Low) - defaults to "4"
            
        Returns:
            JSON string with created request details including REQ and RITM numbers
            
        Example variables_json:
            {"short_description": "Access to security tools", "description": "Need access to vulnerability scanner", "urgency": "2", "priority": "3"}
        """
        try:
            # Get user email from agent context
            user_email = getattr(agent_ref, '_current_user_email', None) if agent_ref else None
            if not user_email:
                return "Error: User context not available. Please try again."
                
            variables = json.loads(variables_json) if variables_json else {}
            if not isinstance(variables, dict):
                return "Error: variables_json must be a JSON object."

            res = servicenow_client.create_security_request(
                variables=variables,
                user_email=user_email
            )
            return json.dumps(res, indent=2)
        except Exception as e:
            return f"Error creating security request: {str(e)}"

    @tool("create_security_incident")
    def create_security_incident(variables_json: str) -> str:
        """
        Create Security Incident directly in ServiceNow incident table.
        
        Creates a security incident that is automatically assigned to the Security 
        Operation Center (SOC) with security classification. Bypasses catalog workflow
        for immediate security response.
        
        Args:
            variables_json: JSON string containing incident variables:
                - short_description (required): Brief title of the security incident
                - description (optional): Detailed description of the security issue
                - urgency (optional): "1" (High), "2" (Medium), "3" (Low) - defaults to "3"  
                - priority (optional): "1" (Critical), "2" (High), "3" (Moderate), "4" (Low) - defaults to "4"
            
        Returns:
            JSON string with created incident details including incident number and assignment
            
        Example variables_json:
            {"short_description": "Suspected malware infection", "description": "Computer running slowly, suspicious network activity detected", "urgency": "1", "priority": "2"}
        """
        try:
            # Get user email from agent context
            user_email = getattr(agent_ref, '_current_user_email', None) if agent_ref else None
            if not user_email:
                return "Error: User context not available. Please try again."
                
            variables = json.loads(variables_json) if variables_json else {}
            if not isinstance(variables, dict):
                return "Error: variables_json must be a JSON object."

            res = servicenow_client.create_security_incident(
                variables=variables,
                user_email=user_email
            )
            return json.dumps(res, indent=2)
        except Exception as e:
            return f"Error creating security incident: {str(e)}"

    # ---------------- Knowledge Base (Cosmos vector search) ----------------

    @tool("knowledge_base_search")
    def knowledge_base_search(query: str, top_k: int = 3) -> str:
        """
        Search the knowledge base for security procedures, auto-resolution guides, and IT documentation.
        
        Args:
            query: Search terms describing the security issue or procedure needed
            top_k: Number of most relevant results to return (default: 3, max recommended: 5)
            
        Use for finding:
        - Security incident response procedures
        - Auto-resolution steps for common security issues
        - IT security policies and guidelines
        - Troubleshooting guides for security tools
        """
        try:
            results = cosmos_client.vector_search(query, top_k)
            
            # Enhanced formatting for CU2 compatibility
            formatted_results = []
            for result in results:
                formatted = {
                    "article_id": result.get("number", result.get("id", "")),
                    "title": result.get("title", ""),
                    "content": result.get("content", ""),
                    "score": result.get("score", 0),
                    "chunk_type": result.get("chunk_type", "content"),
                    "is_summary": result.get("is_summary", False)
                }
                
                # Add article URL if available
                if "article_url" in result:
                    formatted["article_url"] = result["article_url"]
                
                # Add context about summary chunks
                if formatted["is_summary"]:
                    formatted["content_type"] = "Article Summary"
                else:
                    formatted["content_type"] = "Article Content"
                
                formatted_results.append(formatted)
            
            return json.dumps(formatted_results, indent=2)
        except Exception as e:
            return f"Error searching knowledge base: {str(e)}"

    # Return complete tool set for MN8 IT Security Assistant
    return [
        # User ticket and request query tools
        list_my_request_items,
        list_my_incidents,  
        # Security incident and request creation tools
        create_security_request,
        create_security_incident,
        # Knowledge base search for auto-resolution
        knowledge_base_search,
    ]
