"""CLI for local preset documents. No processing, network or automatic applying."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The bundled interpreter's ._pth can omit the script directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from preset_documents import PresetError, read_regular_bytes, decode_document, document_digest, schema_document


def main(argv=None):
    parser = argparse.ArgumentParser(description='VWA 本机预设文件与预设库（不执行混音或调音）')
    parser.add_argument('--root', help='本机预设库目录；默认应用 user-presets')
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('schema')
    example = actions.add_parser('example')
    example.add_argument('type', choices=('preparation', 'mixdown', 'tuning', 'style_recipe'))
    actions.add_parser('list')
    inspect = actions.add_parser('validate')
    inspect.add_argument('file')
    install = actions.add_parser('import')
    install.add_argument('file')
    install.add_argument(
        '--as-copy',
        action='store_true',
        help='安装为新的 user.UUID/0.1.0 独立副本；内置导出文件始终按副本导入',
    )
    for name in ('show', 'export', 'copy', 'rename', 'favorite', 'delete'):
        command = actions.add_parser(name)
        command.add_argument('id')
        command.add_argument('version')
        if name == 'export':
            command.add_argument('destination')
        elif name == 'copy':
            command.add_argument('--name', required=True)
        elif name == 'rename':
            command.add_argument('--alias', required=True)
        elif name == 'favorite':
            command.add_argument('value', choices=('on', 'off'))
    actions.add_parser('recover')
    args = parser.parse_args(argv)
    try:
        if args.action == 'schema':
            result = schema_document()
        elif args.action == 'example':
            from preset_builtin_documents import example_documents
            result = next(doc for doc in example_documents() if doc['type'] == args.type)
        elif args.action == 'validate':
            raw = read_regular_bytes(args.file)
            doc = decode_document(raw, allow_builtin=True)
            result = {'id': doc['id'], 'version': doc['version'], 'type': doc['type'],
                      'sha256': document_digest(raw), 'valid': True, 'applied': False,
                      'will_copy': doc['id'].startswith('builtin.')}
        else:
            from preset_library import PresetLibrary
            library = PresetLibrary(args.root)
            if args.action == 'list':
                result = library.snapshot()
            elif args.action == 'show':
                result = library.get_document(args.id, args.version)
            elif args.action == 'export':
                result = {'path': str(library.export_file(args.id, args.version, args.destination))}
            elif args.action == 'recover':
                result = library.recover_previous()
            else:
                revision = library.snapshot()['revision']
                if args.action == 'import':
                    result = library.import_file(
                        args.file,
                        expected_revision=revision,
                        as_copy=args.as_copy,
                    )
                    result['will_copy'] = bool(result.get('copied'))
                elif args.action == 'copy':
                    result = library.copy(args.id, args.version, name=args.name, expected_revision=revision)
                elif args.action == 'rename':
                    result = library.rename(args.id, args.version, alias=args.alias, expected_revision=revision)
                elif args.action == 'favorite':
                    result = library.set_favorite(args.id, args.version, favorite=args.value == 'on', expected_revision=revision)
                else:
                    result = library.delete(args.id, args.version, expected_revision=revision)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except (PresetError, OSError) as error:
        print(json.dumps({'error': {'code': getattr(error, 'code', 'io_error'),
                                   'message': str(error), 'details': getattr(error, 'details', {})}},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='strict')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
