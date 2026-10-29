"""Independent SDK contract for workflow review dry-run; no model execution."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

APP = Path(__file__).resolve().parents[1]
SDK = os.environ.get('VWA_MCP_SDK_PYTHON')


def contract():
    sys.path.insert(0, str(APP / 'src'))
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from agent_control import ControlError
    from agent_mcp_models import Result
    import agent_mcp_standard_decide as decide

    class Backend:
        calls = []

        def apply_review(self, *args):
            self.calls.append(args)
            return {'dry_run': True, 'approved': False}

    backend = Backend()

    async def invoke(function, *args):
        try:
            return Result(data=function(*args))
        except ControlError as error:
            raise ToolError(json.dumps({'code': error.code, 'message': str(error)})) from error

    async def run():
        server = MCPServer('dry-run-contract', log_level='ERROR')
        decide.register(server, object(), backend, invoke)
        tool = (await server.list_tools())[0]
        request_schema = tool.input_schema['properties']['request']
        definition = tool.input_schema['$defs'][request_schema['discriminator']['mapping']
                                                 ['workflow_apply_review'].rsplit('/', 1)[-1]]
        assert definition['properties']['dry_run']['default'] is False
        request = {'action': 'workflow_apply_review', 'workflow_id': 'flow_fixture',
                   'expected_revision': 3, 'rationale': '试算', 'dry_run': True}
        result = await server.call_tool('vwa_decide', {'request': request})
        assert result.structured_content['data'] == {'dry_run': True, 'approved': False}
        assert backend.calls[-1][-1] is True
        for extra in ({'run_until_gate': True}, {'wait_ms': 1}):
            count = len(backend.calls)
            try:
                await server.call_tool('vwa_decide', {'request': {**request, **extra}})
            except ToolError as error:
                assert 'invalid_options' in str(error)
            else:
                raise AssertionError('dry-run must reject drive/wait')
            assert len(backend.calls) == count

    asyncio.run(run())


class DryRunSDKTest(unittest.TestCase):
    @unittest.skipUnless(SDK, 'Dedicated optional SDK not configured')
    def test_contract(self):
        result = subprocess.run([SDK, '-B', str(Path(__file__)), '--contract'],
                                cwd=APP, capture_output=True, text=True, encoding='utf-8',
                                timeout=30, env=dict(os.environ, PYTHONUTF8='1'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    if '--contract' in sys.argv:
        contract()
    else:
        unittest.main()
