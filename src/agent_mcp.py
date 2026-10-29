"""VWA's optional stdio MCP entry point. Run in the dedicated MCP environment."""
from __future__ import annotations

import argparse
from functools import partial
import json
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from agent_control import Control, ControlError
from agent_mcp_models import Result
from agent_mcp_standard_routing import STANDARD_INSTRUCTIONS, interface_identity, standardize


class StandardMCPServer(MCPServer):
    """Publish the strict 12-tool catalogue and reject extra top-level fields."""

    async def list_tools(self):
        listed = await super().list_tools()
        for tool in listed:
            tool.input_schema = {**tool.input_schema, 'additionalProperties': False}
        return listed

    async def call_tool(self, name, arguments, context=None):
        listed = await super().list_tools()
        tool = next((item for item in listed if item.name == name), None)
        if tool is not None and isinstance(arguments, dict):
            extra = sorted(set(arguments) - set(tool.input_schema.get('properties', {})))
            if extra:
                raise ToolError(json.dumps({
                    'code': 'invalid_arguments',
                    'message': 'Unexpected top-level arguments',
                    'extra_fields': extra,
                }, ensure_ascii=False))
        return await super().call_tool(name, arguments, context)


def build_server(config_path):
    control = Control(config_path)
    interface_revision = interface_identity(control.app)
    mcp = StandardMCPServer(
        'VWA Agent', version='0.5.0', log_level='WARNING',
        instructions=STANDARD_INSTRUCTIONS,
    )

    async def invoke(function, *args, **kwargs):
        try:
            data = await anyio.to_thread.run_sync(partial(function, *args, **kwargs))
            if isinstance(data, dict) and data.get('server') == 'VWA Agent MCP':
                data = {**data, 'interface_profile': 'standard',
                        'interface_revision': interface_revision}
            if isinstance(data, dict):
                owner = getattr(function, '__self__', None)
                name = getattr(function, '__name__', '')
                mutations = {
                    'create', 'advance', 'apply_review', 'references_set',
                    'pronunciation_apply', 'clone', 'rebind_score', 'close',
                }
                if ((owner is not None and owner.__class__.__name__ == 'Workflow'
                     and name in mutations)
                        or (getattr(function, '__module__', '') == 'agent_segment_workflow'
                            and name == 'style_decide')):
                    from agent_views import workflow_view
                    data = workflow_view(data)
            return Result(data=standardize(data))
        except (ControlError, ValueError, OSError, TypeError, KeyError) as error:
            logging.getLogger('vwa.mcp').warning('%s: %s', type(error).__name__, error)
            from agent_errors import error_payload
            raise ToolError(json.dumps(standardize(error_payload(error)),
                                       ensure_ascii=False)) from error

    from agent_workflow import Workflow
    from agent_mcp_standard_read import register as register_read
    from agent_mcp_standard_assets import register as register_assets
    from agent_mcp_standard_decide import register as register_decide
    from agent_mcp_standard_flow import register as register_flow

    workflow = Workflow(control)
    for register in (register_read, register_assets, register_decide, register_flow):
        register(mcp, control, workflow, invoke)

    @mcp.resource('vwa://capabilities')
    def capabilities_resource() -> str:
        capabilities = {**control.capabilities(), 'interface_profile': 'standard',
                        'interface_revision': interface_revision}
        return json.dumps(standardize(capabilities), ensure_ascii=False, indent=2)

    @mcp.resource('vwa://workflow')
    def workflow_resource() -> str:
        return (STANDARD_INSTRUCTIONS +
                ' 工作流操作按工具的有类型request.action选择；各工具说明包含所需参数。')

    return mcp


def main():
    parser = argparse.ArgumentParser(description='VWA optional stdio MCP server')
    parser.add_argument('--config', required=True, help='Absolute path to reviewed local MCP config')
    args = parser.parse_args()
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    build_server(args.config).run(transport='stdio')


if __name__ == '__main__':
    main()
