"""
Agent implementation for Enterprise IT Assistant.

This module provides the main Agent class that orchestrates LangChain ReAct agents
with ServiceNow integration and conversation history management through Cosmos DB.

Features:
- LangChain ReAct agent pattern
- Azure OpenAI integration
- ServiceNow tool integration
- Conversation history persistence
- Context window management
"""

from typing import List, Dict, Any
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, AIMessage
from langchain_openai import AzureChatOpenAI

from ..clients.cosmos_client import CosmosDBClient
from ..clients.servicenow_client import ServiceNowClient
from ..clients.logging_client import log
from .config import config
from .agent_tools import create_support_tools


class Agent:
    """
    ReAct agent for IT security management and incident handling.
    
    This agent combines LangChain's reasoning capabilities with ServiceNow
    integration to provide automated IT support and security incident management.
    
    Features:
    - Knowledge base search and auto-resolution
    - Security incident and request creation
    - Conversation history management
    - Context-aware responses with configurable memory
    """
    
    def __init__(self):
        """
        Initialize the Agent with Azure OpenAI and ServiceNow integration.
        
        Sets up:
        - Azure OpenAI model configuration from environment
        - ServiceNow client for IT operations
        - Cosmos DB client for conversation history
        - LangChain tools and agent creation
        """
        log.info("agent.init.start")
        
        # Initialize user context
        self._current_user_email = None
        
        # Load configuration from centralized config
        self.model_name = config.openai_deployment_name
        self.sys_prompt = config.agent_default_sys_prompt
        
        log.info("agent.init.config", 
                model_name=self.model_name,
                has_sys_prompt=bool(self.sys_prompt),
                max_context_turns=config.agent_max_context_turns)
        
        try:
            # Initialize Cosmos DB for conversation history management
            self.cosmos_client = CosmosDBClient()
            cosmos_available = self.cosmos_client.is_available()
            
            # Initialize ServiceNow client for IT operations and tools
            self.servicenow_client = ServiceNowClient()
            
            log.info("agent.init.clients", 
                    cosmos_available=cosmos_available,
                    servicenow_initialized=bool(self.servicenow_client))
            
            # Create support tools using LangChain @tool decorator pattern
            self.tools = create_support_tools(
                servicenow_client=self.servicenow_client,
                cosmos_client=self.cosmos_client,  # Used for knowledge base search functionality
                agent_ref=self  # Pass agent reference for user context access
            )
            
            # Create Azure OpenAI model instance with configuration from environment
            self.model = AzureChatOpenAI(
                azure_endpoint=config.openai_api_base,
                api_key=config.openai_api_key,
                azure_deployment=self.model_name,
                api_version=config.openai_api_version,
                temperature=0,  # Deterministic responses for IT support
                max_tokens=config.agent_max_tokens,  # Configurable token limit for responses
            )
            
            # Create LangChain ReAct agent with tools and system prompt
            self.agent = create_agent(
                model=self.model,
                tools=self.tools,
                system_prompt=self.sys_prompt
            )
            
            log.info("agent.init.success", 
                    tools_count=len(self.tools),
                    cosmos_available=cosmos_available)
                    
        except Exception as e:
            log.error("agent.init.error", 
                     error=str(e),
                     error_type=type(e).__name__)
            raise

    def invoke(self, user_input: str, user_email: str = None) -> str:
        """
        Main method to invoke the agent with user input and context.
        
        Args:
            user_input: The user's message or question
            user_email: User's email for context and history management
            
        Returns:
            str: The agent's response to the user input
        """
        log.info("agent.chat.start", 
                user_email=user_email,
                input_length=len(user_input) if user_input else 0)
        
        # Load conversation history for context
        conversation_history = self._load_conversation_history(user_email)
        
        # Store user email in agent context for tools to access
        # This allows tools to use the email without it appearing in messages
        self._current_user_email = user_email
        
        # Build message list with conversation history and current input (without email)
        messages = self._build_messages_list(conversation_history, user_input)
        
        log.info("agent.chat.invoke", 
                user_email=user_email,
                total_messages=len(messages),
                context_messages=len(conversation_history))
        
        try:
            # Invoke the LangChain agent
            result = self.agent.invoke({"messages": messages})
            
            # Extract the final response from agent result
            result_messages = result.get("messages", [])
            final_answer = result_messages[-1].content if result_messages else "No response available"
            
            log.info("agent.chat.response_generated", 
                    user_email=user_email,
                    response_length=len(final_answer) if final_answer else 0)
            
            # Save conversation history with original input (without email enhancement)
            conversation_history.append({"role": "user", "content": user_input})
            conversation_history.append({"role": "assistant", "content": final_answer})
            self._save_conversation_history(user_email, conversation_history)
            
            log.info("agent.chat.success", 
                    user_email=user_email,
                    final_history_length=len(conversation_history))
            
            return final_answer
            
        except Exception as e:
            log.error("agent.chat.error", 
                     user_email=user_email,
                     error=str(e),
                     error_type=type(e).__name__,
                     input_length=len(user_input) if user_input else 0)
            raise

    def run(self, user_text: str, user_email: str = None) -> str:
        """
        Backward compatibility method for agent invocation.
        
        Args:
            user_text: The user's message or question
            user_email: User's email for context
            
        Returns:
            str: The agent's response
        """
        return self.invoke(user_text, user_email)

    def _build_messages_list(self, history: List[Dict[str, Any]], current_message: str) -> List:
        """
        Build message list for LangChain agent invocation.
        
        Converts conversation history to LangChain message format and applies
        context window management based on configuration.
        
        Args:
            history: List of previous conversation turns
            current_message: The current user message
            
        Returns:
            List: LangChain message objects for agent invocation
        """
        messages = []
        
        # Apply context window limitation from configuration
        max_turns = config.agent_max_context_turns
        original_history_length = len(history)
        recent_history = history[-max_turns:] if len(history) > max_turns else history
        
        if original_history_length > max_turns:
            log.info("agent.context.truncated", 
                    original_length=original_history_length,
                    max_turns=max_turns,
                    truncated_length=len(recent_history))
        
        # Convert history to LangChain message format
        valid_messages = 0
        invalid_messages = 0
        
        for i, turn in enumerate(recent_history):
            role = turn.get("role", "user")
            content = turn.get("content", "")
            
            if not content:
                log.warning("agent.context.empty_message", 
                           turn_index=i,
                           role=role)
                invalid_messages += 1
                continue
            
            if role == "user":
                messages.append(HumanMessage(content=content))
                valid_messages += 1
            elif role == "assistant":
                messages.append(AIMessage(content=content))
                valid_messages += 1
            else:
                log.warning("agent.context.invalid_role", 
                           turn_index=i,
                           role=role,
                           content_preview=content[:100])
                invalid_messages += 1
        
        # Add current message
        messages.append(HumanMessage(content=current_message))
        
        log.info("agent.context.messages_built", 
                total_messages=len(messages),
                context_messages=valid_messages,
                invalid_messages=invalid_messages,
                current_message_length=len(current_message) if current_message else 0)
        
        return messages

    def _load_conversation_history(self, user_email: str = None) -> List[Dict[str, Any]]:
        """
        Load conversation history from Cosmos DB.
        
        Args:
            user_email: User's email identifier for history retrieval
            
        Returns:
            List[Dict]: Conversation history or empty list if unavailable
        """
        if not user_email:
            log.warning("agent.context.load.no_user_email")
            return []
            
        if not self.cosmos_client:
            log.warning("agent.context.load.no_cosmos_client", user_email=user_email)
            return []
            
        try:
            item = self.cosmos_client.get_session(user_email=user_email)
            
            if item:
                history = item.get('trace', [])
                log.info("agent.context.load.success", 
                        user_email=user_email,
                        history_length=len(history),
                        last_updated=item.get('last_updated'))
                return history
            else:
                log.info("agent.context.load.no_history", 
                        user_email=user_email,
                        reason="new_user")
                return []
                
        except Exception as e:
            log.error("agent.context.load.error", 
                     user_email=user_email,
                     error=str(e),
                     error_type=type(e).__name__)
            # Silently handle errors to avoid breaking agent functionality
            return []

    def _save_conversation_history(self, user_email: str, conversation_history: List[Dict[str, Any]]):
        """
        Save conversation history to Cosmos DB.
        
        Args:
            user_email: User's email identifier for history storage
            conversation_history: Complete conversation history to save
        """
        if not user_email:
            log.warning("agent.context.save.no_user_email", 
                       history_length=len(conversation_history) if conversation_history else 0)
            return
            
        if not self.cosmos_client:
            log.warning("agent.context.save.no_cosmos_client", 
                       user_email=user_email,
                       history_length=len(conversation_history) if conversation_history else 0)
            return
            
        try:
            log.info("agent.context.save.attempt", 
                    user_email=user_email,
                    history_length=len(conversation_history) if conversation_history else 0)
            
            self.cosmos_client.save_session(
                user_email=user_email,
                trace=conversation_history
            )
            
            log.info("agent.context.save.success", 
                    user_email=user_email,
                    history_length=len(conversation_history) if conversation_history else 0)
                    
        except Exception as e:
            log.error("agent.context.save.error", 
                     user_email=user_email,
                     error=str(e),
                     error_type=type(e).__name__,
                     history_length=len(conversation_history) if conversation_history else 0)
            # Silently handle errors to avoid breaking agent functionality
            pass
