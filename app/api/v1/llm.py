"""
LLM Selector API Endpoints

Provides endpoints for:
1. Getting available LLMs with their working status
2. Setting user LLM preferences
3. Getting current user LLM preference
"""

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from app.decorators import audit_log

from app.services.llm_service import LLMService
from app.utils.pagination import safe_int_arg

llm_bp = Blueprint("llm", __name__, url_prefix="/api/v1/llm")


@llm_bp.route("/available", methods=["GET"])
@login_required
def get_available_llms():
    """
    Get all available LLMs with their working status.
    
    Returns a list of LLM providers that have valid API keys configured,
    plus a "No LLM" option for manual workflows.
    
    Response format:
    {
        "success": true,
        "llms": [
            {
                "id": "none",
                "name": "No LLM - Manual Mode",
                "available": true,
                "has_credit": true,
                "models": ["manual"],
                "test_result": "Manual mode - no AI assistance",
                "key_count": 0,
                "is_no_llm": true
            },
            {
                "id": "openai",
                "name": "OpenAI (GPT-4, GPT-3.5)",
                "available": true,
                "has_credit": true,
                "models": ["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"],
                "test_result": "2 API key(s) configured",
                "key_count": 2
            },
            ...
        ]
    }
    """
    try:
        llms = LLMService.get_available_llms()
        return jsonify({
            "success": True,
            "llms": llms,
            "count": len(llms),
            "available_count": sum(1 for llm in llms if llm.get("available", False))
        })
    except Exception as e:
        current_app.logger.error("Failed to retrieve available LLMs: %s", e, exc_info=True)
        return jsonify({
            "success": False,
            "error": "An internal error occurred. Please try again.",
            "message": "Failed to retrieve available LLMs"
        }), 500


@llm_bp.route("/preference", methods=["GET"])
@login_required
def get_user_preference():
    """
    Get the current user's LLM preference.
    
    Response format:
    {
        "success": true,
        "preference": {
            "provider": "openai",
            "model": "gpt-4",
            "has_preference": true
        }
    }
    """
    try:
        user_id = current_user.id
        preference = LLMService.get_user_llm_preference(user_id)
        return jsonify({
            "success": True,
            "preference": preference
        })
    except Exception as e:
        current_app.logger.error("Failed to retrieve user preference: %s", e, exc_info=True)
        return jsonify({
            "success": False,
            "error": "An internal error occurred. Please try again.",
            "message": "Failed to retrieve user preference"
        }), 500


@llm_bp.route("/preference", methods=["POST"])
@login_required
@audit_log("llm_preference_set")
def set_user_preference():
    """
    Set the user's LLM preference.
    
    Request body:
    {
        "provider": "openai",  // or "anthropic", "gemini", "nvidia", "huggingface", "none"
        "model": "gpt-4"       // optional, uses default if not specified
    }
    
    Response format:
    {
        "success": true,
        "provider": "openai",
        "model": "gpt-4",
        "message": "Preference saved successfully"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "error": "No data provided"
            }), 400
        
        provider = data.get("provider")
        if not provider:
            return jsonify({
                "success": False,
                "error": "Provider is required"
            }), 400
        
        # Validate provider
        valid_providers = ["none", "openai", "anthropic", "gemini", "nvidia", "huggingface", "azure", "custom", "openrouter", "watsonx"]
        if provider not in valid_providers:
            return jsonify({
                "success": False,
                "error": f"Invalid provider. Must be one of: {', '.join(valid_providers)}"
            }), 400
        
        model = data.get("model")
        user_id = current_user.id
        
        result = LLMService.set_user_llm_preference(user_id, provider, model)
        
        if result.get("success"):
            return jsonify({
                "success": True,
                "provider": provider,
                "model": model,
                "message": "Preference saved successfully"
            })
        else:
            return jsonify({
                "success": False,
                "error": result.get("error", "Failed to save preference")
            }), 500
            
    except Exception as e:
        current_app.logger.error("Failed to save user preference: %s", e, exc_info=True)
        return jsonify({
            "success": False,
            "error": "An internal error occurred. Please try again.",
            "message": "Failed to save user preference"
        }), 500


@llm_bp.route("/openrouter/models", methods=["GET"])
@login_required
def get_openrouter_models():
    """
    Fetch available models from the OpenRouter API.

    Query params:
        free_only (bool): If "true", return only free models.
        search (str): Filter models by name substring.
        limit (int): Max models to return (default 50).

    Response format:
    {
        "success": true,
        "models": [
            {
                "id": "google/gemini-2.5-flash-preview:free",
                "name": "Google: Gemini 2.5 Flash Preview",
                "context_length": 131072,
                "pricing": {"prompt": "0", "completion": "0"},
                "is_free": true
            },
            ...
        ],
        "count": 42
    }
    """
    import requests as http_requests

    try:
        # OpenRouter's /models endpoint is public — no API key required.
        # If a key is available, include it (may show account-specific models).
        headers = {"Content-Type": "application/json"}
        api_keys = LLMService._get_all_api_keys("openrouter")
        if api_keys:
            headers["Authorization"] = f"Bearer {api_keys[0]}"

        resp = http_requests.get(
            "https://openrouter.ai/api/v1/models",
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        raw_models = data.get("data", [])

        # Parse query params
        free_only = request.args.get("free_only", "false").lower() == "true"
        search_query = request.args.get("search", "").lower()
        limit = min(safe_int_arg('limit', 50, minimum=1, maximum=500), 200)

        models = []
        for m in raw_models:
            pricing = m.get("pricing", {})
            # OpenRouter returns pricing as strings like "0", "0.000001", etc.
            # Handle both string and numeric values
            try:
                prompt_cost_val = float(pricing.get("prompt", 0) or 0)
            except (ValueError, TypeError):
                prompt_cost_val = 0
            try:
                completion_cost_val = float(pricing.get("completion", 0) or 0)
            except (ValueError, TypeError):
                completion_cost_val = 0
            is_free = prompt_cost_val == 0 and completion_cost_val == 0

            if free_only and not is_free:
                continue

            model_name = m.get("name", m.get("id", ""))
            model_id = m.get("id", "")
            if search_query and search_query not in model_name.lower() and search_query not in model_id.lower():
                continue

            models.append({
                "id": model_id,
                "name": model_name,
                "context_length": m.get("context_length", 0),
                "pricing": {
                    "prompt": str(pricing.get("prompt", "0")),
                    "completion": str(pricing.get("completion", "0")),
                },
                "is_free": is_free,
            })

        # Sort: free first, then by name
        models.sort(key=lambda x: (not x["is_free"], x["name"]))
        models = models[:limit]

        return jsonify({
            "success": True,
            "models": models,
            "count": len(models),
            "total_available": len(raw_models),
        })

    except http_requests.exceptions.RequestException as e:
        current_app.logger.error("Failed to fetch OpenRouter models: %s", e, exc_info=True)
        return jsonify({
            "success": False,
            "error": "Failed to fetch OpenRouter models. Please check connectivity."
        }), 502
    except Exception as e:
        current_app.logger.error("Failed to retrieve OpenRouter models: %s", e, exc_info=True)
        return jsonify({
            "success": False,
            "error": "An internal error occurred. Please try again.",
            "message": "Failed to retrieve OpenRouter models"
        }), 500


@llm_bp.route("/watsonx/models", methods=["GET"])
@login_required
def get_watsonx_models():
    """The Granite instruct models the organisation's watsonx.ai service offers for chat.

    The service address is the ``url`` query parameter when given (so the form can ask before it is
    saved), otherwise the organisation's saved address, otherwise the platform's WATSONX_URL.
    """
    from app.modules.ai_chat.services import watsonx_client

    url = (request.args.get("url") or "").strip() or watsonx_client.connection()[0]
    try:
        models = watsonx_client.list_granite_models(url)
    except watsonx_client.WatsonxError as e:
        return jsonify({"success": False, "error": str(e)}), 502
    return jsonify({"success": True, "models": models, "count": len(models)})


@llm_bp.route("/test/<provider>", methods=["POST"])
@login_required
def test_llm_connection(provider):
    """
    Test connection to a specific LLM provider.
    
    This will try to use the first available API key for the provider
    and make a minimal test call to verify it works.
    
    Response format:
    {
        "success": true,
        "provider": "openai",
        "message": "Connection successful. Model: gpt-3.5-turbo",
        "has_credit": true
    }
    """
    try:
        # Get API keys for provider
        api_keys = LLMService._get_all_api_keys(provider)
        
        if not api_keys:
            return jsonify({
                "success": False,
                "provider": provider,
                "error": f"No API keys configured for {provider}",
                "has_credit": False
            }), 400
        
        # Test the first key
        from app.services.llm_service import test_api_key
        result = test_api_key(provider, api_keys[0])
        
        return jsonify({
            "success": result.get("success", False),
            "provider": provider,
            "message": result.get("message", "Unknown result"),
            "has_credit": result.get("success", False),
            "key_count": len(api_keys)
        })
        
    except Exception as e:
        current_app.logger.error("LLM connection test failed for %s: %s", provider, e, exc_info=True)
        return jsonify({
            "success": False,
            "provider": provider,
            "error": "An internal error occurred. Please try again.",
            "has_credit": False
        }), 500
