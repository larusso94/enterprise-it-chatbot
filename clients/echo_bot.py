"""
"""Enterprise IT Teams Bot - Official Microsoft Echo-Bot Pattern Implementation

This module implements an enterprise IT assistant bot following the official Microsoft 
Bot Framework echo-bot pattern, integrating with the Agent for intelligent responses.

Reference: https://github.com/microsoft/BotBuilder-Samples/blob/main/samples/python/02.echo-bot/
"""

# Standard library imports
from typing import Optional, List
import aiohttp
import json
import asyncio

# Bot Framework imports
from botbuilder.core import ActivityHandler, MessageFactory, TurnContext
from botbuilder.schema import ChannelAccount
from botbuilder.core.teams import TeamsInfo

# Local imports
from .logging_client import log
from ..mcp.agent import Agent
from ..mcp.config import AppConfig, config
from .resilience_utils import ResilienceManager, with_resilience
# ============================================================================
# MAIN BOT CLASS - Following Official Echo-Bot Pattern
# ============================================================================

class EchoBot(ActivityHandler):
    """
    Enterprise IT Teams bot following official Microsoft echo-bot pattern.
    
    Based on: https://github.com/microsoft/BotBuilder-Samples/blob/main/samples/python/02.echo-bot/
    
    Key features:
    - Inherits from ActivityHandler (official pattern)
    - Uses MessageFactory for all responses (official pattern) 
    - Simple on_message_activity that processes with Agent
    - Welcome messages on member addition
    - Clean error handling and logging
    """

    def __init__(self, agent: Optional[Agent] = None, config_instance: Optional[AppConfig] = None):
        """Initialize the bot with an Agent instance and resilience configuration."""
        super().__init__()
        self.config = config_instance or config
        self.resilience_manager = ResilienceManager(self.config)
        
        # Fallback responses for different scenarios
        self.fallback_responses = {
            'agent_unavailable': "🔧 Service temporarily unavailable. Please try again in a few minutes.",
            'timeout': "⏰ Request timed out. Please try again with a simpler query.",
            'connection_error': "🌐 Connection issues detected. Please check your network and try again.",
            'general_error': "⚠️ An error occurred processing your query. Please try again."
        }
        
        try:
            self.agent = agent or Agent()
            log.info("bot.init", 
                    pattern="echo-bot-teams", 
                    agent_ready=True,
                    resilience_enabled=True,
                    max_retries=self.config.max_retry_attempts,
                    bot_timeout=self.config.bot_framework_timeout)
        except Exception as e:
            log.error("bot.init.agent_failed", error=str(e))
            self.agent = None

    async def on_message_activity(self, turn_context: TurnContext):
        """Following official echo-bot pattern with resilience - process with Agent."""
        # Get user message text
        user_text = turn_context.activity.text
        
        # Extract user email using Microsoft Graph API with resilience
        user_email = await self._get_user_email_with_retry(turn_context)
        
        # Log incoming message with user info
        log.info("bot.message.received", 
                text_length=len(user_text) if user_text else 0,
                user_email=user_email,
                user_id=turn_context.activity.from_property.id if turn_context.activity.from_property else None,
                has_agent=self.agent is not None,
                resilience_enabled=True)
        
        # Check if agent is available
        if not self.agent:
            return await turn_context.send_activity(
                MessageFactory.text(self.fallback_responses['agent_unavailable'])
            )
        
        # Process message with resilience and fallbacks
        response = await self._process_message_with_resilience(user_text, user_email)
        
        return await turn_context.send_activity(MessageFactory.text(response))

    async def _process_message_with_resilience(self, user_text: str, user_email: str) -> str:
        """Process message with Agent using resilience patterns."""
        try:
            # Use resilience manager to execute agent processing
            agent_response = await self.resilience_manager.execute_with_retry(
                func=self._run_agent_async,
                service_name="it_agent",
                user_text=user_text.strip(),
                user_email=user_email,
                fallback_response=self.fallback_responses['general_error']
            )
            
            if agent_response and agent_response.strip():
                log.info("bot.agent.success", 
                        response_length=len(agent_response),
                        resilience_used=True)
                return agent_response
            else:
                log.warning("bot.agent.empty_response")
                return "🤔 I couldn't generate a response. Could you rephrase your question?"
                
        except asyncio.TimeoutError:
            log.error("bot.agent.timeout", user_email=user_email)
            return self.fallback_responses['timeout']
            
        except ConnectionError:
            log.error("bot.agent.connection_error", user_email=user_email)
            return self.fallback_responses['connection_error']
            
        except Exception as e:
            log.error("bot.agent.error", 
                     error=str(e), 
                     error_type=type(e).__name__,
                     user_email=user_email,
                     resilience_fallback=True)
            return self.fallback_responses['general_error']

    async def _run_agent_async(self, user_text: str, user_email: str) -> str:
        """Async wrapper for agent.run() with timeout."""
        try:
            # Apply timeout to agent execution
            return await asyncio.wait_for(
                asyncio.to_thread(self.agent.run, user_text=user_text, user_email=user_email),
                timeout=self.config.bot_framework_timeout
            )
        except asyncio.TimeoutError:
            log.error("bot.agent.execution_timeout", 
                     timeout=self.config.bot_framework_timeout,
                     user_email=user_email)
            raise

    async def _get_user_email_with_retry(self, turn_context: TurnContext) -> Optional[str]:
        """Get user email with retry mechanism."""
        try:
            return await self.resilience_manager.execute_with_retry(
                func=self._get_user_email,
                service_name="teams_graph_api",
                turn_context=turn_context,
                fallback_response=None
            )
        except Exception as e:
            log.warning("bot.user_email.failed_with_retry", 
                       error=str(e),
                       fallback_to_anonymous=True)
            return None  # Fallback to anonymous user

    async def on_members_added_activity(
        self, members_added: List[ChannelAccount], turn_context: TurnContext
    ):
        """Handle members added activity - silently log without sending messages."""
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                # Log the member addition but don't send any welcome message
                log.info("bot.member.added", 
                        member_id=member.id[:8] + "..." if member.id else "unknown")

    async def _get_user_email(self, turn_context: TurnContext) -> Optional[str]:
        """
        Extract user email from Teams context using Bot Framework SDK.
        
        Attempts to get user email via Teams SDK by checking multiple possible
        fields that may contain the email address depending on tenant configuration.
        
        Args:
            turn_context: Bot Framework turn context
            
        Returns:
            Optional[str]: User's email address if available, None otherwise
        """
        # Attempt direct email extraction via Teams SDK
        try:
            user_id = getattr(turn_context.activity.from_property, "id", None)  # Teams user id
            member = await TeamsInfo.get_member(turn_context, user_id)
            
            # Check possible email fields (depends on tenant and context)
            for field_name in ("email", "user_principal_name", "userPrincipalName"):
                email = getattr(member, field_name, None)
                if email:
                    return email
        except Exception as e:
            log.warning("bot.user_email.teamsinfo_failed", error=str(e))
            return None
