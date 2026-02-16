# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

"""
Enterprise IT Chatbot - Official Microsoft Echo-Bot Pattern with aiohttp

Based on: https://github.com/microsoft/BotBuilder-Samples/blob/main/samples/python/02.echo-bot/app.py
Adapted for Agent integration with Teams support.
"""

import sys
import traceback
from datetime import datetime

# IMPORTANT: Configure HTTP timeouts BEFORE importing Bot Framework
# This ensures the Bot Framework uses our timeout settings from the start
try:
    import socket
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    from .config import config
    
    # Set global socket timeout (affects all network operations)  
    socket.setdefaulttimeout(config.bot_framework_read_timeout)
    
    # Configure default requests session with our timeouts
    original_session_init = requests.Session.__init__
    
    def patched_session_init(self):
        original_session_init(self)
        
        # Override the request method to always include timeout
        original_request = self.request
        def request_with_timeout(*args, **kwargs):
            if 'timeout' not in kwargs:
                kwargs['timeout'] = (
                    config.bot_framework_connection_timeout,  # Connect timeout
                    config.bot_framework_read_timeout  # Read timeout 
                )
            return original_request(*args, **kwargs)
        self.request = request_with_timeout
    
    # Apply the patch
    requests.Session.__init__ = patched_session_init
    
    print(f"[TIMEOUT] Bot Framework timeouts: connect={config.bot_framework_connection_timeout}s, read={config.bot_framework_read_timeout}s")
    
except Exception as e:
    print(f"[TIMEOUT ERROR] Failed to configure timeouts: {e}")

from aiohttp import web
from aiohttp.web import Request, Response
from botbuilder.core import TurnContext
from botbuilder.core.integration import aiohttp_error_middleware
from botbuilder.integration.aiohttp import CloudAdapter, ConfigurationBotFrameworkAuthentication
from botbuilder.schema import Activity, ActivityTypes
import aiohttp

from ..clients.echo_bot import EchoBot
from ..clients.servicenow_client import ServiceNowClient
from .agent import Agent
from .config import config

# Import structured logger for the application
from ..clients.logging_client import log

# Catch-all for errors - following official echo-bot pattern
async def on_error(context: TurnContext, error: Exception):
    """Error handler following official Microsoft echo-bot pattern."""
    # Enhanced error logging for authentication issues
    error_msg = str(error)
    error_type = type(error).__name__
    
    print(f"\n[BOT ERROR] {error_type}: {error_msg}", file=sys.stderr)
    traceback.print_exc()
    
    # Check for authentication-specific errors
    is_auth_error = any(keyword in error_msg.lower() for keyword in [
        'unauthorized', '401', 'authentication', 'token', 'credentials'
    ])
    
    if is_auth_error:
        print(f"[AUTH ERROR DETECTED] This appears to be an authentication error!")
        print(f"[AUTH ERROR] Error details: {error_msg}")
    
    # Structured logging with auth detection
    log.error("bot.turn_error", 
             error=error_msg,
             error_type=error_type,
             is_auth_error=is_auth_error,
             channel_id=getattr(context.activity, 'channel_id', 'unknown'))

    # Send a message to the user (only if not auth error to avoid loops)
    if not is_auth_error:
        try:
            await context.send_activity("🚨 The bot encountered a technical error.")
            await context.send_activity(
                "🔧 The technical team has been notified. Please try again."
            )
        except Exception as send_error:
            print(f"[ERROR] Could not send error message: {send_error}")
    else:
        print("[AUTH ERROR] Skipping error message send to avoid authentication loop")
    
    # Send a trace activity if we're talking to the Bot Framework Emulator
    if context.activity.channel_id == "emulator":
        # Create a trace activity that contains the error object
        trace_activity = Activity(
            label="TurnError",
            name="on_turn_error Trace",
            timestamp=datetime.utcnow(),
            type=ActivityTypes.trace,
            value=f"{error}",
            value_type="https://www.botframework.com/schemas/error",
        )
        # Send a trace activity, which will be displayed in Bot Framework Emulator
        await context.send_activity(trace_activity)

# Listen for incoming requests on /api/messages - official echo-bot pattern
async def messages(req: Request) -> Response:
    """
    Handle incoming messages following official echo-bot pattern.
    
    This is the exact same pattern as Microsoft's official echo-bot:
    - Uses aiohttp.Request (not FastAPI)
    - Calls ADAPTER.process(req, BOT) directly
    - No complex request/response handling needed
    """
    return await ADAPTER.process(req, BOT)


# Simple health check endpoint for Docker
async def health_check(req: Request) -> Response:
    """Health check endpoint for container orchestration."""
    print(f"💚 [DEBUG] health_check called: {req.method} {req.path} from {req.remote}")
    
    # Use dynamic timestamp instead of hardcoded value
    current_time = datetime.utcnow().isoformat() + "Z"
    response = {"status": "healthy", "service": "MN8 Teams Bot", "timestamp": current_time}
    print(f"✅ [DEBUG] Sending health response: {response}")
    
    return web.json_response(response)


# ServiceNow OAuth status endpoint (delegates to ServiceNow client)
async def servicenow_oauth_status(req: Request) -> Response:
    """Get ServiceNow OAuth status via ServiceNow client."""
    try:
        # Use ServiceNow client's OAuth status method
        oauth_status = SERVICENOW_CLIENT.get_oauth_status()
        
        log.info("servicenow.oauth.status_requested", 
                status=oauth_status.get("status"),
                auth_method=oauth_status.get("auth_method"))
        
        return web.json_response(oauth_status)
        
    except Exception as e:
        log.error("servicenow.oauth.status_exception", error=str(e))
        return web.json_response({
            "error": "Failed to get OAuth status",
            "details": str(e)
        }, status=500)


# Endpoint to get OAuth configuration info (delegates to ServiceNow client)
async def oauth_info(req: Request) -> Response:
    """Get OAuth configuration information via ServiceNow client."""
    print(f"🔍 [DEBUG] oauth_info called: {req.method} {req.path} from {req.remote}")
    
    try:
        # Use ServiceNow client's OAuth info method
        oauth_info_data = SERVICENOW_CLIENT.get_oauth_info()
        
        # Add app-specific information
        oauth_info_data.update({
            "app_service": "MN8 Teams Bot",
            "status_endpoint": "/oauth/servicenow/status",
            "app_integration": "OAuth managed by ServiceNow client"
        })
        
        print(f"✅ [DEBUG] Sending oauth_info response from ServiceNow client")
        
        return web.json_response(oauth_info_data)
        
    except Exception as e:
        print(f"❌ [ERROR] oauth_info exception: {str(e)}")
        traceback.print_exc()
        return web.json_response({
            "error": "Failed to get OAuth info",
            "details": str(e)
        }, status=500)


# Create aiohttp app following official echo-bot pattern
APP = web.Application(middlewares=[aiohttp_error_middleware])

# Register routes with logging
APP.router.add_post("/api/messages", messages)
log.info("route.registered", method="POST", path="/api/messages", handler="messages")

APP.router.add_get("/", health_check)
log.info("route.registered", method="GET", path="/", handler="health_check")

APP.router.add_get("/oauth/servicenow/status", servicenow_oauth_status)
log.info("route.registered", method="GET", path="/oauth/servicenow/status", handler="servicenow_oauth_status")

APP.router.add_get("/oauth/info", oauth_info)
log.info("route.registered", method="GET", path="/oauth/info", handler="oauth_info")

class DefaultConfig:
    """
    Bot Framework configuration class.
    
    This class provides configuration values for the Bot Framework
    CloudAdapter, loaded from the centralized configuration.
    """
    PORT = config.port
    APP_ID = config.microsoft_app_id
    APP_PASSWORD = config.microsoft_app_password
    APP_TYPE = config.microsoft_app_type
    APP_TENANTID = config.microsoft_tenant_id
        
# Create CloudAdapter with default configuration (timeouts configured via requests patching above)
ADAPTER = CloudAdapter(ConfigurationBotFrameworkAuthentication(DefaultConfig()))

# Log the timeout configuration for monitoring
log.info("bot_framework.adapter.created", 
         connect_timeout=config.bot_framework_connection_timeout,
         read_timeout=config.bot_framework_read_timeout,
         total_timeout=config.bot_framework_timeout)

ADAPTER.on_turn_error = on_error

# Create ServiceNow client instance for OAuth token management
SERVICENOW_CLIENT = ServiceNowClient()
AGENT = Agent()
AGENT.servicenow_client = SERVICENOW_CLIENT
BOT = EchoBot(agent=AGENT)

if __name__ == "__main__":
    import os
    
    try:
        
        # Print system prompt for debugging
        print("\n" + "="*60)
        print("🤖 AGENT SYSTEM PROMPT:")
        print("="*60)
        print(config.agent_default_sys_prompt or "No system prompt configured")
        print("="*60 + "\n")

        # Use container-friendly settings based on environment
        is_container = os.getenv("container-env", "").lower() in ["true", "1", "yes"]
        host = "0.0.0.0" if is_container else "localhost"
        port = config.port
        
        print(f"🚀 Starting MN8 Teams Bot (Echo-Bot Pattern)")
        print(f"📡 Listening on: {host}:{port}")
        print(f"🔗 Bot Endpoint: http://{host}:{port}/api/messages")
        print(f"🔗 OAuth Status: http://{host}:{port}/oauth/servicenow/status")
        print(f"🤖 App ID: {config.microsoft_app_id[:8] + '...' if config.microsoft_app_id else 'None'}")
        print(f"🔑 Password: {'SET' if config.microsoft_app_password else 'MISSING'}")
        print(f"🌍 Environment: {'Container' if is_container else 'Local'}")
        
        # ServiceNow OAuth information (Client Credentials flow)
        print(f"🔐 ServiceNow OAuth: Client Credentials flow (no redirect URL needed)")
        print(f"🔑 OAuth Client ID: {'SET' if config.servicenow_oauth_client_id else 'MISSING'}")
        print(f"🔒 OAuth Client Secret: {'SET' if config.servicenow_oauth_client_secret else 'MISSING'}")
        
        # Warn about fallback authentication
        if not config.servicenow_oauth_client_id or not config.servicenow_oauth_client_secret:
            print("⚠️  OAuth authentication will fall back to Basic Auth or Bearer Token!")
        
        print("=" * 60)

        web.run_app(APP, host=host, port=port)
    except Exception as error:
        print(f"❌ Failed to start bot: {error}")
        raise error
