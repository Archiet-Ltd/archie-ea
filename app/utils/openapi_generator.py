#!/usr/bin/env python3
"""
OpenAPI Specification Generator

Auto-generates OpenAPI 3.0.3 specification from Flask routes by:
1. Introspecting app.url_map for all registered routes
2. Extracting docstrings for endpoint descriptions
3. Inferring request/response schemas from type hints and patterns
4. Organizing endpoints by blueprint/tag

Usage:
    python -m app.utils.openapi_generator                    # Print to stdout
    python -m app.utils.openapi_generator --output spec.yaml # Write to file
    python -m app.utils.openapi_generator --format json      # JSON output

Output: OpenAPI 3.0.3 specification (YAML or JSON)
"""

import argparse
import inspect
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import yaml


def get_flask_app():
    """Import and return the Flask app instance."""
    try:
        from app import create_app
        return create_app("development")
    except ImportError as e:
        print(f"Error importing Flask app: {e}", file=sys.stderr)
        sys.exit(1)


def extract_route_info(app) -> List[Dict[str, Any]]:
    """Extract route information from Flask app.url_map."""
    routes = []

    for rule in app.url_map.iter_rules():
        # Skip static files and internal routes
        if rule.endpoint == 'static' or rule.endpoint.startswith('_'):
            continue

        # Get the view function
        view_func = app.view_functions.get(rule.endpoint)
        if not view_func:
            continue

        # Extract docstring
        docstring = inspect.getdoc(view_func) or ""

        # Extract HTTP methods (exclude HEAD, OPTIONS which Flask adds automatically)
        methods = [m for m in rule.methods if m not in ('HEAD', 'OPTIONS')]

        # Parse path parameters
        path_params = extract_path_params(rule.rule)

        # Determine blueprint/tag
        blueprint = rule.endpoint.split('.')[0] if '.' in rule.endpoint else 'main'

        routes.append({
            'path': convert_flask_path_to_openapi(rule.rule),
            'flask_path': rule.rule,
            'methods': methods,
            'endpoint': rule.endpoint,
            'docstring': docstring,
            'blueprint': blueprint,
            'path_params': path_params,
            'view_func': view_func,
        })

    return routes


def convert_flask_path_to_openapi(flask_path: str) -> str:
    """Convert Flask path syntax to OpenAPI path syntax.

    Flask: /api/applications/<int:app_id>
    OpenAPI: /api/applications/{app_id}
    """
    # Pattern matches Flask path parameters like <int:id>, <string:name>, <id>
    pattern = r'<(?:int:|string:|float:|path:|uuid:)?([^>]+)>'
    return re.sub(pattern, r'{\1}', flask_path)


def extract_path_params(flask_path: str) -> List[Dict[str, Any]]:
    """Extract path parameters from Flask route path."""
    params = []
    pattern = r'<((?:int|string|float|path|uuid):)?([^>]+)>'

    for match in re.finditer(pattern, flask_path):
        param_type = match.group(1) or 'string:'
        param_name = match.group(2)

        # Map Flask types to OpenAPI types
        type_mapping = {
            'int:': ('integer', 'int64'),
            'string:': ('string', None),
            'float:': ('number', 'float'),
            'path:': ('string', None),
            'uuid:': ('string', 'uuid'),
        }

        openapi_type, openapi_format = type_mapping.get(param_type, ('string', None))

        param_schema = {'type': openapi_type}
        if openapi_format:
            param_schema['format'] = openapi_format

        params.append({
            'name': param_name,
            'in': 'path',
            'required': True,
            'schema': param_schema,
        })

    return params


def infer_query_params_from_docstring(docstring: str) -> List[Dict[str, Any]]:
    """Infer query parameters from docstring patterns."""
    params = []

    # Common patterns in docstrings
    query_param_patterns = [
        (r'page\s*(?:number)?', 'page', 'integer', 'Page number for pagination'),
        (r'per_page|limit', 'per_page', 'integer', 'Items per page'),
        (r'search\s*(?:term)?', 'search', 'string', 'Search term'),
        (r'sort_by|order_by', 'sort_by', 'string', 'Column to sort by'),
        (r'sort_order|order', 'sort_order', 'string', 'Sort order (asc/desc)'),
        (r'status', 'status', 'string', 'Filter by status'),
        (r'domain', 'domain', 'string', 'Filter by domain'),
        (r'level', 'level', 'string', 'Filter by level'),
    ]

    docstring_lower = docstring.lower()
    seen = set()

    for pattern, name, param_type, description in query_param_patterns:
        if re.search(pattern, docstring_lower) and name not in seen:
            seen.add(name)
            schema = {'type': param_type}
            if param_type == 'integer':
                schema['default'] = 1 if name == 'page' else 50

            params.append({
                'name': name,
                'in': 'query',
                'required': False,
                'description': description,
                'schema': schema,
            })

    return params


def infer_request_body(route_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Infer request body schema for POST/PUT/PATCH methods."""
    methods = route_info['methods']

    if not any(m in methods for m in ('POST', 'PUT', 'PATCH')):
        return None

    # Default JSON request body
    return {
        'required': True,
        'content': {
            'application/json': {
                'schema': {
                    'type': 'object',
                    'description': 'Request payload',
                }
            }
        }
    }


def infer_responses(route_info: Dict[str, Any]) -> Dict[str, Any]:
    """Infer response schemas based on endpoint patterns."""
    responses = {}
    methods = route_info['methods']
    path = route_info['path']

    # Success response
    if 'POST' in methods and 'create' in route_info['docstring'].lower():
        responses['201'] = {
            'description': 'Resource created successfully',
            'content': {
                'application/json': {
                    'schema': {'$ref': '#/components/schemas/SuccessResponse'}
                }
            }
        }
    else:
        responses['200'] = {
            'description': 'Successful response',
            'content': {
                'application/json': {
                    'schema': {'$ref': '#/components/schemas/SuccessResponse'}
                }
            }
        }

    # Common error responses for API endpoints
    if path.startswith('/api'):
        responses['400'] = {
            'description': 'Bad request - validation error',
            'content': {
                'application/json': {
                    'schema': {'$ref': '#/components/schemas/ErrorResponse'}
                }
            }
        }
        responses['401'] = {
            'description': 'Unauthorized - authentication required',
            'content': {
                'application/json': {
                    'schema': {'$ref': '#/components/schemas/ErrorResponse'}
                }
            }
        }

        # 404 for single resource endpoints
        if route_info['path_params']:
            responses['404'] = {
                'description': 'Resource not found',
                'content': {
                    'application/json': {
                        'schema': {'$ref': '#/components/schemas/ErrorResponse'}
                    }
                }
            }

        responses['500'] = {
            'description': 'Internal server error',
            'content': {
                'application/json': {
                    'schema': {'$ref': '#/components/schemas/ErrorResponse'}
                }
            }
        }

    return responses


def generate_operation(route_info: Dict[str, Any], method: str) -> Dict[str, Any]:
    """Generate OpenAPI operation object for a single HTTP method."""
    operation = {
        'operationId': f"{route_info['endpoint'].replace('.', '_')}_{method.lower()}",
        'tags': [format_tag_name(route_info['blueprint'])],
        'responses': infer_responses(route_info),
    }

    # Add summary and description
    if route_info['docstring']:
        lines = route_info['docstring'].split('\n')
        operation['summary'] = lines[0][:100] if lines else 'No description'
        if len(lines) > 1:
            operation['description'] = route_info['docstring']
    else:
        operation['summary'] = f"{method} {route_info['path']}"

    # Add parameters
    params = list(route_info['path_params'])  # Copy path params

    # Add query params for GET requests
    if method == 'GET':
        params.extend(infer_query_params_from_docstring(route_info['docstring']))

    if params:
        operation['parameters'] = params

    # Add request body for POST/PUT/PATCH
    if method in ('POST', 'PUT', 'PATCH'):
        request_body = infer_request_body(route_info)
        if request_body:
            operation['requestBody'] = request_body

    # Add security for API endpoints
    if route_info['path'].startswith('/api'):
        operation['security'] = [{'sessionAuth': []}]

    return operation


def format_tag_name(blueprint: str) -> str:
    """Format blueprint name as a readable tag."""
    # Common blueprint name transformations
    name = blueprint.replace('_', ' ').replace('-', ' ')
    name = ' '.join(word.capitalize() for word in name.split())
    return name


def generate_openapi_spec(app) -> Dict[str, Any]:
    """Generate complete OpenAPI specification from Flask app."""
    routes = extract_route_info(app)

    # Group routes by path
    paths_dict = defaultdict(dict)
    tags_set = set()

    for route in routes:
        path = route['path']
        tags_set.add(format_tag_name(route['blueprint']))

        for method in route['methods']:
            method_lower = method.lower()
            operation = generate_operation(route, method)
            paths_dict[path][method_lower] = operation

    # Sort tags
    tags = [{'name': tag, 'description': f'{tag} operations'} for tag in sorted(tags_set)]

    # Build spec
    spec = {
        'openapi': '3.0.3',
        'info': {
            'title': 'Flask Enterprise Architecture API',
            'description': '''
Enterprise Architecture Management API for the Entelim platform.

This API provides comprehensive endpoints for:
- Application portfolio management
- Capability modeling and mapping (ACM - Application Capability Model)
- Vendor organization and product management
- ArchiMate enterprise architecture modeling
- Dashboard and metrics
- AI-powered chat and analysis

## Authentication
All API endpoints require authentication via session-based login.

## Response Format
All responses follow a standardized format:
```json
{
  "success": true,
  "data": { ... },
  "message": "Optional message"
}
```

---
**Auto-generated**: ''' + datetime.now(timezone.utc).isoformat(),
            'version': '1.0.0',
            'contact': {
                'name': 'Enterprise Architecture Team',
            },
            'license': {
                'name': 'Proprietary',
            },
        },
        'servers': [
            {'url': 'http://localhost:5000', 'description': 'Local development server'},
            {'url': 'https://api.enterprise.local', 'description': 'Internal enterprise server'},
        ],
        'tags': tags,
        'paths': dict(sorted(paths_dict.items())),
        'components': {
            'securitySchemes': {
                'sessionAuth': {
                    'type': 'apiKey',
                    'in': 'cookie',
                    'name': 'session',
                    'description': 'Session-based authentication using Flask-Login',
                },
            },
            'schemas': {
                'SuccessResponse': {
                    'type': 'object',
                    'required': ['success'],
                    'properties': {
                        'success': {'type': 'boolean', 'example': True},
                        'data': {'type': 'object', 'description': 'Response data'},
                        'message': {'type': 'string', 'description': 'Optional message'},
                    },
                },
                'ErrorResponse': {
                    'type': 'object',
                    'required': ['success', 'error'],
                    'properties': {
                        'success': {'type': 'boolean', 'example': False},
                        'error': {
                            'type': 'object',
                            'properties': {
                                'code': {'type': 'string', 'example': 'ERROR_CODE'},
                                'message': {'type': 'string', 'example': 'Error description'},
                                'details': {'type': 'object'},
                            },
                        },
                    },
                },
                'Pagination': {
                    'type': 'object',
                    'properties': {
                        'page': {'type': 'integer', 'example': 1},
                        'per_page': {'type': 'integer', 'example': 50},
                        'total': {'type': 'integer', 'example': 100},
                        'pages': {'type': 'integer', 'example': 2},
                        'has_prev': {'type': 'boolean', 'example': False},
                        'has_next': {'type': 'boolean', 'example': True},
                    },
                },
            },
        },
    }

    return spec


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Generate OpenAPI specification from Flask routes'
    )
    parser.add_argument(
        '--output', '-o',
        help='Output file path (default: stdout)',
    )
    parser.add_argument(
        '--format', '-f',
        choices=['yaml', 'json'],
        default='yaml',
        help='Output format (default: yaml)',
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Show verbose output',
    )

    args = parser.parse_args()

    if args.verbose:
        print("Loading Flask application...", file=sys.stderr)

    app = get_flask_app()

    if args.verbose:
        print(f"Found {len(list(app.url_map.iter_rules()))} routes", file=sys.stderr)

    spec = generate_openapi_spec(app)

    if args.verbose:
        print(f"Generated spec with {len(spec['paths'])} paths", file=sys.stderr)

    # Output
    if args.format == 'json':
        output = json.dumps(spec, indent=2, default=str)
    else:
        output = yaml.dump(spec, default_flow_style=False, allow_unicode=True, sort_keys=False)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output)
        if args.verbose:
            print(f"Wrote specification to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == '__main__':
    main()
