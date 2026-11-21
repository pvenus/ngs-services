"""Loopback-only, decision-only HTTP boundary. It never executes a worker."""
import json
from .service import SelectionError
from .catalog import CatalogUnavailable


class WorkflowDecisionApi:
    def __init__(self, service, *, port=8040):
        self.service = service
        self.port = port

    @staticmethod
    def _response(start_response, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        start_response(status, [('Content-Type', 'application/json; charset=utf-8'), ('Content-Length', str(len(data)))])
        return [data]

    def __call__(self, environ, start_response):
        path, method = environ.get('PATH_INFO', ''), environ.get('REQUEST_METHOD', 'GET')
        respond = lambda status, payload: self._response(start_response, status, payload)
        try:
            if path == '/health' and method == 'GET':
                registry, source = self.service.registry_snapshot()
                return respond('200 OK', {'ok': True, 'service': 'workflow-decision-service', 'registryVersion': registry['version'], 'workflowSource': source, 'modelCalls': 0})
            if environ.get('REMOTE_ADDR') not in ('127.0.0.1', '::1') or environ.get('HTTP_HOST') not in (f'127.0.0.1:{self.port}', f'localhost:{self.port}', f'[::1]:{self.port}') or environ.get('HTTP_ORIGIN'):
                return respond('403 Forbidden', {'error': 'WORKFLOW_LOCAL_CALL_REQUIRED'})
            if path == '/v1/worker-registry' and method == 'GET':
                registry, source = self.service.registry_snapshot()
                return respond('200 OK', {'registry': registry, 'registryHash': source['sha256'], 'workflowSource': source})
            if path in ('/v1/workflow-selections', '/v1/request-routing', '/v1/selections', '/v1/npc-one-stop-plans') and method == 'POST':
                length = int(environ.get('CONTENT_LENGTH') or '0')
                if length <= 0 or (length > 100_000 and path != '/v1/workflow-selections'):
                    raise SelectionError('WORKFLOW_BODY_SIZE_INVALID')
                raw = environ['wsgi.input'].read(length)
                if len(raw) != length:
                    raise SelectionError('WORKFLOW_BODY_INCOMPLETE')
                payload = json.loads(raw.decode('utf-8'))
                # This shared endpoint can classify only after parsing. Preserve
                # every original byte; only exact eight-direction/frame admission is
                # exempt from the generic envelope cap. Other operations retain it.
                if length > 100_000 and not (
                    path == '/v1/workflow-selections' and isinstance(payload, dict)
                    and payload.get('mode') == 'single'
                    and isinstance(payload.get('request'), dict)
                    and (payload.get('requestType'), payload['request'].get('schemaVersion')) in (
                        ('npc.pose.expand-eight-directions', 'ngs.npc-pose-expansion-request.v1'),
                        ('npc.animation.frames.generate', 'ngs.npc-animation-frames-request.v1'),
                    )
                ):
                    raise SelectionError('WORKFLOW_BODY_SIZE_INVALID')
                if path == '/v1/npc-one-stop-plans':
                    profile = environ.get('HTTP_X_NGS_ONE_STOP_PLAN')
                    if profile is None:
                        result = self.service.one_stop_plan(payload)
                    elif profile == 'representative-first-v5':
                        result = self.service.one_stop_plan(payload, representative_first=True)
                    else:
                        raise SelectionError('ONE_STOP_PLAN_PROFILE_INVALID')
                elif path == '/v1/selections':
                    if not isinstance(payload, dict) or set(payload) - {'intent', 'inputs'}:
                        raise SelectionError('WORKFLOW_FEATURE_REQUEST_INVALID')
                    result = self.service.select(payload.get('intent'), payload.get('inputs'))
                else:
                    result = self.service.route_request(payload) if path == '/v1/request-routing' else self.service.select_workflow(payload)
                return respond('200 OK', result)
            return respond('404 Not Found', {'error': 'not_found'})
        except (SelectionError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            return respond('422 Unprocessable Entity', {'error': str(exc), 'details': getattr(exc, 'details', {'modelCalls': 0, 'workerCalls': 0})})
        except CatalogUnavailable as exc:
            return respond('503 Service Unavailable', {'error': str(exc), 'details': {'modelCalls': 0, 'workerCalls': 0}})
        except OSError:
            return respond('503 Service Unavailable', {'error': 'WORKFLOW_CONFIGURATION_UNAVAILABLE', 'details': {'modelCalls': 0, 'workerCalls': 0}})
